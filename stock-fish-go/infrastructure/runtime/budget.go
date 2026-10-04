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
	Context             context.Context
	cancel              context.CancelFunc
	start, last         time.Time
	mu                  sync.Mutex
	m                   contracts.Metrics
	sink                ContextWriter
	logs                chan logEntry
	logDone             chan struct{}
	closeOnce           sync.Once
	clock               Clock
	duration            time.Duration
	wallStart, wallLast time.Time
	monoStart, monoLast time.Duration
	done                chan struct{}
	failure             error
	report              ports.ProgressReporter
}

var errClock = contracts.ErrCode("CLOCK_OR_SUSPEND_ANOMALY")
var errWait = contracts.ErrCode("WAIT_BUDGET_EXCEEDED")

type logEntry struct {
	metrics contracts.Metrics
	final   bool
}

func NewBudget(parent context.Context, d time.Duration, w io.Writer) (*Budget, error) {
	return NewBudgetWithClock(parent, d, w, systemClock{time.Now()})
}
func NewBudgetWithClock(parent context.Context, d time.Duration, w io.Writer, clock Clock) (*Budget, error) {
	if d <= 0 || d > 1800*time.Second {
		return nil, contracts.ErrCode("BUDGET_MUST_BE_0_TO_1800_SECONDS")
	}
	ctx, cancel := context.WithTimeout(parent, d)
	wall, mono := clockSample(clock)
	b := &Budget{Context: ctx, cancel: cancel, start: time.Now(), last: time.Now(), done: make(chan struct{}), logs: make(chan logEntry, 2), logDone: make(chan struct{}), clock: clock, duration: d, wallStart: wall, wallLast: wall, monoStart: mono, monoLast: mono, m: contracts.Metrics{Stage: "input_validation", Stages: map[string]float64{}, FinalLogStatus: "NOT_ATTEMPTED", CacheSampleStatus: "UNAVAILABLE"}}
	b.Context = ports.WithBudget(ctx, b)
	b.report = ports.ProgressFromContext(parent)
	var e error
	b.sink, e = metricsSink(w)
	if e != nil {
		b.m.LogWriteErrors++
		b.m.LogSinkStatus = e.Error()
	} else if b.sink != nil {
		b.m.LogSinkStatus = "CONTEXT_WRITER"
	} else {
		b.m.LogSinkStatus = "DISABLED"
	}
	b.sample()
	if b.m.RSS != nil && *b.m.RSS > MaxRSS {
		cancel()
		return nil, contracts.ErrCode("RSS_BUDGET_EXCEEDED")
	}
	go b.logWorker()
	go b.monitor()
	return b, nil
}
func (b *Budget) enqueue(m contracts.Metrics, final bool) {
	if b.sink == nil {
		b.mu.Lock()
		b.m.LogDropped++
		if final {
			b.m.FinalLogStatus = "NOT_DELIVERED"
		}
		b.mu.Unlock()
		return
	}
	// Final summary replaces queued periodic records. The in-flight record is
	// bounded by its own write timeout, so Close can join both workers.
	if final {
		for {
			select {
			case <-b.logs:
				b.mu.Lock()
				b.m.LogDropped++
				b.mu.Unlock()
			default:
				goto drained
			}
		}
	drained:
	}
	select {
	case b.logs <- logEntry{m, final}:
	default:
		b.mu.Lock()
		b.m.LogDropped++
		if final {
			b.m.FinalLogStatus = "NOT_DELIVERED"
		}
		b.mu.Unlock()
	}
}
func (b *Budget) logWorker() {
	defer close(b.logDone)
	for q := range b.logs {
		m := q.metrics
		m.Final = q.final
		m.FinalLogStatus = "DELIVERY_ATTEMPT"
		raw, e := json.Marshal(m)
		if e == nil {
			raw = append(raw, '\n')
			ctx, cancel := context.WithTimeout(context.Background(), 250*time.Millisecond)
			n, er := b.sink.WriteContext(ctx, raw)
			cancel()
			e = er
			if e == nil && n != len(raw) {
				e = io.ErrShortWrite
			}
		}
		b.mu.Lock()
		if e != nil {
			b.m.LogWriteErrors++
			if q.final {
				b.m.FinalLogStatus = "NOT_DELIVERED"
			}
		} else if q.final {
			b.m.Final = true
			b.m.FinalLogStatus = "DELIVERED"
		}
		b.mu.Unlock()
	}
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
			b.sample()
			b.mu.Lock()
			if b.m.RSS != nil && *b.m.RSS > MaxRSS {
				b.failure = contracts.ErrCode("RSS_BUDGET_EXCEEDED")
				b.cancel()
			}
			b.mu.Unlock()
			b.enqueue(b.Snapshot(), false)
		}
	}
}
func (b *Budget) sample() {
	// OS sampling and runtime memory inspection take place outside the state lock.
	rss, e := currentRSS()
	var memory goruntime.MemStats
	goruntime.ReadMemStats(&memory)
	b.mu.Lock()
	defer b.mu.Unlock()
	b.checkClockLocked()
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
	b.m.GoHeap = memory.HeapAlloc
}
func (b *Budget) Check() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.checkClockLocked()
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
	return b.Check()
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
func (b *Budget) CacheObservation(outcome string, occupancy *int64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	switch outcome {
	case "hit":
		b.m.CacheHits++
	case "miss":
		b.m.CacheMisses++
	case "corrupt":
		b.m.CacheMisses++
		b.m.CacheCorrupt++
	case "decode_corrupt":
		b.m.CacheHits--
		b.m.CacheMisses++
		b.m.CacheCorrupt++
	case "error":
		b.m.CacheErrors++
	}
	if occupancy != nil {
		v := *occupancy
		b.m.CacheBytes = &v
		b.m.CacheSampleAt = time.Now().UTC().Format(time.RFC3339Nano)
		b.m.CacheSampleStatus = "MEASURED_AFTER_WRITE"
		b.m.CacheSamples++
	}
}
func (b *Budget) BindMetrics(run, input, hash, source string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if run != "" {
		b.m.RunID = run
	}
	if input != "" {
		b.m.InputID = input
	}
	if hash != "" {
		b.m.InputHash = hash
	}
	if source != "" {
		b.m.SourceHash = source
	}
}
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
	b.sample()
	b.mu.Lock()
	defer b.mu.Unlock()
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
	if m.RSS != nil {
		v := *m.RSS
		m.RSS = &v
	}
	if m.PeakRSS != nil {
		v := *m.PeakRSS
		m.PeakRSS = &v
	}
	if m.CacheBytes != nil {
		v := *m.CacheBytes
		m.CacheBytes = &v
	}
	return m
}
func (b *Budget) Close() {
	b.closeOnce.Do(func() {
		b.cancel()
		<-b.done
		b.enqueue(b.Snapshot(), true)
		close(b.logs)
		<-b.logDone
		if owned, ok := b.sink.(*OwnedPipe); ok {
			owned.Close()
		}
	})
}
func (b *Budget) CacheSample(occupancy *int64, status string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.m.CacheSamples++
	b.m.CacheSampleAt = time.Now().UTC().Format(time.RFC3339Nano)
	b.m.CacheSampleStatus = status
	b.m.CacheBytes = nil
	if occupancy != nil {
		v := *occupancy
		b.m.CacheBytes = &v
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
