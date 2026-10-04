package main

import (
	"context"
	"encoding/json"
	resources "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/cli"
	"os"
	"os/signal"
	"runtime/debug"
	"syscall"
	"time"
)

var sourceHash = "UNRECORDED_DEVELOPMENT_BUILD"

func main() {
	// Leave RSS headroom for SQLite, stacks and result encoding; this soft
	// heap target does not replace the independent 1 GiB RSS guard.
	debug.SetMemoryLimit(512 << 20)
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	stdout, e := resources.NewCommandFile(ctx, os.Stdout)
	if e != nil {
		os.Exit(1)
	}
	defer stdout.Close()
	stderr, e := resources.NewCommandFile(ctx, os.Stderr)
	if e != nil {
		os.Exit(1)
	}
	defer stderr.Close()
	if e := cli.Run(ctx, os.Args[1:], stdout, stderr, sourceHash); e != nil {
		final, c := context.WithTimeout(context.Background(), 250*time.Millisecond)
		raw, _ := json.Marshal(map[string]string{"execution": "FAILED", "reason": e.Error(), "go_source_hash": sourceHash})
		_, _ = stderr.WriteContext(final, append(raw, '\n'))
		c()
		stdout.Close()
		stderr.Close()
		os.Exit(1)
	}
}
