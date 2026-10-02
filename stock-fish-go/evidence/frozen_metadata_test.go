package evidence_test

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/native"
)

func frozenMetadataFixture(t *testing.T) map[string]any {
	t.Helper()
	raw, e := os.ReadFile("../core/testdata/input_contract_python.json")
	if e != nil {
		t.Fatal(e)
	}
	var v map[string]json.RawMessage
	if e = json.Unmarshal(raw, &v); e != nil {
		t.Fatal(e)
	}
	return map[string]any{"input": v["input"], "cutoff": v["cutoff"], "purpose": "TEST_FIXTURE_ENVELOPE"}
}
func TestFrozenEnvelopeMetadataDoesNotChangeProjectionOrRawIdentity(t *testing.T) {
	base := frozenMetadataFixture(t)
	raw, e := json.Marshal(base)
	if e != nil {
		t.Fatal(e)
	}
	model, e := ev.DecodeFrozen(raw)
	if e != nil {
		t.Fatal(e)
	}
	projection, e := ev.CanonicalInputProjection(context.Background(), model)
	if e != nil {
		t.Fatal(e)
	}
	for _, parent := range []map[string]any{
		{"file_sha256": strings.Repeat("a", 64), "path": "TEST_FIXTURE/parent.json", "purpose": "FROZEN_ORIGINAL"},
		{"namespace": "candidate_inputs", "external_id": "TEST_FIXTURE_INPUT", "raw_sha256": strings.Repeat("b", 64)},
	} {
		full := frozenMetadataFixture(t)
		full["parent_input"] = parent
		full["target_session"] = "2026-01-06"
		full["supplement"] = map[string]any{"actual_capture_clock_preserved": true, "added_bar_revisions": 2, "normalization_report": "TEST_FIXTURE/normalization.json"}
		original, e := json.MarshalIndent(full, "", "  ")
		if e != nil {
			t.Fatal(e)
		}
		original = append(original, '\n')
		decoded, e := ev.DecodeFrozen(original)
		if e != nil {
			t.Fatal(e)
		}
		got, e := ev.CanonicalInputProjection(context.Background(), decoded)
		if e != nil {
			t.Fatal(e)
		}
		if !bytes.Equal(got, projection) {
			t.Fatal("wrapper metadata altered market model projection")
		}
		engine := native.NativeEngine{}
		record, e := engine.Validate(original)
		if e != nil {
			t.Fatal(e)
		}
		if !bytes.Equal(record.Raw, original) || record.RawHash != c.Hash(original) {
			t.Fatal("frozen source bytes were stripped or reencoded")
		}
	}
	if ev.InputContractVersion != "GO_STRICT_MARKET_INPUT_V1" {
		t.Fatal("stored model projection version drifted")
	}
	if ev.FrozenEnvelopeContractVersion != "GO_FROZEN_ENVELOPE_V2" {
		t.Fatal("wrapper version not explicit")
	}
}
func TestFrozenEnvelopeMetadataStrictVariants(t *testing.T) {
	tests := []struct {
		name, key string
		value     any
	}{
		{"null_parent", "parent_input", nil},
		{"unknown_parent", "parent_input", map[string]any{"anything": "TEST_FIXTURE"}},
		{"file_missing_purpose", "parent_input", map[string]any{"file_sha256": strings.Repeat("a", 64), "path": "fixture"}},
		{"bad_file_hash", "parent_input", map[string]any{"file_sha256": "bad", "path": "fixture", "purpose": "TEST_FIXTURE"}},
		{"mixed_variants", "parent_input", map[string]any{"file_sha256": strings.Repeat("a", 64), "path": "fixture", "purpose": "TEST_FIXTURE", "namespace": "candidate_inputs"}},
		{"legacy_missing_id", "parent_input", map[string]any{"namespace": "candidate_inputs", "raw_sha256": strings.Repeat("a", 64)}},
		{"legacy_extra", "parent_input", map[string]any{"namespace": "candidate_inputs", "external_id": "fixture", "raw_sha256": strings.Repeat("a", 64), "extra": 1}},
		{"negative_count", "supplement", map[string]any{"actual_capture_clock_preserved": true, "added_bar_revisions": -1, "normalization_report": "fixture"}},
		{"string_bool", "supplement", map[string]any{"actual_capture_clock_preserved": "true", "added_bar_revisions": 1, "normalization_report": "fixture"}},
		{"supplement_extra", "supplement", map[string]any{"actual_capture_clock_preserved": true, "added_bar_revisions": 1, "normalization_report": "fixture", "extra": false}},
		{"invalid_date", "target_session", "2026-02-30"},
		{"null_date", "target_session", nil},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			v := frozenMetadataFixture(t)
			v[tt.key] = tt.value
			raw, _ := json.Marshal(v)
			if _, e := ev.DecodeFrozen(raw); e == nil {
				t.Fatal("invalid metadata accepted")
			}
		})
	}
}
