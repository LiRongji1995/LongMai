package sqlitev2

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	review "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application/review"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"path/filepath"
	"time"
)

// A separate extension preserves every byte of JOURNAL_V1's immutable schema.
const reviewAvailabilityDDL = `
CREATE TABLE review_availability_schema(version TEXT PRIMARY KEY,checksum TEXT NOT NULL);
CREATE TRIGGER review_availability_schema_no_update BEFORE UPDATE ON review_availability_schema BEGIN SELECT RAISE(ABORT,'immutable availability schema'); END;
CREATE TRIGGER review_availability_schema_no_delete BEFORE DELETE ON review_availability_schema BEGIN SELECT RAISE(ABORT,'immutable availability schema'); END;
CREATE TABLE review_availability_records(sequence INTEGER PRIMARY KEY,candidate_id TEXT NOT NULL UNIQUE,candidate_record_hash TEXT NOT NULL CHECK(length(candidate_record_hash)=64),payload BLOB NOT NULL,raw_hash TEXT NOT NULL CHECK(length(raw_hash)=64));
CREATE INDEX review_availability_candidate ON review_availability_records(candidate_record_hash);
CREATE TRIGGER review_availability_records_no_update BEFORE UPDATE ON review_availability_records BEGIN SELECT RAISE(ABORT,'immutable availability record'); END;
CREATE TRIGGER review_availability_records_no_delete BEFORE DELETE ON review_availability_records BEGIN SELECT RAISE(ABORT,'immutable availability record'); END;
`

func verifyReviewAvailabilitySchema(ctx context.Context, db reviewSchemaQuery) (bool, error) {
	var count int
	if err := db.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE substr(name,1,length('review_availability'))='review_availability' AND sql IS NOT NULL").Scan(&count); err != nil {
		return false, err
	}
	if count == 0 {
		return false, nil
	}
	if count != 7 {
		return false, errors.New("REVIEW_AVAILABILITY_SCHEMA_PARTIAL_OR_EXTRA")
	}
	var version, hash string
	var rowsCount int
	if err := db.QueryRowContext(ctx, "SELECT count(*) FROM review_availability_schema").Scan(&rowsCount); err != nil {
		return false, fmt.Errorf("REVIEW_AVAILABILITY_SCHEMA_PARTIAL: %w", err)
	}
	if rowsCount != 1 {
		return false, errors.New("REVIEW_AVAILABILITY_SCHEMA_VERSION_COUNT_INVALID")
	}
	if err := db.QueryRowContext(ctx, "SELECT version,checksum FROM review_availability_schema").Scan(&version, &hash); err != nil {
		return false, err
	}
	if version != review.CandidateAvailabilityVersion || hash != c.Hash([]byte(reviewAvailabilityDDL)) {
		return false, errors.New("REVIEW_AVAILABILITY_SCHEMA_CHECKSUM_MISMATCH")
	}
	expected, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		return false, err
	}
	defer expected.Close()
	expected.SetMaxOpenConns(1)
	if _, err = expected.ExecContext(ctx, reviewAvailabilityDDL); err != nil {
		return false, err
	}
	objects, err := expected.QueryContext(ctx, "SELECT name,type,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")
	if err != nil {
		return false, err
	}
	defer objects.Close()
	for objects.Next() {
		var name, kind, want, actual string
		if err = objects.Scan(&name, &kind, &want); err != nil {
			return false, err
		}
		if err = db.QueryRowContext(ctx, "SELECT sql FROM sqlite_master WHERE name=? AND type=?", name, kind).Scan(&actual); err != nil {
			return false, fmt.Errorf("REVIEW_AVAILABILITY_SCHEMA_OBJECT_MISSING: %s: %w", name, err)
		}
		if actual != want {
			return false, fmt.Errorf("REVIEW_AVAILABILITY_SCHEMA_OBJECT_CHANGED: %s", name)
		}
	}
	return true, objects.Err()
}

func (s *Store) initReviewAvailability(ctx context.Context) error {
	present, err := verifyReviewAvailabilitySchema(ctx, s.db)
	if err != nil {
		return err
	}
	if present {
		_, err = readReview(ctx, s.db)
		return err
	}
	if _, err = readReview(ctx, s.db); err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = readReviewTx(ctx, tx); err != nil {
		return err
	}
	if present, err = verifyReviewAvailabilitySchema(ctx, tx); err != nil {
		return err
	} else if present {
		return tx.Commit()
	}
	if _, err = tx.ExecContext(ctx, reviewAvailabilityDDL); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, "INSERT INTO review_availability_schema VALUES(?,?)", review.CandidateAvailabilityVersion, c.Hash([]byte(reviewAvailabilityDDL))); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	_, err = readReview(ctx, s.db)
	return err
}

