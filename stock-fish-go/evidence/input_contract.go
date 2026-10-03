package evidence

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"strconv"
	"strings"
	"time"
)

// InputContractVersion identifies a Go validation projection, not a Python
// model digest. Raw bytes and any supplied Python source hash retain separate
// identities; defaults never change the immutable source bytes.
const InputContractVersion = "GO_STRICT_MARKET_INPUT_V1"

// FrozenEnvelopeContractVersion only versions provenance metadata around the
// market model. It must not change stored evidence or market projections.
const FrozenEnvelopeContractVersion = "GO_FROZEN_ENVELOPE_V2"

type contractField struct {
	kind               string
	required, nullable bool
	fallback           string
	choices            []string
}
type contractSchema map[string]contractField

func required(kind string) contractField { return contractField{kind: kind, required: true} }
func optional(kind, fallback string) contractField {
	return contractField{kind: kind, nullable: fallback == "null", fallback: fallback}
}
func nullable(kind string) contractField {
	return contractField{kind: kind, required: true, nullable: true}
}
func enumeration(requiredField bool, fallback string, choices ...string) contractField {
	return contractField{kind: "string", required: requiredField, fallback: fallback, choices: choices}
}
func clockSchema() contractSchema {
	s := contractSchema{}
	for _, k := range []string{"event_time", "source_published_at", "first_seen_at", "ingested_at"} {
		s[k] = required("datetime")
	}
	return s
}
func sourceSchema() contractSchema {
	s := clockSchema()
	for _, k := range []string{"receipt_id", "provider", "dataset", "field_version"} {
		s[k] = required("string")
	}
	s["content_hash"] = required("hash")
	s["row_count"] = required("nonnegative_integer")
	s["pagination_complete"] = required("bool")
	s["status"] = enumeration(true, "", "COMPLETE", "PARTIAL", "INVALID", "FETCH_FAILED", "NOT_PUBLISHED")
	s["parameters"] = optional("parameters", "{}")
	for _, k := range []string{"source_url", "source_type", "definition_version", "measurement_definition", "currency", "amount_unit"} {
		s[k] = optional("string", "null")
	}
	s["instrument_ids"] = optional("strings", "[]")
	s["business_sessions"] = optional("dates", "[]")
	s["historical_time_proof"] = optional("proof", "null")
	return s
}
func securitySchema() contractSchema {
	s := clockSchema()
	s["instrument_id"] = required("string")
	s["exchange"] = enumeration(true, "", "SSE", "SZSE", "BSE", "UNKNOWN")
	s["board"] = enumeration(true, "", "MAIN", "STAR", "CHINEXT", "BJ", "UNKNOWN")
	s["security_type"] = enumeration(true, "", "A_COMMON", "B_SHARE", "FUND", "BOND", "OTHER", "UNKNOWN")
	s["listed_on"] = required("date")
	s["effective_from"] = required("date")
	s["effective_to"] = optional("date", "null")
	s["delisted_on"] = optional("date", "null")
	s["st"] = nullable("bool")
	s["suspended"] = nullable("bool")
	s["delisting_period"] = optional("bool", "false")
	f := s["delisting_period"]
	f.nullable = true
	s["delisting_period"] = f
	s["name"] = optional("string", `""`)
	s["source_refs"] = optional("strings", "[]")
	s["risk_flags"] = optional("strings", "[]")
	return s
}
func barSchema() contractSchema {
	s := clockSchema()
	s["instrument_id"] = required("string")
	s["session"] = required("date")
	for _, k := range []string{"open", "high", "low", "close", "reference_prev_close", "limit_up", "limit_down", "comparable_open", "comparable_high", "comparable_low", "comparable_close", "float_shares"} {
		s[k] = optional("positive_decimal", "null")
	}
	for _, k := range []string{"volume_shares", "amount_cny"} {
		s[k] = optional("nonnegative_decimal", "null")
	}
	s["limit_applicable"] = optional("bool", "null")
	s["limit_verified"] = optional("bool", "false")
	s["tick_size"] = optional("positive_decimal", `"0.01"`)
	for _, k := range []string{"rule_id", "comparable_basis", "turnover_basis"} {
		s[k] = optional("string", "null")
	}
	s["float_shares_as_of"] = optional("datetime", "null")
	s["turnover_fraction"] = optional("turnover", "null")
	s["source_refs"] = optional("strings", "[]")
	s["quality"] = enumeration(false, `"VALID"`, "VALID", "PARTIAL", "INVALID")
	return s
}
func themeSchema() contractSchema {
	s := clockSchema()
	for _, k := range []string{"theme_id", "name", "definition_version"} {
		s[k] = required("string")
	}
	s["theme_type"] = enumeration(true, "", "INDUSTRY", "EVENT", "INDUSTRY_PROXY", "QUALIFICATION", "UNKNOWN")
	s["member_ids"] = required("strings")
	s["effective_from"] = required("date")
	s["effective_to"] = optional("date", "null")
	s["source_refs"] = optional("strings", "[]")
	return s
}
func universeSchema() contractSchema {
	s := clockSchema()
	s["snapshot_id"] = required("string")
	s["securities"] = required("securities")
	s["source_refs"] = optional("strings", "[]")
	s["complete"] = optional("bool", "false")
	s["expected_count"] = optional("nonnegative_integer", "null")
	return s
}
func proofSchema() contractSchema {
	s := contractSchema{"policy_id": required("nonempty_string"), "source_url": required("nonempty_string"), "evidence_hash": required("hash"), "applies_from": required("date"), "applies_to": required("date"), "evidence_refs": required("strings"), "verified_at": required("datetime")}
	s["event_time_basis"] = enumeration(true, "", "SOURCE_DATED_EVENT_RECORD", "SOURCE_VERIFIED_EVENT_TIMESTAMP")
	s["publication_time_basis"] = enumeration(true, "", "SOURCE_PUBLICATION_TIMESTAMP", "SOURCE_DOCUMENTED_RELEASE_RULE")
	return s
}
func emotionSchema() contractSchema {
	s := contractSchema{"session": required("date"), "as_of": required("datetime"), "score": nullable("number"), "state": required("string")}
	for _, k := range []string{"components", "readings", "coverage", "history_selection", "publication_authority"} {
		s[k] = optional("object", "{}")
	}
	s["reasons"] = optional("strings", "[]")
	for k, v := range map[string]string{"emotion_definition_version": "LEGACY_R02_D1", "pool_profile": "MAIN_NON_ST", "coverage_policy": "LEGACY_R02_COVERAGE", "authorization_mode": "PUBLISHED_CANDIDATE"} {
		b, _ := json.Marshal(v)
		s[k] = optional("string", string(b))
	}
	for _, k := range []string{"strategy_id", "config_hash", "feature_definition_version", "document_id", "effective_input_hash"} {
		s[k] = optional("string", "null")
	}
	s["coverage_type"] = optional("coverage", "null")
	s["source_complete"] = optional("bool", "false")
	s["published_at"] = optional("datetime", "null")
	s["research_available_as_of"] = optional("datetime", "null")
	return s
}
func marketSchema() contractSchema {
	s := contractSchema{"observation_date": required("date"), "sessions": required("dates"), "universe": required("universe"), "bars": required("bars"), "coverage_type": required("coverage")}
	for k, kind := range map[string]string{"themes": "themes", "receipts": "receipts", "previous_emotions": "emotions", "funds": "objects"} {
		s[k] = optional(kind, "[]")
	}
	s["history_artifact_ref"] = optional("string", "null")
	s["history_artifact_hash"] = optional("hash", "null")
	return s
}

