package sqlitev2

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

// ABANDONED_RUN is an immutable recovery disposition, not a new run state.
// The original PREPARING header, items and summary projections remain intact.
const abandonedRunKind = "ABANDONED_RUN"

func ensureRunNotAbandoned(ctx context.Context, tx *sql.Tx, id string) error {
	var n int
	if e := tx.QueryRowContext(ctx, "SELECT count(*) FROM app_change_ledger WHERE kind=? AND entity_id=?", abandonedRunKind, id).Scan(&n); e != nil {
		return e
	}
	if n != 0 {
		return errors.New("ABANDONED_RUN_CANNOT_PUBLISH")
	}
	return nil
}

// abandonedRunDigest verifies every currently stored original payload and its
// summary projection before hashing the full header and ordered partial sets.
// The expected total may exceed the preserved prefix after an interrupted batch.
func abandonedRunDigest(ctx context.Context, tx *sql.Tx, id string) (string, error) {
	var input, strategy, created, implementation, availability, mh, sh, state string
	var manifest, summary []byte
	var members sql.NullString
	var expected int
	if e := tx.QueryRowContext(ctx, "SELECT input_id,strategy_id,created_at,implementation_status,candidate_availability,manifest,manifest_hash,summary,summary_hash,state,item_count,members_hash FROM app_runs WHERE run_id=?", id).Scan(&input, &strategy, &created, &implementation, &availability, &manifest, &mh, &summary, &sh, &state, &expected, &members); e != nil {
		return "", e
	}
	if state != "PREPARING" {
		return "", errors.New("ONLY_UNPUBLISHED_RUN_CAN_BE_ABANDONED")
	}
	if appHash(manifest) != mh || appHash(summary) != sh || !json.Valid(manifest) || !json.Valid(summary) {
		return "", errors.New("ABANDONED_RUN_HEADER_CORRUPT")
	}
	hash := sha256.New()
	frame := func(v any) error {
		raw, e := json.Marshal(v)
		if e != nil {
			return e
		}
		_, e = hash.Write(append(raw, '\n'))
		return e
	}
	if e := frame([]any{"GO_ABANDONED_RUN_V1", id, input, strategy, created, implementation, availability, mh, sh, state, expected, members.Valid, members.String}); e != nil {
		return "", e
	}
	rows, e := tx.QueryContext(ctx, "SELECT i.instrument_id,i.ordinal,i.payload,i.raw_hash,i.raw_size,s.ordinal,s.display_rank,s.decision_status,s.payload,s.raw_hash,s.source_hash,s.projection_version FROM app_items i LEFT JOIN app_item_summaries s ON s.run_id=i.run_id AND s.instrument_id=i.instrument_id WHERE i.run_id=? ORDER BY i.ordinal", id)
	if e != nil {
		return "", e
	}
	n := 0
	fullMembers := sha256.New()
	for rows.Next() {
		if e = ctx.Err(); e != nil {
			rows.Close()
			return "", e
		}
		var iid, h string
		var ordinal int
		var size int64
		var packed, projection []byte
		var so, rank sql.NullInt64
		var status, ph, sourceHash, version sql.NullString
		if e = rows.Scan(&iid, &ordinal, &packed, &h, &size, &so, &rank, &status, &projection, &ph, &sourceHash, &version); e != nil {
			rows.Close()
			return "", e
		}
		raw, err := appUnpack(packed, size)
		if err != nil {
			rows.Close()
			return "", err
		}
		if appHash(raw) != h || ordinal != n || n >= expected {
			rows.Close()
			return "", errors.New("ABANDONED_RUN_DETAIL_CORRUPT")
		}
		want, wantStatus, wantRank, err := appItemSummary(c.Item{ID: iid, Payload: raw}, ordinal)
		if err != nil {
			rows.Close()
			return "", err
		}
		if !so.Valid || int(so.Int64) != ordinal || !rank.Valid || int(rank.Int64) != wantRank || !status.Valid || status.String != wantStatus || !ph.Valid || ph.String != appHash(projection) || !sourceHash.Valid || sourceHash.String != h || !version.Valid || version.String != "GO_RESULT_SUMMARY_V1" || !bytes.Equal(projection, want) {
			rows.Close()
			return "", errors.New("ABANDONED_RUN_SUMMARY_CORRUPT")
		}
		if e = frame([]any{"item", iid, ordinal, h, size, appHash(packed), so.Int64, rank.Int64, status.String, ph.String, sourceHash.String, version.String}); e != nil {
			rows.Close()
			return "", e
		}
		fmt.Fprintf(fullMembers, "%d\x00%s\x00%s\n", ordinal, iid, h)
		n++
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return "", e
	}
	var summaries int
	if e = tx.QueryRowContext(ctx, "SELECT count(*) FROM app_item_summaries WHERE run_id=?", id).Scan(&summaries); e != nil {
		return "", e
	}
	if summaries != n {
		return "", errors.New("ABANDONED_RUN_ORPHAN_SUMMARY")
	}
	if members.Valid && (n != expected || members.String != hex.EncodeToString(fullMembers.Sum(nil))) {
		return "", errors.New("ABANDONED_RUN_MEMBERS_CORRUPT")
	}
	if e = frame([]any{"preserved_items", n, "preserved_summaries", summaries}); e != nil {
		return "", e
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

func abandonedLedgerHash(ctx context.Context, tx *sql.Tx, id string) (string, bool, error) {
	var count int
	var hash sql.NullString
	if e := tx.QueryRowContext(ctx, "SELECT count(*),min(content_hash) FROM app_change_ledger WHERE kind=? AND entity_id=?", abandonedRunKind, id).Scan(&count, &hash); e != nil {
		return "", false, e
	}
	if count > 1 {
		return "", false, errors.New("CONFLICTING_ABANDONED_RUN_LEDGER")
	}
	return hash.String, count == 1, nil
}

// AbandonUnpublishedRuns explicitly seals interrupted output only while there
// are no queued/running jobs. Repeated calls reverify existing seals. It never
// deletes rows, rewrites run metadata, or returns a partial run as published.
func (r *AppRepository) AbandonUnpublishedRuns(ctx context.Context) ([]string, error) {
	if e := r.resourceCheck(); e != nil {
		return nil, e
	}
	tx, e := r.db.BeginTx(ctx, nil)
	if e != nil {
		return nil, e
	}
	defer tx.Rollback()
	var active int
	if e = tx.QueryRowContext(ctx, "SELECT count(*) FROM app_jobs WHERE state IN ('QUEUED','RUNNING')").Scan(&active); e != nil {
		return nil, e
	}
	if active != 0 {
		return nil, errors.New("ACTIVE_JOBS_PREVENT_RUN_ABANDONMENT")
	}
	rows, e := tx.QueryContext(ctx, "SELECT run_id FROM app_runs WHERE state='PREPARING' ORDER BY run_id")
	if e != nil {
		return nil, e
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if e = rows.Scan(&id); e != nil {
			rows.Close()
			return nil, e
		}
		ids = append(ids, id)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	abandoned := []string{}
	for _, id := range ids {
		current, e := abandonedRunDigest(ctx, tx, id)
		if e != nil {
			return nil, fmt.Errorf("abandon %s: %w", id, e)
		}
		previous, found, e := abandonedLedgerHash(ctx, tx, id)
		if e != nil {
			return nil, e
		}
		if found {
			if previous != current {
				return nil, errors.New("ABANDONED_RUN_CONTENT_CHANGED")
			}
			continue
		}
		if e = appLedger(ctx, tx, abandonedRunKind, id, current); e != nil {
			return nil, e
		}
		abandoned = append(abandoned, id)
	}
	if e = tx.Commit(); e != nil {
		return nil, e
	}
	return abandoned, nil
}

func (r *AppRepository) VerifyAbandonedRun(ctx context.Context, id string) error {
	tx, e := r.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if e != nil {
		return e
	}
	defer tx.Rollback()
	want, found, e := abandonedLedgerHash(ctx, tx, id)
	if e != nil {
		return e
	}
	if !found {
		return errors.New("ABANDONED_RUN_SEAL_REQUIRED")
	}
	actual, e := abandonedRunDigest(ctx, tx, id)
	if e != nil {
		return e
	}
	if actual != want {
		return errors.New("ABANDONED_RUN_CONTENT_CHANGED")
	}
	return nil
}
