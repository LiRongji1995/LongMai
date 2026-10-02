package lifecycle

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"time"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
)

func emptyWAL(path string) error {
	st, e := os.Lstat(path + "-wal")
	if errors.Is(e, os.ErrNotExist) {
		return nil
	}
	if e != nil {
		return e
	}
	if !st.Mode().IsRegular() || st.Size() != 0 {
		return errors.New("CLONE_REQUIRES_EMPTY_REGULAR_WAL")
	}
	return nil
}
func currentWriter(root string) error {
	raw, e := os.ReadFile(filepath.Join(root, "writer.lock"))
	if e != nil {
		return e
	}
	var writer struct {
		PID   int    `json:"pid"`
		Token string `json:"token"`
		Owner string `json:"owner"`
	}
	if e = json.Unmarshal(raw, &writer); e != nil {
		return e
	}
	if writer.PID != os.Getpid() || writer.Token == "" || writer.Owner == "" {
		return errors.New("CLONE_REQUIRES_CURRENT_PROCESS_WRITER_LEASE")
	}
	return nil
}
func reserveClone(ctx context.Context, root, source string) error {
	st, e := os.Stat(source)
	if e != nil {
		return e
	}
	free, e := availableBytes(root)
	if e != nil {
		return e
	}
	overhead := int64(128 << 20)
	if wal, err := os.Stat(source + "-wal"); err == nil {
		overhead += wal.Size()
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if free-overhead < MinimumReserve {
		return errors.New("CLONE_SPACE_RESERVE_EXHAUSTED")
	}
	used, e := diskUsage(ctx, root)
	if e != nil {
		return e
	}
	if used+st.Size() > MaximumPersistent {
		return errors.New("BACKUP_PERSISTENT_SPACE_LIMIT")
	}
	return nil
}
func checkpointIsolated(ctx context.Context, source string) error {
	db, e := sql.Open("sqlite", sqlitev2.URI(source, false))
	if e != nil {
		return e
	}
	db.SetMaxOpenConns(1)
	var busy, log, done int
	e = db.QueryRowContext(ctx, "PRAGMA wal_checkpoint(TRUNCATE)").Scan(&busy, &log, &done)
	closeErr := db.Close()
	if e != nil {
		return e
	}
	if closeErr != nil {
		return closeErr
	}
	// Non-WAL SQLite returns -1/-1. WAL returns zero/zero after TRUNCATE.
	if busy != 0 || !((log == 0 && done == 0) || (log == -1 && done == -1)) {
		return errors.New("CLONE_CHECKPOINT_BUSY_OR_INCOMPLETE")
	}
	return emptyWAL(source)
}

// isolatedSnapshot is reachable only from lifecycle operations holding the
// isolated writer lease. Production/source capture continues using Snapshot's
// online SQLite backup API and is deliberately not changed here.
func isolatedSnapshot(ctx context.Context, root, source, target string, mutable bool) (out sqlitev2.SnapshotIdentity, err error) {
	started := time.Now()
	lastStage := started
	timings := map[string]float64{}
	mark := func(name string) { now := time.Now(); timings[name] = now.Sub(lastStage).Seconds(); lastStage = now }
	conf, e := Load(root)
	if e != nil {
		return out, e
	}
	if e = currentWriter(root); e != nil {
		return out, e
	}
	if source, e = inside(root, source); e != nil {
		return out, e
	}
	if target, e = inside(root, target); e != nil {
		return out, e
	}
	if source == target {
		return out, errors.New("CLONE_SOURCE_TARGET_MUST_DIFFER")
	}
	if mutable && source != conf.Target {
		return out, errors.New("CLONE_MUTATION_ONLY_ISOLATED_V2_TARGET")
	}
	if _, e = os.Lstat(target); !errors.Is(e, os.ErrNotExist) {
		return out, errors.New("SNAPSHOT_TARGET_MUST_NOT_EXIST")
	}
	possible, e := cloneAvailable(source, filepath.Dir(target))
	if e != nil {
		return out, e
	}
	if !possible {
		if e = reserveBackup(ctx, root, source); e != nil {
			return out, e
		}
		return sqlitev2.Snapshot(ctx, source, target)
	}
	if e = reserveClone(ctx, root, source); e != nil {
		return out, e
	}
	mark("preflight_and_capacity")
	mode := "mode=ro; verified frozen source; no source PRAGMA mutation"
	if mutable {
		if e = checkpointIsolated(ctx, source); e != nil {
			return out, e
		}
		mode = "isolated V2 writer lease; wal_checkpoint(TRUNCATE); connection closed; WAL empty"
	} else {
		if _, e = sqlitev2.SnapshotCheck(ctx, source); e != nil {
			return out, e
		}
		if e = emptyWAL(source); e != nil {
			return out, e
		}
	}
	mark("source_prepare")
	before, e := sqlitev2.FileHash(ctx, source)
	if e != nil {
		return out, e
	}
	mark("source_hash_before")
	if e = cloneDatabase(source, target); e != nil {
		return out, e
	}
	mark("clonefile")
	complete := false
	defer func() {
		if !complete {
			_ = os.Remove(target)
			_ = os.Remove(target + "-wal")
			_ = os.Remove(target + "-shm")
			_ = os.Remove(target + ".snapshot.json")
			_ = os.Remove(target + ".clone-metrics.json")
		}
	}()
	if e = os.Chmod(target, 0600); e != nil {
		return out, e
	}
	after, e := sqlitev2.FileHash(ctx, source)
	if e != nil {
		return out, e
	}
	if e = emptyWAL(source); e != nil {
		return out, e
	}
	mark("source_hash_after")
	targetHash, e := sqlitev2.FileHash(ctx, target)
	if e != nil {
		return out, e
	}
	if before != after || before != targetHash {
		return out, errors.New("CLONE_SOURCE_OR_CONTENT_CHANGED")
	}
	mark("target_hash")
	db, e := readDB(ctx, target)
	if e != nil {
		return out, e
	}
	defer db.Close()
	var integrity, sqliteVersion string
	var foreignKeys, userVersion int
	if e = db.QueryRowContext(ctx, "PRAGMA integrity_check").Scan(&integrity); e != nil {
		return out, e
	}
	if integrity != "ok" {
		return out, errors.New("CLONE_INTEGRITY_CHECK_FAILED")
	}
	if e = db.QueryRowContext(ctx, "SELECT count(*) FROM pragma_foreign_key_check").Scan(&foreignKeys); e != nil {
		return out, e
	}
	if foreignKeys != 0 {
		return out, errors.New("CLONE_FOREIGN_KEY_CHECK_FAILED")
	}
	if e = db.QueryRowContext(ctx, "PRAGMA user_version").Scan(&userVersion); e != nil {
		return out, e
	}
	if mutable || source != conf.Source {
		if userVersion != sqlitev2.SchemaVersion {
			return out, errors.New("CLONE_V2_SCHEMA_REQUIRED")
		}
		if _, _, _, e = metadata(ctx, db); e != nil {
			return out, e
		}
	}
	if e = db.QueryRowContext(ctx, "SELECT sqlite_version()").Scan(&sqliteVersion); e != nil {
		return out, e
	}
	if e = db.Close(); e != nil {
		return out, e
	}
	if e = emptyWAL(target); e != nil {
		return out, e
	}
	mark("integrity_foreign_keys_schema")
	file, e := os.Open(target)
	if e != nil {
		return out, e
	}
	e = file.Sync()
	closeErr := file.Close()
	if e != nil {
		return out, e
	}
	if closeErr != nil {
		return out, closeErr
	}
	st, e := os.Stat(target)
	if e != nil {
		return out, e
	}
	free, e := availableBytes(root)
	if e != nil {
		return out, e
	}
	if free < MinimumReserve {
		return out, errors.New("CLONE_SPACE_RESERVE_EXHAUSTED")
	}
	out = sqlitev2.SnapshotIdentity{Version: "ISOLATED_APFS_CLONEFILE_V1", Source: source, Snapshot: target, SHA256: targetHash, Bytes: st.Size(), CreatedAt: now(), SQLiteVersion: sqliteVersion, SourceOpenMode: mode}
	if e = writeJSON(target+".snapshot.json", out); e != nil {
		return out, e
	}
	mark("durability_and_publication")
	if e = writeJSON(target+".clone-metrics.json", map[string]any{"version": out.Version, "filesystem": "APFS", "clone_api": "unix.Clonefile/clonefile", "bytes": out.Bytes, "sha256": out.SHA256, "timings_seconds": timings, "total_seconds": time.Since(started).Seconds()}); e != nil {
		return out, e
	}
	complete = true
	return out, nil
}