var contractSchemas = map[string]contractSchema{
	"security": securitySchema(), "bar": barSchema(), "theme": themeSchema(),
	"receipt": sourceSchema(), "universe": universeSchema(), "proof": proofSchema(),
	"emotion": emotionSchema(), "market": marketSchema(),
}

func ValidHash(v string) bool {
	_, e := hex.DecodeString(v)
	return e == nil && len(v) == 64 && v == strings.ToLower(v)
}
func contractError(path, code string) error { return fmt.Errorf("INPUT_CONTRACT_%s: %s", code, path) }
func IsNull(v []byte) bool                  { return bytes.Equal(bytes.TrimSpace(v), []byte("null")) }
func decodeObject(raw []byte, path string) (contracts.Row, error) {
	if len(bytes.TrimSpace(raw)) == 0 || bytes.TrimSpace(raw)[0] != '{' {
		return nil, contractError(path, "OBJECT_REQUIRED")
	}
	var r contracts.Row
	if e := json.Unmarshal(raw, &r); e != nil {
		return nil, e
	}
	return r, nil
}
func validateObject(ctx context.Context, r contracts.Row, schema contractSchema, path string) error {
	if e := ctx.Err(); e != nil {
		return e
	}
	if r == nil {
		return contractError(path, "OBJECT_REQUIRED")
	}
	for k := range r {
		if _, ok := schema[k]; !ok {
			return contractError(path+"."+k, "EXTRA_FIELD")
		}
	}
	for k, f := range schema {
		p := path + "." + k
		raw, ok := r[k]
		if !ok {
			if f.required {
				return contractError(p, "MISSING_FIELD")
			}
			raw = json.RawMessage(f.fallback)
			r[k] = raw
		}
		if IsNull(raw) {
			if f.nullable {
				continue
			}
			return contractError(p, "NULL_NOT_ALLOWED")
		}
		out, e := validateValue(ctx, raw, f, p)
		if e != nil {
			return e
		}
		r[k] = out
	}
	if _, ok := schema["event_time"]; ok {
		if _, e := clocks(r); e != nil {
			return contractError(path, e.Error())
		}
	}
	return nil
}
func validateValue(ctx context.Context, raw json.RawMessage, f contractField, path string) (json.RawMessage, error) {
	fail := func(code string) (json.RawMessage, error) { return nil, contractError(path, code) }
	switch f.kind {
	case "frozen_parent", "frozen_supplement":
		r, e := decodeObject(raw, path)
		if e != nil {
			return nil, e
		}
		var schema contractSchema
		if f.kind == "frozen_supplement" {
			schema = contractSchema{"actual_capture_clock_preserved": required("bool"), "added_bar_revisions": required("nonnegative_integer"), "normalization_report": required("nonempty_string")}
		} else if _, ok := r["file_sha256"]; ok {
			schema = contractSchema{"file_sha256": required("hash"), "path": required("nonempty_string"), "purpose": required("nonempty_string")}
		} else if _, ok := r["namespace"]; ok {
			schema = contractSchema{"namespace": required("nonempty_string"), "external_id": required("nonempty_string"), "raw_sha256": required("hash")}
		} else {
			return fail("UNKNOWN_PARENT_INPUT_VARIANT")
		}
		if e = validateObject(ctx, r, schema, path); e != nil {
			return nil, e
		}
		// These are validated provenance descriptors, not paths to read or
		// instructions to rewrite the immutable input bytes.
	case "string", "nonempty_string", "date", "datetime", "hash", "coverage":
		var v string
		if e := json.Unmarshal(raw, &v); e != nil {
			return fail("STRING_REQUIRED")
		}
		if f.kind == "nonempty_string" && v == "" {
			return fail("EMPTY_STRING")
		}
		if f.kind == "date" {
			if len(v) != 10 {
				return fail("INVALID_DATE")
			}
			if _, e := time.Parse("2006-01-02", v); e != nil {
				return fail("INVALID_DATE")
			}
		}
		if f.kind == "datetime" {
			t, e := time.Parse(time.RFC3339Nano, v)
			if e != nil {
				return fail("EXPLICIT_TIMEZONE_REQUIRED")
			}
			// contracts.Frozen Python evidence uses microsecond datetimes. Retain the raw
			// bytes separately; the validation projection matches that precision.
			t = t.Truncate(time.Microsecond)
			layout := time.RFC3339
			if t.Nanosecond() != 0 {
				layout = "2006-01-02T15:04:05.000000Z07:00"
			}
			raw, _ = json.Marshal(t.Format(layout))
		}
		if f.kind == "hash" && !ValidHash(v) {
			return fail("INVALID_SHA256")
		}
		if f.kind == "coverage" && !contracts.Contains([]string{"PROSPECTIVE_CAPTURED", "RECONSTRUCTED_HISTORICAL", "UNVERIFIABLE_AS_OF", "TEST_FIXTURE"}, v) {
			return fail("INVALID_ENUM")
		}
		if len(f.choices) > 0 && !contracts.Contains(f.choices, v) {
			return fail("INVALID_ENUM")
		}
	case "bool":
		var v bool
		if e := json.Unmarshal(raw, &v); e != nil {
			return fail("BOOLEAN_REQUIRED")
		}
	case "number", "positive_decimal", "nonnegative_decimal", "turnover", "nonnegative_integer":
		s := string(raw)
		if len(raw) > 0 && raw[0] == '"' {
			if f.kind == "number" || f.kind == "turnover" || f.kind == "nonnegative_integer" {
				return fail("NUMBER_REQUIRED")
			}
			if e := json.Unmarshal(raw, &s); e != nil {
				return fail("INVALID_DECIMAL")
			}
		}
		n, e := contracts.Parse(s)
		if e != nil {
			return fail("INVALID_DECIMAL")
		}
		if f.kind == "nonnegative_integer" && (!n.IsInt() || strings.ContainsAny(s, ".eE")) {
			return fail("INTEGER_REQUIRED")
		}
		if (f.kind == "positive_decimal" && !n.Positive()) || ((f.kind == "nonnegative_decimal" || f.kind == "nonnegative_integer" || f.kind == "turnover") && n.Cmp(contracts.N("0")) < 0) {
			return fail("NUMBER_OUT_OF_RANGE")
		}
		if f.kind == "turnover" && n.Cmp(contracts.N("10")) > 0 {
			return fail("NUMBER_OUT_OF_RANGE")
		}
		if f.kind == "positive_decimal" || f.kind == "nonnegative_decimal" {
			raw, _ = json.Marshal(decimalProjection(s))
		}
	case "strings", "dates", "securities", "bars", "themes", "receipts", "emotions", "objects":
		if len(raw) == 0 || raw[0] != '[' {
			return fail("ARRAY_REQUIRED")
		}
		var a []json.RawMessage
		if e := json.Unmarshal(raw, &a); e != nil {
			return nil, e
		}
		for i, v := range a {
			if e := ctx.Err(); e != nil {
				return nil, e
			}
			p := fmt.Sprintf("%s[%d]", path, i)
			kind := map[string]string{"strings": "string", "dates": "date", "securities": "security", "bars": "bar", "themes": "theme", "receipts": "receipt", "emotions": "emotion", "objects": "object"}[f.kind]
			if IsNull(v) {
				return nil, contractError(p, "NULL_NOT_ALLOWED")
			}
			out, e := validateValue(ctx, v, required(kind), p)
			if e != nil {
				return nil, e
			}
			a[i] = out
		}
		raw, _ = json.Marshal(a)
	case "object", "parameters", "security", "bar", "theme", "receipt", "universe", "proof", "emotion", "market":
		r, e := decodeObject(raw, path)
		if e != nil {
			return nil, e
		}
		if f.kind == "parameters" {
			for k, v := range r {
				t := bytes.TrimSpace(v)
				if len(t) == 0 || t[0] == '{' || t[0] == '[' {
					return nil, contractError(path+"."+k, "SCALAR_REQUIRED")
				}
			}
		}
		if schema, ok := contractSchemas[f.kind]; ok {
			if e = validateObject(ctx, r, schema, path); e != nil {
				return nil, e
			}
		}
		if f.kind == "proof" && !proofValid(r, r.Text("applies_from")) {
			return fail("INVALID_HISTORICAL_PROOF")
		}
		raw, _ = json.Marshal(r)
	default:
		return fail("UNKNOWN_FIELD_SCHEMA")
	}
	return raw, nil
}

