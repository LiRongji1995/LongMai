package cli

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/calendar"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/legacy"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/lifecycle"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/native"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/daily"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/httpapi"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"io"
	"os"
	"path/filepath"
	"time"
)

var ConfigHashes = map[string]string{"stage_aware_research_r1.json": "e2f65685814b335fcb92f192e3057da82516f442fdf94bf7ba5e6c2d89b8aa62", "stage_aware_momentum_v2.yaml": "89c03dde925256b6b9822048d4f69703c6ca60ca4a29591f4a4c1d160689ba78", "r05_strategy_experiments.yaml": "8cd729cab31f80ec84d6486c733165360ba264fc3efb2d70c0c69f5a78148462"}

func emit(w io.Writer, v any) error { return json.NewEncoder(w).Encode(v) }

// Ordinary application commands open an existing database. Resolve symlinks
// before selecting its lease name, so aliases share the same writer identity.
func canonicalDatabasePath(path string) (string, error) {
	absolute, e := filepath.Abs(path)
	if e != nil {
		return "", e
	}
	canonical, e := filepath.EvalSymlinks(absolute)
	if e != nil {
		return "", fmt.Errorf("DATABASE_IDENTITY_UNRESOLVED: %w", e)
	}
	return canonical, nil
}

func Run(ctx context.Context, args []string, out, log io.Writer, sourceHash string) (result error) {
	if len(args) == 0 || daily.IsCommand(args[0]) {
		return daily.Run(ctx, args, out, log, sourceHash, ConfigHashes)
	}
	if len(args) == 0 {
		return errors.New("usage: stockfish database|import|research|runs|show|detail|export|verify-run|legacy|jobs|job|cancel|recover|resume|health|readiness|schedule|cache-clear|serve")
	}
	if args[0] == "isolation" {
		return isolation(ctx, args[1:], out, log)
	}
	if args[0] == "database" {
		return database(ctx, args[1:], out, log)
	}
	cmd := args[0]
	flags := flag.NewFlagSet(cmd, flag.ContinueOnError)
	flags.SetOutput(log)
	instanceRoot := flags.String("instance-root", "", "explicit isolated control root")
	db := flags.String("db", "", "explicit V2 authority database")
	input := flags.String("input", "", "frozen input file for import only")
	id := flags.String("id", "", "registered input, run, or job ID")
	iid := flags.String("instrument", "", "instrument ID")
	ns := flags.String("namespace", "", "legacy namespace")
	strategy := flags.String("strategy", c.V2, "explicit strategy identity; R1 is research only")
	key := flags.String("key", "", "idempotency key")
	seconds := flags.Int("budget-seconds", 300, "total research deadline, 1..1800")
	limit := flags.Int("limit", 50, "page size 1..200")
	offset := flags.Int("offset", 0, "page offset")
	dest := flags.String("output", "", "new export file")
	address := flags.String("listen", "127.0.0.1:18081", "isolated loopback listener")
	comparisonHistory := flags.String("comparison-history-db", "", "explicit read-only legacy adapter for controlled comparison")
	noCache := flags.Bool("no-cache", false, "disable disposable fact cache")
	cutoff := flags.String("cutoff", "", "explicit evidence cutoff for migrated input registration")
	selection := flags.String("selection", "all", "saved result selection: all, focus, outcomes, tracking")
	page := flags.Int("page", 1, "saved result summary page")
	if e := flags.Parse(args[1:]); e != nil {
		return e
	}
	if *db != "" {
		resolved, e := canonicalDatabasePath(*db)
		if e != nil {
			return e
		}
		*db = resolved
	}
	if cmd == "research" {
		if *seconds < 1 || *seconds > 1800 {
			return errors.New("BUDGET_MUST_BE_1_TO_1800_SECONDS")
		}
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, time.Duration(*seconds)*time.Second)
		defer cancel()
	}
	if flags.NArg() != 0 {
		return errors.New("UNEXPECTED_POSITIONAL_ARGUMENTS")
	}
	if *instanceRoot != "" {
		conf, release, e := lifecycle.AcquireWriterConfig(*instanceRoot, fmt.Sprintf("stockfish-pid-%d", os.Getpid()))
		if e != nil {
			return e
		}
		defer func() { result = errors.Join(result, release()) }()
		if conf.Mode != "GO_V2" {
			return errors.New("ISOLATED_INSTANCE_NOT_CUT_OVER")
		}
		if *db != "" && *db != conf.ActiveDatabase {
			return errors.New("INSTANCE_DATABASE_CONFLICT")
		}
		*db = conf.ActiveDatabase
		*address = conf.Listen
	} else {
		if *db == "" {
			return errors.New("EXPLICIT_V2_DATABASE_REQUIRED")
		}
		canonical, release, e := lifecycle.AcquireDatabaseWriter(*db, fmt.Sprintf("stockfish-pid-%d", os.Getpid()), false)
		if e != nil {
			return e
		}
		defer func() { result = errors.Join(result, release()) }()
		*db = canonical
	}
	// Store.Open may initialize versioned application/extensions schemas, even
	// for a query. Every CLI path reaching it owns the same database lease.
	store, e := sqlitev2.Open(ctx, *db, false)
	if e != nil {
		return e
	}
	defer store.Close()
	repo := sqlitev2.NewAppRepository(store)
	engine := &native.NativeEngine{SourceHash: sourceHash, ConfigHashes: ConfigHashes, Progress: log, History: func(ctx context.Context, f c.Frozen, b ports.ResearchBudget) (ports.HistoryReader, func(), error) {
		if *comparisonHistory != "" {
			h, e := legacy.OpenHistory(*comparisonHistory, f.Input.HistoryRef, f.Input.HistoryHash, b)
			if e != nil {
				return nil, nil, e
			}
			return h, h.Close, nil
		}
		h, e := store.History(ctx, f.Input.HistoryRef, f.Input.HistoryHash, b)
		if e != nil {
			return nil, nil, e
		}
		return h, func() { h.Close() }, nil
	}}
	engine.StorageBackend = "SQLITE_V2"
	engine.DatabaseIdentity, e = store.ResearchIdentity(ctx)
	if e != nil {
		return e
	}
	engine.DatabaseIdentitySource = store.PublishedResearchIdentity
	if *comparisonHistory != "" {
		engine.StorageBackend = "LEGACY_READ_ONLY"
	}
	if !*noCache {
		engine.Cache = repo
	}
	service := application.New(repo, engine)
	service.Calendar = calendar.Official()
	switch cmd {
	case "bundle-import":
		v, e := store.ImportBundle(ctx, *input)
		if e != nil {
			return e
		}
		return emit(out, map[string]string{"bundle_id": v, "integrity": "VERIFIED", "status": "PUBLISHED"})
	case "bundle-verify":
		if e := store.VerifyBundle(ctx, *id); e != nil {
			return e
		}
		return emit(out, map[string]string{"bundle_id": *id, "integrity": "VERIFIED"})
	case "bundle-export":
		if *dest == "" {
			return errors.New("NEW_BUNDLE_EXPORT_DIRECTORY_REQUIRED")
		}
		if e := store.ExportBundle(ctx, *id, *dest); e != nil {
			return e
		}
		return emit(out, map[string]string{"bundle_id": *id, "export": "VERIFIED"})
	case "import-legacy-input":
		if *cutoff == "" {
			return errors.New("EXPLICIT_ORIGINAL_EVIDENCE_CUTOFF_REQUIRED")
		}
		raw, e := repo.Legacy(ctx, "candidate_inputs", *id)
		if e != nil {
			return e
		}
		wrapped, e := json.Marshal(map[string]any{"input": json.RawMessage(raw), "cutoff": *cutoff, "purpose": "MIGRATED_EXISTING_COLLECTION_BATCH", "parent_input": map[string]string{"namespace": "candidate_inputs", "external_id": *id, "raw_sha256": c.Hash(raw)}})
		if e != nil {
			return e
		}
		v, e := service.Import(ctx, wrapped)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "summary":
		v, e := repo.ResearchSummary(ctx, *id, *strategy, *selection, *page, *limit)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "import":
		f, e := os.Open(*input)
		if e != nil {
			return e
		}
		raw, e := io.ReadAll(io.LimitReader(f, 64<<20+1))
		f.Close()
		if e != nil {
			return e
		}
		if len(raw) > 64<<20 {
			return errors.New("FROZEN_INPUT_SIZE_LIMIT")
		}
		v, e := service.Import(ctx, raw)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "research":
		j, created, e := service.Submit(ctx, c.ResearchRequest{InputID: *id, Strategy: *strategy, BudgetSeconds: *seconds, IdempotencyKey: *key})
		if e != nil {
			return e
		}
		if created || j.State == "QUEUED" {
			j, e = service.Execute(ctx, j.ID)
		}
		if er := emit(out, j); er != nil {
			return er
		}
		return e
	case "runs":
		v, e := repo.Runs(ctx, *limit, *offset)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "show":
		v, e := repo.Run(ctx, *id)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "detail":
		v, e := repo.Item(ctx, *id, *iid)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "verify-run":
		if e = repo.VerifyRun(ctx, *id); e != nil {
			return e
		}
		return emit(out, map[string]any{"run_id": *id, "saved_result_integrity": "VERIFIED"})
	case "export":
		if *dest == "" {
			return service.ExportTo(ctx, *id, out)
		}
		return atomicExport(ctx, *dest, func(w io.Writer) error { return service.ExportTo(ctx, *id, w) })
	case "legacy":
		v, e := repo.Legacy(ctx, *ns, *id)
		if e != nil {
			return e
		}
		if *dest != "" {
			f, e := os.OpenFile(*dest, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if e != nil {
				return e
			}
			_, e = f.Write(v)
			if e == nil {
				e = f.Sync()
			}
			ce := f.Close()
			if e != nil {
				return e
			}
			return ce
		}
		_, e = out.Write(v)
		return e
	case "jobs":
		v, e := repo.Jobs(ctx, "")
		if e != nil {
			return e
		}
		return emit(out, v)
	case "job":
		v, e := repo.Job(ctx, *id)
		if e != nil {
			return e
		}
		events, e := repo.Events(ctx, *id)
		if e != nil {
			return e
		}
		return emit(out, map[string]any{"job": v, "events": events})
	case "cancel":
		v, e := service.Cancel(ctx, *id)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "recover":
		v, e := service.Recover(ctx)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "resume":
		v, _, e := service.Resume(ctx, *id, *key)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "health", "readiness":
		v, e := repo.Ready(ctx)
		if e != nil {
			return e
		}
		if e = emit(out, v); e != nil {
			return e
		}
		if cmd == "readiness" && !v.Ready {
			return errors.New("NOT_READY")
		}
		return nil
	case "schedule":
		v, e := service.SchedulePlan(ctx)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "cache-clear":
		if e = repo.CacheClear(ctx); e != nil {
			return e
		}
		return emit(out, map[string]string{"cache": "CLEARED", "authority": "PRESERVED"})
	case "serve":
		s := httpapi.New(ctx, service, os.Getenv("STOCKFISH_V2_API_TOKEN"))
		defer s.Close()
		return httpapi.Listen(ctx, *address, s)
	default:
		return fmt.Errorf("UNKNOWN_COMMAND: %s", cmd)
	}
}

