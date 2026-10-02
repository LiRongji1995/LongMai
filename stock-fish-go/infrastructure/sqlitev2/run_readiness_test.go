package sqlitev2

import (
	"context"
	"strings"
	"testing"
)

func TestQueuedExecutionRechecksReadiness(t *testing.T) {
	for _, mode := range []string{"migration_unpublished", "new_unprojected_owner"} {
		t.Run(mode, func(t *testing.T) {
			store, repo, service := appFixture(t)
			ctx := context.Background()
			job, _, err := service.Submit(ctx, appRequest("TEST_FIXTURE_queued_gate"))
			if err != nil {
				t.Fatal(err)
			}
			if mode == "migration_unpublished" {
				_, err = store.db.Exec("UPDATE migration_state SET status='MIGRATING' WHERE id=1")
			} else {
				_, err = store.db.Exec("INSERT INTO app_inputs(input_id,raw_hash,payload,observation,cutoff,coverage,created_at) SELECT 'TEST_FIXTURE_UNPROJECTED',raw_hash,payload,observation,cutoff,coverage,created_at FROM app_inputs LIMIT 1")
			}
			if err != nil {
				t.Fatal(err)
			}
			if _, err = store.PublishedResearchIdentity(ctx); err == nil {
				t.Fatal("unready database returned a research identity")
			}
			returned, err := service.Execute(ctx, job.ID)
			if err == nil || !strings.Contains(err.Error(), "DATABASE_NOT_READY") || returned.State != "QUEUED" {
				t.Fatalf("queued request bypassed readiness: %+v %v", returned, err)
			}
			events, err := repo.Events(ctx, job.ID)
			if err != nil || len(events) != 1 || events[0].State != "QUEUED" {
				t.Fatalf("unready request started engine: %+v %v", events, err)
			}
			input, err := repo.Input(ctx, "TEST_FIXTURE")
			if err != nil {
				t.Fatal(err)
			}
			run, err := (testEngine{}).Evaluate(ctx, input, job.Request)
			if err != nil {
				t.Fatal(err)
			}
			run.InputID, run.Strategy = input.ID, job.Request.Strategy
			if err = repo.SaveRun(ctx, run); err == nil {
				t.Fatal("direct result save bypassed readiness")
			}
			var count int
			if err = store.db.QueryRow("SELECT count(*) FROM app_runs").Scan(&count); err != nil || count != 0 {
				t.Fatalf("unready result became visible: %d %v", count, err)
			}
		})
	}
}

func TestResultPublicationRechecksReadinessAfterCommittedItems(t *testing.T) {
	store, repo, _ := appFixture(t)
	ctx := context.Background()
	// Deterministic fault injection revokes authority after the first detail
	// write. The committed evidence must survive, but no formal run may appear.
	if _, err := store.db.Exec(`CREATE TRIGGER TEST_FIXTURE_revoke_authority AFTER INSERT ON app_items BEGIN UPDATE migration_state SET status='MIGRATING' WHERE id=1; END`); err != nil {
		t.Fatal(err)
	}
	input, err := repo.Input(ctx, "TEST_FIXTURE")
	if err != nil {
		t.Fatal(err)
	}
	run, err := (testEngine{}).Evaluate(ctx, input, appRequest("TEST_FIXTURE_revoke"))
	if err != nil {
		t.Fatal(err)
	}
	run.InputID, run.Strategy = input.ID, appRequest("TEST_FIXTURE_revoke").Strategy
	if err = repo.SaveRun(ctx, run); err == nil || !strings.Contains(err.Error(), "MIGRATION_NOT_PUBLISHED") {
		t.Fatalf("publication ignored revoked authority: %v", err)
	}
	var state string
	var count int
	if err = store.db.QueryRow("SELECT state FROM app_runs WHERE run_id=?", run.ID).Scan(&state); err != nil || state != "PREPARING" {
		t.Fatalf("partial run published or discarded: %s %v", state, err)
	}
	if err = store.db.QueryRow("SELECT count(*) FROM app_items WHERE run_id=?", run.ID).Scan(&count); err != nil || count != 1 {
		t.Fatalf("committed detail discarded: %d %v", count, err)
	}
	if err = repo.VerifyRun(ctx, run.ID); err == nil {
		t.Fatal("unpublished result verified as formal")
	}
}
