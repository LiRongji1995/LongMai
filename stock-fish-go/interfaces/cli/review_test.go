package cli_test

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	review "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application/review"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/lifecycle"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/native"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/cli"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func reviewFrozen(t *testing.T, last string) c.InputRecord {
	t.Helper()
	clock := func(day string) map[string]any {
		return map[string]any{"event_time": day + "T15:00:00+08:00", "source_published_at": day + "T15:00:00+08:00", "first_seen_at": day + "T16:00:00+08:00", "ingested_at": day + "T16:00:00+08:00"}
	}
	receipt := clock("2025-12-01")
	for k, v := range map[string]any{"receipt_id": "TEST_FIXTURE_REVIEW", "provider": "TEST_FIXTURE", "dataset": "TEST_FIXTURE", "field_version": "TEST_FIXTURE_V1", "content_hash": c.Hash([]byte("TEST_FIXTURE_RAW")), "row_count": 200, "pagination_complete": true, "status": "COMPLETE"} {
		receipt[k] = v
	}
	securities := []any{}
	for _, id := range []string{"600001.SH", "600002.SH"} {
		s := clock("2025-12-01")
		for k, v := range map[string]any{"instrument_id": id, "exchange": "SSE", "board": "MAIN", "security_type": "A_COMMON", "listed_on": "2020-01-01", "effective_from": "2020-01-01", "st": false, "suspended": false, "source_refs": []string{"TEST_FIXTURE_REVIEW"}} {
			s[k] = v
		}
		securities = append(securities, s)
	}
	u := clock("2025-12-01")
	for k, v := range map[string]any{"snapshot_id": "TEST_FIXTURE_REVIEW", "securities": securities, "source_refs": []string{"TEST_FIXTURE_REVIEW"}, "complete": true, "expected_count": 2} {
		u[k] = v
	}
	sessions := []string{}
	bars := []any{}
	first := time.Date(2025, 12, 1, 0, 0, 0, 0, time.UTC)
	for i := 0; i < 75; i++ {
		day := first.AddDate(0, 0, i).Format("2006-01-02")
		sessions = append(sessions, day)
		if day > last {
			continue
		}
		for _, id := range []string{"600001.SH", "600002.SH"} {
			price := "10"
			if id == "600001.SH" && day > "2026-01-02" && day <= "2026-01-12" {
				price = "11"
			}
			if id == "600001.SH" && day > "2026-01-20" {
				price = "12"
			}
			b := clock(day)
			for k, v := range map[string]any{"instrument_id": id, "session": day, "open": price, "high": price, "low": price, "close": price, "comparable_close": price, "comparable_basis": "TEST_FIXTURE_SAME_BASIS", "source_refs": []string{"TEST_FIXTURE_REVIEW"}} {
				b[k] = v
			}
			bars = append(bars, b)
		}
	}
	raw, _ := json.Marshal(map[string]any{"purpose": "TEST_FIXTURE_REVIEW_E2E", "cutoff": last + "T16:00:00+08:00", "input": map[string]any{"observation_date": last, "sessions": sessions, "universe": u, "bars": bars, "receipts": []any{receipt}, "coverage_type": "TEST_FIXTURE"}})
	in, err := (&native.NativeEngine{}).Validate(raw)
	if err != nil {
		t.Fatal(err)
	}
	return in
}

