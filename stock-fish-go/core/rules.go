package core

// Facts is the typed R1 rule input. Public fact computation has no dependency on
// V2's decisions. These primitives are not a claim of full pipeline parity.
type Facts struct {
	LegacyRS map[int]Number

	Returns, RS                                                                                                  map[int]Number
	Momentum                                                                                                     string
	RecentTurnover, PriorTurnover, PriorReturn5, PriorReturn10, PriorPercentile10, PriorHigh, Close, AmountRatio Number
	PriorLimitUp                                                                                                 bool
	Path                                                                                                         Path
	SectorRS1                                                                                                    Number
	Trend                                                                                                        Trend
}

func Acceleration(f Facts) Condition {
	p := map[string]Condition{"absolute_strength": Any(map[string]Condition{"ret5": Compare(f.Returns[5], N(".15"), false, "RET5"), "ret10": Compare(f.Returns[10], N(".25"), false, "RET10")}), "rs5": Compare(f.RS[5], N("0"), true, "RS5"), "persistence": Compare(f.RecentTurnover, N("3"), false, "TURNOVER_5_INCLUDING_T")}
	s := Fail
	if f.Momentum == "UNKNOWN" || f.Momentum == "" {
		s = Unknown
	} else if f.Momentum == "RISING" {
		s = Pass
	}
	p["momentum"] = C(s, "MOMENTUM_NOT_RISING")
	for _, n := range []int{1, 3} {
		key := "1"
		if n == 3 {
			key = "3"
		}
		p["ret"+key] = Compare(f.Returns[n], N("0"), true, "RET"+key)
		p["rs"+key] = Compare(f.RS[n], N("0"), true, "RS"+key)
	}
	return All(p)
}
func MA20Trend(close, current, previous Number) Condition {
	return All(map[string]Condition{"close_above_ma20": Compare(close.Sub(current), N("0"), true, "C_GT_MA20"), "ma20_slope": Compare(current.Sub(previous), N("0"), false, "MA20_NONDECREASING")})
}
func Start(f Facts) Condition {
	if f.LegacyRS != nil {
		f.RS = f.LegacyRS
	}
	momentum := C(Fail, "SAM_START_SHARED_MOMENTUM_NOT_RISING")
	if f.Momentum == "UNKNOWN" || f.Momentum == "" {
		momentum = C(Unknown, "SAM_START_SHARED_MOMENTUM_UNAVAILABLE")
	} else if f.Momentum == "RISING" {
		momentum = C(Pass, "")
	}
	return All(map[string]Condition{"amount": Compare(f.AmountRatio, N("1.2"), false, "SAM_START_AMOUNT_RATIO"), "ret1": Compare(f.Returns[1], N("0"), true, "SAM_START_RET1"), "prior_ret5": Compare(N(".08").Sub(f.PriorReturn5), N("0"), false, "SAM_START_PRIOR_STATE_ALREADY_EXPANDED"), "prior_turnover": Compare(N("2").Sub(f.PriorTurnover), N("0"), false, "SAM_START_PRIOR_HIGH_TURNOVER_ALREADY_EXPANDED"), "breakout": Compare(f.Close.Sub(f.PriorHigh), N("0"), true, "SAM_START_BREAKOUT"), "rs1": Compare(f.RS[1], N("0"), true, "SAM_START_RS1"), "momentum": momentum})
}
func HighLevelRepair(f Facts) Condition {
	if f.LegacyRS != nil {
		f.RS = f.LegacyRS
	}
	p := f.Path
	if !p.Available {
		return C(Unknown, "SAM_HLR_DAILY_PATH_UNAVAILABLE")
	}
	if p.CloseLimitDown == "TRUE" {
		return C(Fail, "SAM_HLR_CLOSE_LIMIT_DOWN_VERIFIED")
	}
	ge := func(v Number, t string) bool { return v.Known() && v.Cmp(N(t)) >= 0 }
	positive := func(v Number) bool { return v.Positive() }
	high := ge(f.PriorReturn5, ".10") || ge(f.PriorReturn10, ".18") || ge(f.PriorPercentile10, "80") || f.PriorLimitUp
	if !high {
		if !f.PriorReturn5.Known() && !f.PriorReturn10.Known() && !f.PriorPercentile10.Known() {
			return C(Unknown, "SAM_HLR_HIGH_LEVEL_CONTEXT_UNAVAILABLE")
		}
		return C(Fail, "SAM_HLR_HIGH_LEVEL_CONTEXT_NOT_MET")
	}
	divergence := ge(p.Amplitude, ".07") || (p.LowReturn.Known() && p.LowReturn.Cmp(N("-.04")) <= 0) || p.TouchedLimitDown == "TRUE"
	if !divergence {
		return C(Fail, "SAM_HLR_DIVERGENCE_PATH_NOT_MET")
	}
	support := C(Fail, "SAM_HLR_TODAY_REPAIR_SUPPORT_NOT_MET")
	if positive(p.CloseReturn) || positive(f.RS[1]) || positive(f.SectorRS1) {
		support = C(Pass, "")
	} else if !p.CloseReturn.Known() && !f.RS[1].Known() && !f.SectorRS1.Known() {
		support = C(Unknown, "SAM_HLR_TODAY_REPAIR_SUPPORT_UNAVAILABLE")
	}
	closeKnown := C(Pass, "")
	if !p.CloseReturn.Known() {
		closeKnown = C(Unknown, "SAM_HLR_CLOSE_RETURN_UNAVAILABLE")
	}
	return All(map[string]Condition{"recovery": Compare(p.Recovery, N(".05"), false, "SAM_HLR_RECOVERY_POINTS_NOT_MET"), "clv": Compare(p.CLV, N(".45"), false, "SAM_HLR_CLV_REPAIR_NOT_MET"), "support": support, "close_known": closeKnown})
}
func Pullback(f Facts) Condition {
	if f.LegacyRS != nil {
		f.RS = f.LegacyRS
	}
	t := f.Trend
	if t.Condition.Status == Unknown {
		return C(Unknown, "SAM_TREND_PULLBACK_TREND_UNKNOWN")
	}
	if t.State != "STRONG_UPTREND" {
		return C(Fail, "SAM_TREND_PULLBACK_TREND_FAIL")
	}
	rs := C(Unknown, "SAM_TREND_PULLBACK_RESILIENCE_UNKNOWN")
	if f.RS[1].Known() || f.SectorRS1.Known() {
		rs = C(Fail, "SAM_TREND_PULLBACK_RELATIVE_WEAKNESS_COUNTER")
		for _, v := range []Number{f.RS[1], f.SectorRS1} {
			if v.Known() && v.Cmp(N("0")) >= 0 {
				rs = C(Pass, "")
			}
		}
	}
	return All(map[string]Condition{"min_days": Compare(t.Days, N("1"), false, "SAM_TREND_PULLBACK_DAYS"), "max_days": Compare(N("3").Sub(t.Days), N("0"), false, "SAM_TREND_PULLBACK_DAYS"), "distance": Compare(N(".03").Sub(t.Distance), N("0"), false, "SAM_TREND_PULLBACK_DISTANCE"), "contraction": Compare(N(".9").Sub(f.AmountRatio), N("0"), false, "SAM_TREND_PULLBACK_AMOUNT"), "resilience": rs})
}
func WeakToStrong(p Path) Condition {
	if !p.Available {
		return C(Unknown, "SAM_INTRADAY_PATH_UNAVAILABLE")
	}
	if p.LowReturn.Known() && p.LowReturn.Cmp(N("0")) < 0 && p.CloseReturn.Positive() && p.Recovery.Positive() {
		return C(Pass, "")
	}
	return C(Fail, "SAM_INTRADAY_WEAK_TO_STRONG_NOT_CONFIRMED_FROM_DAILY_OHLC")
}
func Identify(f Facts) map[string]Condition {
	return map[string]Condition{"START": Start(f), "ACCELERATION": Acceleration(f), "HIGH_LEVEL_REPAIR": HighLevelRepair(f), "TREND_PULLBACK": Pullback(f)}
}
