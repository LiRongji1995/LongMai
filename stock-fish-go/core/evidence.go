package core

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"
)

// Row is confined to the raw evidence boundary. Strategy facts are typed.
type Row map[string]json.RawMessage

func (r Row) Text(k string) string      { var v string; _ = json.Unmarshal(r[k], &v); return v }
func (r Row) Bool(k string) *bool       { var v *bool; _ = json.Unmarshal(r[k], &v); return v }
func (r Row) Strings(k string) []string { var v []string; _ = json.Unmarshal(r[k], &v); return v }
func (r Row) Num(k string) Number       { var v Number; _ = json.Unmarshal(r[k], &v); return v }
func Hash(b []byte) string              { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }

// Canonical preserves numeric lexemes and Unicode and rejects duplicate keys.
// It does not insert Pydantic defaults or normalize datetime strings.
func Canonical(raw []byte) ([]byte, error) {
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	v, e := jsonValue(d)
	if e != nil {
		return nil, e
	}
	if _, e = d.Token(); e != io.EOF {
		return nil, errCode("TRAILING_JSON")
	}
	var b bytes.Buffer
	e = encodeCanonical(&b, v)
	return b.Bytes(), e
}
func jsonValue(d *json.Decoder) (any, error) {
	t, e := d.Token()
	if e != nil {
		return nil, e
	}
	switch v := t.(type) {
	case json.Delim:
		if v == '{' {
			m := map[string]any{}
			for d.More() {
				t, e := d.Token()
				if e != nil {
					return nil, e
				}
				k, ok := t.(string)
				if !ok {
					return nil, errCode("INVALID_JSON_KEY")
				}
				if _, ok = m[k]; ok {
					return nil, errCode("DUPLICATE_JSON_KEY")
				}
				m[k], e = jsonValue(d)
				if e != nil {
					return nil, e
				}
			}
			_, e = d.Token()
			return m, e
		}
		if v == '[' {
			a := []any{}
			for d.More() {
				x, e := jsonValue(d)
				if e != nil {
					return nil, e
				}
				a = append(a, x)
			}
			_, e = d.Token()
			return a, e
		}
		return nil, errCode("INVALID_JSON")
	default:
		return t, nil
	}
}
func encodeCanonical(b *bytes.Buffer, v any) error {
	switch x := v.(type) {
	case map[string]any:
		keys := []string{}
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		b.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				b.WriteByte(',')
			}
			writeString(b, k)
			b.WriteByte(':')
			if e := encodeCanonical(b, x[k]); e != nil {
				return e
			}
		}
		b.WriteByte('}')
	case []any:
		b.WriteByte('[')
		for i, v := range x {
			if i > 0 {
				b.WriteByte(',')
			}
			if e := encodeCanonical(b, v); e != nil {
				return e
			}
		}
		b.WriteByte(']')
	case string:
		writeString(b, x)
	case json.Number:
		b.WriteString(x.String())
	case bool:
		if x {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case nil:
		b.WriteString("null")
	default:
		return errCode("INVALID_CANONICAL_TYPE")
	}
	return nil
}
func writeString(b *bytes.Buffer, s string) {
	b.WriteByte('"')
	for _, c := range s {
		switch c {
		case '"', '\\':
			b.WriteByte('\\')
			b.WriteRune(c)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		default:
			if c < 32 {
				fmt.Fprintf(b, `\u%04x`, c)
			} else {
				b.WriteRune(c)
			}
		}
	}
	b.WriteByte('"')
}
func RowHash(r Row) string {
	b, _ := json.Marshal(r)
	c, e := Canonical(b)
	if e != nil {
		return ""
	}
	return Hash(c)
}

type Clocks struct{ Event, Published, Seen, Ingested time.Time }

func clocks(r Row) (Clocks, error) {
	v := []time.Time{}
	for _, k := range []string{"event_time", "source_published_at", "first_seen_at", "ingested_at"} {
		t, e := time.Parse(time.RFC3339Nano, r.Text(k))
		if e != nil {
			return Clocks{}, errCode("EXPLICIT_TIMEZONE_REQUIRED")
		}
		v = append(v, t)
	}
	if v[2].After(v[3]) {
		return Clocks{}, errCode("INVALID_CLOCK_ORDER")
	}
	return Clocks{v[0], v[1], v[2], v[3]}, nil
}

var sh = time.FixedZone("Asia/Shanghai", 8*3600)

func day(t time.Time) string { return t.In(sh).Format("2006-01-02") }
func contains(a []string, v string) bool {
	for _, s := range a {
		if s == v {
			return true
		}
	}
	return false
}

