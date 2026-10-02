package research

import (
	"encoding/json"
	"sort"
)

type Support struct {
	CoreEvaluable bool     `json:"core_facts_evaluable"`
	CoreTruth     State    `json:"core_truth"`
	Decidable     bool     `json:"selection_decidable"`
	Truth         State    `json:"selection_truth"`
	Background    string   `json:"background_coverage"`
	Missing       []string `json:"background_missing"`
	Reasons       []string `json:"reason_codes"`
}
type Coverage struct {
	Contract              string             `json:"contract"`
	CandidateAvailability string             `json:"candidate_availability"`
	Universe              string             `json:"universe_coverage"`
	Selection             string             `json:"selection_decidability"`
	Required              string             `json:"required_data_coverage"`
	Background            string             `json:"background_coverage"`
	Evaluation            string             `json:"selection_evaluation_status"`
	Support               map[string]Support `json:"support"`
	Counts                map[string]int     `json:"counts"`
}

func scanBackground(v any, path string, missing *[]string) {
	switch x := v.(type) {
	case map[string]any:
		state, _ := x["status"].(string)
		if state == "" {
			state, _ = x["ecosystem_state"].(string)
		}
		if contains([]string{"UNKNOWN", "UNAVAILABLE", "NOT_QUERIED", "INCOMPLETE", "MISSING"}, state) {
			*missing = append(*missing, path)
		}
		if x["applicable"] == false || contains([]string{"NOT_APPLICABLE", "N/A"}, state) {
			return
		}
		if x["query_complete"] == false {
			*missing = append(*missing, path)
		}
		for k, y := range x {
			if contains([]string{"records", "sector_candidates", "funds_candidates"}, k) {
				if a, ok := y.([]any); ok && len(a) == 0 && x["query_complete"] != true {
					*missing = append(*missing, path+":"+k)
				}
			}
			if contains([]string{"reason_codes", "source_refs", "evidence_refs", "warnings", "counterevidence"}, k) {
				continue
			}
			scanBackground(y, path+":"+k, missing)
		}
	case []any:
		for i, y := range x {
			scanBackground(y, path+":"+count(i).String(), missing)
		}
	}
}
func EvaluateCoverage(rows []Decision, q Qualification) Coverage {
	c := Coverage{Contract: "R1_FACT_SELECTION_COVERAGE_V3", CandidateAvailability: "ABSENT", Universe: "COMPLETE", Selection: "COMPLETE", Required: "SUFFICIENT_FOR_DECISION", Background: "NO_RECORDED_GAP", Evaluation: "EVALUABLE", Support: map[string]Support{}, Counts: map[string]int{}}
	if !q.UniverseComplete || len(q.Unknown) > 0 || q.Absent > 0 {
		c.Universe = "INCOMPLETE"
	}
	for _, d := range rows {
		c.Counts[d.CandidateStatus]++
		coreSetups := map[string]Condition{}
		full := map[string]Condition{}
		for s, setup := range d.Setups {
			parts := map[string]Condition{}
			for k, v := range setup.Parts {
				if k != "critical" && k != "market" && k != "theme_funds" {
					parts[k] = v
				}
			}
			coreSetups[s] = All(parts)
			full[s] = setup
		}
		core := All(map[string]Condition{"common": d.Common, "setups": Any(coreSetups)}).Status
		truth := All(map[string]Condition{"common": d.Common, "setups": Any(full)}).Status
		s := Support{CoreEvaluable: core != Unknown, CoreTruth: core, Decidable: truth != Unknown, Truth: truth, Background: "NO_RECORDED_GAP", Missing: []string{}, Reasons: []string{"REQUIRED_FACTS_UNRESOLVED"}}
		raw, _ := json.Marshal(map[string]any{"market_status": map[string]any{"status": d.Background.Market.Status, "ecosystem_state": d.Background.Market.State, "reason_codes": d.Background.Market.Reasons}, "sector_candidates": d.Background.Sectors, "funds_candidates": d.Background.Funds, "supply_pressure": d.Background.Supply, "trend_strength": d.Background.Trend.Condition})
		var bg any
		_ = json.Unmarshal(raw, &bg)
		scanBackground(bg, "background", &s.Missing)
		sort.Strings(s.Missing)
		if len(s.Missing) > 0 {
			s.Background = "INCOMPLETE"
			c.Background = "INCOMPLETE"
		}
		if truth == Fail {
			s.Reasons = []string{"KNOWN_FAILURE_SHORT_CIRCUIT"}
		} else if truth == Pass {
			s.Reasons = []string{"COMPLETE_PASSING_PATH"}
			c.CandidateAvailability = "PRESENT"
		}
		if !s.Decidable {
			c.Selection = "INCOMPLETE"
			c.Required = "INCOMPLETE"
		}
		c.Support[d.InstrumentID] = s
	}
	if c.Universe != "COMPLETE" || c.Selection != "COMPLETE" {
		c.Evaluation = "NOT_EVALUABLE_AS_COMPLETE_STRATEGY"
	}
	return c
}
func EvaluateV2Coverage(rows []V2Decision, q Qualification) Coverage {
	c := Coverage{Contract: "R1_FACT_SELECTION_COVERAGE_V3", CandidateAvailability: "ABSENT", Universe: "COMPLETE", Selection: "COMPLETE", Required: "SUFFICIENT_FOR_DECISION", Background: "NO_RECORDED_GAP", Evaluation: "EVALUABLE", Support: map[string]Support{}, Counts: map[string]int{}}
	if !q.UniverseComplete || len(q.Unknown) > 0 || q.Absent > 0 {
		c.Universe = "INCOMPLETE"
	}
	for _, r := range rows {
		core := map[string]Condition{}
		all := map[string]Condition{}
		for k, v := range r.Conditions {
			if contains([]string{"liquidity_bottom", "trend_state", "setup_identification", "behavioral_confirmation", "market_compatibility", "same_theme_context", "critical_counterevidence"}, k) {
				all[k] = v
			}
			if contains([]string{"liquidity_bottom", "trend_state", "setup_identification", "behavioral_confirmation"}, k) {
				core[k] = v
			}
		}
		truth := All(all).Status
		ct := All(core).Status
		s := Support{CoreEvaluable: ct != Unknown, CoreTruth: ct, Decidable: truth != Unknown, Truth: truth, Missing: []string{}, Background: "NO_RECORDED_GAP"}
		for k, v := range map[string]Condition{"sector_context": r.Background.SectorCondition, "capital_linkage": r.Background.FundsCondition} {
			if v.Status == Unknown {
				s.Missing = append(s.Missing, "background:"+k)
			}
		}
		sort.Strings(s.Missing)
		s.Reasons = []string{"REQUIRED_FACTS_UNRESOLVED"}
		if truth == Fail {
			s.Reasons = []string{"KNOWN_FAILURE_SHORT_CIRCUIT"}
		} else if truth == Pass {
			s.Reasons = []string{"COMPLETE_PASSING_PATH"}
		}
		if len(s.Missing) > 0 {
			s.Background = "INCOMPLETE"
			c.Background = "INCOMPLETE"
		}
		if !s.Decidable {
			c.Selection = "INCOMPLETE"
			c.Required = "INCOMPLETE"
		}
		if r.Status == "FOCUS" {
			c.CandidateAvailability = "PRESENT"
		}
		c.Counts[r.Status]++
		c.Support[r.InstrumentID] = s
	}
	if c.Universe != "COMPLETE" || c.Selection != "COMPLETE" {
		c.Evaluation = "NOT_EVALUABLE_AS_COMPLETE_STRATEGY"
	}
	return c
}
