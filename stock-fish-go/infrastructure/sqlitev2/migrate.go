package sqlitev2

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
)

var projectionColumns = []string{"open", "high", "low", "close", "amount_cny", "volume_shares", "turnover_fraction", "comparable_open", "comparable_high", "comparable_low", "comparable_close", "comparable_basis", "quality", "limit_up", "limit_down", "limit_verified", "limit_applicable", "reference_prev_close", "source_refs", "event_time", "source_published_at", "first_seen_at", "ingested_at", "effective_from", "effective_to", "listed_on", "exchange", "board", "security_type", "st", "suspended", "delisting_period"}

func object(raw []byte) (map[string]json.RawMessage, error) {
	var m map[string]json.RawMessage
	d := json.NewDecoder(bytes.NewReader(raw))
	if e := d.Decode(&m); e != nil {
		return nil, e
	}
	if m == nil {
		return nil, errors.New("EVIDENCE_NOT_OBJECT")
	}
	return m, nil
}
func scalar(v json.RawMessage) any {
	if len(v) == 0 || string(v) == "null" {
		return nil
	}
	var s string
	if json.Unmarshal(v, &s) == nil {
		return s
	}
	return string(v)
}
func txt(m map[string]json.RawMessage, k string) string {
	v := scalar(m[k])
	if v == nil {
		return ""
	}
	return fmt.Sprint(v)
}
func projection(m map[string]json.RawMessage, kind, iid, session string) ([]any, string) {
	p := []any{ProjectionVersion, kind, iid, session}
	for _, k := range projectionColumns {
		p = append(p, scalar(m[k]))
	}
	b, _ := json.Marshal(p)
	return p[4:], digest(b)
}

type Options struct {
	ReconcileExisting bool
	BatchSize         int
	MaxBatches        int
	Progress          func(string, int64)
	ResourceCheck     func() error
}
type MigrationReport struct {
	SourceSHA256    string           `json:"source_sha256"`
	Status          string           `json:"status"`
	Processed       map[string]int64 `json:"processed"`
	DurationSeconds float64          `json:"duration_seconds"`
	SQLiteVersion   string           `json:"sqlite_version"`
}

