package sqlitev2

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"time"
)

type Verification struct {
	QueryPlans   map[string][]string `json:"query_plans"`
	Version      string              `json:"version"`
	SourceSHA256 string              `json:"source_sha256"`
	Checked      map[string]int64    `json:"checked"`
	Errors       []string            `json:"errors"`
	Integrity    string              `json:"integrity"`
	ForeignKeys  int                 `json:"foreign_keys"`
	Duration     float64             `json:"duration_seconds"`
	CompletedAt  string              `json:"completed_at"`
}

func Verify(ctx context.Context, source, target string, opt Options) (out Verification, err error) {
	start := time.Now()
	out = Verification{Version: "V2_FULL_VERIFICATION_1", Checked: map[string]int64{}}
	defer func() { out.Duration = time.Since(start).Seconds() }()
	ident, e := SnapshotCheck(ctx, source)
	if e != nil {
		return out, e
	}
	out.SourceSHA256 = ident.SHA256
	s, e := Open(ctx, target, false)
	if e != nil {
		return out, e
	}
	defer s.Close()
	var expected string
	if e = s.db.QueryRowContext(ctx, "SELECT source_sha256 FROM migration_state WHERE id=1").Scan(&expected); e != nil {
		return out, e
	}
	if expected != ident.SHA256 {
		return out, errors.New("VERIFICATION_SOURCE_MISMATCH")
	}
	// Revalidation closes the publication gate. A cancelled or failed audit must
	// never leave a previous PUBLISHED marker advertising corrupted evidence.
	if _, e = s.db.ExecContext(ctx, "UPDATE migration_state SET status='MIGRATING',verification=NULL WHERE id=1 AND source_sha256=?", ident.SHA256); e != nil {
		return out, e
	}
	if e = s.VerifySchema(ctx); e != nil {
		return out, e
	}
	if _, e = s.db.ExecContext(ctx, "ATTACH DATABASE ? AS frozen", URI(source, true)); e != nil {
		return out, e
	}
	defer s.db.ExecContext(context.Background(), "DETACH DATABASE frozen")
	checks := []struct{ name, q string }{
		{"document_identity_set", `SELECT count(*) FROM (SELECT namespace,document_id,content_hash,created_at FROM frozen.documents EXCEPT SELECT namespace,external_id,source_hash,created_at FROM legacy_records)`},
		{"document_extra_identity", `SELECT count(*) FROM (SELECT namespace,external_id,source_hash,created_at FROM legacy_records EXCEPT SELECT namespace,document_id,content_hash,created_at FROM frozen.documents)`},
		{"snapshot_count", `SELECT count(*) FROM snapshots s WHERE (SELECT count(*) FROM snapshot_partitions p WHERE p.snapshot_id=s.snapshot_id)!=(CASE WHEN s.namespace='history_artifact_manifests' THEN json_array_length((SELECT payload FROM frozen.documents WHERE namespace=s.namespace AND document_id=s.snapshot_id),'$.partitions') ELSE 0 END)`},
		{"result_detail_closure", `SELECT count(*) FROM legacy_result_items i LEFT JOIN legacy_result_headers r ON r.namespace='strategy_experiment_runs' AND r.legacy_id=i.run_id LEFT JOIN legacy_records d ON d.namespace='strategy_experiment_item_details' AND d.external_id=i.detail_id WHERE r.legacy_id IS NULL OR d.external_id IS NULL`},
		{"snapshot_history_closure", `SELECT count(*) FROM snapshots s LEFT JOIN legacy_records l ON l.namespace='history_artifact_manifests' AND l.external_id=s.history_ref WHERE s.history_ref IS NOT NULL AND s.history_ref!='' AND l.external_id IS NULL`},
		{"historical_jobs_not_executable", `SELECT count(*) FROM historical_jobs WHERE executable!=0`},
	}
	for _, ch := range checks {
		if opt.ResourceCheck != nil {
			if e = opt.ResourceCheck(); e != nil {
				return out, e
			}
		}
		var n int64
		if e = s.db.QueryRowContext(ctx, ch.q).Scan(&n); e != nil {
			return out, fmt.Errorf("%s: %w", ch.name, e)
		}
		out.Checked[ch.name] = n
		if n != 0 {
			return out, fmt.Errorf("%s: %d mismatches", ch.name, n)
		}
		if opt.Progress != nil {
			opt.Progress(ch.name, n)
		}
	}
	if e = s.verifyExactSets(ctx, opt, &out); e != nil {
		return out, e
	}
	rows, e := s.db.QueryContext(ctx, "SELECT raw_sha256,raw_size,codec,payload FROM evidence_objects ORDER BY id")
	if e != nil {
		return out, e
	}
	for rows.Next() {
		var h, c string
		var n int64
		var p []byte
		if e = rows.Scan(&h, &n, &c, &p); e != nil {
			rows.Close()
			return out, e
		}
		if _, e = decodeContext(ctx, c, p, n, h); e != nil {
			rows.Close()
			return out, e
		}
		out.Checked["evidence_raw_hashes"]++
		if out.Checked["evidence_raw_hashes"]%10000 == 0 && opt.Progress != nil {
			opt.Progress("evidence_raw_hashes", out.Checked["evidence_raw_hashes"])
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return out, e
	}
	rows, e = s.db.QueryContext(ctx, "SELECT h.row_hash,h.kind,h.instrument_id,h.session,h.raw_size,h.codec,h.payload,h.projection_hash,h.projection_version,"+prefixColumns("h.")+" FROM history_rows h ORDER BY h.id")
	if e != nil {
		return out, e
	}
	for rows.Next() {
		var hash, kind, iid, sess, codec, ph, pv string
		var n int64
		var blob []byte
		p := make([]any, len(projectionColumns))
		ptr := []any{&hash, &kind, &iid, &sess, &n, &codec, &blob, &ph, &pv}
		for i := range p {
			ptr = append(ptr, &p[i])
		}
		if e = rows.Scan(ptr...); e != nil {
			rows.Close()
			return out, e
		}
		if pv != ProjectionVersion {
			rows.Close()
			return out, errors.New("PROJECTION_VERSION_MISMATCH")
		}
		if kind != "bar" && kind != "security" {
			rows.Close()
			return out, fmt.Errorf("UNSUPPORTED_HISTORY_KIND_PRESERVED: %s", kind)
		}
		raw, e := decodeContext(ctx, codec, blob, n, hash)
		if e != nil {
			rows.Close()
			return out, e
		}
		o, e := object(raw)
		if e != nil {
			rows.Close()
			return out, e
		}
		if txt(o, "instrument_id") != iid || (kind == "bar" && txt(o, "session") != sess) {
			rows.Close()
			return out, errors.New("HISTORY_RAW_METADATA_MISMATCH")
		}
		if e = validateProjectionTypes(o); e != nil {
			rows.Close()
			return out, e
		}
		want, wh := projection(o, kind, iid, sess)
		if ph != wh || !equalProjection(p, want) {
			rows.Close()
			return out, errors.New("TYPED_PROJECTION_TAMPERED")
		}
		out.Checked["history_raw_and_projections"]++
		if out.Checked["history_raw_and_projections"]%10000 == 0 {
			if opt.Progress != nil {
				opt.Progress("history_raw_and_projections", out.Checked["history_raw_and_projections"])
			}
			if opt.ResourceCheck != nil {
				if e = opt.ResourceCheck(); e != nil {
					rows.Close()
					return out, e
				}
			}
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return out, e
	}
	// Source raw bytes are verified independently; identity-set equality alone is insufficient.
	rows, e = s.db.QueryContext(ctx, "SELECT row_hash,payload FROM frozen.history_evidence_rows UNION ALL SELECT content_hash,payload FROM frozen.documents")
	if e != nil {
		return out, e
	}
	for rows.Next() {
		var hash string
		var raw []byte
		if e = rows.Scan(&hash, &raw); e != nil {
			rows.Close()
			return out, e
		}
		if digest(raw) != hash {
			rows.Close()
			return out, errors.New("SOURCE_RAW_HASH_MISMATCH")
		}
		out.Checked["source_raw_hashes"]++
		if out.Checked["source_raw_hashes"]%10000 == 0 && opt.Progress != nil {
			opt.Progress("source_raw_hashes", out.Checked["source_raw_hashes"])
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return out, e
	}
	if e = s.verifyRelations(ctx, &out); e != nil {
		return out, e
	}
	rows, e = s.db.QueryContext(ctx, "PRAGMA foreign_key_check")
	if e != nil {
		return out, e
	}
	for rows.Next() {
		out.ForeignKeys++
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return out, e
	}
	if out.ForeignKeys != 0 {
		return out, errors.New("FOREIGN_KEY_VIOLATION")
	}
	if e = s.db.QueryRowContext(ctx, "PRAGMA integrity_check").Scan(&out.Integrity); e != nil {
		return out, e
	}
	if out.Integrity != "ok" {
		return out, errors.New(out.Integrity)
	}
	out.QueryPlans, e = s.QueryPlans(ctx)
	if e != nil {
		return out, e
	}
	out.CompletedAt = time.Now().UTC().Format(time.RFC3339Nano)
	out.Duration = time.Since(start).Seconds()
	raw, _ := json.Marshal(out)
	_, e = s.db.ExecContext(ctx, "UPDATE migration_state SET status='VERIFIED',verification=? WHERE id=1 AND source_sha256=?", string(raw), ident.SHA256)
	return out, e
}
func Publish(ctx context.Context, target string) error {
	s, e := Open(ctx, target, false)
	if e != nil {
		return e
	}
	defer s.Close()
	if e = s.VerifySchema(ctx); e != nil {
		return e
	}
	var state string
	var source string
	var raw sql.NullString
	if e = s.db.QueryRowContext(ctx, "SELECT status,source_sha256,verification FROM migration_state WHERE id=1").Scan(&state, &source, &raw); e != nil {
		return e
	}
	if (state != "VERIFIED" && state != "PUBLISHED") || !raw.Valid {
		return errors.New("UNVERIFIED_MIGRATION_CANNOT_PUBLISH")
	}
	var v Verification
	if e = json.Unmarshal([]byte(raw.String), &v); e != nil || v.Version != "V2_FULL_VERIFICATION_1" || v.SourceSHA256 != source || v.CompletedAt == "" || v.Integrity != "ok" || v.ForeignKeys != 0 || len(v.Errors) != 0 {
		return errors.New("INVALID_VERIFICATION_CANNOT_PUBLISH")
	}
	if _, e = s.BackfillBatchProjections(ctx, BatchProjectionOptions{ResourceCheck: func() error { return CheckResources(filepath.Dir(target)) }}); e != nil {
		return e
	}
	if _, e = s.VerifyBatchProjections(ctx); e != nil {
		return e
	}
	if e = s.BatchProjectionsReady(ctx); e != nil {
		return e
	}
	if state == "PUBLISHED" {
		return nil
	}
	_, e = s.db.ExecContext(ctx, "UPDATE migration_state SET status='PUBLISHED' WHERE id=1 AND status='VERIFIED'")
	return e
}
