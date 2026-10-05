package review

import (
	"context"
	"encoding/json"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"time"
)

const StabilityContractVersion = "GO_INDEPENDENT_HOLDOUT_V1"
const PrimaryComparisonPhase = "PRIMARY_COMPARISON"
const IndependentStabilityPhase = "INDEPENDENT_STABILITY"

func (v Candidate) ValidateStabilityWindow(contract Contract, calendar []string) error {
	if v.StabilityContract != StabilityContractVersion || v.StabilityStart == "" || v.StabilityEnd == "" {
		return errors.New("INDEPENDENT_STABILITY_WINDOW_NOT_PREREGISTERED")
	}
	for _, day := range []string{v.StabilityStart, v.StabilityEnd} {
		if _, err := time.Parse("2006-01-02", day); err != nil {
			return errors.New("INDEPENDENT_STABILITY_DATE_INVALID")
		}
	}
	if v.StabilityStart <= v.EvaluationEnd || v.StabilityStart >= v.StabilityEnd {
		return errors.New("INDEPENDENT_STABILITY_OVERLAPS_PRIMARY_WINDOW")
	}
	gap := 0
	found := false
	for i, d := range calendar {
		if _, err := time.Parse("2006-01-02", d); err != nil || (i > 0 && d <= calendar[i-1]) {
			return errors.New("INDEPENDENT_STABILITY_CALENDAR_NOT_SORTED_UNIQUE")
		}
		if d == v.StabilityStart {
			found = true
		}
		if d >= v.EvaluationEnd && d < v.StabilityStart {
			gap++
		}
	}
	if !found || gap < contract.EmbargoSessions {
		return errors.New("INDEPENDENT_STABILITY_CALENDAR_EMBARGO_UNPROVEN")
	}
	return nil
}

func validateStabilityInput(candidate, baseline Candidate, contract Contract, input c.InputRecord) error {
	if input.ID != candidate.CalendarInputID || input.ID != baseline.CalendarInputID || input.RawHash != candidate.CalendarHash || input.RawHash != baseline.CalendarHash || !ev.ValidHash(input.RawHash) || c.Hash(input.Raw) != input.RawHash {
		return errors.New("INDEPENDENT_STABILITY_REGISTERED_CALENDAR_IDENTITY_MISMATCH")
	}
	f, err := ev.DecodeFrozenContext(context.Background(), input.Raw)
	if err != nil {
		return err
	}
	if candidate.FixtureOnly != (f.Input.Coverage == "TEST_FIXTURE") {
		return errors.New("INDEPENDENT_STABILITY_FIXTURE_REAL_CALENDAR_MIX")
	}
	if !candidate.FixtureOnly {
		visible, err := time.Parse(time.RFC3339Nano, f.Cutoff)
		if err != nil {
			return err
		}
		for _, v := range []Candidate{candidate, baseline} {
			cutoff, err := time.Parse(time.RFC3339Nano, v.DataCutoff)
			if err != nil || visible.After(cutoff) {
				return errors.New("INDEPENDENT_STABILITY_CALENDAR_NOT_VISIBLE_BY_DECLARED_CUTOFF")
			}
		}
	}
	if candidate.StabilityContract != baseline.StabilityContract || candidate.StabilityStart != baseline.StabilityStart || candidate.StabilityEnd != baseline.StabilityEnd {
		return errors.New("INDEPENDENT_STABILITY_BASELINE_WINDOW_OR_CONTRACT_MISMATCH")
	}
	if err = candidate.ValidateStabilityWindow(contract, f.Input.Sessions); err != nil {
		return err
	}
	return baseline.ValidateStabilityWindow(contract, f.Input.Sessions)
}

func Stability(records []Record, candidate, baseline Candidate, contract Contract, calendar c.InputRecord, asof time.Time, key string) (Evaluation, error) {
	return comparePhase(records, candidate, baseline, contract, asof, key, IndependentStabilityPhase, &calendar)
}

func evaluationPhase(v Evaluation) string {
	if v.Phase == "" {
		return PrimaryComparisonPhase
	}
	return v.Phase
}

func latestPhaseEvaluation(records []Record, id, phase string) (*Evaluation, error) {
	var latest *Evaluation
	for _, r := range records {
		if r.Kind != "EVALUATION" {
			continue
		}
		var v Evaluation
		if err := json.Unmarshal(r.Payload, &v); err != nil {
			return nil, err
		}
		if v.CandidateID == id && evaluationPhase(v) == phase {
			copy := v
			latest = &copy
		}
	}
	return latest, nil
}

