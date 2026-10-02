//go:build !linux && !darwin && !windows

package runtime

import "fmt"

func currentRSS() (int64, error) { return 0, fmt.Errorf("RSS_UNAVAILABLE_ON_PLATFORM") }
