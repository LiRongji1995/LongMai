package core

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"runtime"
	"sync"
	"time"
)

const MaxSourceBytes int64 = 2 << 30
const MaxOutputBytes int64 = 128 << 20
const MaxRSS int64 = 1 << 30

type Metrics struct {
	Elapsed             float64            `json:"elapsed_seconds"`
	Stage               string             `json:"stage"`
	Processed           int                `json:"processed"`
	Rows                int64              `json:"source_rows"`
	SourceBytes         int64              `json:"source_bytes"`
	IndexRows           int64              `json:"index_rows_verified"`
	ExpandedOutputBytes int64              `json:"expanded_detail_output_bytes"`
	OutputBytes         int64              `json:"output_bytes"`
	CacheReadBytes      int64              `json:"cache_read_bytes"`
	CacheBytes          int64              `json:"cache_bytes"`
	RSS                 *int64             `json:"rss_bytes"`
	PeakRSS             *int64             `json:"peak_rss_bytes"`
	GoHeap              uint64             `json:"go_heap_bytes"`
	RSSStatus           string             `json:"rss_status"`
	Stages              map[string]float64 `json:"stage_seconds"`
}
type Budget struct {
	Context     context.Context
	cancel      context.CancelFunc
	start, last time.Time
	mu          sync.Mutex
	m           Metrics
	writer      io.Writer
	done        chan struct{}
	failure     error
}

func NewBudget(parent context.Context, d time.Duration, w io.Writer) (*Budget, error) {
	if d <= 0 || d > 1800*time.Second {
		return nil, errCode("BUDGET_MUST_BE_0_TO_1800_SECONDS")
	}
	ctx, cancel := context.WithTimeout(parent, d)
	b := &Budget{Context: ctx, cancel: cancel, start: time.Now(), last: time.Now(), writer: w, done: make(chan struct{}), m: Metrics{Stage: "input_validation", Stages: map[string]float64{}}}
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
				b.failure = errCode("RSS_BUDGET_EXCEEDED")
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
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
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
	b.mu.Lock()
	b.m.SourceBytes += n
	b.m.Rows++
	if b.m.SourceBytes > MaxSourceBytes {
		b.failure = errCode("SOURCE_BYTES_BUDGET_EXCEEDED")
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
	b.mu.Lock()
	b.m.OutputBytes += n
	if b.m.OutputBytes > MaxOutputBytes {
		b.failure = errCode("OUTPUT_BYTES_BUDGET_EXCEEDED")
		b.cancel()
	}
	b.mu.Unlock()
	return b.Check()
}
func (b *Budget) Stage(s string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	now := time.Now()
	b.m.Stages[b.m.Stage] += now.Sub(b.last).Seconds()
	b.last = now
	b.m.Stage = s
}
func (b *Budget) Processed(n int) { b.mu.Lock(); b.m.Processed = n; b.mu.Unlock() }
func (b *Budget) Snapshot() Metrics {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.sample()
	m := b.m
	m.Stages = map[string]float64{}
	for k, v := range b.m.Stages {
		m.Stages[k] = v
	}
	m.Stages[m.Stage] += time.Since(b.last).Seconds()
	return m
}
func (b *Budget) Close() { b.cancel(); <-b.done }
func ReadBounded(path string, max int64, b *Budget) ([]byte, error) {
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