type Resolver struct {
	Cutoff                time.Time
	Coverage, Observation string
	Sessions              map[string]bool
	Receipts              map[string]Row
	Conflicts             map[string]bool
}
type Scope struct {
	Instrument, Session, Definition string
	Types                           []string
	Complete, RequireSession        bool
}
type Resolution struct {
	Allowed       bool     `json:"allowed"`
	Reason        string   `json:"reason"`
	Sources       []string `json:"source_ids"`
	Reconstructed bool     `json:"reconstruction_used"`
}

func NewResolver(receipts []Row, cutoff, coverage, observation string, sessions []string) (*Resolver, error) {
	t, e := time.Parse(time.RFC3339Nano, cutoff)
	if e != nil {
		return nil, e
	}
	r := &Resolver{t, coverage, observation, map[string]bool{}, map[string]Row{}, map[string]bool{}}
	for _, s := range sessions {
		if _, e := time.Parse("2006-01-02", s); e != nil {
			return nil, e
		}
		r.Sessions[s] = true
	}
	for _, v := range receipts {
		id := v.Text("receipt_id")
		if id == "" {
			return nil, errCode("RECEIPT_ID_REQUIRED")
		}
		if p, ok := r.Receipts[id]; ok && RowHash(p) != RowHash(v) {
			r.Conflicts[id] = true
		}
		r.Receipts[id] = v
	}
	return r, nil
}
func proofValid(p Row, business string) bool {
	h := p.Text("evidence_hash")
	_, e := hex.DecodeString(h)
	if len(h) != 64 || e != nil || strings.ToLower(h) != h {
		return false
	}
	if p.Text("policy_id") == "" || (!strings.HasPrefix(p.Text("source_url"), "https://") && !strings.HasPrefix(p.Text("source_url"), "http://")) {
		return false
	}
	if !contains([]string{"SOURCE_DATED_EVENT_RECORD", "SOURCE_VERIFIED_EVENT_TIMESTAMP"}, p.Text("event_time_basis")) || !contains([]string{"SOURCE_PUBLICATION_TIMESTAMP", "SOURCE_DOCUMENTED_RELEASE_RULE"}, p.Text("publication_time_basis")) {
		return false
	}
	if _, e := time.Parse(time.RFC3339Nano, p.Text("verified_at")); e != nil {
		return false
	}
	for _, k := range []string{"applies_from", "applies_to"} {
		if _, e := time.Parse("2006-01-02", p.Text(k)); e != nil {
			return false
		}
	}
	refs := p.Strings("evidence_refs")
	if len(refs) == 0 {
		return false
	}
	for _, v := range refs {
		if v == "" {
			return false
		}
	}
	return p.Text("applies_from") <= business && business <= p.Text("applies_to")
}
func (r *Resolver) clock(c Clocks, proofs []Row, business string, prior bool) (string, bool) {
	if c.Event.After(r.Cutoff) || c.Published.After(r.Cutoff) {
		return "EVENT_OR_PUBLICATION_NOT_VISIBLE", false
	}
	if !c.Seen.After(r.Cutoff) && !c.Ingested.After(r.Cutoff) {
		return "", false
	}
	if r.Coverage != "RECONSTRUCTED_HISTORICAL" {
		return "CAPTURE_AFTER_CUTOFF", false
	}
	if prior {
		return "", true
	}
	if len(proofs) == 0 {
		return "HISTORICAL_SOURCE_TIME_PROOF_REQUIRED", false
	}
	if business == "" {
		business = day(c.Event)
	}
	for _, p := range proofs {
		if !proofValid(p, business) {
			return "HISTORICAL_TIME_PROOF_INVALID_OR_OUTSIDE_SCOPE", false
		}
	}
	return "", true
}
func (r *Resolver) Resolve(row Row, s Scope) Resolution {
	reject := func(c string) Resolution { return Resolution{Reason: c} }
	if !contains([]string{"PROSPECTIVE_CAPTURED", "RECONSTRUCTED_HISTORICAL", "TEST_FIXTURE"}, r.Coverage) {
		return reject("UNVERIFIABLE_AS_OF")
	}
	c, e := clocks(row)
	if e != nil {
		return reject("EVENT_OR_PUBLICATION_NOT_VISIBLE")
	}
	if c.Event.After(r.Cutoff) || c.Published.After(r.Cutoff) {
		return reject("EVENT_OR_PUBLICATION_NOT_VISIBLE")
	}
	if s.Instrument != "" && row.Text("instrument_id") != s.Instrument {
		return reject("INSTRUMENT_ID_MISMATCH")
	}
	observed := s.Session
	if observed == "" {
		observed = row.Text("session")
	}
	if s.Session != "" && row.Text("session") != "" && row.Text("session") != s.Session {
		return reject("BUSINESS_SESSION_MISMATCH")
	}
	if observed != "" && observed > day(r.Cutoff) {
		return reject("FUTURE_BUSINESS_SESSION")
	}
	if observed != "" && row.Text("session") != "" && (day(c.Event) < observed || day(c.Published) < observed) {
		return reject("EVENT_OR_PUBLICATION_PRECEDES_BUSINESS_SESSION")
	}
	if s.RequireSession {
		if observed == "" || row.Text("session") == "" {
			return reject("BUSINESS_SESSION_REQUIRED")
		}
		if !r.Sessions[observed] {
			return reject("BUSINESS_SESSION_NOT_IN_EXCHANGE_CALENDAR")
		}
		if r.Observation != "" && observed > r.Observation {
			return reject("FUTURE_BUSINESS_SESSION")
		}
	}
	if observed != "" && row.Text("effective_from") != "" && (row.Text("effective_from") > observed || (row.Text("effective_to") != "" && observed >= row.Text("effective_to"))) {
		return reject("OUTSIDE_EFFECTIVE_INTERVAL")
	}
	refs := row.Strings("source_refs")
	if len(refs) == 0 {
		return reject("SOURCE_REFERENCE_REQUIRED")
	}
	proofs := []Row{}
	late := false
	for _, id := range refs {
		p, ok := r.Receipts[id]
		if !ok {
			return reject("SOURCE_REFERENCE_UNRESOLVED")
		}
		if r.Conflicts[id] {
			return reject("SOURCE_REFERENCE_CONFLICT")
		}
		status := p.Text("status")
		if status != "COMPLETE" && status != "PARTIAL" {
			return reject("SOURCE_STATUS_" + status)
		}
		if s.Complete && (status != "COMPLETE" || p.Bool("pagination_complete") == nil || !*p.Bool("pagination_complete")) {
			return reject("SOURCE_NOT_COMPLETE")
		}
		pc, e := clocks(p)
		if e != nil {
			return reject("EVENT_OR_PUBLICATION_NOT_VISIBLE")
		}
		var proof Row
		_ = json.Unmarshal(p["historical_time_proof"], &proof)
		reason, l := r.clock(pc, []Row{proof}, observed, false)
		if reason != "" {
			return reject(reason)
		}
		late = late || l
		if len(s.Types) > 0 && !contains(s.Types, p.Text("source_type")) {
			return reject("SOURCE_TYPE_MISMATCH")
		}
		if s.Definition != "" && (p.Text("field_version") != s.Definition || p.Text("definition_version") != s.Definition) {
			return reject("SOURCE_DEFINITION_VERSION_MISMATCH")
		}
		if ids := p.Strings("instrument_ids"); s.Instrument != "" && len(ids) > 0 && !contains(ids, s.Instrument) {
			return reject("SOURCE_INSTRUMENT_SCOPE_MISMATCH")
		}
		if ds := p.Strings("business_sessions"); observed != "" && len(ds) > 0 && !contains(ds, observed) {
			return reject("SOURCE_SESSION_SCOPE_MISMATCH")
		}
		if c.Seen.Before(pc.Seen) || c.Ingested.Before(pc.Ingested) {
			return reject("ROW_CAPTURE_PRECEDES_PARENT_RECEIPT")
		}
		if pc.Seen.After(r.Cutoff) || pc.Ingested.After(r.Cutoff) {
			proofs = append(proofs, proof)
		}
	}
	reason, l := r.clock(c, proofs, observed, len(proofs) == 0)
	if reason != "" {
		return reject(reason)
	}
	return Resolution{true, "", refs, late || l}
}
func SelectRevision(rows []Row) (Row, error) {
	var chosen Row
	var best Clocks
	conflict := false
	for _, r := range rows {
		c, e := clocks(r)
		if e != nil {
			return nil, e
		}
		cmp := 1
		if chosen != nil {
			cmp = 0
			for _, p := range [][2]time.Time{{c.Published, best.Published}, {c.Ingested, best.Ingested}, {c.Seen, best.Seen}} {
				if p[0].After(p[1]) {
					cmp = 1
					break
				}
				if p[0].Before(p[1]) {
					cmp = -1
					break
				}
			}
		}
		if cmp > 0 {
			chosen = r
			best = c
			conflict = false
		} else if cmp == 0 && RowHash(chosen) != RowHash(r) {
			conflict = true
		}
	}
	if conflict {
		return nil, errCode("CONFLICTING_REVISION")
	}
	return chosen, nil
}
