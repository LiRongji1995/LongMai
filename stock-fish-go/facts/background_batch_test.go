package facts

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"testing"
)

func batchTestRow(t *testing.T, values map[string]any) Row {
	t.Helper()
	raw, err := json.Marshal(values)
	if err != nil {
		t.Fatal(err)
	}
	var row Row
	if err := json.Unmarshal(raw, &row); err != nil {
		t.Fatal(err)
	}
	return row
}

func batchTestEvidence(t *testing.T, values map[string]any) Row {
	t.Helper()
	row := batchTestRow(t, map[string]any{"event_time": "2026-01-07T15:00:00+08:00", "source_published_at": "2026-01-07T15:00:00+08:00", "first_seen_at": "2026-01-07T16:00:00+08:00", "ingested_at": "2026-01-07T16:00:00+08:00", "session": "2026-01-07", "source_refs": []string{"TEST_FIXTURE_PARENT"}})
	for key, value := range batchTestRow(t, values) {
		row[key] = value
	}
	return row
}

func batchTestFund(t *testing.T, id, seat string) Row {
	return batchTestEvidence(t, map[string]any{"instrument_id": id, "theme_id": "TEST_FIXTURE_THEME", "source_layer": "PUBLIC_EXCHANGE_LHB", "seat_id": seat, "buy_amount_cny": "60000000", "sell_amount_cny": "10000000", "net_amount_cny": "50000000"})
}

