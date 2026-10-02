package research

import (
	"encoding/json"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	shared "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/shared"
	"sort"
	"time"
)

type Diagnostic struct {
	StrategyID               string              `json:"strategy_id"`
	ImplementationAcceptance string              `json:"implementation_acceptance"`
	FactCacheHits            int                 `json:"fact_cache_hits"`
	FactCacheMisses          int                 `json:"fact_cache_misses"`
	DetailFile               string              `json:"detail_file"`
	DetailCount              int                 `json:"detail_count"`
	Contract                 string              `json:"contract"`
	RunStatus                string              `json:"run_status"`
	CandidateAvailability    string              `json:"candidate_availability"`
	Blockers                 []string            `json:"implementation_blockers"`
	Observation              string              `json:"observation_date"`
	Cutoff                   string              `json:"cutoff"`
	InputBytesHash           string              `json:"input_bytes_hash"`
	HistoryHash              string              `json:"history_hash"`
	Qualification            Qualification       `json:"qualification"`
	Benchmarks               map[int]Benchmark   `json:"benchmarks"`
	Decisions                []Decision          `json:"diagnostic_decisions"`
	Facts                    []StockFacts        `json:"diagnostic_facts"`
	Coverage                 Coverage            `json:"coverage"`
	V2                       []V2Decision        `json:"v2_decisions,omitempty"`
	MarketContext            MarketContext       `json:"market_context"`
	RejectedThemes           []EvidenceRejection `json:"rejected_themes"`
	Metrics                  Metrics             `json:"metrics"`
}

