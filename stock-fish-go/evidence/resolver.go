package evidence

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"sort"
	"strings"
	"time"
)

type Clocks struct{ Event, Published, Seen, Ingested time.Time }

func clocks(r contracts.Row) (Clocks, error) {
	v := []time.Time{}
	for _, k := range []string{"event_time", "source_published_at", "first_seen_at", "ingested_at"} {
		t, e := time.Parse(time.RFC3339Nano, r.Text(k))
		if e != nil {
			return Clocks{}, contracts.ErrCode("EXPLICIT_TIMEZONE_REQUIRED")
		}
		v = append(v, t)
	}
	if v[2].After(v[3]) {
		return Clocks{}, contracts.ErrCode("INVALID_CLOCK_ORDER")
	}
	return Clocks{v[0], v[1], v[2], v[3]}, nil
}

var sh = time.FixedZone("Asia/Shanghai", 8*3600)

func day(t time.Time) string { return t.In(sh).Format("2006-01-02") }

type Resolver struct {
	Cutoff                time.Time
	Coverage, Observation string
	Sessions              map[string]bool
	Receipts              map[string]contracts.Row
	Conflicts             map[string]bool
	// Normal research inputs are immutable and decode each parent only once.
	// Raw-byte comparisons also preserve the legacy mutable Row API: changing
	// a relevant receipt field invalidates its cached clocks and scope.
	receiptEvidence map[string]receiptEvidence
}

type receiptEvidence struct {
	clocks                                              Clocks
	clocksInvalid                                       bool
	status, sourceType, fieldVersion, definitionVersion string
	complete                                            bool
	instruments, sessions                               map[string]bool
	proof                                               historicalProof
	raw                                                 []string
}

var receiptFields = []string{"event_time", "source_published_at", "first_seen_at", "ingested_at", "status", "source_type", "field_version", "definition_version", "pagination_complete", "instrument_ids", "business_sessions", "historical_time_proof"}

func (r *Resolver) receipt(id string, row contracts.Row) receiptEvidence {
	p, exists := r.receiptEvidence[id]
	if exists {
		for i, field := range receiptFields {
			if string(row[field]) != p.raw[i] {
				exists = false
				break
			}
		}
	}
	if !exists {
		p = decodeReceipt(row)
		if r.receiptEvidence == nil {
			r.receiptEvidence = map[string]receiptEvidence{}
		}
		r.receiptEvidence[id] = p
	}
	return p
}

type historicalProof struct {
	valid    bool
	from, to string
}

func (p historicalProof) validAt(business string) bool {
	return p.valid && p.from <= business && business <= p.to
}

func receiptSet(values []string) map[string]bool {
	out := make(map[string]bool, len(values))
	for _, value := range values {
		out[value] = true
	}
	return out
}

func decodeReceipt(row contracts.Row) receiptEvidence {
	c, err := clocks(row)
	complete := row.Bool("pagination_complete")
	var proof contracts.Row
	_ = json.Unmarshal(row["historical_time_proof"], &proof)
	raw := make([]string, len(receiptFields))
	for i, field := range receiptFields {
		raw[i] = string(row[field])
	}
	return receiptEvidence{clocks: c, clocksInvalid: err != nil, status: row.Text("status"), sourceType: row.Text("source_type"), fieldVersion: row.Text("field_version"), definitionVersion: row.Text("definition_version"), complete: complete != nil && *complete, instruments: receiptSet(row.Strings("instrument_ids")), sessions: receiptSet(row.Strings("business_sessions")), proof: decodeHistoricalProof(proof), raw: raw}
}

type Scope struct {
	Instrument, Session, Definition string
	Types                           []string
	Complete, RequireSession        bool
	// Fields limits a PARTIAL receipt to present, non-null observations. It
	// never authorizes completeness, missing-field absence, or a zero count.
	Fields []string
}
type Resolution struct {
	Allowed       bool     `json:"allowed"`
	Reason        string   `json:"reason"`
	Sources       []string `json:"source_ids"`
	Reconstructed bool     `json:"reconstruction_used"`
}

func NewResolver(receipts []contracts.Row, cutoff, coverage, observation string, sessions []string) (*Resolver, error) {
	return NewResolverContext(context.Background(), receipts, cutoff, coverage, observation, sessions)
}

