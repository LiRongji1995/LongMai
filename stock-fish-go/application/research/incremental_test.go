package research

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
)

type fixtureBudget struct{}

func (fixtureBudget) Ctx() context.Context { return context.Background() }
func (fixtureBudget) Check() error         { return nil }
func (fixtureBudget) Read(int64) error     { return nil }
func (fixtureBudget) Output(int64) error   { return nil }
func (fixtureBudget) Expanded(int64) error { return nil }
func (fixtureBudget) IndexRow() error      { return nil }
func (fixtureBudget) Stage(string)         {}
func (fixtureBudget) Processed(int)        {}
func (fixtureBudget) Snapshot() c.Metrics  { return c.Metrics{} }

type fixtureFactCache map[string][]byte

func (m fixtureFactCache) GetFact(_ context.Context, key string) (StockFacts, bool, error) {
	var f StockFacts
	raw, exists := m[key]
	if !exists {
		return f, false, nil
	}
	err := json.Unmarshal(raw, &f)
	return f, true, err
}
func (m fixtureFactCache) PutFact(_ context.Context, key string, f StockFacts) error {
	raw, err := json.Marshal(f)
	m[key] = raw
	return err
}

func fixtureRow(v map[string]any) Row {
	raw, _ := json.Marshal(v)
	var row Row
	_ = json.Unmarshal(raw, &row)
	return row
}
func fixtureSet(row Row, field string, value any) { row[field], _ = json.Marshal(value) }
func cloneFrozen(f Frozen) Frozen {
	raw, _ := json.Marshal(f)
	var copy Frozen
	_ = json.Unmarshal(raw, &copy)
	return copy
}

