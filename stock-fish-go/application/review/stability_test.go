package review

import (
	"encoding/json"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"strings"
	"testing"
	"time"
)

func stabilityFixture(t *testing.T) ([]Record, Candidate, Candidate, Contract, c.InputRecord) {
	t.Helper()
	rows, candidate, baseline, contract := fixtureLab(t)
	calendar := fixtureFrozen(t, "2026-01-14", "11")
	f, err := ev.DecodeFrozen(calendar.Raw)
	if err != nil {
		t.Fatal(err)
	}
	f.Input.Sessions = []string{}
	first := time.Date(2025, 12, 1, 0, 0, 0, 0, time.UTC)
	for i := 0; i < 220; i++ {
		f.Input.Sessions = append(f.Input.Sessions, first.AddDate(0, 0, i).Format("2006-01-02"))
	}
	calendar.Raw = fixtureFrozenBytes(t, f)
	calendar.RawHash = c.Hash(calendar.Raw)
	calendar.ID = "frozen_" + calendar.RawHash
	for _, v := range []*Candidate{&candidate, &baseline} {
		v.EvaluationEnd = "2026-03-01"
		v.StabilityContract = StabilityContractVersion
		v.StabilityStart = "2026-03-15"
		v.StabilityEnd = "2026-06-01"
		v.CalendarInputID = calendar.ID
		v.CalendarHash = calendar.RawHash
	}
	for i, r := range rows {
		if r.Kind == "CANDIDATE" {
			v := candidate
			if r.ID == baseline.ID {
				v = baseline
			}
			rows[i], _ = NewRecord(r.Kind, r.ID, r.Revision, v)
		}
	}
	original := append([]Record{}, rows...)
	for _, day := range []string{"2026-03-20", "2026-04-20"} {
		for _, r := range original {
			if r.Kind != "SIGNAL" {
				continue
			}
			var s Signal
			_ = json.Unmarshal(r.Payload, &s)
			if s.Observation != "2026-01-02" {
				continue
			}
			oldID := s.ID
			s.Observation = day
			s.RunID = s.Implementation + day
			s.ID = oldID + "_HOLDOUT_" + day
			s.InputHash = c.Hash([]byte("TEST_FIXTURE_HOLDOUT_INPUT" + day))
			added, _ := NewRecord("SIGNAL", s.ID, 1, s)
			rows = append(rows, added)
			for _, out := range original {
				if out.Kind != "OUTCOME" {
					continue
				}
				var o Outcome
				_ = json.Unmarshal(out.Payload, &o)
				if o.SignalID != oldID {
					continue
				}
				o.SignalID = s.ID
				o.RunID = s.RunID
				o.ID = s.ID + stringInt(o.Window)
				d, _ := time.Parse("2006-01-02", day)
				o.LabelEnd = d.AddDate(0, 0, o.Window).Format("2006-01-02")
				o.LabelCutoff = "2026-07-01T16:00:00+08:00"
				added, _ = NewRecord("OUTCOME", o.ID, 1, o)
				rows = append(rows, added)
			}
		}
	}
	return rows, candidate, baseline, contract, calendar
}

func addEvaluation(t *testing.T, rows []Record, v Evaluation) []Record {
	t.Helper()
	v.EvaluatorBuild = c.Hash([]byte("TEST_FIXTURE_STABILITY_EVALUATOR"))
	r, err := NewRecord("EVALUATION", v.ID, 1, v)
	if err != nil {
		t.Fatal(err)
	}
	return append(rows, r)
}

