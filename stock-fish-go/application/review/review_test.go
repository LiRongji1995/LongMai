package review

import (
	"context"
	"encoding/json"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"strings"
	"testing"
	"time"
)

func fixtureClock(day string) map[string]any {
	return map[string]any{"event_time": day + "T15:00:00+08:00", "source_published_at": day + "T15:00:00+08:00", "first_seen_at": day + "T16:00:00+08:00", "ingested_at": day + "T16:00:00+08:00"}
}
func fixtureFrozen(t *testing.T, last string, price string) c.InputRecord {
	t.Helper()
	first := "2026-01-02"
	receipt := fixtureClock(first)
	for k, v := range map[string]any{"receipt_id": "TEST_FIXTURE", "provider": "TEST_FIXTURE", "dataset": "TEST_FIXTURE", "field_version": "TEST_FIXTURE_V1", "content_hash": strings.Repeat("a", 64), "row_count": 22, "pagination_complete": true, "status": "COMPLETE"} {
		receipt[k] = v
	}
	securities := []any{}
	for _, id := range []string{"600001.SH", "600002.SH"} {
		state := fixtureClock(first)
		for k, v := range map[string]any{"instrument_id": id, "exchange": "SSE", "board": "MAIN", "security_type": "A_COMMON", "listed_on": "2020-01-01", "effective_from": "2020-01-01", "st": false, "suspended": false, "source_refs": []string{"TEST_FIXTURE"}} {
			state[k] = v
		}
		securities = append(securities, state)
	}
	universe := fixtureClock(first)
	for k, v := range map[string]any{"snapshot_id": "TEST_FIXTURE", "securities": securities, "source_refs": []string{"TEST_FIXTURE"}, "complete": true, "expected_count": 2} {
		universe[k] = v
	}
	sessions := []string{}
	bars := []any{}
	day, _ := time.Parse("2006-01-02", first)
	for i := 0; i <= 12; i++ {
		d := day.AddDate(0, 0, i).Format("2006-01-02")
		sessions = append(sessions, d)
		if d > last {
			continue
		}
		for _, id := range []string{"600001.SH", "600002.SH"} {
			p := "10"
			if i > 0 {
				p = price
			}
			bar := fixtureClock(d)
			for k, v := range map[string]any{"instrument_id": id, "session": d, "open": p, "high": p, "low": p, "close": p, "comparable_close": p, "comparable_basis": "TEST_FIXTURE_BASIS", "source_refs": []string{"TEST_FIXTURE"}, "quality": "VALID"} {
				bar[k] = v
			}
			bars = append(bars, bar)
		}
	}
	raw, err := json.Marshal(map[string]any{"purpose": "TEST_FIXTURE_REVIEW", "cutoff": last + "T16:00:00+08:00", "input": map[string]any{"observation_date": last, "sessions": sessions, "universe": universe, "bars": bars, "receipts": []any{receipt}, "coverage_type": "TEST_FIXTURE"}})
	if err != nil {
		t.Fatal(err)
	}
	f, err := ev.DecodeFrozen(raw)
	if err != nil {
		t.Fatal(err)
	}
	return c.InputRecord{ID: "frozen_" + c.Hash(raw), Raw: raw, RawHash: c.Hash(raw), Observation: f.Input.Observation, Cutoff: f.Cutoff, Coverage: f.Input.Coverage}
}
func fixtureEvidence(t *testing.T, in c.InputRecord) *Evidence {
	t.Helper()
	f, err := ev.DecodeFrozen(in.Raw)
	if err != nil {
		t.Fatal(err)
	}
	e, err := NewEvidence(context.Background(), f, nil)
	if err != nil {
		t.Fatal(err)
	}
	return e
}

