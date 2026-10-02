package runtime

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
)

func TestInFlightProgressCancellationPreservesBudgetFailure(t *testing.T) {
	entered, finished := make(chan struct{}), make(chan struct{})
	var work context.Context
	parent := ports.WithProgressReporter(context.Background(), func(string, int) error {
		close(entered)
		<-work.Done()
		return work.Err()
	})
	b, err := NewBudget(parent, 5*time.Second, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	work = b.Ctx()
	go func() { b.Stage("TEST_FIXTURE_PROGRESS_WRITE"); close(finished) }()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("progress callback did not start")
	}
	// Accounting alone reproduces the watchdog's failure-then-cancel order;
	// there is no large allocation, SQL operation or resident-memory pressure.
	if err = b.Output(MaxOutputBytes + 1); err == nil || err.Error() != "OUTPUT_BYTES_BUDGET_EXCEEDED" {
		t.Fatal(err)
	}
	select {
	case <-finished:
	case <-time.After(5 * time.Second):
		t.Fatal("cancellation did not reach progress callback")
	}
	if err = b.Check(); err == nil || err.Error() != "OUTPUT_BYTES_BUDGET_EXCEEDED" || errors.Is(err, context.Canceled) {
		t.Fatalf("original resource cause overwritten: %v", err)
	}
}

func TestFirstProgressErrorRemainsCauseAndCancelsWork(t *testing.T) {
	want := errors.New("TEST_FIXTURE_PROGRESS_WRITE_FAILED")
	calls := 0
	parent := ports.WithProgressReporter(context.Background(), func(string, int) error {
		calls++
		if calls == 1 {
			return want
		}
		return context.Canceled
	})
	b, err := NewBudget(parent, 5*time.Second, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	b.Stage("TEST_FIXTURE_FIRST_FAILURE")
	if !errors.Is(b.Ctx().Err(), context.Canceled) {
		t.Fatal("progress failure did not cancel work")
	}
	b.Processed(1)
	if !errors.Is(b.Check(), want) {
		t.Fatalf("first progress error overwritten: %v", b.Check())
	}
}
