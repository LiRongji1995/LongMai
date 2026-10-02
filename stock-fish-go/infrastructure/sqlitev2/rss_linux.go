//go:build linux

package sqlitev2

import (
	"fmt"
	"os"
)

func migrationRSS() (int64, error) {
	b, e := os.ReadFile("/proc/self/statm")
	if e != nil {
		return 0, e
	}
	var size, rss int64
	if _, e = fmt.Sscan(string(b), &size, &rss); e != nil {
		return 0, e
	}
	return rss * int64(os.Getpagesize()), nil
}
