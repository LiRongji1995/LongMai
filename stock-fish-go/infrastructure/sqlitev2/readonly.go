package sqlitev2

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"os"
)

// ReadOnly exposes no mutation operation. There is no lease, schema initializer,
// immutable URI, cache, checkpoint or migration verification on this path.
type ReadOnly struct {
	db       *sql.DB
	identity map[string]string
}

func OpenReadOnly(ctx context.Context, path string) (*ReadOnly, error) {
	if _, e := os.Stat(path); e != nil {
		return nil, e
	}
	db, e := sql.Open("sqlite", URI(path, true))
	if e != nil {
		return nil, e
	}
	db.SetMaxOpenConns(1)
	fail := func(e error) (*ReadOnly, error) { db.Close(); return nil, e }
	if e = VerifySchemaDB(ctx, db); e != nil {
		return fail(e)
	}
	if e = VerifyApplicationSchemaDB(ctx, db); e != nil {
		return fail(e)
	}
	s := &Store{db: db, Path: path}
	identity, e := s.PublishedResearchIdentity(ctx)
	if e != nil {
		return fail(e)
	}
	return &ReadOnly{db, identity}, nil
}
func (r *ReadOnly) Close() error { return r.db.Close() }
func (r *ReadOnly) Identity() map[string]string {
	out := map[string]string{}
	for k, v := range r.identity {
		out[k] = v
	}
	return out
}
func (r *ReadOnly) Ready(ctx context.Context) (c.Readiness, error) {
	return NewReadOnlyAppRepository(r.db).Ready(ctx)
}
func (r *ReadOnly) Runs(ctx context.Context, limit, offset int) ([]c.Run, error) {
	return NewReadOnlyAppRepository(r.db).Runs(ctx, limit, offset)
}
func (r *ReadOnly) Inputs(ctx context.Context, observation string) ([]c.InputRecord, error) {
	rows, e := r.db.QueryContext(ctx, "SELECT input_id,raw_hash,observation,cutoff,coverage FROM app_inputs WHERE observation=? ORDER BY cutoff DESC,input_id", observation)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []c.InputRecord{}
	for rows.Next() {
		var in c.InputRecord
		if e = rows.Scan(&in.ID, &in.RawHash, &in.Observation, &in.Cutoff, &in.Coverage); e != nil {
			return nil, e
		}
		out = append(out, in)
	}
	return out, rows.Err()
}

// Saved verifies every detail and derived summary, then selects the requested
// projection from the same consistent transaction. Pagination is internal.
func (r *ReadOnly) Saved(ctx context.Context, id, instrument string, all bool) (c.Run, error) {
	tx, e := r.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if e != nil {
		return c.Run{}, e
	}
	defer tx.Rollback()
	var state string
	if e = tx.QueryRowContext(ctx, "SELECT state FROM app_runs WHERE run_id=?", id).Scan(&state); e != nil {
		return c.Run{}, e
	}
	if state != "PUBLISHED" {
		return c.Run{}, errors.New("RUN_NOT_PUBLISHED")
	}
	if e = ensureRunNotAbandoned(ctx, tx, id); e != nil {
		return c.Run{}, e
	}
	repo := NewReadOnlyAppRepository(r.db)
	if e = repo.verifyPublishedTx(ctx, tx, id); e != nil {
		return c.Run{}, e
	}
	run, e := appScanRun(tx.QueryRowContext(ctx, "SELECT "+appRunColumns+" FROM app_runs WHERE run_id=?", id))
	if e != nil {
		return run, e
	}
	rows, e := tx.QueryContext(ctx, "SELECT i.instrument_id,i.payload,i.raw_hash,i.raw_size,s.decision_status FROM app_items i JOIN app_item_summaries s USING(run_id,instrument_id) WHERE i.run_id=? ORDER BY s.display_rank,i.ordinal", id)
	if e != nil {
		return run, e
	}
	defer rows.Close()
	run.Items = []c.Item{}
	expanded := int64(0)
	for rows.Next() {
		var iid, hash, status string
		var packed []byte
		var n int64
		if e = rows.Scan(&iid, &packed, &hash, &n, &status); e != nil {
			return run, e
		}
		if instrument != "" && iid != instrument {
			continue
		}
		if instrument == "" && !all && status != "CORE_PASS" && status != "FOCUS" && status != "WATCH" {
			continue
		}
		expanded += n
		if expanded > 128<<20 {
			return run, errors.New("DETAIL_OUTPUT_LIMIT")
		}
		raw, e := appUnpack(packed, n)
		if e != nil {
			return run, e
		}
		if appHash(raw) != hash {
			return run, errors.New("RESULT_DETAIL_CORRUPT")
		}
		run.Items = append(run.Items, c.Item{ID: iid, Payload: json.RawMessage(raw)})
	}
	if e = rows.Err(); e != nil {
		return run, e
	}
	if instrument != "" && len(run.Items) == 0 {
		var raw []byte
		var hash string
		if e = tx.QueryRowContext(ctx, "SELECT payload,raw_hash FROM app_inputs WHERE input_id=?", run.InputID).Scan(&raw, &hash); e != nil {
			return run, e
		}
		if appHash(raw) != hash {
			return run, errors.New("INPUT_BYTES_CORRUPT")
		}
		var envelope struct {
			Input struct {
				Universe struct {
					Securities []struct {
						ID string `json:"instrument_id"`
					} `json:"securities"`
				} `json:"universe"`
			} `json:"input"`
		}
		if json.Unmarshal(raw, &envelope) != nil {
			return run, errors.New("SAVED_SCOPE_UNAVAILABLE")
		}
		for _, security := range envelope.Input.Universe.Securities {
			if security.ID == instrument {
				return run, errors.New("INSTRUMENT_IN_SCOPE_NOT_EVALUATED")
			}
		}
		return run, errors.New("INSTRUMENT_OUTSIDE_SAVED_SCOPE")
	}
	return run, ctx.Err()
}

// DailyReadOnly opens a separate read connection while the server's single
// writer continues. It cannot fall back to another database.
func (r *AppRepository) DailyReadOnly(ctx context.Context) (*ReadOnly, error) {
	if r.store == nil {
		return nil, errors.New("SERVER_DATABASE_OWNER_REQUIRED")
	}
	return OpenReadOnly(ctx, r.store.Path)
}
func (r *AppRepository) DatabasePath() string {
	if r.store == nil {
		return ""
	}
	return r.store.Path
}