func Migrate(ctx context.Context, source, target string, opt Options) (out MigrationReport, err error) {
	started := time.Now()
	defer func() { out.DurationSeconds = time.Since(started).Seconds() }()
	frozen, releaseFrozen, e := acquireFrozen(ctx, source)
	if e != nil {
		return out, e
	}
	defer releaseFrozen()
	ident := frozen.Identity
	s, e := Open(ctx, target, true)
	if e != nil {
		return out, e
	}
	defer s.Close()
	out.SourceSHA256 = ident.SHA256
	out.Processed = map[string]int64{}
	s.db.QueryRowContext(ctx, "SELECT sqlite_version()").Scan(&out.SQLiteVersion)
	var prior, status string
	e = s.db.QueryRowContext(ctx, "SELECT source_sha256,status FROM migration_state WHERE id=1").Scan(&prior, &status)
	if errors.Is(e, sql.ErrNoRows) {
		_, e = s.db.ExecContext(ctx, "INSERT INTO migration_state VALUES(1,?,'MIGRATING',NULL,?)", ident.SHA256, time.Now().UTC().Format(time.RFC3339Nano))
	} else if e == nil && prior != ident.SHA256 {
		return out, errors.New("CHECKPOINT_SOURCE_MISMATCH")
	}
	if e != nil {
		return out, e
	}
	if status == "PUBLISHED" {
		out.Status = status
		return out, nil
	}
	db := frozen.DB
	if opt.BatchSize <= 0 {
		opt.BatchSize = 1000
	}
	batches := 0
	stages := []string{"documents", "history_evidence_rows", "history_partition_members"}
	r, e := db.QueryContext(ctx, "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
	if e != nil {
		return out, e
	}
	type table struct{ name, ddl string }
	var tables []table
	for r.Next() {
		var t table
		if e = r.Scan(&t.name, &t.ddl); e != nil {
			r.Close()
			return out, e
		}
		tables = append(tables, t)
	}
	e = r.Err()
	r.Close()
	if e != nil {
		return out, e
	}
	for _, t := range tables {
		if t.name == "yaogu_derived_features_v2" {
			var count int64
			if e = db.QueryRowContext(ctx, "SELECT count(*) FROM yaogu_derived_features_v2").Scan(&count); e != nil {
				return out, e
			}
			_, e = s.db.ExecContext(ctx, "INSERT INTO source_inventory VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET record_count=excluded.record_count", t.name, "DISCARD_REBUILDABLE_CACHE", count, t.ddl)
			if e != nil {
				return out, e
			}
			continue
		}
		known := false
		for _, n := range stages {
			if n == t.name {
				known = true
			}
		}
		if !known {
			stages = append(stages, t.name)
		}
		_, e = s.db.ExecContext(ctx, "INSERT INTO source_inventory VALUES(?,?,0,?) ON CONFLICT(name) DO NOTHING", t.name, "MIGRATE_OR_IMMUTABLE_ARCHIVE", t.ddl)
		if e != nil {
			return out, e
		}
	}
	for _, stage := range stages {
		var last, count int64
		var sh string
		e = s.db.QueryRowContext(ctx, "SELECT last_key,records,source_sha256 FROM migration_checkpoints WHERE stage=?", stage).Scan(&last, &count, &sh)
		if e != nil && !errors.Is(e, sql.ErrNoRows) {
			return out, e
		}
		if sh != "" && sh != ident.SHA256 {
			return out, errors.New("CHECKPOINT_SOURCE_MISMATCH")
		}
		for {
			if e = ctx.Err(); e != nil {
				return out, e
			}
			if opt.ResourceCheck != nil {
				if e = opt.ResourceCheck(); e != nil {
					return out, e
				}
			}
			tx, e := s.db.BeginTx(ctx, nil)
			if e != nil {
				return out, e
			}
			next, n, e := migrateBatch(ctx, db, tx, stage, last, opt.BatchSize, opt.ReconcileExisting)
			if e != nil {
				tx.Rollback()
				return out, fmt.Errorf("%s checkpoint=%d: %w", stage, last, e)
			}
			if n == 0 {
				tx.Rollback()
				break
			}
			last = next
			count += n
			_, e = tx.ExecContext(ctx, "INSERT INTO migration_checkpoints VALUES(?,?,?,?) ON CONFLICT(stage) DO UPDATE SET last_key=excluded.last_key,records=excluded.records WHERE migration_checkpoints.source_sha256=excluded.source_sha256", stage, last, count, ident.SHA256)
			if e == nil {
				_, e = tx.ExecContext(ctx, "UPDATE source_inventory SET record_count=? WHERE name=?", count, stage)
			}
			if e != nil {
				tx.Rollback()
				return out, e
			}
			if e = tx.Commit(); e != nil {
				return out, e
			}
			out.Processed[stage] = count
			batches++
			if opt.Progress != nil {
				opt.Progress(stage, count)
			}
			if opt.MaxBatches > 0 && batches >= opt.MaxBatches {
				out.Status = "INTERRUPTED_CHECKPOINTED"
				return out, nil
			}
		}
	}
	rows, readErr := s.db.QueryContext(ctx, "SELECT stage,records FROM migration_checkpoints")
	if readErr != nil {
		return out, readErr
	}
	for rows.Next() {
		var stage string
		var n int64
		if readErr = rows.Scan(&stage, &n); readErr != nil {
			rows.Close()
			return out, readErr
		}
		out.Processed[stage] = n
	}
	readErr = rows.Err()
	rows.Close()
	if readErr != nil {
		return out, readErr
	}
	out.Status = "MIGRATED_UNVERIFIED"
	return out, nil
}
func migrateBatch(ctx context.Context, db *sql.DB, tx *sql.Tx, stage string, last int64, batch int, reconcile bool) (int64, int64, error) {
	rows, e := db.QueryContext(ctx, `SELECT rowid,* FROM "`+stage+`" WHERE rowid>? ORDER BY rowid LIMIT ?`, last, batch)
	if e != nil {
		return last, 0, e
	}
	defer rows.Close()
	cols, e := rows.Columns()
	if e != nil {
		return last, 0, e
	}
	var n int64
	var hist *sql.Stmt
	var partitionLookup, memberHistoryLookup, memberInsert *sql.Stmt
	partitionIDs := map[string]int64{}
	if stage == "history_evidence_rows" {
		q := "INSERT INTO history_rows(row_hash,kind,instrument_id,session,raw_size,codec,payload,projection_version,projection_hash," + strings.Join(projectionColumns, ",") + ") VALUES(" + strings.TrimRight(strings.Repeat("?,", 9+len(projectionColumns)), ",") + ")"
		hist, e = tx.PrepareContext(ctx, q)
		if e != nil {
			return last, 0, e
		}
		defer hist.Close()
	}
	if stage == "history_partition_members" {
		partitionLookup, e = tx.PrepareContext(ctx, "SELECT id FROM partitions WHERE partition_hash=?")
		if e != nil {
			return last, 0, e
		}
		defer partitionLookup.Close()
		memberHistoryLookup, e = tx.PrepareContext(ctx, "SELECT id,kind,instrument_id,session FROM history_rows WHERE row_hash=?")
		if e != nil {
			return last, 0, e
		}
		defer memberHistoryLookup.Close()
		memberInsert, e = tx.PrepareContext(ctx, "INSERT INTO partition_members VALUES(?,?)")
		if e != nil {
			return last, 0, e
		}
		defer memberInsert.Close()
	}
	for rows.Next() {
		values := make([]any, len(cols))
		ptr := make([]any, len(cols))
		for i := range values {
			ptr[i] = &values[i]
		}
		if e = rows.Scan(ptr...); e != nil {
			return last, n, e
		}
		rid := values[0].(int64)
		m := map[string]any{}
		for i, k := range cols[1:] {
			m[k] = values[i+1]
		}
		s := func(k string) string {
			if m[k] == nil {
				return ""
			}
			return fmt.Sprint(m[k])
		}
		switch stage {
		case "documents":
			raw := []byte(s("payload"))
			if digest(raw) != s("content_hash") {
				return last, n, errors.New("SOURCE_RAW_HASH_MISMATCH")
			}
			if e = putDocument(ctx, tx, s("namespace"), s("document_id"), raw, s("content_hash"), s("created_at")); e != nil {
				return last, n, e
			}
		case "history_evidence_rows":
			raw := []byte(s("payload"))
			if digest(raw) != s("row_hash") {
				return last, n, errors.New("SOURCE_HISTORY_HASH_MISMATCH")
			}
			o, e := object(raw)
			if e != nil {
				return last, n, e
			}
			if txt(o, "instrument_id") != s("instrument_id") {
				return last, n, errors.New("SOURCE_HISTORY_ID_MISMATCH")
			}
			if s("kind") == "bar" && txt(o, "session") != s("session") {
				return last, n, errors.New("SOURCE_HISTORY_SESSION_MISMATCH")
			}
			if reconcile {
				var k, i, se string
				e = tx.QueryRowContext(ctx, "SELECT kind,instrument_id,session FROM history_rows WHERE row_hash=?", s("row_hash")).Scan(&k, &i, &se)
				if e == nil {
					if k != s("kind") || i != s("instrument_id") || se != s("session") {
						return last, n, errors.New("HISTORY_EXISTING_METADATA_CONFLICT")
					}
					last = rid
					n++
					continue
				}
				if !errors.Is(e, sql.ErrNoRows) {
					return last, n, e
				}
			}
			p, ph := projection(o, s("kind"), s("instrument_id"), s("session"))
			codec, blob := encode(raw)
			args := []any{s("row_hash"), s("kind"), s("instrument_id"), s("session"), len(raw), codec, blob, ProjectionVersion, ph}
			args = append(args, p...)
			_, e = hist.ExecContext(ctx, args...)
			if e != nil {
				return last, n, e
			}
		case "history_partition_members":
			partitionHash := s("partition_hash")
			pid, cached := partitionIDs[partitionHash]
			if !cached {
				e = partitionLookup.QueryRowContext(ctx, partitionHash).Scan(&pid)
				if errors.Is(e, sql.ErrNoRows) {
					r, e := tx.ExecContext(ctx, "INSERT INTO partitions(partition_hash) VALUES(?)", partitionHash)
					if e != nil {
						return last, n, e
					}
					pid, e = r.LastInsertId()
					if e != nil {
						return last, n, e
					}
				} else if e != nil {
					return last, n, e
				}
				partitionIDs[partitionHash] = pid
			}
			var rowid int64
			var kind, iid, session string
			if e = memberHistoryLookup.QueryRowContext(ctx, s("row_hash")).Scan(&rowid, &kind, &iid, &session); e != nil {
				return last, n, e
			}
			if kind != s("kind") || iid != s("instrument_id") || session != s("session") {
				return last, n, errors.New("SOURCE_PARTITION_METADATA_CONFLICT")
			}
			if reconcile {
				var exists int
				if e = tx.QueryRowContext(ctx, "SELECT count(*) FROM partition_members WHERE partition_id=? AND row_id=?", pid, rowid).Scan(&exists); e != nil {
					return last, n, e
				}
				if exists == 1 {
					last = rid
					n++
					continue
				}
			}
			if _, e = memberInsert.ExecContext(ctx, pid, rowid); e != nil {
				return last, n, e
			}
		default:
			raw, e := json.Marshal(m)
			if e != nil {
				return last, n, e
			}
			id, e := evidence(ctx, tx, raw)
			if e != nil {
				return last, n, e
			}
			rev := digest(raw)
			if reconcile {
				var old int64
				e = tx.QueryRowContext(ctx, "SELECT evidence_id FROM archived_table_rows WHERE table_name=? AND legacy_key=? AND revision=?", stage, strconv.FormatInt(rid, 10), rev).Scan(&old)
				if e == nil {
					if old != id {
						return last, n, errors.New("ARCHIVE_CONTENT_CONFLICT")
					}
					last = rid
					n++
					continue
				}
				if !errors.Is(e, sql.ErrNoRows) {
					return last, n, e
				}
			}
			_, e = tx.ExecContext(ctx, "INSERT INTO archived_table_rows VALUES(?,?,?,?)", stage, strconv.FormatInt(rid, 10), rev, id)
			if e != nil {
				return last, n, e
			}
			if stage == "jobs" {
				revision, _ := m["revision"].(int64)
				_, e = tx.ExecContext(ctx, "INSERT INTO historical_jobs VALUES(?,?,?,?,?,?,0)", s("job_id"), revision, s("state"), s("kind"), s("updated_at"), id)
				if e != nil {
					return last, n, e
				}
			}
		}
		last = rid
		n++
	}
	return last, n, rows.Err()
}
func putDocument(ctx context.Context, tx *sql.Tx, ns, id string, raw []byte, hash, created string) error {
	var old string
	e := tx.QueryRowContext(ctx, "SELECT source_hash FROM legacy_records WHERE namespace=? AND external_id=?", ns, id).Scan(&old)
	if e == nil {
		if old != hash {
			return errors.New("SAME_KEY_DIFFERENT_CONTENT")
		}
		return nil
	}
	if !errors.Is(e, sql.ErrNoRows) {
		return e
	}
	eid, e := evidence(ctx, tx, raw)
	if e != nil {
		return e
	}
	_, e = tx.ExecContext(ctx, "INSERT INTO legacy_records VALUES(?,?,?,?,?)", ns, id, hash, eid, created)
	if e != nil {
		return e
	}
	o, e := object(raw)
	if e != nil {
		return e
	}
	if ns == "candidate_inputs" || ns == "history_artifact_manifests" {
		_, e = tx.ExecContext(ctx, "INSERT INTO snapshots VALUES(?,?,?,?,?,?,?)", id, ns, eid, txt(o, "observation_date"), scalar(o["cutoff"]), scalar(o["history_artifact_ref"]), scalar(o["coverage_type"]))
		if e != nil {
			return e
		}
		for _, kind := range []string{"receipts", "bars", "universe", "securities", "funds", "themes", "sessions", "previous_emotions"} {
			members, err := snapshotMembers(o, kind)
			if err != nil {
				return err
			}
			for ord, member := range members {
				mid, e := evidence(ctx, tx, member)
				if e != nil {
					return e
				}
				mo, _ := object(member)
				_, e = tx.ExecContext(ctx, "INSERT INTO snapshot_members VALUES(?,?,?,?,?,?)", id, kind, ord, mid, scalar(mo["instrument_id"]), scalar(mo["session"]))
				if e != nil {
					return e
				}
				if kind == "receipts" {
					_, e = tx.ExecContext(ctx, "INSERT INTO source_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", id, ord, txt(mo, "receipt_id"), scalar(mo["content_hash"]), scalar(mo["provider"]), scalar(mo["dataset"]), scalar(mo["status"]), scalar(mo["event_time"]), scalar(mo["source_published_at"]), scalar(mo["first_seen_at"]), scalar(mo["ingested_at"]), mid, digest(member))
					if e != nil {
						return e
					}
				}
			}
		}
		if ns == "history_artifact_manifests" {
			var parts []map[string]json.RawMessage
			if e = json.Unmarshal(o["partitions"], &parts); e != nil {
				return e
			}
			for _, p := range parts {
				hash := txt(p, "partition_hash")
				var pid int64
				e = tx.QueryRowContext(ctx, "SELECT id FROM partitions WHERE partition_hash=?", hash).Scan(&pid)
				if errors.Is(e, sql.ErrNoRows) {
					r, e := tx.ExecContext(ctx, "INSERT INTO partitions(partition_hash) VALUES(?)", hash)
					if e != nil {
						return e
					}
					pid, _ = r.LastInsertId()
				} else if e != nil {
					return e
				}
				_, e = tx.ExecContext(ctx, "INSERT INTO snapshot_partitions VALUES(?,?,?,?)", id, pid, txt(p, "session"), scalar(p["row_count"]))
				if e != nil {
					return e
				}
			}
		}
	}
	if strings.Contains(ns, "runs") || ns == "daily_research_snapshots" || ns == "strategy_experiment_summaries" {
		_, e = tx.ExecContext(ctx, "INSERT INTO legacy_result_headers VALUES(?,?,?,?,?,?,?)", id, ns, scalar(o["strategy_id"]), scalar(o["input_id"]), scalar(o["observation_date"]), scalar(o["decision_cutoff"]), eid)
		if e != nil {
			return e
		}
	}
	if ns == "strategy_experiment_item_details" {
		_, e = tx.ExecContext(ctx, "INSERT INTO legacy_result_items VALUES(?,?,?,?)", txt(o, "run_id"), txt(o, "instrument_id"), txt(o, "strategy_id"), id)
		if e != nil {
			return e
		}
	}
	return nil
}

func snapshotMembers(o map[string]json.RawMessage, kind string) ([]json.RawMessage, error) {
	v := o[kind]
	if kind == "securities" {
		u, e := object(o["universe"])
		if e != nil {
			return nil, nil
		}
		v = u["securities"]
	}
	if len(v) == 0 || string(v) == "null" {
		return nil, nil
	}
	if kind == "universe" && len(bytes.TrimSpace(v)) > 0 && bytes.TrimSpace(v)[0] == '{' {
		return []json.RawMessage{v}, nil
	}
	var members []json.RawMessage
	e := json.Unmarshal(v, &members)
	return members, e
}
