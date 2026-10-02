package v2

import (
	shared "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/shared"
	"sort"
)

type V2Decision struct {
	Background   Background           `json:"background"`
	InstrumentID string               `json:"instrument_id"`
	StrategyID   string               `json:"strategy_id"`
	Status       string               `json:"decision_status"`
	Primary      string               `json:"primary_stage"`
	Alternate    []string             `json:"alternate_stages"`
	Setups       map[string]Condition `json:"stage_candidates"`
	Conditions   map[string]Condition `json:"conditions"`
	Reasons      []string             `json:"reason_codes"`
	WatchReasons []string             `json:"watch_reasons"`
	Bucket       string               `json:"observation_bucket,omitempty"`
	RankFeatures []Number             `json:"setup_rank_features"`
	Rank         int                  `json:"rank"`
	SetupRank    int                  `json:"setup_rank"`
	RankScope    string               `json:"rank_scope"`
	Evidence     []string             `json:"evidence_refs"`
}

func MarketCompatibility(setup string, m MarketContext) Condition {
	if setup == "" {
		return C(Unknown, "SAM_MARKET_COMPATIBILITY_WAITING_FOR_SETUP")
	}
	if m.State == "UNKNOWN" {
		c := C(Unknown, "SAM_MARKET_CONTEXT_UNKNOWN")
		if len(m.Reasons) > 0 {
			c.Reasons = m.Reasons
		}
		return c
	}
	allowed := map[string][]string{"START": {"EXPANSION", "REPAIR", "UNKNOWN"}, "ACCELERATION": {"EXPANSION", "REPAIR"}, "HIGH_LEVEL_REPAIR": {"DIVERGENCE", "REPAIR", "EXPANSION", "UNKNOWN"}, "TREND_PULLBACK": {"EXPANSION", "DIVERGENCE", "REPAIR", "RETREAT", "UNKNOWN"}}
	if contains(allowed[setup], m.State) {
		return C(Pass, "")
	}
	return C(Fail, "SAM_MARKET_CONTEXT_INCOMPATIBLE_WITH_SETUP")
}
func SameTheme(setup string, sectors []SectorContext, funds []FundsContext, summaries ...Condition) Condition {
	if setup == "" {
		return C(Unknown, "SAM_SAME_THEME_WAITING_FOR_SETUP")
	}
	byID := map[string]Condition{}
	for _, f := range funds {
		byID[f.ThemeID] = f.Condition
	}
	unknown, neutral, negative, hard := []Condition{}, []Condition{}, []Condition{}, []Condition{}
	for _, s := range sectors {
		c := s.Compatibility[setup]
		label, _ := c.Details["context_status"].(string)
		switch label {
		case "SUPPORTIVE":
			f, ok := byID[s.ThemeID]
			if !ok {
				f = C(Unknown, "SAM_FUNDS_CONTEXT_FOR_SUPPORTIVE_THEME_UNKNOWN")
			}
			if f.Status == Pass {
				r := C(Pass, "")
				r.Details = map[string]any{"primary_theme_id": s.ThemeID, "context_status": "SUPPORTIVE", "same_theme_policy": "SETUP_SPECIFIC_SECTOR_AND_FUNDS_MUST_SHARE_THEME"}
				return r
			}
			if f.Status == Fail {
				hard = append(hard, f)
			} else {
				unknown = append(unknown, f)
			}
		case "NEGATIVE":
			negative = append(negative, c)
		case "NEUTRAL":
			neutral = append(neutral, c)
		default:
			unknown = append(unknown, c)
		}
	}
	if len(hard) > 0 {
		return Condition{Status: Fail, Reasons: shared.ReasonSet(hard...)}
	}
	if len(negative) > 0 && len(unknown) == 0 && len(neutral) == 0 {
		return Condition{Status: Fail, Reasons: shared.ReasonSet(negative...)}
	}
	all := append(unknown, neutral...)
	if len(summaries) == 0 {
		summaries = []Condition{shared.SectorSummary(sectors, false), shared.FundsSummary(funds)}
	}
	all = append(all, summaries...)
	reasons := shared.ReasonSet(all...)
	if len(reasons) == 0 {
		reasons = []string{"SAM_SAME_THEME_CONTEXT_NOT_SUPPORTIVE"}
	}
	return Condition{Status: Unknown, Reasons: reasons}
}
func Acceleration(f Facts) Condition {
	rs := f.LegacyRS[5]
	level := "LOW"
	if (f.Returns[5].Known() && f.Returns[5].Cmp(N(".15")) >= 0) || (f.Returns[10].Known() && f.Returns[10].Cmp(N(".25")) >= 0) || rs.Positive() {
		level = "HIGH"
	} else if !f.Returns[5].Known() || !f.Returns[10].Known() || !rs.Known() {
		level = "UNKNOWN"
	}
	turnover := Compare(f.RecentTurnover, N("3"), false, "")
	c := C(Fail, "SAM_ACCEL_LEVEL_MOMENTUM_OR_TURNOVER_NOT_MET")
	state := "ACCELERATION_NOT_CONFIRMED"
	if level == "HIGH" && f.Momentum == "RISING" && turnover.Status == Pass {
		c = C(Pass, "")
		state = "ACCELERATION_CONFIRMED"
	} else if level == "HIGH" && f.Momentum == "FALLING" && turnover.Status == Pass {
		c = C(Fail, "SAM_ACCEL_WEAKENING_HIGH_ACTIVITY")
		state = "WEAKENING_HIGH_ACTIVITY"
	} else if (level == "UNKNOWN" || f.Momentum == "UNKNOWN" || turnover.Status == Unknown) && level != "LOW" && turnover.Status != Fail {
		c = C(Unknown, "")
		if level == "UNKNOWN" {
			if !f.Returns[5].Known() {
				c.Reasons = append(c.Reasons, "SAM_STRENGTH_LEVEL_RET5_UNAVAILABLE")
			}
			if !f.Returns[10].Known() {
				c.Reasons = append(c.Reasons, "SAM_STRENGTH_LEVEL_RET10_UNAVAILABLE")
			}
			if !rs.Known() {
				c.Reasons = append(c.Reasons, "SAM_STRENGTH_LEVEL_RS5_UNAVAILABLE")
			}
		}
		if f.Momentum == "UNKNOWN" || f.Momentum == "" {
			c.Reasons = append(c.Reasons, "SAM_ACCEL_STRENGTH_MOMENTUM_UNAVAILABLE")
		}
		if !f.RecentTurnover.Known() {
			c.Reasons = append(c.Reasons, "SAM_ACCEL_HIGH_TURNOVER_PERSISTENCE_UNAVAILABLE")
		}
		sort.Strings(c.Reasons)
		state = "ACCELERATION_PENDING"
	}
	c.Details = map[string]any{"setup_state": state, "strength_level": level, "strength_momentum": f.Momentum, "turnover_persistence_status": turnover.Status}
	return c
}
func behavior(setup string, ident Condition, p Path) Condition {
	if setup == "" {
		return C(Unknown, "SAM_BEHAVIORAL_WAITING_FOR_SETUP")
	}
	if ident.Status != Pass {
		c := C(ident.Status, "SAM_BEHAVIORAL_SETUP_NOT_CONFIRMED")
		c.Reasons = ident.Reasons
		return c
	}
	if setup == "HIGH_LEVEL_REPAIR" {
		return shared.WeakToStrong(p)
	}
	return C(Pass, "")
}

