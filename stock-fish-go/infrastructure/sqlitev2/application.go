package sqlitev2

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"io"
	"path/filepath"
	"time"
)

const appDDL = `
CREATE TABLE IF NOT EXISTS app_schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_schema(version INTEGER PRIMARY KEY CHECK(version=1));
INSERT INTO app_schema VALUES(1) ON CONFLICT(version) DO NOTHING;
CREATE TABLE IF NOT EXISTS app_inputs(input_id TEXT PRIMARY KEY,raw_hash TEXT NOT NULL CHECK(length(raw_hash)=64),payload BLOB NOT NULL,observation TEXT NOT NULL,cutoff TEXT NOT NULL,coverage TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_runs(run_id TEXT PRIMARY KEY,input_id TEXT NOT NULL REFERENCES app_inputs(input_id),strategy_id TEXT NOT NULL,created_at TEXT NOT NULL,implementation_status TEXT NOT NULL,candidate_availability TEXT NOT NULL,manifest BLOB NOT NULL,manifest_hash TEXT NOT NULL,summary BLOB NOT NULL,summary_hash TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('PREPARING','PUBLISHED')),item_count INTEGER NOT NULL CHECK(item_count>=0),members_hash TEXT);
CREATE INDEX IF NOT EXISTS app_runs_list ON app_runs(state,created_at DESC,run_id);
CREATE TABLE IF NOT EXISTS app_items(run_id TEXT NOT NULL REFERENCES app_runs(run_id),instrument_id TEXT NOT NULL,ordinal INTEGER NOT NULL CHECK(ordinal>=0),payload BLOB NOT NULL,raw_hash TEXT NOT NULL,raw_size INTEGER NOT NULL CHECK(raw_size>=0),PRIMARY KEY(run_id,instrument_id),UNIQUE(run_id,ordinal));
CREATE TABLE IF NOT EXISTS app_item_summaries(run_id TEXT NOT NULL,instrument_id TEXT NOT NULL,ordinal INTEGER NOT NULL,display_rank INTEGER NOT NULL,decision_status TEXT NOT NULL,payload BLOB NOT NULL,raw_hash TEXT NOT NULL,source_hash TEXT NOT NULL,projection_version TEXT NOT NULL,PRIMARY KEY(run_id,instrument_id),FOREIGN KEY(run_id,instrument_id) REFERENCES app_items(run_id,instrument_id));
CREATE INDEX IF NOT EXISTS app_summary_selection ON app_item_summaries(run_id,decision_status,display_rank,instrument_id);
CREATE TRIGGER IF NOT EXISTS app_summaries_no_update BEFORE UPDATE ON app_item_summaries BEGIN SELECT RAISE(ABORT,'immutable summary'); END;
CREATE TRIGGER IF NOT EXISTS app_summaries_no_delete BEFORE DELETE ON app_item_summaries BEGIN SELECT RAISE(ABORT,'immutable summary'); END;
CREATE TABLE IF NOT EXISTS app_jobs(job_id TEXT PRIMARY KEY,idempotency_key TEXT NOT NULL UNIQUE,request BLOB NOT NULL,request_hash TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED','INTERRUPTED')),revision INTEGER NOT NULL CHECK(revision>=0),progress INTEGER NOT NULL CHECK(progress>=0),run_id TEXT,parent_job_id TEXT REFERENCES app_jobs(job_id),error TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_job_events(sequence INTEGER PRIMARY KEY,job_id TEXT NOT NULL REFERENCES app_jobs(job_id),revision INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,event_at TEXT NOT NULL,UNIQUE(job_id,revision));
CREATE TABLE IF NOT EXISTS app_change_ledger(sequence INTEGER PRIMARY KEY,kind TEXT NOT NULL,entity_id TEXT NOT NULL,content_hash TEXT NOT NULL,event_at TEXT NOT NULL,UNIQUE(kind,entity_id,content_hash));
CREATE TABLE IF NOT EXISTS app_fact_cache(cache_key TEXT PRIMARY KEY,version TEXT NOT NULL,dependencies_hash TEXT NOT NULL,payload BLOB NOT NULL,payload_hash TEXT NOT NULL,size INTEGER NOT NULL CHECK(size>=0),last_access TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS app_cache_eviction ON app_fact_cache(last_access,cache_key);
CREATE TRIGGER IF NOT EXISTS app_inputs_no_update BEFORE UPDATE ON app_inputs BEGIN SELECT RAISE(ABORT,'immutable input'); END;
CREATE TRIGGER IF NOT EXISTS app_inputs_no_delete BEFORE DELETE ON app_inputs BEGIN SELECT RAISE(ABORT,'immutable input'); END;
CREATE TRIGGER IF NOT EXISTS app_items_no_update BEFORE UPDATE ON app_items BEGIN SELECT RAISE(ABORT,'immutable detail'); END;
CREATE TRIGGER IF NOT EXISTS app_items_no_delete BEFORE DELETE ON app_items BEGIN SELECT RAISE(ABORT,'immutable detail'); END;
CREATE TRIGGER IF NOT EXISTS app_runs_no_delete BEFORE DELETE ON app_runs BEGIN SELECT RAISE(ABORT,'immutable run'); END;
CREATE TRIGGER IF NOT EXISTS app_runs_published BEFORE UPDATE ON app_runs WHEN OLD.state='PUBLISHED' BEGIN SELECT RAISE(ABORT,'immutable published run'); END;
CREATE TRIGGER IF NOT EXISTS app_events_no_update BEFORE UPDATE ON app_job_events BEGIN SELECT RAISE(ABORT,'immutable event'); END;
CREATE TRIGGER IF NOT EXISTS app_events_no_delete BEFORE DELETE ON app_job_events BEGIN SELECT RAISE(ABORT,'immutable event'); END;
CREATE TRIGGER IF NOT EXISTS app_ledger_no_update BEFORE UPDATE ON app_change_ledger BEGIN SELECT RAISE(ABORT,'immutable ledger'); END;
CREATE TRIGGER IF NOT EXISTS app_ledger_no_delete BEFORE DELETE ON app_change_ledger BEGIN SELECT RAISE(ABORT,'immutable ledger'); END;
`

