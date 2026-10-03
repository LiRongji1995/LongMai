//go:build !darwin

package sqlitev2

import "context"

func copyFrozen(ctx context.Context, source, target string) error {
	return streamFrozen(ctx, source, target)
}
