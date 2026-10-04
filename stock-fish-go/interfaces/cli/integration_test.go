package cli_test

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/core"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/native"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/cli"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/httpapi"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func TestHTTPAuthenticationQueueAndCLIReadParity(t *testing.T) {
	store, repo, service := appFixture(t)
	ctx := context.Background()
	server := httpapi.New(ctx, service, "TEST_FIXTURE_TOKEN")
	defer server.Close()
	request := func(method, path, body, token string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, "http://127.0.0.1"+path, bytes.NewBufferString(body))
		r.Header.Set("Idempotency-Key", "TEST_FIXTURE_HTTP")
		if token != "" {
			r.Header.Set("Authorization", "Bearer "+token)
		}
		w := httptest.NewRecorder()
		server.ServeHTTP(w, r)
		return w
	}
	if w := request("POST", "/api/research/r1/run", `{"input_id":"TEST_FIXTURE"}`, ""); w.Code != 401 {
		t.Fatal("unauthenticated write", w.Code)
	}
	if w := request("POST", "/api/research/r1/run", `{"input_id":"TEST_FIXTURE","path":"/etc/passwd"}`, "TEST_FIXTURE_TOKEN"); w.Code != 400 {
		t.Fatal("path injection accepted", w.Code)
	}
	w := request("POST", "/api/research/r1/run", `{"input_id":"TEST_FIXTURE"}`, "TEST_FIXTURE_TOKEN")
	if w.Code != 202 {
		t.Fatal(w.Code, w.Body.String())
	}
	var accepted struct {
		ID string `json:"job_id"`
	}
	if e := json.Unmarshal(w.Body.Bytes(), &accepted); e != nil {
		t.Fatal(e)
	}
	var job c.Job
	deadline := time.Now().Add(4 * time.Second)
	for time.Now().Before(deadline) {
		job, _ = repo.Job(ctx, accepted.ID)
		if job.State == "SUCCEEDED" || job.State == "FAILED" {
			break
		}
		time.Sleep(time.Millisecond)
	}
	if job.State != "SUCCEEDED" {
		t.Fatal(job)
	}
	w = request("GET", "/api/strategy-experiments/"+job.RunID, "", "")
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	var out, log bytes.Buffer
	if e := cli.Run(ctx, []string{"show", "--db", store.Path, "--id", job.RunID}, &out, &log, "TEST_FIXTURE_BUILD"); e != nil {
		t.Fatal(e)
	}
	var hv, cv any
	if e := json.Unmarshal(w.Body.Bytes(), &hv); e != nil {
		t.Fatal(e)
	}
	if e := json.Unmarshal(out.Bytes(), &cv); e != nil {
		t.Fatal(e)
	}
	if !reflect.DeepEqual(hv, cv) {
		t.Fatal("CLI/API diverged")
	}
	jobs, _ := repo.Jobs(ctx, "")
	if len(jobs) != 1 {
		t.Fatal("query created work")
	}
	w = request("GET", "/api/candidate-schedule", "", "")
	if !bytes.Contains(w.Body.Bytes(), []byte(`"PAUSED"`)) {
		t.Fatal("scheduler enabled")
	}
}

