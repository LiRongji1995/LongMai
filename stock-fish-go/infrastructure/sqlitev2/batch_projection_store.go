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
	"strings"
)

type batchOwner struct {
	Kind    string `json:"kind"`
	ID      string `json:"id"`
	RawHash string `json:"raw_hash"`
}
type BatchProjectionOptions struct {
	BatchSize     int
	MaxBatches    int
	Progress      func(string, int64)
	ResourceCheck func() error
}
type BatchProjectionReport struct {
	Status            string `json:"status"`
	Owners            int64  `json:"owners"`
	Records           int64  `json:"records"`
	Themes            int64  `json:"themes"`
	Funds             int64  `json:"funds"`
	Receipts          int64  `json:"receipts"`
	ManifestHash      string `json:"manifest_hash"`
	ConversionVersion string `json:"conversion_version"`
}
type batchQuerier interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
	QueryRowContext(context.Context, string, ...any) *sql.Row
}

const batchOwnerSQL = `SELECT 'SNAPSHOT',s.snapshot_id,e.raw_sha256 FROM snapshots s LEFT JOIN evidence_objects e ON e.id=s.evidence_id UNION ALL SELECT 'APP_INPUT',input_id,raw_hash FROM app_inputs ORDER BY 1,2`

func batchOwners(ctx context.Context, q batchQuerier) ([]batchOwner, error) {
	rows, e := q.QueryContext(ctx, batchOwnerSQL)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	var out []batchOwner
	for rows.Next() {
		var o batchOwner
		if e = rows.Scan(&o.Kind, &o.ID, &o.RawHash); e != nil {
			return nil, e
		}
		out = append(out, o)
	}
	return out, rows.Err()
}
func batchOwnerRaw(ctx context.Context, tx *sql.Tx, o batchOwner) ([]byte, error) {
	var raw []byte
	if o.Kind == "APP_INPUT" {
		var hash string
		if e := tx.QueryRowContext(ctx, "SELECT payload,raw_hash FROM app_inputs WHERE input_id=?", o.ID).Scan(&raw, &hash); e != nil {
			return nil, e
		}
		if hash != o.RawHash || digest(raw) != o.RawHash {
			return nil, errors.New("BATCH_PARENT_RAW_HASH_MISMATCH")
		}
		if len(raw) > 128<<20 {
			return nil, errors.New("BATCH_PARENT_SIZE_LIMIT")
		}
		return raw, nil
	}
	var hash, codec string
	var size int64
	var packed []byte
	if e := tx.QueryRowContext(ctx, "SELECT e.raw_sha256,e.codec,e.raw_size,e.payload FROM snapshots s JOIN evidence_objects e ON e.id=s.evidence_id WHERE s.snapshot_id=?", o.ID).Scan(&hash, &codec, &size, &packed); e != nil {
		return nil, e
	}
	if hash != o.RawHash {
		return nil, errors.New("BATCH_PARENT_IDENTITY_MISMATCH")
	}
	return decodeContext(ctx, codec, packed, size, hash)
}

type batchOwnerSummary struct {
	Records, Themes, Funds, Receipts int64
	MembersHash                      string
}