func fixtureFrozenBytes(t *testing.T, f c.Frozen) []byte {
	t.Helper()
	input, err := ev.CanonicalInputProjection(context.Background(), f)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(map[string]any{"purpose": f.Purpose, "cutoff": f.Cutoff, "input": json.RawMessage(input)})
	if err != nil {
		t.Fatal(err)
	}
	return raw
}
func fixtureRun(t *testing.T, in c.InputRecord) c.Run {
	t.Helper()
	manifest, _ := json.Marshal(map[string]any{"go_source_hash": c.Hash([]byte("TEST_FIXTURE_BUILD")), "config_hashes": map[string]string{"TEST_FIXTURE": c.Hash([]byte("TEST_FIXTURE_CONFIG"))}, "conversion_version": ev.InputContractVersion, "input_bytes_hash": in.RawHash, "cutoff": in.Cutoff})
	summary, _ := json.Marshal(map[string]any{"qualification": c.Qualification{Eligible: []string{"600001.SH", "600002.SH"}, Excluded: map[string]string{"688001.SH": "OUTSIDE_MAIN_BOARD_A_COMMON"}, Unknown: map[string]string{}, UniverseComplete: true}})
	return c.Run{ID: "TEST_FIXTURE_RUN", InputID: in.ID, Strategy: c.R1, CreatedAt: "2026-01-02T17:00:00+08:00", Manifest: manifest, Summary: summary, Items: []c.Item{{ID: "600001.SH", Payload: json.RawMessage(`{"decision":{"candidate_status":"CORE_PASS","matched_setups":["START","ACCELERATION"]}}`)}, {ID: "600002.SH", Payload: json.RawMessage(`{"decision":{"candidate_status":"CORE_FAIL","matched_setups":[]}}`)}}}
}

func TestFrozenSignalAndMatureUnknownNeverZero(t *testing.T) {
	ctx := context.Background()
	in := fixtureFrozen(t, "2026-01-02", "10")
	run := fixtureRun(t, in)
	contract := DefaultContract()
	now := time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC)
	signals, err := Register(ctx, run, in, fixtureEvidence(t, in), contract, run.CreatedAt, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(signals) != 3 || signals[0].Decision != "CORE_PASS" || len(signals[0].Matched) != 2 || signals[2].Qualified {
		t.Fatal(signals)
	}
	label := fixtureFrozen(t, "2026-01-14", "11")
	out, err := Evaluate(ctx, signals, label, fixtureEvidence(t, label), contract, c.Hash([]byte("TEST_FIXTURE_EVALUATOR")))
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 12 {
		t.Fatal(len(out))
	}
	for _, o := range out {
		if o.Instrument == "688001.SH" {
			if o.Return.Known() || o.Status != "QUALIFICATION_EXCLUDED" {
				t.Fatal(o)
			}
		} else if o.Status != "MATURE" || o.Return.Cmp(c.N(".1")) != 0 {
			t.Fatal(o)
		}
	}
	missing := fixtureFrozen(t, "2026-01-14", "11")
	f, err := ev.DecodeFrozen(missing.Raw)
	if err != nil {
		t.Fatal(err)
	}
	filtered := []c.Row{}
	for _, row := range f.Input.Bars {
		if row.Text("session") != "2026-01-05" {
			filtered = append(filtered, row)
		}
	}
	f.Input.Bars = filtered
	missing.Raw = fixtureFrozenBytes(t, f)
	missing.RawHash = c.Hash(missing.Raw)
	missing.ID = "frozen_" + missing.RawHash
	out, err = Evaluate(ctx, signals, missing, fixtureEvidence(t, missing), contract, c.Hash([]byte("TEST_FIXTURE_EVALUATOR")))
	if err != nil {
		t.Fatal(err)
	}
	for _, o := range out {
		if o.Instrument == "600001.SH" && o.Window == 3 {
			if o.Status != "UNKNOWN" || o.Return.Known() {
				t.Fatal("missing label converted to zero", o)
			}
		}
	}
	immature := fixtureFrozen(t, "2026-01-03", "11")
	out, err = Evaluate(ctx, signals, immature, fixtureEvidence(t, immature), contract, c.Hash([]byte("TEST_FIXTURE_EVALUATOR")))
	if err != nil {
		t.Fatal(err)
	}
	for _, o := range out {
		if o.Window == 10 && o.Instrument == "600001.SH" && (o.Status != "NOT_MATURED" || o.Return.Known()) {
			t.Fatal(o)
		}
	}
}

