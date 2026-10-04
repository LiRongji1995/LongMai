package runtime

import (
	"bytes"
	"context"
	"errors"
	"io"
	"strings"
	"sync"
	"time"
)

// ContextWriter must return when ctx expires. Ordinary external io.Writers are
// deliberately not invoked: Go cannot cancel their Write or safely reclaim a
// blocked goroutine. A budget uses one worker and a two-entry dropping queue.
type ContextWriter interface {
	WriteContext(context.Context, []byte) (int, error)
}
type memorySink struct{ io.Writer }

func (w memorySink) WriteContext(ctx context.Context, p []byte) (int, error) {
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	return w.Write(p)
}
func metricsSink(w io.Writer) (ContextWriter, error) {
	if w == nil {
		return nil, nil
	}
	if s, ok := w.(ContextWriter); ok {
		return s, nil
	}
	switch w.(type) {
	case *bytes.Buffer, *strings.Builder:
		return memorySink{w}, nil
	}
	return nil, errors.New("LOG_SINK_REQUIRES_CONTEXT_WRITER_OR_OWNED_PIPE")
}

// OwnedPipe transfers ownership of the writer only (never of its reader).
// One worker performs writes. Cancellation closes the writer, joins that worker
// and permanently disables the sink. No caller-owned stderr is closed.
type OwnedPipe struct {
	writer *io.PipeWriter
	queue  chan pipeWrite
	done   chan struct{}
	stop   chan struct{}
	once   sync.Once
}
type pipeWrite struct {
	raw    []byte
	result chan pipeResult
}
type pipeResult struct {
	n   int
	err error
}

func NewOwnedPipe(w *io.PipeWriter) *OwnedPipe {
	p := &OwnedPipe{writer: w, queue: make(chan pipeWrite), done: make(chan struct{}), stop: make(chan struct{})}
	go func() {
		defer close(p.done)
		for {
			select {
			case <-p.stop:
				return
			case q := <-p.queue:
				n, e := w.Write(q.raw)
				q.result <- pipeResult{n, e}
				if e != nil {
					return
				}
			}
		}
	}()
	return p
}
func (p *OwnedPipe) Write(b []byte) (int, error) {
	ctx, c := context.WithTimeout(context.Background(), 250*time.Millisecond)
	defer c()
	return p.WriteContext(ctx, b)
}
func (p *OwnedPipe) WriteContext(ctx context.Context, b []byte) (int, error) {
	q := pipeWrite{append([]byte(nil), b...), make(chan pipeResult, 1)}
	select {
	case <-p.done:
		return 0, io.ErrClosedPipe
	case <-ctx.Done():
		return 0, ctx.Err()
	case p.queue <- q:
	}
	select {
	case r := <-q.result:
		return r.n, r.err
	case <-ctx.Done():
		_ = p.writer.CloseWithError(ctx.Err())
		<-p.done
		return 0, ctx.Err()
	}
}
func (p *OwnedPipe) Close() error {
	p.once.Do(func() { _ = p.writer.Close(); close(p.stop); <-p.done })
	return nil
}
