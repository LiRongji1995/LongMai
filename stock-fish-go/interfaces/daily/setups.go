package daily

import (
	"encoding/json"
	"strings"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

type savedSetups struct {
	Name            string
	Status          string
	Matched         []string
	MatchedJSON     string
	Primary         string
	AlternateJSON   string
	StageStatesJSON string
	Warnings        []string
	V2              bool
}

// R1's saved matched_setups is authoritative and remains byte-for-byte intact.
// V2's selected primary/alternates are distinct from its candidate condition
// tree: only a selected stage with a saved PASS can be displayed as matched.
func setupSelection(item c.Item) savedSetups {
	var payload struct {
		Fact struct {
			Name string `json:"name"`
		} `json:"fact"`
		Decision struct {
			Candidate string          `json:"candidate_status"`
			Status    string          `json:"decision_status"`
			Matched   json.RawMessage `json:"matched_setups"`
			Primary   *string         `json:"primary_stage"`
			Alternate json.RawMessage `json:"alternate_stages"`
			Stages    map[string]struct {
				Status c.State `json:"status"`
			} `json:"stage_candidates"`
		} `json:"decision"`
	}
	if err := json.Unmarshal(item.Payload, &payload); err != nil {
		return savedSetups{MatchedJSON: "null", Warnings: []string{"SAVED_SETUP_METADATA_INVALID"}}
	}
	d := payload.Decision
	view := savedSetups{Name: payload.Fact.Name, Status: d.Candidate, Matched: []string{}, Warnings: []string{}}
	if view.Status != "" || (d.Status == "" && len(d.Matched) > 0) {
		view.MatchedJSON = string(d.Matched)
		if len(d.Matched) > 0 && string(d.Matched) != "null" {
			if err := json.Unmarshal(d.Matched, &view.Matched); err != nil {
				view.Matched = nil
				view.Warnings = append(view.Warnings, "SAVED_R1_MATCHED_SETUPS_INVALID")
			}
		} else {
			view.Warnings = append(view.Warnings, "SAVED_R1_MATCHED_SETUPS_NOT_RECORDED")
		}
		return view
	}
	view.V2 = true
	view.Status = d.Status
	if d.Primary != nil {
		view.Primary = *d.Primary
	} else {
		view.Warnings = append(view.Warnings, "SAVED_V2_PRIMARY_STAGE_NOT_RECORDED")
	}
	view.AlternateJSON = string(d.Alternate)
	var alternate []string
	if len(d.Alternate) > 0 && string(d.Alternate) != "null" {
		if err := json.Unmarshal(d.Alternate, &alternate); err != nil {
			alternate = nil
			view.Warnings = append(view.Warnings, "SAVED_V2_ALTERNATE_STAGES_INVALID")
		}
	} else {
		view.Warnings = append(view.Warnings, "SAVED_V2_ALTERNATE_STAGES_NOT_RECORDED")
	}
	states := map[string]c.State{}
	for stage, condition := range d.Stages {
		states[stage] = condition.Status
	}
	if d.Stages != nil {
		raw, _ := json.Marshal(states)
		view.StageStatesJSON = string(raw)
	}
	selected := []string{}
	if view.Primary != "" {
		selected = append(selected, view.Primary)
	}
	selected = append(selected, alternate...)
	seen := map[string]bool{}
	for _, stage := range selected {
		if stage == "" {
			view.Warnings = append(view.Warnings, "SAVED_V2_SELECTED_STAGE_EMPTY")
			continue
		}
		if stage != "START" && stage != "ACCELERATION" && stage != "HIGH_LEVEL_REPAIR" && stage != "TREND_PULLBACK" {
			view.Warnings = append(view.Warnings, "SAVED_V2_SELECTED_STAGE_NAME_UNKNOWN:"+stage)
			continue
		}
		if seen[stage] {
			view.Warnings = append(view.Warnings, "SAVED_V2_SELECTED_STAGE_DUPLICATE:"+stage)
			continue
		}
		seen[stage] = true
		state, exists := states[stage]
		if !exists || state == "" {
			view.Warnings = append(view.Warnings, "SAVED_V2_SELECTED_STAGE_STATUS_NOT_RECORDED:"+stage)
			continue
		}
		if state != c.Pass {
			view.Warnings = append(view.Warnings, "SAVED_V2_SELECTED_STAGE_NOT_PASS:"+stage+":"+string(state))
			continue
		}
		view.Matched = append(view.Matched, stage)
	}
	if d.Stages == nil {
		view.Warnings = append(view.Warnings, "SAVED_V2_STAGE_CANDIDATES_NOT_RECORDED")
	}
	if len(view.Matched) == 0 && len(view.Warnings) > 0 {
		view.MatchedJSON = "null"
	} else {
		raw, _ := json.Marshal(view.Matched)
		view.MatchedJSON = string(raw)
	}
	return view
}

func (s savedSetups) warningsText() string { return strings.Join(s.Warnings, "; ") }
