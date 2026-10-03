// Package lifecycle operates only explicitly marked isolated instances. It never
// modifies a production service or the frozen source database.
package lifecycle

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
)

const Version = "ISOLATED_LIFECYCLE_V1"
const MinimumReserve int64 = 10 << 30
const MaximumPersistent int64 = 50 << 30

var availableBytes = sqlitev2.AvailableBytes

type Config struct {
	Version          string `json:"version"`
	IsolationID      string `json:"isolation_id"`
	Source           string `json:"source_snapshot"`
	SourceHash       string `json:"source_sha256"`
	Target           string `json:"target_database"`
	SchemaChecksum   string `json:"schema_checksum"`
	VerificationHash string `json:"verification_hash"`
	Mode             string `json:"mode"`
	ActiveDatabase   string `json:"active_database"`
	DefaultStrategy  string `json:"default_strategy"`
	Listen           string `json:"listen"`
	Scheduler        string `json:"scheduler"`
	CutoverSequence  int64  `json:"cutover_sequence"`
	Revision         int    `json:"revision"`
	LastExport       string `json:"last_export,omitempty"`
	LegacyReplayHash string `json:"legacy_replay_sha256,omitempty"`
	LastEvent        string `json:"last_event"`
}
type marker struct{ Version, IsolationID string }
type PreflightReport struct {
	Status                string   `json:"status"`
	SourceSHA256          string   `json:"source_sha256"`
	TargetIdentity        string   `json:"target_identity"`
	Schema                int      `json:"schema_version"`
	ApplicationSchema     int      `json:"application_schema"`
	FreeBytes             int64    `json:"free_bytes"`
	PersistentBytes       int64    `json:"persistent_bytes"`
	UnfinishedJobs        int      `json:"unfinished_jobs"`
	UnpublishedRuns       int      `json:"unpublished_runs"`
	VerifiedAbandonedRuns int      `json:"verified_abandoned_runs"`
	LedgerSequence        int64    `json:"ledger_sequence"`
	DefaultStrategy       string   `json:"default_strategy"`
	Synchronization       string   `json:"synchronization"`
	Checks                []string `json:"checks"`
}
type Change struct {
	Sequence int64  `json:"sequence"`
	Kind     string `json:"kind"`
	ID       string `json:"entity_id"`
	Hash     string `json:"content_hash"`
	At       string `json:"event_at"`
}
type ExportManifest struct {
	Version           string   `json:"version"`
	IsolationID       string   `json:"isolation_id"`
	SourceHash        string   `json:"source_sha256"`
	Backup            string   `json:"backup"`
	BackupHash        string   `json:"backup_sha256"`
	BackupBytes       int64    `json:"backup_bytes"`
	Ledger            string   `json:"ledger_file"`
	LedgerHash        string   `json:"ledger_sha256"`
	From              int64    `json:"after_sequence"`
	Through           int64    `json:"through_sequence"`
	Changes           int64    `json:"change_count"`
	IncompatibleKinds []string `json:"legacy_incompatible_kinds"`
	CompletedAt       string   `json:"completed_at"`
}
type RollbackResult struct {
	Status   string            `json:"status"`
	Export   string            `json:"export"`
	Replay   string            `json:"replay_database,omitempty"`
	Mappings map[string]string `json:"input_id_mappings,omitempty"`
	Reason   string            `json:"reason,omitempty"`
}

