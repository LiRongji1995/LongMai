package review

import (
	"encoding/json"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"reflect"
	"strings"
	"testing"
	"time"
)

func qualificationFixture(t *testing.T, unknown bool) ([]Record, Candidate, Candidate, Contract) {
	t.Helper()
	rows, candidate, baseline, contract := fixtureLab(t)
	if unknown {
		for i, r := range rows {
			if r.Kind == "SIGNAL" {
				var s Signal
				_ = json.Unmarshal(r.Payload, &s)
				s.PoolComplete = false
				rows[i], _ = NewRecord(r.Kind, r.ID, r.Revision, s)
			} else if r.Kind == "OUTCOME" {
				var o Outcome
				_ = json.Unmarshal(r.Payload, &o)
				o.Benchmark = c.Number{}
				o.Excess = c.Number{}
				o.Reasons = append(o.Reasons, "FULL_QUALIFIED_BENCHMARK_UNAVAILABLE")
				rows[i], _ = NewRecord(r.Kind, r.ID, r.Revision, o)
			}
		}
	}
	for _, version := range []Candidate{candidate, baseline} {
		for _, day := range []string{"2026-01-02", "2026-02-02"} {
			statuses := []string{"KNOWN_EXCLUDED"}
			if unknown {
				statuses = append(statuses, "UNKNOWN")
			}
			for _, status := range statuses {
				id, decision, reason, outcomeStatus := "688001.SH", "QUALIFICATION_EXCLUDED", "OUTSIDE_MAIN_BOARD_A_COMMON", "QUALIFICATION_EXCLUDED"
				if status == "UNKNOWN" {
					id, decision, reason, outcomeStatus = "600003.SH", "QUALIFICATION_UNKNOWN", "UNKNOWN_HISTORICAL_STATUS", "QUALIFICATION_UNKNOWN"
				}
				s := Signal{ID: version.ID + day + id, RunID: version.ID + day, InputHash: c.Hash([]byte("TEST_FIXTURE_INPUT" + day)), Instrument: id, Observation: day, Decision: decision, Implementation: version.Implementation, ConfigHash: version.ConfigHash, ContractHash: Digest(contract), Mode: "TEST_FIXTURE", QualificationStatus: status, QualificationReason: reason, UniverseComplete: true, PoolComplete: !unknown}
				r, _ := NewRecord("SIGNAL", s.ID, 1, s)
				rows = append(rows, r)
				for _, h := range contract.Windows {
					o := Outcome{ID: s.ID + stringInt(h), SignalID: s.ID, RunID: s.RunID, Instrument: id, Window: h, LabelCutoff: "2026-03-01T16:00:00+08:00", Status: outcomeStatus, ContractHash: Digest(contract), Reasons: []string{reason}}
					r, _ := NewRecord("OUTCOME", o.ID, 1, o)
					rows = append(rows, r)
				}
			}
		}
	}
	return rows, candidate, baseline, contract
}

func TestCandidateDeclarationClockCannotAttestPostCommitPreregistration(t *testing.T) {
	_, candidate, baseline, _ := fixtureLab(t)
	contract := DefaultContract()
	for _, v := range []*Candidate{&candidate, &baseline} {
		v.FixtureOnly = false
		v.ContractHash = Digest(contract)
		v.CreatedAt = "2025-12-01T00:00:00Z"
	}
	rows := []Record{}
	for _, v := range []Candidate{candidate, baseline} {
		r, err := NewRecord("CANDIDATE", v.ID, 1, v)
		if err != nil {
			t.Fatal(err)
		}
		rows = append(rows, r)
	}
	before := Digest(rows)
	got, err := CandidateFrom(rows, candidate.ID)
	if !errors.Is(err, ErrPreregistrationPublicationUnproven) || got.CreatedAt != candidate.CreatedAt {
		t.Fatal("old transaction-entry declaration clock became available proof", got, err)
	}
	asof := time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC)
	if _, err = Compare(rows, candidate, baseline, contract, asof, "TEST_FIXTURE_UNPROVEN_PUBLICATION"); !errors.Is(err, ErrPreregistrationPublicationUnproven) {
		t.Fatal("non-fixture comparison bypassed missing post-commit proof", err)
	}
	if _, err = TransitionCandidate(rows, candidate, "OFFLINE_EVALUATED", "TEST_FIXTURE_OLD_BEST", "TEST_FIXTURE missing proof", asof); !errors.Is(err, ErrPreregistrationPublicationUnproven) {
		t.Fatal("non-fixture promotion bypassed missing publication proof", err)
	}
	if _, err = TransitionCandidate(rows, candidate, "REJECTED", "", "TEST_FIXTURE stop unproven declaration", asof); err != nil {
		t.Fatal("unproven declaration could not be explicitly rejected", err)
	}
	if Digest(rows) != before {
		t.Fatal("publication check changed immutable declaration")
	}
	// An ordinary state transition, even with an early clock and a matching
	// hash in its reason, is not a distinct post-commit read proof.
	r, err := NewRecord("TRANSITION", candidate.ID, 1, Transition{CandidateID: candidate.ID, From: "PROPOSED", To: "PROPOSED", At: candidate.CreatedAt, Reason: "TEST_FIXTURE alleged availability " + rows[0].Hash, ProductionPromotion: "DISABLED"})
	if err != nil {
		t.Fatal(err)
	}
	rows = append(rows, r)
	if _, err = CandidateFrom(rows, candidate.ID); !errors.Is(err, ErrPreregistrationPublicationUnproven) {
		t.Fatal("state event disguised as publication proof", err)
	}
}

