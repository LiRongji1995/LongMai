package sqlitev2

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

func recoveryRun(id string, n int) c.Run {
	run := c.Run{ID: id, InputID: "TEST_FIXTURE", Strategy: c.R1, CreatedAt: "2026-01-02T08:00:00Z", ImplementationStatus: "TEST_FIXTURE_ONLY", CandidateAvailability: "ABSENT", Manifest: json.RawMessage(`{"purpose":"TEST_FIXTURE_RECOVERY"}`), Summary: json.RawMessage(`{"scope":"TEST_FIXTURE"}`)}
	for i := 0; i < n; i++ {
		raw, _ := json.Marshal(map[string]any{"status": "FAIL", "fixture_ordinal": i, "source": "TEST_FIXTURE"})
		run.Items = append(run.Items, c.Item{ID: fmt.Sprintf("TEST%06d", i), Payload: raw})
	}
	return run
}
func TestAbandonInterruptedRunAfterFiftyOneBatches(t *testing.T) {
	ctx := context.Background()
	s, r, service := appFixture(t)
	run := recoveryRun("TEST_FIXTURE_PARTIAL_51_BATCHES", 2601)
	// SaveRun commits 50 rows per batch. Fail after 51 committed batches so the
	// test exercises real durable partial output, not hand-inserted fake state.
	_, e := s.db.Exec(`CREATE TEMP TRIGGER fixture_save_interrupt BEFORE INSERT ON app_items WHEN NEW.run_id='TEST_FIXTURE_PARTIAL_51_BATCHES' AND NEW.ordinal=2550 BEGIN SELECT RAISE(ABORT,'TEST_FIXTURE_INTERRUPTION');END;`)
	if e != nil {
		t.Fatal(e)
	}
	if e = r.SaveRun(ctx, run); e == nil || !strings.Contains(e.Error(), "TEST_FIXTURE_INTERRUPTION") {
		t.Fatal("write interruption not reached", e)
	}
	var count int
	var state string
	if e = s.db.QueryRow("SELECT count(*) FROM app_items WHERE run_id=?", run.ID).Scan(&count); e != nil || count != 2550 {
		t.Fatal("51 batches not preserved", count, e)
	}
	if e = s.db.QueryRow("SELECT state FROM app_runs WHERE run_id=?", run.ID).Scan(&state); e != nil || state != "PREPARING" {
		t.Fatal(state, e)
	}
	if _, e = r.Run(ctx, run.ID); e == nil {
		t.Fatal("partial result readable as published")
	}
	if e = r.VerifyRun(ctx, run.ID); e == nil || !strings.Contains(e.Error(), "RUN_NOT_PUBLISHED") {
		t.Fatal("public verifier accepted partial", e)
	}
	if _, e = s.db.Exec("DROP TRIGGER fixture_save_interrupt"); e != nil {
		t.Fatal(e)
	}
	abandoned, e := r.AbandonUnpublishedRuns(ctx)
	if e != nil || len(abandoned) != 1 || abandoned[0] != run.ID {
		t.Fatal(abandoned, e)
	}
	if e = r.VerifyAbandonedRun(ctx, run.ID); e != nil {
		t.Fatal(e)
	}
	if ids, e := r.AbandonUnpublishedRuns(ctx); e != nil || len(ids) != 0 {
		t.Fatal("repeat seal was not idempotent", ids, e)
	}
	if e = r.SaveRun(ctx, run); e == nil || !strings.Contains(e.Error(), "ABANDONED_RUN_CANNOT_PUBLISH") {
		t.Fatal("abandoned ID resurrected", e)
	}
	job, _, e := service.Submit(ctx, appRequest("TEST_FIXTURE_NEW_ATTEMPT_AFTER_ABANDON"))
	if e != nil {
		t.Fatal(e)
	}
	job, e = service.Execute(ctx, job.ID)
	if e != nil || job.State != "SUCCEEDED" {
		t.Fatal("new attempt blocked by preserved partial", job, e)
	}
	if e = r.VerifyRun(ctx, job.RunID); e != nil {
		t.Fatal(e)
	}
	if e = r.VerifyAbandonedRun(ctx, run.ID); e != nil {
		t.Fatal("new attempt changed abandoned contents", e)
	}
	if e = s.db.QueryRow("SELECT count(*) FROM app_items WHERE run_id=?", run.ID).Scan(&count); e != nil || count != 2550 {
		t.Fatal("old partial data deleted", count, e)
	}
	var savedProjection []byte
	if e = s.db.QueryRow("SELECT payload FROM app_item_summaries WHERE run_id=? AND ordinal=0", run.ID).Scan(&savedProjection); e != nil {
		t.Fatal(e)
	}
	if _, e = s.db.Exec("DROP TRIGGER app_summaries_no_update"); e != nil {
		t.Fatal(e)
	}
	if _, e = s.db.Exec("UPDATE app_item_summaries SET payload=? WHERE run_id=? AND ordinal=0", []byte(`{"TEST_FIXTURE_CORRUPT":true}`), run.ID); e != nil {
		t.Fatal(e)
	}
	if e = r.VerifyAbandonedRun(ctx, run.ID); e == nil {
		t.Fatal("sealed summary projection corruption accepted")
	}
	if _, e = s.db.Exec("UPDATE app_item_summaries SET payload=? WHERE run_id=? AND ordinal=0", savedProjection, run.ID); e != nil {
		t.Fatal(e)
	}
	if e = r.VerifyAbandonedRun(ctx, run.ID); e != nil {
		t.Fatal("restored exact projection no longer matches original seal", e)
	}
	// Native immutable triggers reject mutation; simulate offline corruption by
	// dropping only that fixture trigger, and require seal verification to fail.
	if _, e = s.db.Exec("UPDATE app_items SET raw_hash='bad' WHERE run_id=?", run.ID); e == nil {
		t.Fatal("immutable payload accepted update")
	}
	if _, e = s.db.Exec("DROP TRIGGER app_items_no_update"); e != nil {
		t.Fatal(e)
	}
	if _, e = s.db.Exec("UPDATE app_items SET payload=X'00' WHERE run_id=? AND ordinal=0", run.ID); e != nil {
		t.Fatal(e)
	}
	if e = r.VerifyAbandonedRun(ctx, run.ID); e == nil {
		t.Fatal("corrupt archived payload accepted")
	}
}
func TestAbandonRequiresIdleJobsAndVerifiesAllHeaderAndSummaryData(t *testing.T) {
	for _, mutation := range []string{"header", "summary", "extra_item"} {
		t.Run(mutation, func(t *testing.T) {
			ctx := context.Background()
			s, r, service := appFixture(t)
			run := recoveryRun("TEST_FIXTURE_PARTIAL", 2)
			if _, e := s.db.Exec(`CREATE TEMP TRIGGER fixture_interrupt BEFORE INSERT ON app_items WHEN NEW.ordinal=1 BEGIN SELECT RAISE(ABORT,'TEST_FIXTURE');END;`); e != nil {
				t.Fatal(e)
			}
			if e := r.SaveRun(ctx, run); e == nil {
				t.Fatal("fixture should interrupt")
			}
			// First batch rolls back completely; header-only interrupted output is also
			// sealable, and orphan/late inserts must change the authenticated inventory.
			job, _, e := service.Submit(ctx, appRequest("TEST_FIXTURE_QUEUED"))
			if e != nil {
				t.Fatal(e)
			}
			if _, e = r.AbandonUnpublishedRuns(ctx); e == nil || !strings.Contains(e.Error(), "ACTIVE_JOBS") {
				t.Fatal("active writer allowed seal", e)
			}
			if _, e = service.Cancel(ctx, job.ID); e != nil {
				t.Fatal(e)
			}
			if _, e = s.db.Exec("DROP TRIGGER fixture_interrupt"); e != nil {
				t.Fatal(e)
			}
			if _, e = r.AbandonUnpublishedRuns(ctx); e != nil {
				t.Fatal(e)
			}
			switch mutation {
			case "header":
				_, e = s.db.Exec("UPDATE app_runs SET candidate_availability='CORRUPT' WHERE run_id=?", run.ID)
			case "summary":
				_, e = s.db.Exec("UPDATE app_runs SET summary=?,summary_hash=? WHERE run_id=?", []byte(`{"changed":true}`), appHash([]byte(`{"changed":true}`)), run.ID)
			case "extra_item":
				packed, _ := appPack(run.Items[0].Payload)
				_, e = s.db.Exec("INSERT INTO app_items VALUES(?,?,?,?,?,?)", run.ID, run.Items[0].ID, 0, packed, appHash(run.Items[0].Payload), len(run.Items[0].Payload))
			}
			if e != nil {
				t.Fatal(e)
			}
			if e = r.VerifyAbandonedRun(ctx, run.ID); e == nil {
				t.Fatal("sealed content changed undetected")
			}
		})
	}
}
func TestVerifyRunRejectsCompletePreparingResult(t *testing.T) {
	ctx := context.Background()
	s, r, _ := appFixture(t)
	run := recoveryRun("TEST_FIXTURE_COMPLETE_NOT_PUBLISHED", 1)
	run.JobID = "NONEXISTENT_TEST_FIXTURE_JOB"
	if e := r.SaveRun(ctx, run); e == nil {
		t.Fatal("missing result job unexpectedly published")
	}
	h := sha256.New()
	for i, item := range run.Items {
		fmt.Fprintf(h, "%d\x00%s\x00%s\n", i, item.ID, appHash(item.Payload))
	}
	if _, e := s.db.Exec("UPDATE app_runs SET members_hash=? WHERE run_id=?", hex.EncodeToString(h.Sum(nil)), run.ID); e != nil {
		t.Fatal(e)
	}
	tx, e := s.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if e != nil {
		t.Fatal(e)
	}
	if e = r.verifyPublishedTx(ctx, tx, run.ID); e != nil {
		tx.Rollback()
		t.Fatal("fixture must have fully valid closure before publication", e)
	}
	tx.Rollback()
	if e = r.VerifyRun(ctx, run.ID); e == nil || !strings.Contains(e.Error(), "RUN_NOT_PUBLISHED") {
		t.Fatal("unpublished valid closure publicly verified", e)
	}
}