func TestNativeInputResearchRestartAndCacheLifecycle(t *testing.T) {
	dir := os.Getenv("STOCKFISH_R1_ORACLE")
	if dir == "" {
		if os.Getenv("STOCKFISH_FORMAL_ACCEPTANCE") == "1" {
			t.Fatal("FORMAL_ACCEPTANCE_BLOCKED_ORACLE_REQUIRED")
		}
		t.Skip("explicit Python TEST_FIXTURE oracle required")
	}
	store, repo, _ := appFixture(t)
	var metricsLog bytes.Buffer
	engine := &native.NativeEngine{Progress: &metricsLog, SourceHash: "TEST_FIXTURE_BUILD", ConfigHashes: cli.ConfigHashes, Cache: repo, DatabaseIdentity: map[string]string{"batch_projection_manifest_hash": "STALE_TEST_FIXTURE"}, DatabaseIdentitySource: store.PublishedResearchIdentity}
	service := application.New(repo, engine)
	ctx := context.Background()
	raw, e := os.ReadFile(filepath.Join(dir, "repair.json"))
	if e != nil {
		t.Fatal(e)
	}
	in, e := service.Import(ctx, raw)
	if e != nil {
		t.Fatal(e)
	}
	var baseline []c.Item
	for _, key := range []string{"native-cold", "native-warm", "native-cleared"} {
		if key == "native-warm" {
			var wrapper map[string]json.RawMessage
			if e = json.Unmarshal(raw, &wrapper); e != nil {
				t.Fatal(e)
			}
			wrapper["purpose"] = json.RawMessage(`"TEST_FIXTURE_SECOND_REGISTRATION"`)
			other, _ := json.Marshal(wrapper)
			if _, e = service.Import(ctx, other); e != nil {
				t.Fatal(e)
			}
		}
		if key == "native-cleared" {
			if e = repo.CacheClear(ctx); e != nil {
				t.Fatal(e)
			}
		}
		j, _, e := service.Submit(ctx, c.ResearchRequest{InputID: in.ID, Strategy: c.R1, BudgetSeconds: 60, IdempotencyKey: key})
		if e != nil {
			t.Fatal(e)
		}
		metricsLog.Reset()
		j, e = service.Execute(ctx, j.ID)
		if e != nil {
			t.Fatal(e)
		}
		if e = repo.VerifyRun(ctx, j.RunID); e != nil {
			t.Fatal(e)
		}
		items, e := repo.Items(ctx, j.RunID, 200, 0)
		if e != nil {
			t.Fatal(e)
		}
		if key == "native-cold" {
			baseline = items
		} else if !reflect.DeepEqual(baseline, items) {
			t.Fatal("cold/warm/clear semantic mismatch")
		}
		run, e := repo.Run(ctx, j.RunID)
		if e != nil {
			t.Fatal(e)
		}
		var manifest struct {
			Identity map[string]string `json:"database_identity"`
		}
		if e = json.Unmarshal(run.Manifest, &manifest); e != nil {
			t.Fatal(e)
		}
		current, e := store.ResearchIdentity(ctx)
		if e != nil || !reflect.DeepEqual(manifest.Identity, current) {
			t.Fatal("long-lived engine retained a stale database identity", e)
		}
		var summary struct {
			Hits    int       `json:"fact_cache_hits"`
			Metrics c.Metrics `json:"metrics"`
		}
		if e = json.Unmarshal(run.Summary, &summary); e != nil {
			t.Fatal(e)
		}
		if summary.Metrics.CacheBytes == nil || *summary.Metrics.CacheBytes <= 0 {
			t.Fatal("cache occupancy missing")
		}
		var final c.Metrics
		lines := bytes.Split(bytes.TrimSpace(metricsLog.Bytes()), []byte("\n"))
		if e = json.Unmarshal(lines[len(lines)-1], &final); e != nil {
			t.Fatal(e)
		}
		if !final.Final || final.RunID != j.RunID || final.InputID != in.ID || final.InputHash != in.RawHash || final.SourceHash != "TEST_FIXTURE_BUILD" || final.Elapsed < summary.Metrics.Elapsed {
			t.Fatalf("final metrics identity or timing missing: %+v", final)
		}
		if final.CacheReadBytes != summary.Metrics.CacheReadBytes || final.CacheErrors != 0 {
			t.Fatal("final cache accounting mismatch")
		}
		if key == "native-warm" && final.CacheSamples != 2 {
			t.Fatalf("warm engine must sample only start/end, got %d", final.CacheSamples)
		}
		if key == "native-warm" && final.CacheHits != int64(len(items)) {
			t.Fatal("warm hit accounting missing")
		}
		if key != "native-warm" && final.CacheMisses != int64(len(items)) {
			t.Fatal("cold miss accounting missing")
		}
		t.Logf("%s final metrics: %s", key, lines[len(lines)-1])
		if key == "native-warm" && summary.Metrics.CacheReadBytes <= 0 {
			t.Fatal("warm SQLite payload metrics lost across NativeEngine budget context")
		}
		if key != "native-warm" && summary.Metrics.CacheReadBytes != 0 {
			t.Fatal("cache miss inflated payload bytes")
		}
		if key == "native-warm" && summary.Hits != len(items) {
			t.Fatalf("warm cache %d/%d", summary.Hits, len(items))
		}
	}
}

type testEngine struct{}

func (testEngine) Validate(raw []byte) (c.InputRecord, error) {
	return c.InputRecord{ID: "TEST_FIXTURE", Raw: raw, RawHash: core.Hash(raw), Observation: "2026-01-02", Cutoff: "2026-01-02T16:00:00+08:00", Coverage: "TEST_FIXTURE"}, nil
}
func (testEngine) Evaluate(ctx context.Context, in c.InputRecord, r c.ResearchRequest) (c.Run, error) {
	return c.Run{ID: "TEST_FIXTURE_RUN", ImplementationStatus: "TEST_FIXTURE_ONLY", CandidateAvailability: "ABSENT", Manifest: json.RawMessage(`{"coverage":"TEST_FIXTURE"}`), Summary: json.RawMessage(`{"selection":"COMPLETE"}`), Items: []c.Item{{ID: "TEST000", Payload: json.RawMessage(`{"status":"FAIL"}`)}}}, nil
}
func appFixture(t *testing.T) (*sqlitev2.Store, *sqlitev2.AppRepository, *application.Service) {
	t.Helper()
	store, e := sqlitev2.Open(context.Background(), filepath.Join(t.TempDir(), "TEST_FIXTURE.sqlite3"), true)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { store.Close() })
	_, e = store.DB().Exec("INSERT INTO migration_state VALUES(1,'TEST_FIXTURE','PUBLISHED','{}','TEST_FIXTURE')")
	if e != nil {
		t.Fatal(e)
	}
	repo := sqlitev2.NewAppRepository(store)
	service := application.New(repo, testEngine{})
	if _, e = service.Import(context.Background(), []byte(`{"coverage_type":"TEST_FIXTURE"}`)); e != nil {
		t.Fatal(e)
	}
	return store, repo, service
}

func (testEngine) Start(ctx context.Context, _ int) (context.Context, func(), error) {
	return ctx, func() {}, nil
}