func readReviewAvailabilityTx(ctx context.Context, tx *sql.Tx, declarations []review.Record) ([]review.Record, error) {
	var exists int
	if err := tx.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='review_availability_records'").Scan(&exists); err != nil {
		return nil, err
	}
	if exists == 0 {
		return nil, nil
	}
	var size int64
	if err := tx.QueryRowContext(ctx, "SELECT coalesce(sum(length(payload)),0) FROM review_availability_records").Scan(&size); err != nil {
		return nil, err
	}
	for _, r := range declarations {
		size += int64(len(r.Payload))
	}
	if size > review.MaxJournalBytes {
		return nil, errors.New("REVIEW_JOURNAL_OUTPUT_LIMIT; NO_PARTIAL_SUCCESS")
	}
	rows, err := tx.QueryContext(ctx, "SELECT sequence,candidate_id,candidate_record_hash,payload,raw_hash FROM review_availability_records ORDER BY sequence")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []review.Record{}
	for rows.Next() {
		r := review.Record{Kind: review.CandidateAvailabilityKind, Revision: 1}
		var declarationHash string
		var payload []byte
		if err = rows.Scan(&r.Sequence, &r.ID, &declarationHash, &payload, &r.Hash); err != nil {
			return nil, err
		}
		r.Payload = payload
		if c.Hash(payload) != r.Hash {
			return nil, errors.New("REVIEW_AVAILABILITY_RECORD_CONTENT_CORRUPT")
		}
		_, declaration, err := review.CandidateDeclarationRecord(declarations, r.ID)
		if err != nil {
			return nil, fmt.Errorf("REVIEW_AVAILABILITY_DECLARATION_MISSING: %w", err)
		}
		p, err := review.ValidateCandidateAvailability(declaration, r)
		if err != nil {
			return nil, err
		}
		if p.DeclarationHash != declarationHash {
			return nil, errors.New("REVIEW_AVAILABILITY_COLUMN_BINDING_CORRUPT")
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// ObserveCandidateAvailability accepts no caller timestamp or proof payload.
// A fresh read transaction verifies schema, members and ledger and commits
// before now is captured; the declaration's own commit is already visible.
func (s *Store) ObserveCandidateAvailability(ctx context.Context, id string) (review.CandidateAvailability, bool, error) {
	var empty review.CandidateAvailability
	if err := s.Ready(ctx); err != nil {
		return empty, false, err
	}
	if err := CheckResources(filepath.Dir(s.Path)); err != nil {
		return empty, false, err
	}
	readTx, err := s.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return empty, false, err
	}
	defer readTx.Rollback()
	records, err := readReviewTx(ctx, readTx)
	if err != nil {
		return empty, false, err
	}
	present, err := verifyReviewAvailabilitySchema(ctx, readTx)
	if err != nil {
		return empty, false, err
	}
	if !present {
		return empty, false, errors.New("CANDIDATE_AVAILABILITY_EXTENSION_NOT_INITIALIZED; EXPLICIT_REVIEW_INIT_REQUIRED")
	}
	if err = readTx.Commit(); err != nil {
		return empty, false, err
	}
	v, declaration, err := review.CandidateDeclarationRecord(records, id)
	if err != nil {
		return empty, false, err
	}
	if v.FixtureOnly {
		return empty, false, errors.New("CANDIDATE_AVAILABILITY_FIXTURE_CANNOT_ATTEST_REAL_PUBLICATION")
	}
	for _, r := range records {
		if r.Kind == review.CandidateAvailabilityKind && r.ID == id {
			p, err := review.ValidateCandidateAvailability(declaration, r)
			return p, false, err
		}
	}
	observed := time.Now().UTC()
	start, err := review.CandidateEvaluationOpen(v)
	if err != nil {
		return empty, false, err
	}
	p := review.CandidateAvailability{Version: review.CandidateAvailabilityVersion, CandidateID: id, DeclarationHash: declaration.Hash, DeclarationRevision: 1, TimeSplitContract: v.TimeSplitContract, ContractHash: v.ContractHash, FirstSessionOpen: start.Format(time.RFC3339Nano), AvailableUpperBound: observed.Format(time.RFC3339Nano), Basis: review.CandidateAvailabilityBasis, Mode: "POST_COMMIT_OBSERVED_REAL_DECLARATION"}
	r, err := review.NewRecord(review.CandidateAvailabilityKind, id, 1, p)
	if err != nil {
		return empty, false, err
	}
	if _, err = review.ValidateCandidateAvailability(declaration, r); err != nil {
		return empty, false, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return empty, false, err
	}
	defer tx.Rollback()
	current, err := readReviewTx(ctx, tx)
	if err != nil {
		return empty, false, err
	}
	_, currentDeclaration, err := review.CandidateDeclarationRecord(current, id)
	if err != nil || currentDeclaration.Hash != declaration.Hash {
		return empty, false, errors.New("CANDIDATE_AVAILABILITY_DECLARATION_CHANGED")
	}
	for _, old := range current {
		if old.Kind == review.CandidateAvailabilityKind && old.ID == id {
			saved, err := review.ValidateCandidateAvailability(currentDeclaration, old)
			return saved, false, err
		}
	}
	size := len(r.Payload)
	for _, old := range current {
		size += len(old.Payload)
	}
	if size > review.MaxJournalBytes {
		return empty, false, errors.New("REVIEW_JOURNAL_OUTPUT_LIMIT; NO_PARTIAL_SUCCESS")
	}
	if _, err = tx.ExecContext(ctx, "INSERT INTO review_availability_records(candidate_id,candidate_record_hash,payload,raw_hash) VALUES(?,?,?,?)", id, declaration.Hash, []byte(r.Payload), r.Hash); err != nil {
		return empty, false, err
	}
	if err = appLedger(ctx, tx, "REVIEW_RECORD", reviewLedgerID(r), r.Hash); err != nil {
		return empty, false, err
	}
	if err = tx.Commit(); err != nil {
		return empty, false, err
	}
	return p, true, nil
}
