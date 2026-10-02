package sqlitev2

// This extension deliberately has its own DDL/version. The immutable raw input
// remains authoritative; every typed row is reconstructible from its exact bytes.
import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

const BatchProjectionVersion = "SQLITE_BATCH_EVIDENCE_PROJECTION_V1"

type batchField struct{ name, kind string }
type batchSpec struct {
	kind, table, relations string
	fields                 []batchField
	arrays                 []string
}

func batchFields(kind, names string) []batchField {
	var out []batchField
	for _, n := range strings.Fields(names) {
		out = append(out, batchField{n, kind})
	}
	return out
}
func batchJoin(parts ...[]batchField) []batchField {
	var out []batchField
	for _, p := range parts {
		out = append(out, p...)
	}
	return out
}

var batchClocks = batchFields("text", "event_time source_published_at first_seen_at ingested_at")
var batchSpecs = []batchSpec{
	{"themes", "batch_theme_headers", "batch_theme_relations", batchJoin(batchFields("text", "theme_id name theme_type definition_version effective_from effective_to"), batchClocks), []string{"member_ids", "source_refs"}},
	{"funds", "batch_fund_rows", "batch_fund_relations", batchJoin(batchFields("text", "instrument_id theme_id session trade_date event_session window_start window_end start_session end_session source_layer evidence_layer classification seat_id seat_name source_id dedupe_key record_type evidence_type completion_status provider_status query_status result_status currency amount_unit definition_version measurement_definition"), batchFields("decimal", "buy_amount_cny sell_amount_cny net_amount_cny amount_cny main_net_flow_cny large_order_net_flow_cny row_count"), batchClocks), []string{"source_refs", "expected_scope", "expected_instruments", "instrument_ids"}},
	{"receipts", "batch_receipt_scopes", "batch_receipt_relations", batchJoin(batchFields("text", "receipt_id content_hash provider dataset field_version status source_url source_type definition_version measurement_definition currency amount_unit"), batchFields("integer", "row_count"), batchFields("bool", "pagination_complete"), batchClocks, batchFields("text", "proof_policy_id proof_source_url proof_evidence_hash proof_applies_from proof_applies_to proof_verified_at proof_event_time_basis proof_publication_time_basis")), []string{"instrument_ids", "business_sessions", "proof_evidence_refs"}},
}

func batchSchemaDDL() string {
	var b strings.Builder
	b.WriteString(`CREATE TABLE batch_projection_versions(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL);
CREATE TABLE batch_projection_owners(owner_kind TEXT NOT NULL,owner_id TEXT NOT NULL,parent_raw_hash TEXT NOT NULL,conversion_version TEXT NOT NULL,manifest BLOB NOT NULL,manifest_hash TEXT NOT NULL,published_at TEXT NOT NULL,PRIMARY KEY(owner_kind,owner_id)) WITHOUT ROWID;
CREATE TABLE batch_projection_records(record_id TEXT PRIMARY KEY,owner_kind TEXT NOT NULL,owner_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,parent_raw_hash TEXT NOT NULL,raw_hash TEXT NOT NULL,evidence_id INTEGER,json_pointer TEXT NOT NULL,conversion_version TEXT NOT NULL,projection_hash TEXT NOT NULL,presence_json TEXT NOT NULL,UNIQUE(owner_kind,owner_id,kind,ordinal));
CREATE INDEX batch_projection_owner_records ON batch_projection_records(owner_kind,owner_id,kind,ordinal);
CREATE TABLE batch_theme_members(record_id TEXT NOT NULL REFERENCES batch_projection_records(record_id),ordinal INTEGER NOT NULL,instrument_id TEXT NOT NULL,PRIMARY KEY(record_id,ordinal)) WITHOUT ROWID;
CREATE TABLE batch_receipt_parameters(record_id TEXT NOT NULL REFERENCES batch_projection_records(record_id),name TEXT NOT NULL,value_type TEXT NOT NULL,value_text TEXT,PRIMARY KEY(record_id,name)) WITHOUT ROWID;
CREATE TABLE batch_projection_publications(manifest_hash TEXT PRIMARY KEY,manifest BLOB NOT NULL,owner_count INTEGER NOT NULL,published_at TEXT NOT NULL);
`)
	tables := []string{"batch_projection_versions", "batch_projection_owners", "batch_projection_records", "batch_theme_members", "batch_receipt_parameters", "batch_projection_publications"}
	for _, s := range batchSpecs {
		fmt.Fprintf(&b, "CREATE TABLE %s(record_id TEXT PRIMARY KEY REFERENCES batch_projection_records(record_id)", s.table)
		for _, f := range s.fields {
			typ := "TEXT"
			if f.kind == "integer" || f.kind == "bool" {
				typ = "INTEGER"
			}
			fmt.Fprintf(&b, ",%s %s", f.name, typ)
		}
		b.WriteString(");\n")
		fmt.Fprintf(&b, "CREATE TABLE %s(record_id TEXT NOT NULL REFERENCES batch_projection_records(record_id),field TEXT NOT NULL,ordinal INTEGER NOT NULL,value_text TEXT NOT NULL,PRIMARY KEY(record_id,field,ordinal)) WITHOUT ROWID;\n", s.relations)
		tables = append(tables, s.table, s.relations)
	}
	b.WriteString("CREATE INDEX batch_theme_lookup ON batch_theme_headers(theme_id,record_id);\nCREATE INDEX batch_theme_member_lookup ON batch_theme_members(instrument_id,record_id);\nCREATE INDEX batch_fund_lookup ON batch_fund_rows(theme_id,instrument_id,session,record_id);\nCREATE INDEX batch_receipt_lookup ON batch_receipt_scopes(receipt_id,content_hash,record_id);\n")
	for _, table := range tables {
		for _, op := range []string{"UPDATE", "DELETE"} {
			fmt.Fprintf(&b, "CREATE TRIGGER %s_no_%s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT,'immutable batch projection'); END;\n", table, op, op, table)
		}
	}
	return b.String()
}

