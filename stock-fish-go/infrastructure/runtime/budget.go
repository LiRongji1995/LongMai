package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"io"
	"os"
	goruntime "runtime"
	"sync"
	"time"
)

const MaxSourceBytes int64 = 2 << 30
const MaxOutputBytes int64 = 128 << 20
const MaxRSS int64 = 1 << 30

type Budget struct {
	Context     context.Context
	cancel      context.CancelFunc
	start, last time.Time
	mu          sync.Mutex
	m           contracts.Metrics
	writer      io.Writer
	done        chan struct{}
	failure     error
	report      ports.ProgressReporter
}

func NewBudget(parent context.Context, d time.Duration, w io.Writer) (*Budget, error) {
	if d <= 0 || d > 1800*time.Second {
		return nil, contracts.ErrCode("BUDGET_MUST_BE_0_TO_1800_SECONDS")
	}
	ctx, cancel := context.WithTimeout(parent, d)
	b := &Budget{Context: ctx, cancel: cancel, start: time.Now(), last: time.Now(), writer: w, done: make(chan struct{}), m: contracts.Metrics{Stage: "input_validation", Stages: map[string]float64{}}}
	b.report = ports.ProgressFromContext(parent)
	// Sample immediately as well as periodically; short runs still publish a
	// measured RSS and cannot start while already over the resident-set limit.
	b.sample()
	if b.m.RSS != nil && *b.m.RSS > MaxRSS {
		cancel()
		return nil, contracts.ErrCode("RSS_BUDGET_EXCEEDED")
	}
	go b.monitor()
	return b, nil
}
func (b *Budget) monitor() {
	defer close(b.done)
	t := time.NewTicker(time.Second)
	defer t.Stop()
	for {
		select {
		case <-b.Context.Done():
			return
		case <-t.C:
			b.mu.Lock()
			b.sample()
			if b.m.RSS != nil && *b.m.RSS > MaxRSS {
				b.failure = contracts.ErrCode("RSS_BUDGET_EXCEEDED")
				b.cancel()
			}
			if b.writer != nil {
				_ = json.NewEncoder(b.writer).Encode(b.m)
			}
			b.mu.Unlock()
		}
	}
}
func (b *Budget) sample() {
	b.m.Elapsed = time.Since(b.start).Seconds()
	rss, e := currentRSS()
	if e != nil {
		b.m.RSS = nil
		b.m.RSSStatus = "UNAVAILABLE"
	} else {
		b.m.RSS = &rss
		b.m.RSSStatus = "AVAILABLE"
		if b.m.PeakRSS == nil || rss > *b.m.PeakRSS {
			v := rss
			b.m.PeakRSS = &v
		}
	}
	var m goruntime.MemStats
	goruntime.ReadMemStats(&m)
	b.m.GoHeap = m.HeapAlloc
}
func (b *Budget) Check() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.failure != nil {
		return b.failure
	}
	return b.Context.Err()
}
func (b *Budget) Read(n int64) error {
	if n < 0 {
		return contracts.ErrCode("NEGATIVE_BUDGET_ACCOUNTING")
	}
	b.mu.Lock()
	b.m.SourceBytes += n
	b.m.Rows++
	if b.m.SourceBytes > MaxSourceBytes {
		b.failure = contracts.ErrCode("SOURCE_BYTES_BUDGET_EXCEEDED")
		b.cancel()
	}
	b.mu.Unlock()
	return b.Check()
}

func (b *Budget) IndexRow() error {
	b.mu.Lock()
	b.m.IndexRows++
	failure := b.failure
	b.mu.Unlock()
	if failure != nil {
		return failure
	}
	return b.Context.Err()
}
func (b *Budget) Output(n int64) error {
	if n < 0 {
		return contracts.ErrCode("NEGATIVE_BUDGET_ACCOUNTING")
	}
	b.mu.Lock()
	b.m.OutputBytes += n
	if b.m.OutputBytes > MaxOutputBytes {
		b.failure = contracts.ErrCode("OUTPUT_BYTES_BUDGET_EXCEEDED")
		b.cancel()
	}
	b.mu.Unlock()
	return b.Check()
}
func (b *Budget) Stage(s string) {
	b.mu.Lock()
	now := time.Now()
	b.m.Stages[b.m.Stage] += now.Sub(b.last).Seconds()
	b.last = now
	b.m.Stage = s
	n := b.m.Processed
	b.mu.Unlock()
	b.reportProgress(s, n)
}
func (b *Budget) Processed(n int) {
	b.mu.Lock()
	b.m.Processed = n
	s := b.m.Stage
	b.mu.Unlock()
	b.reportProgress(s, n)
}
func (b *Budget) reportProgress(stage string, count int) {
	if b.report == nil {
		return
	}
	if e := b.report(stage, count); e != nil {
		b.mu.Lock()
		// A resource violation may cancel an in-flight progress write. Preserve
		// that original cause when the write returns context.Canceled, otherwise
		// the application would mistake a budget failure for a user cancellation.
		if b.failure == nil {
			b.failure = e
		}
		b.cancel()
		b.mu.Unlock()
	}
}
func (b *Budget) Observe(label string, seconds float64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.m.Components == nil {
		b.m.Components = map[string]float64{}
	}
	b.m.Components[label] += seconds
}
func (b *Budget) CacheRead(n int64) { b.mu.Lock(); defer b.mu.Unlock(); b.m.CacheReadBytes += n }
func (b *Budget) HistoryPayload(n int64, unique bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if unique {
		b.m.HistoryUniqueRows++
		b.m.HistoryUniqueBytes += n
	} else {
		b.m.HistoryRepeatedRows++
		b.m.HistoryRepeatedBytes += n
	}
}
func (b *Budget) Snapshot() contracts.Metrics {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.sample()
	m := b.m
	m.Stages = map[string]float64{}
	m.Components = map[string]float64{}
	for k, v := range b.m.Components {
		m.Components[k] = v
	}
	for k, v := range b.m.Stages {
		m.Stages[k] = v
	}
	m.Stages[m.Stage] += time.Since(b.last).Seconds()
	return m
}
func (b *Budget) Close() {
	b.cancel()
	<-b.done
	if b.writer != nil {
		_ = json.NewEncoder(b.writer).Encode(b.Snapshot())
	}
}
func ReadBounded(path string, max int64, b ports.ResearchBudget) ([]byte, error) {
	f, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	var out []byte
	buf := make([]byte, 64<<10)
	for {
		if e = b.Check(); e != nil {
			return nil, e
		}
		n, er := f.Read(buf)
		if int64(len(out)+n) > max {
			return nil, fmt.Errorf("INPUT_SIZE_LIMIT_%d", max)
		}
		out = append(out, buf[:n]...)
		if er == io.EOF {
			break
		}
		if er != nil {
			return nil, er
		}
	}
	return out, b.Check()
}

func (b *Budget) Cancel()              { b.cancel() }
func (b *Budget) Ctx() context.Context { return b.Context }
func (b *Budget) Expanded(n int64) error {
	b.mu.Lock()
	b.m.ExpandedOutputBytes += n
	if b.m.ExpandedOutputBytes > 1<<30 {
		b.failure = contracts.ErrCode("EXPANDED_OUTPUT_BUDGET_EXCEEDED")
		b.cancel()
	}
	b.mu.Unlock()
	return b.Check()
}
func CurrentRSS() (int64, error) { return currentRSS() }
