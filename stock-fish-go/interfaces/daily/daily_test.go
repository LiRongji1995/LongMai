package daily

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/buildidentity"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/lifecycle"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

var fixtureHashes = map[string]string{"TEST_FIXTURE_CONFIG": "same"}

func dailyFixture(t *testing.T) (string, *sqlitev2.Store, string) {
	t.Helper()
	root := filepath.Join(t.TempDir(), "中文 空格")
	if e := os.Mkdir(root, 0700); e != nil {
		t.Fatal(e)
	}
	db := filepath.Join(root, "TEST_FIXTURE.db")
	ctx := context.Background()
	s, e := sqlitev2.Open(ctx, db, true)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { s.Close() })
	if _, e = s.DB().Exec("INSERT INTO migration_state VALUES(1,'TEST_FIXTURE','PUBLISHED','{}','TEST_FIXTURE')"); e != nil {
		t.Fatal(e)
	}
	repo := sqlitev2.NewAppRepository(s)
	raw := []byte(`{"coverage_type":"TEST_FIXTURE"}`)
	in := c.InputRecord{ID: "TEST_FIXTURE_INPUT", Raw: raw, RawHash: c.Hash(raw), Observation: "2026-09-30", Cutoff: "2026-10-02T20:10:00+08:00", Coverage: "TEST_FIXTURE"}
	if e = repo.PutInput(ctx, in); e != nil {
		t.Fatal(e)
	}
	manifest, _ := json.Marshal(map[string]any{"input_bytes_hash": in.RawHash, "observation_date": in.Observation, "cutoff": in.Cutoff, "config_hashes": fixtureHashes, "go_source_hash": c.Hash([]byte("TEST_FIXTURE_RECORDED_SOURCE")), "business_implementation_id": buildidentity.Current(), "daily_evidence_policy": Policy, "conversion_version": evidence.InputContractVersion})
	items := []c.Item{}
	for i := 0; i < 250; i++ {
		iid := fmt.Sprintf("%06d.SH", i)
		raw, _ := json.Marshal(map[string]any{"fact": map[string]any{"name": "=危险", "coverage": "TEST_FIXTURE"}, "decision": map[string]any{"candidate_status": "CORE_PASS", "rank": i + 1, "matched_setups": []string{"START", "TREND_PULLBACK"}, "conditions": map[string]any{"status": "UNKNOWN", "value": nil, "threshold": 1}}})
		items = append(items, c.Item{ID: iid, Payload: raw})
	}
	run := c.Run{ID: "TEST_FIXTURE_RUN", InputID: in.ID, Strategy: c.R1, CreatedAt: "2026-10-02T12:11:00Z", ImplementationStatus: "TEST_FIXTURE_ONLY", CandidateAvailability: "PARTIAL", Manifest: manifest, Summary: json.RawMessage(`{"coverage":"TEST_FIXTURE","required_data_completeness":"INCOMPLETE","counts":{"CORE_PASS":250}}`), Items: items}
	if e = repo.SaveRun(ctx, run); e != nil {
		t.Fatal(e)
	}
	config := filepath.Join(root, "config.json")
	var out bytes.Buffer
	if e = runAt(ctx, []string{"init", "--config", config, "--db", "TEST_FIXTURE.db"}, &out, &out, "TEST_FIXTURE_BUILD", fixtureHashes, time.Date(2026, 10, 4, 8, 0, 0, 0, time.UTC)); e != nil {
		t.Fatal(e)
	}
	return config, s, db
}
func callFixture(t *testing.T, config string, now time.Time, args ...string) ([]byte, error) {
	t.Helper()
	var out, log bytes.Buffer
	args = append(args, "--config", config)
	e := runAt(context.Background(), args, &out, &log, c.Hash([]byte("TEST_FIXTURE_BUILD")), fixtureHashes, now)
	return out.Bytes(), e
}
func TestDailyReadOnlyMatchingPaginationExportsAndNoWriterLease(t *testing.T) {
	config, s, db := dailyFixture(t)
	now := time.Date(2026, 10, 4, 8, 0, 0, 0, time.UTC)
	_, release, e := lifecycle.AcquireDatabaseWriter(db, "TEST_FIXTURE_ACTIVE_RESEARCH", false)
	if e != nil {
		t.Fatal(e)
	}
	defer release()
	var before int
	if e = s.DB().QueryRow("SELECT count(*) FROM app_change_ledger").Scan(&before); e != nil {
		t.Fatal(e)
	}
	queryArgs := []string{}
	if !buildidentity.Recorded(buildidentity.Current()) {
		if _, err := callFixture(t, config, now, "picks", "--strategy", "r1"); err == nil {
			t.Fatal("unrecorded business implementation automatically reused")
		}
		queryArgs = []string{"--run-id", "TEST_FIXTURE_RUN"}
	}
	for i := 0; i < 2; i++ {
		raw, e := callFixture(t, config, now, append([]string{"picks", "--strategy", "r1", "--format", "json"}, queryArgs...)...)
		if e != nil {
			t.Fatal(e)
		}
		var v Saved
		if e = json.Unmarshal(raw, &v); e != nil || v.Total != 250 || v.Shown != 250 || len(v.Run.Items) != 250 || v.History != (len(queryArgs) > 0) {
			t.Fatalf("%v %+v", e, v)
		}
	}
	raw, e := callFixture(t, config, now, "picks", "--run-id", "TEST_FIXTURE_RUN", "--format", "json", "--limit", "7")
	if e != nil {
		t.Fatal(e)
	}
	var v Saved
	_ = json.Unmarshal(raw, &v)
	if !v.History || !v.Truncated || v.Shown != 7 || v.Total != 250 {
		t.Fatal(v)
	}
	path := filepath.Join(filepath.Dir(config), "全部.csv")
	if _, e = callFixture(t, config, now, append([]string{"picks", "--format", "csv", "--limit", "7", "--output", path}, queryArgs...)...); e != nil {
		t.Fatal(e)
	}
	csv, _ := os.ReadFile(path)
	if bytes.Count(csv, []byte("\n")) != 251 || !bytes.Contains(csv, []byte("'000000.SH")) || !bytes.Contains(csv, []byte("'=危险")) {
		t.Fatal("CSV truncated or unsafe")
	}
	if _, e = callFixture(t, config, now, append([]string{"picks", "--output", path}, queryArgs...)...); e == nil {
		t.Fatal("overwrote export")
	}
	if _, e = callFixture(t, config, now, "picks", "--strategy", "v2"); e == nil {
		t.Fatal("fell back to R1")
	}
	raw, e = callFixture(t, config, now, append([]string{"explain", "000001.SH", "--format", "json"}, queryArgs...)...)
	if e != nil || !bytes.Contains(raw, []byte("threshold")) {
		t.Fatal(e)
	}
	var after, jobs int
	_ = s.DB().QueryRow("SELECT count(*) FROM app_change_ledger").Scan(&after)
	_ = s.DB().QueryRow("SELECT count(*) FROM app_jobs").Scan(&jobs)
	if before != after || jobs != 0 {
		t.Fatal("query wrote business state")
	}
}
func TestInitNoOverwriteDatesDryRunAndMissingWorker(t *testing.T) {
	config, s, _ := dailyFixture(t)
	now := time.Date(2026, 10, 4, 8, 0, 0, 0, time.UTC)
	if _, e := callFixture(t, config, now, "init"); e == nil {
		t.Fatal("init overwrite")
	}
	if _, e := callFixture(t, filepath.Join(t.TempDir(), "missing.json"), now, "init"); e == nil || !strings.Contains(e.Error(), "NONINTERACTIVE") {
		t.Fatal(e)
	}
	for _, tc := range []struct {
		now  string
		want string
	}{{"2026-10-08T10:00:00+08:00", "REQUIRED_OBSERVATION_NOT_COMPLETE"}, {"2027-01-01T08:00:00+08:00", "OFFICIAL_CALENDAR_COVERAGE_UNAVAILABLE"}, {"2026-10-09T16:00:00+08:00", "LOCAL_FROZEN_INPUT_UNAVAILABLE"}} {
		now, _ := time.Parse(time.RFC3339, tc.now)
		raw, e := callFixture(t, config, now, "daily", "--dry-run", "--format", "json")
		if !strings.Contains(string(raw), tc.want) && (e == nil || !strings.Contains(e.Error(), tc.want)) {
			t.Fatal(tc, e, string(raw))
		}
	}
	raw, e := callFixture(t, config, now, "daily", "--dry-run", "--refresh", "--format", "json")
	if e != nil || !bytes.Contains(raw, []byte("BLOCKED_PROVIDER")) {
		t.Fatal(e, string(raw))
	}
	if _, e = callFixture(t, config, now, "daily", "--refresh"); e == nil {
		t.Fatal("fake refresh success")
	}
	var jobs int
	_ = s.DB().QueryRow("SELECT count(*) FROM app_jobs").Scan(&jobs)
	if jobs != 0 {
		t.Fatal("dry run created job")
	}
	t.Setenv("STOCKFISH_CONFIG", config)
	path, e := configPath("")
	if e != nil || path != config {
		t.Fatal(path, e)
	}
	explicit := filepath.Join(t.TempDir(), "explicit.json")
	path, e = configPath(explicit)
	if e != nil || path != explicit {
		t.Fatal(path, e)
	}
}
