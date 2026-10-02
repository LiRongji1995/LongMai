package sqlitev2

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlmetrics"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"strings"
	"time"
)

type History struct {
	store    *Store
	manifest c.HistoryManifest
	hash     string
	b        ports.ResearchBudget
}

func (s *Store) History(ctx context.Context, ref, expected string, b ports.ResearchBudget) (*History, error) {
	if b == nil {
		return nil, errors.New("HISTORY_BUDGET_REQUIRED")
	}
	if e := b.Check(); e != nil {
		return nil, e
	}
	if e := s.Ready(ctx); e != nil {
		return nil, e
	}
	var rawSize int64
	if e := s.db.QueryRowContext(ctx, `SELECT e.raw_size FROM legacy_records l JOIN evidence_objects e ON e.id=l.evidence_id WHERE l.namespace='history_artifact_manifests' AND l.external_id=?`, ref).Scan(&rawSize); e != nil {
		return nil, e
	}
	if rawSize < 0 || rawSize > 128<<20 {
		return nil, errors.New("EVIDENCE_SIZE_LIMIT")
	}
	if e := b.Read(rawSize); e != nil {
		return nil, e
	}
	raw, hash, e := s.LegacyDocument(ctx, "history_artifact_manifests", ref)
	if e != nil {
		return nil, e
	}
	if hash != expected || ref != "history_"+hash {
		return nil, errors.New("HISTORY_IDENTITY_MISMATCH")
	}
	canonical, e := c.CanonicalContext(ctx, raw)
	if e != nil {
		return nil, e
	}
	if c.Hash(canonical) != hash {
		return nil, errors.New("HISTORY_CANONICAL_HASH_MISMATCH")
	}
	var m c.HistoryManifest
	if e = json.Unmarshal(raw, &m); e != nil {
		return nil, e
	}
	if m.Version != "CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1" {
		return nil, errors.New("HISTORY_VERSION_UNSUPPORTED")
	}
	var envelope map[string]json.RawMessage
	if e = json.Unmarshal(raw, &envelope); e != nil {
		return nil, e
	}
	for _, field := range []string{"partitions", "receipts"} {
		v := bytes.TrimSpace(envelope[field])
		if len(v) == 0 || v[0] != '[' {
			return nil, fmt.Errorf("INVALID_MANIFEST_%s", field)
		}
	}
	var descriptors []map[string]json.RawMessage
	if e = json.Unmarshal(envelope["partitions"], &descriptors); e != nil {
		return nil, e
	}
	seen := map[string]bool{}
	for i, p := range m.Partitions {
		for _, field := range []string{"session", "partition_hash", "row_count"} {
			v := descriptors[i][field]
			if len(v) == 0 || string(v) == "null" {
				return nil, errors.New("INCOMPLETE_PARTITION_DESCRIPTOR")
			}
		}
		if _, e = time.Parse("2006-01-02", p.Session); e != nil {
			return nil, errors.New("INVALID_PARTITION_SESSION")
		}
		if !ev.ValidHash(p.Hash) || p.Rows < 0 {
			return nil, errors.New("INVALID_PARTITION_DESCRIPTOR")
		}
		if seen[p.Session] {
			return nil, errors.New("DUPLICATE_PARTITION_SESSION")
		}
		seen[p.Session] = true
	}
	for i, r := range m.Receipts {
		m.Receipts[i], e = ev.ValidateEvidenceRow(ctx, "receipt", r)
		if e != nil {
			return nil, e
		}
	}
	return &History{s, m, hash, b}, nil
}
func (h *History) Close()            {}
func (h *History) Identity() string  { return h.hash }
func (h *History) Receipts() []c.Row { return h.manifest.Receipts }
func (h *History) VerifyIndex() error {
	var sqlTime, hashTime time.Duration
	defer func() {
		ports.Observe(h.b, "history_index_sql", sqlTime.Seconds())
		ports.Observe(h.b, "history_index_hash", hashTime.Seconds())
	}()
	if e := h.b.Check(); e != nil {
		return e
	}
	for _, p := range h.manifest.Partitions {
		n, hash, e := h.store.verifyPartition(h.b.Ctx(), p.Hash, p.Session, h.b, &sqlTime, &hashTime)
		if e != nil {
			return e
		}
		if n != p.Rows || hash != p.Hash {
			return errors.New("CORRUPT_HISTORY_PARTITION")
		}
	}
	return nil
}
func (s *Store) verifyPartition(ctx context.Context, hash, session string, b ports.ResearchBudget, times ...*time.Duration) (int64, string, error) {
	var localSQL, localHash time.Duration
	sqlTime, hashTime := &localSQL, &localHash
	if len(times) == 2 {
		sqlTime, hashTime = times[0], times[1]
	}
	rows, e := sqlmetrics.Query(ctx, s.db, sqlTime, `SELECT h.row_hash,h.kind,h.instrument_id,h.session FROM partitions p JOIN partition_members m ON m.partition_id=p.id LEFT JOIN history_rows h ON h.id=m.row_id WHERE p.partition_hash=? ORDER BY h.kind,h.instrument_id,h.row_hash`, hash)
	if e != nil {
		return 0, "", e
	}
	defer rows.Close()
	out := sha256.New()
	var n int64
	for rows.Next() {
		if b != nil {
			if e = b.IndexRow(); e != nil {
				return n, "", e
			}
		}
		var rh, kind, iid, sess string
		if e = rows.Scan(&rh, &kind, &iid, &sess); e != nil {
			return n, "", e
		}
		if session != "" && sess != session {
			return n, "", errors.New("PARTITION_SESSION_MISMATCH")
		}
		hashStart := time.Now()
		v, _ := json.Marshal([]string{rh, kind, iid, sess})
		out.Write(v)
		out.Write([]byte{'\n'})
		*hashTime += time.Since(hashStart)
		n++
	}
	return n, hex.EncodeToString(out.Sum(nil)), rows.Err()
}
func (h *History) Bars(iid, start, end string, consume func(c.Row) error) error {
	return h.BarsWithIdentity(iid, start, end, func(r c.Row, _ c.EvidenceIdentity) error { return consume(r) })
}
func (h *History) BarsWithIdentity(iid, start, end string, consume func(c.Row, c.EvidenceIdentity) error) error {
	var sqlTime, rawTime, decodeTime, projectionTime, consumeTime time.Duration
	defer func() {
		for label, d := range map[string]time.Duration{"history_window_sql": sqlTime, "history_window_raw_verify": rawTime, "history_window_decode": decodeTime, "history_window_projection_verify": projectionTime, "history_window_consume": consumeTime} {
			ports.Observe(h.b, label, d.Seconds())
		}
	}()
	if e := h.b.Check(); e != nil {
		return e
	}
	var args []any
	var marks []string
	for _, p := range h.manifest.Partitions {
		if p.Session >= start && p.Session <= end {
			args = append(args, p.Hash)
			marks = append(marks, "?")
		}
	}
	if len(args) == 0 {
		return nil
	}
	args = append(args, iid, start, end)
	q := `SELECT h.row_hash,h.raw_size,h.codec,h.payload,h.kind,h.instrument_id,h.session,h.projection_hash,h.projection_version,` + prefixColumns("h.") + ` FROM history_rows h JOIN partition_members m ON m.row_id=h.id JOIN partitions p ON p.id=m.partition_id WHERE p.partition_hash IN (` + strings.Join(marks, ",") + `) AND h.kind='bar' AND h.instrument_id=? AND h.session>=? AND h.session<=? ORDER BY h.session,h.row_hash`
	rows, e := sqlmetrics.Query(h.b.Ctx(), h.store.db, &sqlTime, q, args...)
	if e != nil {
		return e
	}
	defer rows.Close()
	for rows.Next() {
		if e = h.b.Check(); e != nil {
			return e
		}
		var hash, codec, kind, inst, sess, ph, pv string
		var n int64
		var data []byte
		p := make([]any, len(projectionColumns))
		ptr := []any{&hash, &n, &codec, &data, &kind, &inst, &sess, &ph, &pv}
		for i := range p {
			ptr = append(ptr, &p[i])
		}
		if e = rows.Scan(ptr...); e != nil {
			return e
		}
		if pv != ProjectionVersion {
			return errors.New("PROJECTION_VERSION_MISMATCH")
		}
		if n < 0 || n > 128<<20 {
			return errors.New("EVIDENCE_SIZE_LIMIT")
		}
		if e = h.b.Read(n); e != nil {
			return e
		}
		rawStart := time.Now()
		raw, e := decodeContext(h.b.Ctx(), codec, data, n, hash)
		rawTime += time.Since(rawStart)
		if e != nil {
			return e
		}
		projectionStart := time.Now()
		o, e := object(raw)
		if e != nil {
			return e
		}
		want, wh := projection(o, kind, inst, sess)
		projectionTime += time.Since(projectionStart)
		if wh != ph || !equalProjection(p, want) {
			return errors.New("TYPED_PROJECTION_TAMPERED")
		}
		decodeStart := time.Now()
		r, e := ev.DecodeEvidenceRow(h.b.Ctx(), kind, raw)
		decodeTime += time.Since(decodeStart)
		if e != nil {
			return e
		}
		if kind != "bar" || inst != r.Text("instrument_id") || sess != r.Text("session") {
			return errors.New("CORRUPT_HISTORY_PARTITION_INDEX")
		}
		consumeStart := time.Now()
		e = consume(r, c.EvidenceIdentity{Origin: "HISTORY_ROW", ParentHash: h.hash, RawSHA256: hash, Locator: hash, RawBytes: int64(len(raw))})
		consumeTime += time.Since(consumeStart)
		if e != nil {
			return e
		}
	}
	return rows.Err()
}
func prefixColumns(prefix string) string {
	a := make([]string, len(projectionColumns))
	for i, k := range projectionColumns {
		a[i] = prefix + k
	}
	return strings.Join(a, ",")
}
func equalProjection(a, b []any) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if fmt.Sprint(a[i]) != fmt.Sprint(b[i]) || (a[i] == nil) != (b[i] == nil) {
			return false
		}
	}
	return true
}
