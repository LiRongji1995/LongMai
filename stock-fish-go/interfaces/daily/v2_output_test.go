package daily

import (
	"bytes"
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

func v2DisplayFixture() c.Item {
	return c.Item{ID: "TEST_FIXTURE_V2_SECURITY", Payload: json.RawMessage(`{"fact":{"name":"TEST_FIXTURE_V2"},"decision":{"decision_status":"WATCH","primary_stage":"START","alternate_stages":["HIGH_LEVEL_REPAIR"],"stage_candidates":{"START":{"status":"PASS"},"HIGH_LEVEL_REPAIR":{"status":"PASS"},"ACCELERATION":{"status":"UNKNOWN","reason_codes":["TEST_FIXTURE_MISSING_ACTIVITY"]},"TREND_PULLBACK":{"status":"PASS"}},"conditions":{"market_compatibility":{"status":"UNKNOWN","reason_codes":["TEST_FIXTURE_CONTEXT_UNKNOWN"],"details":{"exact_decimal":0.070000000000000001,"large_integer":9007199254740993}}}}}`)}
}

func TestV2SavedSelectionDoesNotInventMatchedCandidates(t *testing.T) {
	for _, tc := range []struct {
		name, decision, want string
		warning              bool
	}{
		{"recorded selection", `{"decision_status":"WATCH","primary_stage":"START","alternate_stages":["HIGH_LEVEL_REPAIR"],"stage_candidates":{"START":{"status":"PASS"},"HIGH_LEVEL_REPAIR":{"status":"PASS"},"TREND_PULLBACK":{"status":"PASS"},"ACCELERATION":{"status":"UNKNOWN"}}}`, `["START","HIGH_LEVEL_REPAIR"]`, false},
		{"unknown primary", `{"decision_status":"UNKNOWN","primary_stage":"HIGH_LEVEL_REPAIR","alternate_stages":["START"],"stage_candidates":{"START":{"status":"PASS"},"HIGH_LEVEL_REPAIR":{"status":"UNKNOWN"}}}`, `["START"]`, true},
		{"failed alternate", `{"decision_status":"WATCH","primary_stage":"START","alternate_stages":["ACCELERATION"],"stage_candidates":{"START":{"status":"PASS"},"ACCELERATION":{"status":"FAIL"}}}`, `["START"]`, true},
		{"missing selected status", `{"decision_status":"UNKNOWN","primary_stage":"START","stage_candidates":{"HIGH_LEVEL_REPAIR":{"status":"PASS"}}}`, `null`, true},
		{"unknown stage name", `{"decision_status":"UNKNOWN","primary_stage":"TEST_FIXTURE_OTHER_STAGE","stage_candidates":{"TEST_FIXTURE_OTHER_STAGE":{"status":"PASS"}}}`, `null`, true},
		{"missing metadata", `{"decision_status":"UNKNOWN"}`, `null`, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			item := c.Item{ID: "TEST_FIXTURE", Payload: json.RawMessage(`{"decision":` + tc.decision + `}`)}
			view := setupSelection(item)
			if !view.V2 || view.MatchedJSON != tc.want || (len(view.Warnings) > 0) != tc.warning {
				t.Fatalf("wrong saved selection: %+v want=%s warning=%t", view, tc.want, tc.warning)
			}
		})
	}
	item := c.Item{ID: "TEST_FIXTURE_R1", Payload: json.RawMessage(`{"decision":{"candidate_status":"CORE_PASS","matched_setups":[ "TREND_PULLBACK", "START" ]}}`)}
	view := setupSelection(item)
	if view.V2 || view.MatchedJSON != `[ "TREND_PULLBACK", "START" ]` || len(view.Matched) != 2 || view.Matched[0] != "TREND_PULLBACK" {
		t.Fatal("R1 recorded order/text changed", view)
	}
}

func csvColumns(t *testing.T, raw []byte) map[string]string {
	t.Helper()
	rows, err := csv.NewReader(bytes.NewReader(raw)).ReadAll()
	if err != nil || len(rows) != 2 {
		t.Fatal("invalid two-row fixture export", err, string(raw))
	}
	columns := map[string]string{}
	for i, key := range rows[0] {
		columns[key] = rows[1][i]
	}
	return columns
}

func TestV2UnconfirmedSelectionIsUnknownRatherThanSavedEmpty(t *testing.T) {
	item := c.Item{ID: "TEST_FIXTURE_UNKNOWN_STAGE", Payload: json.RawMessage(`{"decision":{"decision_status":"UNKNOWN","primary_stage":"HIGH_LEVEL_REPAIR","alternate_stages":[],"stage_candidates":{"HIGH_LEVEL_REPAIR":{"status":"UNKNOWN","reason_codes":["TEST_FIXTURE_PATH_MISSING"]}}}}`)}
	var out bytes.Buffer
	if err := renderExplanation(&out, item, c.V2); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "已确认命中形态（保存选择存在缺口/不一致）：未记录/未知") || strings.Contains(out.String(), "全部命中形态：HIGH_LEVEL_REPAIR") || strings.Contains(out.String(), "无（保存的选择没有已确认命中）") {
		t.Fatal("unknown stage presented as matched or a known empty selection", out.String())
	}
}