func TestComparisonKnownExclusionsDoNotRequireMaturePrices(t *testing.T) {
	rows, candidate, baseline, contract := qualificationFixture(t, false)
	now := time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC)
	result, err := Compare(rows, candidate, baseline, contract, now, "TEST_FIXTURE_KNOWN_EXCLUSIONS")
	if err != nil {
		t.Fatal(err)
	}
	if result.Status != "REVIEW_REQUIRED" || result.CandidatePopulation.Registered != 6 || result.CandidatePopulation.Qualified != 4 || result.CandidatePopulation.KnownExcluded != 2 || result.CandidatePopulation.PrimaryMature != 4 || result.CandidatePopulation.PrimaryMissing != 0 {
		t.Fatal("known exclusions blocked valid qualified comparison", result)
	}
	for _, g := range result.CandidateGroups {
		if g.Group == "ALL_REGISTERED" && g.Window == 5 {
			if g.Samples != 6 || g.Mature != 4 || g.KnownExcluded != 2 || g.Missing != 0 || g.Return.N != 4 {
				t.Fatal("excluded records mixed into returns or missing denominator", g)
			}
		}
	}
}

func TestComparisonUnknownQualificationsRemainUnknownAndBlockFullPool(t *testing.T) {
	rows, candidate, baseline, contract := qualificationFixture(t, true)
	now := time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC)
	result, err := Compare(rows, candidate, baseline, contract, now, "TEST_FIXTURE_UNKNOWN_QUALIFICATION")
	if err != nil {
		t.Fatal(err)
	}
	if result.Status != "INSUFFICIENT_EVIDENCE" || result.CandidatePopulation.Registered != 8 || result.CandidatePopulation.Qualified != 4 || result.CandidatePopulation.KnownExcluded != 2 || result.CandidatePopulation.QualificationUnknown != 2 || result.CandidatePopulation.PrimaryMature != 4 || !result.CandidatePopulation.UniverseComplete || result.CandidatePopulation.QualifiedPoolComplete {
		t.Fatal("unknown identities became known exclusions or full qualified coverage", result)
	}
	if !c.Contains(result.Reasons, "QUALIFICATION_UNKNOWN_REMAINS_OUTSIDE_KNOWN_EXCLUSIONS") {
		t.Fatal(result.Reasons)
	}
	for _, g := range result.CandidateGroups {
		if g.Group == "ALL_REGISTERED" && g.Window == 5 {
			if g.Samples != 8 || g.Mature != 4 || g.KnownExcluded != 2 || g.QualificationUnknown != 2 || g.Return.N != 4 || g.Excess.N != 0 {
				t.Fatal("unknown counted as zero return or removed", g)
			}
		}
	}
}

