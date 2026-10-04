package sqlitev2

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"os"
	"sort"
	"time"
)

// ReadOnly exposes no mutation operation. There is no lease, schema initializer,
// immutable URI, cache, checkpoint or migration verification on this path.
type ReadOnly struct {
	db       *sql.DB
	identity map[string]string
}

func OpenReadOnly(ctx context.Context, path string) (*ReadOnly, error) {
	started := time.Now()
	defer func() {
		ports.Observe(ports.BudgetFromContext(ctx), "readonly_open_schema_identity", time.Since(started).Seconds())
	}()
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
	started := time.Now()
	defer func() {
		ports.Observe(ports.BudgetFromContext(ctx), "readonly_readiness", time.Since(started).Seconds())
	}()
	return NewReadOnlyAppRepository(r.db).Ready(ctx)
}
func (r *ReadOnly) Runs(ctx context.Context, limit, offset int) ([]c.Run, error) {
	return NewReadOnlyAppRepository(r.db).Runs(ctx, limit, offset)
}
func (r *ReadOnly) Inputs(ctx context.Context, observation string) ([]c.InputRecord, error) {
	started := time.Now()
	defer func() {
		ports.Observe(ports.BudgetFromContext(ctx), "planner_input_query", time.Since(started).Seconds())
	}()
	rows, e := r.db.QueryContext(ctx, "SELECT input_id,raw_hash,observation,cutoff,coverage FROM app_inputs WHERE observation=? ORDER BY input_id,raw_hash", observation)
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
	if e = rows.Err(); e != nil {
		return nil, e
	}
	// Raw cutoff text remains unchanged. Invalid timestamps sort after all valid
	// timestamps; eligibility (future/intraday/session) is the planner's concern.
	sort.SliceStable(out, func(i, j int) bool {
		a, ea := time.Parse(time.RFC3339Nano, out[i].Cutoff)
		b, eb := time.Parse(time.RFC3339Nano, out[j].Cutoff)
		if (ea == nil) != (eb == nil) {
			return ea == nil
		}
		if ea == nil && !a.Equal(b) {
			return a.After(b)
		}
		if out[i].ID != out[j].ID {
			return out[i].ID < out[j].ID
		}
		return out[i].RawHash < out[j].RawHash
	})
	return out, nil
}

// MatchingRuns filters at the database boundary instead of scanning unrelated
// historical pages. Publication header identities are verified by appScanRun.
func (r *ReadOnly) MatchingRuns(ctx context.Context, input, strategy string, limit, offset int) ([]c.Run, error) {
	started := time.Now()
	defer func() {
		ports.Observe(ports.BudgetFromContext(ctx), "planner_matching_runs", time.Since(started).Seconds())
	}()
	if limit < 1 || limit > 200 || offset < 0 {
		return nil, errors.New("INVALID_RUN_PAGE")
	}
	rows, e := r.db.QueryContext(ctx, "SELECT "+appRunColumns+" FROM app_runs WHERE state='PUBLISHED' AND input_id=? AND strategy_id=? ORDER BY created_at DESC,run_id LIMIT ? OFFSET ?", input, strategy, limit, offset)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []c.Run{}
	for rows.Next() {
		run, e := appScanRun(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, run)
	}
	return out, rows.Err()
}

// Saved verifies every detail and derived summary, then selects the requested
// projection from the same consistent transaction. Pagination is internal.
func (r *ReadOnly) Saved(ctx context.Context, id, instrument string, all bool) (c.Run, error) {
	started := time.Now()
	defer func() {
		ports.Observe(ports.BudgetFromContext(ctx), "saved_projection_total", time.Since(started).Seconds())
	}()
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
	run, e := appScanRun(tx.QueryRowContext(ctx, "SELECT "+appRunColumns+" FROM app_runs WHERE run_id=?", id))
	if e != nil {
		return run, e
	}
	type selected struct {
		item          c.Item
		ordinal, rank int
	}
	items := []selected{}
	expanded := int64(0)
	e = repo.verifyPublishedCollectTx(ctx, tx, id, func(item c.Item, ordinal, rank int, status string) error {
		if instrument != "" && item.ID != instrument {
			return nil
		}
		if instrument == "" && !all && status != "CORE_PASS" && status != "FOCUS" && status != "WATCH" {
			return nil
		}
		expanded += int64(len(item.Payload))
		if expanded > 128<<20 {
			return errors.New("DETAIL_OUTPUT_LIMIT")
		}
		items = append(items, selected{item, ordinal, rank})
		return nil
	})
	if e != nil {
		return c.Run{}, e
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].rank != items[j].rank {
			return items[i].rank < items[j].rank
		}
		return items[i].ordinal < items[j].ordinal
	})
	run.Items = []c.Item{}
	for _, item := range items {
		run.Items = append(run.Items, item.item)
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