// AnalyzeFrozen consumes validated evidence using an injected history port.
// Implementation acceptance is independent from a successfully evaluated run.
func AnalyzeFrozen(frozen Frozen, inputBytesHash string, history HistoryReader, b Budget) (Diagnostic, error) {
	return AnalyzeStrategyFrozen(frozen, inputBytesHash, history, b, R1StrategyID, nil)
}
func AnalyzeStrategyFrozen(frozen Frozen, inputBytesHash string, history HistoryReader, b Budget, strategyID string, cache FactsCache) (Diagnostic, error) {
	if strategyID != R1StrategyID && strategyID != V2StrategyID {
		return Diagnostic{}, errCode("UNKNOWN_STRATEGY")
	}

	out := Diagnostic{Contract: "GO_R1_FULL_V2", RunStatus: "RUNNING", CandidateAvailability: "UNAVAILABLE", Blockers: []string{}, Benchmarks: map[int]Benchmark{}}
	if e := ValidateFrozenContext(b.Ctx(), &frozen); e != nil {
		return out, e
	}
	var e error
	input := frozen.Input
	out.StrategyID = strategyID
	out.ImplementationAcceptance = "DIFFERENTIAL_ACCEPTANCE_REQUIRED"
	out.InputBytesHash = inputBytesHash
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
	if input.HistoryRef != "" && history == nil {
		return out, errCode("HISTORY_READER_REQUIRED")
	}
	if history != nil {
		b.Stage("history_index_validation")
		if history.Identity() != input.HistoryHash {
			return out, errCode("HISTORY_IDENTITY_MISMATCH")
		}
		if e = history.VerifyIndex(); e != nil {
			return out, e
		}
		old := map[string]string{}
		for _, r := range history.Receipts() {
			old[r.Text("receipt_id")] = RowHash(r)
		}
		for _, r := range receipts {
			if h, ok := old[r.Text("receipt_id")]; ok && h != RowHash(r) {
				return out, errCode("HISTORY_RECEIPT_IDENTITY_CONFLICT")
			}
		}
		receipts = append(append([]Row{}, history.Receipts()...), receipts...)
	}
	resolver, e := NewResolverContext(b.Ctx(), receipts, frozen.Cutoff, input.Coverage, input.Observation, input.Sessions)
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
	type inputBar struct {
		row   Row
		index int
	}
	recent := map[string][]inputBar{}
	for i, r := range input.Bars {
		recent[r.Text("instrument_id")] = append(recent[r.Text("instrument_id")], inputBar{r, i})
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
		origins := map[string][]c.EvidenceIdentity{}
		seenRaw := map[string]bool{}
		resolutionTime := time.Duration(0)
		rejected := map[string]int{}
		consume := func(r Row, origin c.EvidenceIdentity) error {
			started := time.Now()
			defer func() { resolutionTime += time.Since(started) }()
			if origin.Origin == "HISTORY_ROW" {
				ports.HistoryPayload(b, origin.RawBytes, !seenRaw[origin.RawSHA256])
				seenRaw[origin.RawSHA256] = true
			}
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
			origins[r.Text("session")] = append(origins[r.Text("session")], origin)
			return nil
		}
		if history != nil {
			if reader, ok := history.(ports.HistoryIdentityReader); ok {
				e = reader.BarsWithIdentity(id, sessions[start], input.Observation, consume)
			} else {
				e = history.Bars(id, sessions[start], input.Observation, func(r Row) error { return consume(r, c.EvidenceIdentity{Origin: "UNBOUND_TEST_ADAPTER"}) })
			}
			if e != nil {
				return out, e
			}
		}
		for _, row := range recent[id] {
			r := row.row
			if r.Text("session") < sessions[start] || r.Text("session") > input.Observation {
				continue
			}
			if e = consume(r, c.EvidenceIdentity{Origin: "FROZEN_INPUT", ParentHash: inputBytesHash, Locator: fmt.Sprintf("/input/bars/%d", row.index)}); e != nil {
				return out, e
			}
		}
		w := Window{Sessions: sessions, Bars: map[string]Bar{}}
		hashes := []string{}
		bindings := []c.SelectedEvidenceBinding{}
		ports.Observe(b, "source_visibility_selection", resolutionTime.Seconds())
		revisionStart := time.Now()
		for session, rows := range byDay {
			selected, er := SelectRevisionIndex(rows)
			if er != nil {
				if er.Error() == "CONFLICTING_REVISION" {
					rejected["CONFLICTING_PRICE_REVISION"]++
					continue
				}
				return out, er
			}
			bar, er := ParseBar(rows[selected])
			if er != nil {
				return out, er
			}
			w.Bars[session] = bar
			hashes = append(hashes, bar.Hash)
			bindings = append(bindings, c.SelectedEvidenceBinding{ProjectionHash: bar.Hash, Identity: origins[session][selected]})
		}
		ports.Observe(b, "revision_selection_and_bar_decode", time.Since(revisionStart).Seconds())
		sort.Strings(hashes)
		sort.Slice(bindings, func(i, j int) bool { return bindings[i].ProjectionHash < bindings[j].ProjectionHash })
		cacheIdentity, _ := json.Marshal(map[string]any{"calculation": "SHARED_FACTS_V2_20261002", "input": inputBytesHash, "history": input.HistoryHash, "cutoff": frozen.Cutoff, "observation": input.Observation, "instrument": id, "sessions": sessions, "selected_evidence": hashes, "policy": "FOUR_CLOCK_SCOPE_REVISION_V2"})
		key := Hash(cacheIdentity)
		var sf StockFacts
		hit := false
		if cache != nil {
			sf, hit, e = cache.GetFact(b.Ctx(), key)
			if e != nil {
				return out, e
			}
			if hit && sf.ID != id {
				return out, errCode("FACT_CACHE_IDENTITY_MISMATCH")
			}
		}
		if hit {
			out.FactCacheHits++
		} else {
			out.FactCacheMisses++
			var er error
			started := time.Now()
			sf, er = buildFacts(b.Ctx(), id, states[id].Text("name"), w, end)
			ports.Observe(b, "public_facts_arithmetic", time.Since(started).Seconds())
			if er != nil {
				return out, er
			}
			if cache != nil {
				if e = cache.PutFact(b.Ctx(), key, sf); e != nil {
					return out, e
				}
			}
		}
		sf.EvidenceHashes = hashes
		sf.EvidenceBindings = bindings
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
	visible, rejected, e := visibleThemes(b.Ctx(), input.Themes, resolver)
	if e != nil {
		return out, e
	}
	out.RejectedThemes = rejected
	eligible := map[string]bool{}
	for _, id := range q.Eligible {
		eligible[id] = true
	}
	background, e := facts.NewBackgroundBatch(b.Ctx(), visible, input.Funds, eligible, resolver, sessions)
	if e != nil {
		return out, e
	}
	sectors, e := background.ComputeSectors(b.Ctx(), out.Facts, out.Benchmarks)
	if e != nil {
		return out, e
	}
	sectors = completeSectorCompatibility(sectors)
	out.MarketContext = ComputeMarket(out.Facts)
	priorRanks := NewPercentiles(prior10)
	b.Stage("strategy_judgment")
	for ix := range out.Facts {
		if e = b.Check(); e != nil {
			return out, e
		}
		sf := &out.Facts[ix]
		f := &sf.Facts
		f.LegacyRS = map[int]Number{}
		for _, sector := range sectors[sf.ID] {
			if sector.Status == Pass {
				v := sector.Windows[1].RS
				if v.Known() && (!f.SectorRS1.Known() || v.Cmp(f.SectorRS1) > 0) {
					f.SectorRS1 = v
				}
			}
		}
		for _, n := range []int{1, 3, 5, 10} {
			rs := f.Returns[n].Sub(out.Benchmarks[n].Value)
			f.RS[n] = rs
			f.LegacyRS[n] = rs.Mul(N("100")).Rounded(4).Div(N("100"))
		}
		f.PriorPercentile10 = priorRanks.Lookup(f.PriorReturn10)
		f.Momentum = Momentum(sf.Recent3, sf.Previous3, sf.Recent3.Sub(out.Benchmarks[3].Value), sf.Previous3.Sub(previousMarket.Value))
		f.Strength.ReturnRaw = sf.Recent3.Sub(sf.Previous3)
		f.Strength.RSRaw = sf.Recent3.Sub(out.Benchmarks[3].Value).Sub(sf.Previous3.Sub(previousMarket.Value))
		f.Strength.ReturnState = deltaState(f.Strength.ReturnRaw, "RISING", "STABLE", "FALLING")
		f.Strength.RSState = deltaState(f.Strength.RSRaw, "RISING", "STABLE", "FALLING")
		f.Strength.StrengthState = f.Momentum
		f.Strength.StrengthRaw = f.Strength.ReturnRaw.Add(f.Strength.RSRaw).Div(N("2"))
		bg := Background{Market: out.MarketContext, Sectors: sectors[sf.ID], Funds: []FundsContext{}, IndependentFunds: []FundsContext{}, Supply: sf.Supply, Trend: f.Trend}
		for _, theme := range background.ThemesForInstrument(sf.ID) {
			fund, er := background.FundForTheme(b.Ctx(), sf.ID, theme)
			if er != nil {
				return out, er
			}
			bg.IndependentFunds = append(bg.IndependentFunds, fund)
			for _, sector := range bg.Sectors {
				if sector.ThemeID == theme.ID && sector.Status == Pass {
					bg.Funds = append(bg.Funds, fund)
				}
			}
		}
		bg = shared.CompleteBackground(bg, len(input.Themes) > 0)
		if strategyID == R1StrategyID {
			d, er := Decide(sf.ID, sf.Amount, f.Path.CloseLimitDown, Identify(*f), MA20Trend(f.Close, sf.CurrentMA20, sf.PriorMA20), f.Trend.Condition, f.Trend.State, WeakToStrong(f.Path), nil)
			if er != nil {
				return out, er
			}
			d.Background = bg
			d.Evidence = sf.EvidenceHashes
			parts := []Condition{d.Common}
			for _, s := range Stages {
				parts = append(parts, d.Setups[s])
			}
			d.Reasons = reasonSet(parts...)
			out.Decisions = append(out.Decisions, d)
		} else {
			out.V2 = append(out.V2, DecideV2(*sf, bg))
		}

	}
	if strategyID == R1StrategyID {
		out.Coverage = EvaluateCoverage(out.Decisions, q)
	} else {
		RankV2(out.V2)
		out.Coverage = EvaluateV2Coverage(out.V2, q)
		out.Contract = "GO_V2_FULL_V2"
	}
	out.CandidateAvailability = out.Coverage.CandidateAvailability
	out.RunStatus = "SUCCEEDED"
	out.Metrics = b.Snapshot()
	return out, b.Check()
}
func appendUnique(a []string, s string) []string {
	if !contains(a, s) {
		return append(a, s)
	}
	return a
}