func TestReviewBasisRevisionParentVisibilityAndMix(t *testing.T) {
	in := fixtureFrozen(t, "2026-01-02", "10")
	run := fixtureRun(t, in)
	contract := DefaultContract()
	signals, err := Register(context.Background(), run, in, fixtureEvidence(t, in), contract, run.CreatedAt, time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	for _, change := range []string{"REFERENCE", "BASIS", "LATE_PARENT"} {
		t.Run(change, func(t *testing.T) {
			label := fixtureFrozen(t, "2026-01-14", "11")
			f, _ := ev.DecodeFrozen(label.Raw)
			if change == "LATE_PARENT" {
				f.Input.Receipts[0]["first_seen_at"] = json.RawMessage(`"2026-02-01T16:00:00+08:00"`)
				f.Input.Receipts[0]["ingested_at"] = json.RawMessage(`"2026-02-01T16:00:00+08:00"`)
			} else {
				for _, row := range f.Input.Bars {
					if row.Text("session") == in.Observation && change == "REFERENCE" {
						row["comparable_close"] = json.RawMessage(`"12"`)
					}
					if row.Text("session") > in.Observation && change == "BASIS" {
						row["comparable_basis"] = json.RawMessage(`"REVISED_BASIS"`)
					}
				}
			}
			label.Raw = fixtureFrozenBytes(t, f)
			label.RawHash = c.Hash(label.Raw)
			label.ID = "frozen_" + label.RawHash
			out, err := Evaluate(context.Background(), signals, label, fixtureEvidence(t, label), contract, c.Hash([]byte("TEST_FIXTURE_EVALUATOR")))
			if err != nil {
				t.Fatal(err)
			}
			for _, o := range out {
				if o.Instrument == "600001.SH" && o.Window == 5 && (o.Return.Known() || o.Status != "UNKNOWN") {
					t.Fatal(change, o)
				}
			}
		})
	}
	label := fixtureFrozen(t, "2026-01-14", "11")
	label.Coverage = "PROSPECTIVE_CAPTURED"
	if _, err = Evaluate(context.Background(), signals, label, fixtureEvidence(t, label), contract, c.Hash([]byte("TEST_FIXTURE_EVALUATOR"))); err == nil {
		t.Fatal("fixture mixed with real input")
	}
}

func TestPublicationUnknownUsesObservedUpperBoundAndReplaySeparated(t *testing.T) {
	in := fixtureFrozen(t, "2026-01-02", "10")
	f, _ := ev.DecodeFrozen(in.Raw)
	f.Input.Coverage = "PROSPECTIVE_CAPTURED"
	in.Raw = fixtureFrozenBytes(t, f)
	in.RawHash = c.Hash(in.Raw)
	in.ID = "frozen_" + in.RawHash
	in.Coverage = f.Input.Coverage
	run := fixtureRun(t, in)
	late := time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC)
	signals, err := Register(context.Background(), run, in, fixtureEvidence(t, in), DefaultContract(), "", late)
	if err != nil {
		t.Fatal(err)
	}
	if signals[0].PublishedAt != "" || signals[0].Mode != "HISTORICAL_REPLAY" || signals[0].ObservedAvailableAt != late.Format(time.RFC3339Nano) {
		t.Fatal(signals[0])
	}
}

func TestLabelRevisionSelectionParsedClockAndConflict(t *testing.T) {
	one := Outcome{ID: "A", SignalID: "S", Window: 1, LabelCutoff: "2026-10-02T21:00:00+08:00"}
	two := one
	two.ID = "B"
	two.LabelCutoff = "2026-10-02T14:00:00Z"
	r1, _ := NewRecord("OUTCOME", "A", 1, one)
	r2, _ := NewRecord("OUTCOME", "B", 1, two)
	latest, err := LatestOutcomes([]Record{r2, r1}, time.Date(2026, 10, 3, 0, 0, 0, 0, time.UTC))
	if err != nil || latest["S:1"].ID != "B" {
		t.Fatal(latest, err)
	}
	one.ID = "C"
	one.LabelCutoff = "2026-10-02T14:00:00+00:00"
	r3, _ := NewRecord("OUTCOME", "C", 1, one)
	if _, err = LatestOutcomes([]Record{r2, r3}, time.Date(2026, 10, 3, 0, 0, 0, 0, time.UTC)); err == nil {
		t.Fatal("same instant conflicting labels silently chosen")
	}
}

