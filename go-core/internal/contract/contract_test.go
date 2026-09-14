package contract

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func fixture() Fact {
	unit := "CNY"
	return Fact{ContractVersion: Version, SchemaRevision: SchemaRevision, Instrument: "000001.SZ", Exchange: "SZSE", Session: "2026-01-30", Eligibility: "ELIGIBLE", FieldGroup: "PRICE", Field: "close", Value: json.Number("10"), ValueJSON: "10", Status: "KNOWN", Unit: &unit, Provenance: map[string]interface{}{"status": "KNOWN", "pit_status": "VISIBLE", "source_refs": []string{"TEST_FIXTURE"}, "source_revision_id": "TEST_FIXTURE_REV", "visible_at": "2026-01-30T15:00:00+08:00", "decision_cutoff": "2026-01-30T20:30:00+08:00"}}
}
func TestContractRoundTripAndHash(t *testing.T) {
	facts := []Fact{fixture()}
	var out bytes.Buffer
	if err := WriteFactsJSONL(&out, facts); err != nil {
		t.Fatal(err)
	}
	decoded, err := DecodeFactsJSONL(bytes.NewReader(out.Bytes()))
	if err != nil {
		t.Fatal(err)
	}
	left, _ := NormalizedHash(facts)
	right, _ := NormalizedHash(decoded)
	if left != right {
		t.Fatal("hash mismatch")
	}
}
func TestRejectMutations(t *testing.T) {
	for _, mutate := range []func(*Fact){func(f *Fact) { f.Value = 999 }, func(f *Fact) { f.SchemaRevision = "old" }, func(f *Fact) { f.Status = "UNKNOWN" }, func(f *Fact) { f.Unit = nil }, func(f *Fact) { f.Provenance["status"] = "UNVERIFIED" }, func(f *Fact) { f.Provenance["visible_at"] = "2026-02-01T15:00:00+08:00" }, func(f *Fact) { f.Value = true; f.ValueJSON = "true" }, func(f *Fact) { f.ExactValue = map[string]string{"numerator": "20", "denominator": "1"} }} {
		f := fixture()
		mutate(&f)
		if ValidateFact(f) == nil {
			t.Fatalf("mutation accepted: %+v", f)
		}
	}
}
func TestDuplicateKeysRejected(t *testing.T) {
	facts := []Fact{fixture(), fixture()}
	var out bytes.Buffer
	if WriteFactsJSONL(&out, facts) == nil {
		t.Fatal("accepted duplicate")
	}
	if _, err := NormalizedHash(facts); err == nil {
		t.Fatal("hashed duplicate")
	}
}

func TestStrictJSONRejectsDuplicateMembersAtEveryDepth(t *testing.T) {
	for _, raw := range []string{
		`[{"close":999,"close":10}]`,
		`{"provenance":{"status":"UNVERIFIED","status":"KNOWN"}}`,
		`{"a":1,"\u0061":2}`,
		`{"a":1} {"b":2}`,
	} {
		var value interface{}
		if DecodeJSONStrict([]byte(raw), &value) == nil {
			t.Fatalf("accepted ambiguous JSON: %s", raw)
		}
	}
	fact, err := json.Marshal(fixture())
	if err != nil {
		t.Fatal(err)
	}
	duplicate := strings.Replace(string(fact), `"value":10`, `"value":999,"value":10`, 1)
	if _, err := DecodeFactsJSONL(strings.NewReader(duplicate)); err == nil {
		t.Fatal("contract JSONL accepted duplicate value member")
	}
	var precise map[string]interface{}
	if err := DecodeJSONStrict([]byte(`{"value":0.12345678901234567890123456789}`), &precise); err != nil {
		t.Fatal(err)
	}
	if precise["value"] != json.Number("0.12345678901234567890123456789") {
		t.Fatal("typed decoder lost decimal precision")
	}
}

func TestStrictProvenanceAndDecimalTypes(t *testing.T) {
	for _, refs := range []interface{}{"TEST_FIXTURE", []interface{}{123}, []string{""}} {
		f := fixture()
		f.Provenance["source_refs"] = refs
		if ValidateFact(f) == nil {
			t.Fatal("accepted invalid source_refs")
		}
	}
	f := fixture()
	f.Provenance["source_revision_id"] = ""
	if ValidateFact(f) == nil {
		t.Fatal("accepted empty source identity")
	}
	for _, value := range []string{"1_0", "01", "+10", "1/2", "0x10", " 10"} {
		f := fixture()
		f.Value = value
		raw, _ := json.Marshal(value)
		f.ValueJSON = string(raw)
		if ValidateFact(f) == nil {
			t.Fatalf("accepted invalid decimal %q", value)
		}
	}
}
