package daily

import (
	"context"
	"encoding/json"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/calendar"
	"reflect"
	"strings"
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
	for _, in := range inputs {
		cut, e := time.Parse(time.RFC3339Nano, in.Cutoff)
		if e != nil || cut.After(now) {
			continue
		}
		closed, e := time.Parse(time.RFC3339, in.Observation+"T15:00:00+08:00")
		if e != nil || cut.Before(closed) {
			continue
		}
		p.InputID = in.ID
		p.Cutoff = in.Cutoff
		break
	}
	if p.InputID == "" {
		p.Blockers = append(p.Blockers, "LOCAL_FROZEN_INPUT_UNAVAILABLE")
		return p, nil
	}
	for offset := 0; ; offset += 200 {
		runs, e := b.Runs(ctx, 200, offset)
		if e != nil {
			return p, e
		}
		for _, r := range runs {
			if r.Strategy != s || r.InputID != p.InputID {
				continue
			}
			var m struct {
				InputHash   string            `json:"input_bytes_hash"`
				Observation string            `json:"observation_date"`
				Cutoff      string            `json:"cutoff"`
				Config      map[string]string `json:"config_hashes"`
				Source      string            `json:"go_source_hash"`
				Target      string            `json:"target_session"`
				Conversion  string            `json:"conversion_version"`
			}
			if json.Unmarshal(r.Manifest, &m) != nil {
				continue
			}
			if m.Observation != p.Observation || m.Cutoff != p.Cutoff || !reflect.DeepEqual(m.Config, hashes) || m.Source == "" || strings.HasPrefix(m.Source, "UNRECORDED") || m.Conversion != evidence.InputContractVersion {
				continue
			}
			inputHash := ""
			for _, in := range inputs {
				if in.ID == p.InputID {
					inputHash = in.RawHash
					break
				}
			}
			if m.InputHash != inputHash {
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
	if source == "" || strings.HasPrefix(source, "UNRECORDED") {
		p.Blockers = append(p.Blockers, "RECORDED_BUILD_REQUIRED")
	}
	return p, nil
}