func TestIndependentHoldoutShadowResearchAdmissionAndLatestFailureRevocation(t *testing.T) {
	rows, candidate, baseline, contract, calendar := stabilityFixture(t)
	now := time.Date(2026, 7, 2, 0, 0, 0, 0, time.UTC)
	primary, err := Compare(rows, candidate, baseline, contract, now, "TEST_FIXTURE_PRIMARY")
	if err != nil || primary.Status != "REVIEW_REQUIRED" {
		t.Fatal(primary, err)
	}
	rows = addEvaluation(t, rows, primary)
	stable, err := Stability(rows, candidate, baseline, contract, calendar, now, "TEST_FIXTURE_STABILITY")
	if err != nil || stable.Status != "REVIEW_REQUIRED" || stable.Phase != IndependentStabilityPhase || stable.WindowStart != candidate.StabilityStart || stable.CandidatePopulation.PrimaryMature != 4 {
		t.Fatal(stable, err)
	}
	rows = addEvaluation(t, rows, stable)
	for index, to := range []string{"OFFLINE_EVALUATED", "REVIEW_REQUIRED", "SHADOW_ELIGIBLE", "SHADOW_OBSERVING"} {
		attempt := primary.ID
		if strings.HasPrefix(to, "SHADOW_") {
			attempt = stable.ID
		}
		tr, err := TransitionCandidate(rows, candidate, to, attempt, "TEST_FIXTURE registered research state", now)
		if err != nil {
			t.Fatal(to, err)
		}
		if strings.HasPrefix(to, "SHADOW_") && (tr.PrimaryEvaluationID != primary.ID || tr.StabilityEvaluationID != stable.ID || !ev.ValidHash(tr.PrimaryEvaluationHash) || !ev.ValidHash(tr.StabilityEvaluationHash) || tr.ResearchStateOnly == "" || tr.ProductionPromotion != "DISABLED") {
			t.Fatal("shadow state lost bound attempts or implied execution", tr)
		}
		r, _ := NewRecord("TRANSITION", candidate.ID, index+1, tr)
		rows = append(rows, r)
	}
	if CandidateState(rows, candidate.ID) != "SHADOW_OBSERVING" {
		t.Fatal("research state not retained")
	}
	failed := primary
	failed.ID = "TEST_FIXTURE_NEW_PRIMARY_FAILED"
	failed.Attempt = 3
	failed.Status = "ENGINEERING_ERROR"
	rows = addEvaluation(t, rows, failed)
	if CandidateState(rows, candidate.ID) != "INSUFFICIENT_EVIDENCE" {
		t.Fatal("new primary failure failed to revoke shadow admission")
	}
	if _, err = TransitionCandidate(rows, candidate, "OFFLINE_EVALUATED", primary.ID, "TEST_FIXTURE old best", now); err == nil || !strings.Contains(err.Error(), "LATEST_EVALUATION_REQUIRED") {
		t.Fatal("old best primary selected", err)
	}
	if _, err = Stability(rows, candidate, baseline, contract, calendar, now.Add(-time.Hour), "TEST_FIXTURE_AS_OF_REWIND"); err == nil || !strings.Contains(err.Error(), "CANNOT_REWIND") {
		t.Fatal("stability rewound evaluation evidence", err)
	}
	if _, err = TransitionCandidate(rows, candidate, "PRODUCTION", stable.ID, "TEST_FIXTURE forbidden production", now); err == nil {
		t.Fatal("research state promoted production")
	}
	// Fresh successful attempts must not reactivate the old observing event.
	freshPrimary, err := Compare(rows, candidate, baseline, contract, now, "TEST_FIXTURE_RECOVERED_PRIMARY")
	if err != nil || freshPrimary.Status != "REVIEW_REQUIRED" {
		t.Fatal(freshPrimary, err)
	}
	rows = addEvaluation(t, rows, freshPrimary)
	freshStable, err := Stability(rows, candidate, baseline, contract, calendar, now, "TEST_FIXTURE_RECOVERED_STABILITY")
	if err != nil || freshStable.Status != "REVIEW_REQUIRED" {
		t.Fatal(freshStable, err)
	}
	rows = addEvaluation(t, rows, freshStable)
	if CandidateState(rows, candidate.ID) != "INSUFFICIENT_EVIDENCE" {
		t.Fatal("new success automatically resurrected a revoked shadow event")
	}
	if _, err = TransitionCandidate(rows, candidate, "SHADOW_ELIGIBLE", freshStable.ID, "TEST_FIXTURE skipped re-entry", now); err == nil || !strings.Contains(err.Error(), "TRANSITION_NOT_ALLOWED") {
		t.Fatal("revoked shadow skipped the explicit full state chain", err)
	}
	for index, to := range []string{"OFFLINE_EVALUATED", "REVIEW_REQUIRED", "SHADOW_ELIGIBLE", "SHADOW_OBSERVING"} {
		attempt := freshPrimary.ID
		if strings.HasPrefix(to, "SHADOW_") {
			attempt = freshStable.ID
		}
		tr, err := TransitionCandidate(rows, candidate, to, attempt, "TEST_FIXTURE explicit research re-entry", now)
		if err != nil {
			t.Fatal(to, err)
		}
		r, _ := NewRecord("TRANSITION", candidate.ID, index+5, tr)
		rows = append(rows, r)
	}
	if CandidateState(rows, candidate.ID) != "SHADOW_OBSERVING" {
		t.Fatal("explicit fresh state chain failed to re-enter research observation")
	}
	transitionCount := 0
	for _, r := range rows {
		if r.Kind == "TRANSITION" && r.ID == candidate.ID {
			transitionCount++
		}
	}
	if transitionCount != 8 {
		t.Fatal("re-entry deleted the original state history", transitionCount)
	}
}

