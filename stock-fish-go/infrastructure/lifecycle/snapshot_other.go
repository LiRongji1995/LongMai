//go:build !darwin

package lifecycle

import "errors"

func cloneAvailable(source, destinationDirectory string) (bool, error) { return false, nil }
func cloneDatabase(source, target string) error                        { return errors.New("APFS_CLONE_UNAVAILABLE") }
