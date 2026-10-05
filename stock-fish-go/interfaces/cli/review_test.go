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
	for i := 0; i < 160; i++ {
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
			if id == "600001.SH" && day > "2026-02-20" && day <= "2026-03-01" {
				price = "14"
			}
			if id == "600001.SH" && day > "2026-03-10" {
				price = "15"
			}
			b := clock(day)
			for k, v := range map[string]any{"instrument_id": id, "session": day, "open": price, "high": price, "low": price, "close": price, "comparable_close": price, "comparable_basis": "TEST_FIXTURE_SAME_BASIS", "source_refs": []string{"TEST_FIXTURE_REVIEW"}} {
				b[k] = v
			}
			bars = append(bars, b)
		}
	}
	receipt["row_count"] = len(bars)
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
	// Seal an explicitly synthetic, empty authority baseline through the actual
	// batch publication API before enabling app writers. A PUBLISHED state row
	// alone cannot satisfy the read-only authority identity gate.
	fixtureSource := c.Hash([]byte("TEST_FIXTURE_REVIEW_EMPTY_AUTHORITY"))
	if _, err = store.DB().Exec("INSERT INTO migration_state VALUES(1,?,'VERIFIED',?,?)", fixtureSource, `{"purpose":"TEST_FIXTURE_REVIEW_EMPTY_AUTHORITY; NOT_REAL_MIGRATION_VERIFICATION"}`, time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
		t.Fatal(err)
	}
	publication, err := store.BackfillBatchProjections(ctx, sqlitev2.BatchProjectionOptions{})
	if err != nil || publication.Status != "PUBLISHED" || !evidence.ValidHash(publication.ManifestHash) {
		t.Fatal("fixture batch publication failed", publication, err)
	}
	verified, err := store.VerifyBatchProjections(ctx)
	if err != nil || verified.Status != "VERIFIED" || verified.ManifestHash != publication.ManifestHash {
		t.Fatal("fixture published batch closure failed", verified, err)
	}
	if err = store.BatchProjectionsReady(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err = store.DB().Exec("UPDATE migration_state SET status='PUBLISHED' WHERE id=1 AND status='VERIFIED'"); err != nil {
		t.Fatal(err)
	}
	if err = store.Ready(ctx); err != nil {
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
	saveRuns := func(days []string, labelInput c.InputRecord) {
		for _, day := range days {
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
				args = []string{"review", "evaluate", "--db", path, "--run", runID, "--label-input", labelInput.ID, "--contract", contract.Version}
				call(false, args...)
				if duplicate := call(false, args...); !strings.Contains(duplicate, `"outcome_records_added":0`) {
					t.Fatal(duplicate)
				}
			}
		}
	}
	saveRuns([]string{"2026-01-02", "2026-01-20"}, label)
	baseConfigRaw, _ := json.Marshal(baseConfig)
	candidateConfigRaw, _ := json.Marshal(candidateConfig)
	base := review.Candidate{ID: "TEST_FIXTURE_BASELINE", Role: "BASELINE", TimeSplitContract: review.CandidateTimeSplitVersion, Hypothesis: "frozen fixture baseline", Change: "TEST_FIXTURE independent baseline identity", Scope: "TEST_FIXTURE", Implementation: baseBuild, ConfigHash: c.Hash(baseConfigRaw), DataCutoff: "2025-12-02T16:00:00+08:00", SelectionEnd: "2025-12-01", EvaluationStart: "2026-01-02", EvaluationEnd: "2026-02-02", CalendarInputID: label.ID, CalendarHash: label.RawHash, ContractVersion: contract.Version, ContractHash: review.Digest(contract), PrimaryMetric: "PRIMARY_WINDOW_EXCESS_MEDIAN", SecondaryMetrics: []string{"ALL_FIXED_WINDOWS", "NEGATIVE_TAIL", "FIXED_COST_SENSITIVITY"}, FixtureOnly: true, ProductionPromotion: "DISABLED"}
	base.StabilityContract = review.StabilityContractVersion
	base.StabilityStart = "2026-02-15"
	base.StabilityEnd = "2026-04-01"
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
		args := []string{"research-lab", "propose", "--db", path, "--file", writeJSON(v.ID+".json", v)}
		first := call(false, args...)
		if replay := call(false, args...); replay != first {
			t.Fatal("proposal replay changed registration identity", replay, first)
		}
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
	var jobsBefore int
	if err = store.DB().QueryRow("SELECT count(*) FROM app_jobs").Scan(&jobsBefore); err != nil {
		t.Fatal(err)
	}
	if result := call(true, "research-lab", "transition", "--db", path, "--candidate", one.ID, "--to", "SHADOW_ELIGIBLE", "--attempt", "TEST_FIXTURE_ATTEMPT_ONE", "--reason", "TEST_FIXTURE no independent data yet"); !strings.Contains(result, "INDEPENDENT_STABILITY_REQUIRED") {
		t.Fatal("primary evidence alone entered shadow", result)
	}
	holdoutLabel := reviewFrozen(t, "2026-04-20")
	if err = repo.PutInput(ctx, holdoutLabel); err != nil {
		t.Fatal(err)
	}
	saveRuns([]string{"2026-02-20", "2026-03-10"}, holdoutLabel)
	futureArgs := []string{"research-lab", "stability", "--db", path, "--candidate", one.ID, "--baseline", base.ID, "--key", "TEST_FIXTURE_STABILITY_FUTURE", "--as-of", "2026-02-01T16:00:00+08:00"}
	if result := call(false, futureArgs...); !strings.Contains(result, `"admission_status":"INSUFFICIENT_EVIDENCE"`) {
		t.Fatal("future holdout data was consumed", result)
	}
	stableArgs := []string{"research-lab", "stability", "--db", path, "--candidate", one.ID, "--baseline", base.ID, "--key", "TEST_FIXTURE_STABILITY_COMPLETE", "--as-of", "2026-04-21T16:00:00+08:00"}
	stable := call(false, stableArgs...)
	if !strings.Contains(stable, `"admission_status":"REVIEW_REQUIRED"`) || !strings.Contains(stable, `"evaluation_phase":"INDEPENDENT_STABILITY"`) {
		t.Fatal(stable)
	}
	if replay := call(false, stableArgs...); replay != stable {
		t.Fatal("stability attempt replay changed", replay, stable)
	}
	for _, to := range []string{"SHADOW_ELIGIBLE", "SHADOW_OBSERVING"} {
		if result := call(false, "research-lab", "transition", "--db", path, "--candidate", one.ID, "--to", to, "--attempt", "TEST_FIXTURE_STABILITY_COMPLETE", "--reason", "TEST_FIXTURE fixed holdout research admission"); !strings.Contains(result, "NO_JOB_LIST_OR_PRODUCTION_CHANGE") {
			t.Fatal("shadow implied an active job or strategy change", result)
		}
	}
	var jobsAfter int
	if err = store.DB().QueryRow("SELECT count(*) FROM app_jobs").Scan(&jobsAfter); err != nil || jobsBefore != jobsAfter {
		t.Fatal("shadow state started a research job", jobsBefore, jobsAfter, err)
	}
	if result := call(true, "research-lab", "compare", "--db", path, "--candidate", one.ID, "--baseline", base.ID, "--key", "TEST_FIXTURE_PRIMARY_REWIND_FAILURE", "--as-of", "2026-02-01T16:00:00+08:00"); !strings.Contains(result, "CANNOT_REWIND") {
		t.Fatal(result)
	}
	if result := call(false, "research-lab", "propose", "--db", path, "--file", filepath.Join(root, one.ID+".json")); !strings.Contains(result, `"state":"INSUFFICIENT_EVIDENCE"`) {
		t.Fatal("new primary failure retained shadow admission", result)
	}
	call(true, "research-lab", "transition", "--db", path, "--candidate", one.ID, "--to", "SHADOW_OBSERVING", "--attempt", "TEST_FIXTURE_STABILITY_COMPLETE", "--reason", "TEST_FIXTURE stale old best")
	call(false, "research-lab", "transition", "--db", path, "--candidate", one.ID, "--to", "REJECTED", "--reason", "TEST_FIXTURE retain rejected research history")
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
	revised := reviewFrozen(t, "2026-04-21")
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
	if revisions != 64 {
		t.Fatal("label revision history lost", revisions)
	}
	if result := call(false, "research-lab", "list", "--db", path, "--kind", "EVALUATION"); !strings.Contains(result, "TEST_FIXTURE_ATTEMPT_ONE") || !strings.Contains(result, "TEST_FIXTURE_ATTEMPT_TWO") {
		t.Fatal("restart lost attempts", result)
	}
	if result := call(false, "research-lab", "list", "--db", path, "--kind", "EVALUATION"); !strings.Contains(result, "TEST_FIXTURE_STABILITY_FUTURE") || !strings.Contains(result, "TEST_FIXTURE_STABILITY_COMPLETE") || !strings.Contains(result, "TEST_FIXTURE_PRIMARY_REWIND_FAILURE") {
		t.Fatal("stability/failed attempts lost after restart", result)
	}
	if state := review.CandidateState(persisted, one.ID); state != "REJECTED" {
		t.Fatal("rejected shadow research history not retained", state)
	}
	// Optional persistent fixture output is for the parent budget runner's binary
	// package checks. It contains only explicit TEST_FIXTURE data.
	if os.Getenv("STOCKFISH_REVIEW_FIXTURE_DIR") != "" {
		writeJSON("TEST_FIXTURE_identity.json", map[string]any{"db": path, "run_ids": runIDs, "contract": contract.Version, "baseline": base.ID, "candidate": one.ID, "label_input": revised.ID, "build_source_sha256": build})
	}
}