// Emit a legal serialized fixture from Go's optional zero values. This changes
// no evidence scalar/default and lets tests use the real strict decode boundary.
func fixtureFrozenBytes(t *testing.T, f Frozen) []byte {
	t.Helper()
	raw, err := json.Marshal(f)
	if err != nil {
		t.Fatal(err)
	}
	var envelope, input Row
	_ = json.Unmarshal(raw, &envelope)
	_ = json.Unmarshal(envelope["input"], &input)
	for _, key := range []string{"themes", "receipts", "previous_emotions", "funds"} {
		if isNull(input[key]) {
			delete(input, key)
		}
	}
	if f.Input.HistoryRef == "" {
		input["history_artifact_ref"] = json.RawMessage("null")
	}
	if f.Input.HistoryHash == "" {
		input["history_artifact_hash"] = json.RawMessage("null")
	}
	envelope["input"], err = json.Marshal(input)
	if err != nil {
		t.Fatal(err)
	}
	raw, err = json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func advanceFixture(f *Frozen) {
	day, _ := time.Parse("2006-01-02", f.Input.Observation)
	for {
		day = day.AddDate(0, 0, 1)
		if day.Weekday() != time.Saturday && day.Weekday() != time.Sunday {
			break
		}
	}
	next := day.Format("2006-01-02")
	f.Input.Sessions = append(f.Input.Sessions, next)
	for _, r := range f.Input.Receipts {
		fixtureSet(r, "business_sessions", f.Input.Sessions)
	}
	for _, ix := range []int{319, 639} {
		copy := Row{}
		for k, v := range f.Input.Bars[ix] {
			copy[k] = v
		}
		fixtureSet(copy, "session", next)
		for _, k := range []string{"event_time", "source_published_at", "first_seen_at", "ingested_at"} {
			fixtureSet(copy, k, next+"T15:00:00+08:00")
		}
		f.Input.Bars = append(f.Input.Bars, copy)
	}
	var states []Row
	_ = json.Unmarshal(f.Input.Universe["securities"], &states)
	for _, r := range append(states, f.Input.Universe) {
		for _, k := range []string{"event_time", "source_published_at", "first_seen_at", "ingested_at"} {
			fixtureSet(r, k, next+"T15:00:00+08:00")
		}
	}
	fixtureSet(f.Input.Universe, "securities", states)
	f.Input.Observation, f.Cutoff = next, next+"T20:00:00+08:00"
}

func incrementalFixture() Frozen {
	sessions := []string{}
	d := time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC)
	for len(sessions) < 320 {
		if d.Weekday() != time.Saturday && d.Weekday() != time.Sunday {
			sessions = append(sessions, d.Format("2006-01-02"))
		}
		d = d.AddDate(0, 0, 1)
	}
	observation := sessions[len(sessions)-1]
	clock := func(day string, ref string) map[string]any {
		stamp := day + "T15:00:00+08:00"
		return map[string]any{"event_time": stamp, "source_published_at": stamp, "first_seen_at": stamp, "ingested_at": stamp, "source_refs": []string{ref}}
	}
	in := Input{Coverage: "TEST_FIXTURE", Observation: observation, Sessions: sessions}
	for _, ref := range []string{"TEST_FIXTURE_MASTER", "TEST_FIXTURE_PRICE_A", "TEST_FIXTURE_PRICE_B"} {
		r := clock(sessions[0], "")
		delete(r, "source_refs")
		for k, v := range map[string]any{"receipt_id": ref, "provider": "TEST_FIXTURE", "dataset": "TEST_FIXTURE", "field_version": "TEST_FIXTURE_V1", "content_hash": strings.Repeat("a", 64), "row_count": 640, "pagination_complete": true, "status": "COMPLETE", "business_sessions": sessions} {
			r[k] = v
		}
		in.Receipts = append(in.Receipts, fixtureRow(r))
	}
	states := []Row{}
	for ix, id := range []string{"TEST_FIXTURE_A.SH", "TEST_FIXTURE_B.SH"} {
		r := clock(observation, "TEST_FIXTURE_MASTER")
		for k, v := range map[string]any{"instrument_id": id, "name": id, "exchange": "SSE", "board": "MAIN", "security_type": "A_COMMON", "listed_on": "2020-01-01", "effective_from": "2020-01-01", "st": false, "suspended": false, "delisting_period": false} {
			r[k] = v
		}
		states = append(states, fixtureRow(r))
		ref := []string{"TEST_FIXTURE_PRICE_A", "TEST_FIXTURE_PRICE_B"}[ix]
		for j, day := range sessions {
			r := clock(day, ref)
			price := fmt.Sprintf("%d.%02d", 10+j/100+ix, (j % 100))
			for k, v := range map[string]any{"instrument_id": id, "session": day, "open": price, "high": price, "low": price, "close": price, "comparable_close": price, "comparable_basis": "TEST_FIXTURE_HFQ_V1", "amount_cny": "700000000", "turnover_fraction": json.Number("0.08"), "reference_prev_close": price, "limit_applicable": false, "quality": "VALID"} {
				r[k] = v
			}
			in.Bars = append(in.Bars, fixtureRow(r))
		}
	}
	u := clock(observation, "TEST_FIXTURE_MASTER")
	u["snapshot_id"], u["securities"], u["expected_count"], u["complete"] = "TEST_FIXTURE", states, 2, true
	in.Universe = fixtureRow(u)
	return Frozen{Purpose: "TEST_FIXTURE", Input: in, Cutoff: observation + "T20:00:00+08:00"}
}

