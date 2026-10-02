package sqlitev2

import (
	"encoding/json"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

func appItemSummary(item c.Item, ordinal int) ([]byte, string, int, error) {
	var envelope struct {
		Decision map[string]json.RawMessage `json:"decision"`
		Fact     map[string]json.RawMessage `json:"fact"`
	}
	if e := json.Unmarshal(item.Payload, &envelope); e != nil {
		return nil, "", 0, e
	}
	result := map[string]json.RawMessage{}
	result["instrument_id"], _ = json.Marshal(item.ID)
	for _, key := range []string{"strategy_id", "candidate_status", "decision_status", "matched_setups", "primary_stage", "alternate_stages", "observation_bucket", "rank", "setup_rank", "rank_scope", "watch_reasons", "reason_codes"} {
		if v, ok := envelope.Decision[key]; ok {
			result[key] = v
		}
	}
	for _, key := range []string{"name", "amount_cny", "turnover_fraction"} {
		if v, ok := envelope.Fact[key]; ok {
			result[key] = v
		}
	}
	status := "UNKNOWN"
	for _, key := range []string{"candidate_status", "decision_status"} {
		if v, ok := result[key]; ok {
			_ = json.Unmarshal(v, &status)
		}
	}
	rank := ordinal + 1
	if v, ok := result["rank"]; ok {
		var n int
		if json.Unmarshal(v, &n) == nil && n > 0 {
			rank = n
		}
	}
	raw, e := json.Marshal(result)
	return raw, status, rank, e
}