func TestSavedV2MatchedStagesOnlyUseDeclaredConfirmedIdentities(t *testing.T) {
	decision := c.Row{"primary_stage": json.RawMessage(`"START"`), "alternate_stages": json.RawMessage(`["TREND_PULLBACK","HIGH_LEVEL_REPAIR","START","MISSING"]`), "stage_candidates": json.RawMessage(`{"START":{"status":"PASS"},"TREND_PULLBACK":{"status":"PASS"},"HIGH_LEVEL_REPAIR":{"status":"UNKNOWN"},"ACCELERATION":{"status":"PASS"}}`)}
	matched, reasons, err := savedMatchedStages(c.V2, decision)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(matched, []string{"START", "TREND_PULLBACK"}) || !c.Contains(reasons, "DECLARED_STAGE_NOT_CONFIRMED_PASS:HIGH_LEVEL_REPAIR") || !c.Contains(reasons, "DECLARED_STAGE_IDENTITY_OUTSIDE_FROZEN_SET:MISSING") {
		t.Fatal(matched, reasons)
	}
	if c.Contains(matched, "ACCELERATION") {
		t.Fatal("unselected PASS detector became final matched stage")
	}
	r1 := c.Row{"matched_setups": json.RawMessage(`["TREND_PULLBACK","START"]`)}
	matched, reasons, err = savedMatchedStages(c.R1, r1)
	if err != nil || len(reasons) != 0 || !reflect.DeepEqual(matched, []string{"TREND_PULLBACK", "START"}) {
		t.Fatal("R1 saved display order changed", matched, reasons, err)
	}
	matched, reasons, err = savedMatchedStages(c.V2, c.Row{})
	if err != nil || matched != nil || len(reasons) != 2 {
		t.Fatal("missing final stage identities became a legal empty set", matched, reasons, err)
	}
	matched, reasons, err = savedMatchedStages(c.V2, c.Row{"primary_stage": json.RawMessage(`null`), "alternate_stages": json.RawMessage(`[]`), "decision_status": json.RawMessage(`"REJECTED"`)})
	if err != nil || matched == nil || len(matched) != 0 || len(reasons) != 0 {
		t.Fatal("known empty selection became unknown", matched, reasons, err)
	}
	matched, reasons, err = savedMatchedStages(c.V2, c.Row{"primary_stage": json.RawMessage(`null`), "alternate_stages": json.RawMessage(`[]`), "decision_status": json.RawMessage(`"FOCUS"`)})
	if err != nil || matched != nil || !c.Contains(reasons, "V2_SELECTED_DECISION_WITHOUT_CONFIRMED_STAGE") {
		t.Fatal("FOCUS without saved stage became known empty", matched, reasons, err)
	}
	if _, _, err = savedMatchedStages(c.V2, c.Row{"primary_stage": json.RawMessage(`17`)}); err == nil {
		t.Fatal("malformed primary stage silently became no match")
	}
	matched, reasons, err = savedMatchedStages(c.R1, c.Row{"matched_setups": json.RawMessage(`null`)})
	if err != nil || matched != nil || len(reasons) != 0 {
		t.Fatal("R1 original null was rewritten", matched, reasons, err)
	}
}

func TestSavedV2SelectionGapsCannotBecomeFullEvaluationEvidence(t *testing.T) {
	rows, candidate, baseline, contract := fixtureLab(t)
	for i, r := range rows {
		if r.Kind != "SIGNAL" {
			continue
		}
		var s Signal
		_ = json.Unmarshal(r.Payload, &s)
		if s.Implementation == candidate.Implementation {
			s.Strategy = c.V2
			s.Reasons = append(s.Reasons, "DECLARED_STAGE_NOT_CONFIRMED_PASS:START")
			rows[i], _ = NewRecord(r.Kind, r.ID, r.Revision, s)
			break
		}
	}
	result, err := Compare(rows, candidate, baseline, contract, time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC), "TEST_FIXTURE_STAGE_GAP")
	if err != nil || result.Status != "INSUFFICIENT_EVIDENCE" || result.CandidatePopulation.SelectionUnknown != 1 || !c.Contains(result.Reasons, "SAVED_V2_STAGE_SELECTION_INCOMPLETE") {
		t.Fatal(result, err)
	}
}

func TestCandidatePromotionCannotSelectOlderBestAttemptOrEngineeringError(t *testing.T) {
	rows, candidate, baseline, contract := fixtureLab(t)
	now := time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC)
	good, err := Compare(rows, candidate, baseline, contract, now, "TEST_FIXTURE_GOOD_OLD")
	if err != nil {
		t.Fatal(err)
	}
	good.EvaluatorBuild = c.Hash([]byte("TEST_FIXTURE_EVALUATOR"))
	r, _ := NewRecord("EVALUATION", good.ID, 1, good)
	rows = append(rows, r)
	failed := good
	failed.ID = "TEST_FIXTURE_FAILED_NEW"
	failed.Attempt = 2
	failed.Status = "ENGINEERING_ERROR"
	failed.Reasons = []string{"TEST_FIXTURE_CONTRACT_FAILURE"}
	r, _ = NewRecord("EVALUATION", failed.ID, 1, failed)
	rows = append(rows, r)
	if _, err = TransitionCandidate(rows, candidate, "OFFLINE_EVALUATED", good.ID, "pick old best", now); err == nil || !strings.Contains(err.Error(), "LATEST_EVALUATION_REQUIRED") {
		t.Fatal("old successful attempt bypassed newer failure", err)
	}
	if _, err = TransitionCandidate(rows, candidate, "OFFLINE_EVALUATED", failed.ID, "failed engine", now); err == nil || !strings.Contains(err.Error(), "NOT_COMPLETED") {
		t.Fatal("engineering error became completed evaluation", err)
	}
	if _, err = Compare(rows, candidate, baseline, contract, now.Add(-time.Hour), "TEST_FIXTURE_REWIND"); err == nil || !strings.Contains(err.Error(), "CANNOT_REWIND") {
		t.Fatal("latest evidence was rewound", err)
	}
}

