package sqlitev2

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	review "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application/review"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"path/filepath"
	"strings"
	"time"
)

// This extension is initialized only by the explicit `review init` writer.
// Existing authority and application schema checksums are unchanged.
const reviewDDL = `
CREATE TABLE review_schema(version TEXT PRIMARY KEY,checksum TEXT NOT NULL);
CREATE TRIGGER review_schema_no_update BEFORE UPDATE ON review_schema BEGIN SELECT RAISE(ABORT,'immutable review schema'); END;
CREATE TRIGGER review_schema_no_delete BEFORE DELETE ON review_schema BEGIN SELECT RAISE(ABORT,'immutable review schema'); END;
CREATE TABLE review_records(sequence INTEGER PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('CONTRACT','SIGNAL','OUTCOME','CANDIDATE','EVALUATION','TRANSITION')),entity_id TEXT NOT NULL,revision INTEGER NOT NULL CHECK(revision>0),payload BLOB NOT NULL,raw_hash TEXT NOT NULL CHECK(length(raw_hash)=64),UNIQUE(kind,entity_id,revision));
CREATE INDEX review_records_entity ON review_records(kind,entity_id,revision);
CREATE TRIGGER review_records_no_update BEFORE UPDATE ON review_records BEGIN SELECT RAISE(ABORT,'immutable review record'); END;
CREATE TRIGGER review_records_no_delete BEFORE DELETE ON review_records BEGIN SELECT RAISE(ABORT,'immutable review record'); END;
`

func (s *Store) InitReview(ctx context.Context) error {
	if err := s.Ready(ctx); err != nil {
		return err
	}
	if err := CheckResources(filepath.Dir(s.Path)); err != nil {
		return err
	}
	var exists int
	if err := s.db.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='review_schema'").Scan(&exists); err != nil {
		return err
	}
	if exists > 0 {
		return verifyReviewSchema(ctx, s.db)
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, reviewDDL); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, "INSERT INTO review_schema VALUES(?,?)", review.JournalVersion, c.Hash([]byte(reviewDDL))); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	return verifyReviewSchema(ctx, s.db)
}

func verifyReviewSchema(ctx context.Context, db *sql.DB) error {
	var hash string
	if err := db.QueryRowContext(ctx, "SELECT checksum FROM review_schema WHERE version=?", review.JournalVersion).Scan(&hash); err != nil {
		return fmt.Errorf("REVIEW_NOT_INITIALIZED_OR_SCHEMA_MISSING: %w", err)
	}
	if hash != c.Hash([]byte(reviewDDL)) {
		return errors.New("REVIEW_SCHEMA_CHECKSUM_MISMATCH")
	}
	expected, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		return err
	}
	defer expected.Close()
	expected.SetMaxOpenConns(1)
	if _, err = expected.ExecContext(ctx, reviewDDL); err != nil {
		return err
	}
	rows, err := expected.QueryContext(ctx, "SELECT name,type,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var name, kind, want, actual string
		if err = rows.Scan(&name, &kind, &want); err != nil {
			return err
		}
		if err = db.QueryRowContext(ctx, "SELECT sql FROM sqlite_master WHERE name=? AND type=?", name, kind).Scan(&actual); err != nil {
			return fmt.Errorf("REVIEW_SCHEMA_OBJECT_MISSING: %s: %w", name, err)
		}
		if actual != want {
			return fmt.Errorf("REVIEW_SCHEMA_OBJECT_CHANGED: %s", name)
		}
	}
	return rows.Err()
}

func reviewLedgerID(r review.Record) string {
	return r.Kind + ":" + r.ID + ":" + fmt.Sprint(r.Revision)
}

func readReviewTx(ctx context.Context, tx *sql.Tx) ([]review.Record, error) {
	var total int64
	if err := tx.QueryRowContext(ctx, "SELECT coalesce(sum(length(payload)),0) FROM review_records").Scan(&total); err != nil {
		return nil, err
	}
	if total > review.MaxJournalBytes {
		return nil, errors.New("REVIEW_JOURNAL_OUTPUT_LIMIT; NO_PARTIAL_SUCCESS")
	}
	rows, err := tx.QueryContext(ctx, "SELECT sequence,kind,entity_id,revision,payload,raw_hash FROM review_records ORDER BY sequence")
	if err != nil {
		return nil, err
	}
	out := []review.Record{}
	size := 0
	for rows.Next() {
		var r review.Record
		if err = rows.Scan(&r.Sequence, &r.Kind, &r.ID, &r.Revision, &r.Payload, &r.Hash); err != nil {
			rows.Close()
			return nil, err
		}
		size += len(r.Payload)
		if size > review.MaxJournalBytes {
			rows.Close()
			return nil, errors.New("REVIEW_JOURNAL_OUTPUT_LIMIT; NO_PARTIAL_SUCCESS")
		}
		if c.Hash(r.Payload) != r.Hash {
			rows.Close()
			return nil, errors.New("REVIEW_RECORD_CONTENT_CORRUPT")
		}
		out = append(out, r)
	}
	if err = rows.Err(); err != nil {
		rows.Close()
		return nil, err
	}
	rows.Close()
	var count int
	if err = tx.QueryRowContext(ctx, "SELECT count(*) FROM app_change_ledger WHERE kind='REVIEW_RECORD'").Scan(&count); err != nil {
		return nil, err
	}
	if count != len(out) {
		return nil, errors.New("REVIEW_JOURNAL_MEMBERSHIP_CORRUPT")
	}
	for _, r := range out {
		var n int
		if err = tx.QueryRowContext(ctx, "SELECT count(*) FROM app_change_ledger WHERE kind='REVIEW_RECORD' AND entity_id=? AND content_hash=?", reviewLedgerID(r), r.Hash).Scan(&n); err != nil {
			return nil, err
		}
		if n != 1 {
			return nil, errors.New("REVIEW_RECORD_LEDGER_BINDING_CORRUPT")
		}
	}
	return out, nil
}

