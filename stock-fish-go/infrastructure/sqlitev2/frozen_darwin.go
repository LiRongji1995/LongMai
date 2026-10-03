//go:build darwin

package sqlitev2

import (
	"context"
	"golang.org/x/sys/unix"
)

func copyFrozen(ctx context.Context, source, target string) error {
	if e := ctx.Err(); e != nil {
		return e
	}
	if e := unix.Clonefile(source, target, unix.CLONE_NOFOLLOW); e == nil {
		return nil
	}
	return streamFrozen(ctx, source, target)
}
