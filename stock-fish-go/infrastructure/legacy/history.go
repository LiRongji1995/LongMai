package legacy

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlmetrics"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	_ "modernc.org/sqlite"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type History struct {
	db       *sql.DB
	tx       *sql.Tx
	Budget   Budget
	Manifest HistoryManifest
	Hash     string
}

func OpenHistory(path, ref, expected string, b Budget) (h *History, err error) {
	if b == nil {
		return nil, errCode("HISTORY_BUDGET_REQUIRED")
	}
	if err = b.Check(); err != nil {
		return nil, err
	}
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
	// A named return `return nil, err` overwrites h before deferred cleanup.
	// Retain the resource owner independently of the returned value.
	owner := h
	defer func() {
		if err != nil {
			owner.Close()
		}
	}()
	h.tx, err = db.BeginTx(b.Ctx(), &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, err
	}
	var raw []byte
	var stored string
	err = h.tx.QueryRowContext(b.Ctx(), "SELECT payload,content_hash FROM documents WHERE namespace=? AND document_id=?", "history_artifact_manifests", ref).Scan(&raw, &stored)
	if err != nil {
		return nil, err
	}
	if err = b.Read(int64(len(raw))); err != nil {
		return nil, err
	}
	// The SQLite content_hash authenticates the actual stored bytes. Do this
	// before JSON decoding: whitespace changes must not pass canonical equality.
	if Hash(raw) != stored {
		return nil, errCode("HISTORY_ARTIFACT_RAW_HASH_MISMATCH")
	}
	canonical, err := CanonicalContext(b.Ctx(), raw)
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
	var envelope Row
	if err = json.Unmarshal(raw, &envelope); err != nil {
		return nil, err
	}
	for _, field := range []string{"partitions", "receipts"} {
		value, ok := envelope[field]
		if !ok || len(value) == 0 || value[0] != '[' {
			return nil, errCode("INVALID_HISTORY_MANIFEST_" + strings.ToUpper(field))
		}
	}
	var descriptors []Row
	if err = json.Unmarshal(envelope["partitions"], &descriptors); err != nil {
		return nil, err
	}
	for _, descriptor := range descriptors {
		for _, field := range []string{"session", "partition_hash", "row_count"} {
			if value, ok := descriptor[field]; !ok || isNull(value) {
				return nil, errCode("INCOMPLETE_PARTITION_DESCRIPTOR")
			}
		}
	}
	if h.Manifest.Version != "CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1" {
		return nil, errCode("HISTORY_ARTIFACT_VERSION_UNSUPPORTED")
	}
	for i, receipt := range h.Manifest.Receipts {
		projected, e := ValidateEvidenceRow(b.Ctx(), "receipt", receipt)
		if e != nil {
			return nil, e
		}
		h.Manifest.Receipts[i] = projected
	}
	seen := map[string]bool{}
	for _, p := range h.Manifest.Partitions {
		if _, e := time.Parse("2006-01-02", p.Session); e != nil {
			return nil, errCode("INVALID_PARTITION_SESSION")
		}
		if !validHash(p.Hash) || p.Rows < 0 {
			return nil, errCode("INVALID_PARTITION_DESCRIPTOR")
		}
		if seen[p.Session] {
			return nil, errCode("DUPLICATE_PARTITION_SESSION")
		}
		seen[p.Session] = true
	}
	return h, nil
}
func (h *History) Receipts() []Row  { return h.Manifest.Receipts }
func (h *History) Identity() string { return h.Hash }
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
	var sqlTime, hashTime time.Duration
	defer func() {
		ports.Observe(h.Budget, "history_index_sql", sqlTime.Seconds())
		ports.Observe(h.Budget, "history_index_hash", hashTime.Seconds())
	}()
	if e := h.Budget.Check(); e != nil {
		return e
	}
	for _, p := range h.Manifest.Partitions {
		if e := h.Budget.Check(); e != nil {
			return e
		}
		rows, e := sqlmetrics.Query(h.Budget.Ctx(), h.tx, &sqlTime, "SELECT m.row_hash,m.kind,m.instrument_id,m.session,r.row_hash,r.kind,r.instrument_id,r.session FROM history_partition_members m LEFT JOIN history_evidence_rows r ON r.row_hash=m.row_hash WHERE m.partition_hash=? ORDER BY m.kind,m.instrument_id,m.row_hash", p.Hash)
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
			var storedKind, storedID, storedSession sql.NullString
			if e = rows.Scan(&hash, &kind, &iid, &session, &stored, &storedKind, &storedID, &storedSession); e != nil {
				rows.Close()
				return e
			}
			if !stored.Valid || stored.String != hash || !validHash(hash) || !contains([]string{"bar", "security"}, kind) || iid == "" || session != p.Session || !storedKind.Valid || storedKind.String != kind || !storedID.Valid || storedID.String != iid || !storedSession.Valid || storedSession.String != session {
				rows.Close()
				return errCode("CORRUPT_HISTORY_PARTITION_INDEX")
			}
			hashStart := time.Now()
			v, _ := json.Marshal([]string{hash, kind, iid, session})
			v, e = Canonical(v)
			if e != nil {
				rows.Close()
				return e
			}
			hasher.Write(v)
			hasher.Write([]byte{'\n'})
			hashTime += time.Since(hashStart)
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
	return h.BarsWithIdentity(iid, start, end, func(r Row, _ c.EvidenceIdentity) error { return consume(r) })
}
func (h *History) BarsWithIdentity(iid, start, end string, consume func(Row, c.EvidenceIdentity) error) error {
	var sqlTime, rawTime, decodeTime, consumeTime time.Duration
	defer func() {
		for label, d := range map[string]time.Duration{"history_window_sql": sqlTime, "history_window_raw_verify": rawTime, "history_window_decode": decodeTime, "history_window_projection_verify": 0, "history_window_consume": consumeTime} {
			ports.Observe(h.Budget, label, d.Seconds())
		}
	}()
	if e := h.Budget.Check(); e != nil {
		return e
	}
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
		rows, e := sqlmetrics.Query(h.Budget.Ctx(), h.tx, &sqlTime, query, args...)
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
			rawStart := time.Now()
			actualHash := Hash(raw)
			rawTime += time.Since(rawStart)
			if actualHash != hash {
				rows.Close()
				return errCode("CORRUPT_HISTORY_ROW")
			}
			decodeStart := time.Now()
			r, e := DecodeEvidenceRow(h.Budget.Ctx(), "bar", raw)
			decodeTime += time.Since(decodeStart)
			if e != nil {
				rows.Close()
				return e
			}
			if kind != "bar" || mi != r.Text("instrument_id") || ms != r.Text("session") {
				rows.Close()
				return errCode("CORRUPT_HISTORY_PARTITION_INDEX")
			}
			consumeStart := time.Now()
			e = consume(r, c.EvidenceIdentity{Origin: "HISTORY_ROW", ParentHash: h.Hash, RawSHA256: hash, Locator: hash, RawBytes: int64(len(raw))})
			consumeTime += time.Since(consumeStart)
			if e != nil {
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