func (r *ReadOnly) ReviewRecords(ctx context.Context) ([]review.Record, error) {
	return readReview(ctx, r.db)
}
func (s *Store) ReviewRecords(ctx context.Context) ([]review.Record, error) {
	return readReview(ctx, s.db)
}

func readReview(ctx context.Context, db *sql.DB) ([]review.Record, error) {
	if err := verifyReviewSchema(ctx, db); err != nil {
		return nil, err
	}
	tx, err := db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	rows, err := readReviewTx(ctx, tx)
	if err != nil {
		return nil, err
	}
	return rows, tx.Commit()
}

func (s *Store) AppendReview(ctx context.Context, records []review.Record) (int, error) {
	if err := s.Ready(ctx); err != nil {
		return 0, err
	}
	if err := verifyReviewSchema(ctx, s.db); err != nil {
		return 0, err
	}
	if err := CheckResources(filepath.Dir(s.Path)); err != nil {
		return 0, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	old, err := readReviewTx(ctx, tx)
	if err != nil {
		return 0, err
	}
	size := 0
	byID := map[string]review.Record{}
	lastRevision := map[string]int{}
	for _, r := range old {
		size += len(r.Payload)
		byID[reviewLedgerID(r)] = r
		key := r.Kind + ":" + r.ID
		if r.Revision > lastRevision[key] {
			lastRevision[key] = r.Revision
		}
	}
	added := 0
	for _, r := range records {
		if err = ctx.Err(); err != nil {
			return 0, err
		}
		if r.ID == "" || strings.ContainsRune(r.ID, '\x00') || r.Revision < 1 || c.Hash(r.Payload) != r.Hash {
			return 0, errors.New("REVIEW_RECORD_IDENTITY_INVALID")
		}
		key := reviewLedgerID(r)
		if prior, ok := byID[key]; ok {
			if prior.Hash != r.Hash {
				return 0, errors.New("REVIEW_IDEMPOTENCY_CONFLICT")
			}
			continue
		}
		if r.Revision != lastRevision[r.Kind+":"+r.ID]+1 {
			return 0, errors.New("REVIEW_REVISION_GAP")
		}
		size += len(r.Payload)
		if size > review.MaxJournalBytes {
			return 0, errors.New("REVIEW_JOURNAL_OUTPUT_LIMIT; NO_PARTIAL_SUCCESS")
		}
		if _, err = tx.ExecContext(ctx, "INSERT INTO review_records(kind,entity_id,revision,payload,raw_hash) VALUES(?,?,?,?,?)", r.Kind, r.ID, r.Revision, []byte(r.Payload), r.Hash); err != nil {
			return 0, err
		}
		if err = appLedger(ctx, tx, "REVIEW_RECORD", key, r.Hash); err != nil {
			return 0, err
		}
		byID[key] = r
		lastRevision[r.Kind+":"+r.ID] = r.Revision
		added++
	}
	return added, tx.Commit()
}

// ReviewInput consumes registered immutable input bytes on a read-only handle.
func (r *ReadOnly) ReviewInput(ctx context.Context, id string) (c.InputRecord, error) {
	return NewReadOnlyAppRepository(r.db).Input(ctx, id)
}
func (r *ReadOnly) ReviewHistory(ctx context.Context, ref, hash string, b ports.ResearchBudget) (*History, error) {
	return (&Store{db: r.db}).History(ctx, ref, hash, b)
}

// SUCCEEDED and PUBLISHED are recorded in the same transaction before commit.
// This timestamp is a lower bound, never an actual availability timestamp.
func (r *ReadOnly) ReviewAvailabilityLowerBound(ctx context.Context, run c.Run, coverage string) (string, error) {
	if coverage == "TEST_FIXTURE" {
		return run.CreatedAt, nil
	}
	rows, err := r.db.QueryContext(ctx, "SELECT "+appJobColumns+" FROM app_jobs WHERE run_id=? AND state='SUCCEEDED'", run.ID)
	if err != nil {
		return "", err
	}
	defer rows.Close()
	var latest time.Time
	raw := ""
	for rows.Next() {
		j, err := appScanJob(rows)
		if err != nil {
			return "", err
		}
		t, err := time.Parse(time.RFC3339Nano, j.UpdatedAt)
		if err != nil {
			return "", err
		}
		if t.After(latest) {
			latest = t
			raw = j.UpdatedAt
		}
	}
	return raw, rows.Err()
}

// VerifyReviewChange keeps the authority recovery/export ledger closed over the
// new consumer extension. It rechecks the full journal membership and content;
// an existence-only lookup cannot prove a complete or untampered append set.
func VerifyReviewChange(ctx context.Context, db *sql.DB, id, hash string) error {
	if !ev.ValidHash(hash) {
		return errors.New("REVIEW_CHANGE_HASH_INVALID")
	}
	rows, err := readReview(ctx, db)
	if err != nil {
		return err
	}
	for _, r := range rows {
		if reviewLedgerID(r) == id {
			if r.Hash != hash {
				return errors.New("REVIEW_CHANGE_CONTENT_HASH_MISMATCH")
			}
			return nil
		}
	}
	return errors.New("REVIEW_CHANGE_RECORD_MISSING")
}