func sum(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func randomID() string {
	b := make([]byte, 16)
	if _, e := rand.Read(b); e != nil {
		panic(e)
	}
	return hex.EncodeToString(b)
}
func now() string { return time.Now().UTC().Format(time.RFC3339Nano) }
func inside(root, path string) (string, error) {
	root, e := filepath.Abs(root)
	if e != nil {
		return "", e
	}
	p, e := filepath.Abs(path)
	if e != nil {
		return "", e
	}
	rel, e := filepath.Rel(root, p)
	if e != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return "", errors.New("ISOLATION_PATH_ESCAPE")
	}
	// Do not allow symlink components to redirect writes outside the marked root.
	cursor := p
	for {
		st, er := os.Lstat(cursor)
		if er == nil && st.Mode()&os.ModeSymlink != 0 {
			return "", errors.New("ISOLATION_SYMLINK_FORBIDDEN")
		}
		if er != nil && !errors.Is(er, os.ErrNotExist) {
			return "", er
		}
		if cursor == root {
			break
		}
		next := filepath.Dir(cursor)
		if next == cursor {
			return "", errors.New("ISOLATION_ROOT_INVALID")
		}
		cursor = next
	}
	return p, nil
}
func writeJSON(path string, v any) error {
	b, e := json.MarshalIndent(v, "", "  ")
	if e != nil {
		return e
	}
	b = append(b, '\n')
	return atomicWrite(path, b)
}
func atomicWrite(path string, b []byte) error {
	f, e := os.CreateTemp(filepath.Dir(path), ".publish-")
	if e != nil {
		return e
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if e = f.Chmod(0600); e == nil {
		_, e = f.Write(b)
	}
	if e == nil {
		e = f.Sync()
	}
	closeErr := f.Close()
	if e == nil {
		e = closeErr
	}
	if e != nil {
		return e
	}
	if e = os.Rename(tmp, path); e != nil {
		return e
	}
	if runtime.GOOS != "windows" {
		d, er := os.Open(filepath.Dir(path))
		if er != nil {
			return er
		}
		defer d.Close()
		return d.Sync()
	}
	return nil
}
func Load(root string) (Config, error) {
	var conf Config
	raw, e := os.ReadFile(filepath.Join(root, ".stockfish-isolated.json"))
	if e != nil {
		return conf, e
	}
	var m marker
	if e = json.Unmarshal(raw, &m); e != nil {
		return conf, e
	}
	raw, e = os.ReadFile(filepath.Join(root, "instance.json"))
	if e != nil {
		return conf, e
	}
	if e = json.Unmarshal(raw, &conf); e != nil {
		return conf, e
	}
	if m.Version != Version || conf.Version != Version || m.IsolationID == "" || m.IsolationID != conf.IsolationID {
		return conf, errors.New("ISOLATION_MARKER_MISMATCH")
	}
	for _, p := range []string{conf.Source, conf.Target, conf.ActiveDatabase} {
		if _, e = inside(root, p); e != nil {
			return conf, e
		}
	}
	host, _, e := net.SplitHostPort(conf.Listen)
	if e != nil {
		return conf, e
	}
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() {
		return conf, errors.New("LOOPBACK_LISTENER_REQUIRED")
	}
	if conf.Scheduler != "PAUSED" || conf.DefaultStrategy != c.V2 {
		return conf, errors.New("ISOLATED_DEFAULT_OR_SCHEDULER_CHANGED")
	}
	if conf.Mode == "GO_V2" && conf.ActiveDatabase != conf.Target {
		return conf, errors.New("GO_ACTIVE_DATABASE_TARGET_MISMATCH")
	}
	return conf, nil
}

func readDB(ctx context.Context, path string) (*sql.DB, error) {
	db, e := sql.Open("sqlite", sqlitev2.URI(path, true))
	if e != nil {
		return nil, e
	}
	db.SetMaxOpenConns(1)
	if e = db.PingContext(ctx); e != nil {
		db.Close()
		return nil, e
	}
	return db, nil
}
func metadata(ctx context.Context, db *sql.DB) (string, string, string, error) {
	if e := sqlitev2.VerifySchemaDB(ctx, db); e != nil {
		return "", "", "", e
	}
	if e := sqlitev2.VerifyApplicationSchemaDB(ctx, db); e != nil {
		return "", "", "", e
	}
	if e := sqlitev2.VerifyBatchProjectionReadinessDB(ctx, db); e != nil {
		return "", "", "", e
	}
	var source, status, verification, checksum string
	if e := db.QueryRowContext(ctx, "SELECT source_sha256,status,verification FROM migration_state WHERE id=1").Scan(&source, &status, &verification); e != nil {
		return "", "", "", e
	}
	if status != "PUBLISHED" {
		return "", "", "", errors.New("MIGRATION_NOT_PUBLISHED")
	}
	var v sqlitev2.Verification
	if e := json.Unmarshal([]byte(verification), &v); e != nil {
		return "", "", "", e
	}
	if v.Version != sqlitev2.VerificationVersion || v.SourceSHA256 != source || v.Integrity != "ok" || v.ForeignKeys != 0 || len(v.Errors) > 0 || v.CompletedAt == "" {
		return "", "", "", errors.New("MIGRATION_VERIFICATION_INVALID")
	}
	if e := db.QueryRowContext(ctx, "SELECT checksum FROM schema_versions WHERE version=?", sqlitev2.SchemaVersion).Scan(&checksum); e != nil {
		return "", "", "", e
	}
	if checksum != sqlitev2.ExpectedSchemaChecksum() {
		return "", "", "", errors.New("COMPILED_SCHEMA_CHECKSUM_MISMATCH")
	}
	return source, checksum, sum([]byte(verification)), nil
}

// Init binds an existing frozen snapshot and migrated target to a new isolated
// control file. It does not start, stop, or alter any process.
func Init(ctx context.Context, root, source, target, listen string) (Config, error) {
	var conf Config
	var e error
	if _, e = os.Stat(filepath.Join(root, ".stockfish-isolated.json")); !errors.Is(e, os.ErrNotExist) {
		return conf, errors.New("ISOLATION_ALREADY_EXISTS")
	}
	if source, e = inside(root, source); e != nil {
		return conf, e
	}
	if target, e = inside(root, target); e != nil {
		return conf, e
	}
	if source == target {
		return conf, errors.New("SOURCE_TARGET_MUST_DIFFER")
	}
	host, _, e := net.SplitHostPort(listen)
	if e != nil {
		return conf, e
	}
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() {
		return conf, errors.New("LOOPBACK_LISTENER_REQUIRED")
	}
	ident, e := sqlitev2.SnapshotCheck(ctx, source)
	if e != nil {
		return conf, e
	}
	db, e := readDB(ctx, target)
	if e != nil {
		return conf, e
	}
	defer db.Close()
	sourceHash, checksum, verification, e := metadata(ctx, db)
	if e != nil {
		return conf, e
	}
	if sourceHash != ident.SHA256 {
		return conf, errors.New("SOURCE_TARGET_IDENTITY_MISMATCH")
	}
	conf = Config{Version: Version, IsolationID: randomID(), Source: source, SourceHash: sourceHash, Target: target, SchemaChecksum: checksum, VerificationHash: verification, Mode: "LEGACY_FROZEN", ActiveDatabase: source, DefaultStrategy: c.V2, Listen: listen, Scheduler: "PAUSED", LastEvent: "ISOLATED_INSTANCE_INITIALIZED"}
	if e = writeJSON(filepath.Join(root, "instance.json"), conf); e != nil {
		return conf, e
	}
	if e = writeJSON(filepath.Join(root, ".stockfish-isolated.json"), marker{Version, conf.IsolationID}); e != nil {
		return conf, e
	}
	return conf, nil
}
func diskUsage(ctx context.Context, root string) (int64, error) {
	var bytes int64
	e := filepath.WalkDir(root, func(path string, d os.DirEntry, e error) error {
		if e != nil {
			return e
		}
		if e = ctx.Err(); e != nil {
			return e
		}
		if d.Type()&os.ModeSymlink != 0 {
			return errors.New("ISOLATION_SYMLINK_FORBIDDEN")
		}
		if !d.IsDir() {
			s, e := d.Info()
			if e != nil {
				return e
			}
			bytes += s.Size()
		}
		return nil
	})
	return bytes, e
}
func preflight(ctx context.Context, root string, owned bool) (PreflightReport, error) {
	var out PreflightReport
	conf, e := Load(root)
	if e != nil {
		return out, e
	}
	if !owned {
		for _, lock := range []string{filepath.Join(root, "writer.lock"), conf.Target + ".go-writer"} {
			if _, e = os.Lstat(lock); e == nil {
				return out, errors.New("WRITER_OWNERSHIP_BUSY")
			}
			if !errors.Is(e, os.ErrNotExist) {
				return out, e
			}
		}
	} else if e = verifyOwnedWriter(root, conf.Target); e != nil {
		return out, e
	}

	ident, e := sqlitev2.SnapshotCheck(ctx, conf.Source)
	if e != nil {
		return out, e
	}
	if ident.SHA256 != conf.SourceHash {
		return out, errors.New("FROZEN_SOURCE_CHANGED")
	}
	if conf.Mode == "LEGACY_REPLAYED" {
		replay, e := sqlitev2.OpenFrozenAtHash(ctx, conf.ActiveDatabase, conf.LegacyReplayHash)
		if e != nil {
			return out, fmt.Errorf("UNHANDLED_LEGACY_INCREMENTAL_CHANGES: %w", e)
		}
		if e = replay.Close(); e != nil {
			return out, e
		}
	}

	db, e := readDB(ctx, conf.Target)
	if e != nil {
		return out, e
	}
	defer db.Close()
	source, checksum, verification, e := metadata(ctx, db)
	if e != nil {
		return out, e
	}
	if source != conf.SourceHash || checksum != conf.SchemaChecksum || verification != conf.VerificationHash {
		return out, errors.New("TARGET_IDENTITY_CHANGED")
	}
	if e = db.QueryRowContext(ctx, "PRAGMA user_version").Scan(&out.Schema); e != nil {
		return out, e
	}
	if e = db.QueryRowContext(ctx, "SELECT version FROM app_schema").Scan(&out.ApplicationSchema); e != nil {
		return out, e
	}
	if out.Schema != sqlitev2.SchemaVersion || out.ApplicationSchema != c.SchemaVersion {
		return out, errors.New("SCHEMA_INCOMPATIBLE")
	}
	if e = db.QueryRowContext(ctx, "SELECT count(*) FROM app_jobs WHERE state IN ('QUEUED','RUNNING')").Scan(&out.UnfinishedJobs); e != nil {
		return out, e
	}
	if e = db.QueryRowContext(ctx, "SELECT count(*) FROM app_runs WHERE state='PREPARING'").Scan(&out.UnpublishedRuns); e != nil {
		return out, e
	}
	verified, e := verifyAbandonedRuns(ctx, db)
	if e != nil {
		return out, e
	}
	out.VerifiedAbandonedRuns = verified
	out.UnpublishedRuns -= verified
	if out.UnpublishedRuns < 0 {
		return out, errors.New("ABANDONED_RUN_COUNT_MISMATCH")
	}
	if out.UnfinishedJobs > 0 || out.UnpublishedRuns > 0 {
		return out, errors.New("UNFINISHED_WORK_REQUIRES_EXPLICIT_RECOVERY")
	}
	if e = verifyAuthorityLedger(ctx, db); e != nil {
		return out, e
	}
	if e = db.QueryRowContext(ctx, "SELECT coalesce(max(sequence),0) FROM app_change_ledger").Scan(&out.LedgerSequence); e != nil {
		return out, e
	}
	if out.FreeBytes, e = availableBytes(root); e != nil {
		return out, e
	}
	if out.PersistentBytes, e = diskUsage(ctx, root); e != nil {
		return out, e
	}
	if out.FreeBytes < MinimumReserve {
		return out, errors.New("SPACE_RESERVE_EXHAUSTED")
	}
	if out.PersistentBytes > MaximumPersistent {
		return out, errors.New("PERSISTENT_SPACE_LIMIT_EXCEEDED")
	}
	out.Status = "READY_FOR_ISOLATED_CUTOVER"
	out.SourceSHA256 = source
	out.TargetIdentity = sum([]byte(source + "\x00" + checksum + "\x00" + verification))
	out.DefaultStrategy = conf.DefaultStrategy
	out.Synchronization = "EXACT_FROZEN_SNAPSHOT_ONLY; LIVE_SOURCE_NOT_CLAIMED"
	out.Checks = []string{"isolation marker and path bounds", "loopback; scheduler paused; V2 default", "source frozen bytes and identity", "published migration and verification", "database and application schema", "single writer lease", "no unfinished jobs or unresolved unpublished runs; explicit abandoned prefixes verified", "space reserve and cumulative persistent budget"}
	return out, nil
}
func Preflight(ctx context.Context, root string) (PreflightReport, error) {
	return preflight(ctx, root, false)
}
func saveEvent(root string, conf *Config, event string) error {
	conf.Revision++
	conf.LastEvent = event
	return writeJSON(filepath.Join(root, "instance.json"), conf)
}
func Cutover(ctx context.Context, root string) (Config, error) {
	conf, release, e := AcquireWriterConfig(root, "isolated-cutover")
	if e != nil {
		return conf, e
	}
	defer release()
	if conf.Mode != "LEGACY_FROZEN" && conf.Mode != "LEGACY_REPLAYED" {
		return conf, errors.New("CUTOVER_REQUIRES_LEGACY_MODE")
	}
	report, e := preflight(ctx, root, true)
	if e != nil {
		return conf, e
	}
	conf.CutoverSequence = report.LedgerSequence
	conf.Mode = "GO_V2"
	conf.ActiveDatabase = conf.Target
	e = saveEvent(root, &conf, "ISOLATED_CUTOVER_TO_GO")
	return conf, e
}

func reserveBackup(ctx context.Context, root, path string) error {
	size, e := os.Stat(path)
	if e != nil {
		return e
	}
	needed := size.Size()
	if st, e := os.Stat(path + "-wal"); e == nil {
		needed += st.Size()
	}
	free, e := availableBytes(root)
	if e != nil {
		return e
	}
	used, e := diskUsage(ctx, root)
	if e != nil {
		return e
	}
	if free-needed < MinimumReserve {
		return errors.New("BACKUP_SPACE_RESERVE_EXHAUSTED")
	}
	if used+needed > MaximumPersistent {
		return errors.New("BACKUP_PERSISTENT_SPACE_LIMIT")
	}
	return nil
}
func changesPage(ctx context.Context, db *sql.DB, after int64) ([]Change, error) {
	rows, e := db.QueryContext(ctx, "SELECT sequence,kind,entity_id,content_hash,event_at FROM app_change_ledger WHERE sequence>? ORDER BY sequence LIMIT 100", after)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []Change{}
	for rows.Next() {
		var ch Change
		if e = rows.Scan(&ch.Sequence, &ch.Kind, &ch.ID, &ch.Hash, &ch.At); e != nil {
			return nil, e
		}
		out = append(out, ch)
	}
	return out, rows.Err()
}
func verifyChange(ctx context.Context, db *sql.DB, ch Change) error {
	var raw []byte
	var h string
	switch ch.Kind {
	case sqlitev2.BundleChangeKind:
		return sqlitev2.VerifyBundleChange(ctx, db, ch.ID, ch.Hash)
	case "INPUT":
		if e := db.QueryRowContext(ctx, "SELECT raw_hash,payload FROM app_inputs WHERE input_id=?", ch.ID).Scan(&h, &raw); e != nil {
			return e
		}
		if h != ch.Hash || sum(raw) != h {
			return errors.New("INPUT_LEDGER_HASH_MISMATCH")
		}
		if _, e := evidence.DecodeFrozenContext(ctx, raw); e != nil {
			return e
		}
	case "JOB":
		if e := db.QueryRowContext(ctx, "SELECT request_hash,request FROM app_jobs WHERE job_id=?", ch.ID).Scan(&h, &raw); e != nil {
			return e
		}
		if h != ch.Hash || sum(raw) != h {
			return errors.New("JOB_LEDGER_HASH_MISMATCH")
		}
	case "JOB_EVENT":
		at := strings.LastIndex(ch.ID, ":")
		if at < 1 {
			return errors.New("JOB_EVENT_ID_INVALID")
		}
		var revision int
		var state, reason, eventAt string
		if e := db.QueryRowContext(ctx, "SELECT revision,state,reason,event_at FROM app_job_events WHERE job_id=? AND revision=?", ch.ID[:at], ch.ID[at+1:]).Scan(&revision, &state, &reason, &eventAt); e != nil {
			return e
		}
		raw, _ = json.Marshal(map[string]any{"job_id": ch.ID[:at], "revision": revision, "state": state, "reason": reason, "event_at": eventAt})
		if sum(raw) != ch.Hash {
			return errors.New("JOB_EVENT_LEDGER_HASH_MISMATCH")
		}
	case "ABANDONED_RUN":
		if e := db.QueryRowContext(ctx, "SELECT content_hash FROM app_change_ledger WHERE kind='ABANDONED_RUN' AND entity_id=?", ch.ID).Scan(&h); e != nil {
			return e
		}
		if h != ch.Hash {
			return errors.New("ABANDONED_RUN_LEDGER_HASH_MISMATCH")
		}
		if e := sqlitev2.NewReadOnlyAppRepository(db).VerifyAbandonedRun(ctx, ch.ID); e != nil {
			return e
		}
	case "RUN":
		if e := db.QueryRowContext(ctx, "SELECT members_hash FROM app_runs WHERE run_id=? AND state='PUBLISHED'", ch.ID).Scan(&h); e != nil {
			return e
		}
		if h != ch.Hash {
			return errors.New("RUN_LEDGER_HASH_MISMATCH")
		}
		if e := sqlitev2.NewReadOnlyAppRepository(db).VerifyRun(ctx, ch.ID); e != nil {
			return e
		}
	default:
		return fmt.Errorf("UNKNOWN_CHANGE_LEDGER_KIND: %s", ch.Kind)
	}
	return ctx.Err()
}
func exportChanges(ctx context.Context, root string, conf *Config) (ExportManifest, error) {
	var manifest ExportManifest
	dir := filepath.Join(root, "changes-"+randomID())
	if e := os.Mkdir(dir, 0700); e != nil {
		return manifest, e
	}
	backup := filepath.Join(dir, "v2-recovery.db")
	identity, e := isolatedSnapshot(ctx, root, conf.Target, backup, true)
	if e != nil {
		return manifest, e
	}
	frozen, e := sqlitev2.OpenFrozen(ctx, backup)
	if e != nil {
		return manifest, e
	}
	defer frozen.Close()
	db := frozen.DB
	source, checksum, verification, e := metadata(ctx, db)
	if e != nil {
		return manifest, e
	}
	if source != conf.SourceHash || checksum != conf.SchemaChecksum || verification != conf.VerificationHash {
		return manifest, errors.New("EXPORTED_TARGET_IDENTITY_MISMATCH")
	}
	// Native clone already completed these full scans before publishing its identity.
	if identity.Version != "ISOLATED_APFS_CLONEFILE_V1" {
		var integrity string
		if e = db.QueryRowContext(ctx, "PRAGMA integrity_check").Scan(&integrity); e != nil {
			return manifest, e
		}
		if integrity != "ok" {
			return manifest, errors.New("EXPORTED_DATABASE_CORRUPT")
		}
		var foreignKeys int
		if e = db.QueryRowContext(ctx, "SELECT count(*) FROM pragma_foreign_key_check").Scan(&foreignKeys); e != nil {
			return manifest, e
		}
		if foreignKeys != 0 {
			return manifest, errors.New("EXPORTED_FOREIGN_KEY_FAILURE")
		}
	}

	if e = verifyAuthorityLedger(ctx, db); e != nil {
		return manifest, e
	}
	ledger := filepath.Join(dir, "changes.jsonl")
	f, e := os.OpenFile(ledger, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return manifest, e
	}
	defer f.Close()
	hasher := sha256.New()
	writer := io.MultiWriter(f, hasher)
	manifest = ExportManifest{Version: Version, IsolationID: conf.IsolationID, SourceHash: conf.SourceHash, Backup: backup, BackupHash: identity.SHA256, BackupBytes: identity.Bytes, Ledger: ledger, From: conf.CutoverSequence, Through: conf.CutoverSequence, IncompatibleKinds: []string{}}
	kinds := map[string]bool{}
	for {
		page, e := changesPage(ctx, db, manifest.Through)
		if e != nil {
			return manifest, e
		}
		if len(page) == 0 {
			break
		}
		for _, ch := range page {
			if e = verifyChange(ctx, db, ch); e != nil {
				return manifest, e
			}
			if e = json.NewEncoder(writer).Encode(ch); e != nil {
				return manifest, e
			}
			manifest.Through = ch.Sequence
			manifest.Changes++
			if ch.Kind != "INPUT" {
				kinds[ch.Kind] = true
			}
		}
	}
	if e = f.Sync(); e != nil {
		return manifest, e
	}
	if e = f.Close(); e != nil {
		return manifest, e
	}
	manifest.LedgerHash = hex.EncodeToString(hasher.Sum(nil))
	for kind := range kinds {
		manifest.IncompatibleKinds = append(manifest.IncompatibleKinds, kind)
	}
	sort.Strings(manifest.IncompatibleKinds)
	manifest.CompletedAt = now()
	manifestPath := filepath.Join(dir, "completion.json")
	if e = writeJSON(manifestPath, manifest); e != nil {
		return manifest, e
	}
	conf.LastExport = manifestPath
	if e = saveEvent(root, conf, "DELTA_AND_RECOVERY_BACKUP_PUBLISHED"); e != nil {
		return manifest, e
	}
	return manifest, nil
}
func ExportChanges(ctx context.Context, root string) (ExportManifest, error) {
	conf, release, e := AcquireWriterConfig(root, "isolated-change-export")
	if e != nil {
		return ExportManifest{}, e
	}
	defer release()
	return exportChanges(ctx, root, &conf)
}
func VerifyExport(ctx context.Context, root, path string) (ExportManifest, error) {
	var out ExportManifest
	conf, e := Load(root)
	if e != nil {
		return out, e
	}
	if _, e = inside(root, path); e != nil {
		return out, e
	}
	raw, e := os.ReadFile(path)
	if e != nil {
		return out, e
	}
	if e = json.Unmarshal(raw, &out); e != nil {
		return out, e
	}
	if out.Version != Version || out.IsolationID != conf.IsolationID || out.SourceHash != conf.SourceHash || out.CompletedAt == "" {
		return out, errors.New("EXPORT_IDENTITY_MISMATCH")
	}
	if _, e = inside(root, out.Backup); e != nil {
		return out, e
	}
	if _, e = inside(root, out.Ledger); e != nil {
		return out, e
	}
	frozen, e := sqlitev2.OpenFrozen(ctx, out.Backup)
	if e != nil {
		return out, e
	}
	defer frozen.Close()
	identity := frozen.Identity
	if identity.SHA256 != out.BackupHash || identity.Bytes != out.BackupBytes {
		return out, errors.New("EXPORT_BACKUP_HASH_MISMATCH")
	}
	hash, e := sqlitev2.FileHash(ctx, out.Ledger)
	if e != nil {
		return out, e
	}
	if hash != out.LedgerHash {
		return out, errors.New("EXPORT_LEDGER_HASH_MISMATCH")
	}
	db := frozen.DB
	if e = verifyAuthorityLedger(ctx, db); e != nil {
		return out, e
	}
	var max int64
	if e = db.QueryRowContext(ctx, "SELECT coalesce(max(sequence),0) FROM app_change_ledger").Scan(&max); e != nil {
		return out, e
	}
	if max != out.Through {
		return out, errors.New("EXPORT_LEDGER_BOUNDARY_MISMATCH")
	}
	f, e := os.Open(out.Ledger)
	if e != nil {
		return out, e
	}
	defer f.Close()
	decoder := json.NewDecoder(f)
	last := out.From
	var n int64
	for {
		var ch Change
		e = decoder.Decode(&ch)
		if e == io.EOF {
			break
		}
		if e != nil {
			return out, e
		}
		if ch.Sequence <= last {
			return out, errors.New("EXPORT_LEDGER_ORDER_INVALID")
		}
		var dbch Change
		if e = db.QueryRowContext(ctx, "SELECT sequence,kind,entity_id,content_hash,event_at FROM app_change_ledger WHERE sequence=?", ch.Sequence).Scan(&dbch.Sequence, &dbch.Kind, &dbch.ID, &dbch.Hash, &dbch.At); e != nil {
			return out, e
		}
		if ch != dbch {
			return out, errors.New("EXPORT_LEDGER_RECORD_MISMATCH")
		}
		if e = verifyChange(ctx, db, ch); e != nil {
			return out, e
		}
		last = ch.Sequence
		n++
	}
	var expected int64
	if e = db.QueryRowContext(ctx, "SELECT count(*) FROM app_change_ledger WHERE sequence>?", out.From).Scan(&expected); e != nil {
		return out, e
	}
	if last != out.Through || n != out.Changes || n != expected {
		return out, errors.New("EXPORT_LEDGER_MEMBERS_MISMATCH")
	}
	return out, nil
}
func putLegacy(ctx context.Context, db *sql.DB, namespace, id string, raw []byte) error {
	tx, e := db.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	h := sum(raw)
	var existing string
	var stored []byte
	e = tx.QueryRowContext(ctx, "SELECT content_hash,payload FROM documents WHERE namespace=? AND document_id=?", namespace, id).Scan(&existing, &stored)
	if e == nil {
		if existing != h || sum(stored) != existing {
			return errors.New("LEGACY_REPLAY_ID_CONTENT_CONFLICT")
		}
		return nil
	}
	if !errors.Is(e, sql.ErrNoRows) {
		return e
	}
	if _, e = tx.ExecContext(ctx, "INSERT INTO documents(namespace,document_id,payload,content_hash,created_at) VALUES(?,?,?,?,?)", namespace, id, string(raw), h, now()); e != nil {
		return e
	}
	return tx.Commit()
}
func replayInputs(ctx context.Context, root string, conf Config, export ExportManifest) (string, map[string]string, error) {
	mappings := map[string]string{}
	path := filepath.Join(root, "legacy-replay-"+randomID()+".db")
	if _, e := isolatedSnapshot(ctx, root, conf.Source, path, false); e != nil {
		return "", mappings, e
	}
	if e := os.Rename(path+".snapshot.json", path+".seed-snapshot.json"); e != nil {
		return "", mappings, e
	}
	old, e := sql.Open("sqlite", sqlitev2.URI(path, false))
	if e != nil {
		return "", mappings, e
	}
	defer old.Close()
	view, e := sqlitev2.OpenFrozen(ctx, export.Backup)
	if e != nil {
		return "", mappings, e
	}
	defer view.Close()
	if view.Identity.SHA256 != export.BackupHash {
		return "", mappings, errors.New("EXPORT_BACKUP_HASH_MISMATCH")
	}
	fresh := view.DB
	after := export.From
	for {
		page, e := changesPage(ctx, fresh, after)
		if e != nil {
			return "", mappings, e
		}
		if len(page) == 0 {
			break
		}
		for _, ch := range page {
			if ch.Kind != "INPUT" {
				return "", mappings, errors.New("GO_ONLY_DATA_NOT_REPLAYABLE")
			}
			var raw []byte
			if e = fresh.QueryRowContext(ctx, "SELECT payload FROM app_inputs WHERE input_id=?", ch.ID).Scan(&raw); e != nil {
				return "", mappings, e
			}
			f, e := evidence.DecodeFrozenContext(ctx, raw)
			if e != nil {
				return "", mappings, e
			}
			if f.Input.HistoryRef != "" {
				var count int
				if e = old.QueryRowContext(ctx, "SELECT count(*) FROM documents WHERE namespace='history_artifact_manifests' AND document_id=? AND content_hash=?", f.Input.HistoryRef, f.Input.HistoryHash).Scan(&count); e != nil {
					return "", mappings, e
				}
				if count != 1 {
					return "", mappings, errors.New("NEW_HISTORY_NOT_REPLAYABLE_TO_LEGACY")
				}
			}
			projection, e := evidence.CanonicalInputProjection(ctx, f)
			if e != nil {
				return "", mappings, e
			}
			legacyID := "market_" + sum(projection)
			if e = putLegacy(ctx, old, "candidate_inputs", legacyID, projection); e != nil {
				return "", mappings, e
			}
			index, _ := json.Marshal(map[string]any{"input_id": legacyID, "observation_date": f.Input.Observation, "coverage_type": f.Input.Coverage, "max_session": f.Input.Sessions[len(f.Input.Sessions)-1]})
			index, e = c.CanonicalContext(ctx, index)
			if e != nil {
				return "", mappings, e
			}
			if e = putLegacy(ctx, old, "candidate_input_indexes", legacyID, index); e != nil {
				return "", mappings, e
			}
			mapping, _ := json.Marshal(map[string]any{"go_input_id": ch.ID, "legacy_input_id": legacyID, "frozen_raw_sha256": ch.Hash, "cutoff": f.Cutoff, "conversion": evidence.InputContractVersion})
			mapping, e = c.CanonicalContext(ctx, mapping)
			if e != nil {
				return "", mappings, e
			}
			if e = putLegacy(ctx, old, "go_v2_input_mappings", ch.ID, mapping); e != nil {
				return "", mappings, e
			}
			archived, e := c.CanonicalContext(ctx, raw)
			if e != nil {
				return "", mappings, e
			}
			if e = putLegacy(ctx, old, "go_v2_frozen_input_archives", ch.ID, archived); e != nil {
				return "", mappings, e
			}
			mappings[ch.ID] = legacyID
			after = ch.Sequence
		}
	}
	var integrity string
	if e = old.QueryRowContext(ctx, "PRAGMA integrity_check").Scan(&integrity); e != nil {
		return "", mappings, e
	}
	if integrity != "ok" {
		return "", mappings, errors.New("LEGACY_REPLAY_INTEGRITY_FAILURE")
	}
	if e = old.Close(); e != nil {
		return "", mappings, e
	}
	hash, e := sqlitev2.FileHash(ctx, path)
	if e != nil {
		return "", mappings, e
	}
	if e = writeJSON(path+".replay.json", map[string]any{"version": Version, "source_sha256": conf.SourceHash, "replay_sha256": hash, "input_mappings": mappings, "export": conf.LastExport, "completed_at": now()}); e != nil {
		return "", mappings, e
	}
	return path, mappings, nil
}
func Rollback(ctx context.Context, root string) (RollbackResult, error) {
	out := RollbackResult{}
	conf, release, e := AcquireWriterConfig(root, "isolated-rollback")
	if e != nil {
		return out, e
	}
	defer release()
	if conf.Mode != "GO_V2" {
		return out, errors.New("ROLLBACK_REQUIRES_GO_MODE")
	}
	manifest, e := exportChanges(ctx, root, &conf)
	if e != nil {
		return out, e
	}
	out.Export = conf.LastExport
	if len(manifest.IncompatibleKinds) > 0 {
		out.Status = "BLOCKED_UNREPRESENTABLE"
		out.Reason = "Go-only results or task history cannot be represented by legacy semantics; V2 database and verified recovery backup retained"
		if e = saveEvent(root, &conf, "ROLLBACK_BLOCKED_GO_ONLY_DATA_PRESERVED"); e != nil {
			return out, e
		}
		return out, nil
	}
	if _, e = preflight(ctx, root, true); e != nil {
		return out, e
	}
	path, mappings, e := replayInputs(ctx, root, conf, manifest)
	if e != nil {
		return out, e
	}
	conf.Mode = "LEGACY_REPLAYED"
	conf.ActiveDatabase = path
	conf.LegacyReplayHash, e = sqlitev2.FileHash(ctx, path)
	if e != nil {
		return out, e
	}
	if e = saveEvent(root, &conf, "ISOLATED_ROLLBACK_WITH_INPUT_REPLAY"); e != nil {
		return out, e
	}
	out.Status = "ROLLED_BACK_WITH_REPLAY"
	out.Replay = path
	out.Mappings = mappings
	return out, nil
}

// Restore recovers an exported V2 database into a fresh file. Pending tasks are
// explicitly interrupted and never automatically re-executed.
func Restore(ctx context.Context, root, exportPath string) (Config, error) {
	conf, release, e := AcquireWriterConfig(root, "isolated-restore")
	if e != nil {
		return conf, e
	}
	defer release()
	manifest, e := VerifyExport(ctx, root, exportPath)
	if e != nil {
		return conf, e
	}
	target := filepath.Join(root, "v2-restored-"+randomID()+".db")
	newOwner, _ := json.Marshal(writerOwner{Owner: "isolated-restored-target", Token: randomID(), PID: os.Getpid(), StartedAt: now(), Database: target})
	releaseTarget, e := acquireDatabaseWriter(target, newOwner)
	if e != nil {
		return conf, e
	}
	defer releaseTarget()
	copied, e := isolatedSnapshot(ctx, root, manifest.Backup, target, false)
	if e != nil {
		return conf, e
	}
	if copied.SHA256 != manifest.BackupHash {
		return conf, errors.New("RESTORE_BACKUP_IDENTITY_CHANGED")
	}
	if e = os.Rename(target+".snapshot.json", target+".seed-snapshot.json"); e != nil {
		return conf, e
	}
	store, e := sqlitev2.Open(ctx, target, false)
	if e != nil {
		return conf, e
	}
	defer store.Close()
	repo := sqlitev2.NewAppRepository(store)
	for _, state := range []string{"QUEUED", "RUNNING"} {
		jobs, e := repo.Jobs(ctx, state)
		if e != nil {
			return conf, e
		}
		for _, job := range jobs {
			if _, e = repo.Transition(ctx, job.ID, "INTERRUPTED", "EXPLICIT_V2_BACKUP_RESTORE_NO_AUTORUN", job.RunID, job.Progress); e != nil {
				return conf, e
			}
		}
	}
	conf.Target = target
	conf.Mode = "GO_V2"
	conf.ActiveDatabase = target
	if e = saveEvent(root, &conf, "ISOLATED_GO_RESTORED_WITH_PENDING_TASKS_INTERRUPTED"); e != nil {
		return conf, e
	}
	return conf, nil
}

// Check coverage in both directions, including pre-cutover data. Missing
// historical ledger entries require explicit reconciliation, never guessed dates.
func verifyAuthorityLedger(ctx context.Context, db *sql.DB) error {
	if e := sqlitev2.VerifyBundleLedger(ctx, db); e != nil {
		return e
	}
	for _, q := range []string{
		"SELECT count(*) FROM app_inputs a WHERE NOT EXISTS(SELECT 1 FROM app_change_ledger l WHERE l.kind='INPUT' AND l.entity_id=a.input_id AND l.content_hash=a.raw_hash)",
		"SELECT count(*) FROM app_runs a WHERE a.state='PUBLISHED' AND NOT EXISTS(SELECT 1 FROM app_change_ledger l WHERE l.kind='RUN' AND l.entity_id=a.run_id AND l.content_hash=a.members_hash)",
		"SELECT count(*) FROM app_jobs a WHERE NOT EXISTS(SELECT 1 FROM app_change_ledger l WHERE l.kind='JOB' AND l.entity_id=a.job_id AND l.content_hash=a.request_hash)",
		"SELECT count(*) FROM app_job_events a WHERE NOT EXISTS(SELECT 1 FROM app_change_ledger l WHERE l.kind='JOB_EVENT' AND l.entity_id=a.job_id||':'||a.revision)",
	} {
		var n int
		if e := db.QueryRowContext(ctx, q).Scan(&n); e != nil {
			return e
		}
		if n != 0 {
			return errors.New("UNRECONCILED_AUTHORITY_LEDGER")
		}
	}
	var after int64
	for {
		page, e := changesPage(ctx, db, after)
		if e != nil {
			return e
		}
		if len(page) == 0 {
			break
		}
		for _, ch := range page {
			if e = verifyChange(ctx, db, ch); e != nil {
				return e
			}
			after = ch.Sequence
		}
	}
	return nil
}
