//go:build !darwin && !linux

package runtime

import (
	"context"
	"errors"
	"os"
)

type CommandFile struct{}

func NewCommandFile(context.Context, *os.File) (*CommandFile, error) {
	return nil, errors.New("COMMAND_PIPE_CANCELLATION_NOT_SUPPORTED_ON_THIS_PLATFORM")
}
func (*CommandFile) Write([]byte) (int, error) { return 0, errors.New("UNSUPPORTED_LOG_SINK") }
func (*CommandFile) WriteContext(context.Context, []byte) (int, error) {
	return 0, errors.New("UNSUPPORTED_LOG_SINK")
}
func (*CommandFile) Close() error { return nil }
