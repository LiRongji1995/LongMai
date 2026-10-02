package core

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	_ "modernc.org/sqlite"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

type Partition struct {
	Session string `json:"session"`
	Hash    string `json:"partition_hash"`
	Rows    int64  `json:"row_count"`
}
type HistoryManifest struct {
	Version    string      `json:"artifact_version"`
	Partitions []Partition `json:"partitions"`
	Receipts   []Row       `json:"receipts"`
}
type History struct {
	db       *sql.DB
	tx       *sql.Tx
	Budget   *Budget
	Manifest HistoryManifest
	Hash     string
}

func OpenHistory(path, ref, expected string, b *Budget) (h *History, err error) {
	if path == "" {
		return nil, errCode("EXPLICIT_READ_ONLY_HISTORY_DB_REQUIRED")
	}
	if _, err = os.Stat(path); err != nil {
		return nil, err
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	u := url.URL{Scheme: "file", Path: filepath.ToSlash(abs)}
	q := url.Values{"mode": {"ro"}}
	u.RawQuery = q.Encode()
	db, err := sql.Open("sqlite", u.String())
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	h = &History{db: db, Budget: b, Hash: expected}
	defer func() {
		if err != nil {
			h.Close()
		}
	}()
	h.tx, err = db.BeginTx(b.Context, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, err
	}
	var raw []byte
	var stored string
	err = h.tx.QueryRowContext(b.Context, "SELECT payload,content_hash FROM documents WHERE namespace=? AND document_id=?", "history_artifact_manifests", ref).Scan(&raw, &stored)
	if err != nil {
		return nil, err
	}
	if err = b.Read(int64(len(raw))); err != nil {
		return nil, err
	}
	canonical, err := Canonical(raw)
	if err != nil {
		return nil, err
	}
	actual := Hash(canonical)
	if actual != stored || actual != expected || ref != "history_"+actual {
		return nil, errCode("HISTORY_ARTIFACT_HASH_MISMATCH")
	}
	if err = json.Unmarshal(raw, &h.Manifest); err != nil {
		return nil, err
	}
	if h.Manifest.Version != "CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1" {
		return nil, errCode("HISTORY_ARTIFACT_VERSION_UNSUPPORTED")
	}
	seen := map[string]bool{}
	for _, p := range h.Manifest.Partitions {
		if seen[p.Session] {
			return nil, errCode("DUPLICATE_PARTITION_SESSION")
		}
		seen[p.Session] = true
	}
	return h, nil
}
func (h *History) Close() {
	if h == nil {
		return
	}
	if h.tx != nil {
		_ = h.tx.Rollback()
	}
	if h.db != nil {
		_ = h.db.Close()
	}
}
func (h *History) VerifyIndex() error {
	for _, p := range h.Manifest.Partitions {
		if e := h.Budget.Check(); e != nil {
			return e
		}
		rows, e := h.tx.QueryContext(h.Budget.Context, "SELECT m.row_hash,m.kind,m.instrument_id,m.session,r.row_hash FROM history_partition_members m LEFT JOIN history_evidence_rows r ON r.row_hash=m.row_hash WHERE m.partition_hash=? ORDER BY m.kind,m.instrument_id,m.row_hash", p.Hash)
		if e != nil {
			return e
		}
		hasher := sha256.New()
		n := int64(0)
		for rows.Next() {
			if e = h.Budget.IndexRow(); e != nil {
				rows.Close()
				return e
			}
			var hash, kind, iid, session string
			var stored sql.NullString
			if e = rows.Scan(&hash, &kind, &iid, &session, &stored); e != nil {
				rows.Close()
				return e
			}
			if !stored.Valid || stored.String != hash || session != p.Session {
				rows.Close()
				return errCode("CORRUPT_HISTORY_PARTITION_INDEX")
			}
			v, _ := json.Marshal([]string{hash, kind, iid, session})
			v, e = Canonical(v)
			if e != nil {
				rows.Close()
				return e
			}
			hasher.Write(v)
			hasher.Write([]byte{'\n'})
			n++
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return e
		}
		if hex.EncodeToString(hasher.Sum(nil)) != p.Hash || n != p.Rows {
			return errCode("CORRUPT_HISTORY_PARTITION")
		}
	}
	return nil
}
func (h *History) Bars(iid, start, end string, consume func(Row) error) error {
	parts := []string{}
	for _, p := range h.Manifest.Partitions {
		if p.Session >= start && p.Session <= end {
			parts = append(parts, p.Hash)
		}
	}
	for offset := 0; offset < len(parts); offset += 300 {
		stop := offset + 300
		if stop > len(parts) {
			stop = len(parts)
		}
		args := []any{}
		marks := []string{}
		for _, p := range parts[offset:stop] {
			args = append(args, p)
			marks = append(marks, "?")
		}
		args = append(args, iid, start, end)
		query := "SELECT r.row_hash,r.payload,m.kind,m.instrument_id,m.session FROM history_partition_members m LEFT JOIN history_evidence_rows r ON r.row_hash=m.row_hash WHERE m.partition_hash IN (" + strings.Join(marks, ",") + ") AND m.kind='bar' AND m.instrument_id=? AND m.session>=? AND m.session<=? ORDER BY m.session,r.row_hash"
		rows, e := h.tx.QueryContext(h.Budget.Context, query, args...)
		if e != nil {
			return e
		}
		for rows.Next() {
			if e = h.Budget.Check(); e != nil {
				rows.Close()
				return e
			}
			var hash, kind, mi, ms string
			var raw []byte
			if e = rows.Scan(&hash, &raw, &kind, &mi, &ms); e != nil {
				rows.Close()
				return e
			}
			if e = h.Budget.Read(int64(len(raw))); e != nil {
				rows.Close()
				return e
			}
			if Hash(raw) != hash {
				rows.Close()
				return errCode("CORRUPT_HISTORY_ROW")
			}
			var r Row
			if e = json.Unmarshal(raw, &r); e != nil {
				rows.Close()
				return e
			}
			if kind != "bar" || mi != r.Text("instrument_id") || ms != r.Text("session") {
				rows.Close()
				return errCode("CORRUPT_HISTORY_PARTITION_INDEX")
			}
			if e = consume(r); e != nil {
				rows.Close()
				return e
			}
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return fmt.Errorf("history rows: %w", e)
		}
	}
	return nil
}