// decimalProjection follows Decimal's finite string representation without
// converting through a binary float. Precision/trailing zeros remain explicit.
func decimalProjection(s string) string {
	negative := strings.HasPrefix(s, "-")
	s = strings.TrimPrefix(strings.TrimPrefix(s, "+"), "-")
	exponent := 0
	if i := strings.IndexAny(s, "eE"); i >= 0 {
		exponent, _ = strconv.Atoi(s[i+1:])
		s = s[:i]
	}
	if i := strings.IndexByte(s, '.'); i >= 0 {
		exponent -= len(s) - i - 1
		s = s[:i] + s[i+1:]
	}
	s = strings.TrimLeft(s, "0")
	if s == "" {
		s = "0"
	}
	adjusted := exponent + len(s) - 1
	var out string
	if exponent > 0 || adjusted < -6 {
		out = s[:1]
		if len(s) > 1 {
			out += "." + s[1:]
		}
		out += "E"
		if adjusted >= 0 {
			out += "+"
		}
		out += strconv.Itoa(adjusted)
	} else if exponent < 0 {
		point := len(s) + exponent
		if point > 0 {
			out = s[:point] + "." + s[point:]
		} else {
			out = "0." + strings.Repeat("0", -point) + s
		}
	} else {
		out = s
	}
	if negative {
		out = "-" + out
	}
	return out
}