func fixtureLab(t *testing.T) ([]Record, Candidate, Candidate, Contract) {
	t.Helper()
	contract := DefaultContract()
	contract.Version = "TEST_FIXTURE_CONTRACT"
	contract.FixtureOnly = true
	contract.MinSamples = 2
	contract.MinDates = 2
	contract.MinBlocks = 2
	base := Candidate{ID: "TEST_FIXTURE_BASE", Role: "BASELINE", Scope: "TEST_FIXTURE", TimeSplitContract: CandidateTimeSplitVersion, Implementation: c.Hash([]byte("TEST_FIXTURE_BASE")), ConfigHash: c.Hash([]byte("TEST_FIXTURE_BASE_CFG")), ContractHash: Digest(contract), EvaluationStart: "2026-01-02", EvaluationEnd: "2026-04-01", FixtureOnly: true}
	candidate := base
	candidate.ID = "TEST_FIXTURE_CANDIDATE"
	candidate.Role = "CANDIDATE"
	candidate.Parent = base.ID
	candidate.Implementation = c.Hash([]byte("TEST_FIXTURE_CANDIDATE"))
	candidate.ConfigHash = c.Hash([]byte("TEST_FIXTURE_CANDIDATE_CFG"))
	rows := []Record{}
	for _, v := range []Candidate{base, candidate} {
		r, _ := NewRecord("CANDIDATE", v.ID, 1, v)
		rows = append(rows, r)
	}
	for _, v := range []Candidate{base, candidate} {
		for _, day := range []string{"2026-01-02", "2026-02-02"} {
			for _, id := range []string{"600001.SH", "600002.SH"} {
				status := "CORE_FAIL"
				if (v.Role == "BASELINE" && id == "600002.SH") || (v.Role == "CANDIDATE" && id == "600001.SH") {
					status = "CORE_PASS"
				}
				s := Signal{ID: v.ID + day + id, RunID: v.ID + day, InputHash: c.Hash([]byte("TEST_FIXTURE_INPUT" + day)), Instrument: id, Observation: day, Decision: status, Implementation: v.Implementation, ConfigHash: v.ConfigHash, ContractHash: Digest(contract), Mode: "TEST_FIXTURE", Qualified: true, QualificationStatus: "QUALIFIED", UniverseComplete: true, PoolComplete: true}
				r, _ := NewRecord("SIGNAL", s.ID, 1, s)
				rows = append(rows, r)
				for _, h := range contract.Windows {
					ret := c.N("0")
					if id == "600001.SH" {
						ret = c.N(".2")
					}
					d, _ := time.Parse("2006-01-02", day)
					o := Outcome{ID: s.ID + stringInt(h), SignalID: s.ID, RunID: s.RunID, Instrument: id, Window: h, LabelCutoff: "2026-03-01T16:00:00+08:00", LabelEnd: d.AddDate(0, 0, h).Format("2006-01-02"), Status: "MATURE", Return: ret, Benchmark: c.N(".1"), Excess: ret.Sub(c.N(".1")), Costs: map[string]c.Number{"0": ret}, ContractHash: Digest(contract)}
					r, _ := NewRecord("OUTCOME", o.ID, 1, o)
					rows = append(rows, r)
				}
			}
		}
	}
	return rows, candidate, base, contract
}

func TestFixedEvaluationAttemptsMinimumAndFiniteState(t *testing.T) {
	rows, candidate, base, contract := fixtureLab(t)
	now := time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC)
	v, err := Compare(rows, candidate, base, contract, now, "ATTEMPT_ONE")
	if err != nil || v.Status != "REVIEW_REQUIRED" || v.Attempt != 1 {
		t.Fatal(v, err)
	}
	v.EvaluatorBuild = c.Hash([]byte("TEST_FIXTURE_EVALUATOR"))
	r, _ := NewRecord("EVALUATION", v.ID, 1, v)
	rows = append(rows, r)
	second, err := Compare(rows, candidate, base, contract, now, "ATTEMPT_TWO")
	if err != nil || second.Attempt != 2 {
		t.Fatal(second, err)
	}
	if _, err = TransitionCandidate(rows, candidate, "PRODUCTION", v.ID, "request", now); err == nil {
		t.Fatal("production promoted")
	}
	tr, err := TransitionCandidate(rows, candidate, "OFFLINE_EVALUATED", v.ID, "fixture engine check", now)
	if err != nil {
		t.Fatal(err)
	}
	r, _ = NewRecord("TRANSITION", candidate.ID, 1, tr)
	rows = append(rows, r)
	if _, err = TransitionCandidate(rows, candidate, "REVIEW_REQUIRED", v.ID, "fixed evidence review", now); err != nil {
		t.Fatal(err)
	}
	contract.MinSamples = 50
	candidate.ContractHash = Digest(contract)
	base.ContractHash = Digest(contract)
	insufficient, err := Compare(rows, candidate, base, contract, now, "ATTEMPT_INSUFFICIENT")
	if err != nil || insufficient.Status != "INSUFFICIENT_EVIDENCE" {
		t.Fatal(insufficient, err)
	}
	tr, err = TransitionCandidate(rows, candidate, "REJECTED", v.ID, "stop observation", now)
	if err != nil {
		t.Fatal(err)
	}
	r, _ = NewRecord("TRANSITION", candidate.ID, 2, tr)
	rows = append(rows, r)
	if CandidateState(rows, candidate.ID) != "REJECTED" || len(rows) < 1 {
		t.Fatal("history lost")
	}
}

