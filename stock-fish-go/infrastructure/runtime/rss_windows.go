//go:build windows

package runtime

import (
	"fmt"
	"syscall"
	"unsafe"
)

// GetProcessMemoryInfo reports the current resident working set, including
// allocations outside the Go heap. Native Windows execution is still required
// for platform acceptance; a cross compile proves only the API binding builds.
var getProcessMemoryInfo = syscall.NewLazyDLL("psapi.dll").NewProc("GetProcessMemoryInfo")

func currentRSS() (int64, error) {
	var counters struct {
		Size, PageFaultCount           uint32
		PeakWorkingSet, WorkingSet     uintptr
		PeakPagedPool, PagedPool       uintptr
		PeakNonPagedPool, NonPagedPool uintptr
		Pagefile, PeakPagefile         uintptr
	}
	counters.Size = uint32(unsafe.Sizeof(counters))
	handle, err := syscall.GetCurrentProcess()
	if err != nil {
		return 0, err
	}
	ok, _, callErr := getProcessMemoryInfo.Call(uintptr(handle), uintptr(unsafe.Pointer(&counters)), uintptr(counters.Size))
	if ok == 0 {
		return 0, fmt.Errorf("GetProcessMemoryInfo: %w", callErr)
	}
	return int64(counters.WorkingSet), nil
}
