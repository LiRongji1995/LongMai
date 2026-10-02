//go:build darwin

package sqlitev2

import (
	"syscall"
	"unsafe"
)

func migrationRSS() (int64, error) {
	var task [12]uint64
	_, _, e := syscall.Syscall6(336, 2, uintptr(syscall.Getpid()), 4, 0, uintptr(unsafe.Pointer(&task[0])), uintptr(unsafe.Sizeof(task)))
	if e != 0 {
		return 0, e
	}
	return int64(task[1]), nil
}
