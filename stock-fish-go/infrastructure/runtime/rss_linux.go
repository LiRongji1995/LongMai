//go:build linux

package runtime

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

func currentRSS() (int64, error) {
	b, e := os.ReadFile("/proc/self/statm")
	if e != nil {
		return 0, e
	}
	s := strings.Fields(string(b))
	if len(s) < 2 {
		return 0, fmt.Errorf("RSS_UNAVAILABLE")
	}
	n, e := strconv.ParseInt(s[1], 10, 64)
	return n * int64(os.Getpagesize()), e
}