func TestShadowLegacyOrChangedEvaluationBindingsRemainInsufficient(t *testing.T) {
	rows, candidate, baseline, contract, calendar := stabilityFixture(t)
	now := time.Date(2026, 7, 2, 0, 0, 0, 0, time.UTC)
	primary, err := Compare(rows, candidate, baseline, contract, now, "TEST_FIXTURE_BOUND_PRIMARY")
	if err != nil {
		t.Fatal(err)
	}
	rows = addEvaluation(t, rows, primary)
	stable, err := Stability(rows, candidate, baseline, contract, calendar, now, "TEST_FIXTURE_BOUND_HOLDOUT")
	if err != nil {
		t.Fatal(err)
	}
	rows = addEvaluation(t, rows, stable)
	ph, err := evaluationRecordHash(rows, *mustLatestPhase(t, rows, candidate.ID, PrimaryComparisonPhase))
	if err != nil {
		t.Fatal(err)
	}
	sh, err := evaluationRecordHash(rows, *mustLatestPhase(t, rows, candidate.ID, IndependentStabilityPhase))
	if err != nil {
		t.Fatal(err)
	}
	tr := Transition{CandidateID: candidate.ID, To: "SHADOW_OBSERVING", PrimaryEvaluationID: primary.ID, StabilityEvaluationID: stable.ID, PrimaryEvaluationHash: ph, StabilityEvaluationHash: sh}
	for _, damage := range []string{"legacy_unbound", "wrong_record_hash", "changed_evaluation_bytes"} {
		t.Run(damage, func(t *testing.T) {
			copyRows := append([]Record{}, rows...)
			event := tr
			if damage == "legacy_unbound" {
				event.PrimaryEvaluationHash, event.StabilityEvaluationHash = "", ""
			} else if damage == "wrong_record_hash" {
				event.StabilityEvaluationHash = c.Hash([]byte("TEST_FIXTURE_OTHER_RECORD"))
			} else {
				for i, r := range copyRows {
					if r.Kind == "EVALUATION" && r.ID == stable.ID {
						var e Evaluation
						_ = json.Unmarshal(r.Payload, &e)
						e.Reasons = append(e.Reasons, "TEST_FIXTURE changed bytes after binding")
						copyRows[i], _ = NewRecord(r.Kind, r.ID, r.Revision, e)
					}
				}
			}
			r, _ := NewRecord("TRANSITION", candidate.ID, 1, event)
			copyRows = append(copyRows, r)
			if CandidateState(copyRows, candidate.ID) != "INSUFFICIENT_EVIDENCE" {
				t.Fatal("unbound or changed shadow evaluation was admitted", damage)
			}
		})
	}
}

func mustLatestPhase(t *testing.T, rows []Record, id, phase string) *Evaluation {
	t.Helper()
	v, err := latestPhaseEvaluation(rows, id, phase)
	if err != nil || v == nil {
		t.Fatal(v, err)
	}
	return v
}

func TestIndependentHoldoutCannotReusePrimaryOrMutateDeclaredWindow(t *testing.T) {
	rows, candidate, baseline, contract, calendar := stabilityFixture(t)
	now := time.Date(2026, 7, 2, 0, 0, 0, 0, time.UTC)
	onlyPrimary := []Record{}
	for _, r := range rows {
		if r.Kind == "SIGNAL" {
			var s Signal
			_ = json.Unmarshal(r.Payload, &s)
			if s.Observation >= candidate.StabilityStart {
				continue
			}
		}
		if r.Kind == "OUTCOME" {
			var o Outcome
			_ = json.Unmarshal(r.Payload, &o)
			if strings.Contains(o.SignalID, "_HOLDOUT_") {
				continue
			}
		}
		onlyPrimary = append(onlyPrimary, r)
	}
	stable, err := Stability(onlyPrimary, candidate, baseline, contract, calendar, now, "TEST_FIXTURE_NO_HOLDOUT")
	if err != nil || stable.Status != "INSUFFICIENT_EVIDENCE" || stable.CandidatePopulation.Registered != 0 {
		t.Fatal("primary samples leaked into holdout", stable, err)
	}
	changed := candidate
	changed.StabilityStart = "2026-01-02"
	if _, err = Stability(rows, changed, baseline, contract, calendar, now, "TEST_FIXTURE_REDEFINED_HOLDOUT"); err == nil || !strings.Contains(err.Error(), "IMMUTABLE_DECLARATION_MISMATCH") {
		t.Fatal("holdout was changed after declaration", err)
	}
	f, err := ev.DecodeFrozen(calendar.Raw)
	if err != nil {
		t.Fatal(err)
	}
	changed.StabilityStart = "2026-03-05"
	if err = changed.ValidateStabilityWindow(contract, f.Input.Sessions); err == nil || !strings.Contains(err.Error(), "EMBARGO_UNPROVEN") {
		t.Fatal("insufficient purge/embargo was accepted", err)
	}
	legacy := candidate
	legacy.StabilityContract = ""
	legacy.StabilityStart = ""
	legacy.StabilityEnd = ""
	if err = legacy.ValidateStabilityWindow(contract, f.Input.Sessions); err == nil || !strings.Contains(err.Error(), "NOT_PREREGISTERED") {
		t.Fatal("old declaration gained implicit holdout", err)
	}
}

