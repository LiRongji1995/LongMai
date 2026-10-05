package sqlitev2

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	review "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application/review"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"strings"
	"testing"
	"time"
)

func TestReviewSchemaProofAndLedgerUseOneReadSnapshot(t *testing.T) {
	for _, trigger := range []string{"review_records_no_update", "review_availability_records_no_update"} {
		t.Run(trigger, func(t *testing.T) {
			s, _, _ := appFixture(t)
			ctx := context.Background()
			if err := s.InitReview(ctx); err != nil {
				t.Fatal(err)
			}
			declaration := availabilityDeclaration(t, s, "TEST_FIXTURE_SNAPSHOT", false)
			if _, _, err := s.ObserveCandidateAvailability(ctx, declaration.ID); err != nil {
				t.Fatal(err)
			}
			before, err := s.ReviewRecords(ctx)
			if err != nil {
				t.Fatal(err)
			}
			tx, err := s.DB().BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
			if err != nil {
				t.Fatal(err)
			}
			defer tx.Rollback()
			// Pin schema and records before an independent WAL writer commits.
			if err = verifyReviewSchema(ctx, tx); err != nil {
				t.Fatal(err)
			}
			writer, err := sql.Open("sqlite", s.Path)
			if err != nil {
				t.Fatal(err)
			}
			defer writer.Close()
			writer.SetMaxOpenConns(1)
			writeTx, err := writer.BeginTx(ctx, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer writeTx.Rollback()
			if _, err = writeTx.ExecContext(ctx, "DROP TRIGGER "+trigger); err != nil {
				t.Fatal(err)
			}
			if _, err = writeTx.ExecContext(ctx, "INSERT INTO app_change_ledger(kind,entity_id,content_hash,event_at) VALUES('REVIEW_RECORD','TEST_FIXTURE_EXTRA_SNAPSHOT_MEMBER',?,?)", c.Hash([]byte("TEST_FIXTURE_EXTRA")), time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
				t.Fatal(err)
			}
			if err = writeTx.Commit(); err != nil {
				t.Fatal(err)
			}
			inside, err := readReviewTx(ctx, tx)
			if err != nil || review.Digest(before) != review.Digest(inside) {
				t.Fatal("mixed schema/content/ledger snapshots", inside, err)
			}
			if err = tx.Commit(); err != nil {
				t.Fatal(err)
			}
			classification := "REVIEW_SCHEMA_OBJECT_MISSING"
			if trigger == "review_availability_records_no_update" {
				classification = "REVIEW_AVAILABILITY_SCHEMA_PARTIAL_OR_EXTRA"
			}
			if _, err = s.ReviewRecords(ctx); err == nil || !strings.Contains(err.Error(), classification) {
				t.Fatal("fresh snapshot accepted the committed schema damage", err)
			}
		})
	}
}

func availabilityDeclaration(t *testing.T, s *Store, id string, late bool) review.Record {
	t.Helper()
	now := time.Now().UTC()
	start := now.AddDate(0, 0, 30)
	if late {
		start = now.AddDate(0, 0, -30)
	}
	v := review.Candidate{ID: id, Role: "BASELINE", Hypothesis: "TEST_FIXTURE declaration proof", Change: "TEST_FIXTURE version", Scope: "TEST_FIXTURE", Implementation: c.Hash([]byte("TEST_FIXTURE_IMPLEMENTATION")), ConfigHash: c.Hash([]byte("TEST_FIXTURE_CONFIG")), CalendarInputID: "TEST_FIXTURE_CALENDAR", CalendarHash: c.Hash([]byte("TEST_FIXTURE_CALENDAR")), ContractVersion: review.DefaultContract().Version, ContractHash: review.Digest(review.DefaultContract()), TimeSplitContract: review.CandidateTimeSplitVersion, DataCutoff: now.AddDate(0, 0, -60).Format(time.RFC3339Nano), CreatedAt: now.Add(-time.Second).Format(time.RFC3339Nano), EvaluationStart: start.Format("2006-01-02"), EvaluationEnd: start.AddDate(0, 0, 30).Format("2006-01-02"), FixtureOnly: false, ProductionPromotion: "DISABLED"}
	r, err := review.NewRecord("CANDIDATE", id, 1, v)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.AppendReview(context.Background(), []review.Record{r}); err != nil {
		t.Fatal(err)
	}
	return r
}

func TestCandidateAvailabilityPostCommitReadIdempotenceAndRestart(t *testing.T) {
	s, _, _ := appFixture(t)
	ctx := context.Background()
	if err := s.InitReview(ctx); err != nil {
		t.Fatal(err)
	}
	declaration := availabilityDeclaration(t, s, "TEST_FIXTURE_AVAILABLE", false)
	before := time.Now().UTC()
	p, created, err := s.ObserveCandidateAvailability(ctx, declaration.ID)
	after := time.Now().UTC()
	if err != nil || !created {
		t.Fatal(p, created, err)
	}
	observed, err := time.Parse(time.RFC3339Nano, p.AvailableUpperBound)
	if err != nil || observed.Before(before) || observed.After(after) || p.DeclarationHash != declaration.Hash {
		t.Fatal("proof was not generated after committed declaration read", p, before, after, err)
	}
	if again, created, err := s.ObserveCandidateAvailability(ctx, declaration.ID); err != nil || created || review.Digest(again) != review.Digest(p) {
		t.Fatal("retry rewrote immutable observation", again, created, err)
	}
	rows, err := s.ReviewRecords(ctx)
	if err != nil || len(rows) != 2 {
		t.Fatal(rows, err)
	}
	if _, err = review.CandidateFrom(rows, declaration.ID); err != nil {
		t.Fatal(err)
	}
	proof := rows[1]
	if err = VerifyReviewChange(ctx, s.DB(), reviewLedgerID(proof), proof.Hash); err != nil {
		t.Fatal("recovery/export did not close proof ledger", err)
	}
	path := s.Path
	s.Close()
	ro, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer ro.Close()
	reopened, err := ro.ReviewRecords(ctx)
	if err != nil || review.Digest(rows) != review.Digest(reopened) {
		t.Fatal("restart changed proof or declaration", reopened, err)
	}
}

func TestCandidateAvailabilityFailedProofKeepsDeclarationAndCanRetry(t *testing.T) {
	s, _, _ := appFixture(t)
	ctx := context.Background()
	if err := s.InitReview(ctx); err != nil {
		t.Fatal(err)
	}
	declaration := availabilityDeclaration(t, s, "TEST_FIXTURE_FAILED_PROOF", false)
	if _, err := s.DB().Exec(`CREATE TEMP TRIGGER TEST_FIXTURE_fail_availability BEFORE INSERT ON review_availability_records BEGIN SELECT RAISE(ABORT,'TEST_FIXTURE proof failure'); END`); err != nil {
		t.Fatal(err)
	}
	if _, created, err := s.ObserveCandidateAvailability(ctx, declaration.ID); err == nil || created {
		t.Fatal("failed proof was reported committed", created, err)
	}
	rows, err := s.ReviewRecords(ctx)
	if err != nil || len(rows) != 1 || rows[0].Hash != declaration.Hash {
		t.Fatal("proof rollback changed saved declaration", rows, err)
	}
	if _, err = review.CandidateFrom(rows, declaration.ID); !errors.Is(err, review.ErrPreregistrationPublicationUnproven) {
		t.Fatal("partial declaration became registered", err)
	}
	if _, err = s.DB().Exec("DROP TRIGGER TEST_FIXTURE_fail_availability"); err != nil {
		t.Fatal(err)
	}
	if _, created, err := s.ObserveCandidateAvailability(ctx, declaration.ID); err != nil || !created {
		t.Fatal("missing proof could not be retried", created, err)
	}
	late := availabilityDeclaration(t, s, "TEST_FIXTURE_LATE_RETRY", true)
	if _, created, err := s.ObserveCandidateAvailability(ctx, late.ID); err != nil || !created {
		t.Fatal(created, err)
	}
	rows, err = s.ReviewRecords(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = review.CandidateFrom(rows, late.ID); !errors.Is(err, review.ErrPreregistrationPublicationUnproven) {
		t.Fatal("late observation backfilled pre-opening publication", err)
	}
}

func TestReviewAvailabilityExplicitUpgradePreservesLegacyReadOnlyBytes(t *testing.T) {
	s, _, _ := appFixture(t)
	ctx := context.Background()
	if _, err := s.DB().Exec(reviewDDL); err != nil {
		t.Fatal(err)
	}
	if _, err := s.DB().Exec("INSERT INTO review_schema VALUES(?,?)", review.JournalVersion, c.Hash([]byte(reviewDDL))); err != nil {
		t.Fatal(err)
	}
	r := availabilityDeclaration(t, s, "TEST_FIXTURE_LEGACY_DECLARATION", false)
	old, err := s.ReviewRecords(ctx)
	if err != nil {
		t.Fatal(err)
	}
	ro, err := OpenReadOnly(ctx, s.Path)
	if err != nil {
		t.Fatal(err)
	}
	read, err := ro.ReviewRecords(ctx)
	ro.Close()
	if err != nil || review.Digest(old) != review.Digest(read) {
		t.Fatal("legacy query initialized or changed schema", read, err)
	}
	if _, _, err = s.ObserveCandidateAvailability(ctx, r.ID); err == nil || !strings.Contains(err.Error(), "EXPLICIT_REVIEW_INIT_REQUIRED") {
		t.Fatal("ordinary observation initialized extension", err)
	}
	if err = s.InitReview(ctx); err != nil {
		t.Fatal(err)
	}
	upgraded, err := s.ReviewRecords(ctx)
	if err != nil || review.Digest(old) != review.Digest(upgraded) {
		t.Fatal("upgrade changed legacy record bytes", upgraded, err)
	}
	var hash string
	if err = s.DB().QueryRow("SELECT checksum FROM review_schema WHERE version=?", review.JournalVersion).Scan(&hash); err != nil || hash != c.Hash([]byte(reviewDDL)) {
		t.Fatal("JOURNAL_V1 checksum changed", hash, err)
	}
	if _, err = review.CandidateFrom(upgraded, r.ID); !errors.Is(err, review.ErrPreregistrationPublicationUnproven) {
		t.Fatal("schema upgrade invented old publication", err)
	}
}

func TestReviewAvailabilitySchemaMembersHashesAndLedgerClosed(t *testing.T) {
	for _, damage := range []string{"partial_schema", "extra_schema", "payload", "hash", "column_binding", "delete_proof", "extra_ledger", "delete_ledger"} {
		t.Run(damage, func(t *testing.T) {
			s, _, _ := appFixture(t)
			ctx := context.Background()
			if err := s.InitReview(ctx); err != nil {
				t.Fatal(err)
			}
			r := availabilityDeclaration(t, s, "TEST_FIXTURE_CORRUPTION", false)
			if _, _, err := s.ObserveCandidateAvailability(ctx, r.ID); err != nil {
				t.Fatal(err)
			}
			if damage == "partial_schema" {
				if _, err := s.DB().Exec("DROP TABLE review_availability_schema"); err != nil {
					t.Fatal(err)
				}
			} else if damage == "extra_schema" {
				if _, err := s.DB().Exec("CREATE TABLE review_availability_extra(TEST_FIXTURE TEXT)"); err != nil {
					t.Fatal(err)
				}
			} else if damage == "extra_ledger" {
				if _, err := s.DB().Exec("INSERT INTO app_change_ledger(kind,entity_id,content_hash,event_at) VALUES('REVIEW_RECORD','CANDIDATE_AVAILABILITY:TEST_FIXTURE_EXTRA:1',?,?)", c.Hash([]byte("TEST_FIXTURE_EXTRA")), time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
					t.Fatal(err)
				}
			} else {
				trigger, statement := "review_availability_records_no_update", "UPDATE review_availability_records SET payload='{}'"
				switch damage {
				case "hash":
					statement = "UPDATE review_availability_records SET raw_hash='" + strings.Repeat("b", 64) + "'"
				case "column_binding":
					statement = "UPDATE review_availability_records SET candidate_record_hash='" + strings.Repeat("c", 64) + "'"
				case "delete_proof":
					trigger = "review_availability_records_no_delete"
					statement = "DELETE FROM review_availability_records"
				case "delete_ledger":
					trigger = "app_ledger_no_delete"
					statement = "DELETE FROM app_change_ledger WHERE kind='REVIEW_RECORD' AND entity_id LIKE 'CANDIDATE_AVAILABILITY:%'"
				}
				var sql string
				if err := s.DB().QueryRow("SELECT sql FROM sqlite_master WHERE name=?", trigger).Scan(&sql); err != nil {
					t.Fatal(err)
				}
				if _, err := s.DB().Exec("DROP TRIGGER " + trigger); err != nil {
					t.Fatal(err)
				}
				if _, err := s.DB().Exec(statement); err != nil {
					t.Fatal(err)
				}
				if _, err := s.DB().Exec(sql); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := s.ReviewRecords(ctx); err == nil {
				t.Fatal("damaged proof closure accepted", damage)
			} else if (damage == "payload" || damage == "hash") && !strings.Contains(err.Error(), "CONTENT_CORRUPT") {
				t.Fatal("hash damage lost explicit integrity classification", damage, err)
			}
		})
	}
}

func TestReviewAvailabilityExtraMemberWithoutLedgerDetected(t *testing.T) {
	s, _, _ := appFixture(t)
	ctx := context.Background()
	if err := s.InitReview(ctx); err != nil {
		t.Fatal(err)
	}
	r := availabilityDeclaration(t, s, "TEST_FIXTURE_ORIGINAL", false)
	p, _, err := s.ObserveCandidateAvailability(ctx, r.ID)
	if err != nil {
		t.Fatal(err)
	}
	extra := availabilityDeclaration(t, s, "TEST_FIXTURE_EXTRA_MEMBER", false)
	p.CandidateID = extra.ID
	p.DeclarationHash = extra.Hash
	p.AvailableUpperBound = time.Now().UTC().Format(time.RFC3339Nano)
	v, _, err := review.CandidateDeclarationRecord([]review.Record{extra}, extra.ID)
	if err != nil {
		t.Fatal(err)
	}
	start, err := review.CandidateEvaluationOpen(v)
	if err != nil {
		t.Fatal(err)
	}
	p.FirstSessionOpen = start.Format(time.RFC3339Nano)
	raw, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.DB().Exec("INSERT INTO review_availability_records(candidate_id,candidate_record_hash,payload,raw_hash) VALUES(?,?,?,?)", extra.ID, extra.Hash, raw, c.Hash(raw)); err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReviewRecords(ctx); err == nil || !strings.Contains(err.Error(), "MEMBERSHIP_CORRUPT") {
		t.Fatal("extra member without ledger accepted", err)
	}
}
