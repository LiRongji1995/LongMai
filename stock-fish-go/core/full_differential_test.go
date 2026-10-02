package core

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"time"
)

func oracleMap(v any) map[string]any { m, _ := v.(map[string]any); return m }
func oracleList(v any) []any         { a, _ := v.([]any); return a }
func oracleText(v any) string {
	if v == nil {
		return ""
	}
	return fmt.Sprint(v)
}
func oracleNum(v any) Number {
	if v == nil {
		return Number{}
	}
	var n Number
	raw, _ := json.Marshal(v)
	if e := n.UnmarshalJSON(raw); e != nil {
		panic(fmt.Sprint(v) + ":" + e.Error())
	}
	return n
}
func oracleStrings(v any) []string {
	out := []string{}
	for _, x := range oracleList(v) {
		out = append(out, oracleText(x))
	}
	sort.Strings(out)
	return out
}
func conditionParity(t *testing.T, path string, got Condition, want map[string]any) {
	t.Helper()
	if string(got.Status) != oracleText(want["status"]) {
		t.Errorf("%s status: %s != %v", path, got.Status, want["status"])
	}
	r := append([]string{}, got.Reasons...)
	sort.Strings(r)
	if !reflect.DeepEqual(r, oracleStrings(want["reason_codes"])) {
		t.Errorf("%s reasons: %v != %v", path, r, want["reason_codes"])
	}
	for k, c := range oracleMap(want["conditions"]) {
		p, ok := got.Parts[k]
		if !ok {
			t.Errorf("%s missing condition %s", path, k)
			continue
		}
		conditionParity(t, path+"."+k, p, oracleMap(c))
	}
	for k, n := range map[string]Number{"value": got.Value, "threshold": got.Threshold} {
		if v, ok := want[k]; ok && n.String() != oracleNum(v).String() {
			t.Errorf("%s.%s: %s != %v", path, k, n.String(), v)
		}
	}
}
func TestFullPythonSemanticDifferential(t *testing.T) {
	root := os.Getenv("STOCKFISH_R1_ORACLE")
	if root == "" {
		if os.Getenv("STOCKFISH_FORMAL_ACCEPTANCE") == "1" {
			t.Fatal("FORMAL_ACCEPTANCE_BLOCKED_ORACLE_REQUIRED")
		}
		t.Skip("explicit complete TEST_FIXTURE oracle required")
	}
	paths, _ := filepath.Glob(filepath.Join(root, "*.expected.json"))
	if len(paths) < 5 {
		t.Fatal("all oracle cases required")
	}
	for _, path := range paths {
		t.Run(filepath.Base(path), func(t *testing.T) {
			raw, e := os.ReadFile(path)
			if e != nil {
				t.Fatal(e)
			}
			var expected map[string]any
			if e = json.Unmarshal(raw, &expected); e != nil {
				t.Fatal(e)
			}
			if expected["scope"] != "R1_V2_FULL_SEMANTIC_PROJECTION_WITH_RAW_ORACLE" {
				t.Fatal("complete oracle required")
			}
			input := path[:len(path)-len(".expected.json")] + ".json"
			raw, e = os.ReadFile(input)
			if e != nil {
				t.Fatal(e)
			}
			frozen, e := DecodeFrozen(raw)
			if e != nil {
				t.Fatal(e)
			}
			// This fixture exercises both strategies with race instrumentation.
			// Production deadlines are unchanged; no oracle assertion is skipped.
			b, _ := NewBudget(context.Background(), 300*time.Second, nil)
			defer b.Close()
			d, e := AnalyzeFrozen(frozen, Hash(raw), nil, b)
			if e != nil {
				t.Fatal(e)
			}
			coverage := oracleMap(expected["coverage"])
			for key, got := range map[string]string{"candidate_availability": d.Coverage.CandidateAvailability, "universe_coverage": d.Coverage.Universe, "selection_decidability": d.Coverage.Selection, "required_data_coverage": d.Coverage.Required, "background_coverage": d.Coverage.Background, "selection_evaluation_status": d.Coverage.Evaluation} {
				if got != oracleText(coverage[key]) {
					t.Errorf("coverage %s: %s != %v", key, got, coverage[key])
				}
			}
			for id, support := range d.Coverage.Support {
				w := oracleMap(oracleMap(coverage["support"])[id])
				for key, got := range map[string]string{"core_truth": string(support.CoreTruth), "selection_truth": string(support.Truth), "background_coverage": support.Background} {
					if got != oracleText(w[key]) {
						t.Errorf("%s coverage %s: %s != %v", id, key, got, w[key])
					}
				}
			}
			for i, row := range d.Decisions {
				r := oracleMap(oracleMap(oracleMap(expected["expected"])[row.InstrumentID])["full_r1"])
				conditionParity(t, row.InstrumentID+".common", row.Common, oracleMap(r["common"]))
				for s, c := range row.Setups {
					conditionParity(t, row.InstrumentID+"."+s, c, oracleMap(oracleMap(r["setups"])[s]))
				}
				bg := oracleMap(r["background"])
				supply := oracleMap(bg["supply_pressure"])
				conditionParity(t, row.InstrumentID+".supply", row.Background.Supply, supply)
				for _, k := range []string{"long_upper_shadow_count", "unreclaimed_recent_shadow_count", "unresolved_supply_count", "reclaimed_then_failed_count"} {
					if v, ok := supply[k]; ok && fmt.Sprint(row.Background.Supply.Details[k]) != fmt.Sprint(v) {
						t.Errorf("%s supply.%s mismatch", row.InstrumentID, k)
					}
				}
				market := oracleMap(bg["market_status"])
				if row.Background.Market.State != market["ecosystem_state"] {
					t.Errorf("%s market mismatch", row.InstrumentID)
				}
				conditionParity(t, row.InstrumentID+".trend", row.Background.Trend.Condition, oracleMap(bg["trend_strength"]))
				sf := d.Facts[i]
				p := oracleMap(oracleMap(oracleMap(expected["v2_expected"])[row.InstrumentID])["market_status"])
				limit := oracleMap(p["instrument_limit"])
				if sf.CurrentLimit.CloseUp != limit["close_limit_up"] || sf.CurrentLimit.CloseDown != limit["close_limit_down"] || sf.CurrentLimit.TouchedUp != limit["touched_limit_up"] || sf.CurrentLimit.Broken != limit["touched_not_closed"] {
					t.Errorf("%s limit full mismatch", row.InstrumentID)
				}
				sectors := oracleList(bg["sector_candidates"])
				if len(sectors) != len(row.Background.Sectors) {
					t.Errorf("%s sector count mismatch", row.InstrumentID)
				} else {
					for j, s := range row.Background.Sectors {
						want := oracleMap(sectors[j])
						for k, got := range map[string]string{"theme_id": s.ThemeID, "status": string(s.Status), "sector_level_state": s.Level, "sector_absolute_level": s.Absolute, "sector_relative_level": s.Relative, "sector_momentum_state": s.Momentum, "breadth_state": s.Breadth, "stock_role": s.Role} {
							if got != oracleText(want[k]) {
								t.Errorf("%s sector %s: %s != %v", row.InstrumentID, k, got, want[k])
							}
						}
						for setup, c := range s.Compatibility {
							w := oracleMap(oracleMap(want["setup_compatibility"])[setup])
							if c.Details["context_status"] != w["context_status"] {
								t.Errorf("%s sector %s compatibility mismatch", row.InstrumentID, setup)
							}
						}
						for n, w := range s.Windows {
							p := oracleMap(oracleMap(want["windows"])[fmt.Sprint(n)])
							for k, v := range map[string]Number{"peer_equal_weight_return_pct": w.Value, "market_equal_weight_return_pct": w.MarketReturn, "sector_market_rs_pct": w.MarketRS, "peer_median_return_pct": w.PeerMedian, "peer_amount_change_median_pct": w.AmountMedian, "stock_return_pct": w.StockReturn, "rs_sector_pct": w.RS} {
								if v.Mul(N("100")).Rounded(4).String() != oracleNum(p[k]).String() {
									t.Errorf("%s sector window %d %s: %s != %v", row.InstrumentID, n, k, v.Mul(N("100")).Rounded(4).String(), p[k])
								}
							}
						}
					}
				}
				funds := oracleList(r["independent_theme_funds"])
				if len(funds) != len(row.Background.IndependentFunds) {
					t.Errorf("%s fund count mismatch", row.InstrumentID)
				} else {
					for j, f := range row.Background.IndependentFunds {
						w := oracleMap(funds[j])
						c := oracleMap(w["funds_condition"])
						conditionParity(t, row.InstrumentID+".funds."+f.ThemeID, f.Condition, c)
						if f.Condition.Details["grade"] != c["grade"] {
							t.Errorf("%s fund grade mismatch", row.InstrumentID)
						}
					}
				}
			}
			v, e := AnalyzeStrategyFrozen(frozen, Hash(raw), nil, b, V2StrategyID, nil)
			if e != nil {
				t.Fatal(e)
			}
			vc := oracleMap(expected["v2_coverage"])
			if len(vc) == 0 {
				t.Fatal("V2 coverage oracle required")
			}
			for key, got := range map[string]string{"candidate_availability": v.Coverage.CandidateAvailability, "universe_coverage": v.Coverage.Universe, "selection_decidability": v.Coverage.Selection, "required_data_coverage": v.Coverage.Required, "background_coverage": v.Coverage.Background, "selection_evaluation_status": v.Coverage.Evaluation} {
				if got != oracleText(vc[key]) {
					t.Errorf("V2 coverage %s: %s != %v", key, got, vc[key])
				}
			}
			for id, s := range v.Coverage.Support {
				w := oracleMap(oracleMap(vc["support"])[id])
				for key, got := range map[string]string{"core_truth": string(s.CoreTruth), "selection_truth": string(s.Truth), "background_coverage": s.Background} {
					if got != oracleText(w[key]) {
						t.Errorf("%s V2 coverage %s: %s != %v", id, key, got, w[key])
					}
				}
			}
			if len(v.Decisions) != 0 {
				t.Fatal("V2 ran R1")
			}
			for _, row := range v.V2 {
				w := oracleMap(oracleMap(expected["v2_expected"])[row.InstrumentID])
				if row.Status != w["decision_status"] || row.Primary != oracleText(w["primary_stage"]) {
					t.Errorf("%s V2 decision %s/%s != %v/%v", row.InstrumentID, row.Status, row.Primary, w["decision_status"], w["primary_stage"])
				}
				for setup, c := range row.Setups {
					conditionParity(t, row.InstrumentID+".v2."+setup, c, oracleMap(oracleMap(w["stage_candidates"])[setup]))
				}
				for k, c := range row.Conditions {
					conditionParity(t, row.InstrumentID+".v2.conditions."+k, c, oracleMap(oracleMap(w["conditions"])[k]))
				}
				if fmt.Sprint(row.Rank) != fmt.Sprint(w["rank"]) {
					t.Errorf("%s rank %d != %v", row.InstrumentID, row.Rank, w["rank"])
				}
			}
		})
	}
}