func database(parent context.Context, args []string, out, log io.Writer) (result error) {
	if len(args) == 0 {
		return errors.New("database inventory|snapshot|migrate|verify|publish")
	}
	command := args[0]
	switch command {
	case "inventory", "snapshot", "migrate", "incremental", "verify", "publish", "project-batches", "verify-batches":
	default:
		return errors.New("UNKNOWN_DATABASE_COMMAND")
	}
	f := flag.NewFlagSet("database "+command, flag.ContinueOnError)
	f.SetOutput(log)
	source := f.String("source", "", "read-only source or frozen source")
	target := f.String("target", "", "new target path")
	seconds := f.Int("budget-seconds", 7200, "migration operation deadline (cumulative ledger still applies)")
	batch := f.Int("batch-size", 250, "bounded rows per transaction")
	if e := f.Parse(args[1:]); e != nil {
		return e
	}
	if *seconds < 1 || *seconds > 7200 {
		return errors.New("MIGRATION_BUDGET_INVALID")
	}
	if f.NArg() != 0 {
		return errors.New("UNEXPECTED_POSITIONAL_ARGUMENTS")
	}
	if command != "inventory" {
		if *target == "" {
			return errors.New("EXPLICIT_MIGRATION_TARGET_REQUIRED")
		}
		allowNew := command == "snapshot" || command == "migrate"
		canonical, release, e := lifecycle.AcquireDatabaseWriter(*target, fmt.Sprintf("stockfish-database-pid-%d", os.Getpid()), allowNew)
		if e != nil {
			return e
		}
		defer func() { result = errors.Join(result, release()) }()
		*target = canonical
	}
	ctx, cancel := context.WithTimeout(parent, time.Duration(*seconds)*time.Second)
	defer cancel()
	options := sqlitev2.Options{BatchSize: *batch, ResourceCheck: func() error { return sqlitev2.CheckResources(filepath.Dir(*target)) }, Progress: func(stage string, n int64) { _ = emit(log, map[string]any{"stage": stage, "records": n}) }}
	switch command {
	case "project-batches", "verify-batches":
		s, e := sqlitev2.Open(ctx, *target, false)
		if e != nil {
			return e
		}
		defer s.Close()
		var report sqlitev2.BatchProjectionReport
		if command == "project-batches" {
			report, e = s.BackfillBatchProjections(ctx, sqlitev2.BatchProjectionOptions{BatchSize: *batch, ResourceCheck: options.ResourceCheck, Progress: options.Progress})
		} else {
			report, e = s.VerifyBatchProjections(ctx)
		}
		if er := emit(out, report); er != nil {
			return er
		}
		return e
	case "inventory":
		v, e := sqlitev2.InventorySource(ctx, *source)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "snapshot":
		v, e := sqlitev2.Snapshot(ctx, *source, *target)
		if e != nil {
			return e
		}
		return emit(out, v)
	case "incremental":
		v, e := sqlitev2.Incremental(ctx, *source, *target, options)
		if er := emit(out, v); er != nil {
			return er
		}
		return e
	case "migrate":
		v, e := sqlitev2.Migrate(ctx, *source, *target, options)
		if er := emit(out, v); er != nil {
			return er
		}
		return e
	case "verify":
		v, e := sqlitev2.Verify(ctx, *source, *target, options)
		if er := emit(out, v); er != nil {
			return er
		}
		return e
	case "publish":
		if e := sqlitev2.Publish(ctx, *target); e != nil {
			return e
		}
		return emit(out, map[string]string{"migration": "PUBLISHED"})
	default:
		return errors.New("UNKNOWN_DATABASE_COMMAND")
	}
}
