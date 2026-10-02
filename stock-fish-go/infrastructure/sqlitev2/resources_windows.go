//go:build windows

package sqlitev2

import (
	"fmt"
	"golang.org/x/sys/windows"
	"unsafe"
)

func AvailableBytes(path string) (int64, error) {
	p, e := windows.UTF16PtrFromString(path)
	if e != nil {
		return 0, e
	}
	var free, total, totalFree uint64
	if e = windows.GetDiskFreeSpaceEx(p, &free, &total, &totalFree); e != nil {
		return 0, e
	}
	return int64(free), nil
}
func CheckResources(path string) error {
	n, e := AvailableBytes(path)
	if e != nil {
		return e
	}
	if n < 10<<30 {
		return fmt.Errorf("SPACE_RESERVE_EXHAUSTED: %d", n)
	}
	var info struct {
		CB, Faults                                                          uint32
		Peak, Working, PagePeak, Page, NonPeak, Non, Pagefile, PagefilePeak uintptr
	}
	info.CB = uint32(unsafe.Sizeof(info))
	p := windows.NewLazySystemDLL("psapi.dll").NewProc("GetProcessMemoryInfo")
	r, _, e := p.Call(uintptr(windows.CurrentProcess()), uintptr(unsafe.Pointer(&info)), uintptr(info.CB))
	if r == 0 {
		return e
	}
	if info.Working > 1<<30 {
		return fmt.Errorf("RSS_LIMIT_EXCEEDED: %d", info.Working)
	}
	return nil
}
