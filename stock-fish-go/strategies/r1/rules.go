package r1

import shared "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/strategies/shared"

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
func Identify(f Facts) map[string]Condition {
	return map[string]Condition{"START": shared.Start(f), "ACCELERATION": Acceleration(f), "HIGH_LEVEL_REPAIR": shared.HighLevelRepair(f), "TREND_PULLBACK": shared.Pullback(f)}
}
