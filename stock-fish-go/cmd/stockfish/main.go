package main

import (
	"context"
	"encoding/json"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/cli"
	"os"
	"os/signal"
	"runtime/debug"
	"syscall"
)

var sourceHash = "UNRECORDED_DEVELOPMENT_BUILD"

func main() {
	debug.SetMemoryLimit(768 << 20)
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if e := cli.Run(ctx, os.Args[1:], os.Stdout, os.Stderr, sourceHash); e != nil {
		_ = json.NewEncoder(os.Stderr).Encode(map[string]string{"execution": "FAILED", "reason": e.Error(), "go_source_hash": sourceHash})
		os.Exit(1)
	}
}
