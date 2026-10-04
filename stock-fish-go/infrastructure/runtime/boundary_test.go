package runtime

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"os/exec"
	"sync"
	"testing"
	"time"
)

type fixtureClock struct {
	mu   sync.Mutex
	wall time.Time
	mono time.Duration
}

func (c *fixtureClock) Wall() time.Time     { c.mu.Lock(); defer c.mu.Unlock(); return c.wall }
func (c *fixtureClock) Mono() time.Duration { c.mu.Lock(); defer c.mu.Unlock(); return c.mono }
func (c *fixtureClock) advance(w, m time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.wall = c.wall.Add(w)
	c.mono += m
}
func TestClockForwardBackwardAndWaitExpiry(t *testing.T) {
	for _, test := range []struct {
		name       string
		wall, mono time.Duration
		want       string
	}{{"forward", time.Hour, time.Second, "CLOCK_OR_SUSPEND_ANOMALY"}, {"backward", -time.Hour, time.Second, "CLOCK_OR_SUSPEND_ANOMALY"}, {"wait_expired", 6 * time.Second, 6 * time.Second, "WAIT_BUDGET_EXCEEDED"}} {
		t.Run(test.name, func(t *testing.T) {
			clock := &fixtureClock{wall: time.Now()}
			b, e := NewBudgetWithClock(context.Background(), 5*time.Second, nil, clock)
			if e != nil {
				t.Fatal(e)
			}
			defer b.Close()
			clock.advance(test.wall, test.mono)
			if e = b.Check(); e == nil || e.Error() != test.want {
				t.Fatal(e)
			}
			if b.Ctx().Err() == nil {
				t.Fatal("expired scope still usable")
			}
			m := b.Snapshot()
			if m.WaitElapsed != test.wall.Seconds() || m.Elapsed != test.mono.Seconds() {
				t.Fatal(m)
			}
		})
	}
}
func TestOwnedPipeBackpressureAndRecovery(t *testing.T) {
	for _, recover := range []bool{false, true} {
		t.Run(map[bool]string{false: "blocked", true: "recovered"}[recover], func(t *testing.T) {
			reader, writer := io.Pipe()
			defer reader.Close()
			sink := NewOwnedPipe(writer)
			b, e := NewBudget(context.Background(), 1100*time.Millisecond, sink)
			if e != nil {
				t.Fatal(e)
			}
			if recover {
				go func() { time.Sleep(1050 * time.Millisecond); io.Copy(io.Discard, reader) }()
			}
			<-b.Ctx().Done()
			start := time.Now()
			if b.Check() == nil {
				t.Fatal("deadline ignored")
			}
			_ = b.Snapshot()
			b.Close()
			if time.Since(start) > time.Second {
				t.Fatal("budget cleanup blocked")
			}
			m := b.Snapshot()
			if recover {
				if !m.Final || m.FinalLogStatus != "DELIVERED" {
					t.Fatal(m)
				}
			} else {
				if m.Final || m.FinalLogStatus != "NOT_DELIVERED" || m.LogWriteErrors == 0 {
					t.Fatal(m)
				}
			}
		})
	}
}

type shortSink struct{}

func (shortSink) Write(p []byte) (int, error)                       { return len(p) - 1, nil }
func (shortSink) WriteContext(context.Context, []byte) (int, error) { return 0, nil }
func TestShortWriterIsNotFinalDelivery(t *testing.T) {
	b, e := NewBudget(context.Background(), time.Second, shortSink{})
	if e != nil {
		t.Fatal(e)
	}
	b.Close()
	m := b.Snapshot()
	if m.Final || m.FinalLogStatus != "NOT_DELIVERED" || m.LogWriteErrors != 1 {
		t.Fatal(m)
	}
}
func TestRealPipeSubprocessExit(t *testing.T) {
	if os.Getenv("STOCKFISH_TEST_PIPE_CHILD") == "1" {
		r, w := io.Pipe()
		defer r.Close()
		b, e := NewBudget(context.Background(), 40*time.Millisecond, NewOwnedPipe(w))
		if e != nil {
			os.Exit(2)
		}
		<-b.Ctx().Done()
		if b.Check() == nil {
			os.Exit(3)
		}
		_ = b.Snapshot()
		b.Close()
		_ = json.NewEncoder(os.Stdout).Encode(b.Snapshot())
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestRealPipeSubprocessExit$")
	cmd.Env = append(os.Environ(), "STOCKFISH_TEST_PIPE_CHILD=1")
	raw, e := cmd.Output()
	if e != nil {
		t.Fatalf("child: %v %s", e, raw)
	}
	var m map[string]any
	line := raw
	for i, b := range raw {
		if b == '\n' {
			line = raw[:i]
			break
		}
	}
	if e = json.Unmarshal(line, &m); e != nil || m["final_log_status"] != "NOT_DELIVERED" {
		t.Fatalf("%v %s", e, raw)
	}
}
