package evidence

import (
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

func Qualify(state contracts.Row, observation string, sessions []string) string {
	if state == nil {
		return "UNKNOWN_IDENTITY_OR_CONFLICT"
	}
	for _, k := range []string{"exchange", "board", "security_type"} {
		if state.Text(k) == "UNKNOWN" || state.Text(k) == "" {
			return "UNKNOWN_IDENTITY"
		}
	}
	if !contracts.Contains([]string{"SSE", "SZSE"}, state.Text("exchange")) || state.Text("board") != "MAIN" || state.Text("security_type") != "A_COMMON" {
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
	if state.Text("listed_on") == "" {
		return "UNKNOWN_LISTING_HISTORY"
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
