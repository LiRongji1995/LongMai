//go:build !linux && !darwin

package core

import "fmt"

func currentRSS() (int64, error) { return 0, fmt.Errorf("RSS_UNAVAILABLE_ON_PLATFORM") }