func NewResolverContext(ctx context.Context, receipts []contracts.Row, cutoff, coverage, observation string, sessions []string) (*Resolver, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	t, e := time.Parse(time.RFC3339Nano, cutoff)
	if e != nil {
		return nil, e
	}
	r := &Resolver{Cutoff: t, Coverage: coverage, Observation: observation, Sessions: map[string]bool{}, Receipts: map[string]contracts.Row{}, Conflicts: map[string]bool{}, receiptEvidence: map[string]receiptEvidence{}}
	for _, s := range sessions {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if _, e := time.Parse("2006-01-02", s); e != nil {
			return nil, e
		}
		r.Sessions[s] = true
	}
	for _, v := range receipts {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		id := v.Text("receipt_id")
		if id == "" {
			return nil, contracts.ErrCode("RECEIPT_ID_REQUIRED")
		}
		if p, ok := r.Receipts[id]; ok && contracts.RowHash(p) != contracts.RowHash(v) {
			r.Conflicts[id] = true
		}
		r.Receipts[id] = v
		r.receiptEvidence[id] = decodeReceipt(v)
	}
	return r, ctx.Err()
}
func proofValid(p contracts.Row, business string) bool {
	return decodeHistoricalProof(p).validAt(business)
}
func decodeHistoricalProof(p contracts.Row) historicalProof {
	h := p.Text("evidence_hash")
	_, e := hex.DecodeString(h)
	if len(h) != 64 || e != nil || strings.ToLower(h) != h {
		return historicalProof{}
	}
	if p.Text("policy_id") == "" || (!strings.HasPrefix(p.Text("source_url"), "https://") && !strings.HasPrefix(p.Text("source_url"), "http://")) {
		return historicalProof{}
	}
	if !contracts.Contains([]string{"SOURCE_DATED_EVENT_RECORD", "SOURCE_VERIFIED_EVENT_TIMESTAMP"}, p.Text("event_time_basis")) || !contracts.Contains([]string{"SOURCE_PUBLICATION_TIMESTAMP", "SOURCE_DOCUMENTED_RELEASE_RULE"}, p.Text("publication_time_basis")) {
		return historicalProof{}
	}
	if _, e := time.Parse(time.RFC3339Nano, p.Text("verified_at")); e != nil {
		return historicalProof{}
	}
	for _, k := range []string{"applies_from", "applies_to"} {
		if _, e := time.Parse("2006-01-02", p.Text(k)); e != nil {
			return historicalProof{}
		}
	}
	refs := p.Strings("evidence_refs")
	if len(refs) == 0 {
		return historicalProof{}
	}
	for _, v := range refs {
		if v == "" {
			return historicalProof{}
		}
	}
	return historicalProof{true, p.Text("applies_from"), p.Text("applies_to")}
}
func (r *Resolver) clock(c Clocks, proofs []historicalProof, business string, prior bool) (string, bool) {
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
		if !p.validAt(business) {
			return "HISTORICAL_TIME_PROOF_INVALID_OR_OUTSIDE_SCOPE", false
		}
	}
	return "", true
}
func (r *Resolver) Resolve(row contracts.Row, s Scope) Resolution {
	reject := func(c string) Resolution { return Resolution{Reason: c} }
	if !contracts.Contains([]string{"PROSPECTIVE_CAPTURED", "RECONSTRUCTED_HISTORICAL", "TEST_FIXTURE"}, r.Coverage) {
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
	for _, k := range []string{"session", "effective_from", "effective_to"} {
		if raw, ok := row[k]; ok && !IsNull(raw) {
			if _, e := time.Parse("2006-01-02", row.Text(k)); e != nil {
				return reject("INVALID_EVIDENCE_ENVELOPE")
			}
		}
	}
	if observed != "" {
		if _, e := time.Parse("2006-01-02", observed); e != nil {
			return reject("INVALID_EVIDENCE_ENVELOPE")
		}
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
	var refs []string
	if e := json.Unmarshal(row["source_refs"], &refs); e != nil {
		return reject("SOURCE_REFERENCE_REQUIRED")
	}
	if len(refs) == 0 {
		return reject("SOURCE_REFERENCE_REQUIRED")
	}
	for _, field := range s.Fields {
		if raw, ok := row[field]; !ok || IsNull(raw) {
			return reject("REQUESTED_FIELD_UNAVAILABLE")
		}
	}
	proofs := []historicalProof{}
	late := false
	for _, id := range refs {
		parent, ok := r.Receipts[id]
		if !ok {
			return reject("SOURCE_REFERENCE_UNRESOLVED")
		}
		if r.Conflicts[id] {
			return reject("SOURCE_REFERENCE_CONFLICT")
		}
		p := r.receipt(id, parent)
		status := p.status
		if status != "COMPLETE" && status != "PARTIAL" {
			return reject("SOURCE_STATUS_" + status)
		}
		if s.Complete && (status != "COMPLETE" || !p.complete) {
			return reject("SOURCE_NOT_COMPLETE")
		}
		pc := p.clocks
		if p.clocksInvalid {
			return reject("EVENT_OR_PUBLICATION_NOT_VISIBLE")
		}
		proof := p.proof
		reason, l := r.clock(pc, []historicalProof{proof}, observed, false)
		if reason != "" {
			return reject(reason)
		}
		late = late || l
		if len(s.Types) > 0 && !contracts.Contains(s.Types, p.sourceType) {
			return reject("SOURCE_TYPE_MISMATCH")
		}
		if s.Definition != "" && (p.fieldVersion != s.Definition || p.definitionVersion != s.Definition) {
			return reject("SOURCE_DEFINITION_VERSION_MISMATCH")
		}
		if s.Instrument != "" && len(p.instruments) > 0 && !p.instruments[s.Instrument] {
			return reject("SOURCE_INSTRUMENT_SCOPE_MISMATCH")
		}
		if observed != "" && len(p.sessions) > 0 && !p.sessions[observed] {
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
	unique := map[string]bool{}
	for _, ref := range refs {
		unique[ref] = true
	}
	sources := make([]string, 0, len(unique))
	for ref := range unique {
		sources = append(sources, ref)
	}
	sort.Strings(sources)
	return Resolution{true, "", sources, late || l}
}
func SelectRevision(rows []contracts.Row) (contracts.Row, error) {
	index, e := SelectRevisionIndex(rows)
	if e != nil || index < 0 {
		return nil, e
	}
	return rows[index], nil
}

// SelectRevisionIndex preserves the selected row's separately held provenance.
func SelectRevisionIndex(rows []contracts.Row) (int, error) {
	var chosen contracts.Row
	index := -1
	var best Clocks
	conflict := false
	for i, r := range rows {
		c, e := clocks(r)
		if e != nil {
			return -1, e
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
			index = i
			best = c
			conflict = false
		} else if cmp == 0 && contracts.RowHash(chosen) != contracts.RowHash(r) {
			conflict = true
		}
	}
	if conflict {
		return -1, contracts.ErrCode("CONFLICTING_REVISION")
	}
	return index, nil
}
