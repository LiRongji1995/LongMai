package shared

import f "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"

func SectorSummary(rows []f.SectorContext, themesProvided bool) Condition {
	if len(rows) == 0 {
		if themesProvided {
			return C(Unknown, "SAM_NO_VISIBLE_AUTHORIZED_SECTOR_LINKAGE")
		}
		return C(Unknown, "SAM_SECTOR_DATA_UNAVAILABLE")
	}
	conditions := []Condition{}
	unknown := false
	for _, r := range rows {
		if r.Status == Pass {
			return C(Pass, "")
		}
		conditions = append(conditions, Condition{Status: r.Status, Reasons: r.Reasons})
		unknown = unknown || r.Status == Unknown
	}
	state := Fail
	if unknown {
		state = Unknown
	}
	return Condition{Status: state, Reasons: ReasonSet(conditions...)}
}
func FundsSummary(rows []f.FundsContext) Condition {
	if len(rows) == 0 {
		return C(Unknown, "SAM_FUNDS_WAITING_FOR_SECTOR_LINKAGE")
	}
	conditions := []Condition{}
	unknown := false
	for _, r := range rows {
		if r.Condition.Status == Pass {
			return C(Pass, "")
		}
		conditions = append(conditions, r.Condition)
		unknown = unknown || r.Condition.Status == Unknown
	}
	state := Fail
	if unknown {
		state = Unknown
	}
	return Condition{Status: state, Reasons: ReasonSet(conditions...)}
}
func CompleteBackground(bg f.Background, themesProvided bool) f.Background {
	bg.SectorCondition = SectorSummary(bg.Sectors, themesProvided)
	bg.FundsCondition = FundsSummary(bg.Funds)
	return bg
}
