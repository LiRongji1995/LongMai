package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/core"
	"os"
	"os/signal"
	"path/filepath"
	"runtime/debug"
	"time"
)

var sourceHash = "UNRECORDED"

func main() { os.Exit(run()) }
func run() int {
	fs := flag.NewFlagSet("stockfish-r1", flag.ContinueOnError)
	input := fs.String("input", "", "frozen input JSON")
	db := fs.String("history-db", "", "existing read-only SQLite")
	output := fs.String("output", "", "new private output directory")
	seconds := fs.Float64("budget-seconds", 300, "total wall-clock budget (0,1800]")
	config := fs.String("config-dir", "", "frozen Python configuration directory")
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: stockfish-r1 run|verify --input ... --history-db ... --output ... --config-dir ... --budget-seconds 300\nCurrent build emits migration diagnostics only, never an accepted R1 list.")
		return 2
	}
	command := os.Args[1]
	if e := fs.Parse(os.Args[2:]); e != nil {
		return 2
	}
	if fs.NArg() != 0 {
		return 2
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()
	b, e := core.NewBudget(ctx, time.Duration(*seconds*float64(time.Second)), os.Stderr)
	if e != nil {
		fmt.Fprintln(os.Stderr, e)
		return 2
	}
	defer b.Close()
	if command == "verify" {
		_, e := core.VerifyDiagnostic(*output, b)
		if e != nil {
			fmt.Fprintln(os.Stderr, e)
			return 1
		}
		fmt.Println("DIAGNOSTIC_INTEGRITY_VERIFIED; RESEARCH_INCOMPLETE; CANDIDATES_UNAVAILABLE")
		return 0
	}
	if command != "run" || *input == "" || *output == "" || *config == "" {
		fmt.Fprintln(os.Stderr, "run requires input, new output and config-dir")
		return 2
	}
	if e = os.Mkdir(*output, 0700); e != nil {
		fmt.Fprintln(os.Stderr, e)
		return 1
	}
	debug.SetMemoryLimit(768 << 20)
	failure := func(err error) int {
		_ = os.Remove(filepath.Join(*output, "diagnostic-completion.json"))
		v := map[string]any{"run_status": "FAILED", "candidate_availability": "UNAVAILABLE", "error_code": err.Error(), "metrics": b.Snapshot(), "go_source_hash": sourceHash, "budget_seconds": *seconds, "cleanup": "cooperative; history handles closed by deferred cleanup"}
		raw, _ := json.MarshalIndent(v, "", "  ")
		_ = os.WriteFile(filepath.Join(*output, "failure.json"), raw, 0600)
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	hashes := map[string]string{}
	for _, n := range []string{"stage_aware_research_r1.json", "stage_aware_momentum_v2.yaml", "r05_strategy_experiments.yaml"} {
		raw, er := core.ReadBounded(filepath.Join(*config, n), 1<<20, b)
		if er != nil {
			return failure(er)
		}
		hashes[n] = core.Hash(raw)
	}
	if e = checkConfigs(hashes); e != nil {
		return failure(e)
	}
	if sourceHash == "UNRECORDED" {
		return failure(fmt.Errorf("BUILD_SOURCE_IDENTITY_REQUIRED_USE_TOOLS_BUILD"))
	}
	d, e := core.Analyze(*input, *db, b)
	if e != nil {
		return failure(e)
	}
	binary, er := os.Executable()
	if er != nil {
		return failure(er)
	}
	raw, er := core.ReadBounded(binary, 128<<20, b)
	if er != nil {
		return failure(er)
	}
	m := core.Manifest{Contract: "GO_R1_DIAGNOSTIC_MANIFEST_V1", Baseline: "2e256883f1103eecb0014161c3de54b181890124", SourceHash: sourceHash, BinaryHash: core.Hash(raw), ConfigHashes: hashes, InputHash: d.InputBytesHash, HistoryHash: d.HistoryHash, Observation: d.Observation, Cutoff: d.Cutoff, BudgetSeconds: *seconds, ProductRole: "RESEARCH_ONLY", Promotion: "DISABLED", MarketEdge: "NOT_ESTABLISHED"}
	if e = core.SaveDiagnostic(*output, d, m, b); e != nil {
		return failure(e)
	}
	metrics := b.Snapshot()
	raw, _ = json.Marshal(metrics)
	fmt.Println(string(raw))
	fmt.Println("INCOMPLETE_IMPLEMENTATION; diagnostic saved; candidates unavailable")
	return 3
}
