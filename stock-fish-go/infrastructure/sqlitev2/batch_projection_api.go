package sqlitev2

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
)

// batchInventory reads identities only. It never reads raw payloads or the
// multi-million-row historical price table, so readiness remains inexpensive.
func batchInventory(ctx context.Context, q batchQuerier, strict bool) ([]byte, string, error) {
	owners, e := batchOwners(ctx, q)
	if e != nil {
		return nil, "", e
	}
	var count int
	if e = q.QueryRowContext(ctx, "SELECT count(*) FROM batch_projection_owners").Scan(&count); e != nil {
		return nil, "", e
	}
	if count > len(owners) {
		return nil, "", errors.New("BATCH_UNEXPECTED_OWNER")
	}
	members := make([][]string, 0, len(owners))
	for _, o := range owners {
		var rawHash, version, mh string
		var manifest []byte
		e = q.QueryRowContext(ctx, "SELECT parent_raw_hash,conversion_version,manifest_hash,manifest FROM batch_projection_owners WHERE owner_kind=? AND owner_id=?", o.Kind, o.ID).Scan(&rawHash, &version, &mh, &manifest)
		if errors.Is(e, sql.ErrNoRows) {
			if strict {
				return nil, "", errors.New("BATCH_OWNER_COVERAGE_INCOMPLETE")
			}
			return nil, "", nil
		}
		if e != nil {
			return nil, "", e
		}
		if rawHash != o.RawHash || version != BatchProjectionVersion || digest(manifest) != mh {
			return nil, "", errors.New("BATCH_OWNER_MANIFEST_MISMATCH")
		}
		members = append(members, []string{o.Kind, o.ID, o.RawHash, mh})
	}
	raw, e := json.Marshal(map[string]any{"version": BatchProjectionVersion, "schema_checksum": digest([]byte(batchSchemaDDL())), "owners": members})
	if e != nil {
		return nil, "", e
	}
	return raw, digest(raw), nil
}
func publishBatchInventory(ctx context.Context, tx *sql.Tx, strict bool) (string, int, error) {
	raw, hash, e := batchInventory(ctx, tx, strict)
	if e != nil || hash == "" {
		return "", 0, e
	}
	var n int
	if e = tx.QueryRowContext(ctx, "SELECT count(*) FROM batch_projection_owners").Scan(&n); e != nil {
		return "", 0, e
	}
	var existing []byte
	e = tx.QueryRowContext(ctx, "SELECT manifest FROM batch_projection_publications WHERE manifest_hash=?", hash).Scan(&existing)
	if e == nil {
		if !batchEqual(string(existing), string(raw)) {
			return "", 0, errors.New("BATCH_PUBLICATION_CONFLICT")
		}
		return hash, n, nil
	}
	if !errors.Is(e, sql.ErrNoRows) {
		return "", 0, e
	}
	if _, e = tx.ExecContext(ctx, "INSERT INTO batch_projection_publications VALUES(?,?,?,?)", hash, raw, n, appNow()); e != nil {
		return "", 0, e
	}
	return hash, n, nil
}
func batchCurrentIdentity(ctx context.Context, q batchQuerier) (map[string]string, error) {
	raw, hash, e := batchInventory(ctx, q, true)
	if e != nil {
		return nil, e
	}
	var saved []byte
	var count int
	if e = q.QueryRowContext(ctx, "SELECT manifest,owner_count FROM batch_projection_publications WHERE manifest_hash=?", hash).Scan(&saved, &count); e != nil {
		return nil, fmt.Errorf("BATCH_PUBLICATION_REQUIRED: %w", e)
	}
	if string(saved) != string(raw) || digest(saved) != hash {
		return nil, errors.New("BATCH_PUBLICATION_BYTES_MISMATCH")
	}
	var actual int
	if e = q.QueryRowContext(ctx, "SELECT count(*) FROM batch_projection_owners").Scan(&actual); e != nil {
		return nil, e
	}
	if actual != count {
		return nil, errors.New("BATCH_PUBLICATION_OWNER_COUNT_MISMATCH")
	}
	return map[string]string{"conversion_version": BatchProjectionVersion, "schema_checksum": digest([]byte(batchSchemaDDL())), "manifest_hash": hash}, nil
}
func (s *Store) BatchProjectionsReady(ctx context.Context) error {
	_, e := s.BatchProjectionIdentity(ctx)
	return e
}
func (s *Store) BatchProjectionIdentity(ctx context.Context) (map[string]string, error) {
	tx, e := s.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if e != nil {
		return nil, e
	}
	defer tx.Rollback()
	return batchCurrentIdentity(ctx, tx)
}
func batchAccumulate(out *BatchProjectionReport, records batchOwnerSummary) {
	out.Owners++
	out.Records += records.Records
	out.Themes += records.Themes
	out.Funds += records.Funds
	out.Receipts += records.Receipts
}
func (s *Store) BackfillBatchProjections(ctx context.Context, opt BatchProjectionOptions) (BatchProjectionReport, error) {
	out := BatchProjectionReport{Status: "IN_PROGRESS", ConversionVersion: BatchProjectionVersion}
	if e := s.InitBatchProjectionSchema(ctx); e != nil {
		return out, e
	}
	var status string
	if e := s.db.QueryRowContext(ctx, "SELECT status FROM migration_state WHERE id=1").Scan(&status); e != nil {
		return out, e
	}
	if status != "VERIFIED" && status != "PUBLISHED" {
		return out, errors.New("BATCH_BASE_VERIFICATION_REQUIRED")
	}
	if opt.BatchSize <= 0 {
		opt.BatchSize = 8
	}
	if opt.MaxBatches < 0 {
		return out, errors.New("BATCH_INVALID_LIMIT")
	}
	owners, e := batchOwners(ctx, s.db)
	if e != nil {
		return out, e
	}
	created := 0
	for _, o := range owners {
		if e = ctx.Err(); e != nil {
			return out, e
		}
		if opt.ResourceCheck != nil {
			if e = opt.ResourceCheck(); e != nil {
				return out, e
			}
		}
		var exists int
		if e = s.db.QueryRowContext(ctx, "SELECT count(*) FROM batch_projection_owners WHERE owner_kind=? AND owner_id=?", o.Kind, o.ID).Scan(&exists); e != nil {
			return out, e
		}
		if exists == 0 && opt.MaxBatches > 0 && created >= opt.BatchSize*opt.MaxBatches {
			out.Status = "INTERRUPTED_CHECKPOINTED"
			return out, nil
		}
		tx, e := s.db.BeginTx(ctx, nil)
		if e != nil {
			return out, e
		}
		raw, e := batchOwnerRaw(ctx, tx, o)
		var records batchOwnerSummary
		if e == nil {
			records, e = projectBatchOwner(ctx, tx, o, raw)
		}
		if e == nil {
			e = tx.Commit()
		} else {
			tx.Rollback()
		}
		if e != nil {
			return out, fmt.Errorf("BATCH_OWNER %s/%s: %w", o.Kind, o.ID, e)
		}
		if exists == 0 {
			created++
		}
		batchAccumulate(&out, records)
		if opt.Progress != nil {
			opt.Progress("batch_projection_owners", out.Owners)
		}
	}
	tx, e := s.db.BeginTx(ctx, nil)
	if e != nil {
		return out, e
	}
	defer tx.Rollback()
	if e = verifyBatchExtraRows(ctx, tx); e != nil {
		return out, e
	}
	out.ManifestHash, _, e = publishBatchInventory(ctx, tx, true)
	if e != nil {
		return out, e
	}
	if e = tx.Commit(); e != nil {
		return out, e
	}
	out.Status = "PUBLISHED"
	return out, nil
}
func verifyBatchExtraRows(ctx context.Context, tx *sql.Tx) error {
	checks := []string{
		"SELECT count(*) FROM batch_projection_records r LEFT JOIN batch_projection_owners o ON o.owner_kind=r.owner_kind AND o.owner_id=r.owner_id WHERE o.owner_id IS NULL",
		"SELECT count(*) FROM batch_theme_members t LEFT JOIN batch_projection_records r ON r.record_id=t.record_id WHERE r.kind IS NULL OR r.kind!='themes'",
		"SELECT count(*) FROM batch_receipt_parameters t LEFT JOIN batch_projection_records r ON r.record_id=t.record_id WHERE r.kind IS NULL OR r.kind!='receipts'",
	}
	for _, s := range batchSpecs {
		for _, table := range []string{s.table, s.relations} {
			checks = append(checks, "SELECT count(*) FROM "+table+" t LEFT JOIN batch_projection_records r ON r.record_id=t.record_id WHERE r.kind IS NULL OR r.kind!='"+s.kind+"'")
		}
	}
	for _, q := range checks {
		var n int
		if e := tx.QueryRowContext(ctx, q).Scan(&n); e != nil {
			return e
		}
		if n != 0 {
			return errors.New("BATCH_ORPHAN_OR_WRONG_KIND_ROW")
		}
	}
	return nil
}

