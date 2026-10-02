package facts

import (
	"context"
	"sort"
)

type SectorWindow struct {
	Benchmark
	PeerMedian     Number   `json:"peer_median_return"`
	UpFraction     Number   `json:"peer_up_fraction"`
	AmountMedian   Number   `json:"peer_amount_change_median"`
	MarketReturn   Number   `json:"market_equal_weight_return"`
	MarketCoverage Number   `json:"market_benchmark_coverage"`
	MarketRS       Number   `json:"sector_market_rs"`
	StockReturn    Number   `json:"stock_return"`
	RS             Number   `json:"rs_sector"`
	Reasons        []string `json:"reason_codes"`
}
type SectorContext struct {
	Temporal        Condition            `json:"temporal_leadership"`
	LimitEvidence   Condition            `json:"limit_streak_evidence"`
	ThemeID         string               `json:"theme_id"`
	Name            string               `json:"theme_name"`
	Type            string               `json:"theme_type"`
	Status          State                `json:"status"`
	Reasons         []string             `json:"reason_codes"`
	MemberCount     int                  `json:"member_count"`
	PeerExpected    int                  `json:"peer_expected"`
	Level           string               `json:"sector_level_state"`
	Absolute        string               `json:"sector_absolute_level"`
	Relative        string               `json:"sector_relative_level"`
	Momentum        string               `json:"sector_momentum_state"`
	MomentumRaw     Number               `json:"sector_momentum_raw"`
	MatchedCoverage Number               `json:"matched_coverage"`
	Breadth         string               `json:"breadth_state"`
	BreadthFraction Number               `json:"sector_breadth_fraction"`
	Role            string               `json:"stock_role"`
	RoleScore       Number               `json:"stock_role_score"`
	RoleFeatures    map[string]Number    `json:"stock_role_features"`
	Compatibility   map[string]Condition `json:"setup_compatibility"`
	Windows         map[int]SectorWindow `json:"windows"`
	Evidence        []string             `json:"evidence_refs"`
	Sources         []string             `json:"source_refs"`
}
type SumValues struct {
	sum             Number
	valid, positive int
	sorted          []Number
	ranks           Percentiles
}

func Summarize(v []Number) SumValues {
	a := SumValues{sum: N("0")}
	for _, n := range v {
		if n.Known() {
			a.sum = a.sum.Add(n)
			a.valid++
			if n.Positive() {
				a.positive++
			}
			a.sorted = append(a.sorted, n)
		}
	}
	sort.Slice(a.sorted, func(i, j int) bool { return a.sorted[i].Cmp(a.sorted[j]) < 0 })
	a.ranks = NewPercentiles(a.sorted)
	return a
}
func (a SumValues) excluding(v Number, expected int) Benchmark {
	b := Benchmark{Expected: expected, Valid: a.valid, Coverage: N("0"), Status: "UNKNOWN"}
	sum := a.sum
	if v.Known() {
		b.Valid--
		sum = sum.Sub(v)
	}
	if expected > 0 {
		b.Coverage = count(b.Valid).Div(count(expected))
	}
	if b.Valid > 0 {
		b.Status = "INSUFFICIENT_COVERAGE"
		if b.Coverage.Cmp(N(".6")) >= 0 {
			b.Status = "COMPLETE"
			b.Value = sum.Div(count(b.Valid))
		}
	}
	return b
}
func (a SumValues) MedianExcluding(v Number) Number {
	n := len(a.sorted)
	remove := -1
	if v.Known() {
		remove = sort.Search(n, func(i int) bool { return a.sorted[i].Cmp(v) >= 0 })
		n--
	}
	if n <= 0 {
		return Number{}
	}
	at := func(i int) Number {
		if remove >= 0 && i >= remove {
			i++
		}
		return a.sorted[i]
	}
	if n%2 == 1 {
		return at(n / 2)
	}
	return at(n/2 - 1).Add(at(n / 2)).Div(N("2"))
}
func ComputeSectors(ctx context.Context, themes []Row, facts []StockFacts, market map[int]Benchmark) (map[string][]SectorContext, error) {
	batch := &BackgroundBatch{}
	for _, row := range themes {
		theme, err := decodeTheme(ctx, row, nil)
		if err != nil {
			return nil, err
		}
		batch.themes = append(batch.themes, theme)
	}
	return batch.ComputeSectors(ctx, facts, market)
}

