package facts

import (
	"context"
	"sort"
)

type EvidenceRejection struct {
	Kind    string   `json:"kind"`
	ID      string   `json:"id"`
	Reasons []string `json:"reason_codes"`
}
type Background struct {
	SectorCondition  Condition       `json:"sector_context"`
	FundsCondition   Condition       `json:"capital_linkage"`
	Market           MarketContext   `json:"market_status"`
	Sectors          []SectorContext `json:"sector_candidates"`
	Funds            []FundsContext  `json:"funds_candidates"`
	IndependentFunds []FundsContext  `json:"independent_theme_funds"`
	Supply           Condition       `json:"supply_pressure"`
	Trend            Trend           `json:"trend_strength"`
}
type LimitFact struct {
	CloseUp       string   `json:"close_limit_up"`
	TouchedUp     string   `json:"touched_limit_up"`
	Broken        string   `json:"touched_not_closed"`
	CloseDown     string   `json:"close_limit_down"`
	Streak        Number   `json:"streak_exchange"`
	LowerBound    Number   `json:"streak_lower_bound"`
	Quality       string   `json:"event_quality"`
	StreakQuality string   `json:"streak_quality"`
	Known         bool     `json:"known_current_limit_event"`
	Evidence      []string `json:"evidence_refs"`
}

func WindowLimit(w Window, end int) LimitFact {
	l := LimitFact{CloseUp: "UNKNOWN", TouchedUp: "UNKNOWN", Broken: "UNKNOWN", CloseDown: "UNKNOWN", Quality: "MISSING_BAR"}
	c := 0
	left := true
	start := end - 19
	if start < 0 {
		start = 0
	}
	for i := start; i <= end; i++ {
		b := w.At(i)
		up, down, quality := Limits(b)
		l = LimitFact{CloseUp: up, CloseDown: down, Quality: quality, TouchedUp: up, Broken: up, Evidence: []string{}}
		if b != nil {
			l.Evidence = []string{b.Hash}
		}
		if up == "TRUE" || up == "FALSE" {
			l.Known = true
			l.TouchedUp = "FALSE"
			l.Broken = "FALSE"
			if b.High.Cmp(b.LimitUp) == 0 {
				l.TouchedUp = "TRUE"
				if up != "TRUE" {
					l.Broken = "TRUE"
				}
			}
		}
		switch up {
		case "TRUE":
			c++
			l.LowerBound = count(c)
			if !left {
				l.Streak = count(c)
				l.StreakQuality = "EXACT"
			} else {
				l.StreakQuality = "LEFT_CENSORED"
			}
		case "FALSE", "NOT_APPLICABLE":
			c = 0
			left = false
			l.Streak = N("0")
			l.LowerBound = N("0")
			l.StreakQuality = "KNOWN_NON_LIMIT_BREAK"
		default:
			c = 0
			left = true
			l.StreakQuality = "MISSING_BREAK_UNKNOWN"
		}
	}
	return l
}

type MarketWidth struct {
	CloseUp    int    `json:"close_limit_count"`
	Broken     int    `json:"touched_not_closed_count"`
	CloseDown  int    `json:"close_limit_down_count"`
	Height     Number `json:"verified_max_height"`
	Known      int    `json:"known_limit_event_count"`
	Unknown    int    `json:"unknown_limit_event_count"`
	Expected   int    `json:"expected_limit_event_count"`
	Coverage   Number `json:"limit_event_coverage"`
	CloseRate  Number `json:"close_limit_rate"`
	BrokenRate Number `json:"broken_board_rate"`
	DownRate   Number `json:"close_limit_down_rate"`
	Score      Number `json:"normalized_strength_score"`
}
type MarketContext struct {
	Status          State       `json:"status"`
	State           string      `json:"ecosystem_state"`
	Level           string      `json:"market_level"`
	Momentum        string      `json:"market_momentum"`
	Previous        string      `json:"previous_state"`
	Reasons         []string    `json:"reason_codes"`
	MomentumReasons []string    `json:"market_momentum_reason_codes"`
	Width           MarketWidth `json:"market_width"`
	PreviousWidth   MarketWidth `json:"previous_market_width"`
	Cohort          Condition   `json:"strong_cohort_feedback"`
	Transition      []string    `json:"transition_evidence"`
}