func walkExpectedBatchOwner(ctx context.Context, tx *sql.Tx, o batchOwner, raw []byte, consume func(batchProjection) error) (batchOwnerSummary, error) {
	var out batchOwnerSummary
	if len(raw) > 128<<20 {
		return out, errors.New("BATCH_PARENT_SIZE_LIMIT")
	}
	membership := sha256.New()
	if digest(raw) != o.RawHash {
		return out, errors.New("BATCH_PARENT_BYTES_MISMATCH")
	}
	obj, e := object(raw)
	if e != nil {
		return out, e
	}
	prefix := ""
	if r, present := obj["input"]; present {
		obj, e = object(r)
		if e != nil {
			return out, e
		}
		prefix = "/input"
	}
	for _, spec := range batchSpecs {
		members, e := snapshotMembers(obj, spec.kind)
		if e != nil {
			return out, e
		}
		var ids []int64
		if o.Kind == "SNAPSHOT" {
			rows, e := tx.QueryContext(ctx, "SELECT m.ordinal,m.evidence_id,e.raw_sha256,e.codec,e.raw_size,e.payload FROM snapshot_members m JOIN evidence_objects e ON e.id=m.evidence_id WHERE m.snapshot_id=? AND m.kind=? ORDER BY m.ordinal", o.ID, spec.kind)
			if e != nil {
				return out, e
			}
			for rows.Next() {
				var ordinal int
				var id, size int64
				var hash, codec string
				var packed []byte
				if e = rows.Scan(&ordinal, &id, &hash, &codec, &size, &packed); e != nil {
					rows.Close()
					return out, e
				}
				if ordinal != len(ids) || ordinal >= len(members) {
					rows.Close()
					return out, errors.New("BATCH_SOURCE_MEMBERSHIP_MISMATCH")
				}
				got, err := decodeContext(ctx, codec, packed, size, hash)
				if err != nil {
					rows.Close()
					return out, err
				}
				if !bytes.Equal(got, members[ordinal]) {
					rows.Close()
					return out, errors.New("BATCH_SOURCE_MEMBER_BYTES_MISMATCH")
				}
				ids = append(ids, id)
			}
			e = rows.Err()
			rows.Close()
			if e != nil {
				return out, e
			}
			if len(ids) != len(members) {
				return out, errors.New("BATCH_SOURCE_MEMBER_COUNT_MISMATCH")
			}
		}
		for i, member := range members {
			p := batchProjection{OwnerKind: o.Kind, OwnerID: o.ID, Kind: spec.kind, Ordinal: i, ParentHash: o.RawHash, Pointer: fmt.Sprintf("%s/%s/%d", prefix, spec.kind, i)}
			if o.Kind == "SNAPSHOT" {
				p.EvidenceID = ids[i]
			}
			p, e = decodeBatchProjection(ctx, spec, member, p)
			if e != nil {
				return out, e
			}
			if e = consume(p); e != nil {
				return out, e
			}
			entry, _ := json.Marshal([]any{p.ID, p.Kind, p.Ordinal, p.RawHash, p.EvidenceID, p.Pointer, p.Hash})
			membership.Write(entry)
			membership.Write([]byte{'\n'})
			out.Records++
			switch p.Kind {
			case "themes":
				out.Themes++
			case "funds":
				out.Funds++
			case "receipts":
				out.Receipts++
			}
		}
	}
	out.MembersHash = hex.EncodeToString(membership.Sum(nil))
	return out, nil
}
func batchSpecFor(kind string) batchSpec {
	for _, s := range batchSpecs {
		if s.kind == kind {
			return s
		}
	}
	panic("internal batch projection kind")
}
func batchPlaceholders(n int) string { return strings.TrimSuffix(strings.Repeat("?,", n), ",") }
func insertBatchProjection(ctx context.Context, tx *sql.Tx, p batchProjection) error {
	_, e := tx.ExecContext(ctx, "INSERT INTO batch_projection_records VALUES("+batchPlaceholders(12)+")", p.ID, p.OwnerKind, p.OwnerID, p.Kind, p.Ordinal, p.ParentHash, p.RawHash, p.EvidenceID, p.Pointer, p.Version, p.Hash, p.Presence)
	if e != nil {
		return e
	}
	s := batchSpecFor(p.Kind)
	args := append([]any{p.ID}, p.Fields...)
	if _, e = tx.ExecContext(ctx, "INSERT INTO "+s.table+" VALUES("+batchPlaceholders(len(args))+")", args...); e != nil {
		return e
	}
	for _, r := range p.Relations {
		if r.Field == "member_ids" {
			_, e = tx.ExecContext(ctx, "INSERT INTO batch_theme_members VALUES(?,?,?)", p.ID, r.Ordinal, r.Value)
		} else {
			_, e = tx.ExecContext(ctx, "INSERT INTO "+s.relations+" VALUES(?,?,?,?)", p.ID, r.Field, r.Ordinal, r.Value)
		}
		if e != nil {
			return e
		}
	}
	for _, v := range p.Parameters {
		if _, e = tx.ExecContext(ctx, "INSERT INTO batch_receipt_parameters VALUES(?,?,?,?)", p.ID, v.Name, v.Type, v.Value); e != nil {
			return e
		}
	}
	return nil
}
func batchEqual(a, b any) bool {
	x, _ := json.Marshal(a)
	y, _ := json.Marshal(b)
	return bytes.Equal(x, y)
}
func batchVerifyValues(ctx context.Context, tx *sql.Tx, query string, args []any, want []any) error {
	got := make([]any, len(want))
	ptr := make([]any, len(want))
	for i := range got {
		ptr[i] = &got[i]
	}
	if e := tx.QueryRowContext(ctx, query, args...).Scan(ptr...); e != nil {
		return e
	}
	for i, v := range got {
		if b, ok := v.([]byte); ok {
			got[i] = string(b)
		}
	}
	if !batchEqual(got, want) {
		return errors.New("BATCH_TYPED_PROJECTION_MISMATCH")
	}
	return nil
}
func verifyBatchProjection(ctx context.Context, tx *sql.Tx, p batchProjection) error {
	want := []any{p.OwnerKind, p.OwnerID, p.Kind, p.Ordinal, p.ParentHash, p.RawHash, p.EvidenceID, p.Pointer, p.Version, p.Hash, p.Presence}
	if e := batchVerifyValues(ctx, tx, "SELECT owner_kind,owner_id,kind,ordinal,parent_raw_hash,raw_hash,evidence_id,json_pointer,conversion_version,projection_hash,presence_json FROM batch_projection_records WHERE record_id=?", []any{p.ID}, want); e != nil {
		return e
	}
	s := batchSpecFor(p.Kind)
	var cols []string
	for _, f := range s.fields {
		cols = append(cols, f.name)
	}
	if e := batchVerifyValues(ctx, tx, "SELECT "+strings.Join(cols, ",")+" FROM "+s.table+" WHERE record_id=?", []any{p.ID}, p.Fields); e != nil {
		return e
	}
	// Query both relations using their logical order, and compare the complete
	// lists (including duplicates and ordinals), not merely stored hashes.
	for _, field := range s.arrays {
		query := "SELECT ordinal,value_text FROM " + s.relations + " WHERE record_id=? AND field=? ORDER BY ordinal"
		args := []any{p.ID, field}
		if field == "member_ids" {
			query = "SELECT ordinal,instrument_id FROM batch_theme_members WHERE record_id=? ORDER BY ordinal"
			args = []any{p.ID}
		}
		rows, e := tx.QueryContext(ctx, query, args...)
		if e != nil {
			return e
		}
		var got, want []batchRelation
		for rows.Next() {
			r := batchRelation{Field: field}
			if e = rows.Scan(&r.Ordinal, &r.Value); e != nil {
				rows.Close()
				return e
			}
			got = append(got, r)
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return e
		}
		for _, r := range p.Relations {
			if r.Field == field {
				want = append(want, r)
			}
		}
		if !batchEqual(got, want) {
			return errors.New("BATCH_RELATION_MISMATCH")
		}
	}
	var count int
	expected := 0
	for _, r := range p.Relations {
		if r.Field != "member_ids" {
			expected++
		}
	}
	if e := tx.QueryRowContext(ctx, "SELECT count(*) FROM "+s.relations+" WHERE record_id=?", p.ID).Scan(&count); e != nil {
		return e
	}
	if count != expected {
		return errors.New("BATCH_EXTRA_RELATION")
	}
	rows, e := tx.QueryContext(ctx, "SELECT name,value_type,value_text FROM batch_receipt_parameters WHERE record_id=? ORDER BY name", p.ID)
	if e != nil {
		return e
	}
	var params []batchParameter
	for rows.Next() {
		var p batchParameter
		if e = rows.Scan(&p.Name, &p.Type, &p.Value); e != nil {
			rows.Close()
			return e
		}
		params = append(params, p)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	if !batchEqual(params, p.Parameters) {
		return errors.New("BATCH_PARAMETER_MISMATCH")
	}
	return nil
}
func batchOwnerManifest(o batchOwner, summary batchOwnerSummary) ([]byte, string) {
	raw, _ := json.Marshal(map[string]any{"version": BatchProjectionVersion, "owner": o, "member_count": summary.Records, "members_hash": summary.MembersHash, "member_hash_contract": "SHA256_NEWLINE_JSON_ORDERED_ID_KIND_ORDINAL_RAW_EVIDENCE_POINTER_PROJECTION_V1"})
	return raw, digest(raw)
}
func verifyBatchOwner(ctx context.Context, tx *sql.Tx, o batchOwner, summary batchOwnerSummary, manifestRequired bool) error {
	var n int64
	if e := tx.QueryRowContext(ctx, "SELECT count(*) FROM batch_projection_records WHERE owner_kind=? AND owner_id=?", o.Kind, o.ID).Scan(&n); e != nil {
		return e
	}
	if n != summary.Records {
		return errors.New("BATCH_RECORD_COUNT_MISMATCH")
	}
	if manifestRequired {
		raw, hash := batchOwnerManifest(o, summary)
		return batchVerifyValues(ctx, tx, "SELECT parent_raw_hash,conversion_version,manifest,manifest_hash FROM batch_projection_owners WHERE owner_kind=? AND owner_id=?", []any{o.Kind, o.ID}, []any{o.RawHash, BatchProjectionVersion, string(raw), hash})
	}
	return nil
}
func projectBatchOwner(ctx context.Context, tx *sql.Tx, o batchOwner, raw []byte) (batchOwnerSummary, error) {
	var summary batchOwnerSummary
	var hash string
	e := tx.QueryRowContext(ctx, "SELECT parent_raw_hash FROM batch_projection_owners WHERE owner_kind=? AND owner_id=?", o.Kind, o.ID).Scan(&hash)
	existing := e == nil
	if existing && hash != o.RawHash {
		return summary, errors.New("BATCH_IMMUTABLE_OWNER_CONFLICT")
	}
	if e != nil && !errors.Is(e, sql.ErrNoRows) {
		return summary, e
	}
	summary, e = walkExpectedBatchOwner(ctx, tx, o, raw, func(p batchProjection) error {
		if !existing {
			if e := insertBatchProjection(ctx, tx, p); e != nil {
				return e
			}
		}
		return verifyBatchProjection(ctx, tx, p)
	})
	if e != nil {
		return summary, e
	}
	if e = verifyBatchOwner(ctx, tx, o, summary, existing); e != nil {
		return summary, e
	}
	if existing {
		return summary, nil
	}
	manifest, mh := batchOwnerManifest(o, summary)
	if _, e = tx.ExecContext(ctx, "INSERT INTO batch_projection_owners VALUES(?,?,?,?,?,?,?)", o.Kind, o.ID, o.RawHash, BatchProjectionVersion, manifest, mh, appNow()); e != nil {
		return summary, e
	}
	return summary, nil
}

// Called in the same transaction that publishes the immutable app_input.
func projectBatchInput(ctx context.Context, tx *sql.Tx, id, rawHash string, raw []byte) error {
	if _, e := projectBatchOwner(ctx, tx, batchOwner{"APP_INPUT", id, rawHash}, raw); e != nil {
		return e
	}
	// Earlier legacy owners may still be awaiting their explicit backfill. Do not
	// announce a complete extension until every current source owner is represented.
	_, _, e := publishBatchInventory(ctx, tx, false)
	return e
}