func TestDuplicateRunsAndWindowBoundaryCannotInflateEvidence(t *testing.T) {
	rows, candidate, base, contract := fixtureLab(t)
	now := time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC)
	for _, r := range rows {
		if r.Kind == "SIGNAL" {
			var s Signal
			_ = json.Unmarshal(r.Payload, &s)
			if s.Implementation == candidate.Implementation {
				s.ID = "DUPLICATE"
				s.RunID = "OTHER_RUN"
				r, _ = NewRecord("SIGNAL", s.ID, 1, s)
				rows = append(rows, r)
				break
			}
		}
	}
	if _, err := Compare(rows, candidate, base, contract, now, "DUPLICATE_ATTEMPT"); err == nil || !strings.Contains(err.Error(), "DUPLICATE_OBSERVATION") {
		t.Fatal(err)
	}
	rows, candidate, base, contract = fixtureLab(t)
	candidate.EvaluationEnd = "2026-02-05"
	base.EvaluationEnd = candidate.EvaluationEnd
	v, err := Compare(rows, candidate, base, contract, now, "BOUNDARY")
	if err != nil || v.Status != "INSUFFICIENT_EVIDENCE" {
		t.Fatal(v, err)
	}
}

func TestCandidateTimeSplitFutureLeakageAndMinimum(t *testing.T) {
	contract := DefaultContract()
	contract.FixtureOnly = true
	contract.Version = "TEST_FIXTURE"
	contract.MinSamples = 2
	contract.MinDates = 2
	contract.MinBlocks = 2
	now := time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC)
	calendar := []string{}
	first := time.Date(2025, 12, 1, 0, 0, 0, 0, time.UTC)
	for i := 0; i < 100; i++ {
		calendar = append(calendar, first.AddDate(0, 0, i).Format("2006-01-02"))
	}
	v := Candidate{ID: "C", Role: "CANDIDATE", Parent: "B", TimeSplitContract: CandidateTimeSplitVersion, Hypothesis: "fixture hypothesis", Change: "independent fixture version", Scope: "TEST_FIXTURE", Implementation: c.Hash([]byte("TEST_FIXTURE_IMPLEMENTATION")), ConfigHash: c.Hash([]byte("TEST_FIXTURE_CONFIG")), DataCutoff: "2025-12-02T16:00:00+08:00", SelectionEnd: "2025-12-01", EvaluationStart: "2026-01-02", EvaluationEnd: "2026-03-01", CalendarInputID: "CAL", CalendarHash: c.Hash([]byte("TEST_FIXTURE_CALENDAR")), ContractVersion: contract.Version, ContractHash: Digest(contract), PrimaryMetric: "PRIMARY_WINDOW_EXCESS_MEDIAN", SecondaryMetrics: []string{"ALL_FIXED_WINDOWS", "NEGATIVE_TAIL", "FIXED_COST_SENSITIVITY"}, FixtureOnly: true, ProductionPromotion: "DISABLED"}
	if err := v.Validate(contract, calendar, now); err != nil {
		t.Fatal(err)
	}
	v.SelectionEnd = "2026-01-01"
	if err := v.Validate(contract, calendar, now); err == nil {
		t.Fatal("future data or purge leak accepted")
	}
	contract.FixtureOnly = false
	if err := contract.Validate(); err == nil {
		t.Fatal("real minimum lowered")
	}
}
