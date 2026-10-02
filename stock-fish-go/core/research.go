package core

import (
	"context"
	"encoding/json"
	"sort"
)

type Input struct {
	Coverage    string   `json:"coverage_type"`
	Observation string   `json:"observation_date"`
	HistoryRef  string   `json:"history_artifact_ref"`
	HistoryHash string   `json:"history_artifact_hash"`
	Sessions    []string `json:"sessions"`
	Bars        []Row    `json:"bars"`
	Receipts    []Row    `json:"receipts"`
	Universe    Row      `json:"universe"`
	Themes      []Row    `json:"themes"`
	Funds       []Row    `json:"funds"`
}
type Frozen struct {
	Input  Input  `json:"input"`
	Cutoff string `json:"cutoff"`
}
type Qualification struct {
	Eligible         []string          `json:"eligible_ids"`
	Excluded         map[string]string `json:"known_excluded"`
	Unknown          map[string]string `json:"eligibility_unknown"`
	Absent           int               `json:"identity_absent"`
	UniverseComplete bool              `json:"universe_complete"`
}

func Qualify(state Row, observation string, sessions []string) string {
	if state == nil {
		return "UNKNOWN_IDENTITY_OR_CONFLICT"
	}
	for _, k := range []string{"exchange", "board", "security_type"} {
		if state.Text(k) == "UNKNOWN" || state.Text(k) == "" {
			return "UNKNOWN_IDENTITY"
		}
	}
	if !contains([]string{"SSE", "SZSE"}, state.Text("exchange")) || state.Text("board") != "MAIN" || state.Text("security_type") != "A_COMMON" {
		return "OUTSIDE_MAIN_BOARD_A_COMMON"
	}
	for _, k := range []string{"st", "suspended", "delisting_period"} {
		if state.Bool(k) == nil {
			return "UNKNOWN_HISTORICAL_STATUS"
		}
	}
	if *state.Bool("delisting_period") || (state.Text("delisted_on") != "" && state.Text("delisted_on") <= observation) {
		return "DELISTING_OR_DELISTED"
	}
	if *state.Bool("st") {
		return "ST_POOL_DISABLED"
	}
	if *state.Bool("suspended") {
		return "SUSPENDED_CURRENT_SESSION"
	}
	age := 0
	for _, s := range sessions {
		if state.Text("listed_on") <= s && s <= observation {
			age++
		}
	}
	if age < 60 {
		if len(sessions) > 0 && state.Text("listed_on") < sessions[0] {
			return "UNKNOWN_LISTING_HISTORY"
		}
		return "INSUFFICIENT_LISTING_SESSIONS"
	}
	return ""
}

type StockFacts struct {
	ID                     string `json:"instrument_id"`
	Name                   string `json:"name"`
	Facts                  Facts  `json:"facts"`
	Amount                 Number `json:"amount_cny"`
	CurrentMA20, PriorMA20 Number
	Recent3, Previous3     Number
	EvidenceHashes         []string       `json:"selected_evidence_hashes"`
	Rejected               map[string]int `json:"rejected_evidence_counts"`
}
type Diagnostic struct {
	DetailFile            string            `json:"detail_file"`
	DetailCount           int               `json:"detail_count"`
	Contract              string            `json:"contract"`
	RunStatus             string            `json:"run_status"`
	CandidateAvailability string            `json:"candidate_availability"`
	Blockers              []string          `json:"implementation_blockers"`
	Observation           string            `json:"observation_date"`
	Cutoff                string            `json:"cutoff"`
	InputBytesHash        string            `json:"input_bytes_hash"`
	HistoryHash           string            `json:"history_hash"`
	Qualification         Qualification     `json:"qualification"`
	Benchmarks            map[int]Benchmark `json:"benchmarks"`
	Decisions             []Decision        `json:"diagnostic_decisions"`
	Facts                 []StockFacts      `json:"diagnostic_facts"`
	Metrics               Metrics           `json:"metrics"`
}