// DecideV2 independently consumes shared facts; it never converts an R1 result.
func DecideV2(sf StockFacts, bg Background) V2Decision {
	f := sf.Facts
	if bg.SectorCondition.Status == "" {
		bg = shared.CompleteBackground(bg, false)
	}
	setups := map[string]Condition{"START": shared.Start(f), "ACCELERATION": Acceleration(f), "HIGH_LEVEL_REPAIR": shared.HighLevelRepair(f), "TREND_PULLBACK": shared.Pullback(f)}
	v := V2Decision{InstrumentID: sf.ID, StrategyID: V2StrategyID, Background: bg, Setups: setups, Conditions: map[string]Condition{}, Reasons: []string{}, WatchReasons: []string{}, Alternate: []string{}, Evidence: sf.EvidenceHashes}
	passing := []string{}
	for _, s := range Stages {
		if setups[s].Status == Pass {
			passing = append(passing, s)
		}
	}
	if len(passing) > 0 {
		v.Primary = passing[0]
		v.Alternate = passing[1:]
	}
	stage := Any(setups)
	stage.Reasons = []string{}
	if stage.Status == Unknown {
		stage.Reasons = []string{"SAM_STAGE_ALL_CANDIDATES_UNKNOWN"}
	} else if stage.Status == Fail {
		stage.Reasons = []string{"SAM_STAGE_NO_PASSING_CANDIDATE"}
	}
	liquidity := C(Pass, "")
	failed, unknown := false, false
	if !sf.Amount.Known() {
		unknown = true
		liquidity.Reasons = append(liquidity.Reasons, "SAM_AMOUNT_UNAVAILABLE")
	} else if sf.Amount.Cmp(N("500000000")) < 0 {
		failed = true
		liquidity.Reasons = append(liquidity.Reasons, "SAM_AMOUNT_BELOW_500M")
	}
	if !sf.Turnover.Known() {
		unknown = true
		liquidity.Reasons = append(liquidity.Reasons, "SAM_TURNOVER_UNAVAILABLE")
	} else if sf.Turnover.Cmp(N(".07")) < 0 {
		failed = true
		liquidity.Reasons = append(liquidity.Reasons, "SAM_TURNOVER_BELOW_7PCT")
	}
	if failed {
		liquidity.Status = Fail
	} else if unknown {
		liquidity.Status = Unknown
	}
	critical := C(Pass, "")
	if f.Path.CloseLimitDown == "TRUE" {
		critical = C(Fail, "SAM_CRITICAL_CLOSE_LIMIT_DOWN")
		critical.Reasons = append(critical.Reasons, "SAM_LATE_CYCLE_WARNING")
	}
	if f.Trend.State == "TREND_BROKEN" {
		critical.Status = Fail
		critical.Reasons = append(critical.Reasons, "SAM_CRITICAL_TREND_BROKEN")
	}
	if critical.Status == Pass && bg.Supply.Status == Unknown && contains(bg.Supply.Reasons, "SAM_REPEATED_UNRECLAIMED_UPPER_SHADOW_SUPPLY_WARNING") {
		critical = bg.Supply
	}
	beh := behavior(v.Primary, setups[v.Primary], f.Path)
	market := MarketCompatibility(v.Primary, bg.Market)
	same := SameTheme(v.Primary, bg.Sectors, bg.Funds, bg.SectorCondition, bg.FundsCondition)
	v.Conditions = map[string]Condition{"liquidity_bottom": liquidity, "trend_state": f.Trend.Condition, "setup_identification": stage, "behavioral_confirmation": beh, "market_compatibility": market, "same_theme_context": same, "critical_counterevidence": critical}
	ordered := []struct {
		c    Condition
		code string
	}{{liquidity, "SAM_DECISION_LIQUIDITY_HARD_FAIL"}, {f.Trend.Condition, "SAM_DECISION_TREND_HARD_FAIL"}, {critical, "SAM_DECISION_CRITICAL_COUNTEREVIDENCE"}, {market, "SAM_DECISION_MARKET_CONTEXT_INCOMPATIBLE"}, {same, "SAM_DECISION_SAME_THEME_CONTEXT_KNOWN_FAIL"}}
	for _, p := range ordered {
		if p.c.Status == Fail {
			v.Reasons = append(v.Reasons, p.code)
		}
	}
	weak := setups["ACCELERATION"].Details["setup_state"] == "WEAKENING_HIGH_ACTIVITY"
	if stage.Status == Fail && !weak {
		v.Reasons = append(v.Reasons, "SAM_DECISION_NO_PASSING_SETUP")
	}
	if len(v.Reasons) > 0 {
		v.Status = "REJECTED"
		sort.Strings(v.Reasons)
	} else if beh.Status == Fail {
		v.Status = "WATCH"
		v.Reasons = []string{"SAM_WATCH_BEHAVIORAL_CONFIRMATION_FAILED_FOR_SETUP"}
		v.WatchReasons = v.Reasons
	} else if v.Primary == "" && weak {
		v.Status = "WATCH"
		v.Reasons = []string{"SAM_WATCH_WEAKENING_HIGH_ACTIVITY"}
		v.WatchReasons = v.Reasons
		v.Bucket = "WEAKENING_HIGH_ACTIVITY_OBSERVATION"
	} else {
		for _, p := range []struct {
			c    Condition
			code string
		}{{liquidity, "SAM_DECISION_LIQUIDITY_UNKNOWN"}, {f.Trend.Condition, "SAM_DECISION_TREND_UNKNOWN"}, {stage, "SAM_DECISION_SETUP_UNKNOWN"}} {
			if p.c.Status == Unknown {
				v.Reasons = append(v.Reasons, p.code)
			}
		}
		if len(v.Reasons) > 0 {
			v.Status = "UNKNOWN"
		} else {
			for _, p := range []struct {
				c    Condition
				code string
			}{{beh, "SAM_WATCH_BEHAVIORAL_CONFIRMATION_PENDING"}, {market, "SAM_WATCH_MARKET_CONTEXT_INCOMPLETE"}, {same, "SAM_WATCH_SAME_THEME_CONTEXT_INCOMPLETE"}, {critical, "SAM_WATCH_COUNTEREVIDENCE_REVIEW_PENDING"}} {
				if p.c.Status == Unknown {
					v.WatchReasons = append(v.WatchReasons, p.code)
				}
			}
			if len(v.WatchReasons) > 0 {
				v.Status = "WATCH"
				v.Reasons = v.WatchReasons
			} else {
				v.Status = "FOCUS"
				v.Reasons = []string{"SAM_FOCUS_BY_STAGE_AWARE_V2_REQUIREMENTS"}
			}
		}
	}
	switch v.Primary {
	case "START":
		v.RankFeatures = []Number{f.Returns[1].Mul(N("100")).Rounded(4), f.AmountRatio, f.LegacyRS[1]}
	case "ACCELERATION":
		v.RankFeatures = []Number{f.Returns[3].Mul(N("100")).Rounded(4), f.Returns[5].Mul(N("100")).Rounded(4), f.RecentTurnover}
	case "HIGH_LEVEL_REPAIR":
		v.RankFeatures = []Number{f.Path.Recovery.Mul(N("100")).Rounded(4), f.Path.CLV, f.LegacyRS[1]}
	case "TREND_PULLBACK":
		v.RankFeatures = []Number{f.Trend.Distance, f.SectorRS1, f.AmountRatio}
	}
	return v
}
func RankV2(rows []V2Decision) {
	tier := map[string]int{"FOCUS": 0, "WATCH": 1, "UNKNOWN": 2, "REJECTED": 3}
	stage := map[string]int{"START": 0, "ACCELERATION": 1, "HIGH_LEVEL_REPAIR": 2, "TREND_PULLBACK": 3, "": 4}
	sort.SliceStable(rows, func(i, j int) bool {
		a, b := rows[i], rows[j]
		if tier[a.Status] != tier[b.Status] {
			return tier[a.Status] < tier[b.Status]
		}
		if stage[a.Primary] != stage[b.Primary] {
			return stage[a.Primary] < stage[b.Primary]
		}
		for k, x := range a.RankFeatures {
			y := b.RankFeatures[k]
			if !x.Known() || !y.Known() {
				if x.Known() != y.Known() {
					return x.Known()
				}
				continue
			}
			if c := x.Cmp(y); c != 0 {
				if a.Primary == "TREND_PULLBACK" && k == 0 {
					return c < 0
				}
				return c > 0
			}
		}
		return a.InstrumentID < b.InstrumentID
	})
	counts := map[string]int{}
	for i := range rows {
		r := &rows[i]
		setup := r.Primary
		if setup == "" {
			setup = r.Bucket
		}
		if setup == "" {
			setup = "NO_PASSING_SETUP"
		}
		r.RankScope = r.Status + ":" + setup
		counts[r.RankScope]++
		r.SetupRank = counts[r.RankScope]
		r.Rank = i + 1
	}
}
