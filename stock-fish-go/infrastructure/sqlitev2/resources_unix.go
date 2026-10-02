//go:build darwin || linux

package sqlitev2

import (
	"fmt"
	"syscall"
)

func AvailableBytes(path string) (int64, error) {
	var st syscall.Statfs_t
	if e := syscall.Statfs(path, &st); e != nil {
		return 0, e
	}
	return int64(st.Bavail) * int64(st.Bsize), nil
}
func CheckResources(path string) error {
	n, e := AvailableBytes(path)
	if e != nil {
		return e
	}
	if n < 10<<30 {
		return fmt.Errorf("SPACE_RESERVE_EXHAUSTED: available=%d required=%d", n, 10<<30)
	}
	rss, e := migrationRSS()
	if e != nil {
		return fmt.Errorf("RSS_MEASUREMENT_UNAVAILABLE: %w", e)
	}
	if rss > 1<<30 {
		return fmt.Errorf("RSS_LIMIT_EXCEEDED: %d", rss)
	}
	return nil
}