func TestIndependentHoldoutFutureMissingAndMixedInputsStayInsufficient(t *testing.T) {
	rows, candidate, baseline, contract, calendar := stabilityFixture(t)
	future, err := Stability(rows, candidate, baseline, contract, calendar, time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC), "TEST_FIXTURE_FUTURE_LABELS")
	if err != nil || future.Status != "INSUFFICIENT_EVIDENCE" || future.CandidatePopulation.PrimaryMature != 0 {
		t.Fatal("future outcome labels consumed", future, err)
	}
	missing := []Record{}
	removed := false
	for _, r := range rows {
		if r.Kind == "OUTCOME" && !removed {
			var o Outcome
			_ = json.Unmarshal(r.Payload, &o)
			if strings.Contains(o.SignalID, "_HOLDOUT_") && strings.HasPrefix(o.SignalID, candidate.ID) && o.Window == 5 {
				removed = true
				continue
			}
		}
		missing = append(missing, r)
	}
	result, err := Stability(missing, candidate, baseline, contract, calendar, time.Date(2026, 7, 2, 0, 0, 0, 0, time.UTC), "TEST_FIXTURE_MISSING_HOLDOUT")
	if err != nil || result.Status != "INSUFFICIENT_EVIDENCE" || result.CandidatePopulation.PrimaryMissing != 1 {
		t.Fatal("missing holdout price became zero or disappeared", result, err)
	}
	for i, r := range rows {
		if r.Kind != "SIGNAL" {
			continue
		}
		var s Signal
		_ = json.Unmarshal(r.Payload, &s)
		if s.Observation >= candidate.StabilityStart && s.Implementation == candidate.Implementation {
			s.InputHash = c.Hash([]byte("TEST_FIXTURE_MIXED_HOLDOUT_INPUT"))
			rows[i], _ = NewRecord(r.Kind, r.ID, r.Revision, s)
			break
		}
	}
	result, err = Stability(rows, candidate, baseline, contract, calendar, time.Date(2026, 7, 2, 0, 0, 0, 0, time.UTC), "TEST_FIXTURE_MIXED_HOLDOUT")
	if err != nil || result.Status != "INSUFFICIENT_EVIDENCE" || !c.Contains(result.Reasons, "COMPARISON_INPUT_IDENTITIES_DIFFER_OR_UNPROVEN; MIXED_CHANGE") {
		t.Fatal("different evidence entered a paired holdout comparison", result, err)
	}
}

func TestShadowCannotChooseOlderHoldoutAfterNewFailureOrChangedBaseline(t *testing.T) {
	rows, candidate, baseline, contract, calendar := stabilityFixture(t)
	now := time.Date(2026, 7, 2, 0, 0, 0, 0, time.UTC)
	primary, err := Compare(rows, candidate, baseline, contract, now, "TEST_FIXTURE_PRIMARY_SHADOW")
	if err != nil {
		t.Fatal(err)
	}
	rows = addEvaluation(t, rows, primary)
	stable, err := Stability(rows, candidate, baseline, contract, calendar, now, "TEST_FIXTURE_OLD_HOLDOUT_BEST")
	if err != nil {
		t.Fatal(err)
	}
	rows = addEvaluation(t, rows, stable)
	failed := stable
	failed.ID = "TEST_FIXTURE_LATEST_HOLDOUT_FAILED"
	failed.Attempt = 3
	failed.Status = "ENGINEERING_ERROR"
	rows = addEvaluation(t, rows, failed)
	if _, _, err = shadowAdmission(rows, candidate, now); err == nil || !strings.Contains(err.Error(), "LATEST_PRIMARY_AND_INDEPENDENT_STABILITY_REQUIRED") {
		t.Fatal("historical best holdout replaced latest failure", err)
	}
	changed := baseline
	changed.StabilityEnd = "2026-05-01"
	for i, r := range rows {
		if r.Kind == "CANDIDATE" && r.ID == baseline.ID {
			rows[i], _ = NewRecord("CANDIDATE", r.ID, 1, changed)
		}
	}
	if _, err = Stability(rows, candidate, changed, contract, calendar, now, "TEST_FIXTURE_BASELINE_HOLDOUT_CHANGED"); err == nil || !strings.Contains(err.Error(), "BASELINE_WINDOW_OR_CONTRACT_MISMATCH") {
		t.Fatal("baseline used a different holdout window", err)
	}
	duplicate := []string{"2026-03-01", "2026-03-01", "2026-03-15"}
	if err = candidate.ValidateStabilityWindow(contract, duplicate); err == nil || !strings.Contains(err.Error(), "NOT_SORTED_UNIQUE") {
		t.Fatal("duplicate calendar inflated embargo", err)
	}
}