func fixtureAnalyze(t *testing.T, f Frozen, cache FactsCache, algorithm, strategy string) Diagnostic {
	t.Helper()
	raw := fixtureFrozenBytes(t, f)
	decoded, err := DecodeFrozen(raw)
	if err != nil {
		t.Fatal(err)
	}
	out, err := analyzeStrategyFrozen(decoded, Hash(raw), nil, fixtureBudget{}, strategy, cache, algorithm)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func assertFullIncrementalEqual(t *testing.T, got, want Diagnostic) {
	t.Helper()
	// These counters disclose actual arithmetic work. All business/evidence
	// fields, including input identity, are compared without exemptions.
	got.FactCacheHits, got.FactCacheMisses = 0, 0
	got.FactRollingUpdates = 0
	want.FactCacheHits, want.FactCacheMisses = 0, 0
	want.FactRollingUpdates = 0
	g, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	w, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(g, w) {
		var gotFields, wantFields map[string]json.RawMessage
		_ = json.Unmarshal(g, &gotFields)
		_ = json.Unmarshal(w, &wantFields)
		fields := map[string]bool{}
		for key := range gotFields {
			fields[key] = true
		}
		for key := range wantFields {
			fields[key] = true
		}
		different := []string{}
		for key := range fields {
			if !bytes.Equal(gotFields[key], wantFields[key]) {
				different = append(different, key)
			}
		}
		sort.Strings(different)
		t.Fatalf("full/incremental business or evidence output differs: fields=%v", different)
	}
}

func TestIncrementalResolvedDependenciesFullDifferential(t *testing.T) {
	algorithm := strings.Repeat("b", 64)
	for _, strategy := range []string{R1StrategyID, V2StrategyID} {
		t.Run(strategy, func(t *testing.T) {
			base := incrementalFixture()
			for _, tc := range []struct {
				name         string
				modify       func(*Frozen)
				hits, misses int
			}{
				{"same_input", func(*Frozen) {}, 2, 0},
				{"next_trade_session_recomputes", advanceFixture, 0, 2},
				{"cutoff_and_unrelated_late_receipt", func(f *Frozen) {
					f.Cutoff = f.Input.Observation + "T21:00:00+08:00"
					extra := fixtureRow(map[string]any{})
					for k, v := range f.Input.Receipts[0] {
						extra[k] = v
					}
					fixtureSet(extra, "receipt_id", "TEST_FIXTURE_UNCONSUMED")
					f.Input.Receipts = append(f.Input.Receipts, extra)
				}, 2, 0},
				{"security_name", func(f *Frozen) {
					var states []Row
					_ = json.Unmarshal(f.Input.Universe["securities"], &states)
					fixtureSet(states[0], "name", "TEST_FIXTURE_RENAMED")
					fixtureSet(f.Input.Universe, "securities", states)
				}, 1, 1},
				{"old_price_revision_propagates_long_MA", func(f *Frozen) { fixtureSet(f.Input.Bars[100], "comparable_close", "12.00") }, 1, 1},
				{"basis_change", func(f *Frozen) { fixtureSet(f.Input.Bars[100], "comparable_basis", "TEST_FIXTURE_HFQ_V2") }, 1, 1},
				{"selected_source_policy", func(f *Frozen) { fixtureSet(f.Input.Receipts[1], "field_version", "TEST_FIXTURE_V2") }, 1, 1},
				{"late_revision_selected_at_new_cutoff", func(f *Frozen) {
					f.Cutoff = f.Input.Observation + "T21:00:00+08:00"
					row := Row{}
					for k, v := range f.Input.Bars[319] {
						row[k] = v
					}
					fixtureSet(row, "comparable_close", "14.00")
					for _, k := range []string{"source_published_at", "first_seen_at", "ingested_at"} {
						fixtureSet(row, k, f.Input.Observation+"T20:30:00+08:00")
					}
					f.Input.Bars = append(f.Input.Bars, row)
				}, 1, 1},
				{"theme_membership_background_recomputed", func(f *Frozen) {
					stamp := f.Input.Observation + "T15:00:00+08:00"
					f.Input.Themes = []Row{fixtureRow(map[string]any{"theme_id": "TEST_FIXTURE_THEME", "name": "TEST_FIXTURE", "definition_version": "TEST_FIXTURE_V1", "theme_type": "INDUSTRY", "member_ids": []string{"TEST_FIXTURE_A.SH", "TEST_FIXTURE_B.SH"}, "effective_from": "2020-01-01", "event_time": stamp, "source_published_at": stamp, "first_seen_at": stamp, "ingested_at": stamp, "source_refs": []string{"TEST_FIXTURE_MASTER"}})}
				}, 2, 0},
				{"missing_current_price", func(f *Frozen) { f.Input.Bars = append(f.Input.Bars[:319], f.Input.Bars[320:]...) }, 1, 1},
				{"qualification_member_suspended", func(f *Frozen) {
					var states []Row
					_ = json.Unmarshal(f.Input.Universe["securities"], &states)
					fixtureSet(states[0], "suspended", true)
					fixtureSet(f.Input.Universe, "securities", states)
				}, 1, 0},
				{"cutoff_re_resolves_visibility", func(f *Frozen) {
					f.Cutoff = f.Input.Observation + "T15:00:00+08:00"
					fixtureSet(f.Input.Bars[319], "source_published_at", f.Input.Observation+"T16:00:00+08:00")
				}, 1, 1},
				{"calendar_change", func(f *Frozen) {
					f.Input.Sessions = append(f.Input.Sessions[:90], f.Input.Sessions[91:]...)
					f.Input.Bars = append(f.Input.Bars[:90], f.Input.Bars[91:]...)
					f.Input.Bars = append(f.Input.Bars[:409], f.Input.Bars[410:]...)
				}, 0, 2},
			} {
				t.Run(tc.name, func(t *testing.T) {
					cache := fixtureFactCache{}
					cold := fixtureAnalyze(t, base, cache, algorithm, strategy)
					if cold.FactCacheMisses != 2 {
						t.Fatal(cold.FactCacheMisses)
					}
					final := cloneFrozen(base)
					tc.modify(&final)
					incremental := fixtureAnalyze(t, final, cache, algorithm, strategy)
					full := fixtureAnalyze(t, final, nil, algorithm, strategy)
					assertFullIncrementalEqual(t, incremental, full)
					if incremental.FactCacheHits != tc.hits || incremental.FactCacheMisses != tc.misses {
						t.Fatalf("arithmetic hits/misses %d/%d want %d/%d", incremental.FactCacheHits, incremental.FactCacheMisses, tc.hits, tc.misses)
					}
				})
			}
		})
	}
}

func TestIncrementalAlgorithmIdentityAndUnknownBuildBoundaries(t *testing.T) {
	base := incrementalFixture()
	cache := fixtureFactCache{}
	fixtureAnalyze(t, base, cache, strings.Repeat("b", 64), R1StrategyID)
	changed := fixtureAnalyze(t, base, cache, strings.Repeat("c", 64), R1StrategyID)
	if changed.FactCacheHits != 0 || changed.FactCacheMisses != 2 {
		t.Fatal("changed arithmetic reused", changed.FactCacheHits)
	}
	cache = fixtureFactCache{}
	fixtureAnalyze(t, base, cache, "UNRECORDED", R1StrategyID)
	final := cloneFrozen(base)
	final.Cutoff = final.Input.Observation + "T21:00:00+08:00"
	unknown := fixtureAnalyze(t, final, cache, "UNRECORDED", R1StrategyID)
	if unknown.FactCacheHits != 0 || unknown.FactCacheScope != "SAME_INPUT_ONLY_UNRECORDED_ALGORITHM" {
		t.Fatal("unknown implementation crossed input boundary")
	}
}

func TestPublicResearchWrapperUnrecordedAndInjectedIdentityBoundaries(t *testing.T) {
	for _, recorded := range []bool{false, true} {
		name := "unrecorded_public_wrapper"
		if recorded {
			name = "composition_injected_identity"
		}
		t.Run(name, func(t *testing.T) {
			call := func(f Frozen, hash string, cache FactsCache) Diagnostic {
				var result Diagnostic
				var err error
				if recorded {
					result, err = AnalyzeStrategyFrozenWithIdentity(f, hash, nil, fixtureBudget{}, R1StrategyID, cache, strings.Repeat("b", 64))
				} else {
					result, err = AnalyzeStrategyFrozen(f, hash, nil, fixtureBudget{}, R1StrategyID, cache)
				}
				if err != nil {
					t.Fatal(err)
				}
				return result
			}
			base := incrementalFixture()
			original := fixtureFrozenBytes(t, base)
			decoded, err := DecodeFrozen(original)
			if err != nil {
				t.Fatal(err)
			}
			cache := fixtureFactCache{}
			cold := call(decoded, Hash(original), cache)
			same := call(decoded, Hash(original), cache)
			if cold.FactCacheMisses != 2 || same.FactCacheHits != 2 {
				t.Fatal("same-input arithmetic cache boundary changed")
			}
			base.Purpose = "TEST_FIXTURE_DIFFERENT_ENVELOPE_SAME_SELECTED_BYTES"
			changed := fixtureFrozenBytes(t, base)
			decoded, err = DecodeFrozen(changed)
			if err != nil {
				t.Fatal(err)
			}
			incremental := call(decoded, Hash(changed), cache)
			full := call(decoded, Hash(changed), nil)
			assertFullIncrementalEqual(t, incremental, full)
			if recorded {
				if incremental.FactCacheHits != 2 || incremental.FactCacheScope != "RESOLVED_STOCK_ARITHMETIC_CROSS_INPUT" {
					t.Fatal("audited injected identity was lost", incremental.FactCacheHits, incremental.FactCacheScope)
				}
			} else if incremental.FactCacheHits != 0 || incremental.FactCacheMisses != 2 ||
				incremental.FactCacheScope != "SAME_INPUT_ONLY_UNRECORDED_ALGORITHM" || incremental.FactRollingUpdates != 0 {
				t.Fatal("public wrapper inferred an audited build from its environment", incremental.FactCacheHits, incremental.FactCacheScope)
			}
		})
	}
}

func TestIncrementalRawIdentityAndEnclosingHistoryBoundary(t *testing.T) {
	f := incrementalFixture()
	resolver, err := NewResolver(f.Input.Receipts, f.Cutoff, f.Input.Coverage, f.Input.Observation, f.Input.Sessions)
	if err != nil {
		t.Fatal(err)
	}
	var states []Row
	_ = json.Unmarshal(f.Input.Universe["securities"], &states)
	row := f.Input.Bars[0]
	bindings := []c.SelectedEvidenceBinding{{ProjectionHash: RowHash(row), Identity: c.EvidenceIdentity{Origin: "HISTORY_ROW", ParentHash: strings.Repeat("c", 64), RawSHA256: strings.Repeat("d", 64)}}}
	f.Input.HistoryHash = bindings[0].Identity.ParentHash
	key := func() string {
		value, _ := factCacheKeyWithIdentity(strings.Repeat("b", 64), f, "TEST_FIXTURE_INPUT", states[0].Text("instrument_id"), states[0], f.Input.Sessions, []Row{row}, bindings, resolver)
		return value
	}
	before := key()
	bindings[0].Identity.ParentHash = strings.Repeat("e", 64)
	f.Input.HistoryHash = bindings[0].Identity.ParentHash
	if key() != before {
		t.Fatal("unrelated enclosing history change invalidated independently verified selected bytes")
	}
	bindings[0].Identity.RawSHA256 = strings.Repeat("a", 64)
	if key() == before {
		t.Fatal("historical original bytes changed without invalidation")
	}
}

func TestIncrementalUnboundEvidenceCannotCrossInputs(t *testing.T) {
	f := incrementalFixture()
	f.Input.HistoryHash = strings.Repeat("c", 64)
	resolver, err := NewResolver(f.Input.Receipts, f.Cutoff, f.Input.Coverage, f.Input.Observation, f.Input.Sessions)
	if err != nil {
		t.Fatal(err)
	}
	var states []Row
	_ = json.Unmarshal(f.Input.Universe["securities"], &states)
	row := f.Input.Bars[0]
	for _, tc := range []struct {
		name, origin, parent, raw string
		bound                     bool
	}{
		{"authenticated_frozen", "FROZEN_INPUT", strings.Repeat("a", 64), "", true},
		{"frozen_parent_only", "FROZEN_INPUT", strings.Repeat("a", 64), "", false},
		{"frozen_claimed_raw", "FROZEN_INPUT", strings.Repeat("a", 64), strings.Repeat("d", 64), false},
		{"authenticated_history", "HISTORY_ROW", f.Input.HistoryHash, strings.Repeat("d", 64), true},
		{"history_missing_raw", "HISTORY_ROW", f.Input.HistoryHash, "", false},
		{"history_invalid_raw", "HISTORY_ROW", f.Input.HistoryHash, "TEST_FIXTURE_INVALID", false},
		{"history_wrong_parent", "HISTORY_ROW", strings.Repeat("e", 64), strings.Repeat("d", 64), false},
		{"unbound_adapter", "UNBOUND_TEST_ADAPTER", f.Input.HistoryHash, strings.Repeat("d", 64), false},
		{"unregistered_origin", "UNREGISTERED", f.Input.HistoryHash, strings.Repeat("d", 64), false},
		{"frozen_missing_parent", "FROZEN_INPUT", "", "", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if tc.name == "authenticated_frozen" {
				actual := cloneFrozen(f)
				actual.Input.HistoryRef = "history_" + actual.Input.HistoryHash
				firstRaw := fixtureFrozenBytes(t, actual)
				first, err := DecodeFrozen(firstRaw)
				if err != nil {
					t.Fatal(err)
				}
				var actualStates []Row
				_ = json.Unmarshal(first.Input.Universe["securities"], &actualStates)
				identity, ok := ev.DecodedInputBarIdentity(first, Hash(firstRaw), 0)
				if !ok {
					t.Fatal("strict source proof absent")
				}
				bindings := []c.SelectedEvidenceBinding{{ProjectionHash: RowHash(first.Input.Bars[0]), Identity: identity}}
				before, scope := factCacheKeyWithIdentity(strings.Repeat("b", 64), first, Hash(firstRaw), actualStates[0].Text("instrument_id"), actualStates[0], first.Input.Sessions, []Row{first.Input.Bars[0]}, bindings, resolver)
				actual.Purpose = "TEST_FIXTURE_SAME_MARKET_DIFFERENT_ENVELOPE"
				secondRaw := fixtureFrozenBytes(t, actual)
				second, err := DecodeFrozen(secondRaw)
				if err != nil {
					t.Fatal(err)
				}
				identity, ok = ev.DecodedInputBarIdentity(second, Hash(secondRaw), 0)
				if !ok {
					t.Fatal("second strict source proof absent")
				}
				bindings[0].Identity = identity
				after, nextScope := factCacheKeyWithIdentity(strings.Repeat("b", 64), second, Hash(secondRaw), actualStates[0].Text("instrument_id"), actualStates[0], second.Input.Sessions, []Row{second.Input.Bars[0]}, bindings, resolver)
				if Hash(firstRaw) == Hash(secondRaw) || before != after || scope != "RESOLVED_STOCK_ARITHMETIC_CROSS_INPUT" || nextScope != scope {
					t.Fatal("valid independently authenticated selected evidence failed to reuse", scope, nextScope)
				}
				return
			}
			bindings := []c.SelectedEvidenceBinding{{ProjectionHash: RowHash(row), Identity: c.EvidenceIdentity{Origin: tc.origin, ParentHash: tc.parent, RawSHA256: tc.raw}}}
			before, scope := factCacheKeyWithIdentity(strings.Repeat("b", 64), f, strings.Repeat("a", 64), states[0].Text("instrument_id"), states[0], f.Input.Sessions, []Row{row}, bindings, resolver)
			if tc.origin == "FROZEN_INPUT" && tc.bound {
				bindings[0].Identity.ParentHash = strings.Repeat("f", 64)
			}
			after, nextScope := factCacheKeyWithIdentity(strings.Repeat("b", 64), f, strings.Repeat("f", 64), states[0].Text("instrument_id"), states[0], f.Input.Sessions, []Row{row}, bindings, resolver)
			if tc.bound {
				if before != after || scope != "RESOLVED_STOCK_ARITHMETIC_CROSS_INPUT" || nextScope != scope {
					t.Fatal("valid independently authenticated selected evidence failed to reuse", scope, nextScope)
				}
			} else if before == after || scope != "SAME_INPUT_ONLY_UNBOUND_EVIDENCE" || nextScope != scope {
				t.Fatal("unbound evidence crossed the same-cutoff input boundary", scope, nextScope)
			}
		})
	}
}
