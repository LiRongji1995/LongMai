package httpapi_test

import (
	"bytes"
	"compress/zlib"
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/cli"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/httpapi"
)

type fixture struct {
	store  *sqlitev2.Store
	repo   *sqlitev2.AppRepository
	server *httpapi.Server
}

func setup(t *testing.T) fixture {
	t.Helper()
	ctx := context.Background()
	root := t.TempDir()
	source, snapshot, target := filepath.Join(root, "TEST_FIXTURE_source.db"), filepath.Join(root, "TEST_FIXTURE_snapshot.db"), filepath.Join(root, "TEST_FIXTURE.db")
	db, e := sql.Open("sqlite", sqlitev2.URI(source, false))
	if e != nil {
		t.Fatal(e)
	}
	_, e = db.Exec(`CREATE TABLE documents(namespace TEXT,document_id TEXT,payload TEXT,content_hash TEXT,created_at TEXT,PRIMARY KEY(namespace,document_id));CREATE TABLE history_evidence_rows(row_hash TEXT PRIMARY KEY,kind TEXT,instrument_id TEXT,session TEXT,payload TEXT);CREATE TABLE history_partition_members(partition_hash TEXT,row_hash TEXT,kind TEXT,instrument_id TEXT,session TEXT,PRIMARY KEY(partition_hash,row_hash));`)
	closeErr := db.Close()
	if e != nil {
		t.Fatal(e)
	}
	if closeErr != nil {
		t.Fatal(closeErr)
	}
	if _, e = sqlitev2.Snapshot(ctx, source, snapshot); e != nil {
		t.Fatal(e)
	}
	if _, e = sqlitev2.Migrate(ctx, snapshot, target, sqlitev2.Options{}); e != nil {
		t.Fatal(e)
	}
	if _, e = sqlitev2.Verify(ctx, snapshot, target, sqlitev2.Options{}); e != nil {
		t.Fatal(e)
	}
	store, e := sqlitev2.Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { store.Close() })
	// Exercise the actual new publication gate. An empty synthetic source is
	// sufficient for transport fixtures; no readiness metadata is manufactured.
	if _, e = store.BackfillBatchProjections(ctx, sqlitev2.BatchProjectionOptions{}); e != nil {
		t.Fatal(e)
	}
	if _, e = store.VerifyBatchProjections(ctx); e != nil {
		t.Fatal(e)
	}
	if e = sqlitev2.Publish(ctx, target); e != nil {
		t.Fatal(e)
	}
	repo := sqlitev2.NewAppRepository(store)
	server := httpapi.New(ctx, application.New(repo, nil), "TEST_FIXTURE_TOKEN")
	t.Cleanup(server.Close)
	return fixture{store, repo, server}
}
func (f fixture) put(t *testing.T, ns, id string, value any) {
	t.Helper()
	raw, e := json.Marshal(value)
	if e != nil {
		t.Fatal(e)
	}
	h := c.Hash(raw)
	_ = fixtureInsert(t, f.store.DB(), "INSERT OR IGNORE INTO evidence_objects(raw_sha256,raw_size,codec,payload)VALUES(?,?,'raw',?)", h, len(raw), raw)
	if e != nil {
		t.Fatal(e)
	}
	var eid int64
	e = f.store.DB().QueryRow("SELECT id FROM evidence_objects WHERE raw_sha256=?", h).Scan(&eid)
	if e != nil {
		t.Fatal(e)
	}
	_ = fixtureInsert(t, f.store.DB(), "INSERT INTO legacy_records VALUES(?,?,?,?,?)", ns, id, h, eid, id)
	if e != nil {
		t.Fatal(e)
	}
}
func (f fixture) get(path string) *httptest.ResponseRecorder {
	r := httptest.NewRequest("GET", "http://127.0.0.1"+path, nil)
	w := httptest.NewRecorder()
	f.server.ServeHTTP(w, r)
	return w
}
func object(t *testing.T, w *httptest.ResponseRecorder) map[string]json.RawMessage {
	t.Helper()
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	m := map[string]json.RawMessage{}
	if e := json.Unmarshal(w.Body.Bytes(), &m); e != nil {
		t.Fatal(e)
	}
	return m
}
func countRows(t *testing.T, m map[string]json.RawMessage) int {
	t.Helper()
	var rows []json.RawMessage
	if e := json.Unmarshal(m["items"], &rows); e != nil {
		t.Fatal(e)
	}
	return len(rows)
}
func seedLegacy(t *testing.T, f fixture) {
	rows := []any{map[string]any{"instrument_id": "TEST_A", "rank": 1, "decision_status": "FOCUS", "reason": "=TEST_FIXTURE", "ret1_pct": -1.5}, map[string]any{"instrument_id": "TEST_B", "rank": nil, "decision_status": "WATCH"}}
	f.put(t, "strategy_experiment_runs", "TEST_RUN", map[string]any{"run_id": "TEST_RUN", "purpose": "TEST_FIXTURE", "observation_date": "2026-01-02", "results": map[string]any{c.V2: map[string]any{"strategy_id": c.V2, "items": rows}}, "selection_results": map[string]any{"STAGE_AWARE_MOMENTUM_V2_FOCUS": map[string]any{"selected_ids": []string{"TEST_A"}, "items": []any{map[string]any{"instrument_id": "TEST_A", "reason_codes": []string{"TEST_FOCUS"}, "close_location": "HIGH"}}}}})
	all := []any{}
	for i := 0; i < 55; i++ {
		all = append(all, map[string]any{"instrument_id": fmt.Sprintf("TEST_%03d", i), "decision_status": "WATCH"})
	}
	header := map[string]any{"run_id": "TEST_RUN", "source_run_hash": "TEST_FIXTURE_HASH", "results": map[string]any{c.V2: map[string]any{"status": "COMPLETE", "counts": map[string]int{"total": 55}}}, "selections": map[string]any{c.V2: map[string]int{"all": 55, "focus": 1, "unselected": 54}}}
	f.put(t, "strategy_experiment_summaries", "TEST_RUN", header)
	for _, selection := range []string{"all", "focus", "unselected"} {
		chosen := all
		if selection == "focus" {
			chosen = all[:1]
		}
		if selection == "unselected" {
			chosen = all[1:]
		}
		for chunk := 0; chunk*50 < len(chosen); chunk++ {
			keyRaw, _ := json.Marshal(map[string]any{"strategy_id": c.V2, "selection": selection, "chunk": chunk})
			canonical, _ := c.Canonical(keyRaw)
			stop := min((chunk+1)*50, len(chosen))
			f.put(t, "strategy_experiment_summary_pages", "TEST_RUN_"+c.Hash(canonical), map[string]any{"run_id": "TEST_RUN", "source_run_hash": "TEST_FIXTURE_HASH", "items": chosen[chunk*50 : stop]})
		}
	}
	for i := 0; i < 4; i++ {
		id := fmt.Sprintf("TEST_RUN_%d", i)
		f.put(t, "strategy_experiment_runs", id, map[string]any{"run_id": id, "purpose": "TEST_FIXTURE"})
		f.put(t, "strategy_experiment_summaries", id, map[string]any{"run_id": id, "purpose": "TEST_FIXTURE"})
	}
	f.put(t, "strategy_trading_session_observations", "TEST_OUTCOME", map[string]any{"run_id": "TEST_RUN", "instrument_id": "TEST_A", "return_pct": nil, "status": "UNKNOWN"})
	f.put(t, "research_episode_observations", "TEST_TRACKING", map[string]any{"run_id": "TEST_RUN", "instrument_id": "TEST_A", "status": "UNKNOWN"})
}
func TestSavedLegacyPaginationDetailsAndExport(t *testing.T) {
	f := setup(t)
	seedLegacy(t, f)
	w := f.get("/api/strategy-experiments?limit=2&offset=1")
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	var runs []map[string]any
	if e := json.Unmarshal(w.Body.Bytes(), &runs); e != nil {
		t.Fatal(e)
	}
	if len(runs) != 2 || runs[0]["run_id"] != "TEST_RUN_2" {
		t.Fatal(runs)
	}
	m := object(t, f.get("/api/strategy-experiments/TEST_RUN/summary?strategy_id="+c.V2+"&selection=all&page_size=51&page=1"))
	if countRows(t, m) != 51 || string(m["filtered_total"]) != "55" || string(m["has_next"]) != "true" {
		t.Fatal(m)
	}
	m = object(t, f.get("/api/strategy-experiments/TEST_RUN/summary?strategy_id="+c.V2+"&selection=all&page_size=51&page=2"))
	if countRows(t, m) != 4 || string(m["has_next"]) != "false" {
		t.Fatal(m)
	}
	m = object(t, f.get("/api/strategy-experiments/TEST_RUN/items/TEST_A"))
	if !bytes.Contains(m["focus_selection"], []byte("TEST_FOCUS")) || !bytes.Contains(m["outcomes"], []byte("UNKNOWN")) || !bytes.Contains(m["tracking"], []byte("UNKNOWN")) {
		t.Fatal(m)
	}
	m = object(t, f.get("/api/strategy-experiments/TEST_RUN/export?strategy_id="+c.V2+"&selection=unselected"))
	if !bytes.Contains(m["results"], []byte("TEST_B")) || bytes.Contains(m["results"], []byte("TEST_A")) {
		t.Fatal(m)
	}
	w = f.get("/api/strategy-experiments/TEST_RUN/export?format=csv&strategy_id=" + c.V2 + "&selection=focus")
	if w.Code != 200 || !strings.HasPrefix(w.Body.String(), "\ufeff") {
		t.Fatal(w.Code, w.Body.String())
	}
	records, e := csv.NewReader(strings.NewReader(strings.TrimPrefix(w.Body.String(), "\ufeff"))).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	if len(records) != 2 {
		t.Fatal(records)
	}
	fields := map[string]string{}
	for i, key := range records[0] {
		fields[key] = records[1][i]
	}
	if fields["reason"] != "'=TEST_FIXTURE" || fields["ret1_pct"] != "-1.5" || fields["focus_reason_codes"] != "TEST_FOCUS" || fields["selected_focus"] != "True" {
		t.Fatal(fields)
	}
	// The complete old artifact is exactly the same archived bytes as CLI legacy.
	w = f.get("/api/strategy-experiments/TEST_RUN/export")
	var out, log bytes.Buffer
	if e = cli.Run(context.Background(), []string{"legacy", "--db", f.store.Path, "--namespace", "strategy_experiment_runs", "--id", "TEST_RUN"}, &out, &log, "TEST_FIXTURE_BUILD"); e != nil {
		t.Fatal(e)
	}
	if !bytes.Equal(bytes.TrimSpace(out.Bytes()), bytes.TrimSpace(w.Body.Bytes())) {
		t.Fatal("CLI/API archived artifact mismatch")
	}
}
func TestQueryAndBodyErrorsAreExplicit(t *testing.T) {
	f := setup(t)
	seedLegacy(t, f)
	for _, path := range []string{"/api/strategy-experiments?limit=1001", "/api/strategy-experiments?offset=-1", "/api/strategy-experiments?limit=1&limit=2", "/api/strategy-experiments?selection=focus", "/api/strategy-experiments/TEST_RUN/summary?strategy_id=MISSING", "/api/strategy-experiments/TEST_RUN/summary?selection=wrong", "/api/strategy-experiments/TEST_RUN/export?format=xml"} {
		if w := f.get(path); w.Code != 400 {
			t.Fatal(path, w.Code, w.Body.String())
		}
	}
	for _, body := range []string{`{"input_id":"a","input_id":"b"}`, `null`, `{"input_id":"a","observation_date":"2026-02-30"}`} {
		r := httptest.NewRequest("POST", "http://127.0.0.1/api/daily-primary/run", strings.NewReader(body))
		r.Header.Set("Authorization", "Bearer TEST_FIXTURE_TOKEN")
		w := httptest.NewRecorder()
		f.server.ServeHTTP(w, r)
		if w.Code != 400 {
			t.Fatal(w.Code, w.Body.String())
		}
	}
}
func TestNativeSummarySelectionAndSavedExports(t *testing.T) {
	f := setup(t)
	ctx := context.Background()
	raw := []byte(`{"purpose":"TEST_FIXTURE"}`)
	if e := f.repo.PutInput(ctx, c.InputRecord{ID: "TEST_INPUT", Raw: raw, RawHash: c.Hash(raw), Observation: "2026-01-02", Coverage: "TEST_FIXTURE"}); e != nil {
		t.Fatal(e)
	}
	items := []c.Item{}
	for i, status := range []string{"WATCH", "FOCUS", "REJECTED"} {
		id := fmt.Sprintf("TEST_%d", i)
		payload, _ := json.Marshal(map[string]any{"fact": map[string]any{"name": "TEST_FIXTURE", "amount_cny": "500000000.00"}, "decision": map[string]any{"instrument_id": id, "decision_status": status, "rank": i + 1}})
		items = append(items, c.Item{ID: id, Payload: payload})
	}
	if e := f.repo.SaveRun(ctx, c.Run{ID: "TEST_NATIVE", InputID: "TEST_INPUT", Strategy: c.V2, CreatedAt: "2026-01-02", ImplementationStatus: "TEST_FIXTURE", CandidateAvailability: "ABSENT", Manifest: json.RawMessage(`{"purpose":"TEST_FIXTURE"}`), Summary: json.RawMessage(`{"observation_date":"2026-01-02"}`), Items: items}); e != nil {
		t.Fatal(e)
	}
	m := object(t, f.get("/api/strategy-experiments/TEST_NATIVE/summary?strategy_id="+c.V2+"&selection=focus"))
	if countRows(t, m) != 1 || !bytes.Contains(m["items"], []byte("TEST_1")) {
		t.Fatal(m)
	}
	m = object(t, f.get("/api/strategy-experiments/TEST_NATIVE/export?strategy_id="+c.V2+"&selection=unselected"))
	if bytes.Contains(m["results"], []byte("TEST_1")) || !bytes.Contains(m["results"], []byte("TEST_2")) {
		t.Fatal(m)
	}
	w := f.get("/api/strategy-experiments/TEST_NATIVE/export?format=csv&selection=focus")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "TEST_1") || strings.Contains(w.Body.String(), "TEST_0") {
		t.Fatal(w.Code, w.Body.String())
	}
	// Deliberate corruption bypasses immutability only inside TEST_FIXTURE; exports must fail closed.
	if _, e := f.store.DB().Exec("DROP TRIGGER app_summaries_no_delete; DELETE FROM app_item_summaries WHERE instrument_id='TEST_1'; DROP TRIGGER app_items_no_delete; DELETE FROM app_items WHERE instrument_id='TEST_1'"); e != nil {
		t.Fatal(e)
	}
	if w = f.get("/api/strategy-experiments/TEST_NATIVE/export?format=csv&selection=focus"); w.Code == 200 {
		t.Fatal("corrupt published run exported")
	}
}