func TestComparisonDoesNotUseExploratoryLabelsOutsideFixedEvaluationWindow(t *testing.T) {
	rows, candidate, baseline, contract := fixtureLab(t)
	candidate.EvaluationEnd = "2026-02-10"
	baseline.EvaluationEnd = candidate.EvaluationEnd
	result, err := Compare(rows, candidate, baseline, contract, time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC), "TEST_FIXTURE_EXPLORATORY_BOUNDARY")
	if err != nil || result.Status != "REVIEW_REQUIRED" {
		t.Fatal(result, err)
	}
	for _, g := range result.CandidateGroups {
		if g.Group == "SELECTED" && g.Window == 10 {
			if g.Mature != 1 || g.Missing != 1 || g.Return.N != 1 {
				t.Fatal("exploratory window consumed data beyond evaluation end", g)
			}
		}
	}
}

func TestComparisonReportsDifferentInputIdentitiesAsMixedChange(t *testing.T) {
	rows, candidate, baseline, contract := fixtureLab(t)
	for i, r := range rows {
		if r.Kind != "SIGNAL" {
			continue
		}
		var s Signal
		_ = json.Unmarshal(r.Payload, &s)
		if s.Implementation == candidate.Implementation {
			s.InputHash = c.Hash([]byte("TEST_FIXTURE_LATE_REVISION"))
			rows[i], _ = NewRecord(r.Kind, r.ID, r.Revision, s)
			break
		}
	}
	result, err := Compare(rows, candidate, baseline, contract, time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC), "TEST_FIXTURE_MIXED_INPUT")
	if err != nil || result.Status != "INSUFFICIENT_EVIDENCE" || !c.Contains(result.Reasons, "COMPARISON_INPUT_IDENTITIES_DIFFER_OR_UNPROVEN; MIXED_CHANGE") {
		t.Fatal(result, err)
	}
}

func TestCandidateTimeSplitRejectsFirstSessionIntradayDataAndUnknownVersion(t *testing.T) {
	contract := DefaultContract()
	contract.FixtureOnly = true
	contract.Version = "TEST_FIXTURE"
	contract.MinSamples = 2
	contract.MinDates = 2
	contract.MinBlocks = 2
	calendar := []string{}
	first := time.Date(2025, 12, 1, 0, 0, 0, 0, time.UTC)
	for i := 0; i < 100; i++ {
		calendar = append(calendar, first.AddDate(0, 0, i).Format("2006-01-02"))
	}
	v := Candidate{ID: "TEST_FIXTURE_C", Role: "CANDIDATE", Parent: "TEST_FIXTURE_B", TimeSplitContract: CandidateTimeSplitVersion, Hypothesis: "fixture", Change: "fixture version", Scope: "TEST_FIXTURE", Implementation: c.Hash([]byte("I")), ConfigHash: c.Hash([]byte("C")), DataCutoff: "2026-01-02T10:00:00+08:00", SelectionEnd: "2025-12-01", EvaluationStart: "2026-01-02", EvaluationEnd: "2026-03-01", CalendarInputID: "TEST_FIXTURE_CALENDAR", CalendarHash: c.Hash([]byte("CAL")), ContractVersion: contract.Version, ContractHash: Digest(contract), PrimaryMetric: "PRIMARY_WINDOW_EXCESS_MEDIAN", SecondaryMetrics: []string{"ALL_FIXED_WINDOWS", "NEGATIVE_TAIL", "FIXED_COST_SENSITIVITY"}, FixtureOnly: true, ProductionPromotion: "DISABLED"}
	if err := v.Validate(contract, calendar, time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC)); err == nil || !strings.Contains(err.Error(), "CUTOFF_AFTER_EVALUATION_START") {
		t.Fatal("first evaluation session data leaked into predeclared cutoff", err)
	}
	v.DataCutoff = "2025-12-02T16:00:00+08:00"
	v.TimeSplitContract = ""
	if err := v.Validate(contract, calendar, time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC)); err == nil || !strings.Contains(err.Error(), "TIME_SPLIT_CONTRACT_UNSUPPORTED") {
		t.Fatal("unregistered time contract admitted", err)
	}
}
