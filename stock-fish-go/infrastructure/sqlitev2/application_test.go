package sqlitev2

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type testEngine struct{ wait bool }

func (testEngine) Validate(raw []byte) (c.InputRecord, error) {
	return c.InputRecord{ID: "TEST_FIXTURE", Raw: raw, RawHash: appHash(raw), Observation: "2026-01-02", Cutoff: "2026-01-02T16:00:00+08:00", Coverage: "TEST_FIXTURE"}, nil
}
func (e testEngine) Evaluate(ctx context.Context, in c.InputRecord, r c.ResearchRequest) (c.Run, error) {
	if e.wait {
		<-ctx.Done()
		return c.Run{}, ctx.Err()
	}
	return c.Run{ID: "TEST_FIXTURE_RUN", ImplementationStatus: "TEST_FIXTURE_ONLY", CandidateAvailability: "ABSENT", Manifest: json.RawMessage(`{"coverage":"TEST_FIXTURE"}`), Summary: json.RawMessage(`{"selection":"COMPLETE","count":0}`), Items: []c.Item{{ID: "TEST000", Payload: json.RawMessage(`{"status":"FAIL","source":"TEST_FIXTURE"}`)}}}, nil
}
func appFixture(t *testing.T) (*Store, *AppRepository, *application.Service) {
	t.Helper()
	s, e := Open(context.Background(), filepath.Join(t.TempDir(), "TEST_FIXTURE.sqlite3"), true)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { s.Close() })
	r := NewAppRepository(s)
	if _, e = s.db.Exec("INSERT INTO migration_state VALUES(1,'TEST_FIXTURE','PUBLISHED','{}','TEST_FIXTURE')"); e != nil {
		t.Fatal(e)
	}
	service := application.New(r, testEngine{})
	if _, e = service.Import(context.Background(), []byte(`{"coverage_type":"TEST_FIXTURE"}`)); e != nil {
		t.Fatal(e)
	}
	return s, r, service
}
func appRequest(key string) c.ResearchRequest {
	return c.ResearchRequest{InputID: "TEST_FIXTURE", Strategy: c.R1, IdempotencyKey: key, BudgetSeconds: 10}
}

func TestApplicationSavedResultLifecycle(t *testing.T) {
	s, r, a := appFixture(t)
	ctx := context.Background()
	j, created, e := a.Submit(ctx, appRequest("one"))
	if e != nil || !created {
		t.Fatal(j, e)
	}
	same, created, e := a.Submit(ctx, appRequest("one"))
	if e != nil || created || same.ID != j.ID {
		t.Fatal("idempotency", e)
	}
	conflict := appRequest("one")
	conflict.Strategy = c.V2
	if _, _, e = a.Submit(ctx, conflict); e == nil {
		t.Fatal("same id different content accepted")
	}
	j, e = a.Execute(ctx, j.ID)
	if e != nil || j.State != "SUCCEEDED" {
		t.Fatal(j, e)
	}
	if e = r.VerifyRun(ctx, j.RunID); e != nil {
		t.Fatal(e)
	}
	var before strings.Builder
	if e = a.ExportTo(ctx, j.RunID, &before); e != nil {
		t.Fatal(e)
	}
	var parsed map[string]any
	if e = json.Unmarshal([]byte(before.String()), &parsed); e != nil {
		t.Fatal(e)
	}
	events, e := r.Events(ctx, j.ID)
	if e != nil || len(events) != 3 || events[2].State != "SUCCEEDED" {
		t.Fatal(events, e)
	}
	if _, e = s.db.Exec("UPDATE app_inputs SET raw_hash='bad'"); e == nil {
		t.Fatal("immutable input modified")
	}
	if _, e = s.db.Exec("DELETE FROM app_items"); e == nil {
		t.Fatal("immutable detail deleted")
	}
	if e = r.CachePut(ctx, "key", "version", "deps", []byte(`{"fact":1}`)); e != nil {
		t.Fatal(e)
	}
	if e = r.CacheClear(ctx); e != nil {
		t.Fatal(e)
	}
	path := s.Path
	s.Close()
	reopened, e := Open(ctx, path, false)
	if e != nil {
		t.Fatal(e)
	}
	defer reopened.Close()
	r2 := NewAppRepository(reopened)
	a2 := application.New(r2, testEngine{})
	var after strings.Builder
	if e = a2.ExportTo(ctx, j.RunID, &after); e != nil {
		t.Fatal(e)
	}
	if before.String() != after.String() {
		t.Fatal("restart/cache cleanup changed saved result")
	}
	if e = r2.VerifyRun(ctx, j.RunID); e != nil {
		t.Fatal(e)
	}
}

