// Package calendar adapts the versioned, finite official exchange calendar.
package calendar

import (
	_ "embed"
	"encoding/json"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"time"
)

//go:embed exchange_calendar_2026.json
var raw []byte

type Calendar struct {
	Start   string      `json:"coverage_start"`
	End     string      `json:"coverage_end"`
	Closed  [][2]string `json:"closed_ranges"`
	Sources []string    `json:"sources"`
}

func Official() Calendar {
	var cal Calendar
	if err := json.Unmarshal(raw, &cal); err != nil {
		panic(err)
	}
	return cal
}
func (cal Calendar) session(day time.Time) (bool, error) {
	s := day.Format("2006-01-02")
	if s < cal.Start || s > cal.End {
		return false, errors.New("OFFICIAL_CALENDAR_COVERAGE_UNAVAILABLE")
	}
	if day.Weekday() == time.Saturday || day.Weekday() == time.Sunday {
		return false, nil
	}
	for _, span := range cal.Closed {
		if s >= span[0] && s <= span[1] {
			return false, nil
		}
	}
	return true, nil
}
func (cal Calendar) Plan(now time.Time) (map[string]any, error) {
	loc := time.FixedZone("Asia/Shanghai", 8*60*60)
	now = now.In(loc)
	day := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, loc)
	target := day
	open, e := cal.session(day)
	if e != nil {
		return nil, e
	}
	if !open || !now.Before(day.Add(9*time.Hour+30*time.Minute)) {
		target = target.AddDate(0, 0, 1)
	}
	for {
		yes, e := cal.session(target)
		if e != nil {
			return nil, e
		}
		if yes {
			break
		}
		target = target.AddDate(0, 0, 1)
	}
	completed := day
	if !open || now.Before(day.Add(15*time.Hour)) {
		completed = completed.AddDate(0, 0, -1)
	}
	for {
		yes, e := cal.session(completed)
		if e != nil {
			return nil, e
		}
		if yes {
			break
		}
		completed = completed.AddDate(0, 0, -1)
	}
	return map[string]any{"shanghai_now": now.Format(time.RFC3339Nano), "target_session": target.Format("2006-01-02"), "latest_completed_session": completed.Format("2006-01-02"), "earliest_trade_at": target.Add(9*time.Hour + 30*time.Minute).Format(time.RFC3339), "calendar_raw_sha256": c.Hash(raw), "calendar_coverage_end": cal.End, "calendar_sources": cal.Sources}, nil
}
