package legacy

import (
	"encoding/json"
	"strings"
	"testing"
)

func row(v any) Row { raw, _ := json.Marshal(v); var r Row; _ = json.Unmarshal(raw, &r); return r }
func contractFixture() Row {
	clock := func() map[string]any {
		return map[string]any{"event_time": "2026-01-05T15:00:00+08:00", "source_published_at": "2026-01-05T15:00:00+08:00", "first_seen_at": "2026-01-05T16:00:00+08:00", "ingested_at": "2026-01-05T16:00:00+08:00"}
	}
	sec := clock()
	for k, v := range map[string]any{"instrument_id": "TEST_FIXTURE.SZ", "exchange": "SZSE", "board": "MAIN", "security_type": "A_COMMON", "listed_on": "2020-01-01", "effective_from": "2020-01-01", "st": false, "suspended": false, "source_refs": []string{"TEST_FIXTURE"}} {
		sec[k] = v
	}
	bar := clock()
	for k, v := range map[string]any{"instrument_id": "TEST_FIXTURE.SZ", "session": "2026-01-05", "close": "10.00", "source_refs": []string{"TEST_FIXTURE"}} {
		bar[k] = v
	}
	receipt := clock()
	for k, v := range map[string]any{"receipt_id": "TEST_FIXTURE", "provider": "TEST_FIXTURE", "dataset": "TEST_FIXTURE", "field_version": "TEST_FIXTURE_V1", "content_hash": strings.Repeat("a", 64), "row_count": 1, "pagination_complete": true, "status": "COMPLETE"} {
		receipt[k] = v
	}
	univ := clock()
	for k, v := range map[string]any{"snapshot_id": "TEST_FIXTURE", "securities": []any{sec}, "source_refs": []string{"TEST_FIXTURE"}} {
		univ[k] = v
	}
	return row(map[string]any{"cutoff": "2026-01-05T20:00:00+08:00", "input": map[string]any{"observation_date": "2026-01-05", "sessions": []string{"2026-01-05"}, "universe": univ, "bars": []any{bar}, "receipts": []any{receipt}, "coverage_type": "TEST_FIXTURE"}})
}
func fixturePart(t *testing.T, envelope Row, key string) (Row, func()) {
	t.Helper()
	var input Row
	_ = json.Unmarshal(envelope["input"], &input)
	if key == "input" {
		return input, func() { envelope["input"], _ = json.Marshal(input) }
	}
	if key == "universe" {
		var obj Row
		_ = json.Unmarshal(input[key], &obj)
		return obj, func() { input[key], _ = json.Marshal(obj); envelope["input"], _ = json.Marshal(input) }
	}
	if key == "securities" {
		var universe Row
		_ = json.Unmarshal(input["universe"], &universe)
		var a []Row
		_ = json.Unmarshal(universe[key], &a)
		return a[0], func() {
			universe[key], _ = json.Marshal(a)
			input["universe"], _ = json.Marshal(universe)
			envelope["input"], _ = json.Marshal(input)
		}
	}
	var a []Row
	_ = json.Unmarshal(input[key], &a)
	return a[0], func() { input[key], _ = json.Marshal(a); envelope["input"], _ = json.Marshal(input) }
}