func TestPublishedResultIdempotencyChecksContentsAndHeader(t *testing.T) {
	_, r, a := appFixture(t)
	ctx := context.Background()
	j, _, e := a.Submit(ctx, appRequest("immutable"))
	if e != nil {
		t.Fatal(e)
	}
	j, e = a.Execute(ctx, j.ID)
	if e != nil {
		t.Fatal(e)
	}
	run, e := r.Run(ctx, j.RunID)
	if e != nil {
		t.Fatal(e)
	}
	run.Items, e = r.Items(ctx, j.RunID, 200, 0)
	if e != nil {
		t.Fatal(e)
	}
	if e = r.SaveRun(ctx, run); e != nil {
		t.Fatal("same bytes rejected", e)
	}
	run.Items[0].Payload = json.RawMessage(`{"status":"PASS","source":"TEST_FIXTURE"}`)
	if e = r.SaveRun(ctx, run); e == nil {
		t.Fatal("same run with changed item accepted")
	}
	run.Items, _ = r.Items(ctx, j.RunID, 200, 0)
	run.Strategy = c.V2
	if e = r.SaveRun(ctx, run); e == nil {
		t.Fatal("same run with changed strategy accepted")
	}
}

func TestApplicationSchemaTamperingRejectedOnReopen(t *testing.T) {
	s, _, _ := appFixture(t)
	path := s.Path
	if _, e := s.db.Exec("DROP TRIGGER app_items_no_update"); e != nil {
		t.Fatal(e)
	}
	s.Close()
	if other, e := Open(context.Background(), path, false); e == nil {
		other.Close()
		t.Fatal("silently repaired missing immutable trigger")
	}
}
func TestApplicationCancellationAndExplicitRecovery(t *testing.T) {
	_, r, a := appFixture(t)
	ctx := context.Background()
	j, _, e := a.Submit(ctx, appRequest("queued"))
	if e != nil {
		t.Fatal(e)
	}
	if j, e = a.Cancel(ctx, j.ID); e != nil || j.State != "CANCELLED" {
		t.Fatal(j, e)
	}
	j, _, e = a.Submit(ctx, appRequest("interrupted"))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = r.Transition(ctx, j.ID, "RUNNING", "TEST_FIXTURE_CRASH", "", 2); e != nil {
		t.Fatal(e)
	}
	recovered, e := a.Recover(ctx)
	if e != nil || len(recovered) != 1 || recovered[0].State != "INTERRUPTED" {
		t.Fatal(recovered, e)
	}
	resumed, _, e := a.Resume(ctx, j.ID, "retry")
	if e != nil || resumed.ParentID != j.ID || resumed.State != "QUEUED" {
		t.Fatal(resumed, e)
	}
	a.Engine = testEngine{wait: true}
	done := make(chan error, 1)
	go func() { _, e := a.Execute(ctx, resumed.ID); done <- e }()
	deadline := time.After(3 * time.Second)
	for {
		current, e := r.Job(ctx, resumed.ID)
		if e != nil {
			t.Fatal(e)
		}
		if current.State == "RUNNING" {
			break
		}
		select {
		case <-deadline:
			t.Fatal("worker did not start")
		case <-time.After(time.Millisecond):
		}
	}
	if _, e = a.Cancel(ctx, resumed.ID); e != nil {
		t.Fatal(e)
	}
	select {
	case e = <-done:
		if !errors.Is(e, context.Canceled) {
			t.Fatal(e)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("cancel did not reach engine")
	}
	j, e = r.Job(ctx, resumed.ID)
	if e != nil || j.State != "CANCELLED" {
		t.Fatal(j, e)
	}
}
func TestFactCacheIdentityCorruptionAndCapacity(t *testing.T) {
	s, r, _ := appFixture(t)
	ctx := context.Background()
	base := CacheIdentity{Version: "TEST_FIXTURE_V1", InputHash: "bytes", HistoryHash: "history", Cutoff: "2026-01-02T16:00:00+08:00", Instrument: "TEST000", Window: "315", Basis: "adjusted", Policy: "visibility-v1", Evidence: []string{"row1"}}
	key, e := CacheKey(base)
	if e != nil {
		t.Fatal(e)
	}
	for _, modify := range []func(*CacheIdentity){func(i *CacheIdentity) { i.Cutoff = "later" }, func(i *CacheIdentity) { i.Evidence = []string{"revised"} }, func(i *CacheIdentity) { i.Policy = "v2" }, func(i *CacheIdentity) { i.Basis = "raw" }} {
		other := base
		modify(&other)
		next, _ := CacheKey(other)
		if next == key {
			t.Fatal("unsafe cache collision")
		}
	}
	if e = r.CachePut(ctx, key, "v1", "deps", []byte(`{"fact":2}`)); e != nil {
		t.Fatal(e)
	}
	if _, ok, e := r.CacheGet(ctx, key, "v1", "deps"); e != nil || !ok {
		t.Fatal(ok, e)
	}
	if _, ok, e := r.CacheGet(ctx, key, "v2", "deps"); e != nil || ok {
		t.Fatal(ok, e)
	}
	if _, e = s.db.Exec("UPDATE app_fact_cache SET payload=? WHERE cache_key=?", []byte("bad"), key); e != nil {
		t.Fatal(e)
	}
	if _, ok, e := r.CacheGet(ctx, key, "v1", "deps"); e != nil || ok {
		t.Fatal("corruption was consumed", ok, e)
	}
	if e = r.CachePut(ctx, "too-large", "v1", "deps", make([]byte, FactCacheCapacity+1)); e == nil {
		t.Fatal("capacity not enforced")
	}
	if e = r.VerifyRun(ctx, "absent"); e == nil {
		t.Fatal("absent run verified")
	}
}
func TestUnpublishedRunAndMigrationAreNotReadable(t *testing.T) {
	s, r, a := appFixture(t)
	ctx := context.Background()
	if _, e := s.db.Exec("UPDATE migration_state SET status='MIGRATING'"); e != nil {
		t.Fatal(e)
	}
	if _, _, e := a.Submit(ctx, appRequest("blocked")); e == nil {
		t.Fatal("migration gate bypassed")
	}
	run := c.Run{ID: "TEST_FIXTURE_PARTIAL", InputID: "TEST_FIXTURE", Strategy: c.R1, Manifest: json.RawMessage(`{}`), Summary: json.RawMessage(`{}`), Items: []c.Item{{ID: "DUP", Payload: json.RawMessage(`{}`)}, {ID: "DUP", Payload: json.RawMessage(`{}`)}}}
	if e := r.SaveRun(ctx, run); e == nil {
		t.Fatal("duplicate item accepted")
	}
	if _, e := r.Run(ctx, run.ID); e == nil {
		t.Fatal("partial run visible")
	}
}

func (testEngine) Start(ctx context.Context, _ int) (context.Context, func(), error) {
	return ctx, func() {}, nil
}
