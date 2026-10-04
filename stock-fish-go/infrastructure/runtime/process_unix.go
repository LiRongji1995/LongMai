//go:build darwin || linux

package runtime

import (
	"os/exec"
	"syscall"
	"time"
)

// Workers and their SDK subprocesses form one explicitly owned process group.
func ConfigureCommand(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error {
		if cmd.Process == nil {
			return nil
		}
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
	cmd.WaitDelay = 2 * time.Second
}
