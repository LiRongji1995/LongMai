package sqlitev2

import (
	"bytes"
	"compress/zlib"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
)

type LegacyResultHeader struct {
	ID          string `json:"run_id"`
	Namespace   string `json:"namespace"`
	Strategy    string `json:"strategy_id"`
	InputID     string `json:"input_id"`
	Observation string `json:"observation_date"`
	Cutoff      string `json:"cutoff"`
}

func (s *Store) LegacyResults(ctx context.Context, namespace string, limit int) ([]LegacyResultHeader, error) {
	if e := s.Ready(ctx); e != nil {
		return nil, e
	}
	if limit <= 0 || limit > 1000 {
		return nil, errors.New("INVALID_PAGE_LIMIT")
	}
	rows, e := s.db.QueryContext(ctx, "SELECT legacy_id,namespace,coalesce(strategy_id,''),coalesce(input_id,''),coalesce(observation_date,''),coalesce(cutoff,'') FROM legacy_result_headers WHERE namespace=? ORDER BY observation_date DESC,legacy_id LIMIT ?", namespace, limit)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []LegacyResultHeader{}
	for rows.Next() {
		var h LegacyResultHeader
		if e = rows.Scan(&h.ID, &h.Namespace, &h.Strategy, &h.InputID, &h.Observation, &h.Cutoff); e != nil {
			return nil, e
		}
		out = append(out, h)
	}
	return out, rows.Err()
}
func (s *Store) LegacyDetail(ctx context.Context, run, iid, strategy string) ([]byte, error) {
	if e := s.Ready(ctx); e != nil {
		return nil, e
	}
	var id string
	e := s.db.QueryRowContext(ctx, "SELECT detail_id FROM legacy_result_items WHERE run_id=? AND instrument_id=? AND strategy_id=?", run, iid, strategy).Scan(&id)
	if e != nil {
		return nil, e
	}
	raw, _, e := s.LegacyDocument(ctx, "strategy_experiment_item_details", id)
	if e != nil {
		return nil, e
	}
	o, e := object(raw)
	if e != nil {
		return nil, e
	}
	if txt(o, "encoding") != "zlib+base64+canonical-json" {
		return nil, errors.New("LEGACY_DETAIL_ENCODING_UNSUPPORTED")
	}
	var n int64
	if e = json.Unmarshal(o["raw_bytes"], &n); e != nil {
		return nil, e
	}
	if n < 0 || n > 128<<20 {
		return nil, errors.New("LEGACY_DETAIL_SIZE_INVALID")
	}
	blob, e := base64.StdEncoding.DecodeString(txt(o, "payload_b64"))
	if e != nil {
		return nil, e
	}
	z, e := zlib.NewReader(bytes.NewReader(blob))
	if e != nil {
		return nil, e
	}
	defer z.Close()
	out, e := io.ReadAll(io.LimitReader(contextReader{ctx, z}, n+1))
	if e != nil {
		return nil, e
	}
	if int64(len(out)) != n || digest(out) != txt(o, "raw_sha256") {
		return nil, errors.New("LEGACY_DETAIL_RAW_HASH_MISMATCH")
	}
	if e := ctx.Err(); e != nil {
		return nil, e
	}
	return out, nil
}
