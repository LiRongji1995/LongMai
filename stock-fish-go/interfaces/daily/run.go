package daily

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/calendar"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/lifecycle"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/native"
	resources "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"time"
)

const Policy = "FOUR_CLOCK_SCOPE_REVISION_V2"

func IsCommand(s string) bool {
	switch s {
	case "--help", "-h", "help", "--version", "init", "doctor", "picks", "explain", "daily":
		return true
	}
	return false
}

const help = `StockFish — saved evidence and local research (no model key)
  stockfish --version | --help
  stockfish init --db EXISTING_PUBLISHED_DB [--config FILE]
  stockfish doctor [--format json]
  stockfish picks --strategy r1 [--run-id ID] [--all] [--limit N]
  stockfish explain INSTRUMENT_ID --strategy r1 [--run-id ID]
  stockfish daily --strategy r1 [--dry-run] [--refresh]
Common: --config FILE (then STOCKFISH_CONFIG, then OS user config)
        --format table|json|csv|markdown --output NEW_FILE
        --budget-seconds 1..1800 (default config 300)
init: --instance-root DIR --mode embedded|server --server-url URL
      --artifacts DIR --provider FIXED_MAINBOARD_PUBLIC_V1
      --python EXECUTABLE --worker SCRIPT
Flags override config. Relative config paths use the config directory.
No query fallback to another date, strategy, database or mode.
Advanced commands retained: database, isolation, import, import-legacy-input,
research, runs, show, summary, selected, detail, export, verify-run, jobs,
job, cancel, recover, resume, health, readiness, schedule, cache-clear, serve.
R1: RESEARCH_ONLY; promotion DISABLED; MARKET_EDGE=NOT_ESTABLISHED.
`

