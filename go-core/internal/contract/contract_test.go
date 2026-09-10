package contract

import (
	"bytes"
	"testing"
)

func TestContractRoundTripAndHash(t *testing.T) {
	facts := []Fact{
		{ContractVersion: Version, Instrument: "TEST000", Exchange: "SSE", Session: "2026-01-30", Eligibility: "ELIGIBLE", FieldGroup: "PRICE", Field: "close", Value: "10.00", ValueJSON: `"10.00"`, Status: "KNOWN", Provenance: map[string]interface{}{"source": "fixture"}},
		{ContractVersion: Version, Instrument: "TEST000", Exchange: "SSE", Session: "2026-01-30", Eligibility: "ELIGIBLE", FieldGroup: "ROLLING", Field: "MA20", Value: nil, ValueJSON: `null`, Status: "UNKNOWN", Provenance: map[string]interface{}{"source": "fixture"}},
	}
	var out bytes.Buffer
	if err := WriteFactsJSONL(&out, facts); err != nil {
		t.Fatal(err)
	}
	decoded, err := DecodeFactsJSONL(bytes.NewReader(out.Bytes()))
	if err != nil {
		t.Fatal(err)
	}
	if len(decoded) != len(facts) {
		t.Fatalf("decoded %d facts", len(decoded))
	}
	left, err := NormalizedHash(facts)
	if err != nil {
		t.Fatal(err)
	}
	right, err := NormalizedHash(decoded)
	if err != nil {
		t.Fatal(err)
	}
	if left != right {
		t.Fatalf("hash mismatch: %s != %s", left, right)
	}
}

func TestRejectsUnknownCoercedZero(t *testing.T) {
	err := ValidateFact(Fact{ContractVersion: Version, Instrument: "TEST000", Session: "2026-01-30", FieldGroup: "EVENT", Field: "close_limit_up", Value: false, ValueJSON: `false`, Status: "UNKNOWN", Provenance: map[string]interface{}{"source": "fixture"}})
	if err == nil {
		t.Fatal("expected validation error")
	}
}