func InitApplicationSchema(ctx context.Context, db *sql.DB) error {
	var exists int
	if e := db.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='app_schema_migrations'").Scan(&exists); e != nil {
		return e
	}
	if exists != 0 {
		var hash string
		if e := db.QueryRowContext(ctx, "SELECT checksum FROM app_schema_migrations WHERE version=?", c.SchemaVersion).Scan(&hash); e != nil {
			return e
		}
		if hash != appHash([]byte(appDDL)) {
			return errors.New("APPLICATION_SCHEMA_CHECKSUM_MISMATCH")
		}
		return VerifyApplicationSchemaDB(ctx, db)
	}
	tx, e := db.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	if _, e = tx.ExecContext(ctx, appDDL); e != nil {
		return e
	}
	if _, e = tx.ExecContext(ctx, "INSERT INTO app_schema_migrations VALUES(?,?,?)", c.SchemaVersion, appHash([]byte(appDDL)), appNow()); e != nil {
		return e
	}
	if e = tx.Commit(); e != nil {
		return e
	}
	return VerifyApplicationSchemaDB(ctx, db)
}

func VerifyApplicationSchemaDB(ctx context.Context, db *sql.DB) error {
	expected, e := sql.Open("sqlite", ":memory:")
	if e != nil {
		return e
	}
	defer expected.Close()
	expected.SetMaxOpenConns(1)
	if _, e = expected.ExecContext(ctx, appDDL); e != nil {
		return e
	}
	rows, e := expected.QueryContext(ctx, "SELECT name,type,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")
	if e != nil {
		return e
	}
	defer rows.Close()
	for rows.Next() {
		var name, kind, want, actual string
		if e = rows.Scan(&name, &kind, &want); e != nil {
			return e
		}
		if e = db.QueryRowContext(ctx, "SELECT sql FROM sqlite_master WHERE name=? AND type=?", name, kind).Scan(&actual); e != nil {
			return fmt.Errorf("APPLICATION_SCHEMA_OBJECT_MISSING %s: %w", name, e)
		}
		if actual != want {
			return fmt.Errorf("APPLICATION_SCHEMA_OBJECT_CHANGED: %s", name)
		}
	}
	return rows.Err()
}

type AppRepository struct {
	store *Store
	db    *sql.DB
}

func NewAppRepository(store *Store) *AppRepository {
	return &AppRepository{store: store, db: store.DB()}
}