func marketWidth(facts []StockFacts, previous bool) MarketWidth {
	w := MarketWidth{Expected: len(facts), Coverage: N("0")}
	for _, sf := range facts {
		l := sf.CurrentLimit
		if previous {
			l = sf.PreviousLimit
		}
		if !l.Known {
			w.Unknown++
			continue
		}
		w.Known++
		if l.CloseUp == "TRUE" {
			w.CloseUp++
			if l.Streak.Known() && (!w.Height.Known() || l.Streak.Cmp(w.Height) > 0) {
				w.Height = l.Streak
			}
		}
		if l.Broken == "TRUE" {
			w.Broken++
		}
		if l.CloseDown == "TRUE" {
			w.CloseDown++
		}
	}
	if w.Expected > 0 {
		w.Coverage = count(w.Known).Div(count(w.Expected))
	}
	if w.Known > 0 {
		w.CloseRate = count(w.CloseUp).Div(count(w.Known))
		w.BrokenRate = count(w.Broken).Div(count(w.Known))
		w.DownRate = count(w.CloseDown).Div(count(w.Known))
		h := w.Height
		if !h.Known() {
			h = N("0")
		}
		if h.Cmp(N("3")) > 0 {
			h = N("3")
		}
		w.Score = w.CloseRate.Mul(N("2")).Add(h.Div(N("3"))).Sub(w.DownRate.Mul(N("2"))).Sub(w.BrokenRate)
	}
	return w
}
func MarketRaw(w MarketWidth) (string, []string) {
	if w.Expected > 0 && w.Coverage.Cmp(N(".6")) < 0 {
		return "UNKNOWN", []string{"SAM_MARKET_LIMIT_EVENT_COVERAGE_INSUFFICIENT"}
	}
	exp := w.CloseUp >= 15 || (w.Height.Known() && w.Height.Cmp(N("3")) >= 0)
	r := []string{}
	if w.CloseDown >= 1 {
		r = append(r, "SAM_MARKET_CLOSE_LIMIT_DOWN_PRESSURE")
	}
	if w.Broken >= 10 {
		r = append(r, "SAM_MARKET_BROKEN_BOARD_PRESSURE")
	}
	if len(r) > 0 {
		if exp {
			r = append([]string{"SAM_MARKET_EXPANSION_WITH_MAJOR_DIVERGENCE_PRESSURE"}, r...)
		}
		return "DIVERGENCE", r
	}
	if exp {
		return "EXPANSION", r
	}
	return "RETREAT", []string{"SAM_MARKET_PARTIAL_OR_WEAK_STRENGTH_WITHOUT_EXPANSION"}
}
func ComputeMarket(facts []StockFacts) MarketContext {
	m := MarketContext{Width: marketWidth(facts, false), PreviousWidth: marketWidth(facts, true), Momentum: "UNKNOWN", MomentumReasons: []string{}, Transition: []string{}}
	m.Level, m.Reasons = MarketRaw(m.Width)
	m.Previous, _ = MarketRaw(m.PreviousWidth)
	a, z := m.Width, m.PreviousWidth
	if !a.Score.Known() || !z.Score.Known() {
		m.MomentumReasons = []string{"SAM_MARKET_NORMALIZED_STRENGTH_UNAVAILABLE"}
	} else if a.Coverage.Cmp(N(".6")) < 0 || z.Coverage.Cmp(N(".6")) < 0 || a.Coverage.Sub(z.Coverage).Abs().Cmp(N(".1")) > 0 {
		m.MomentumReasons = []string{"SAM_MARKET_MOMENTUM_COVERAGE_NOT_COMPARABLE"}
	} else {
		m.Momentum = DeltaState(a.Score.Sub(z.Score), "IMPROVING", "STABLE", "WEAKENING")
		if a.CloseRate.Cmp(z.CloseRate) > 0 {
			m.Transition = append(m.Transition, "SAM_MARKET_CLOSE_LIMIT_COUNT_IMPROVED")
		}
		if a.BrokenRate.Cmp(z.BrokenRate) < 0 {
			m.Transition = append(m.Transition, "SAM_MARKET_BROKEN_BOARD_COUNT_IMPROVED")
		}
		if a.DownRate.Cmp(z.DownRate) < 0 {
			m.Transition = append(m.Transition, "SAM_MARKET_CLOSE_LIMIT_DOWN_COUNT_IMPROVED")
		}
	}
	cohort := []Number{}
	expected, known, advanced := 0, 0, 0
	for _, sf := range facts {
		if sf.PreviousLimit.CloseUp == "TRUE" {
			expected++
			if sf.Facts.Returns[1].Known() {
				cohort = append(cohort, sf.Facts.Returns[1])
			}
			if sf.CurrentLimit.Known {
				known++
			}
			if sf.CurrentLimit.CloseUp == "TRUE" {
				advanced++
			}
		}
	}
	m.Cohort = C(Unknown, "SAM_MARKET_PREVIOUS_CLOSE_LIMIT_COHORT_EMPTY")
	if expected > 0 {
		m.Cohort = C(Unknown, "SAM_MARKET_STRONG_COHORT_FEEDBACK_COVERAGE_INSUFFICIENT")
		if len(cohort) > 0 && count(len(cohort)).Div(count(expected)).Cmp(N(".6")) >= 0 {
			m.Cohort = C(Pass, "")
			if m.Level == "EXPANSION" && Median(cohort).Cmp(N("0")) < 0 {
				m.Level = "DIVERGENCE"
				m.Reasons = append(m.Reasons, "SAM_MARKET_STRONG_COHORT_FEEDBACK_NEGATIVE")
			}
		}
	}
	m.Cohort.Details = map[string]any{"previous_close_limit_cohort_count": expected, "matched_return_count": len(cohort), "matched_coverage": count(len(cohort)).Div(count(expected)), "median_return": Median(cohort), "advance_close_limit_count": advanced, "current_limit_event_known_count": known}
	m.State = m.Level
	m.Status = Pass
	if m.Level == "UNKNOWN" {
		m.Status = Unknown
	}
	if m.Level == "RETREAT" && contains([]string{"RETREAT", "DIVERGENCE"}, m.Previous) && m.Momentum == "IMPROVING" && (a.CloseUp > 0 || a.Broken > 0 || a.Height.Positive()) && len(m.Transition) > 0 {
		m.State = "REPAIR"
		m.Reasons = []string{}
	}
	return m
}
func DeltaState(v Number, rising, stable, falling string) string {
	if !v.Known() {
		return "UNKNOWN"
	}
	if v.Cmp(N(".001")) > 0 {
		return rising
	}
	if v.Cmp(N("-.001")) < 0 {
		return falling
	}
	return stable
}
func Median(v []Number) Number {
	a := []Number{}
	for _, n := range v {
		if n.Known() {
			a = append(a, n)
		}
	}
	if len(a) == 0 {
		return Number{}
	}
	sort.Slice(a, func(i, j int) bool { return a[i].Cmp(a[j]) < 0 })
	if len(a)%2 == 1 {
		return a[len(a)/2]
	}
	return a[len(a)/2-1].Add(a[len(a)/2]).Div(N("2"))
}
func SupplyPressure(w Window, end int) Condition {
	if end < 4 {
		return C(Unknown, "SAM_SUPPLY_PRESSURE_WINDOW_UNAVAILABLE")
	}
	rows := []map[string]any{}
	long, unresolved, failed := 0, 0, 0
	current := w.At(end)
	result := C(Pass, "")
	for i := end - 4; i <= end; i++ {
		b := w.At(i)
		if b == nil || !validOHLC(b) || b.High.Cmp(b.Low) <= 0 {
			result = C(Unknown, "SAM_SUPPLY_PRESSURE_DAILY_PATH_UNAVAILABLE")
			break
		}
		top := b.Open
		if b.RawClose.Cmp(top) > 0 {
			top = b.RawClose
		}
		shadow := b.High.Sub(top).Div(b.High.Sub(b.Low))
		isLong := shadow.Cmp(N(".5")) >= 0
		first := ""
		days := Number{}
		for j := i + 1; j <= end; j++ {
			f := w.At(j)
			if f != nil && f.Close.Known() && f.Close.Cmp(b.High) >= 0 {
				first = w.Sessions[j]
				days = count(j - i)
				break
			}
		}
		above := current != nil && current.Close.Known() && current.Close.Cmp(b.High) >= 0
		u := isLong && first == "" && !above
		f := isLong && first != "" && !above
		if isLong {
			long++
		}
		if u {
			unresolved++
		}
		if f {
			failed++
		}
		rows = append(rows, map[string]any{"session": w.Sessions[i], "shadow_high": b.High, "upper_shadow_to_range": shadow.Rounded(6), "long_upper_shadow": isLong, "first_reclaim_date": first, "sessions_to_first_reclaim": days, "ever_reclaimed": first != "", "reclaimed_then_failed": f, "current_above_reclaimed_level": above, "unresolved_supply": u, "unreclaimed_by_observation_close": u, "evidence_refs": []string{b.Hash}})
	}
	if unresolved >= 2 {
		result = C(Unknown, "SAM_REPEATED_UNRECLAIMED_UPPER_SHADOW_SUPPLY_WARNING")
	}
	result.Details = map[string]any{"upper_shadow_rows": rows, "long_upper_shadow_count": long, "unreclaimed_recent_shadow_count": unresolved, "unresolved_supply_count": unresolved, "reclaimed_then_failed_count": failed, "single_upper_shadow_policy": "NOT_CRITICAL_REJECTION_WITHOUT_REPEATED_UNRECLAIMED_SUPPLY"}
	return result
}
func VisibleThemes(ctx context.Context, themes []Row, resolver *Resolver) ([]Row, []EvidenceRejection, error) {
	groups := map[string][]Row{}
	rejected := []EvidenceRejection{}
	for _, t := range themes {
		if e := ctx.Err(); e != nil {
			return nil, nil, e
		}
		reason := ""
		if !contains([]string{"EVENT", "INDUSTRY"}, t.Text("theme_type")) {
			reason = "SAM_THEME_TYPE_NOT_ALLOWED"
		} else if proof := resolver.Resolve(t, Scope{Session: resolver.Observation, Complete: true}); !proof.Allowed {
			reason = proof.Reason
		}
		if reason != "" {
			rejected = append(rejected, EvidenceRejection{"THEME", t.Text("theme_id"), []string{reason}})
		} else {
			groups[t.Text("theme_id")] = append(groups[t.Text("theme_id")], t)
		}
	}
	ids := []string{}
	for id := range groups {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := []Row{}
	for _, id := range ids {
		t, e := SelectRevision(groups[id])
		if e != nil {
			if e.Error() != "CONFLICTING_REVISION" {
				return nil, nil, e
			}
			rejected = append(rejected, EvidenceRejection{"THEME", id, []string{"SAM_THEME_REVISION_CONFLICT"}})
			continue
		}
		out = append(out, t)
	}
	return out, rejected, nil
}