func TestV2ExplainFormatsPreserveSelectionUnknownsAndExactDetails(t *testing.T) {
	item := v2DisplayFixture()
	value := Saved{Run: c.Run{ID: "TEST_FIXTURE_V2_RUN", Strategy: c.V2, Manifest: json.RawMessage(`{}`), Summary: json.RawMessage(`{}`), Items: []c.Item{item}}, Total: 1, Shown: 1}
	for _, format := range []string{"table", "markdown", "csv"} {
		t.Run(format, func(t *testing.T) {
			var out bytes.Buffer
			if err := renderSaved(&out, value, format, true); err != nil {
				t.Fatal(err)
			}
			if format == "csv" {
				columns := csvColumns(t, out.Bytes())
				if columns["all_matched_setups"] != `["START","HIGH_LEVEL_REPAIR"]` || columns["saved_primary_stage"] != "START" || columns["saved_alternate_stages"] != `["HIGH_LEVEL_REPAIR"]` || !strings.Contains(columns["saved_stage_candidate_states"], `"ACCELERATION":"UNKNOWN"`) {
					t.Fatal(columns)
				}
				if columns["saved_detail_json"] != string(item.Payload) {
					t.Fatal("raw detail export changed")
				}
				return
			}
			for _, want := range []string{"全部命中形态：START, HIGH_LEVEL_REPAIR", "V2 保存主形态=START", "保存其他形态=[\"HIGH_LEVEL_REPAIR\"]", "加速（ACCELERATION）：未知", "TEST_FIXTURE_MISSING_ACTIVITY", "0.070000000000000001", "9007199254740993", "其他 PASS 不追加为最终命中"} {
				if !strings.Contains(out.String(), want) {
					t.Fatalf("missing %q in %s", want, out.String())
				}
			}
			if strings.Contains(out.String(), "全部命中形态：START, HIGH_LEVEL_REPAIR, TREND_PULLBACK") {
				t.Fatal("unselected candidate added to matched stages")
			}
		})
	}
}

type savedDiffFixtureBackend struct {
	planFixtureBackend
	runs map[string]c.Run
}

func (b savedDiffFixtureBackend) Saved(_ context.Context, id, instrument string, all bool) (c.Run, error) {
	if instrument != "" || !all {
		return c.Run{}, errors.New("TEST_FIXTURE_DIFF_REQUIRES_COMPLETE_SET")
	}
	run, exists := b.runs[id]
	if !exists {
		return run, errors.New("TEST_FIXTURE_RUN_NOT_FOUND")
	}
	return run, nil
}

func TestV2DiffExportsSelectionAndKeepsUnknownAttribution(t *testing.T) {
	before := c.Run{ID: "TEST_FIXTURE_V2_BEFORE", Strategy: c.V2, Manifest: json.RawMessage(`{"observation_date":"2026-09-30","input_bytes_hash":"TEST_FIXTURE_SAME"}`), Items: []c.Item{v2DisplayFixture()}}
	after := before
	after.ID = "TEST_FIXTURE_V2_AFTER"
	after.Items = []c.Item{{ID: before.Items[0].ID, Payload: json.RawMessage(`{"fact":{"name":"TEST_FIXTURE_V2"},"decision":{"decision_status":"WATCH","primary_stage":"HIGH_LEVEL_REPAIR","alternate_stages":["START","ACCELERATION"],"stage_candidates":{"START":{"status":"PASS"},"HIGH_LEVEL_REPAIR":{"status":"PASS"},"ACCELERATION":{"status":"UNKNOWN"},"TREND_PULLBACK":{"status":"PASS"}}}}`)}}
	backend := savedDiffFixtureBackend{runs: map[string]c.Run{before.ID: before, after.ID: after}}
	for _, format := range []string{"json", "table", "markdown", "csv"} {
		t.Run(format, func(t *testing.T) {
			var out bytes.Buffer
			if err := diffOutput(context.Background(), backend, before.ID, after.ID, format, "", &out); err != nil {
				t.Fatal(err)
			}
			if format == "csv" {
				columns := csvColumns(t, out.Bytes())
				if columns["before_setups"] != `["START","HIGH_LEVEL_REPAIR"]` || columns["after_setups"] != `["HIGH_LEVEL_REPAIR","START"]` || columns["after_saved_primary_stage"] != "HIGH_LEVEL_REPAIR" || !strings.Contains(columns["after_saved_stage_candidate_states"], `"ACCELERATION":"UNKNOWN"`) || !strings.Contains(columns["after_setup_display_warnings"], "ACCELERATION:UNKNOWN") {
					t.Fatal(columns)
				}
				if columns["saved_price_facts_changed"] != "UNKNOWN_MISSING_SAVED_FIELDS" {
					t.Fatal("missing price data reported as false", columns)
				}
				return
			}
			if format == "json" {
				var diff SavedDiff
				if err := json.Unmarshal(out.Bytes(), &diff); err != nil {
					t.Fatal(err)
				}
				if len(diff.Changes) != 1 || diff.Changes[0].AfterSetups != `["HIGH_LEVEL_REPAIR","START"]` || diff.Changes[0].PriceFactsChanged != nil || diff.Attribution != "NO_SINGLE_CAUSE_PROVEN_FROM_SAVED_RESULTS" || diff.EvidenceInspection != "SAVED_RESULT_FIELDS_ONLY_RAW_INPUT_CATEGORIES_NOT_READ" {
					t.Fatal(diff)
				}
				return
			}
			for _, want := range []string{"保存主形态：START → HIGH_LEVEL_REPAIR", "ACCELERATION:UNKNOWN", "UNKNOWN_MISSING_SAVED_FIELDS", "NO_SINGLE_CAUSE_PROVEN_FROM_SAVED_RESULTS"} {
				if !strings.Contains(out.String(), want) {
					t.Fatalf("missing %q in %s", want, out.String())
				}
			}
		})
	}
}