func TestLegacyDetailIndexCannotReplaceExplicitReference(t *testing.T) {
	f := setup(t)
	makeDetail := func(id, value string) string {
		raw := []byte(`{"instrument_id":"TEST_A","value":"` + value + `"}`)
		var compressed bytes.Buffer
		z := zlib.NewWriter(&compressed)
		_, _ = z.Write(raw)
		if e := z.Close(); e != nil {
			t.Fatal(e)
		}
		f.put(t, "strategy_experiment_item_details", id, map[string]any{"run_id": "TEST_REF_RUN", "instrument_id": "TEST_A", "strategy_id": c.V2, "encoding": "zlib+base64+canonical-json", "raw_bytes": len(raw), "raw_sha256": c.Hash(raw), "payload_b64": base64.StdEncoding.EncodeToString(compressed.Bytes())})
		envelope, _ := json.Marshal(map[string]any{"sha256": c.Hash(raw), "bytes": len(raw)})
		envelope, _ = c.Canonical(envelope)
		return c.Hash(envelope)
	}
	original := makeDetail("TEST_DETAIL_OLD", "SAVED_ORIGINAL")
	makeDetail("TEST_DETAIL_NEW", "DIFFERENT_REVISION")
	f.put(t, "strategy_experiment_runs", "TEST_REF_RUN", map[string]any{"run_id": "TEST_REF_RUN", "results": map[string]any{c.V2: map[string]any{"items": []any{map[string]any{"instrument_id": "TEST_A", "detail_ref": "TEST_DETAIL_OLD", "detail_hash": original}}}}})
	fixtureInsert(t, f.store.DB(), "INSERT INTO legacy_result_items VALUES('TEST_REF_RUN','TEST_A',?,'TEST_DETAIL_OLD')", c.V2)
	m := object(t, f.get("/api/strategy-experiments/TEST_REF_RUN/items/TEST_A"))
	if !bytes.Contains(m["strategy_results"], []byte("SAVED_ORIGINAL")) {
		t.Fatal(m)
	}
	if _, e := f.store.DB().Exec("DROP TRIGGER legacy_result_items_no_UPDATE;UPDATE legacy_result_items SET detail_id='TEST_DETAIL_NEW'"); e != nil {
		t.Fatal(e)
	}
	w := f.get("/api/strategy-experiments/TEST_REF_RUN/items/TEST_A")
	if w.Code != 400 || !strings.Contains(w.Body.String(), "LEGACY_DETAIL_INDEX_REF_MISMATCH") {
		t.Fatal(w.Code, w.Body.String())
	}
}

