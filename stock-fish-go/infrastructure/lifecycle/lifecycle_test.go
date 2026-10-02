package lifecycle

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/core"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
)

func isolatedFixture(t *testing.T) (string, Config) {
	t.Helper()
	ctx := context.Background()
	root := t.TempDir()
	original := filepath.Join(root, "TEST_FIXTURE_original.db")
	db, e := sql.Open("sqlite", sqlitev2.URI(original, false))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = db.Exec(`CREATE TABLE documents(namespace TEXT,document_id TEXT,payload TEXT,content_hash TEXT,created_at TEXT,PRIMARY KEY(namespace,document_id));CREATE TABLE history_evidence_rows(row_hash TEXT PRIMARY KEY,kind TEXT,instrument_id TEXT,session TEXT,payload TEXT);CREATE TABLE history_partition_members(partition_hash TEXT,row_hash TEXT,kind TEXT,instrument_id TEXT,session TEXT,PRIMARY KEY(partition_hash,row_hash));`); e != nil {
		t.Fatal(e)
	}
	db.Close()
	source := filepath.Join(root, "frozen.db")
	target := filepath.Join(root, "v2.db")
	if _, e = sqlitev2.Snapshot(ctx, original, source); e != nil {
		t.Fatal(e)
	}
	if _, e = sqlitev2.Migrate(ctx, source, target, sqlitev2.Options{BatchSize: 5}); e != nil {
		t.Fatal(e)
	}
	if _, e = sqlitev2.Verify(ctx, source, target, sqlitev2.Options{}); e != nil {
		t.Fatal(e)
	}
	if e = sqlitev2.Publish(ctx, target); e != nil {
		t.Fatal(e)
	}
	conf, e := Init(ctx, root, source, target, "127.0.0.1:18097")
	if e != nil {
		t.Fatal(e)
	}
	return root, conf
}
func fixtureInput(t *testing.T) c.InputRecord {
	t.Helper()
	raw, e := os.ReadFile("../../core/testdata/input_contract_python.json")
	if e != nil {
		t.Fatal(e)
	}
	var fixture struct {
		Input  json.RawMessage `json:"input"`
		Cutoff string          `json:"cutoff"`
	}
	if e = json.Unmarshal(raw, &fixture); e != nil {
		t.Fatal(e)
	}
	frozen, _ := json.Marshal(map[string]any{"input": fixture.Input, "cutoff": fixture.Cutoff, "purpose": "TEST_FIXTURE_ISOLATED_LIFECYCLE"})
	f, e := core.DecodeFrozen(frozen)
	if e != nil {
		t.Fatal(e)
	}
	return c.InputRecord{ID: "TEST_FIXTURE_input", Raw: frozen, RawHash: core.Hash(frozen), Observation: f.Input.Observation, Cutoff: f.Cutoff, Coverage: f.Input.Coverage}
}
func TestCutoverInputReplayAndGoOnlyRecovery(t *testing.T) {
	ctx := context.Background()
	root, initial := isolatedFixture(t)
	before, e := sqlitev2.FileHash(ctx, initial.Source)
	if e != nil {
		t.Fatal(e)
	}
	if p, e := Preflight(ctx, root); e != nil || p.Status != "READY_FOR_ISOLATED_CUTOVER" {
		t.Fatal(p, e)
	}
	conf, e := Cutover(ctx, root)
	if e != nil || conf.Mode != "GO_V2" {
		t.Fatal(conf, e)
	}
	store, e := sqlitev2.Open(ctx, conf.Target, false)
	if e != nil {
		t.Fatal(e)
	}
	repo := sqlitev2.NewAppRepository(store)
	input := fixtureInput(t)
	if e = repo.PutInput(ctx, input); e != nil {
		t.Fatal(e)
	}
	store.Close()
	rollback, e := Rollback(ctx, root)
	if e != nil || rollback.Status != "ROLLED_BACK_WITH_REPLAY" || len(rollback.Mappings) != 1 {
		t.Fatal(rollback, e)
	}
	legacy, e := readDB(ctx, rollback.Replay)
	if e != nil {
		t.Fatal(e)
	}
	var raw []byte
	var hash string
	if e = legacy.QueryRow("SELECT payload,content_hash FROM documents WHERE namespace='candidate_inputs' AND document_id=?", rollback.Mappings[input.ID]).Scan(&raw, &hash); e != nil {
		t.Fatal(e)
	}
	if core.Hash(raw) != hash || rollback.Mappings[input.ID] != "market_"+hash {
		t.Fatal("legacy replay identity")
	}
	legacy.Close()
	conf, e = Cutover(ctx, root)
	if e != nil {
		t.Fatal(e)
	}
	store, e = sqlitev2.Open(ctx, conf.Target, false)
	if e != nil {
		t.Fatal(e)
	}
	repo = sqlitev2.NewAppRepository(store)
	run := c.Run{ID: "TEST_FIXTURE_run", InputID: input.ID, Strategy: c.R1, CreatedAt: time.Now().UTC().Format(time.RFC3339Nano), ImplementationStatus: "TEST_FIXTURE", CandidateAvailability: "UNAVAILABLE", Manifest: json.RawMessage(`{"data_class":"TEST_FIXTURE"}`), Summary: json.RawMessage(`{"data_class":"TEST_FIXTURE"}`), Items: []c.Item{{ID: "TEST_FIXTURE.SZ", Payload: json.RawMessage(`{"data_class":"TEST_FIXTURE","state":"UNKNOWN"}`)}}}
	if e = repo.SaveRun(ctx, run); e != nil {
		t.Fatal(e)
	}
	store.Close()
	blocked, e := Rollback(ctx, root)
	if e != nil || blocked.Status != "BLOCKED_UNREPRESENTABLE" {
		t.Fatal(blocked, e)
	}
	conf, e = Load(root)
	if e != nil || conf.Mode != "GO_V2" {
		t.Fatal("blocked rollback switched modes", conf, e)
	}
	if _, e = VerifyExport(ctx, root, blocked.Export); e != nil {
		t.Fatal(e)
	}
	// Simulate loss of access to the active target, without deleting new writes.
	if e = os.Rename(conf.Target, conf.Target+".interrupted"); e != nil {
		t.Fatal(e)
	}
	restored, e := Restore(ctx, root, blocked.Export)
	if e != nil || restored.Mode != "GO_V2" {
		t.Fatal(restored, e)
	}
	store, e = sqlitev2.Open(ctx, restored.Target, false)
	if e != nil {
		t.Fatal(e)
	}
	defer store.Close()
	repo = sqlitev2.NewAppRepository(store)
	if e = repo.VerifyRun(ctx, run.ID); e != nil {
		t.Fatal(e)
	}
	reloaded, e := repo.Input(ctx, input.ID)
	if e != nil || reloaded.RawHash != input.RawHash {
		t.Fatal(reloaded, e)
	}
	after, e := sqlitev2.FileHash(ctx, initial.Source)
	if e != nil || before != after {
		t.Fatal("frozen source modified", e)
	}
}
func TestRestorePendingJobInterruptsAndVerifiesEventLedger(t *testing.T) {
	ctx := context.Background()
	root, _ := isolatedFixture(t)
	conf, e := Cutover(ctx, root)
	if e != nil {
		t.Fatal(e)
	}
	store, e := sqlitev2.Open(ctx, conf.Target, false)
	if e != nil {
		t.Fatal(e)
	}
	repo := sqlitev2.NewAppRepository(store)
	job := c.Job{ID: "TEST_FIXTURE_job", Key: "TEST_FIXTURE_key", Request: c.ResearchRequest{InputID: "TEST_FIXTURE_input", Strategy: c.R1, BudgetSeconds: 1, IdempotencyKey: "TEST_FIXTURE_key"}, CreatedAt: now(), UpdatedAt: now()}
	if _, _, e = repo.Submit(ctx, job); e != nil {
		t.Fatal(e)
	}
	if _, e = repo.Transition(ctx, job.ID, "RUNNING", "TEST_FIXTURE_START", "", 0); e != nil {
		t.Fatal(e)
	}
	store.Close()
	if _, e = Preflight(ctx, root); e == nil || !strings.Contains(e.Error(), "UNFINISHED_WORK") {
		t.Fatal("unfinished work accepted", e)
	}
	if _, e = ExportChanges(ctx, root); e != nil {
		t.Fatal(e)
	}
	conf, e = Load(root)
	if e != nil {
		t.Fatal(e)
	}
	restored, e := Restore(ctx, root, conf.LastExport)
	if e != nil {
		t.Fatal(e)
	}
	store, e = sqlitev2.Open(ctx, restored.Target, false)
	if e != nil {
		t.Fatal(e)
	}
	defer store.Close()
	repo = sqlitev2.NewAppRepository(store)
	got, e := repo.Job(ctx, job.ID)
	if e != nil || got.State != "INTERRUPTED" || got.Revision != 2 {
		t.Fatal(got, e)
	}
	events, e := repo.Events(ctx, job.ID)
	if e != nil || len(events) != 3 {
		t.Fatal(events, e)
	}
	if _, e = ExportChanges(ctx, root); e != nil {
		t.Fatal("restored interruption ledger failed verification", e)
	}
}
func TestLifecyclePathOwnershipSpaceAndTamperingGuards(t *testing.T) {
	ctx := context.Background()
	root, conf := isolatedFixture(t)
	release, e := AcquireWriter(root, "TEST_FIXTURE")
	if e != nil {
		t.Fatal(e)
	}
	if _, e = Preflight(ctx, root); e == nil {
		t.Fatal("writer collision accepted")
	}
	if _, e = Cutover(ctx, root); e == nil {
		t.Fatal("cutover while writer active")
	}
	if e = release(); e != nil {
		t.Fatal(e)
	}
	old := availableBytes
	availableBytes = func(string) (int64, error) { return MinimumReserve - 1, nil }
	_, e = Preflight(ctx, root)
	availableBytes = old
	if e == nil || !strings.Contains(e.Error(), "SPACE_RESERVE") {
		t.Fatal("space guard", e)
	}
	if _, e = inside(root, filepath.Join(root, "..", "production.db")); e == nil {
		t.Fatal("path escape accepted")
	}
	ctxCancelled, cancel := context.WithCancel(ctx)
	cancel()
	if _, e = Preflight(ctxCancelled, root); e == nil {
		t.Fatal("cancel ignored")
	}
	if _, e = Cutover(ctx, root); e != nil {
		t.Fatal(e)
	}
	export, e := ExportChanges(ctx, root)
	if e != nil {
		t.Fatal(e)
	}
	conf, e = Load(root)
	if e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(export.Ledger, []byte("tampered"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = VerifyExport(ctx, root, conf.LastExport); e == nil {
		t.Fatal("ledger tamper accepted")
	}
}

func TestLegacyReplayRejectsConflictsAndUnprocessedIncrement(t *testing.T) {
	ctx := context.Background()
	root, _ := isolatedFixture(t)
	conf, e := Cutover(ctx, root)
	if e != nil {
		t.Fatal(e)
	}
	store, e := sqlitev2.Open(ctx, conf.Target, false)
	if e != nil {
		t.Fatal(e)
	}
	if e = sqlitev2.NewAppRepository(store).PutInput(ctx, fixtureInput(t)); e != nil {
		t.Fatal(e)
	}
	store.Close()
	back, e := Rollback(ctx, root)
	if e != nil {
		t.Fatal(e)
	}
	db, e := sql.Open("sqlite", sqlitev2.URI(back.Replay, false))
	if e != nil {
		t.Fatal(e)
	}
	if e = putLegacy(ctx, db, "TEST_FIXTURE", "same_key", []byte(`{"a":1}`)); e != nil {
		t.Fatal(e)
	}
	if e = putLegacy(ctx, db, "TEST_FIXTURE", "same_key", []byte(`{"a":1}`)); e != nil {
		t.Fatal("idempotent replay failed", e)
	}
	if e = putLegacy(ctx, db, "TEST_FIXTURE", "same_key", []byte(`{"a":2}`)); e == nil {
		t.Fatal("same-key conflict ignored")
	}
	db.Close()
	if _, e = Preflight(ctx, root); e == nil || !strings.Contains(e.Error(), "UNHANDLED_LEGACY_INCREMENTAL_CHANGES") {
		t.Fatal("new legacy writes silently bypassed", e)
	}
}

func TestAbandonedPartialRunPreflightBackupAndRestore(t *testing.T) {
	ctx := context.Background()
	root, _ := isolatedFixture(t)
	conf, e := Cutover(ctx, root)
	if e != nil {
		t.Fatal(e)
	}
	store, e := sqlitev2.Open(ctx, conf.Target, false)
	if e != nil {
		t.Fatal(e)
	}
	repo := sqlitev2.NewAppRepository(store)
	input := fixtureInput(t)
	if e = repo.PutInput(ctx, input); e != nil {
		t.Fatal(e)
	}
	run := c.Run{ID: "TEST_FIXTURE_PARTIAL_RUN", InputID: input.ID, Strategy: c.R1, CreatedAt: "2026-01-02", ImplementationStatus: "TEST_FIXTURE", CandidateAvailability: "UNAVAILABLE", Manifest: json.RawMessage(`{"purpose":"TEST_FIXTURE"}`), Summary: json.RawMessage(`{"purpose":"TEST_FIXTURE"}`)}
	for i := 0; i < 51; i++ {
		raw, _ := json.Marshal(map[string]any{"fact": map[string]any{"name": "TEST_FIXTURE"}, "decision": map[string]any{"candidate_status": "CORE_PASS", "rank": i + 1}})
		if i == 50 {
			raw = []byte(`{"decision":123}`)
		}
		run.Items = append(run.Items, c.Item{ID: fmt.Sprintf("TEST%03d.SZ", i), Payload: raw})
	}
	if e = repo.SaveRun(ctx, run); e == nil {
		t.Fatal("fixture second batch did not fail")
	}
	var count int
	if e = store.DB().QueryRow("SELECT count(*) FROM app_items WHERE run_id=?", run.ID).Scan(&count); e != nil || count != 50 {
		t.Fatal("committed prefix", count, e)
	}
	if _, e = Preflight(ctx, root); e == nil || !strings.Contains(e.Error(), "UNFINISHED_WORK") {
		t.Fatal("unsealed partial run not blocked", e)
	}
	sealed, e := repo.AbandonUnpublishedRuns(ctx)
	if e != nil || len(sealed) != 1 || sealed[0] != run.ID {
		t.Fatal(sealed, e)
	}
	if e = store.Close(); e != nil {
		t.Fatal(e)
	}
	ready, e := Preflight(ctx, root)
	if e != nil || ready.UnpublishedRuns != 0 || ready.VerifiedAbandonedRuns != 1 {
		t.Fatal(ready, e)
	}
	blocked, e := Rollback(ctx, root)
	if e != nil || blocked.Status != "BLOCKED_UNREPRESENTABLE" {
		t.Fatal(blocked, e)
	}
	manifest, e := VerifyExport(ctx, root, blocked.Export)
	if e != nil {
		t.Fatal(e)
	}
	found := false
	for _, kind := range manifest.IncompatibleKinds {
		found = found || kind == "ABANDONED_RUN"
	}
	if !found {
		t.Fatal("partial seal was silently treated as legacy-compatible", manifest)
	}
	restored, e := Restore(ctx, root, blocked.Export)
	if e != nil {
		t.Fatal(e)
	}
	store, e = sqlitev2.Open(ctx, restored.Target, false)
	if e != nil {
		t.Fatal(e)
	}
	repo = sqlitev2.NewAppRepository(store)
	if e = repo.VerifyAbandonedRun(ctx, run.ID); e != nil {
		t.Fatal(e)
	}
	if _, e = repo.Run(ctx, run.ID); !errors.Is(e, sql.ErrNoRows) {
		t.Fatal("abandoned prefix exposed as published", e)
	}
	if e = store.DB().QueryRow("SELECT count(*) FROM app_items WHERE run_id=?", run.ID).Scan(&count); e != nil || count != 50 {
		t.Fatal("restored prefix", count, e)
	}
	changed := []byte(`{"purpose":"TEST_FIXTURE_TAMPERED_SEAL"}`)
	if _, e = store.DB().Exec("UPDATE app_runs SET summary=?,summary_hash=? WHERE run_id=?", changed, c.Hash(changed), run.ID); e != nil {
		t.Fatal(e)
	}
	if e = store.Close(); e != nil {
		t.Fatal(e)
	}
	if _, e = Preflight(ctx, root); e == nil || !strings.Contains(e.Error(), "ABANDONED_RUN_CONTENT_CHANGED") {
		t.Fatal("tampered sealed header bypassed preflight", e)
	}
}
