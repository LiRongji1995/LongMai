package core

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
)

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
func TestStrictInputDefaultsNullTypesAndEnums(t *testing.T) {
	for _, tc := range []struct{ name, part, key, value, code string }{
		{"missing listed_on", "securities", "listed_on", "", "MISSING_FIELD"},
		{"null listed_on", "securities", "listed_on", "null", "NULL_NOT_ALLOWED"},
		{"bad date", "securities", "listed_on", `"2026-02-30"`, "INVALID_DATE"},
		{"bad timestamp", "bars", "event_time", `"2026-01-05T15:00:00"`, "EXPLICIT_TIMEZONE_REQUIRED"},
		{"unknown enum", "securities", "exchange", `"NYSE"`, "INVALID_ENUM"},
		{"bool string", "securities", "st", `"false"`, "BOOLEAN_REQUIRED"},
		{"bool integer", "securities", "st", `0`, "BOOLEAN_REQUIRED"},
		{"required nullable omitted", "securities", "st", "", "MISSING_FIELD"},
		{"null no default override", "bars", "limit_verified", "null", "NULL_NOT_ALLOWED"},
		{"null tick", "bars", "tick_size", "null", "NULL_NOT_ALLOWED"},
		{"zero price", "bars", "close", `"0"`, "NUMBER_OUT_OF_RANGE"},
		{"boolean amount", "bars", "amount_cny", `true`, "INVALID_DECIMAL"},
		{"rational raw", "bars", "close", `"1/2"`, "INVALID_DECIMAL"},
		{"turnover string", "bars", "turnover_fraction", `"0.1"`, "NUMBER_REQUIRED"},
		{"turnover too large", "bars", "turnover_fraction", `10.01`, "NUMBER_OUT_OF_RANGE"},
		{"integer fraction", "receipts", "row_count", `1.0`, "INTEGER_REQUIRED"},
		{"invalid hash", "receipts", "content_hash", `"BAD"`, "INVALID_SHA256"},
		{"invalid parameters", "receipts", "parameters", `{"nested":{}}`, "SCALAR_REQUIRED"},
		{"null members", "universe", "securities", `null`, "NULL_NOT_ALLOWED"},
		{"null optional array", "input", "themes", `null`, "NULL_NOT_ALLOWED"},
		{"unknown extra", "bars", "unexpected", `1`, "EXTRA_FIELD"},
		{"null ref element", "bars", "source_refs", `[null]`, "NULL_NOT_ALLOWED"},
		{"non string ref", "bars", "source_refs", `[1]`, "STRING_REQUIRED"},
		{"invalid clock order", "bars", "first_seen_at", `"2026-01-05T17:00:00+08:00"`, "INVALID_CLOCK_ORDER"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := contractFixture()
			p, save := fixturePart(t, f, tc.part)
			if tc.value == "" {
				delete(p, tc.key)
			} else {
				p[tc.key] = json.RawMessage(tc.value)
			}
			save()
			raw, _ := json.Marshal(f)
			_, e := DecodeFrozen(raw)
			if e == nil || !strings.Contains(e.Error(), tc.code) {
				t.Fatalf("got %v want %s", e, tc.code)
			}
		})
	}
	raw, _ := json.Marshal(contractFixture())
	f, e := DecodeFrozen(raw)
	if e != nil {
		t.Fatal(e)
	}
	var securities []Row
	_ = json.Unmarshal(f.Input.Universe["securities"], &securities)
	if securities[0].Bool("delisting_period") == nil || *securities[0].Bool("delisting_period") || f.Input.Bars[0].Text("quality") != "VALID" || f.Input.Bars[0].Num("tick_size").Cmp(N(".01")) != 0 {
		t.Fatal("defaults missing")
	}
	if f.Input.Bars[0].Num("open").Known() {
		t.Fatal("missing imputed zero")
	}
	if e = ValidateFrozen(&f); e != nil {
		t.Fatal("valid in-memory input rejected", e)
	}
	explicit := contractFixture()
	p, save := fixturePart(t, explicit, "securities")
	p["delisting_period"] = json.RawMessage("null")
	save()
	raw, _ = json.Marshal(explicit)
	f, e = DecodeFrozen(raw)
	if e != nil {
		t.Fatal(e)
	}
	_ = json.Unmarshal(f.Input.Universe["securities"], &securities)
	if securities[0].Bool("delisting_period") != nil {
		t.Fatal("explicit unknown replaced by false")
	}
}
func TestStrictInputRevisionAndReferenceRelations(t *testing.T) {
	for _, tc := range []struct {
		name   string
		change func(Row)
		code   string
	}{
		{"unsorted", func(in Row) { in["sessions"] = json.RawMessage(`["2026-01-05","2026-01-02"]`) }, "CALENDAR_NOT_SORTED_UNIQUE"},
		{"absent observation", func(in Row) { in["sessions"] = json.RawMessage(`["2026-01-02"]`) }, "OBSERVATION_SESSION_REQUIRED"},
		{"half ref", func(in Row) { in["history_artifact_ref"] = json.RawMessage(`"history_x"`) }, "HISTORY_REFERENCE_HASH_PAIR_REQUIRED"},
		{"wrong ref", func(in Row) {
			in["history_artifact_ref"] = json.RawMessage(`"history_x"`)
			in["history_artifact_hash"], _ = json.Marshal(strings.Repeat("a", 64))
		}, "HISTORY_REFERENCE_HASH_MISMATCH"},
		{"duplicate equal instant", func(in Row) {
			var a []Row
			_ = json.Unmarshal(in["bars"], &a)
			b := row(map[string]any{})
			for k, v := range a[0] {
				b[k] = v
			}
			b["ingested_at"] = json.RawMessage(`"2026-01-05T08:00:00Z"`)
			a = append(a, b)
			in["bars"], _ = json.Marshal(a)
		}, "DUPLICATE_BAR_REVISION"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := contractFixture()
			in, save := fixturePart(t, f, "input")
			tc.change(in)
			save()
			raw, _ := json.Marshal(f)
			_, e := DecodeFrozen(raw)
			if e == nil || !strings.Contains(e.Error(), tc.code) {
				t.Fatal(e)
			}
		})
	}
	raw, _ := json.Marshal(contractFixture())
	raw = []byte(strings.Replace(string(raw), `"cutoff":`, `"cutoff":"2026-01-05T20:00:00+08:00","cutoff":`, 1))
	if _, e := DecodeFrozen(raw); e == nil || !strings.Contains(e.Error(), "DUPLICATE_JSON_KEY") {
		t.Fatal("duplicate key", e)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, e := DecodeFrozenContext(ctx, raw); !errors.Is(e, context.Canceled) {
		t.Fatal("uncancelled input", e)
	}
}

func TestPythonContractDefaultProjection(t *testing.T) {
	raw, e := os.ReadFile("testdata/input_contract_python.json")
	if e != nil {
		t.Fatal(e)
	}
	var oracle struct {
		DataClass string          `json:"data_class"`
		Input     json.RawMessage `json:"input"`
		Cutoff    string          `json:"cutoff"`
		Hash      string          `json:"python_model_hash"`
	}
	if e = json.Unmarshal(raw, &oracle); e != nil {
		t.Fatal(e)
	}
	if oracle.DataClass != "TEST_FIXTURE" {
		t.Fatal("fixture class")
	}
	frozenRaw, _ := json.Marshal(map[string]any{"input": oracle.Input, "cutoff": oracle.Cutoff})
	f, e := DecodeFrozen(frozenRaw)
	if e != nil {
		t.Fatal(e)
	}
	canonical, e := CanonicalInputProjection(context.Background(), f)
	if e != nil {
		t.Fatal(e)
	}
	if got := Hash(canonical); got != oracle.Hash {
		t.Fatalf("Python default/decimal/datetime projection mismatch got=%s want=%s\n%s", got, oracle.Hash, canonical)
	}
}
