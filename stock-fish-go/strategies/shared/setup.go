package shared

import "sort"

func stageCondition(failed, unknown []string) Condition {
	if len(failed) > 0 {
		return Condition{Status: Fail, Reasons: failed}
	}
	if len(unknown) > 0 {
		return Condition{Status: Unknown, Reasons: unknown}
	}
	return C(Pass, "")
}
func Start(f Facts) Condition {
	if f.LegacyRS != nil {
		f.RS = f.LegacyRS
	}
	failed, unknown := []string{}, []string{}
	check := func(v Number, bad bool, missing, reason string) {
		if !v.Known() {
			unknown = append(unknown, missing)
		} else if bad {
			failed = append(failed, reason)
		}
	}
	check(f.AmountRatio, f.AmountRatio.Known() && f.AmountRatio.Cmp(N("1.2")) < 0, "SAM_START_AMOUNT_RATIO_UNAVAILABLE", "SAM_START_AMOUNT_RATIO_BELOW_SEED")
	check(f.Returns[1], !f.Returns[1].Positive(), "SAM_START_RET1_UNAVAILABLE", "SAM_START_RET1_NOT_POSITIVE")
	check(f.PriorReturn5, f.PriorReturn5.Known() && f.PriorReturn5.Cmp(N(".08")) > 0, "SAM_START_PRIOR_RET5_UNAVAILABLE", "SAM_START_PRIOR_STATE_ALREADY_EXPANDED")
	check(f.PriorTurnover, f.PriorTurnover.Known() && f.PriorTurnover.Cmp(N("2")) > 0, "SAM_START_PRIOR_TURNOVER_UNAVAILABLE", "SAM_START_PRIOR_HIGH_TURNOVER_ALREADY_EXPANDED")
	d := f.Close.Sub(f.PriorHigh)
	check(d, !d.Positive(), "SAM_START_BREAKOUT_REFERENCE_UNAVAILABLE", "SAM_START_BREAKOUT_NOT_CONFIRMED")
	check(f.RS[1], !f.RS[1].Positive(), "SAM_START_RS1_UNAVAILABLE", "SAM_START_RS1_NOT_POSITIVE")
	if f.Momentum == "UNKNOWN" || f.Momentum == "" {
		unknown = append(unknown, "SAM_START_SHARED_MOMENTUM_UNAVAILABLE")
	} else if f.Momentum != "RISING" {
		failed = append(failed, "SAM_START_SHARED_MOMENTUM_NOT_RISING")
	}
	return stageCondition(failed, unknown)
}
func HighLevelRepair(f Facts) Condition {
	if f.LegacyRS != nil {
		f.RS = f.LegacyRS
	}
	p := f.Path
	if !p.Available {
		c := C(Unknown, "SAM_HLR_DAILY_PATH_UNAVAILABLE")
		if len(p.Reasons) > 0 {
			c.Reasons = p.Reasons
		}
		return c
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
	failed, unknown := []string{}, []string{}
	if !p.Recovery.Known() {
		unknown = append(unknown, "SAM_HLR_RECOVERY_POINTS_NOT_MET")
	} else if !ge(p.Recovery, ".05") {
		failed = append(failed, "SAM_HLR_RECOVERY_POINTS_NOT_MET")
	}
	if !p.CLV.Known() {
		unknown = append(unknown, "SAM_HLR_CLV_REPAIR_NOT_MET")
	} else if !ge(p.CLV, ".45") {
		failed = append(failed, "SAM_HLR_CLV_REPAIR_NOT_MET")
	}
	if !p.CloseReturn.Known() {
		unknown = append(unknown, "SAM_HLR_CLOSE_RETURN_UNAVAILABLE")
	}
	if !positive(p.CloseReturn) && !positive(f.RS[1]) && !positive(f.SectorRS1) {
		if !p.CloseReturn.Known() && !f.RS[1].Known() && !f.SectorRS1.Known() {
			unknown = append(unknown, "SAM_HLR_TODAY_REPAIR_SUPPORT_UNAVAILABLE")
		} else {
			failed = append(failed, "SAM_HLR_TODAY_REPAIR_SUPPORT_NOT_MET")
		}
	}
	sort.Strings(failed)
	sort.Strings(unknown)
	return stageCondition(failed, unknown)
}

func Pullback(f Facts) Condition {
	if f.LegacyRS != nil {
		f.RS = f.LegacyRS
	}
	t := f.Trend
	if t.Condition.Status == Unknown {
		return t.Condition
	}
	if t.State != "STRONG_UPTREND" {
		return t.Condition
	}
	failed, unknown := []string{}, []string{}
	if !t.Days.Known() {
		unknown = append(unknown, "SAM_TREND_PULLBACK_DAYS_UNKNOWN")
	} else if t.Days.Cmp(N("1")) < 0 || t.Days.Cmp(N("3")) > 0 {
		failed = append(failed, "SAM_TREND_PULLBACK_DAYS_OUTSIDE_SEED")
	}
	if !t.Distance.Known() {
		unknown = append(unknown, "SAM_TREND_PULLBACK_DISTANCE_UNKNOWN")
	} else if t.Distance.Cmp(N(".03")) > 0 {
		failed = append(failed, "SAM_TREND_PULLBACK_NOT_NEAR_MA5_10_20")
	}
	if !f.AmountRatio.Known() {
		unknown = append(unknown, "SAM_TREND_PULLBACK_AMOUNT_CONTRACTION_UNAVAILABLE")
	} else if f.AmountRatio.Cmp(N(".9")) > 0 {
		failed = append(failed, "SAM_TREND_PULLBACK_AMOUNT_NOT_CONTRACTED")
	}
	if !f.RS[1].Known() && !f.SectorRS1.Known() {
		unknown = append(unknown, "SAM_TREND_PULLBACK_RESILIENCE_UNKNOWN")
	} else if (!f.RS[1].Known() || f.RS[1].Cmp(N("0")) < 0) && (!f.SectorRS1.Known() || f.SectorRS1.Cmp(N("0")) < 0) {
		failed = append(failed, "SAM_TREND_PULLBACK_RELATIVE_WEAKNESS_COUNTER")
	}
	return stageCondition(failed, unknown)
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
func SectorCompatibility(s SectorContext, setup string) Condition {
	c := C(Unknown, "SAM_SECTOR_CONTEXT_DATA_INCOMPLETE")
	label := "UNKNOWN"
	if s.Status == Pass {
		role := contains([]string{"LEADER", "CORE", "FIRST_TIER"}, s.Role)
		momentum := s.Momentum == "STRENGTHENING" || (setup == "TREND_PULLBACK" && s.Momentum == "STABLE") || (setup == "HIGH_LEVEL_REPAIR" && contains([]string{"STRENGTHENING", "STABLE", "WEAKENING"}, s.Momentum))
		if s.Level == "LOW" || (s.Momentum == "WEAKENING" && setup != "HIGH_LEVEL_REPAIR") {
			label = "NEGATIVE"
			c = C(Fail, "SAM_SECTOR_CONTEXT_NEGATIVE_FOR_SETUP")
		} else if s.Level == "HIGH" && momentum && role && (setup != "HIGH_LEVEL_REPAIR" || s.Breadth != "UNKNOWN") {
			label = "SUPPORTIVE"
			c = C(Pass, "")
		} else if s.Level == "UNKNOWN" || s.Momentum == "UNKNOWN" || s.Breadth == "UNKNOWN" || s.Role == "UNKNOWN" {
			c = C(Unknown, "SAM_SECTOR_CONTEXT_COMPONENT_UNKNOWN")
		} else {
			label = "NEUTRAL"
			c = C(Unknown, "SAM_SECTOR_CONTEXT_NEUTRAL_FOR_SETUP")
		}
	}
	c.Details = map[string]any{"context_status": label}
	return c
}