// DecodeFrozen strictly checks the serialized boundary before any strategy use.
// It preserves explicit null and only applies a default when its field is absent.
func DecodeFrozen(raw []byte) (contracts.Frozen, error) {
	return DecodeFrozenContext(context.Background(), raw)
}
func DecodeFrozenContext(ctx context.Context, raw []byte) (contracts.Frozen, error) {
	var f contracts.Frozen
	if _, e := contracts.CanonicalContext(ctx, raw); e != nil {
		return f, e
	}
	r, e := decodeObject(raw, "frozen")
	if e != nil {
		return f, e
	}
	schema := contractSchema{"input": required("market"), "cutoff": required("datetime"), "purpose": optional("string", `""`)}
	for key, kind := range map[string]string{"parent_input": "frozen_parent", "supplement": "frozen_supplement", "target_session": "date"} {
		if _, present := r[key]; present {
			schema[key] = required(kind)
		}
	}
	if e = validateObject(ctx, r, schema, "frozen"); e != nil {
		return f, e
	}
	normalized, e := json.Marshal(r)
	if e != nil {
		return f, e
	}
	if e = json.Unmarshal(normalized, &f); e != nil {
		return f, e
	}
	if e = validateFrozenRelations(ctx, &f); e != nil {
		return f, e
	}
	return f, nil
}