func evaluationRecordHash(records []Record, v Evaluation) (string, error) {
	hash := ""
	for _, r := range records {
		if r.Kind != "EVALUATION" || r.ID != v.ID {
			continue
		}
		var saved Evaluation
		if err := json.Unmarshal(r.Payload, &saved); err != nil {
			return "", err
		}
		if hash != "" || r.Revision != 1 || !ev.ValidHash(r.Hash) || c.Hash(r.Payload) != r.Hash || Digest(saved) != Digest(v) {
			return "", errors.New("SHADOW_EVALUATION_RECORD_BINDING_INVALID")
		}
		hash = r.Hash
	}
	if hash == "" {
		return "", errors.New("SHADOW_EVALUATION_RECORD_NOT_SAVED")
	}
	return hash, nil
}

func shadowAdmission(records []Record, v Candidate, now time.Time) (*Evaluation, *Evaluation, error) {
	if v.Role != "CANDIDATE" || v.Parent == "" {
		return nil, nil, errors.New("SHADOW_CANDIDATE_PARENT_REQUIRED")
	}
	saved, _, err := CandidateDeclarationRecord(records, v.ID)
	if err != nil {
		return nil, nil, err
	}
	if Digest(saved) != Digest(v) {
		return nil, nil, errors.New("SHADOW_IMMUTABLE_PREREGISTRATION_MISMATCH")
	}
	if v.StabilityContract != StabilityContractVersion || v.StabilityStart == "" || v.StabilityEnd == "" {
		return nil, nil, errors.New("INDEPENDENT_STABILITY_WINDOW_NOT_PREREGISTERED")
	}
	primary, err := latestPhaseEvaluation(records, v.ID, PrimaryComparisonPhase)
	if err != nil {
		return nil, nil, err
	}
	stability, err := latestPhaseEvaluation(records, v.ID, IndependentStabilityPhase)
	if err != nil {
		return nil, nil, err
	}
	if primary == nil || stability == nil || primary.Status != "REVIEW_REQUIRED" || stability.Status != "REVIEW_REQUIRED" {
		return primary, stability, errors.New("SHADOW_LATEST_PRIMARY_AND_INDEPENDENT_STABILITY_REQUIRED; OLD_BEST_CANNOT_BE_SELECTED")
	}
	if stability.Attempt <= primary.Attempt {
		return primary, stability, errors.New("SHADOW_STABILITY_MUST_FOLLOW_LATEST_PRIMARY_ATTEMPT")
	}
	for _, e := range []*Evaluation{primary, stability} {
		evaluated, err := time.Parse(time.RFC3339Nano, e.AsOf)
		if err != nil || evaluated.After(now) {
			return primary, stability, errors.New("SHADOW_EVALUATION_TIME_INVALID")
		}
		if e.CandidateID != v.ID || e.BaselineID != v.Parent || e.ContractHash != v.ContractHash || !ev.ValidHash(e.EvaluatorBuild) {
			return primary, stability, errors.New("SHADOW_EVALUATION_IDENTITY_MISMATCH")
		}
		if !v.FixtureOnly {
			_, proof, err := requireCandidateAvailability(records, v, evaluated)
			if err != nil {
				return primary, stability, err
			}
			baseline, err := CandidateFrom(records, v.Parent)
			if err != nil {
				return primary, stability, err
			}
			_, bp, err := requireCandidateAvailability(records, baseline, evaluated)
			if err != nil {
				return primary, stability, err
			}
			if e.CandidateAvailabilityHash != proof.Hash || e.BaselineAvailabilityHash != bp.Hash {
				return primary, stability, errors.New("SHADOW_EVALUATION_PREREGISTRATION_PROOF_MISMATCH")
			}
		}
	}
	pt, _ := time.Parse(time.RFC3339Nano, primary.AsOf)
	st, _ := time.Parse(time.RFC3339Nano, stability.AsOf)
	if st.Before(pt) {
		return primary, stability, errors.New("SHADOW_STABILITY_PRECEDES_LATEST_PRIMARY_AS_OF")
	}
	if primary.WindowStart != v.EvaluationStart || primary.WindowEnd != v.EvaluationEnd || stability.WindowStart != v.StabilityStart || stability.WindowEnd != v.StabilityEnd || stability.StabilityContract != v.StabilityContract {
		return primary, stability, errors.New("SHADOW_FIXED_EVALUATION_WINDOWS_MISMATCH")
	}
	return primary, stability, nil
}
