package cli

import (
	"context"
	"errors"
	review "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application/review"
	"strings"
	"testing"
)

type failedAvailabilityWriter struct{}

func (failedAvailabilityWriter) ObserveCandidateAvailability(context.Context, string) (review.CandidateAvailability, bool, error) {
	return review.CandidateAvailability{}, false, errors.New("TEST_FIXTURE proof write failed")
}

func TestCandidateProposalPartialCommitIsExplicitAndNonSuccess(t *testing.T) {
	v := review.Candidate{ID: "TEST_FIXTURE_PARTIAL_DECLARATION", Role: "BASELINE", FixtureOnly: false, ProductionPromotion: "DISABLED"}
	r, _ := review.NewRecord("CANDIDATE", v.ID, 1, v)
	output, err := completeCandidateProposal(context.Background(), failedAvailabilityWriter{}, []review.Record{r}, v)
	if err == nil || output["declaration_saved"] != true || output["operation_status"] != "PARTIAL_DECLARATION_SAVED" || output["proof_commit_status"] != "FAILED_OR_UNPROVEN" {
		t.Fatal("declaration-only partial result was reported as success", output, err)
	}
	if _, ok := output["preregistration_proof"]; ok {
		t.Fatal("failed proof was presented as committed", output)
	}
}

func TestCandidateProposalReplayPreservesOldClockAndRejectsChanges(t *testing.T) {
	old := review.Candidate{ID: "TEST_FIXTURE_PREREGISTERED", Role: "BASELINE", TimeSplitContract: review.CandidateTimeSplitVersion, Hypothesis: "TEST_FIXTURE immutable hypothesis", CreatedAt: "2025-12-01T00:00:00Z", EvaluationStart: "2026-01-02", EvaluationEnd: "2026-03-01", FixtureOnly: true, ProductionPromotion: "DISABLED"}
	r, err := review.NewRecord("CANDIDATE", old.ID, 1, old)
	if err != nil {
		t.Fatal(err)
	}
	proposal := old
	proposal.CreatedAt = "2026-10-04T00:00:00Z"
	got, found, err := savedCandidateProposal([]review.Record{r}, proposal)
	if err != nil || !found || review.Digest(got) != review.Digest(old) {
		t.Fatal("saved proposal re-registered after evaluation start", got, found, err)
	}
	proposal.Hypothesis = "TEST_FIXTURE changed after results"
	if _, _, err = savedCandidateProposal([]review.Record{r}, proposal); err == nil || !strings.Contains(err.Error(), "IMMUTABLE_PROPOSAL_CONFLICT") {
		t.Fatal("post-result hypothesis change overwrote predeclaration", err)
	}
}

func TestCandidateProposalReplayReportsPublicationAsUnproven(t *testing.T) {
	old := review.Candidate{ID: "TEST_FIXTURE_UNPROVEN_DECLARATION", Role: "BASELINE", CreatedAt: "2025-12-01T00:00:00Z", EvaluationStart: "2026-01-02", FixtureOnly: false, ProductionPromotion: "DISABLED"}
	r, err := review.NewRecord("CANDIDATE", old.ID, 1, old)
	if err != nil {
		t.Fatal(err)
	}
	proposal := old
	proposal.CreatedAt = "2026-10-04T00:00:00Z"
	saved, found, err := savedCandidateProposal([]review.Record{r}, proposal)
	if err != nil || !found || saved.CreatedAt != old.CreatedAt {
		t.Fatal(saved, found, err)
	}
	output := candidateProposalOutput(saved, "PROPOSED")
	if output["preregistration_availability"] != review.ErrPreregistrationPublicationUnproven.Error() {
		t.Fatal("late retry supplied fictional old commit proof", output)
	}
	parent, err := candidateParentDeclaration([]review.Record{r}, old.ID)
	if err != nil || review.Digest(parent) != review.Digest(old) {
		t.Fatal("unproven saved baseline could not bind a child declaration", parent, err)
	}
	if _, err = review.CandidateFrom([]review.Record{r}, old.ID); !errors.Is(err, review.ErrPreregistrationPublicationUnproven) {
		t.Fatal("parent declaration read established publication proof", err)
	}
	wrongRole := old
	wrongRole.Role = "CANDIDATE"
	wrongRecord, _ := review.NewRecord("CANDIDATE", wrongRole.ID, 1, wrongRole)
	if _, err = candidateParentDeclaration([]review.Record{wrongRecord}, wrongRole.ID); err == nil || !strings.Contains(err.Error(), "PARENT_BASELINE_REQUIRED") {
		t.Fatal("non-baseline declaration accepted as parent", err)
	}
	if _, err = candidateParentDeclaration([]review.Record{r}, "TEST_FIXTURE_MISSING_PARENT"); err == nil || !strings.Contains(err.Error(), "NOT_REGISTERED") {
		t.Fatal("missing parent error suppressed", err)
	}
}
