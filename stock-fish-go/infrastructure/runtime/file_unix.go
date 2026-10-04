//go:build darwin || linux

package runtime

import (
	"context"
	"errors"
	"golang.org/x/sys/unix"
	"io"
	"os"
	"sync"
)

// CommandFile is for command-owned stdout/stderr. It duplicates the descriptor,
// temporarily enables nonblocking pipe writes and restores flags on Close. It
// never closes the supplied file. Regular files need no poll worker. This does
// not offer a deadline for a stalled filesystem/kernel; pipe backpressure is
// bounded. Do not use it on a descriptor shared with another application thread.
type CommandFile struct {
	fd, flags int
	mu        sync.Mutex
	closed    bool
	ctx       context.Context
}

func NewCommandFile(ctx context.Context, f *os.File) (*CommandFile, error) {
	fd, e := unix.Dup(int(f.Fd()))
	if e != nil {
		return nil, e
	}
	unix.CloseOnExec(fd)
	flags, e := unix.FcntlInt(uintptr(fd), unix.F_GETFL, 0)
	if e == nil {
		_, e = unix.FcntlInt(uintptr(fd), unix.F_SETFL, flags|unix.O_NONBLOCK)
	}
	if e != nil {
		unix.Close(fd)
		return nil, e
	}
	return &CommandFile{fd: fd, flags: flags, ctx: ctx}, nil
}
func (w *CommandFile) Write(p []byte) (int, error) { return w.WriteContext(w.ctx, p) }
func (w *CommandFile) WriteContext(ctx context.Context, p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.closed {
		return 0, os.ErrClosed
	}
	n := 0
	for n < len(p) {
		if e := ctx.Err(); e != nil {
			return n, e
		}
		end := n + 4096
		if end > len(p) {
			end = len(p)
		}
		k, e := unix.Write(w.fd, p[n:end])
		if k > 0 {
			n += k
		}
		if errors.Is(e, unix.EAGAIN) || errors.Is(e, unix.EWOULDBLOCK) {
			_, er := unix.Poll([]unix.PollFd{{Fd: int32(w.fd), Events: unix.POLLOUT}}, 20)
			if er != nil && !errors.Is(er, unix.EINTR) {
				return n, er
			}
			continue
		}
		if errors.Is(e, unix.EINTR) {
			continue
		}
		if e != nil {
			return n, e
		}
		if k == 0 {
			return n, io.ErrShortWrite
		}
	}
	return n, nil
}
func (w *CommandFile) Close() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.closed {
		return nil
	}
	w.closed = true
	_, _ = unix.FcntlInt(uintptr(w.fd), unix.F_SETFL, w.flags)
	return unix.Close(w.fd)
}
