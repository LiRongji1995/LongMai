package sqlitev2

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
)

// Quote contains exact decimal text and explicit nulls. It is a projection of
// immutable evidence, never a global-latest revision chosen for every cutoff.
type Quote struct {
	RowHash        string             `json:"row_hash"`
	Instrument     string             `json:"instrument_id"`
	Session        string             `json:"session"`
	ProjectionHash string             `json:"projection_hash"`
	Values         map[string]*string `json:"values"`
}
type QuoteWindow struct {
	SnapshotID string
	Instrument string
	Start      string
	End        string
}

// Quotes reads indexed columns without parsing the complete evidence JSON.
// Every read verifies raw-byte hash and the complete typed-column checksum.
// Full Verify additionally rebuilds the transformation from raw JSON; ordinary
// reads rely on that published verification plus the immutable table constraints.
// A malicious actor able to replace both data and checksums requires re-Verify.
func (s *Store) Quotes(ctx context.Context, q QuoteWindow, consume func(Quote) error) error {
	if e := s.Ready(ctx); e != nil {
		return e
	}
	if q.SnapshotID == "" || q.Instrument == "" || q.Start == "" || q.End == "" || q.Start > q.End {
		return errors.New("EXPLICIT_SNAPSHOT_WINDOW_REQUIRED")
	}
	rows, e := s.db.QueryContext(ctx, `SELECT h.row_hash,h.kind,h.instrument_id,h.session,h.raw_size,h.codec,h.payload,h.projection_hash,h.projection_version,`+prefixColumns("h.")+` FROM history_rows h JOIN partition_members m ON m.row_id=h.id JOIN snapshot_partitions sp ON sp.partition_id=m.partition_id WHERE sp.snapshot_id=? AND h.instrument_id=? AND h.session>=? AND h.session<=? AND h.kind='bar' ORDER BY h.session,h.row_hash`, q.SnapshotID, q.Instrument, q.Start, q.End)
	if e != nil {
		return e
	}
	defer rows.Close()
	for rows.Next() {
		if e = ctx.Err(); e != nil {
			return e
		}
		var hash, kind, iid, session, codec, ph, pv string
		var n int64
		var blob []byte
		p := make([]any, len(projectionColumns))
		ptr := []any{&hash, &kind, &iid, &session, &n, &codec, &blob, &ph, &pv}
		for i := range p {
			ptr = append(ptr, &p[i])
		}
		if e = rows.Scan(ptr...); e != nil {
			return e
		}
		if pv != ProjectionVersion {
			return errors.New("PROJECTION_VERSION_MISMATCH")
		}
		if _, e = decodeContext(ctx, codec, blob, n, hash); e != nil {
			return e
		}
		identity := append([]any{ProjectionVersion, kind, iid, session}, p...)
		raw, e := json.Marshal(identity)
		if e != nil {
			return e
		}
		if digest(raw) != ph {
			return errors.New("TYPED_PROJECTION_TAMPERED")
		}
		out := Quote{hash, iid, session, ph, map[string]*string{}}
		for i, key := range projectionColumns {
			if p[i] == nil {
				out.Values[key] = nil
			} else {
				v := fmt.Sprint(p[i])
				out.Values[key] = &v
			}
		}
		if e = consume(out); e != nil {
			return e
		}
	}
	return rows.Err()
}
