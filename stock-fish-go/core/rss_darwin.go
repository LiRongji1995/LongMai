//go:build darwin

package core

import (
	"syscall"
	"unsafe"
)

// PROC_PIDTASKINFO returns current resident_size (not ru_maxrss peak).
func currentRSS() (int64, error) {
	var task [12]uint64
	_, _, e := syscall.Syscall6(336, 2, uintptr(syscall.Getpid()), 4, 0, uintptr(unsafe.Pointer(&task[0])), uintptr(unsafe.Sizeof(task)))
	if e != 0 {
		return 0, e
	}
	return int64(task[1]), nil
}