func (s *Store) InitBatchProjectionSchema(ctx context.Context) error {
	return initBatchProjectionSchema(ctx, s.db)
}
func initBatchProjectionSchema(ctx context.Context, db *sql.DB) error {
	var n int
	if e := db.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE name='batch_projection_versions' AND type='table'").Scan(&n); e != nil {
		return e
	}
	if n == 0 {
		if e := db.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE name GLOB 'batch_*'").Scan(&n); e != nil {
			return e
		}
		if n != 0 {
			return errors.New("BATCH_PROJECTION_PARTIAL_SCHEMA")
		}
		tx, e := db.BeginTx(ctx, nil)
		if e != nil {
			return e
		}
		defer tx.Rollback()
		if _, e = tx.ExecContext(ctx, batchSchemaDDL()); e != nil {
			return e
		}
		if _, e = tx.ExecContext(ctx, "INSERT INTO batch_projection_versions VALUES(1,?,?)", digest([]byte(batchSchemaDDL())), appNow()); e != nil {
			return e
		}
		if e = tx.Commit(); e != nil {
			return e
		}
	}
	return verifyBatchSchema(ctx, db)
}
func verifyBatchSchema(ctx context.Context, db *sql.DB) error {
	var hash string
	if e := db.QueryRowContext(ctx, "SELECT checksum FROM batch_projection_versions WHERE version=1").Scan(&hash); e != nil {
		return e
	}
	if hash != digest([]byte(batchSchemaDDL())) {
		return errors.New("BATCH_SCHEMA_CHECKSUM_MISMATCH")
	}
	expected, e := sql.Open("sqlite", ":memory:")
	if e != nil {
		return e
	}
	defer expected.Close()
	if _, e = expected.ExecContext(ctx, batchSchemaDDL()); e != nil {
		return e
	}
	rows, e := expected.QueryContext(ctx, "SELECT name,type,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")
	if e != nil {
		return e
	}
	defer rows.Close()
	for rows.Next() {
		var name, kind, want, got string
		if e = rows.Scan(&name, &kind, &want); e != nil {
			return e
		}
		if e = db.QueryRowContext(ctx, "SELECT sql FROM sqlite_master WHERE name=? AND type=?", name, kind).Scan(&got); e != nil {
			return e
		}
		if want != got {
			return fmt.Errorf("BATCH_SCHEMA_OBJECT_CHANGED: %s", name)
		}
	}
	return rows.Err()
}

type batchRelation struct {
	Field   string `json:"field"`
	Ordinal int    `json:"ordinal"`
	Value   string `json:"value"`
}
type batchParameter struct {
	Name  string `json:"name"`
	Type  string `json:"type"`
	Value any    `json:"value"`
}
type batchProjection struct {
	ID, OwnerKind, OwnerID, Kind     string
	Ordinal                          int
	ParentHash, RawHash              string
	EvidenceID                       any
	Pointer, Version, Hash, Presence string
	Fields                           []any
	Relations                        []batchRelation
	Parameters                       []batchParameter
}

var batchDecimal = regexp.MustCompile(`^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$`)