func TestCLIReviewAndResearchPersistedFixtureEndToEnd(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	if explicit := os.Getenv("STOCKFISH_REVIEW_FIXTURE_DIR"); explicit != "" {
		if !strings.Contains(filepath.Base(explicit), "TEST_FIXTURE") {
			t.Fatal("explicit fixture directory must include TEST_FIXTURE")
		}
		root = explicit
		if err := os.MkdirAll(root, 0700); err != nil {
			t.Fatal(err)
		}
	}
	path := filepath.Join(root, "TEST_FIXTURE_复盘 空格.sqlite3")
	store, err := sqlitev2.Open(ctx, path, true)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if _, err = store.DB().Exec("INSERT INTO migration_state VALUES(1,'TEST_FIXTURE','PUBLISHED','{}','TEST_FIXTURE')"); err != nil {
		t.Fatal(err)
	}
	repo := sqlitev2.NewAppRepository(store)
	build := c.Hash([]byte("TEST_FIXTURE_REVIEW_BUILD"))
	call := func(expectError bool, args ...string) string {
		t.Helper()
		var out, log bytes.Buffer
		err := cli.Run(ctx, args, &out, &log, build)
		if (err != nil) != expectError {
			t.Fatalf("args=%v error=%v log=%s out=%s", args, err, log.String(), out.String())
		}
		if expectError {
			return fmt.Sprint(err)
		}
		return out.String()
	}
	for _, command := range []string{"review", "research-lab"} {
		if help := call(false, command, "--help"); !strings.Contains(help, "stockfish") {
			t.Fatal(help)
		}
	}
	if result := call(true, "review", "list", "--db", path); !strings.Contains(result, "NOT_INITIALIZED") {
		t.Fatal(result)
	}
	call(false, "review", "init", "--db", path)
	call(false, "review", "init", "--db", path)
	contract := review.DefaultContract()
	contract.Version = "TEST_FIXTURE_REVIEW_V1"
	contract.FixtureOnly = true
	contract.MinSamples = 2
	contract.MinDates = 2
	contract.MinBlocks = 2
	writeJSON := func(name string, v any) string {
		t.Helper()
		raw, _ := json.Marshal(v)
		p := filepath.Join(root, name)
		if err := os.WriteFile(p, raw, 0600); err != nil {
			t.Fatal(err)
		}
		return p
	}
	call(false, "review", "contract", "--db", path, "--file", writeJSON("TEST_FIXTURE_contract.json", contract))
	label := reviewFrozen(t, "2026-01-31")
	if err = repo.PutInput(ctx, label); err != nil {
		t.Fatal(err)
	}
	baseBuild, candidateBuild := c.Hash([]byte("TEST_FIXTURE_BASE_BUILD")), c.Hash([]byte("TEST_FIXTURE_CANDIDATE_BUILD"))
	baseConfig, candidateConfig := map[string]string{"TEST_FIXTURE": c.Hash([]byte("TEST_FIXTURE_BASE_CONFIG"))}, map[string]string{"TEST_FIXTURE": c.Hash([]byte("TEST_FIXTURE_CANDIDATE_CONFIG"))}
	var runIDs []string
	for _, day := range []string{"2026-01-02", "2026-01-20"} {
		in := reviewFrozen(t, day)
		if err = repo.PutInput(ctx, in); err != nil {
			t.Fatal(err)
		}
		for _, role := range []string{"BASE", "CANDIDATE"} {
			runID := "TEST_FIXTURE_" + role + "_" + day
			source, config := baseBuild, baseConfig
			if role == "CANDIDATE" {
				source, config = candidateBuild, candidateConfig
			}
			manifest, _ := json.Marshal(map[string]any{"input_bytes_hash": in.RawHash, "cutoff": in.Cutoff, "go_source_hash": source, "config_hashes": config, "conversion_version": evidence.InputContractVersion})
			summary, _ := json.Marshal(map[string]any{"qualification": c.Qualification{Eligible: []string{"600001.SH", "600002.SH"}, Excluded: map[string]string{}, Unknown: map[string]string{}, UniverseComplete: true}})
			items := []c.Item{}
			for _, id := range []string{"600001.SH", "600002.SH"} {
				status := "CORE_FAIL"
				if (role == "BASE" && id == "600002.SH") || (role == "CANDIDATE" && id == "600001.SH") {
					status = "CORE_PASS"
				}
				matched := []string{}
				if status == "CORE_PASS" {
					matched = []string{"START"}
				}
				payload, _ := json.Marshal(map[string]any{"decision": map[string]any{"candidate_status": status, "matched_setups": matched}, "fact": map[string]any{"instrument_id": id}})
				items = append(items, c.Item{ID: id, Payload: payload})
			}
			run := c.Run{ID: runID, InputID: in.ID, Strategy: c.R1, CreatedAt: day + "T17:00:00+08:00", Manifest: manifest, Summary: summary, Items: items, ImplementationStatus: "TEST_FIXTURE_ONLY", CandidateAvailability: "PRESENT"}
			if err = repo.SaveRun(ctx, run); err != nil {
				t.Fatal(err)
			}
			runIDs = append(runIDs, runID)
			args := []string{"review", "register", "--db", path, "--run", runID, "--contract", contract.Version}
			call(false, args...)
			if duplicate := call(false, args...); !strings.Contains(duplicate, `"created":false`) {
				t.Fatal(duplicate)
			}
			args = []string{"review", "evaluate", "--db", path, "--run", runID, "--label-input", label.ID, "--contract", contract.Version}
			call(false, args...)
			if duplicate := call(false, args...); !strings.Contains(duplicate, `"outcome_records_added":0`) {
				t.Fatal(duplicate)
			}
		}
	}
	baseConfigRaw, _ := json.Marshal(baseConfig)
	candidateConfigRaw, _ := json.Marshal(candidateConfig)
	base := review.Candidate{ID: "TEST_FIXTURE_BASELINE", Role: "BASELINE", Hypothesis: "frozen fixture baseline", Change: "TEST_FIXTURE independent baseline identity", Scope: "TEST_FIXTURE", Implementation: baseBuild, ConfigHash: c.Hash(baseConfigRaw), DataCutoff: "2025-12-02T16:00:00+08:00", SelectionEnd: "2025-12-01", EvaluationStart: "2026-01-02", EvaluationEnd: "2026-02-02", CalendarInputID: label.ID, CalendarHash: label.RawHash, ContractVersion: contract.Version, ContractHash: review.Digest(contract), PrimaryMetric: "PRIMARY_WINDOW_EXCESS_MEDIAN", SecondaryMetrics: []string{"ALL_FIXED_WINDOWS", "NEGATIVE_TAIL", "FIXED_COST_SENSITIVITY"}, FixtureOnly: true, ProductionPromotion: "DISABLED"}
	one := base
	one.ID = "TEST_FIXTURE_CANDIDATE_ONE"
	one.Role = "CANDIDATE"
	one.Parent = base.ID
	one.Implementation = candidateBuild
	one.ConfigHash = c.Hash(candidateConfigRaw)
	one.Hypothesis = "independent fixture output selects stronger sample"
	two := one
	two.ID = "TEST_FIXTURE_CANDIDATE_TWO"
	two.ConfigHash = c.Hash([]byte("TEST_FIXTURE_NO_OUTPUTS_CONFIG"))
	two.Hypothesis = "incomplete fixture candidate must remain blocked"
	for _, v := range []review.Candidate{base, one, two} {
		call(false, "research-lab", "propose", "--db", path, "--file", writeJSON(v.ID+".json", v))
	}
	third := two
	third.ID = "TEST_FIXTURE_CANDIDATE_THREE"
	if result := call(true, "research-lab", "propose", "--db", path, "--file", writeJSON(third.ID+".json", third)); !strings.Contains(result, "LIMIT_TWO") {
		t.Fatal(result)
	}
	args := []string{"research-lab", "compare", "--db", path, "--candidate", one.ID, "--baseline", base.ID, "--key", "TEST_FIXTURE_ATTEMPT_ONE", "--as-of", "2026-02-01T16:00:00+08:00"}
	firstAttempt := call(false, args...)
	if !strings.Contains(firstAttempt, `"admission_status":"REVIEW_REQUIRED"`) {
		t.Fatal(firstAttempt)
	}
	if duplicate := call(false, args...); duplicate != firstAttempt {
		t.Fatal("attempt idempotency changed")
	}
	if result := call(true, "research-lab", "transition", "--db", path, "--candidate", one.ID, "--to", "PRODUCTION", "--reason", "fixture forbidden promotion"); !strings.Contains(result, "PROMOTION_DISABLED") {
		t.Fatal(result)
	}
	call(false, "research-lab", "transition", "--db", path, "--candidate", one.ID, "--to", "OFFLINE_EVALUATED", "--attempt", "TEST_FIXTURE_ATTEMPT_ONE", "--reason", "fixture full comparison")
	call(false, "research-lab", "transition", "--db", path, "--candidate", one.ID, "--to", "REVIEW_REQUIRED", "--attempt", "TEST_FIXTURE_ATTEMPT_ONE", "--reason", "independent stability evidence still required")
	if result := call(false, "research-lab", "compare", "--db", path, "--candidate", two.ID, "--baseline", base.ID, "--key", "TEST_FIXTURE_ATTEMPT_TWO", "--as-of", "2026-02-01T16:00:00+08:00"); !strings.Contains(result, `"admission_status":"INSUFFICIENT_EVIDENCE"`) {
		t.Fatal(result)
	}
	call(false, "research-lab", "transition", "--db", path, "--candidate", two.ID, "--to", "INSUFFICIENT_EVIDENCE", "--reason", "fixture sample missing")
	before, err := store.ReviewRecords(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, release, err := lifecycle.AcquireDatabaseWriter(path, "TEST_FIXTURE_BUSY", false)
	if err != nil {
		t.Fatal(err)
	}
	call(false, "review", "summary", "--db", path, "--contract", contract.Version, "--as-of", "2026-02-01T16:00:00+08:00")
	if result := call(true, "review", "register", "--db", path, "--run", runIDs[0]); !strings.Contains(result, "WRITER_OWNERSHIP") {
		t.Fatal(result)
	}
	if err = release(); err != nil {
		t.Fatal(err)
	}
	for _, format := range []string{"json", "csv", "markdown", "table"} {
		call(false, "review", "export", "--db", path, "--kind", "OUTCOME", "--format", format, "--output", filepath.Join(root, "TEST_FIXTURE_export."+format))
	}
	call(false, "research-lab", "diagnose", "--db", path, "--contract", contract.Version)
	after, err := store.ReviewRecords(ctx)
	if err != nil || review.Digest(before) != review.Digest(after) {
		t.Fatal("read query changed journal", err)
	}
	revised := reviewFrozen(t, "2026-02-01")
	if err = repo.PutInput(ctx, revised); err != nil {
		t.Fatal(err)
	}
	for _, id := range runIDs {
		call(false, "review", "evaluate", "--db", path, "--run", id, "--label-input", revised.ID, "--contract", contract.Version)
	}
	store.Close()
	reopened, err := sqlitev2.Open(ctx, path, false)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	persisted, err := reopened.ReviewRecords(ctx)
	if err != nil {
		t.Fatal(err)
	}
	revisions := 0
	for _, r := range persisted {
		if r.Kind == "OUTCOME" {
			var o review.Outcome
			_ = json.Unmarshal(r.Payload, &o)
			if o.Supersedes != "" {
				revisions++
			}
		}
	}
	if revisions != 32 {
		t.Fatal("label revision history lost", revisions)
	}
	if result := call(false, "research-lab", "list", "--db", path, "--kind", "EVALUATION"); !strings.Contains(result, "TEST_FIXTURE_ATTEMPT_ONE") || !strings.Contains(result, "TEST_FIXTURE_ATTEMPT_TWO") {
		t.Fatal("restart lost attempts", result)
	}
	// Optional persistent fixture output is for the parent budget runner's binary
	// package checks. It contains only explicit TEST_FIXTURE data.
	if os.Getenv("STOCKFISH_REVIEW_FIXTURE_DIR") != "" {
		writeJSON("TEST_FIXTURE_identity.json", map[string]any{"db": path, "run_ids": runIDs, "contract": contract.Version, "baseline": base.ID, "candidate": one.ID, "label_input": revised.ID, "build_source_sha256": build})
	}
}
