package core

import (
	"encoding/json"
	"testing"
)

func TestPartialFieldsAndParentAuthority(t *testing.T) {
	p := receipt()
	p["status"] = json.RawMessage(`"PARTIAL"`)
	p["pagination_complete"] = json.RawMessage(`false`)
	r, e := NewResolver([]Row{p}, "2026-01-05T20:00:00+08:00", "TEST_FIXTURE", "2026-01-05", []string{"2026-01-05"})
	if e != nil {
		t.Fatal(e)
	}
	x := evidence()
	x["close"] = json.RawMessage(`"10"`)
	s := Scope{Instrument: "TEST_FIXTURE.SZ", Session: "2026-01-05", Fields: []string{"close"}}
	if !r.Resolve(x, s).Allowed {
		t.Fatal("present partial price blocked", r.Resolve(x, s))
	}
	s.Fields = []string{"amount_cny"}
	if got := r.Resolve(x, s); got.Allowed || got.Reason != "REQUESTED_FIELD_UNAVAILABLE" {
		t.Fatal(got)
	}
	x["amount_cny"] = json.RawMessage(`null`)
	if r.Resolve(x, s).Allowed {
		t.Fatal("null partial field authorized")
	}
	s.Fields = nil
	s.Complete = true
	if got := r.Resolve(x, s); got.Allowed || got.Reason != "SOURCE_NOT_COMPLETE" {
		t.Fatal(got)
	}
	s.Complete = false
	p["status"] = json.RawMessage(`"COMPLETE"`)
	p["pagination_complete"] = json.RawMessage(`true`)
	for _, tc := range []struct{ field, value, reason string }{
		{"business_sessions", `["2026-01-02"]`, "SOURCE_SESSION_SCOPE_MISMATCH"},
		{"instrument_ids", `["OTHER.SZ"]`, "SOURCE_INSTRUMENT_SCOPE_MISMATCH"},
		{"source_published_at", `"2026-01-06T15:00:00+08:00"`, "EVENT_OR_PUBLICATION_NOT_VISIBLE"},
		{"first_seen_at", `"2026-01-05T16:00:00+08:00"`, "EVENT_OR_PUBLICATION_NOT_VISIBLE"},
	} {
		t.Run(tc.field, func(t *testing.T) {
			old, exists := p[tc.field]
			p[tc.field] = json.RawMessage(tc.value)
			got := r.Resolve(x, s)
			if exists {
				p[tc.field] = old
			} else {
				delete(p, tc.field)
			}
			if got.Allowed || got.Reason != tc.reason {
				t.Fatal(got)
			}
		})
	}
	p["first_seen_at"] = json.RawMessage(`"2026-01-05T16:00:00+08:00"`)
	p["ingested_at"] = p["first_seen_at"]
	if got := r.Resolve(x, s); got.Allowed || got.Reason != "ROW_CAPTURE_PRECEDES_PARENT_RECEIPT" {
		t.Fatal(got)
	}
}
func TestReconstructionCannotOverrideFuturePublicationOrCoverage(t *testing.T) {
	p := receipt()
	x := evidence()
	x["ingested_at"] = json.RawMessage(`"2026-01-06T15:00:00+08:00"`)
	for _, coverage := range []string{"TEST_FIXTURE", "PROSPECTIVE_CAPTURED", "UNVERIFIABLE_AS_OF"} {
		r, _ := NewResolver([]Row{p}, "2026-01-05T20:00:00+08:00", coverage, "2026-01-05", []string{"2026-01-05"})
		if r.Resolve(x, Scope{}).Allowed {
			t.Fatal("late capture was authorized", coverage)
		}
	}
	r, _ := NewResolver([]Row{p}, "2026-01-05T20:00:00+08:00", "RECONSTRUCTED_HISTORICAL", "2026-01-05", []string{"2026-01-05"})
	if got := r.Resolve(x, Scope{}); !got.Allowed || !got.Reconstructed {
		t.Fatal("prior captured source should permit reconstructed transform", got)
	}
	x["source_published_at"] = json.RawMessage(`"2026-01-06T15:00:00+08:00"`)
	if r.Resolve(x, Scope{}).Allowed {
		t.Fatal("future publication bypassed")
	}
}
func TestReceiptConflictInvalidDateAndSortedSourceIdentity(t *testing.T) {
	p := receipt()
	other := receipt()
	other["status"] = json.RawMessage(`"PARTIAL"`)
	r, _ := NewResolver([]Row{p, other}, "2026-01-05T20:00:00+08:00", "TEST_FIXTURE", "2026-01-05", []string{"2026-01-05"})
	if got := r.Resolve(evidence(), Scope{}); got.Allowed || got.Reason != "SOURCE_REFERENCE_CONFLICT" {
		t.Fatal(got)
	}
	r, _ = NewResolver([]Row{p}, "2026-01-05T20:00:00+08:00", "TEST_FIXTURE", "2026-01-05", []string{"2026-01-05"})
	x := evidence()
	x["effective_from"] = json.RawMessage(`"2026-02-30"`)
	if r.Resolve(x, Scope{}).Allowed {
		t.Fatal("invalid date accepted")
	}
	delete(x, "effective_from")
	x["source_refs"] = json.RawMessage(`["TEST_FIXTURE","TEST_FIXTURE"]`)
	got := r.Resolve(x, Scope{})
	if !got.Allowed || len(got.Sources) != 1 {
		t.Fatal(got)
	}
}
