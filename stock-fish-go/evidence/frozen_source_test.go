package evidence_test

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
)

func TestDecodedFrozenOriginalBarIdentityAndInMemoryFallback(t *testing.T) {
	raw, err := json.MarshalIndent(frozenMetadataFixture(t), "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := ev.DecodeFrozenContext(context.Background(), raw)
	if err != nil {
		t.Fatal(err)
	}
	var envelope, input c.Row
	_ = json.Unmarshal(raw, &envelope)
	_ = json.Unmarshal(envelope["input"], &input)
	var original []json.RawMessage
	_ = json.Unmarshal(input["bars"], &original)
	if len(original) == 0 {
		t.Fatal("TEST_FIXTURE bars missing")
	}
	identity, ok := ev.DecodedInputBarIdentity(decoded, c.Hash(raw), 0)
	if !ok || identity.RawSHA256 != c.Hash(original[0]) || identity.ParentHash != c.Hash(raw) ||
		identity.RawBytes != int64(len(original[0])) || identity.Locator != "/input/bars/0" {
		t.Fatal("original serialized row not bound", identity, ok)
	}
	if identity.RawSHA256 == c.RowHash(decoded.Input.Bars[0]) {
		t.Fatal("original numeric/default bytes confused with validated projection")
	}
	if err = ev.ValidateFrozenContext(context.Background(), &decoded); err != nil {
		t.Fatal(err)
	}
	if next, ok := ev.DecodedInputBarIdentity(decoded, c.Hash(raw), 0); !ok || next != identity {
		t.Fatal("in-memory validation replaced original source proof")
	}
	if _, ok := ev.DecodedInputBarIdentity(decoded, strings.Repeat("a", 64), 0); ok {
		t.Fatal("different parent input hash accepted")
	}
	if _, ok := ev.DecodedInputBarIdentity(decoded, c.Hash(raw), -1); ok {
		t.Fatal("negative source pointer accepted")
	}
	decoded.Input.Bars[0]["quality"] = json.RawMessage(`"PARTIAL"`)
	if _, ok := ev.DecodedInputBarIdentity(decoded, c.Hash(raw), 0); ok {
		t.Fatal("mutated projection kept original proof")
	}
	var constructed c.Frozen
	if err = json.Unmarshal(raw, &constructed); err != nil {
		t.Fatal(err)
	}
	if err = ev.ValidateFrozenContext(context.Background(), &constructed); err != nil {
		t.Fatal(err)
	}
	if _, ok := ev.DecodedInputBarIdentity(constructed, c.Hash(raw), 0); ok {
		t.Fatal("convenience marshal masqueraded as a strict source decode")
	}
	constructed.SourceProof = map[string]any{"inputHash": c.Hash(raw), "rawHash": c.Hash(original[0])}
	if _, ok := ev.DecodedInputBarIdentity(constructed, c.Hash(raw), 0); ok {
		t.Fatal("claimed proof accepted without decoder attestation")
	}
	encoded, err := json.Marshal(constructed)
	if err != nil || strings.Contains(string(encoded), "SourceProof") || strings.Contains(string(encoded), "rawHash") {
		t.Fatal("private proof leaked into the immutable model", err)
	}
}
