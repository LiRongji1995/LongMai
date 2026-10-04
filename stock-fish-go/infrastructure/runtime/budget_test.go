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

func TestBudgetContextRetainsIdentityValuesDeadlineAndCancellation(t *testing.T) {
	type key struct{}
	parent, cancel := context.WithTimeout(context.WithValue(context.Background(), key{}, "TEST_FIXTURE"), 30*time.Millisecond)
	defer cancel()
	b, err := NewBudget(parent, time.Second, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	if ports.BudgetFromContext(b.Ctx()) != b || b.Ctx().Value(key{}) != "TEST_FIXTURE" {
		t.Fatal("scope or parent value lost")
	}
	want, _ := parent.Deadline()
	got, _ := b.Ctx().Deadline()
	if !got.Equal(want) {
		t.Fatal("parent deadline lost")
	}
	<-b.Ctx().Done()
	if !errors.Is(b.Check(), context.DeadlineExceeded) {
		t.Fatal(b.Check())
	}
	parent2, cancel2 := context.WithCancel(context.Background())
	b2, err := NewBudget(parent2, time.Second, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer b2.Close()
	cancel2()
	if !errors.Is(b2.Check(), context.Canceled) {
		t.Fatal("cancel chain lost")
	}
}

type failingMetricsWriter struct{}

func (failingMetricsWriter) Write([]byte) (int, error) {
	return 0, errors.New("TEST_FIXTURE_LOG_FAILURE")
}
func TestMetricsLogFailureDoesNotChangeResearchBudget(t *testing.T) {
	b, err := NewBudget(context.Background(), 5*time.Second, failingMetricsWriter{})
	if err != nil {
		t.Fatal(err)
	}
	if b.Snapshot().CacheBytes != nil {
		t.Fatal("unmeasured occupancy represented as zero")
	}
	b.Close()
	if b.Snapshot().LogWriteErrors != 1 {
		t.Fatal("final log failure unrecorded")
	}
}
