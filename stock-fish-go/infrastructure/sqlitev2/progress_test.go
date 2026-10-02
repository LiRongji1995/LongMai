package sqlitev2

import (
	"context"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	resources "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"strings"
	"testing"
	"time"
)

type failingProgressEngine struct{ testEngine }

func (failingProgressEngine) Start(ctx context.Context, seconds int) (context.Context, func(), error) {
	b, e := resources.NewBudget(ctx, time.Duration(seconds)*time.Second, nil)
	if e != nil {
		return nil, nil, e
	}
	return ports.WithBudget(b.Ctx(), b), b.Close, nil
}
func (failingProgressEngine) Evaluate(ctx context.Context, in c.InputRecord, request c.ResearchRequest) (c.Run, error) {
	b := ports.BudgetFromContext(ctx)
	b.Stage("TEST_FIXTURE_HISTORY")
	b.Processed(100)
	b.Processed(101)
	return c.Run{}, context.DeadlineExceeded
}
func TestApplicationPersistsProgressAndFailureKeepsIt(t *testing.T) {
	_, repo, _ := appFixture(t)
	ctx := context.Background()
	service := application.New(repo, failingProgressEngine{})
	j, _, e := service.Submit(ctx, appRequest("TEST_FIXTURE_progress"))
	if e != nil {
		t.Fatal(e)
	}
	j, e = service.Execute(ctx, j.ID)
	if e == nil || j.State != "FAILED" || j.Progress != 100 {
		t.Fatalf("progress lost at failure: %+v %v", j, e)
	}
	events, e := repo.Events(ctx, j.ID)
	if e != nil {
		t.Fatal(e)
	}
	found := false
	for _, v := range events {
		if v.State == "RUNNING" && strings.Contains(v.Reason, `"processed_instruments":100`) {
			found = true
		}
	}
	if !found {
		t.Fatal("durable progress event missing")
	}
	count := len(events)
	if e = repo.ReportProgress(ctx, j.ID, "late", 200); e != nil {
		t.Fatal(e)
	}
	events, _ = repo.Events(ctx, j.ID)
	after, _ := repo.Job(ctx, j.ID)
	if len(events) != count || after.Progress != 100 || after.State != "FAILED" {
		t.Fatal("late progress revived terminal job")
	}
}
func TestProgressRejectsRegressionAndHasAtomicEvent(t *testing.T) {
	_, repo, service := appFixture(t)
	ctx := context.Background()
	j, _, e := service.Submit(ctx, appRequest("TEST_FIXTURE_progress_order"))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = repo.Transition(ctx, j.ID, "RUNNING", "start", "", 0); e != nil {
		t.Fatal(e)
	}
	if e = repo.ReportProgress(ctx, j.ID, "history", 10); e != nil {
		t.Fatal(e)
	}
	before, _ := repo.Events(ctx, j.ID)
	if e = repo.ReportProgress(ctx, j.ID, "history", 9); e == nil {
		t.Fatal("regression accepted")
	}
	after, _ := repo.Events(ctx, j.ID)
	job, _ := repo.Job(ctx, j.ID)
	if job.Progress != 10 || len(after) != len(before) {
		t.Fatal("failed update changed durable state")
	}
}
