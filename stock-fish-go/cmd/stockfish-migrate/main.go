package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/lifecycle"
	s "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"io"
	"os"
	"path/filepath"
	"runtime/debug"
	"sync"
	"time"
)

func main() {
	debug.SetMemoryLimit(512 << 20)
	if e := run(context.Background(), os.Args[1:], os.Stdout, os.Stderr); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}

// Returning from run releases the shared lease on every handled failure. main
// may exit only after these defers have completed.
func run(parent context.Context, args []string, outWriter, logWriter io.Writer) (result error) {
	f := flag.NewFlagSet("stockfish-migrate", flag.ContinueOnError)
	f.SetOutput(logWriter)
	source := f.String("source", "", "source or frozen snapshot")
	target := f.String("target", "", "new destination")
	seconds := f.Int("seconds", 7200, "command deadline; caller also accounts cumulative 7200s")
	batch := f.Int("batch", 1000, "bounded transaction rows")
	max := f.Int("max-batches", 0, "controlled interruption")
	if len(args) < 1 {
		return errors.New("inventory|snapshot|migrate|verify|publish --source ... --target ...")
	}
	command := args[0]
	switch command {
	case "inventory", "snapshot", "migrate", "incremental", "verify", "stats", "publish":
	default:
		return fmt.Errorf("unknown action %q", command)
	}
	if e := f.Parse(args[1:]); e != nil {
		return e
	}
	if f.NArg() != 0 {
		return errors.New("UNEXPECTED_POSITIONAL_ARGUMENTS")
	}
	if *seconds < 1 || *seconds > 7200 {
		return errors.New("MIGRATION_BUDGET_INVALID")
	}
	if command != "inventory" {
		if *target == "" {
			return errors.New("EXPLICIT_MIGRATION_TARGET_REQUIRED")
		}
		canonical, release, e := lifecycle.AcquireDatabaseWriter(*target, fmt.Sprintf("stockfish-migrate-pid-%d", os.Getpid()), command == "snapshot" || command == "migrate")
		if e != nil {
			return e
		}
		defer func() { result = errors.Join(result, release()) }()
		*target = canonical
	}
	ctx, cancel := context.WithTimeout(parent, time.Duration(*seconds)*time.Second)
	defer cancel()
	var out any
	var e error
	var resourceMu sync.Mutex
	var resourceError error
	if command != "inventory" && *target != "" {
		go func() {
			ticker := time.NewTicker(time.Second)
			defer ticker.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
					if err := s.CheckResources(filepath.Dir(*target)); err != nil {
						resourceMu.Lock()
						resourceError = err
						resourceMu.Unlock()
						cancel()
						return
					}
				}
			}
		}()
	}

	dir := filepath.Dir(*target)
	lastCheck := time.Time{}
	guard := func() error {
		if time.Since(lastCheck) < time.Second {
			return nil
		}
		lastCheck = time.Now()
		return s.CheckResources(dir)
	}
	lastProgress := map[string]int64{}
	lastProgressTime := map[string]time.Time{}
	opt := s.Options{BatchSize: *batch, MaxBatches: *max, ResourceCheck: guard, Progress: func(stage string, n int64) {
		if n == 0 || n-lastProgress[stage] >= 10000 || time.Since(lastProgressTime[stage]) >= 5*time.Second {
			fmt.Fprintf(logWriter, "%s: %d\n", stage, n)
			lastProgress[stage] = n
			lastProgressTime[stage] = time.Now()
		}
	}}
	switch command {
	case "inventory":
		out, e = s.InventorySource(ctx, *source)
	case "snapshot":
		if e = guard(); e == nil {
			out, e = s.Snapshot(ctx, *source, *target)
		}
	case "migrate":
		out, e = s.Migrate(ctx, *source, *target, opt)
	case "incremental":
		out, e = s.Incremental(ctx, *source, *target, opt)
	case "verify":
		out, e = s.Verify(ctx, *source, *target, opt)
	case "stats":
		var store *s.Store
		store, e = s.Open(ctx, *target, false)
		if e == nil {
			out, e = store.Stats(ctx)
			store.Close()
		}
	case "publish":
		e = s.Publish(ctx, *target)
		out = map[string]bool{"published": e == nil}
	default:
		e = fmt.Errorf("unknown action %q", command)
	}
	resourceMu.Lock()
	if resourceError != nil {
		e = resourceError
	}
	resourceMu.Unlock()
	if out != nil {
		if outputErr := json.NewEncoder(outWriter).Encode(out); e == nil {
			e = outputErr
		}
	}
	return e
}