// VerifyBatchProjections re-derives every projected column and relationship from
// original owner/member bytes. It is independent of the legacy database and
// intentionally does not repeat verification of the historical price table.
func (s *Store) VerifyBatchProjections(ctx context.Context) (BatchProjectionReport, error) {
	out := BatchProjectionReport{Status: "IN_PROGRESS", ConversionVersion: BatchProjectionVersion}
	if e := verifyBatchSchema(ctx, s.db); e != nil {
		return out, e
	}
	tx, e := s.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if e != nil {
		return out, e
	}
	defer tx.Rollback()
	owners, e := batchOwners(ctx, tx)
	if e != nil {
		return out, e
	}
	for _, o := range owners {
		raw, e := batchOwnerRaw(ctx, tx, o)
		if e != nil {
			return out, e
		}
		records, e := walkExpectedBatchOwner(ctx, tx, o, raw, func(p batchProjection) error { return verifyBatchProjection(ctx, tx, p) })
		if e != nil {
			return out, e
		}
		if e = verifyBatchOwner(ctx, tx, o, records, true); e != nil {
			return out, fmt.Errorf("BATCH_VERIFY %s/%s: %w", o.Kind, o.ID, e)
		}
		batchAccumulate(&out, records)
	}
	if e = verifyBatchExtraRows(ctx, tx); e != nil {
		return out, e
	}
	identity, e := batchCurrentIdentity(ctx, tx)
	if e != nil {
		return out, e
	}
	out.ManifestHash = identity["manifest_hash"]
	out.Status = "VERIFIED"
	return out, nil
}

// VerifyBatchProjectionReadinessDB supports backup/lifecycle mode=ro handles.
// It validates actual extension DDL and immutable identity coverage without
// initialization, writes, raw evidence decoding, or historical-price scans.
func VerifyBatchProjectionReadinessDB(ctx context.Context, db *sql.DB) error {
	if e := verifyBatchSchema(ctx, db); e != nil {
		return e
	}
	tx, e := db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if e != nil {
		return e
	}
	defer tx.Rollback()
	_, e = batchCurrentIdentity(ctx, tx)
	return e
}
