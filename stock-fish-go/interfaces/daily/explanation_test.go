package daily

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
)

func TestChineseExplanationRetainsSavedValuesUnknownsAndEvidence(t *testing.T) {
	item := c.Item{ID: "TEST_FIXTURE_SECURITY", Payload: json.RawMessage(`{"fact":{"name":"TEST_FIXTURE","facts":{"Returns":{"5":"1/3"},"sessions":["2026-09-29","2026-09-30"]},"selected_evidence_hashes":["TEST_FIXTURE_EVIDENCE"]},"decision":{"candidate_status":"CORE_PASS","matched_setups":["START"],"common":{"status":"PASS","conditions":{"liquidity":{"status":"PASS","value":"500000001","threshold":"500000000","evidence_refs":["TEST_FIXTURE_AMOUNT"]}}},"setups":{"START":{"status":"PASS"},"ACCELERATION":{"status":"FAIL","reason_codes":["TEST_FIXTURE_PERSISTENCE_FAILED"]},"HIGH_LEVEL_REPAIR":{"status":"UNKNOWN","reason_codes":["TEST_FIXTURE_PATH_UNAVAILABLE"]},"TREND_PULLBACK":{"status":"NOT_EVALUATED"}},"background":{"sector_context":{"status":"UNKNOWN","reason_codes":["TEST_FIXTURE_THEME_UNKNOWN"]}}}}`)}
	var output bytes.Buffer
	if err := renderExplanation(&output, item, c.R1); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"至少一个完整形态通过", "启动（START）：通过", "加速（ACCELERATION）：失败", "高位修复（HIGH_LEVEL_REPAIR）：未知", "实际值=500000001", "比较参考=500000000", "TEST_FIXTURE_PATH_UNAVAILABLE", "TEST_FIXTURE_AMOUNT", "TEST_FIXTURE_EVIDENCE", "1/3", "2026-09-29", "背景标签与核心形态分开"} {
		if !strings.Contains(output.String(), want) {
			t.Fatalf("missing %q in %s", want, output.String())
		}
	}
	if strings.Contains(output.String(), "上涨概率") {
		t.Fatal("unvalidated probability introduced")
	}
}

func TestSavedDiffReadsCompleteSetsAndDoesNotClaimUniqueCause(t *testing.T) {
	before := c.Run{ID: "TEST_FIXTURE_BEFORE", Strategy: c.R1, Manifest: json.RawMessage(`{"observation_date":"2026-09-30","cutoff":"2026-10-02T13:00:00Z","input_bytes_hash":"old","config_hashes":{"a":"old"},"business_implementation_id":"business"}`), Items: []c.Item{{ID: "000001.SZ", Payload: json.RawMessage(`{"fact":{"name":"旧名称","facts":{"Close":"10","RS":{"5":"1/10"}},"selected_evidence_hashes":["OLD"]},"decision":{"candidate_status":"CORE_UNKNOWN","matched_setups":[]}}`)}}}
	after := c.Run{ID: "TEST_FIXTURE_AFTER", Strategy: c.R1, Manifest: json.RawMessage(`{"observation_date":"2026-09-30","cutoff":"2026-10-02T14:00:00Z","input_bytes_hash":"new","config_hashes":{"a":"new"},"business_implementation_id":"business"}`), Items: []c.Item{{ID: "000001.SZ", Payload: json.RawMessage(`{"fact":{"name":"新名称","facts":{"Close":"11","RS":{"5":"1/5"}},"selected_evidence_hashes":["NEW"]},"decision":{"candidate_status":"CORE_PASS","matched_setups":["START","TREND_PULLBACK"]}}`)}}}
	diff, err := compareSaved(context.Background(), before, after)
	if err != nil || len(diff.Changes) != 1 || diff.Attribution != "MULTIPLE_CHANGES_NO_UNIQUE_CAUSAL_ATTRIBUTION" {
		t.Fatal(diff, err)
	}
	change := diff.Changes[0]
	if change.PriceFactsChanged == nil || !*change.PriceFactsChanged || change.IdentityOrBenchmarkChanged == nil || !*change.IdentityOrBenchmarkChanged || change.EvidenceChanged == nil || !*change.EvidenceChanged || change.BeforeStatus != "CORE_UNKNOWN" || change.AfterStatus != "CORE_PASS" {
		t.Fatal(change)
	}
	if diff.Unchanged != 0 || diff.OrderChanged {
		t.Fatal(diff)
	}
	unchanged, err := compareSaved(context.Background(), before, before)
	if err != nil || unchanged.Attribution != "NO_SAVED_CHANGE" || unchanged.Unchanged != 1 {
		t.Fatal(unchanged, err)
	}
	after.Items = append(after.Items, after.Items[0])
	if _, err := compareSaved(context.Background(), before, after); err == nil {
		t.Fatal("duplicate evaluated security silently compared")
	}
}

func TestDiffCLIRestartReadOnlyAndExports(t *testing.T) {
	config, store, _ := dailyFixture(t)
	repo := sqlitev2.NewAppRepository(store)
	original, err := repo.Run(context.Background(), "TEST_FIXTURE_RUN")
	if err != nil {
		t.Fatal(err)
	}
	original.Items = nil
	for offset := 0; ; offset += 200 {
		items, readErr := repo.Items(context.Background(), original.ID, 200, offset)
		if readErr != nil {
			t.Fatal(readErr)
		}
		original.Items = append(original.Items, items...)
		if len(items) < 200 {
			break
		}
	}
	comparison := original
	comparison.ID = "TEST_FIXTURE_COMPARISON"
	comparison.Items = append([]c.Item{}, original.Items...)
	comparison.Items[0] = c.Item{ID: original.Items[0].ID, Payload: json.RawMessage(`{"fact":{"name":"TEST_FIXTURE"},"decision":{"candidate_status":"CORE_FAIL","matched_setups":[]}}`)}
	if err := repo.SaveRun(context.Background(), comparison); err != nil {
		t.Fatal(err)
	}
	var before, after int
	if err := store.DB().QueryRow("SELECT count(*) FROM app_change_ledger").Scan(&before); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 10, 4, 8, 0, 0, 0, time.UTC)
	for _, format := range []string{"json", "table", "csv", "markdown"} {
		output, err := callFixture(t, config, now, "diff", "--from-run-id", original.ID, "--to-run-id", comparison.ID, "--format", format)
		if err != nil || !bytes.Contains(output, []byte(original.Items[0].ID)) {
			t.Fatal(format, err, string(output))
		}
		if format == "json" {
			var diff SavedDiff
			if err := json.Unmarshal(output, &diff); err != nil || diff.BeforeTotal != 250 || diff.AfterTotal != 250 || diff.Unchanged != 249 || len(diff.Changes) != 1 {
				t.Fatal(diff, err)
			}
		}
	}
	if err := store.DB().QueryRow("SELECT count(*) FROM app_change_ledger").Scan(&after); err != nil || before != after {
		t.Fatal("diff wrote state", before, after, err)
	}
	if _, err := callFixture(t, config, now, "diff", "--help"); err != nil {
		t.Fatal("help failed", err)
	}
	if _, err := callFixture(t, config, now, "diff", "--from-run-id", original.ID); err == nil {
		t.Fatal("missing comparison accepted")
	}
}