// ValidateFrozen validates an in-memory input, including defaults in evidence rows.
// Serialized callers must use DecodeFrozen so missing fields are distinguishable.
func ValidateFrozen(f *contracts.Frozen) error {
	return ValidateFrozenContext(context.Background(), f)
}
func ValidateFrozenContext(ctx context.Context, f *contracts.Frozen) error {
	if f == nil {
		return contractError("frozen", "OBJECT_REQUIRED")
	}
	raw, e := json.Marshal(f)
	if e != nil {
		return e
	}
	// Go's zero-value optional strings/slices do not carry JSON presence. Keep
	// this in-memory convenience separate from strict serialized null handling.
	var envelope contracts.Row
	_ = json.Unmarshal(raw, &envelope)
	var input contracts.Row
	_ = json.Unmarshal(envelope["input"], &input)
	for _, k := range []string{"themes", "receipts", "previous_emotions", "funds"} {
		if IsNull(input[k]) {
			delete(input, k)
		}
	}
	if f.Input.HistoryRef == "" {
		input["history_artifact_ref"] = json.RawMessage("null")
	}
	if f.Input.HistoryHash == "" {
		input["history_artifact_hash"] = json.RawMessage("null")
	}
	envelope["input"], _ = json.Marshal(input)
	raw, _ = json.Marshal(envelope)
	next, e := DecodeFrozenContext(ctx, raw)
	if e != nil {
		return e
	}
	*f = next
	return nil
}

