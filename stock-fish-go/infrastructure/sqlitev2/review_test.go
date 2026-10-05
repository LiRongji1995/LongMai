package sqlitev2

import (
	"context"
	"encoding/json"
	review "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application/review"
	"strings"
	"testing"
)

func TestReviewJournalAtomicIdempotentRestartAndReadOnly(t *testing.T) {
	s, _, _ := appFixture(t)
	ctx := context.Background()
	if _, err := s.ReviewRecords(ctx); err == nil {
		t.Fatal("read initialized missing schema")
	}
	if err := s.InitReview(ctx); err != nil {
		t.Fatal(err)
	}
	r, _ := review.NewRecord("CONTRACT", review.DefaultContract().Version, 1, review.DefaultContract())
	if n, err := s.AppendReview(ctx, []review.Record{r}); err != nil || n != 1 {
		t.Fatal(n, err)
	}
	if n, err := s.AppendReview(ctx, []review.Record{r}); err != nil || n != 0 {
		t.Fatal("duplicate appended", n, err)
	}
	conflict := r
	conflict.Payload = json.RawMessage(`{"TEST_FIXTURE":"conflict"}`)
	conflict.Hash = appHash(conflict.Payload)
	if _, err := s.AppendReview(ctx, []review.Record{conflict}); err == nil {
		t.Fatal("idempotency conflict accepted")
	}
	gap, _ := review.NewRecord("TRANSITION", "TEST_FIXTURE_CANDIDATE", 2, map[string]string{"to": "REJECTED"})
	if _, err := s.AppendReview(ctx, []review.Record{gap}); err == nil {
		t.Fatal("revision gap accepted")
	}
	unsupported, _ := review.NewRecord("CANDIDATE_AVAILABILITY", "TEST_FIXTURE_CANDIDATE", 1, map[string]string{"candidate_record_sha256": r.Hash, "available_upper_bound": "2025-12-01T00:00:00Z"})
	if _, err := s.AppendReview(ctx, []review.Record{unsupported}); err == nil || !strings.Contains(err.Error(), "STORE_OBSERVATION_API_REQUIRED") {
		t.Fatal("JOURNAL_V1 unexpectedly accepted an unversioned availability event", err)
	}
	if saved, err := s.ReviewRecords(ctx); err != nil || len(saved) != 1 {
		t.Fatal("rejected availability event changed immutable journal", saved, err)
	}
	if _, err := s.DB().Exec("UPDATE review_records SET raw_hash='bad'"); err == nil {
		t.Fatal("immutable journal changed")
	}
	if _, err := s.DB().Exec("DELETE FROM review_records"); err == nil {
		t.Fatal("immutable journal deleted")
	}
	before, err := s.ReviewRecords(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err = VerifyReviewChange(ctx, s.DB(), reviewLedgerID(before[0]), before[0].Hash); err != nil {
		t.Fatal(err)
	}
	if err = VerifyReviewChange(ctx, s.DB(), reviewLedgerID(before[0]), strings.Repeat("b", 64)); err == nil {
		t.Fatal("recovery content mismatch accepted")
	}
	path := s.Path
	s.Close()
	ro, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer ro.Close()
	after, err := ro.ReviewRecords(ctx)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(before)
	a, _ := json.Marshal(after)
	if string(b) != string(a) {
		t.Fatal("restart changed records")
	}
	if _, err = ro.db.Exec("INSERT INTO review_records VALUES(99,'CONTRACT','BAD',1,'{}','bad')"); err == nil {
		t.Fatal("readonly handle writes")
	}
}

func TestReviewCorruptionAndMembershipDeletionDetected(t *testing.T) {
	for _, damage := range []string{"payload", "delete_last", "schema"} {
		t.Run(damage, func(t *testing.T) {
			s, _, _ := appFixture(t)
			ctx := context.Background()
			if err := s.InitReview(ctx); err != nil {
				t.Fatal(err)
			}
			r, _ := review.NewRecord("CONTRACT", review.DefaultContract().Version, 1, review.DefaultContract())
			if _, err := s.AppendReview(ctx, []review.Record{r}); err != nil {
				t.Fatal(err)
			}
			trigger := "review_records_no_update"
			statement := "UPDATE review_records SET payload='{}'"
			if damage == "delete_last" {
				trigger = "review_records_no_delete"
				statement = "DELETE FROM review_records"
			}
			var sql string
			if err := s.DB().QueryRow("SELECT sql FROM sqlite_master WHERE name=?", trigger).Scan(&sql); err != nil {
				t.Fatal(err)
			}
			if _, err := s.DB().Exec("DROP TRIGGER " + trigger); err != nil {
				t.Fatal(err)
			}
			if damage != "schema" {
				if _, err := s.DB().Exec(statement); err != nil {
					t.Fatal(err)
				}
				if _, err := s.DB().Exec(sql); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := s.ReviewRecords(ctx); err == nil || (!strings.Contains(err.Error(), "CORRUPT") && !strings.Contains(err.Error(), "SCHEMA_OBJECT")) {
				t.Fatal("damage accepted", damage, err)
			}
		})
	}
}
