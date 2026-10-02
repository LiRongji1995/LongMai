//go:build darwin

package lifecycle

import (
	"golang.org/x/sys/unix"
)

func cloneAvailable(source, destinationDirectory string) (bool, error) {
	var a, b unix.Statfs_t
	if e := unix.Statfs(source, &a); e != nil {
		return false, e
	}
	if e := unix.Statfs(destinationDirectory, &b); e != nil {
		return false, e
	}
	return unix.ByteSliceToString(a.Fstypename[:]) == "apfs" && unix.ByteSliceToString(b.Fstypename[:]) == "apfs" && a.Fsid == b.Fsid, nil
}
func cloneDatabase(source, target string) error {
	return unix.Clonefile(source, target, unix.CLONE_NOFOLLOW)
}
