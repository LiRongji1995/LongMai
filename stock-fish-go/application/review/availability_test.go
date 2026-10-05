package review

import (
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"strings"
	"testing"
	"time"
)

func proofFixture(t *testing.T, upper string) ([]Record, Candidate, Candidate, Contract) {
	t.Helper()
	_, candidate, baseline, _ := fixtureLab(t)
	contract := DefaultContract()
	rows := []Record{}
	for _, v := range []*Candidate{&candidate, &baseline} {
		v.FixtureOnly = false
		v.ContractHash = Digest(contract)
		v.CalendarHash = c.Hash([]byte("TEST_FIXTURE_CALENDAR"))
		v.CreatedAt = "2025-12-01T01:00:00Z"
		v.DataCutoff = "2025-12-01T00:00:00Z"
		r, err := NewRecord("CANDIDATE", v.ID, 1, *v)
		if err != nil {
			t.Fatal(err)
		}
		rows = append(rows, r)
		start, _ := CandidateEvaluationOpen(*v)
		p := CandidateAvailability{Version: CandidateAvailabilityVersion, CandidateID: v.ID, DeclarationHash: r.Hash, DeclarationRevision: 1, TimeSplitContract: CandidateTimeSplitVersion, ContractHash: v.ContractHash, FirstSessionOpen: start.Format(time.RFC3339Nano), AvailableUpperBound: upper, Basis: CandidateAvailabilityBasis, Mode: "POST_COMMIT_OBSERVED_REAL_DECLARATION"}
		r, err = NewRecord(CandidateAvailabilityKind, v.ID, 1, p)
		if err != nil {
			t.Fatal(err)
		}
		rows = append(rows, r)
	}
	return rows, candidate, baseline, contract
}

func TestPreregistrationProofBoundToDeclarationAndEvaluationAsOf(t *testing.T) {
	rows, candidate, baseline, contract := proofFixture(t, "2025-12-02T00:00:00Z")
	if _, err := CandidateFrom(rows, candidate.ID); err != nil {
		t.Fatal(err)
	}
	result, err := Compare(rows, candidate, baseline, contract, time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC), "TEST_FIXTURE_BOUND_PROOF")
	if err != nil || result.CandidateAvailability == nil || result.CandidateAvailabilityHash != rows[1].Hash || result.BaselineAvailabilityHash != rows[3].Hash {
		t.Fatal("verified publication proof was not consumed", result, err)
	}
	if _, err = Compare(rows, candidate, baseline, contract, time.Date(2025, 12, 1, 12, 0, 0, 0, time.UTC), "TEST_FIXTURE_BEFORE_PROOF"); err == nil || !strings.Contains(err.Error(), "AS_OF_PRECEDES_VERIFIED") {
		t.Fatal("evaluation consumed a future proof", err)
	}
	changed := candidate
	changed.Hypothesis = "TEST_FIXTURE changed after observation"
	if _, err = Compare(rows, changed, baseline, contract, time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC), "TEST_FIXTURE_CHANGED_DECLARATION"); err == nil || !strings.Contains(err.Error(), "IMMUTABLE_DECLARATION_MISMATCH") {
		t.Fatal("proof was rebound to a changed declaration", err)
	}
}

func TestLateAndFixtureProofNeverEstablishRealPreregistration(t *testing.T) {
	rows, candidate, _, _ := proofFixture(t, "2026-01-02T01:30:00Z")
	if _, err := CandidateFrom(rows, candidate.ID); !errors.Is(err, ErrPreregistrationPublicationUnproven) || !strings.Contains(err.Error(), "NOT_BEFORE_FIRST_SESSION") {
		t.Fatal("opening-time observation claimed earlier availability", err)
	}
	_, declaration, err := CandidateDeclarationRecord(rows, candidate.ID)
	if err != nil {
		t.Fatal(err)
	}
	p, _, _ := CandidateAvailabilityFrom(rows, candidate.ID)
	p.Mode = "TEST_FIXTURE"
	proof, _ := NewRecord(CandidateAvailabilityKind, candidate.ID, 1, p)
	if _, err = ValidateCandidateAvailability(declaration, proof); err == nil || !strings.Contains(err.Error(), "FIXTURE_CANNOT_ATTEST_REAL") {
		t.Fatal("fixture proof attested real declaration", err)
	}
	p.Mode = "POST_COMMIT_OBSERVED_REAL_DECLARATION"
	p.DeclarationHash = c.Hash([]byte("TEST_FIXTURE_DIFFERENT_DECLARATION"))
	proof, _ = NewRecord(CandidateAvailabilityKind, candidate.ID, 1, p)
	if _, err = ValidateCandidateAvailability(declaration, proof); err == nil || !strings.Contains(err.Error(), "BINDING_INVALID") {
		t.Fatal("proof did not bind original bytes", err)
	}
}
