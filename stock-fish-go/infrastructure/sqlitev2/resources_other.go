//go:build !darwin && !linux && !windows

package sqlitev2

import "errors"

func AvailableBytes(string) (int64, error) { return 0, errors.New("SPACE_MEASUREMENT_UNAVAILABLE") }
func CheckResources(string) error          { return errors.New("RSS_MEASUREMENT_UNAVAILABLE") }
