package native

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
)

func TestCompactEvidenceRoundTripEqualAndDistinctRaw(t *testing.T) {
	raw := []byte(`{ "instrument_id":"TEST_FIXTURE", "session":"2026-01-01", "close":"+0010.00", "event_time":"2026-01-01T15:00:00Z", "source_published_at":"2026-01-01T15:00:00Z", "first_seen_at":"2026-01-01T15:00:00Z", "ingested_at":"2026-01-01T15:00:00Z" }`)
	row, e := ev.DecodeEvidenceRow(context.Background(), "bar", raw)
	if e != nil {
		t.Fatal(e)
	}
	projection := c.RowHash(row)
	normalized, e := json.Marshal(row)
	if e != nil {
		t.Fatal(e)
	}
	normalized, e = c.Canonical(normalized)
	if e != nil {
		t.Fatal(e)
	}
	if c.Hash(normalized) != projection || c.Hash(raw) == projection {
		t.Fatal("fixture does not distinguish source and projection identities")
	}
	parent := strings.Repeat("a", 64)
	for _, source := range [][]byte{raw, normalized} {
		hash := c.Hash(source)
		binding := []c.SelectedEvidenceBinding{{ProjectionHash: projection, Identity: c.EvidenceIdentity{Origin: "HISTORY_ROW", ParentHash: parent, RawSHA256: hash, Locator: hash, RawBytes: int64(len(source))}}}
		if e := compactEvidence(binding, []string{projection}, strings.Repeat("b", 64), parent); e != nil {
			t.Fatal(e)
		}
		saved, e := json.Marshal(binding)
		if e != nil {
			t.Fatal(e)
		}
		var decoded []c.SelectedEvidenceBinding
		if e = json.Unmarshal(saved, &decoded); e != nil {
			t.Fatal(e)
		}
		got := decoded[0]
		if got.ProjectionHash != "" || got.Identity.ParentHash != "" || got.Identity.Locator != "" || got.Identity.RawBytes != 0 {
			t.Fatal("shared identity was repeated")
		}
		recovered := got.Identity.RawSHA256
		if got.RawEqualsProjection {
			recovered = projection
		}
		if recovered != hash {
			t.Fatal("compact original bytes cannot be traced")
		}
		if got.RawEqualsProjection != (hash == projection) {
			t.Fatal("raw-equal marker is not exact")
		}
	}
}
func TestCompactEvidenceInlinePointerAndRejectsMissing(t *testing.T) {
	input := strings.Repeat("a", 64)
	projection := strings.Repeat("b", 64)
	b := []c.SelectedEvidenceBinding{{ProjectionHash: projection, Identity: c.EvidenceIdentity{Origin: "FROZEN_INPUT", ParentHash: input, Locator: "/input/bars/7"}}}
	if e := compactEvidence(b, []string{projection}, input, ""); e != nil {
		t.Fatal(e)
	}
	if b[0].Identity.Locator != "/input/bars/7" || b[0].Identity.RawSHA256 != "" || b[0].RawEqualsProjection {
		t.Fatal("inline raw pointer mutated into model digest")
	}
	tests := []struct {
		name     string
		bindings []c.SelectedEvidenceBinding
		hashes   []string
	}{
		{"missing", nil, []string{projection}},
		{"wrong_order", []c.SelectedEvidenceBinding{{ProjectionHash: input}}, []string{projection}},
		{"unbound", []c.SelectedEvidenceBinding{{ProjectionHash: projection, Identity: c.EvidenceIdentity{Origin: "UNBOUND_TEST_ADAPTER"}}}, []string{projection}},
		{"missing_history", []c.SelectedEvidenceBinding{{ProjectionHash: projection, Identity: c.EvidenceIdentity{Origin: "HISTORY_ROW"}}}, []string{projection}},
		{"missing_input_pointer", []c.SelectedEvidenceBinding{{ProjectionHash: projection, Identity: c.EvidenceIdentity{Origin: "FROZEN_INPUT", ParentHash: input}}}, []string{projection}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if e := compactEvidence(tt.bindings, tt.hashes, input, ""); e == nil {
				t.Fatal("unbound source accepted")
			}
		})
	}
}