// Analyze computes an independently sourced migration diagnostic. It deliberately
// does not certify a R1 research result until background/source-model parity is
// completed. The absence of themes/funds does not bypass that acceptance gate.
func Analyze(inputPath, dbPath string, b *Budget) (Diagnostic, error) {
	out := Diagnostic{Contract: "GO_R1_MIGRATION_DIAGNOSTIC_V1", RunStatus: "INCOMPLETE_IMPLEMENTATION", CandidateAvailability: "UNAVAILABLE", Blockers: []string{"BACKGROUND_MARKET_SECTOR_FUNDS_SUPPLY_NOT_FULLY_MIGRATED", "PYDANTIC_DEFAULT_AND_DATETIME_CANONICALIZATION_NOT_EQUIVALENT", "FULL_REASON_AND_EVIDENCE_DIFFERENTIAL_NOT_COMPLETED"}, Benchmarks: map[int]Benchmark{}}
	raw, e := ReadBounded(inputPath, 64<<20, b)
	if e != nil {
		return out, e
	}
	if e = b.Read(int64(len(raw))); e != nil {
		return out, e
	}
	out.InputBytesHash = Hash(raw)
	if _, e = Canonical(raw); e != nil {
		return out, e
	}
	if e = b.Check(); e != nil {
		return out, e
	}
	var frozen Frozen
	if e = json.Unmarshal(raw, &frozen); e != nil {
		return out, e
	}
	input := frozen.Input
	out.Observation = input.Observation
	out.Cutoff = frozen.Cutoff
	out.HistoryHash = input.HistoryHash
	if len(input.Sessions) == 0 || !contains(input.Sessions, input.Observation) {
		return out, errCode("OBSERVATION_SESSION_REQUIRED")
	}
	sessions := []string{}
	for i, s := range input.Sessions {
		if i > 0 && s <= input.Sessions[i-1] {
			return out, errCode("CALENDAR_NOT_SORTED_UNIQUE")
		}
		if s <= input.Observation {
			sessions = append(sessions, s)
		}
	}
	receipts := input.Receipts
	var history *History
	if input.HistoryRef != "" {
		b.Stage("history_index_validation")
		history, e = OpenHistory(dbPath, input.HistoryRef, input.HistoryHash, b)
		if e != nil {
			return out, e
		}
		defer history.Close()
		if e = history.VerifyIndex(); e != nil {
			return out, e
		}
		old := map[string]string{}
		for _, r := range history.Manifest.Receipts {
			old[r.Text("receipt_id")] = RowHash(r)
		}
		for _, r := range receipts {
			if h, ok := old[r.Text("receipt_id")]; ok && h != RowHash(r) {
				return out, errCode("HISTORY_RECEIPT_IDENTITY_CONFLICT")
			}
		}
		receipts = append(append([]Row{}, history.Manifest.Receipts...), receipts...)
	}
	resolver, e := NewResolver(receipts, frozen.Cutoff, input.Coverage, input.Observation, input.Sessions)
	if e != nil {
		return out, e
	}
	b.Stage("qualification")
	var securities []Row
	if e = json.Unmarshal(input.Universe["securities"], &securities); e != nil {
		return out, e
	}
	groups := map[string][]Row{}
	for _, r := range securities {
		if e = b.Check(); e != nil {
			return out, e
		}
		id := r.Text("instrument_id")
		if id == "" {
			return out, errCode("SECURITY_ID_REQUIRED")
		}
		groups[id] = append(groups[id], r)
	}
	q := Qualification{Eligible: []string{}, Excluded: map[string]string{}, Unknown: map[string]string{}}
	states := map[string]Row{}
	ids := []string{}
	for id := range groups {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		visible := []Row{}
		for _, r := range groups[id] {
			if resolver.Resolve(r, Scope{Instrument: id, Session: input.Observation}).Allowed {
				visible = append(visible, r)
			}
		}
		state, er := SelectRevision(visible)
		if er != nil && er.Error() != "CONFLICTING_REVISION" {
			return out, er
		}
		states[id] = state
		reason := Qualify(state, input.Observation, sessions)
		if reason == "" {
			q.Eligible = append(q.Eligible, id)
		} else if len(reason) >= 8 && reason[:8] == "UNKNOWN_" {
			q.Unknown[id] = reason
		} else {
			q.Excluded[id] = reason
		}
	}
	var expected int
	_ = json.Unmarshal(input.Universe["expected_count"], &expected)
	q.Absent = expected - len(groups)
	if q.Absent < 0 {
		q.Absent = 0
	}
	complete := input.Universe.Bool("complete")
	q.UniverseComplete = complete != nil && *complete && len(q.Unknown) == 0 && q.Absent == 0 && resolver.Resolve(input.Universe, Scope{Session: input.Observation, Complete: true}).Allowed
	out.Qualification = q
	b.Stage("history_and_facts")
	recent := map[string][]Row{}
	for _, r := range input.Bars {
		recent[r.Text("instrument_id")] = append(recent[r.Text("instrument_id")], r)
	}
	end := len(sessions) - 1
	// Preserve the original 315-session artifact scope (255 warm-up + 60).
	start := 0
	if history != nil {
		start = end - 314
	}
	if start < 0 {
		start = 0
	}
	for ix, id := range q.Eligible {
		if e = b.Check(); e != nil {
			return out, e
		}
		byDay := map[string][]Row{}
		rejected := map[string]int{}
		consume := func(r Row) error {
			if er := b.Check(); er != nil {
				return er
			}
			if r.Text("quality") == "INVALID" {
				rejected["INVALID_PRICE_QUALITY"]++
				return nil
			}
			resolution := resolver.Resolve(r, Scope{Instrument: id, Session: r.Text("session"), RequireSession: true})
			if !resolution.Allowed {
				rejected[resolution.Reason]++
				return nil
			}
			byDay[r.Text("session")] = append(byDay[r.Text("session")], r)
			return nil
		}
		if history != nil {
			if e = history.Bars(id, sessions[start], input.Observation, consume); e != nil {
				return out, e
			}
		}
		for _, r := range recent[id] {
			if r.Text("session") < sessions[start] || r.Text("session") > input.Observation {
				continue
			}
			if e = consume(r); e != nil {
				return out, e
			}
		}
		w := Window{sessions, map[string]Bar{}}
		hashes := []string{}
		for session, rows := range byDay {
			r, er := SelectRevision(rows)
			if er != nil {
				if er.Error() == "CONFLICTING_REVISION" {
					rejected["CONFLICTING_PRICE_REVISION"]++
					continue
				}
				return out, er
			}
			bar, er := ParseBar(r)
			if er != nil {
				return out, er
			}
			w.Bars[session] = bar
			hashes = append(hashes, bar.Hash)
		}
		sort.Strings(hashes)
		sf, er := buildFacts(b.Context, id, states[id].Text("name"), w, end)
		if er != nil {
			return out, er
		}
		sf.EvidenceHashes = hashes
		sf.Rejected = rejected
		out.Facts = append(out.Facts, sf)
		b.Processed(ix + 1)
	}
	b.Stage("shared_benchmarks")
	previous3 := []Number{}
	prior10 := []Number{}
	for _, sf := range out.Facts {
		previous3 = append(previous3, sf.Previous3)
		prior10 = append(prior10, sf.Facts.PriorReturn10)
	}
	previousMarket := Market(previous3, len(q.Eligible))
	for _, n := range []int{1, 3, 5, 10, 20} {
		v := []Number{}
		for _, sf := range out.Facts {
			if e = b.Check(); e != nil {
				return out, e
			}
			v = append(v, sf.Facts.Returns[n])
		}
		out.Benchmarks[n] = Market(v, len(q.Eligible))
	}
	sectorRS, e := SectorRS(b.Context, input.Themes, resolver, out.Facts)
	if e != nil {
		return out, e
	}
	b.Stage("r1_diagnostic_judgment")
	for ix := range out.Facts {
		if e = b.Check(); e != nil {
			return out, e
		}
		sf := &out.Facts[ix]
		f := &sf.Facts
		f.LegacyRS = map[int]Number{}
		f.SectorRS1 = sectorRS[sf.ID]
		for _, n := range []int{1, 3, 5, 10} {
			rs := f.Returns[n].Sub(out.Benchmarks[n].Value)
			f.RS[n] = rs
			f.LegacyRS[n] = rs.Mul(N("100")).Rounded(4).Div(N("100"))
		}
		f.PriorPercentile10 = Percentile(f.PriorReturn10, prior10)
		f.Momentum = Momentum(sf.Recent3, sf.Previous3, sf.Recent3.Sub(out.Benchmarks[3].Value), sf.Previous3.Sub(previousMarket.Value))
		d, er := Decide(sf.ID, sf.Amount, f.Path.CloseLimitDown, Identify(*f), MA20Trend(f.Close, sf.CurrentMA20, sf.PriorMA20), f.Trend.Condition, f.Trend.State, WeakToStrong(f.Path), nil)
		if er != nil {
			return out, er
		}
		out.Decisions = append(out.Decisions, d)
	}
	out.Metrics = b.Snapshot()
	return out, b.Check()
}
func appendUnique(a []string, s string) []string {
	if !contains(a, s) {
		return append(a, s)
	}
	return a
}
func buildFacts(ctx context.Context, id, name string, w Window, end int) (StockFacts, error) {
	sf := StockFacts{ID: id, Name: name, Facts: Facts{Returns: map[int]Number{}, RS: map[int]Number{}}}
	f := &sf.Facts
	for _, n := range []int{1, 3, 5, 10, 20} {
		f.Returns[n] = w.Return(end, n)
	}
	f.PriorReturn5 = w.Return(end-1, 5)
	f.PriorReturn10 = w.Return(end-1, 10)
	f.PriorHigh = w.PriorHigh(end, 5)
	f.PriorTurnover = w.HighTurnover(end-1, 5)
	f.RecentTurnover = w.HighTurnover(end, 5)
	sf.Recent3 = w.Return(end, 3)
	sf.Previous3 = w.Return(end-3, 3)
	bar := w.At(end)
	if bar != nil {
		f.Close = bar.Close
		sf.Amount = bar.Amount
		f.AmountRatio = bar.Amount.Div(w.Average(end-1, 5, "amount")).Rounded(6)
	}
	f.Path = DailyPath(bar)
	up, _, _ := Limits(w.At(end - 1))
	f.PriorLimitUp = up == "TRUE"
	var e error
	sf.CurrentMA20, e = w.MA(ctx, end, 20)
	if e != nil {
		return sf, e
	}
	sf.PriorMA20, e = w.MA(ctx, end-5, 20)
	if e != nil {
		return sf, e
	}
	f.Trend, e = w.Trend(ctx, end)
	return sf, e
}