func Run(ctx context.Context, args []string, out, log io.Writer, source string, hashes map[string]string) error {
	return runAt(ctx, args, out, log, source, hashes, time.Now())
}
func runAt(ctx context.Context, args []string, out, log io.Writer, source string, hashes map[string]string, now time.Time) (result error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" || args[0] == "help" {
		_, e := io.WriteString(out, help)
		return e
	}
	if args[0] == "--version" {
		return json.NewEncoder(out).Encode(map[string]any{"protocol": Protocol, "go_source_hash": source, "go_version": runtime.Version(), "os": runtime.GOOS, "arch": runtime.GOARCH})
	}
	cmd := args[0]
	rest := args[1:]
	instrument := ""
	if cmd == "explain" && len(rest) > 0 && len(rest[0]) > 0 && rest[0][0] != '-' {
		instrument = rest[0]
		rest = rest[1:]
	}
	f := flag.NewFlagSet(cmd, flag.ContinueOnError)
	f.SetOutput(log)
	config := f.String("config", "", "config file")
	db := f.String("db", "", "init database")
	root := f.String("instance-root", "", "init isolated root")
	mode := f.String("mode", "embedded", "init mode")
	artifacts := f.String("artifacts", "artifacts", "init artifact root")
	strat := f.String("strategy", "", "r1 or v2")
	id := f.String("run-id", "", "explicit saved history")
	format := f.String("format", "table", "table/json/csv/markdown")
	output := f.String("output", "", "new file")
	limit := f.Int("limit", 0, "explicit display truncation; zero means all")
	all := f.Bool("all", false, "include rejected/unknown evaluated securities")
	dry := f.Bool("dry-run", false, "plan only")
	refresh := f.Bool("refresh", false, "fixed source capture")
	seconds := f.Int("budget-seconds", 0, "research override")
	server := f.String("server-url", "", "init explicit loopback server")
	token := f.String("token-file", "", "private token file")
	provider := f.String("provider", "", "init fixed provider")
	python := f.String("python", "", "init worker interpreter")
	worker := f.String("worker", "", "init worker script")
	if e := f.Parse(rest); e != nil {
		return e
	}
	visited := map[string]bool{}
	f.Visit(func(option *flag.Flag) { visited[option.Name] = true })
	if visited["budget-seconds"] && (*seconds < 1 || *seconds > 1800) {
		return errors.New("BUDGET_MUST_BE_1_TO_1800_SECONDS")
	}
	if cmd != "daily" && (visited["dry-run"] || visited["refresh"]) {
		return errors.New("DAILY_OPTIONS_ON_NON_DAILY_COMMAND")
	}
	if f.NArg() != 0 || *limit < 0 {
		return errors.New("INVALID_POSITIONAL_ARGUMENT_OR_LIMIT")
	}
	if cmd == "explain" && instrument == "" {
		return errors.New("EXPLAIN_INSTRUMENT_REQUIRED")
	}
	path, e := configPath(*config)
	if e != nil {
		return e
	}
	if cmd == "init" {
		if _, e := os.Stat(path); e == nil {
			return errors.New("CONFIG_EXISTS_NO_OVERWRITE")
		}
		base := filepath.Dir(path)
		database := anchored(base, *db)
		instance := anchored(base, *root)
		if instance != "" {
			v, e := lifecycle.Load(instance)
			if e != nil {
				return e
			}
			if v.Mode != "GO_V2" {
				return errors.New("ISOLATED_INSTANCE_NOT_GO_V2")
			}
			if database != "" && database != v.ActiveDatabase {
				return errors.New("INSTANCE_DATABASE_CONFLICT")
			}
			database = v.ActiveDatabase
		}
		if database == "" {
			return errors.New("INIT_REQUIRES_EXISTING_DB_OR_INSTANCE_ROOT_NONINTERACTIVE")
		}
		database, e = filepath.EvalSymlinks(database)
		if e != nil {
			return e
		}
		database, e = filepath.Abs(database)
		if e != nil {
			return e
		}
		r, e := sqlitev2.OpenReadOnly(ctx, database)
		if e != nil {
			return e
		}
		defer r.Close()
		budget := *seconds
		if budget == 0 {
			budget = 300
		}
		s := *strat
		if s == "" {
			s = "r1"
		}
		s, e = strategy(s)
		if e != nil {
			return e
		}
		dh, e := PathHash(database)
		if e != nil {
			return e
		}
		conf := Config{Version: Protocol, Database: database, InstanceRoot: instance, Artifacts: *artifacts, Mode: *mode, Strategy: s, Budget: budget, Identity: r.Identity(), DatabasePathHash: dh, ServerURL: *server, TokenFile: *token, Provider: *provider, Python: *python, Worker: *worker}
		if conf.Mode != "embedded" && conf.Mode != "server" || budget < 1 || budget > 1800 {
			return errors.New("INVALID_INIT_MODE_OR_BUDGET")
		}
		if conf.Provider != "" && conf.Provider != "FIXED_MAINBOARD_PUBLIC_V1" {
			return errors.New("UNREGISTERED_PROVIDER_CONTRACT")
		}
		if conf.Worker != "" {
			raw, e := os.ReadFile(anchored(base, conf.Worker))
			if e != nil {
				return e
			}
			conf.WorkerHash = c.Hash(raw)
		}
		if conf.Mode == "server" {
			remote, e := openServer(ctx, conf)
			if e != nil {
				return e
			}
			remote.Close()
		}
		if e = createConfig(path, conf); e != nil {
			return e
		}
		return json.NewEncoder(out).Encode(map[string]any{"config": path, "mode": conf.Mode, "database_identity": conf.Identity, "research_started": false, "network_capture_started": false})
	}
	conf, e := load(path)
	if e != nil {
		return fmt.Errorf("CONFIG_REQUIRED_RUN_INIT: %w", e)
	}
	if *db != "" || *root != "" {
		return errors.New("DATABASE_OVERRIDES_REQUIRE_NEW_CONFIG_INIT")
	}
	if *strat == "" {
		*strat = conf.Strategy
	}
	s, e := strategy(*strat)
	if e != nil {
		return e
	}
	if *seconds > 0 {
		conf.Budget = *seconds
	}
	if conf.Budget < 1 || conf.Budget > 1800 {
		return errors.New("BUDGET_MUST_BE_1_TO_1800_SECONDS")
	}
	queryLimit := 300 * time.Second
	if cmd == "daily" {
		queryLimit = time.Duration(conf.Budget) * time.Second
		if *refresh {
			queryLimit += 600 * time.Second
		}
	}
	queryCtx, queryCancel := context.WithTimeout(ctx, queryLimit)
	defer queryCancel()
	ctx = queryCtx
	if cmd == "picks" || cmd == "explain" {
		scope, e := resources.NewBudget(ctx, 300*time.Second, log)
		if e != nil {
			return e
		}
		defer scope.Close()
		scope.Stage("saved_result_verification")
		scope.BindMetrics(*id, "", "", source)
		ctx = scope.Ctx()
	}
	cal, e := calendar.Official().Plan(now)
	calError := ""
	if e != nil {
		calError = e.Error()
	}
	b, e := openBackend(ctx, conf)
	if cmd == "doctor" {
		query := "READY"
		reason := ""
		var ready c.Readiness
		if e != nil {
			query = "BLOCKED"
			reason = e.Error()
		} else {
			defer b.Close()
			ready, e = b.Ready(ctx)
			if e != nil || !ready.Ready {
				query = "BLOCKED"
				reason = ready.Reason
			}
		}
		local := query
		if calError != "" || conf.Mode != "embedded" || strings.HasPrefix(source, "UNRECORDED") {
			local = "BLOCKED"
		}
		if calError == "" && cal != nil {
			if !cal["observation_complete"].(bool) {
				local = "BLOCKED_OBSERVATION_NOT_COMPLETE"
			} else if b != nil {
				inputs, err := b.Inputs(ctx, cal["required_observation"].(string))
				if err != nil || len(inputs) == 0 {
					local = "BLOCKED_LOCAL_INPUT_UNAVAILABLE"
				}
			}
		}
		if e := writerPresence(conf.Database); e != nil {
			local = "BLOCKED"
		}
		v := map[string]any{"protocol": Protocol, "mode": conf.Mode, "database_identity": conf.Identity, "build_source_hash": source, "query": query, "query_reason": reason, "local_research": local, "online_refresh": refreshReady(conf), "source_dependencies": dependencyCheck(ctx, conf), "calendar": cal, "calendar_error": calError, "default_system_strategy": c.V2, "preferred_strategy": conf.Strategy, "readiness": ready, "network_probe": false, "history_scan": false}
		return outputValue(ctx, out, *output, *format, v)
	}
	if e != nil {
		return e
	}
	defer b.Close()
	historical := *id != ""
	if historical && (cmd == "daily") {
		return errors.New("DAILY_CANNOT_REUSE_HISTORY_RUN_FLAG")
	}
	if historical {
		return savedOutput(ctx, b, *id, instrument, true, *all, *limit, *format, *output, out)
	}
	if calError != "" {
		return errors.New(calError)
	}
	plan, e := makePlan(ctx, b, conf, s, source, hashes, cal, now)
	if e != nil {
		return e
	}
	if cmd == "picks" || cmd == "explain" {
		if plan.RunID == "" {
			return fmt.Errorf("NO_MATCHING_PUBLISHED_RESULT: %v", plan.Blockers)
		}
		return savedOutput(ctx, b, plan.RunID, instrument, false, *all, *limit, *format, *output, out)
	}
	if cmd != "daily" {
		return errors.New("UNSUPPORTED_DAILY_COMMAND")
	}
	if *refresh {
		filtered := []string{}
		for _, reason := range plan.Blockers {
			if reason != "LOCAL_FROZEN_INPUT_UNAVAILABLE" {
				filtered = append(filtered, reason)
			}
		}
		plan.Blockers = filtered
		plan.Research = true
	}
	plan.Refresh = *refresh
	plan.Capture = *refresh && refreshReady(conf) == "CONFIGURED_NOT_ONLINE_VERIFIED"
	if *refresh && refreshReady(conf) != "CONFIGURED_NOT_ONLINE_VERIFIED" {
		plan.Blockers = append(plan.Blockers, refreshReady(conf))
	}
	if *dry {
		return outputValue(ctx, out, *output, *format, plan)
	}
	if !*refresh && plan.RunID != "" {
		return savedOutput(ctx, b, plan.RunID, "", false, *all, *limit, *format, *output, out)
	}
	if len(plan.Blockers) > 0 {
		return fmt.Errorf("DAILY_BLOCKED: %v", plan.Blockers)
	}
	if *refresh {
		if conf.Mode != "embedded" {
			return errors.New("REFRESH_REQUIRES_EXPLICIT_EMBEDDED_SOURCE_CONTRACT")
		}
		input, e := capture(ctx, conf, plan.Observation, log)
		if e != nil {
			return e
		}
		plan.InputID = ""
		return localResearch(ctx, conf, plan, s, source, hashes, input, out, log, *format, *output, *all, *limit)
	}
	if conf.Mode == "server" {
		remote := b.(*remote)
		id, e := remote.research(ctx, plan, s, source, hashes)
		if e != nil {
			return e
		}
		return savedOutput(ctx, b, id, "", false, *all, *limit, *format, *output, out)
	}
	return localResearch(ctx, conf, plan, s, source, hashes, nil, out, log, *format, *output, *all, *limit)
}
func writerPresence(db string) error {
	if _, e := os.Stat(db + ".go-writer"); e == nil {
		return errors.New("DATABASE_WRITER_ACTIVE")
	}
	return nil
}
func localResearch(ctx context.Context, conf Config, plan Plan, s, source string, hashes map[string]string, input []byte, out, log io.Writer, format, output string, all bool, limit int) (result error) {
	// Release the read view before acquiring the existing writer authority.
	var release func() error
	db := conf.Database
	var e error
	if conf.InstanceRoot != "" {
		v, r, err := lifecycle.AcquireWriterConfig(conf.InstanceRoot, fmt.Sprintf("daily-pid-%d", os.Getpid()))
		if err != nil {
			return err
		}
		release = r
		if v.Mode != "GO_V2" || v.ActiveDatabase != db {
			release()
			return errors.New("INSTANCE_DATABASE_CHANGED")
		}
	} else {
		db, release, e = lifecycle.AcquireDatabaseWriter(db, fmt.Sprintf("daily-pid-%d", os.Getpid()), false)
		if e != nil {
			return e
		}
	}
	defer func() { result = errors.Join(result, release()) }()
	store, e := sqlitev2.Open(ctx, db, false)
	if e != nil {
		return e
	}
	defer store.Close()
	identity, e := store.PublishedResearchIdentity(ctx)
	if e != nil {
		return e
	}
	if !reflect.DeepEqual(stableIdentity(identity), stableIdentity(conf.Identity)) {
		return errors.New("DATABASE_IDENTITY_CHANGED")
	}
	repo := sqlitev2.NewAppRepository(store)
	engine := &native.NativeEngine{SourceHash: source, ConfigHashes: hashes, Progress: log, StorageBackend: "SQLITE_V2", DatabaseIdentitySource: store.PublishedResearchIdentity, Cache: repo, History: func(ctx context.Context, f c.Frozen, b ports.ResearchBudget) (ports.HistoryReader, func(), error) {
		h, e := store.History(ctx, f.Input.HistoryRef, f.Input.HistoryHash, b)
		if e != nil {
			return nil, nil, e
		}
		return h, func() { h.Close() }, nil
	}}
	svc := application.New(repo, engine)
	svc.Calendar = calendar.Official()
	if input != nil {
		in, e := svc.Import(ctx, input)
		if e != nil {
			return e
		}
		if in.Observation != plan.Observation {
			return errors.New("REFRESH_INPUT_OBSERVATION_MISMATCH")
		}
		plan.InputID = in.ID
		plan.Cutoff = in.Cutoff
	}
	raw, _ := json.Marshal(hashes)
	request := c.ResearchRequest{InputID: plan.InputID, Strategy: s, BudgetSeconds: conf.Budget, TargetSession: plan.Target, ConfigIdentity: c.Hash(raw), Implementation: source, EvidencePolicy: Policy}
	identityRaw, _ := json.Marshal(request)
	request.IdempotencyKey = "daily_" + c.Hash(identityRaw)
	j, created, e := svc.Submit(ctx, request)
	if e != nil {
		return e
	}
	if created || j.State == "QUEUED" {
		j, e = svc.Execute(ctx, j.ID)
	}
	if e != nil {
		return fmt.Errorf("DAILY_ATTEMPT_%s_%s: %w", j.ID, j.State, e)
	}
	if j.State != "SUCCEEDED" {
		return fmt.Errorf("DAILY_EXISTING_ATTEMPT_%s_%s_EXPLICIT_ADVANCED_RETRY_REQUIRED", j.ID, j.State)
	}
	reader, e := sqlitev2.OpenReadOnly(ctx, db)
	if e != nil {
		return e
	}
	defer reader.Close()
	return savedOutput(ctx, reader, j.RunID, "", false, all, limit, format, output, out)
}