func TestR1CorePassSelectionIsExplicitAndSaved(t *testing.T) {
	f := setup(t)
	ctx := context.Background()
	raw := []byte(`{"purpose":"TEST_FIXTURE_CORE_PASS"}`)
	if e := f.repo.PutInput(ctx, c.InputRecord{ID: "TEST_R1_INPUT", Raw: raw, RawHash: c.Hash(raw), Observation: "2026-01-02", Coverage: "TEST_FIXTURE"}); e != nil {
		t.Fatal(e)
	}
	items := []c.Item{}
	for i, status := range []string{"CORE_PASS", "FAIL", "CORE_PASS", "UNKNOWN"} {
		id := fmt.Sprintf("TEST_R1_%d", i)
		payload, _ := json.Marshal(map[string]any{"fact": map[string]any{"name": "TEST_FIXTURE", "amount_cny": "500000000.00"}, "decision": map[string]any{"instrument_id": id, "candidate_status": status, "rank": i + 1}})
		items = append(items, c.Item{ID: id, Payload: payload})
	}
	for _, strategy := range []string{c.R1, c.V2} {
		id := "TEST_" + strategy
		if e := f.repo.SaveRun(ctx, c.Run{ID: id, InputID: "TEST_R1_INPUT", Strategy: strategy, CreatedAt: "2026-01-02", ImplementationStatus: "TEST_FIXTURE", CandidateAvailability: "ABSENT", Manifest: json.RawMessage(`{"purpose":"TEST_FIXTURE"}`), Summary: json.RawMessage(`{"observation_date":"2026-01-02"}`), Items: items}); e != nil {
			t.Fatal(e)
		}
	}
	base := "/api/strategy-experiments/TEST_" + c.R1
	for page, want := range map[int]string{1: "TEST_R1_0", 2: "TEST_R1_2"} {
		m := object(t, f.get(base+fmt.Sprintf("/summary?selection=core_pass&page=%d&page_size=1", page)))
		if string(m["selection"]) != `"core_pass"` || string(m["filtered_total"]) != "2" || countRows(t, m) != 1 || !bytes.Contains(m["items"], []byte(want)) {
			t.Fatal(m)
		}
	}
	m := object(t, f.get(base+"/export?selection=core_pass"))
	if !bytes.Contains(m["export_view"], []byte(`"selection":"core_pass"`)) || bytes.Contains(m["results"], []byte("TEST_R1_1")) || bytes.Contains(m["results"], []byte("TEST_R1_3")) || !bytes.Contains(m["results"], []byte("TEST_R1_2")) {
		t.Fatal(m)
	}
	w := f.get(base + "/export?selection=core_pass&format=csv")
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	records, e := csv.NewReader(strings.NewReader(strings.TrimPrefix(w.Body.String(), "\ufeff"))).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	if len(records) != 3 {
		t.Fatal(records)
	}
	statusIndex, focusIndex := -1, -1
	for i, key := range records[0] {
		if key == "candidate_status" {
			statusIndex = i
		}
		if key == "selected_focus" {
			focusIndex = i
		}
	}
	if statusIndex < 0 || focusIndex < 0 {
		t.Fatal(records[0])
	}
	for _, row := range records[1:] {
		if row[statusIndex] != "CORE_PASS" || row[focusIndex] != "False" {
			t.Fatal(row)
		}
	}
	seedLegacy(t, f)
	for _, path := range []string{base + "/summary?selection=focus", base + "/export?selection=unselected", "/api/strategy-experiments/TEST_" + c.V2 + "/summary?selection=core_pass", "/api/strategy-experiments/TEST_" + c.V2 + "/export?selection=core_pass", "/api/strategy-experiments/TEST_RUN/summary?selection=core_pass", "/api/strategy-experiments/TEST_RUN/export?selection=core_pass"} {
		if w = f.get(path); w.Code != 400 {
			t.Fatal(path, w.Code, w.Body.String())
		}
	}
}

