//go:build !darwin && !linux

package runtime

import (
	"os/exec"
	"time"
)

func ConfigureCommand(cmd *exec.Cmd) { cmd.WaitDelay = 2 * time.Second }