func batchFixture(t *testing.T, funds []Row, partial bool) *BackgroundBatch {
	t.Helper()
	status := "COMPLETE"
	if partial {
		status = "PARTIAL"
	}
	sessions := []string{"2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"}
	p := batchTestEvidence(t, map[string]any{"receipt_id": "TEST_FIXTURE_PARENT", "status": status, "pagination_complete": !partial, "instrument_ids": []string{"A", "B", "C"}, "business_sessions": sessions})
	r, err := evidence.NewResolver([]Row{p}, "2026-01-07T20:00:00+08:00", "TEST_FIXTURE", "2026-01-07", sessions)
	if err != nil {
		t.Fatal(err)
	}
	themes := []Row{batchTestRow(t, map[string]any{"theme_id": "TEST_FIXTURE_THEME", "name": "Fixture", "theme_type": "EVENT", "member_ids": []string{"C", "A", "B", "A"}, "source_refs": []string{"TEST_FIXTURE_PARENT"}})}
	b, err := NewBackgroundBatch(context.Background(), themes, funds, map[string]bool{"A": true, "B": true, "C": true}, r, sessions)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func batchFundResult(t *testing.T, batch *BackgroundBatch, id string) FundsContext {
	t.Helper()
	themes := batch.ThemesForInstrument(id)
	if len(themes) != 1 {
		t.Fatalf("duplicate/missing membership: %d", len(themes))
	}
	out, err := batch.FundForTheme(context.Background(), id, themes[0])
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestBackgroundBatchFundsExcludeSelfAndPreserveExactMoney(t *testing.T) {
	rows := []Row{batchTestFund(t, "A", "OWN"), batchTestFund(t, "B", "B1"), batchTestFund(t, "C", "C1")}
	rows[0]["buy_amount_cny"] = json.RawMessage(`"99999999999999999.99999"`)
	b := batchFixture(t, rows, false)
	out := batchFundResult(t, b, "A")
	if out.Condition.Status != Pass || out.Metrics["grade"] != "STRONG" || out.Metrics["classified_events"] != 2 {
		t.Fatal(out)
	}
	if n := out.Metrics["dedup_buy_amount_cny"].(Number); n.Cmp(N("120000000")) != 0 {
		t.Fatal(n)
	}
	if n := out.Metrics["dedup_net_amount_cny"].(Number); n.Cmp(N("100000000")) != 0 {
		t.Fatal(n)
	}
	if len(out.Metrics["evidence"].([]Row)) != 2 {
		t.Fatal(out)
	}
	// A distinct stock must see A's exact decimal amount, while excluding itself.
	other := batchFundResult(t, b, "B")
	if n := other.Metrics["dedup_buy_amount_cny"].(Number); n.Cmp(N("100000000059999999.99999")) != 0 {
		t.Fatal(n)
	}
}

func TestBackgroundBatchFundsUnknownEmptyPartialAndWindowRules(t *testing.T) {
	marker := func() Row {
		return batchTestEvidence(t, map[string]any{"theme_id": "TEST_FIXTURE_THEME", "record_type": "COMPLETE_NO_RECORD", "expected_scope": []string{"A", "B", "C"}})
	}
	for _, tc := range []struct {
		name    string
		rows    func() []Row
		partial bool
		status  State
		reason  string
	}{
		{"no rows", func() []Row { return nil }, false, Unknown, "SAM_FUNDS_DATA_UNAVAILABLE"},
		{"own only", func() []Row { return []Row{batchTestFund(t, "A", "SELF")} }, false, Unknown, "SAM_FUNDS_NOT_CAPTURED_OR_COMPLETENESS_UNPROVEN"},
		{"complete empty", func() []Row { return []Row{marker()} }, false, Fail, "SAM_FUNDS_COMPLETE_NO_RECORD"},
		{"partial cannot prove empty", func() []Row { return []Row{marker()} }, true, Unknown, "SAM_FUNDS_EVIDENCE_REJECTED_OR_UNVERIFIED"},
		{"scope includes self", func() []Row { m := marker(); m["expected_scope"] = json.RawMessage(`["B","C"]`); return []Row{m} }, false, Unknown, "SAM_FUNDS_EVIDENCE_REJECTED_OR_UNVERIFIED"},
		{"proxy", func() []Row {
			r := batchTestFund(t, "B", "SEAT")
			r["source_layer"] = json.RawMessage(`"CAPITAL_PROXY"`)
			return []Row{r}
		}, false, Unknown, "SAM_FUNDS_PROXY_ONLY_PENDING_VERIFICATION"},
		{"dedupe conflict scale", func() []Row {
			a := batchTestFund(t, "B", "SEAT")
			b := batchTestFund(t, "B", "SEAT")
			b["buy_amount_cny"] = json.RawMessage(`"60000000.0"`)
			return []Row{a, b}
		}, false, Unknown, "SAM_FUNDS_DISCLOSURE_KEY_CONFLICT"},
		{"same key identical", func() []Row { r := batchTestFund(t, "B", "SEAT"); return []Row{r, r} }, false, Pass, ""},
		{"overlap", func() []Row {
			a := batchTestFund(t, "B", "SEAT")
			b := batchTestFund(t, "B", "SEAT")
			b["window_start"] = json.RawMessage(`"2026-01-06"`)
			return []Row{a, b}
		}, false, Unknown, "SAM_FUNDS_OVERLAPPING_CLASSIFIED_WINDOWS"},
		{"partial supports value", func() []Row { return []Row{batchTestFund(t, "B", "SEAT")} }, true, Pass, ""},
		{"future invalid row prevents empty", func() []Row {
			r := batchTestFund(t, "B", "SEAT")
			r["window_end"] = json.RawMessage(`"2026-01-08"`)
			return []Row{marker(), r}
		}, false, Unknown, "SAM_FUNDS_EVIDENCE_REJECTED_OR_UNVERIFIED"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			out := batchFundResult(t, batchFixture(t, tc.rows(), tc.partial), "A")
			if out.Condition.Status != tc.status || (tc.reason != "" && !contains(out.Condition.Reasons, tc.reason)) {
				t.Fatalf("want %s/%s got %#v", tc.status, tc.reason, out)
			}
		})
	}
}

func TestBackgroundBatchCancellationAndUnrelatedFunds(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := NewBackgroundBatch(ctx, nil, nil, nil, nil, nil); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	b := batchFixture(t, []Row{batchTestRow(t, map[string]any{"theme_id": "UNRELATED"})}, false)
	if len(b.fundsByTheme) != 0 {
		t.Fatal("unrelated evidence included")
	}
	out := batchFundResult(t, b, "A")
	if !contains(out.Condition.Reasons, "SAM_FUNDS_NOT_CAPTURED_OR_COMPLETENESS_UNPROVEN") {
		t.Fatal(out)
	}
	if _, err := b.FundForTheme(ctx, "A", b.themes[0]); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}
