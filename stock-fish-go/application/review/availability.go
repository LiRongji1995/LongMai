package review

import (
	"encoding/json"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"time"
)

const CandidateAvailabilityVersion = "GO_CANDIDATE_AVAILABILITY_V1"
const CandidateAvailabilityKind = "CANDIDATE_AVAILABILITY"
const CandidateAvailabilityBasis = "POST_COMMIT_FRESH_READ_TRANSACTION_FULL_JOURNAL_VERIFIED"

type CandidateAvailability struct {
	Version             string `json:"proof_version"`
	CandidateID         string `json:"candidate_id"`
	DeclarationHash     string `json:"candidate_record_sha256"`
	DeclarationRevision int    `json:"candidate_record_revision"`
	TimeSplitContract   string `json:"time_split_contract"`
	ContractHash        string `json:"evaluation_contract_sha256"`
	FirstSessionOpen    string `json:"first_evaluation_session_open"`
	AvailableUpperBound string `json:"verified_available_upper_bound"`
	Basis               string `json:"observation_basis"`
	Mode                string `json:"evidence_mode"`
}

func CandidateEvaluationOpen(v Candidate) (time.Time, error) {
	start, err := time.Parse(time.RFC3339Nano, v.EvaluationStart+"T09:30:00+08:00")
	return start.UTC(), err
}

// This returns a saved declaration only, without attesting admission or actual
// availability. Original bytes are retained and the immutable revision is one.
func CandidateDeclarationRecord(records []Record, id string) (Candidate, Record, error) {
	for _, r := range records {
		if r.Kind != "CANDIDATE" || r.ID != id || r.Revision != 1 {
			continue
		}
		var v Candidate
		if err := json.Unmarshal(r.Payload, &v); err != nil {
			return v, r, err
		}
		if v.ID != id || !ev.ValidHash(r.Hash) || c.Hash(r.Payload) != r.Hash {
			return v, r, errors.New("CANDIDATE_DECLARATION_IDENTITY_INVALID")
		}
		return v, r, nil
	}
	return Candidate{}, Record{}, errors.New("CANDIDATE_NOT_REGISTERED")
}

// ValidateCandidateAvailability checks content and binding only. A valid late
// observation remains a truthful immutable record, but cannot prove admission.
func ValidateCandidateAvailability(declaration, record Record) (CandidateAvailability, error) {
	var v Candidate
	var p CandidateAvailability
	if err := json.Unmarshal(declaration.Payload, &v); err != nil {
		return p, err
	}
	if err := json.Unmarshal(record.Payload, &p); err != nil {
		return p, err
	}
	if declaration.Kind != "CANDIDATE" || declaration.Revision != 1 || declaration.ID != v.ID || c.Hash(declaration.Payload) != declaration.Hash || !ev.ValidHash(declaration.Hash) || record.Kind != CandidateAvailabilityKind || record.ID != v.ID || record.Revision != 1 || c.Hash(record.Payload) != record.Hash || !ev.ValidHash(record.Hash) {
		return p, errors.New("CANDIDATE_AVAILABILITY_RECORD_IDENTITY_INVALID")
	}
	if v.FixtureOnly || p.Mode != "POST_COMMIT_OBSERVED_REAL_DECLARATION" {
		return p, errors.New("CANDIDATE_AVAILABILITY_FIXTURE_CANNOT_ATTEST_REAL_PUBLICATION")
	}
	for _, hash := range []string{v.Implementation, v.ConfigHash, v.CalendarHash, v.ContractHash, p.DeclarationHash, p.ContractHash} {
		if !ev.ValidHash(hash) {
			return p, errors.New("CANDIDATE_AVAILABILITY_SHA256_IDENTITY_INVALID")
		}
	}
	if p.Version != CandidateAvailabilityVersion || p.CandidateID != v.ID || p.DeclarationRevision != 1 || p.DeclarationHash != declaration.Hash || p.TimeSplitContract != CandidateTimeSplitVersion || v.TimeSplitContract != p.TimeSplitContract || p.ContractHash != v.ContractHash || p.Basis != CandidateAvailabilityBasis {
		return p, errors.New("CANDIDATE_AVAILABILITY_DECLARATION_BINDING_INVALID")
	}
	start, err := CandidateEvaluationOpen(v)
	if err != nil || p.FirstSessionOpen != start.Format(time.RFC3339Nano) {
		return p, errors.New("CANDIDATE_AVAILABILITY_FIXED_SESSION_INVALID")
	}
	created, err := time.Parse(time.RFC3339Nano, v.CreatedAt)
	if err != nil {
		return p, errors.New("CANDIDATE_DECLARATION_CLOCK_INVALID")
	}
	cutoff, err := time.Parse(time.RFC3339Nano, v.DataCutoff)
	if err != nil || !cutoff.Before(start) || created.Before(cutoff) {
		return p, errors.New("CANDIDATE_DECLARATION_DATA_CUTOFF_INVALID")
	}
	observed, err := time.Parse(time.RFC3339Nano, p.AvailableUpperBound)
	if err != nil || observed.Before(created) {
		return p, errors.New("CANDIDATE_AVAILABILITY_OBSERVATION_CLOCK_INVALID")
	}
	return p, nil
}

func CandidateAvailabilityFrom(records []Record, id string) (CandidateAvailability, Record, error) {
	v, declaration, err := CandidateDeclarationRecord(records, id)
	if err != nil {
		return CandidateAvailability{}, Record{}, err
	}
	var proof *Record
	for _, r := range records {
		if r.Kind == CandidateAvailabilityKind && r.ID == id {
			if proof != nil {
				return CandidateAvailability{}, r, errors.New("CANDIDATE_AVAILABILITY_MULTIPLE_IMMUTABLE_PROOFS")
			}
			copy := r
			proof = &copy
		}
	}
	if proof == nil {
		return CandidateAvailability{}, Record{}, ErrPreregistrationPublicationUnproven
	}
	p, err := ValidateCandidateAvailability(declaration, *proof)
	if err != nil {
		return p, *proof, err
	}
	start, _ := CandidateEvaluationOpen(v)
	observed, _ := time.Parse(time.RFC3339Nano, p.AvailableUpperBound)
	if !observed.Before(start) {
		return p, *proof, errors.Join(ErrPreregistrationPublicationUnproven, errors.New("PREREGISTRATION_AVAILABLE_NOT_BEFORE_FIRST_SESSION_OPEN"))
	}
	return p, *proof, nil
}

func requireCandidateAvailability(records []Record, v Candidate, asof time.Time) (CandidateAvailability, Record, error) {
	saved, _, err := CandidateDeclarationRecord(records, v.ID)
	if err != nil {
		return CandidateAvailability{}, Record{}, err
	}
	if Digest(saved) != Digest(v) {
		return CandidateAvailability{}, Record{}, errors.New("CANDIDATE_IMMUTABLE_DECLARATION_MISMATCH")
	}
	p, r, err := CandidateAvailabilityFrom(records, v.ID)
	if err != nil {
		return p, r, err
	}
	observed, _ := time.Parse(time.RFC3339Nano, p.AvailableUpperBound)
	if !asof.IsZero() && asof.Before(observed) {
		return p, r, errors.New("EXPERIMENT_AS_OF_PRECEDES_VERIFIED_PREREGISTRATION_AVAILABILITY")
	}
	return p, r, nil
}