// CanonicalInputProjection returns the explicit-default Go input projection.
// Its hash is recorded as a Go projection identity, never as a supplied Python
// source/model hash; accepted JSON number lexemes can preserve extra precision.
func CanonicalInputProjection(ctx context.Context, f contracts.Frozen) ([]byte, error) {
	raw, e := json.Marshal(f.Input)
	if e != nil {
		return nil, e
	}
	var input contracts.Row
	if e = json.Unmarshal(raw, &input); e != nil {
		return nil, e
	}
	for _, k := range []string{"themes", "receipts", "previous_emotions", "funds"} {
		if IsNull(input[k]) {
			delete(input, k)
		}
	}
	if f.Input.HistoryRef == "" {
		input["history_artifact_ref"] = json.RawMessage("null")
	}
	if f.Input.HistoryHash == "" {
		input["history_artifact_hash"] = json.RawMessage("null")
	}
	if e = validateObject(ctx, input, contractSchemas["market"], "input"); e != nil {
		return nil, e
	}
	raw, e = json.Marshal(input)
	if e != nil {
		return nil, e
	}
	return contracts.CanonicalContext(ctx, raw)
}
func validateFrozenRelations(ctx context.Context, f *contracts.Frozen) error {
	in := f.Input
	if len(in.Sessions) == 0 || !contracts.Contains(in.Sessions, in.Observation) {
		return contracts.ErrCode("OBSERVATION_SESSION_REQUIRED")
	}
	for i, s := range in.Sessions {
		if i > 0 && s <= in.Sessions[i-1] {
			return contracts.ErrCode("CALENDAR_NOT_SORTED_UNIQUE")
		}
	}
	if (in.HistoryRef == "") != (in.HistoryHash == "") {
		return contracts.ErrCode("HISTORY_REFERENCE_HASH_PAIR_REQUIRED")
	}
	if in.HistoryRef != "" && in.HistoryRef != "history_"+in.HistoryHash {
		return contracts.ErrCode("HISTORY_REFERENCE_HASH_MISMATCH")
	}
	seen := map[string]bool{}
	for _, r := range in.Bars {
		if e := ctx.Err(); e != nil {
			return e
		}
		c, e := clocks(r)
		if e != nil {
			return e
		}
		key := r.Text("instrument_id") + "\x00" + r.Text("session") + "\x00" + c.Ingested.UTC().Format(time.RFC3339Nano)
		if seen[key] {
			return contracts.ErrCode("DUPLICATE_BAR_REVISION")
		}
		seen[key] = true
	}
	return nil
}

// ValidateEvidenceRow applies the versioned ingestion contract to an isolated
// record. The returned projection may add defaults; the original row is untouched.
func ValidateEvidenceRow(ctx context.Context, kind string, r contracts.Row) (contracts.Row, error) {
	raw, e := json.Marshal(r)
	if e != nil {
		return nil, e
	}
	return DecodeEvidenceRow(ctx, kind, raw)
}

// DecodeEvidenceRow is the strict, cancellable raw-record boundary used by
// history and import adapters after authenticating the storage byte hash.
func DecodeEvidenceRow(ctx context.Context, kind string, raw []byte) (contracts.Row, error) {
	return DecodeEvidenceRowWithProjection(ctx, kind, raw, nil)
}

// DecodeEvidenceRowWithProjection reuses the decoded raw fields for storage
// projection checks before validation inserts defaults or normalizes values.
// check must not mutate the row; errors prevent the evidence from being consumed.
func DecodeEvidenceRowWithProjection(ctx context.Context, kind string, raw []byte, check func(contracts.Row) error) (contracts.Row, error) {
	if _, e := contracts.CanonicalContext(ctx, raw); e != nil {
		return nil, e
	}
	r, e := decodeObject(raw, kind)
	if e != nil {
		return nil, e
	}
	if check != nil {
		if e = check(r); e != nil {
			return nil, e
		}
	}
	schema, ok := contractSchemas[kind]
	if !ok {
		return nil, contractError(kind, "UNKNOWN_ROW_KIND")
	}
	if e = validateObject(ctx, r, schema, kind); e != nil {
		return nil, e
	}
	if kind == "proof" && !proofValid(r, r.Text("applies_from")) {
		return nil, contractError(kind, "INVALID_HISTORICAL_PROOF")
	}
	return r, nil
}
