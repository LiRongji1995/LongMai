// Package sqlitev2 owns the versioned authority store. Raw evidence is immutable;
// exact decimal projections and legacy identities are independently verifiable.
package sqlitev2

import (
	"bytes"
	"compress/flate"
	"context"
	"crypto/sha256"
	"database/sql"
	_ "embed"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	_ "modernc.org/sqlite"
	"net/url"
	"os"
	"path/filepath"
	"sync"
	"time"
)

const SchemaVersion = 2
const ProjectionVersion = "SQLITE_V2_EXACT_PROJECTION_1"

//go:embed schema.sql
var schema string

type Store struct {
	db   *sql.DB
	Path string
}

func (s *Store) DB() *sql.DB  { return s.db }
func (s *Store) Close() error { return s.db.Close() }
func digest(v []byte) string  { h := sha256.Sum256(v); return hex.EncodeToString(h[:]) }
func URI(path string, readonly bool) string {
	a, _ := filepath.Abs(path)
	u := url.URL{Scheme: "file", Path: filepath.ToSlash(a)}
	q := url.Values{}
	if readonly {
		q.Set("mode", "ro")
	} else {
		q.Set("mode", "rwc")
	}
	q.Add("_pragma", "foreign_keys(1)")
	q.Add("_pragma", "busy_timeout(5000)")
	q.Add("_pragma", "synchronous(FULL)")
	if readonly {
		q.Add("_pragma", "cache_size(-32768)")
	} else {
		q.Add("_pragma", "cache_size(-131072)")
	}
	u.RawQuery = q.Encode()
	return u.String()
}
func Open(ctx context.Context, path string, create bool) (*Store, error) {
	if !create {
		if _, e := os.Stat(path); e != nil {
			return nil, e
		}
	} else {
		if e := os.MkdirAll(filepath.Dir(path), 0700); e != nil {
			return nil, e
		}
	}
	if create {
		if _, e := os.Stat(path); errors.Is(e, os.ErrNotExist) {
			f, e := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_RDWR, 0600)
			if e != nil {
				return nil, e
			}
			if e = f.Close(); e != nil {
				return nil, e
			}
		}
	}
	db, e := sql.Open("sqlite", URI(path, false))
	if e != nil {
		return nil, e
	}
	db.SetMaxOpenConns(1)
	fail := func(e error) (*Store, error) { db.Close(); return nil, e }
	if e = db.PingContext(ctx); e != nil {
		return fail(e)
	}
	var v int
	if e = db.QueryRowContext(ctx, "PRAGMA user_version").Scan(&v); e != nil {
		return fail(e)
	}
	if v != SchemaVersion {
		if !create || v != 0 {
			return fail(fmt.Errorf("SCHEMA_INCOMPATIBLE: %d", v))
		}
		var n int
		if e = db.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE type='table'").Scan(&n); e != nil {
			return fail(e)
		}
		if n != 0 {
			return fail(errors.New("REFUSE_INITIALIZE_NONEMPTY_DATABASE"))
		}
		tx, e := db.BeginTx(ctx, nil)
		if e != nil {
			return fail(e)
		}
		if _, e = tx.ExecContext(ctx, schema); e != nil {
			tx.Rollback()
			return fail(e)
		}
		for _, t := range []string{"evidence_objects", "legacy_records", "history_rows", "partitions", "partition_members", "snapshots", "snapshot_members", "snapshot_partitions", "source_receipts", "legacy_result_headers", "legacy_result_items", "archived_table_rows", "historical_jobs"} {
			for _, op := range []string{"UPDATE", "DELETE"} {
				_, e = tx.ExecContext(ctx, fmt.Sprintf("CREATE TRIGGER %s_no_%s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT,'immutable evidence'); END", t, op, op, t))
				if e != nil {
					tx.Rollback()
					return fail(e)
				}
			}
		}
		_, e = tx.ExecContext(ctx, "INSERT INTO schema_versions VALUES(?,?,?)", SchemaVersion, digest([]byte(schema)), time.Now().UTC().Format(time.RFC3339Nano))
		if e != nil {
			tx.Rollback()
			return fail(e)
		}
		if e = tx.Commit(); e != nil {
			return fail(e)
		}
	}
	var hash string
	if e = db.QueryRowContext(ctx, "SELECT checksum FROM schema_versions WHERE version=?", SchemaVersion).Scan(&hash); e != nil || hash != digest([]byte(schema)) {
		return fail(fmt.Errorf("SCHEMA_CHECKSUM_MISMATCH: %v", e))
	}
	if _, e = db.ExecContext(ctx, "PRAGMA journal_mode=WAL"); e != nil {
		return fail(e)
	}
	if e = InitApplicationSchema(ctx, db); e != nil {
		return fail(e)
	}
	store := &Store{db: db, Path: path}
	if e = store.InitBatchProjectionSchema(ctx); e != nil {
		return fail(e)
	}
	return store, nil
}

