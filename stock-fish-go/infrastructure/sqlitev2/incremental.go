package sqlitev2

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"
)

// Incremental reconciles complete key/content sets, never timestamp high-water marks.
// Publication is gated for the entire reconciliation. Mutable jobs retain every
// immutable historical revision and remain non-executable.
func Incremental(ctx context.Context, source, target string, opt Options) (MigrationReport, error) {
	frozen, releaseFrozen, e := acquireFrozen(ctx, source)
	if e != nil {
		return MigrationReport{}, e
	}
	defer releaseFrozen()
	ctx = context.WithValue(ctx, frozenContextKey{}, frozen)
	ident := frozen.Identity
	s, e := Open(ctx, target, false)
	if e != nil {
		return MigrationReport{}, e
	}
	_, e = s.db.ExecContext(ctx, `CREATE TABLE IF NOT EXISTS incremental_epochs(source_sha256 TEXT PRIMARY KEY,prior_source_sha256 TEXT NOT NULL,prior_verification TEXT NOT NULL,prior_checkpoints TEXT NOT NULL,started_at TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('MIGRATING','PUBLISHED')))`)
	if e != nil {
		s.Close()
		return MigrationReport{}, e
	}
	var old, status string
	var verification sql.NullString
	if e = s.db.QueryRowContext(ctx, "SELECT source_sha256,status,verification FROM migration_state WHERE id=1").Scan(&old, &status, &verification); e != nil {
		s.Close()
		return MigrationReport{}, e
	}
	if old != ident.SHA256 {
		if status != "PUBLISHED" {
			s.Close()
			return MigrationReport{}, errors.New("INCREMENTAL_BASE_NOT_PUBLISHED")
		}
		// Immutable disappearance or replacement must be reported before changing state.
		if _, e = s.db.ExecContext(ctx, "ATTACH DATABASE ? AS incoming", frozen.URI()); e != nil {
			s.Close()
			return MigrationReport{}, e
		}
		var missing int64
		e = s.db.QueryRowContext(ctx, `SELECT count(*) FROM (SELECT namespace,external_id,source_hash FROM legacy_records EXCEPT SELECT namespace,document_id,content_hash FROM incoming.documents)`).Scan(&missing)
		if e == nil && missing != 0 {
			e = errors.New("INCREMENTAL_IMMUTABLE_CONFLICT_OR_REMOVAL")
		}
		if e == nil {
			e = s.db.QueryRowContext(ctx, `SELECT count(*) FROM (SELECT row_hash,kind,instrument_id,session FROM history_rows EXCEPT SELECT row_hash,kind,instrument_id,session FROM incoming.history_evidence_rows)`).Scan(&missing)
			if e == nil && missing != 0 {
				e = errors.New("INCREMENTAL_HISTORY_CONFLICT_OR_REMOVAL")
			}
		}
		s.db.ExecContext(ctx, "DETACH DATABASE incoming")
		if e != nil {
			s.Close()
			return MigrationReport{}, e
		}
		rows, e := s.db.QueryContext(ctx, "SELECT stage,last_key,records,source_sha256 FROM migration_checkpoints ORDER BY stage")
		if e != nil {
			s.Close()
			return MigrationReport{}, e
		}
		var checkpoints []any
		for rows.Next() {
			var stage, hash string
			var key, count int64
			if e = rows.Scan(&stage, &key, &count, &hash); e != nil {
				break
			}
			checkpoints = append(checkpoints, []any{stage, key, count, hash})
		}
		if e == nil {
			e = rows.Err()
		}
		rows.Close()
		if e != nil {
			s.Close()
			return MigrationReport{}, e
		}
		raw, _ := json.Marshal(checkpoints)
		tx, e := s.db.BeginTx(ctx, nil)
		if e != nil {
			s.Close()
			return MigrationReport{}, e
		}
		_, e = tx.ExecContext(ctx, "INSERT INTO incremental_epochs VALUES(?,?,?,?,?,'MIGRATING')", ident.SHA256, old, verification.String, string(raw), time.Now().UTC().Format(time.RFC3339Nano))
		if e == nil {
			_, e = tx.ExecContext(ctx, "DELETE FROM migration_checkpoints")
		}
		if e == nil {
			_, e = tx.ExecContext(ctx, "UPDATE migration_state SET source_sha256=?,status='MIGRATING',verification=NULL WHERE id=1", ident.SHA256)
		}
		if e != nil {
			tx.Rollback()
			s.Close()
			return MigrationReport{}, e
		}
		if e = tx.Commit(); e != nil {
			s.Close()
			return MigrationReport{}, e
		}
	} else if status != "PUBLISHED" {
		var n int
		if e = s.db.QueryRowContext(ctx, "SELECT count(*) FROM incremental_epochs WHERE source_sha256=? AND status='MIGRATING'", ident.SHA256).Scan(&n); e != nil || n != 1 {
			s.Close()
			return MigrationReport{}, errors.New("NOT_AN_INCREMENTAL_CHECKPOINT")
		}
	}
	s.Close()
	opt.ReconcileExisting = true
	out, e := Migrate(ctx, source, target, opt)
	if e != nil || out.Status == "INTERRUPTED_CHECKPOINTED" {
		return out, e
	}
	if _, e = Verify(ctx, source, target, opt); e != nil {
		return out, e
	}
	if e = Publish(ctx, target); e != nil {
		return out, e
	}
	s, e = Open(ctx, target, false)
	if e != nil {
		return out, e
	}
	defer s.Close()
	_, e = s.db.ExecContext(ctx, "UPDATE incremental_epochs SET status='PUBLISHED' WHERE source_sha256=?", ident.SHA256)
	out.Status = "PUBLISHED"
	return out, e
}