func (batch *BackgroundBatch) ComputeSectors(ctx context.Context, facts []StockFacts, market map[int]Benchmark) (map[string][]SectorContext, error) {
	byID := map[string]*StockFacts{}
	out := map[string][]SectorContext{}
	for i := range facts {
		byID[facts[i].ID] = &facts[i]
		out[facts[i].ID] = []SectorContext{}
	}
	for _, t := range batch.themes {
		if e := ctx.Err(); e != nil {
			return nil, e
		}
		members := []string{}
		for _, id := range t.members {
			if byID[id] != nil {
				members = append(members, id)
			}
		}
		totals, changes := map[int]SumValues{}, map[int]SumValues{}
		for _, n := range []int{1, 3, 5, 20} {
			v, a := []Number{}, []Number{}
			for _, id := range members {
				v = append(v, byID[id].Facts.Returns[n])
				a = append(a, byID[id].AmountChanges[n])
			}
			totals[n] = Summarize(v)
			changes[n] = Summarize(a)
		}
		amount, turnover, limitScores := []Number{}, []Number{}, []Number{}
		matchedRecent, matchedPrevious := N("0"), N("0")
		matched := 0
		for _, id := range members {
			sf := byID[id]
			amount = append(amount, sf.Amount)
			turnover = append(turnover, sf.Turnover)
			score := Number{}
			if sf.CurrentLimit.Known {
				score = N("0")
				if sf.CurrentLimit.CloseUp == "TRUE" {
					score = N("1")
				}
			}
			limitScores = append(limitScores, score)
			if sf.Recent3.Known() && sf.Previous3.Known() {
				matched++
				matchedRecent = matchedRecent.Add(sf.Recent3)
				matchedPrevious = matchedPrevious.Add(sf.Previous3)
			}
		}
		amounts, turnovers, limits := Summarize(amount), Summarize(turnover), Summarize(limitScores)
		for _, id := range members {
			if e := ctx.Err(); e != nil {
				return nil, e
			}
			sf := byID[id]
			expected := len(members) - 1
			s := SectorContext{ThemeID: t.ID, Name: t.Name, Type: t.Type, Status: Pass, Reasons: []string{}, MemberCount: len(members), PeerExpected: expected, Windows: map[int]SectorWindow{}, RoleFeatures: map[string]Number{}, Compatibility: map[string]Condition{}, Evidence: []string{t.Hash}, Sources: append(t.Sources[:0:0], t.Sources...)}
			complete, valid := 0, 0
			for _, n := range []int{1, 3, 5} {
				own := sf.Facts.Returns[n]
				a := totals[n]
				bench := a.excluding(own, expected)
				v := SectorWindow{Benchmark: bench, StockReturn: own, MarketReturn: market[n].Value, MarketCoverage: market[n].Coverage, Reasons: []string{}}
				if bench.Status == "COMPLETE" {
					complete++
					v.PeerMedian = a.MedianExcluding(own)
					up := a.positive
					if own.Positive() {
						up--
					}
					v.UpFraction = count(up).Div(count(bench.Valid))
					v.AmountMedian = changes[n].MedianExcluding(sf.AmountChanges[n])
					v.MarketRS = bench.Value.Sub(market[n].Value)
					v.RS = own.Sub(bench.Value).Mul(N("100")).Rounded(4).Div(N("100"))
				} else {
					v.Reasons = []string{"SAM_SECTOR_WINDOW_RETURNS_UNAVAILABLE"}
					if bench.Valid > 0 {
						v.Reasons = []string{"SAM_SECTOR_WINDOW_COVERAGE_INSUFFICIENT"}
					}
				}
				valid += bench.Valid
				s.Windows[n] = v
			}
			if expected < 2 {
				s.Status = Fail
				s.Reasons = []string{"SAM_SECTOR_PEER_COUNT_BELOW_MIN"}
			} else if valid == 0 {
				s.Status = Unknown
				s.Reasons = []string{"SAM_SECTOR_PEER_RETURNS_UNAVAILABLE"}
			} else if complete == 0 {
				s.Status = Unknown
				s.Reasons = []string{"SAM_SECTOR_PEER_RETURN_COVERAGE_INSUFFICIENT"}
			}
			// Python V2 explicitly consumes the displayed four-place percent return here.
			p5 := s.Windows[5].Value.Mul(N("100")).Rounded(4).Div(N("100"))
			rs5 := p5.Sub(market[5].Value)
			s.Absolute = "UNKNOWN"
			s.Relative = "UNKNOWN"
			s.Level = "UNKNOWN"
			if p5.Known() {
				s.Absolute = "NORMAL"
				if p5.Cmp(N(".08")) >= 0 {
					s.Absolute = "HIGH"
				} else if p5.Cmp(N("0")) <= 0 {
					s.Absolute = "LOW"
				}
			}
			if rs5.Known() {
				s.Relative = "MARKET_IN_LINE"
				if rs5.Positive() {
					s.Relative = "OUTPERFORMING"
				} else if rs5.Cmp(N("0")) < 0 {
					s.Relative = "UNDERPERFORMING"
				}
			}
			if p5.Known() && rs5.Known() {
				s.Level = "NORMAL"
				if s.Absolute == "HIGH" && s.Relative == "OUTPERFORMING" {
					s.Level = "HIGH"
				} else if s.Absolute == "LOW" || s.Relative == "UNDERPERFORMING" {
					s.Level = "LOW"
				}
			}
			mc := matched
			mr, mp := matchedRecent, matchedPrevious
			if sf.Recent3.Known() && sf.Previous3.Known() {
				mc--
				mr = mr.Sub(sf.Recent3)
				mp = mp.Sub(sf.Previous3)
			}
			s.MatchedCoverage = N("0")
			if expected > 0 {
				s.MatchedCoverage = count(mc).Div(count(expected))
			}
			if mc > 0 && s.MatchedCoverage.Cmp(N(".6")) >= 0 {
				s.MomentumRaw = mr.Sub(mp).Div(count(mc))
			}
			s.Momentum = DeltaState(s.MomentumRaw, "STRENGTHENING", "STABLE", "WEAKENING")
			s.BreadthFraction = s.Windows[1].UpFraction
			s.Breadth = "UNKNOWN"
			if s.BreadthFraction.Known() {
				s.Breadth = "NORMAL"
				if s.BreadthFraction.Cmp(N(".6")) >= 0 {
					s.Breadth = "BROAD"
				} else if s.BreadthFraction.Cmp(N(".4")) <= 0 {
					s.Breadth = "NARROW"
				}
			}
			s.Temporal = C(Unknown, "SAM_INTRADAY_FIRST_SEAL_TIMESTAMPS_NOT_IN_MARKET_INPUT")
			s.LimitEvidence = C(Unknown, "SAM_ROLE_LIMIT_STREAK_PEER_EVIDENCE_INSUFFICIENT")
			s.RoleFeatures["sector_return_percentile_5d"] = totals[5].ranks.Lookup(sf.Facts.Returns[5])
			if p5.Known() {
				s.RoleFeatures["sector_rs_percentile_5d"] = s.RoleFeatures["sector_return_percentile_5d"]
			}
			s.RoleFeatures["amount_percentile"] = amounts.ranks.Lookup(sf.Amount)
			s.RoleFeatures["turnover_percentile"] = turnovers.ranks.Lookup(sf.Turnover)
			s.RoleFeatures["persistent_strength_percentile_20d"] = totals[20].ranks.Lookup(sf.Facts.Returns[20])
			if len(members) > 0 && count(limits.valid).Div(count(len(members))).Cmp(N(".6")) >= 0 && sf.CurrentLimit.Known {
				score := N("0")
				if sf.CurrentLimit.CloseUp == "TRUE" {
					score = N("1")
				}
				s.RoleFeatures["limit_streak_percentile"] = limits.ranks.Lookup(score)
				s.LimitEvidence = C(Pass, "")
			}
			sum := N("0")
			n := 0
			for _, v := range s.RoleFeatures {
				if v.Known() {
					sum = sum.Add(v)
					n++
				}
			}
			s.Role = "UNKNOWN"
			if n >= 3 {
				s.RoleScore = sum.Div(count(n))
				s.Role = "FOLLOWER"
				if s.RoleScore.Cmp(N("90")) >= 0 {
					s.Role = "LEADER"
				} else if s.RoleScore.Cmp(N("67")) >= 0 {
					s.Role = "CORE"
				} else if s.RoleScore.Cmp(N("50")) >= 0 {
					s.Role = "FIRST_TIER"
				}
			}

			out[id] = append(out[id], s)
		}
	}
	return out, nil
}
func SectorRS(ctx context.Context, themes []Row, resolver *Resolver, facts []StockFacts) (map[string]Number, error) {
	visible, _, e := VisibleThemes(ctx, themes, resolver)
	if e != nil {
		return nil, e
	}
	sectors, e := ComputeSectors(ctx, visible, facts, map[int]Benchmark{})
	if e != nil {
		return nil, e
	}
	out := map[string]Number{}
	for id, ss := range sectors {
		for _, s := range ss {
			if s.Status != Pass {
				continue
			}
			v := s.Windows[1].RS
			if v.Known() && (!out[id].Known() || v.Cmp(out[id]) > 0) {
				out[id] = v
			}
		}
	}
	return out, nil
}