var flatePool = sync.Pool{New: func() any { w, _ := flate.NewWriter(io.Discard, flate.BestSpeed); return w }}

func encode(raw []byte) (string, []byte) {
	if len(raw) < 128 {
		return "raw", raw
	}
	var b bytes.Buffer
	w := flatePool.Get().(*flate.Writer)
	w.Reset(&b)
	w.Write(raw)
	w.Close()
	flatePool.Put(w)
	if b.Len() >= len(raw) {
		return "raw", raw
	}
	return "deflate", b.Bytes()
}
func decode(codec string, payload []byte, n int64, hash string) ([]byte, error) {
	return decodeContext(context.Background(), codec, payload, n, hash)
}

type contextReader struct {
	ctx context.Context
	r   io.Reader
}

func (r contextReader) Read(p []byte) (int, error) {
	if e := r.ctx.Err(); e != nil {
		return 0, e
	}
	return r.r.Read(p)
}
func decodeContext(ctx context.Context, codec string, payload []byte, n int64, hash string) ([]byte, error) {
	if e := ctx.Err(); e != nil {
		return nil, e
	}
	if n < 0 || n > 128<<20 {
		return nil, errors.New("EVIDENCE_SIZE_LIMIT")
	}

	var raw []byte
	switch codec {
	case "raw":
		raw = payload
	case "deflate":
		r := flate.NewReader(bytes.NewReader(payload))
		v, e := io.ReadAll(io.LimitReader(contextReader{ctx, r}, n+1))
		r.Close()
		if e != nil {
			return nil, e
		}
		raw = v
	default:
		return nil, errors.New("UNKNOWN_EVIDENCE_CODEC")
	}
	if int64(len(raw)) != n || digest(raw) != hash {
		return nil, errors.New("RAW_EVIDENCE_HASH_MISMATCH")
	}
	return raw, nil
}
func evidence(ctx context.Context, tx *sql.Tx, raw []byte) (int64, error) {
	hash := digest(raw)
	var id int64
	var n int64
	var codec string
	var stored []byte
	e := tx.QueryRowContext(ctx, "SELECT id,raw_size,codec,payload FROM evidence_objects WHERE raw_sha256=?", hash).Scan(&id, &n, &codec, &stored)
	if e == nil {
		got, e := decodeContext(ctx, codec, stored, n, hash)
		if e != nil || !bytes.Equal(got, raw) {
			return 0, errors.New("EVIDENCE_CONTENT_CONFLICT")
		}
		return id, nil
	}
	if !errors.Is(e, sql.ErrNoRows) {
		return 0, e
	}
	codec, stored = encode(raw)
	r, e := tx.ExecContext(ctx, "INSERT INTO evidence_objects(raw_sha256,raw_size,codec,payload) VALUES(?,?,?,?)", hash, len(raw), codec, stored)
	if e != nil {
		return 0, e
	}
	return r.LastInsertId()
}
func (s *Store) LegacyDocument(ctx context.Context, ns, id string) ([]byte, string, error) {
	if e := s.Ready(ctx); e != nil {
		return nil, "", e
	}
	return s.legacyDocument(ctx, ns, id)
}
func (s *Store) legacyDocument(ctx context.Context, ns, id string) ([]byte, string, error) {
	var hash, codec, rawHash string
	var n int64
	var data []byte
	e := s.db.QueryRowContext(ctx, "SELECT l.source_hash,e.codec,e.raw_size,e.payload,e.raw_sha256 FROM legacy_records l JOIN evidence_objects e ON e.id=l.evidence_id WHERE l.namespace=? AND l.external_id=?", ns, id).Scan(&hash, &codec, &n, &data, &rawHash)
	if e != nil {
		return nil, "", e
	}
	raw, e := decodeContext(ctx, codec, data, n, rawHash)
	if e != nil {
		return nil, "", e
	}
	if hash != digest(raw) {
		return nil, "", errors.New("LEGACY_HASH_MISMATCH")
	}
	return raw, hash, nil
}
func (s *Store) Ready(ctx context.Context) error {
	var status string
	e := s.db.QueryRowContext(ctx, "SELECT status FROM migration_state WHERE id=1").Scan(&status)
	if e != nil {
		return e
	}
	if status != "PUBLISHED" {
		return errors.New("MIGRATION_NOT_PUBLISHED")
	}
	return s.BatchProjectionsReady(ctx)
}
