package daily

import (
	"context"
	"encoding/json"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/buildidentity"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/calendar"
	"time"
)

type Plan struct {
	Mode        string         `json:"mode"`
	Target      string         `json:"target_session"`
	Observation string         `json:"observation_date"`
	Cutoff      string         `json:"cutoff"`
	InputID     string         `json:"input_id"`
	RunID       string         `json:"matching_run_id"`
	Strategy    string         `json:"strategy_id"`
	Budget      int            `json:"budget_seconds"`
	Calendar    map[string]any `json:"calendar"`
	Blockers    []string       `json:"blockers"`
	Research    bool           `json:"would_research"`
	Capture     bool           `json:"would_capture"`
	Refresh     bool           `json:"refresh_requested"`
	RefreshCold bool           `json:"cold_history_capture_authorized"`
	InputChoice string         `json:"input_choice,omitempty"`
}

// Equal instants have no temporal ordering. Input ID, byte hash, then original
// cutoff text provide a stable choice without claiming one input is newer.
func latestEligibleInput(inputs []c.InputRecord, observation string, now time.Time) (c.InputRecord, bool) {
	closed, err := time.Parse(time.RFC3339, observation+"T15:00:00+08:00")
	if err != nil {
		return c.InputRecord{}, false
	}
	var selected c.InputRecord
	var selectedTime time.Time
	found := false
	for _, in := range inputs {
		cutoff, err := time.Parse(time.RFC3339Nano, in.Cutoff)
		if err != nil || in.ID == "" || in.Observation != observation || cutoff.After(now) || cutoff.Before(closed) {
			continue
		}
		tieBefore := in.ID < selected.ID || (in.ID == selected.ID && (in.RawHash < selected.RawHash || (in.RawHash == selected.RawHash && in.Cutoff < selected.Cutoff)))
		if !found || cutoff.After(selectedTime) || (cutoff.Equal(selectedTime) && tieBefore) {
			selected, selectedTime, found = in, cutoff, true
		}
	}
	return selected, found
}

func makePlan(ctx context.Context, b backend, conf Config, s, source string, hashes map[string]string, cal map[string]any, now time.Time) (Plan, error) {
	p := Plan{Mode: conf.Mode, Target: cal["target_session"].(string), Observation: cal["required_observation"].(string), Strategy: s, Budget: conf.Budget, Calendar: cal, Blockers: []string{}}
	if !cal["observation_complete"].(bool) {
		p.Blockers = append(p.Blockers, "REQUIRED_OBSERVATION_NOT_COMPLETE")
		return p, nil
	}
	inputs, e := b.Inputs(ctx, p.Observation)
	if e != nil {
		return p, e
	}
	inputHash := ""
	if in, ok := latestEligibleInput(inputs, p.Observation, now); ok {
		p.InputID, p.Cutoff = in.ID, in.Cutoff
		inputHash = in.RawHash
		p.InputChoice = "PARSED_INSTANT_DESC_ID_HASH_TEXT_ASC_ON_TIES"
	}
	if p.InputID == "" {
		p.Blockers = append(p.Blockers, "LOCAL_FROZEN_INPUT_UNAVAILABLE")
		return p, nil
	}
	for offset := 0; ; offset += 200 {
		var runs []c.Run
		if matching, ok := b.(interface {
			MatchingRuns(context.Context, string, string, int, int) ([]c.Run, error)
		}); ok {
			runs, e = matching.MatchingRuns(ctx, p.InputID, s, 200, offset)
		} else {
			runs, e = b.Runs(ctx, 200, offset)
		}
		if e != nil {
			return p, e
		}
		for _, r := range runs {
			if r.Strategy != s || r.InputID != p.InputID {
				continue
			}
			var m struct {
				InputHash   string `json:"input_bytes_hash"`
				Observation string `json:"observation_date"`
				Cutoff      string `json:"cutoff"`
				Target      string `json:"target_session"`
			}
			if json.Unmarshal(r.Manifest, &m) != nil {
				continue
			}
			if m.Observation != p.Observation || m.Cutoff != p.Cutoff || !buildidentity.Matches(r.Manifest, source, hashes) {
				continue
			}
			if inputHash == "" || m.InputHash != inputHash {
				continue
			}
			target := m.Target
			if target == "" {
				day, e := time.Parse(time.RFC3339, p.Observation+"T15:01:00+08:00")
				if e != nil {
					continue
				}
				derived, e := calendar.Official().Plan(day)
				if e != nil {
					continue
				}
				target = derived["target_session"].(string)
			}
			if target != p.Target {
				continue
			}
			p.RunID = r.ID
			return p, nil
		}
		if len(runs) < 200 {
			break
		}
	}
	p.Research = true
	if !buildidentity.Recorded(source) || !buildidentity.Recorded(buildidentity.Current()) {
		p.Blockers = append(p.Blockers, "RECORDED_BUILD_REQUIRED")
	}
	return p, nil
}