func TestObservationDateCheckUsesMetadataOnly(t *testing.T) {
	f := setup(t)
	ctx := context.Background()
	raw := []byte(`{"purpose":"TEST_FIXTURE_METADATA"}`)
	if e := f.repo.PutInput(ctx, c.InputRecord{ID: "TEST_META_INPUT", Raw: raw, RawHash: c.Hash(raw), Observation: "2026-01-02", Coverage: "TEST_FIXTURE"}); e != nil {
		t.Fatal(e)
	}
	// A damaged raw payload must not be read for a cheap metadata/date rejection;
	// actual execution remains responsible for raw bytes and model verification.
	if _, e := f.store.DB().Exec("DROP TRIGGER app_inputs_no_update;UPDATE app_inputs SET payload='TEST_FIXTURE_CORRUPT'"); e != nil {
		t.Fatal(e)
	}
	observation, e := f.repo.InputObservation(ctx, "TEST_META_INPUT")
	if e != nil || observation != "2026-01-02" {
		t.Fatal(observation, e)
	}
	if _, e = f.repo.Input(ctx, "TEST_META_INPUT"); e == nil {
		t.Fatal("fixture raw corruption not detectable")
	}
	request := httptest.NewRequest("POST", "http://127.0.0.1/api/daily-primary/run", strings.NewReader(`{"input_id":"TEST_META_INPUT","observation_date":"2026-01-03"}`))
	request.Header.Set("Authorization", "Bearer TEST_FIXTURE_TOKEN")
	w := httptest.NewRecorder()
	f.server.ServeHTTP(w, request)
	if w.Code != 400 || !strings.Contains(w.Body.String(), "INPUT_OBSERVATION_DATE_MISMATCH") {
		t.Fatal(w.Code, w.Body.String())
	}
	if _, e = f.store.DB().Exec("UPDATE migration_state SET status='MIGRATING'"); e != nil {
		t.Fatal(e)
	}
	if _, e = f.repo.InputObservation(ctx, "TEST_META_INPUT"); e == nil {
		t.Fatal("metadata bypassed publication gate")
	}
}

// fixtureInsert opens only TEST_FIXTURE insertion guards while constructing
// transport-specific synthetic records. Every original DDL is restored in the
// same transaction, before any API is invoked. Migration tests never use this.
func fixtureInsert(t *testing.T, db *sql.DB, q string, args ...any) sql.Result {
	t.Helper()
	tx, e := db.Begin()
	if e != nil {
		t.Fatal(e)
	}
	defer tx.Rollback()
	var ddls []string
	for _, table := range []string{"evidence_objects", "legacy_records", "legacy_result_items"} {
		var ddl string
		name := table + "_verification_insert"
		if e = tx.QueryRow("SELECT sql FROM sqlite_master WHERE name=?", name).Scan(&ddl); e != nil {
			t.Fatal(e)
		}
		ddls = append(ddls, ddl)
		if _, e = tx.Exec("DROP TRIGGER " + name); e != nil {
			t.Fatal(e)
		}
	}
	result, e := tx.Exec(q, args...)
	if e != nil {
		t.Fatal(e)
	}
	for _, ddl := range ddls {
		if _, e = tx.Exec(ddl); e != nil {
			t.Fatal(e)
		}
	}
	if e = tx.Commit(); e != nil {
		t.Fatal(e)
	}
	return result
}