// NewReadOnlyAppRepository uses a caller-owned mode=ro connection for verification.
func NewReadOnlyAppRepository(db *sql.DB) *AppRepository { return &AppRepository{db: db} }
func (r *AppRepository) resourceCheck() error {
	if r.store == nil {
		return errors.New("READ_ONLY_REPOSITORY_WRITE_FORBIDDEN")
	}
	return CheckResources(filepath.Dir(r.store.Path))
}
func appHash(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func appNow() string          { return time.Now().UTC().Format(time.RFC3339Nano) }
func appPack(b []byte) ([]byte, error) {
	var out bytes.Buffer
	w := gzip.NewWriter(&out)
	if _, e := w.Write(b); e != nil {
		return nil, e
	}
	if e := w.Close(); e != nil {
		return nil, e
	}
	return out.Bytes(), nil
}
func appUnpack(b []byte, size int64) ([]byte, error) {
	if size < 0 || size > 128<<20 {
		return nil, errors.New("DETAIL_SIZE_LIMIT")
	}
	r, e := gzip.NewReader(bytes.NewReader(b))
	if e != nil {
		return nil, e
	}
	defer r.Close()
	raw, e := io.ReadAll(io.LimitReader(r, size+1))
	if e != nil {
		return nil, e
	}
	if int64(len(raw)) != size {
		return nil, errors.New("DETAIL_SIZE_MISMATCH")
	}
	return raw, nil
}
func appLedger(ctx context.Context, tx *sql.Tx, kind, id, hash string) error {
	_, e := tx.ExecContext(ctx, "INSERT INTO app_change_ledger(kind,entity_id,content_hash,event_at) VALUES(?,?,?,?) ON CONFLICT(kind,entity_id,content_hash) DO NOTHING", kind, id, hash, appNow())
	return e
}

func (r *AppRepository) Ready(ctx context.Context) (c.Readiness, error) {
	out := c.Readiness{DefaultStrategy: c.V2, Scheduler: "PAUSED"}
	if e := r.db.PingContext(ctx); e != nil {
		return out, e
	}
	out.Healthy = true
	if e := r.db.QueryRowContext(ctx, "SELECT version FROM app_schema").Scan(&out.Schema); e != nil {
		return out, e
	}
	if e := r.db.QueryRowContext(ctx, "SELECT count(*) FROM app_jobs WHERE state IN ('QUEUED','RUNNING')").Scan(&out.UnfinishedJobs); e != nil {
		return out, e
	}
	var status string
	if e := r.db.QueryRowContext(ctx, "SELECT status FROM migration_state WHERE id=1").Scan(&status); e != nil {
		if errors.Is(e, sql.ErrNoRows) {
			out.Migration = "UNPUBLISHED"
			out.Reason = "MIGRATION_PUBLICATION_REQUIRED"
			return out, nil
		}
		return out, e
	}
	out.Migration = status
	out.Ready = out.Schema == c.SchemaVersion && status == "PUBLISHED"
	if !out.Ready {
		out.Reason = "MIGRATION_PUBLICATION_REQUIRED"
	} else if _, e := batchCurrentIdentity(ctx, r.db); e != nil {
		out.Ready = false
		out.Reason = e.Error()
	}
	return out, nil
}
func (r *AppRepository) PutInput(ctx context.Context, in c.InputRecord) error {
	if in.ID == "" || in.RawHash != appHash(in.Raw) || !json.Valid(in.Raw) {
		return errors.New("INPUT_IDENTITY_INVALID")
	}
	tx, e := r.db.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	var existing string
	e = tx.QueryRowContext(ctx, "SELECT raw_hash FROM app_inputs WHERE input_id=?", in.ID).Scan(&existing)
	if e == nil {
		if existing != in.RawHash {
			return errors.New("IMMUTABLE_INPUT_CONFLICT")
		}
		if e = projectBatchInput(ctx, tx, in.ID, in.RawHash, in.Raw); e != nil {
			return e
		}
		return tx.Commit()
	}
	if !errors.Is(e, sql.ErrNoRows) {
		return e
	}
	_, e = tx.ExecContext(ctx, "INSERT INTO app_inputs VALUES(?,?,?,?,?,?,?)", in.ID, in.RawHash, in.Raw, in.Observation, in.Cutoff, in.Coverage, appNow())
	if e != nil {
		return e
	}
	if e = appLedger(ctx, tx, "INPUT", in.ID, in.RawHash); e != nil {
		return e
	}
	if e = projectBatchInput(ctx, tx, in.ID, in.RawHash, in.Raw); e != nil {
		return e
	}
	return tx.Commit()
}
func (r *AppRepository) InputExists(ctx context.Context, id string) error {
	var found string
	return r.db.QueryRowContext(ctx, "SELECT input_id FROM app_inputs WHERE input_id=?", id).Scan(&found)
}
func (r *AppRepository) Input(ctx context.Context, id string) (c.InputRecord, error) {
	started := time.Now()
	defer func() {
		ports.Observe(ports.BudgetFromContext(ctx), "input_sql_and_integrity", time.Since(started).Seconds())
	}()
	out := c.InputRecord{ID: id}
	e := r.db.QueryRowContext(ctx, "SELECT raw_hash,payload,observation,cutoff,coverage FROM app_inputs WHERE input_id=?", id).Scan(&out.RawHash, &out.Raw, &out.Observation, &out.Cutoff, &out.Coverage)
	if e != nil {
		return out, e
	}
	if appHash(out.Raw) != out.RawHash {
		return out, errors.New("INPUT_BYTES_CORRUPT")
	}
	return out, ctx.Err()
}

func (r *AppRepository) SaveRun(ctx context.Context, run c.Run) error {
	if e := r.resourceCheck(); e != nil {
		return e
	}
	b := ports.BudgetFromContext(ctx)
	started := time.Now()
	compression := 0.0
	defer func() {
		ports.Observe(b, "result_save_and_verify", time.Since(started).Seconds())
		ports.Observe(b, "result_compression", compression)
	}()
	if run.ID == "" || !json.Valid(run.Manifest) || !json.Valid(run.Summary) {
		return errors.New("INVALID_RESULT_ENVELOPE")
	}
	manifestHash, summaryHash := appHash(run.Manifest), appHash(run.Summary)
	seen := map[string]bool{}
	members := sha256.New()
	for i, item := range run.Items {
		if err := ctx.Err(); err != nil {
			return err
		}
		if seen[item.ID] || item.ID == "" || !json.Valid(item.Payload) {
			return errors.New("INVALID_DUPLICATE_RESULT_ITEM")
		}
		seen[item.ID] = true
		fmt.Fprintf(members, "%d\x00%s\x00%s\n", i, item.ID, appHash(item.Payload))
	}
	memberHash := hex.EncodeToString(members.Sum(nil))
	tx, e := r.db.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	if e = requirePublishedForRun(ctx, tx); e != nil {
		return e
	}
	var mh, sh, state, inputID, strategy, created, implementation, availability string
	var existingMembers sql.NullString
	var count int
	if e = ensureRunNotAbandoned(ctx, tx, run.ID); e != nil {
		return e
	}
	e = tx.QueryRowContext(ctx, "SELECT manifest_hash,summary_hash,state,input_id,strategy_id,created_at,implementation_status,candidate_availability,item_count,members_hash FROM app_runs WHERE run_id=?", run.ID).Scan(&mh, &sh, &state, &inputID, &strategy, &created, &implementation, &availability, &count, &existingMembers)
	if e == nil {
		if mh != manifestHash || sh != summaryHash || inputID != run.InputID || strategy != run.Strategy || created != run.CreatedAt || implementation != run.ImplementationStatus || availability != run.CandidateAvailability || count != len(run.Items) || (existingMembers.Valid && existingMembers.String != memberHash) {
			return errors.New("IMMUTABLE_RUN_CONFLICT")
		}
		if state == "PUBLISHED" {
			return r.verifyPublishedTx(ctx, tx, run.ID)
		}
	} else if errors.Is(e, sql.ErrNoRows) {
		_, e = tx.ExecContext(ctx, "INSERT INTO app_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)", run.ID, run.InputID, run.Strategy, run.CreatedAt, run.ImplementationStatus, run.CandidateAvailability, []byte(run.Manifest), manifestHash, []byte(run.Summary), summaryHash, "PREPARING", len(run.Items))
		if e != nil {
			return e
		}
	} else {
		return e
	}
	if e = tx.Commit(); e != nil {
		return e
	}
	for start := 0; start < len(run.Items); start += 50 {
		if e := r.resourceCheck(); e != nil {
			return e
		}
		stop := start + 50
		if stop > len(run.Items) {
			stop = len(run.Items)
		}
		tx, e = r.db.BeginTx(ctx, nil)
		if e != nil {
			return e
		}
		if e = ensureRunNotAbandoned(ctx, tx, run.ID); e != nil {
			tx.Rollback()
			return e
		}
		for i := start; i < stop; i++ {
			item := run.Items[i]
			h := appHash(item.Payload)
			compressionStart := time.Now()
			packed, err := appPack(item.Payload)
			compression += time.Since(compressionStart).Seconds()
			if b := ports.BudgetFromContext(ctx); b != nil {
				if er := b.Output(int64(len(packed))); er != nil {
					tx.Rollback()
					return er
				}
			}
			if err != nil {
				tx.Rollback()
				return err
			}
			var old string
			err = tx.QueryRowContext(ctx, "SELECT raw_hash FROM app_items WHERE run_id=? AND instrument_id=?", run.ID, item.ID).Scan(&old)
			if err == nil {
				if old != h {
					tx.Rollback()
					return errors.New("IMMUTABLE_DETAIL_CONFLICT")
				}
				continue
			}
			if !errors.Is(err, sql.ErrNoRows) {
				tx.Rollback()
				return err
			}
			if _, err = tx.ExecContext(ctx, "INSERT INTO app_items VALUES(?,?,?,?,?,?)", run.ID, item.ID, i, packed, h, len(item.Payload)); err != nil {
				tx.Rollback()
				return err
			}
			summary, status, rank, err := appItemSummary(item, i)
			if err != nil {
				tx.Rollback()
				return err
			}
			if _, err = tx.ExecContext(ctx, "INSERT INTO app_item_summaries VALUES(?,?,?,?,?,?,?,?,?)", run.ID, item.ID, i, rank, status, summary, appHash(summary), h, "GO_RESULT_SUMMARY_V1"); err != nil {
				tx.Rollback()
				return err
			}
		}
		if e = tx.Commit(); e != nil {
			return e
		}
	}
	tx, e = r.db.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	if e = requirePublishedForRun(ctx, tx); e != nil {
		return e
	}
	if e = ensureRunNotAbandoned(ctx, tx, run.ID); e != nil {
		return e
	}
	if _, e = tx.ExecContext(ctx, "UPDATE app_runs SET members_hash=? WHERE run_id=? AND state='PREPARING'", memberHash, run.ID); e != nil {
		return e
	}
	if e = r.verifyPublishedTx(ctx, tx, run.ID); e != nil {
		return e
	}
	if _, e = tx.ExecContext(ctx, "UPDATE app_runs SET state='PUBLISHED' WHERE run_id=? AND state='PREPARING'", run.ID); e != nil {
		return e
	}
	if run.JobID != "" {
		var state string
		var revision int
		if e = tx.QueryRowContext(ctx, "SELECT state,revision FROM app_jobs WHERE job_id=?", run.JobID).Scan(&state, &revision); e != nil {
			return e
		}
		if state != "RUNNING" {
			return errors.New("RESULT_JOB_NO_LONGER_RUNNING")
		}
		revision++
		at := appNow()
		if _, e = tx.ExecContext(ctx, "UPDATE app_jobs SET state='SUCCEEDED',revision=?,progress=?,run_id=?,updated_at=? WHERE job_id=?", revision, len(run.Items), run.ID, at, run.JobID); e != nil {
			return e
		}
		if _, e = tx.ExecContext(ctx, "INSERT INTO app_job_events(job_id,revision,state,reason,event_at) VALUES(?,?,'SUCCEEDED','RESULT_PUBLISHED',?)", run.JobID, revision, at); e != nil {
			return e
		}
		event, _ := json.Marshal(map[string]any{"job_id": run.JobID, "revision": revision, "state": "SUCCEEDED", "reason": "RESULT_PUBLISHED", "event_at": at})
		if e = appLedger(ctx, tx, "JOB_EVENT", fmt.Sprintf("%s:%d", run.JobID, revision), appHash(event)); e != nil {
			return e
		}
	}
	if e = appLedger(ctx, tx, "RUN", run.ID, memberHash); e != nil {
		return e
	}
	return tx.Commit()
}

func (r *AppRepository) verifyPublishedTx(ctx context.Context, tx *sql.Tx, id string) error {
	started := time.Now()
	defer func() {
		ports.Observe(ports.BudgetFromContext(ctx), "result_closure_verification", time.Since(started).Seconds())
	}()
	var manifest, summary []byte
	var mh, sh, expected string
	var count int
	if e := tx.QueryRowContext(ctx, "SELECT manifest,manifest_hash,summary,summary_hash,item_count,members_hash FROM app_runs WHERE run_id=?", id).Scan(&manifest, &mh, &summary, &sh, &count, &expected); e != nil {
		return e
	}
	if appHash(manifest) != mh || appHash(summary) != sh {
		return errors.New("RESULT_HEADER_CORRUPT")
	}
	rows, e := tx.QueryContext(ctx, "SELECT i.instrument_id,i.ordinal,i.payload,i.raw_hash,i.raw_size,s.ordinal,s.display_rank,s.decision_status,s.payload,s.raw_hash,s.source_hash,s.projection_version FROM app_items i LEFT JOIN app_item_summaries s ON s.run_id=i.run_id AND s.instrument_id=i.instrument_id WHERE i.run_id=? ORDER BY i.ordinal", id)
	if e != nil {
		return e
	}
	defer rows.Close()
	members := sha256.New()
	n := 0
	for rows.Next() {
		var iid, h string
		var ordinal int
		var size int64
		var packed []byte
		var so, srank sql.NullInt64
		var status, summaryHash, sourceHash, projectionVersion sql.NullString
		var summary []byte
		if e = rows.Scan(&iid, &ordinal, &packed, &h, &size, &so, &srank, &status, &summary, &summaryHash, &sourceHash, &projectionVersion); e != nil {
			return e
		}
		raw, err := appUnpack(packed, size)
		if err != nil {
			return err
		}
		if appHash(raw) != h || ordinal != n {
			return errors.New("RESULT_DETAIL_CORRUPT")
		}
		expectedSummary, expectedStatus, expectedRank, err := appItemSummary(c.Item{ID: iid, Payload: raw}, ordinal)
		if err != nil {
			return err
		}
		if !so.Valid || int(so.Int64) != ordinal || !srank.Valid || int(srank.Int64) != expectedRank || status.String != expectedStatus || projectionVersion.String != "GO_RESULT_SUMMARY_V1" || sourceHash.String != h || summaryHash.String != appHash(summary) || !bytes.Equal(summary, expectedSummary) {
			return errors.New("RESULT_SUMMARY_PROJECTION_CORRUPT")
		}
		fmt.Fprintf(members, "%d\x00%s\x00%s\n", n, iid, h)
		n++
		if e = ctx.Err(); e != nil {
			return e
		}
	}
	if e = rows.Err(); e != nil {
		return e
	}
	if n != count || hex.EncodeToString(members.Sum(nil)) != expected {
		return errors.New("RESULT_MEMBERS_CORRUPT")
	}
	return nil
}
func (r *AppRepository) VerifyRun(ctx context.Context, id string) error {
	tx, e := r.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if e != nil {
		return e
	}
	defer tx.Rollback()
	var state string
	if e = tx.QueryRowContext(ctx, "SELECT state FROM app_runs WHERE run_id=?", id).Scan(&state); e != nil {
		return e
	}
	if state != "PUBLISHED" {
		return errors.New("RUN_NOT_PUBLISHED")
	}
	if e = ensureRunNotAbandoned(ctx, tx, id); e != nil {
		return e
	}
	return r.verifyPublishedTx(ctx, tx, id)
}
func appScanRun(scan interface{ Scan(...any) error }) (c.Run, error) {
	var out c.Run
	var m, s []byte
	var mh, sh string
	e := scan.Scan(&out.ID, &out.InputID, &out.Strategy, &out.CreatedAt, &out.ImplementationStatus, &out.CandidateAvailability, &m, &mh, &s, &sh)
	if e != nil {
		return out, e
	}
	if appHash(m) != mh || appHash(s) != sh {
		return out, errors.New("RESULT_HEADER_CORRUPT")
	}
	out.Manifest = m
	out.Summary = s
	return out, nil
}

const appRunColumns = "run_id,input_id,strategy_id,created_at,implementation_status,candidate_availability,manifest,manifest_hash,summary,summary_hash"

func (r *AppRepository) Run(ctx context.Context, id string) (c.Run, error) {
	return appScanRun(r.db.QueryRowContext(ctx, "SELECT "+appRunColumns+" FROM app_runs WHERE run_id=? AND state='PUBLISHED'", id))
}
func (r *AppRepository) Runs(ctx context.Context, limit, offset int) ([]c.Run, error) {
	if limit < 1 || limit > 200 || offset < 0 {
		return nil, errors.New("INVALID_PAGE")
	}
	rows, e := r.db.QueryContext(ctx, "SELECT "+appRunColumns+" FROM app_runs WHERE state='PUBLISHED' ORDER BY created_at DESC,run_id LIMIT ? OFFSET ?", limit, offset)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []c.Run{}
	for rows.Next() {
		v, e := appScanRun(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, v)
	}
	return out, rows.Err()
}
func appScanItem(scan interface{ Scan(...any) error }) (c.Item, error) {
	var out c.Item
	var packed []byte
	var h string
	var n int64
	e := scan.Scan(&out.ID, &packed, &h, &n)
	if e != nil {
		return out, e
	}
	raw, e := appUnpack(packed, n)
	if e != nil {
		return out, e
	}
	if appHash(raw) != h {
		return out, errors.New("RESULT_DETAIL_CORRUPT")
	}
	out.Payload = raw
	return out, nil
}
func (r *AppRepository) Items(ctx context.Context, id string, limit, offset int) ([]c.Item, error) {
	if limit < 1 || limit > 200 || offset < 0 {
		return nil, errors.New("INVALID_PAGE")
	}
	rows, e := r.db.QueryContext(ctx, "SELECT i.instrument_id,i.payload,i.raw_hash,i.raw_size FROM app_items i JOIN app_runs r USING(run_id) WHERE i.run_id=? AND r.state='PUBLISHED' ORDER BY i.ordinal LIMIT ? OFFSET ?", id, limit, offset)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []c.Item{}
	for rows.Next() {
		v, e := appScanItem(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, v)
	}
	return out, rows.Err()
}
func (r *AppRepository) Item(ctx context.Context, id, iid string) (c.Item, error) {
	return appScanItem(r.db.QueryRowContext(ctx, "SELECT i.instrument_id,i.payload,i.raw_hash,i.raw_size FROM app_items i JOIN app_runs r USING(run_id) WHERE i.run_id=? AND i.instrument_id=? AND r.state='PUBLISHED'", id, iid))
}

const appJobColumns = "job_id,idempotency_key,request,state,revision,progress,coalesce(run_id,''),coalesce(parent_job_id,''),error,created_at,updated_at"

func appScanJob(scan interface{ Scan(...any) error }) (c.Job, error) {
	var j c.Job
	var raw []byte
	e := scan.Scan(&j.ID, &j.Key, &raw, &j.State, &j.Revision, &j.Progress, &j.RunID, &j.ParentID, &j.Error, &j.CreatedAt, &j.UpdatedAt)
	if e == nil {
		e = json.Unmarshal(raw, &j.Request)
	}
	return j, e
}
func (r *AppRepository) Job(ctx context.Context, id string) (c.Job, error) {
	return appScanJob(r.db.QueryRowContext(ctx, "SELECT "+appJobColumns+" FROM app_jobs WHERE job_id=?", id))
}
func (r *AppRepository) Jobs(ctx context.Context, state string) ([]c.Job, error) {
	query := "SELECT " + appJobColumns + " FROM app_jobs"
	args := []any{}
	if state != "" {
		query += " WHERE state=?"
		args = append(args, state)
	}
	query += " ORDER BY created_at,job_id"
	rows, e := r.db.QueryContext(ctx, query, args...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []c.Job{}
	for rows.Next() {
		j, e := appScanJob(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, j)
	}
	return out, rows.Err()
}
func (r *AppRepository) Submit(ctx context.Context, j c.Job) (c.Job, bool, error) {
	raw, e := json.Marshal(j.Request)
	if e != nil {
		return j, false, e
	}
	h := appHash(raw)
	tx, e := r.db.BeginTx(ctx, nil)
	if e != nil {
		return j, false, e
	}
	defer tx.Rollback()
	old, e := appScanJob(tx.QueryRowContext(ctx, "SELECT "+appJobColumns+" FROM app_jobs WHERE idempotency_key=?", j.Key))
	if e == nil {
		oldRaw, _ := json.Marshal(old.Request)
		if appHash(oldRaw) != h {
			return old, false, errors.New("IDEMPOTENCY_KEY_CONFLICT")
		}
		return old, false, nil
	}
	if !errors.Is(e, sql.ErrNoRows) {
		return j, false, e
	}
	var parent any
	if j.ParentID != "" {
		parent = j.ParentID
	}
	_, e = tx.ExecContext(ctx, "INSERT INTO app_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", j.ID, j.Key, raw, h, "QUEUED", 0, 0, nil, parent, "", j.CreatedAt, j.UpdatedAt)
	if e != nil {
		return j, false, e
	}
	if _, e = tx.ExecContext(ctx, "INSERT INTO app_job_events(job_id,revision,state,reason,event_at) VALUES(?,?,?,?,?)", j.ID, 0, "QUEUED", "EXPLICIT_SUBMISSION", j.CreatedAt); e != nil {
		return j, false, e
	}
	event, _ := json.Marshal(map[string]any{"job_id": j.ID, "revision": 0, "state": "QUEUED", "reason": "EXPLICIT_SUBMISSION", "event_at": j.CreatedAt})
	if e = appLedger(ctx, tx, "JOB_EVENT", fmt.Sprintf("%s:0", j.ID), appHash(event)); e != nil {
		return j, false, e
	}
	if e = appLedger(ctx, tx, "JOB", j.ID, h); e != nil {
		return j, false, e
	}
	e = tx.Commit()
	return j, true, e
}
func appTransitionAllowed(from, to string) bool {
	switch from {
	case "QUEUED":
		return to == "RUNNING" || to == "CANCELLED" || to == "INTERRUPTED"
	case "RUNNING":
		return to == "SUCCEEDED" || to == "FAILED" || to == "CANCELLED" || to == "INTERRUPTED"
	}
	return false
}
func (r *AppRepository) Transition(ctx context.Context, id, to, reason, run string, progress int) (c.Job, error) {
	tx, e := r.db.BeginTx(ctx, nil)
	if e != nil {
		return c.Job{}, e
	}
	defer tx.Rollback()
	j, e := appScanJob(tx.QueryRowContext(ctx, "SELECT "+appJobColumns+" FROM app_jobs WHERE job_id=?", id))
	if e != nil {
		return j, e
	}
	if !appTransitionAllowed(j.State, to) {
		return j, fmt.Errorf("INVALID_JOB_TRANSITION_%s_TO_%s", j.State, to)
	}
	if to == "SUCCEEDED" {
		var n int
		if e = tx.QueryRowContext(ctx, "SELECT count(*) FROM app_runs WHERE run_id=? AND state='PUBLISHED'", run).Scan(&n); e != nil {
			return j, e
		}
		if n != 1 {
			return j, errors.New("RESULT_NOT_PUBLISHED")
		}
	}
	j.State = to
	j.Revision++
	j.Progress = progress
	j.RunID = run
	j.UpdatedAt = appNow()
	if to == "FAILED" || to == "CANCELLED" || to == "INTERRUPTED" {
		j.Error = reason
	}
	_, e = tx.ExecContext(ctx, "UPDATE app_jobs SET state=?,revision=?,progress=?,run_id=?,error=?,updated_at=? WHERE job_id=?", j.State, j.Revision, j.Progress, j.RunID, j.Error, j.UpdatedAt, id)
	if e != nil {
		return j, e
	}
	if _, e = tx.ExecContext(ctx, "INSERT INTO app_job_events(job_id,revision,state,reason,event_at) VALUES(?,?,?,?,?)", id, j.Revision, to, reason, j.UpdatedAt); e != nil {
		return j, e
	}
	raw, _ := json.Marshal(map[string]any{"job_id": id, "revision": j.Revision, "state": to, "reason": reason, "event_at": j.UpdatedAt})
	if e = appLedger(ctx, tx, "JOB_EVENT", fmt.Sprintf("%s:%d", id, j.Revision), appHash(raw)); e != nil {
		return j, e
	}
	return j, tx.Commit()
}
func (r *AppRepository) Events(ctx context.Context, id string) ([]c.JobEvent, error) {
	rows, e := r.db.QueryContext(ctx, "SELECT sequence,job_id,state,reason,event_at FROM app_job_events WHERE job_id=? ORDER BY sequence", id)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []c.JobEvent{}
	for rows.Next() {
		var v c.JobEvent
		if e = rows.Scan(&v.Sequence, &v.JobID, &v.State, &v.Reason, &v.At); e != nil {
			return nil, e
		}
		out = append(out, v)
	}
	return out, rows.Err()
}
func (r *AppRepository) Legacy(ctx context.Context, ns, id string) (json.RawMessage, error) {
	raw, _, e := r.store.LegacyDocument(ctx, ns, id)
	return json.RawMessage(raw), e
}
