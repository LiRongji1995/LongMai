package sqlitev2

import (
	"context"
	"errors"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application"
	resources "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"strings"
	"testing"
	"time"
)

type rejectedResourceEngine struct{ testEngine }

func (rejectedResourceEngine) Start(context.Context, int) (context.Context, func(), error) {
	return nil, nil, errors.New("TEST_FIXTURE_RESOURCE_SCOPE_REJECTED")
}

func TestResourceStartFailureReturnsPersistedTerminalState(t *testing.T) {
	_, repo, _ := appFixture(t)
	service := application.New(repo, rejectedResourceEngine{})
	job, _, err := service.Submit(context.Background(), appRequest("TEST_FIXTURE_start_failure"))
	if err != nil {
		t.Fatal(err)
	}
	job, err = service.Execute(context.Background(), job.ID)
	if err == nil || job.State != "FAILED" || job.Error != "TEST_FIXTURE_RESOURCE_SCOPE_REJECTED" {
		t.Fatalf("returned stale running state: %+v %v", job, err)
	}
	saved, err := repo.Job(context.Background(), job.ID)
	if err != nil || saved.State != job.State || saved.Error != job.Error {
		t.Fatalf("returned and saved failure differ: %+v %v", saved, err)
	}
}

type exhaustedOutputEngine struct{ testEngine }

func (exhaustedOutputEngine) Start(ctx context.Context, seconds int) (context.Context, func(), error) {
	b, e := resources.NewBudget(ctx, time.Duration(seconds)*time.Second, nil)
	if e != nil {
		return nil, nil, e
	}
	// Accounting alone triggers cancellation; this allocates no oversized output.
	_ = b.Output(resources.MaxOutputBytes + 1)
	return ports.WithBudget(b.Ctx(), b), b.Close, nil
}
func TestResourceCancellationRemainsFailedWithBudgetCause(t *testing.T) {
	_, repo, _ := appFixture(t)
	service := application.New(repo, exhaustedOutputEngine{})
	job, _, e := service.Submit(context.Background(), appRequest("TEST_FIXTURE_output_resource_failure"))
	if e != nil {
		t.Fatal(e)
	}
	job, e = service.Execute(context.Background(), job.ID)
	if e == nil || job.State != "FAILED" || !strings.Contains(job.Error, "OUTPUT_BYTES_BUDGET_EXCEEDED") {
		t.Fatalf("resource cancellation lost its cause: %+v %v", job, e)
	}
	if job.RunID != "" {
		t.Fatal("resource failure published a result")
	}
}