func batchScalar(raw json.RawMessage, kind string) (any, error) {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 || bytes.Equal(raw, []byte("null")) {
		return nil, nil
	}
	switch kind {
	case "text":
		var v string
		if e := json.Unmarshal(raw, &v); e != nil {
			return nil, e
		}
		return v, nil
	case "bool":
		var v bool
		if e := json.Unmarshal(raw, &v); e != nil {
			return nil, e
		}
		if v {
			return int64(1), nil
		}
		return int64(0), nil
	case "integer":
		v, e := strconv.ParseInt(string(raw), 10, 64)
		if e != nil || v < 0 {
			return nil, errors.New("NONNEGATIVE_INTEGER_REQUIRED")
		}
		return v, nil
	case "decimal":
		v := string(raw)
		if raw[0] == '"' {
			if e := json.Unmarshal(raw, &v); e != nil {
				return nil, e
			}
		}
		if !batchDecimal.MatchString(v) {
			return nil, errors.New("EXACT_DECIMAL_REQUIRED")
		}
		return v, nil
	}
	return nil, errors.New("UNKNOWN_BATCH_FIELD_TYPE")
}

// decodeBatchProjection is shared by migrated snapshots and new native inputs.
// No model defaults are injected: presence/null distinctions and original exact
// decimal spelling remain independently verifiable against the source bytes.
func decodeBatchProjection(ctx context.Context, s batchSpec, raw []byte, p batchProjection) (batchProjection, error) {
	if e := ctx.Err(); e != nil {
		return p, e
	}
	obj, e := object(raw)
	if e != nil {
		return p, e
	}
	var proof map[string]json.RawMessage
	if r := obj["historical_time_proof"]; s.kind == "receipts" && len(r) > 0 && !bytes.Equal(bytes.TrimSpace(r), []byte("null")) {
		if e = json.Unmarshal(r, &proof); e != nil {
			return p, e
		}
	}
	get := func(key string) json.RawMessage {
		if strings.HasPrefix(key, "proof_") {
			return proof[strings.TrimPrefix(key, "proof_")]
		}
		return obj[key]
	}
	presence := map[string]string{}
	mark := func(key string, r json.RawMessage) {
		v := "ABSENT"
		if len(r) > 0 {
			v = "VALUE"
			if bytes.Equal(bytes.TrimSpace(r), []byte("null")) {
				v = "NULL"
			}
		}
		presence[key] = v
	}
	for _, f := range s.fields {
		r := get(f.name)
		mark(f.name, r)
		v, err := batchScalar(r, f.kind)
		if err != nil {
			if s.kind != "funds" {
				return p, fmt.Errorf("BATCH_FIELD %s.%s: %w", s.kind, f.name, err)
			}
			// Funds are deliberately an open dictionary in the frozen contract.
			// Keep malformed values as unavailable, never invent zero or reject
			// a previously accepted input because of an optional query projection.
			presence[f.name] = "INVALID_" + strings.ToUpper(f.kind)
			v = nil
		}
		p.Fields = append(p.Fields, v)
	}
	for _, key := range s.arrays {
		r := get(key)
		mark(key, r)
		if len(r) == 0 || bytes.Equal(bytes.TrimSpace(r), []byte("null")) {
			continue
		}
		var a []string
		if e = json.Unmarshal(r, &a); e != nil {
			if s.kind != "funds" {
				return p, e
			}
			presence[key] = "INVALID_STRINGS"
			continue
		}
		for i, v := range a {
			p.Relations = append(p.Relations, batchRelation{key, i, v})
		}
	}
	if s.kind == "receipts" {
		mark("parameters", obj["parameters"])
		mark("historical_time_proof", obj["historical_time_proof"])
		var m map[string]json.RawMessage
		if r := obj["parameters"]; len(r) > 0 && !bytes.Equal(bytes.TrimSpace(r), []byte("null")) {
			if e = json.Unmarshal(r, &m); e != nil {
				return p, e
			}
		}
		keys := make([]string, 0, len(m))
		for k := range m {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			r := bytes.TrimSpace(m[k])
			v := batchParameter{Name: k}
			switch {
			case bytes.Equal(r, []byte("null")):
				v.Type = "NULL"
			case len(r) > 0 && r[0] == '"':
				v.Type = "TEXT"
				var text string
				if e = json.Unmarshal(r, &text); e != nil {
					return p, e
				}
				v.Value = text
			case bytes.Equal(r, []byte("true")) || bytes.Equal(r, []byte("false")):
				v.Type = "BOOL"
				v.Value = string(r)
			default:
				if !batchDecimal.Match(r) {
					return p, errors.New("BATCH_PARAMETER_SCALAR_REQUIRED")
				}
				v.Type = "DECIMAL"
				v.Value = string(r)
			}
			p.Parameters = append(p.Parameters, v)
		}
	}
	b, _ := json.Marshal(presence)
	p.Presence = string(b)
	p.Version = BatchProjectionVersion
	p.RawHash = digest(raw)
	b, _ = json.Marshal([]any{p.OwnerKind, p.OwnerID, p.Kind, p.Ordinal})
	p.ID = digest(b)
	b, _ = json.Marshal([]any{p.Version, p.Kind, p.Presence, p.Fields, p.Relations, p.Parameters})
	p.Hash = digest(b)
	return p, nil
}
