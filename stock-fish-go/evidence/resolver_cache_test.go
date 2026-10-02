package evidence

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

func resolverCacheRow(t *testing.T, values map[string]any) c.Row {
	t.Helper()
	raw, err := json.Marshal(values)
	if err != nil {
		t.Fatal(err)
	}
	var row c.Row
	if err = json.Unmarshal(raw, &row); err != nil {
		t.Fatal(err)
	}
	return row
}

func TestCachedReceiptProofScopeAndMutableRawCompatibility(t *testing.T) {
	proof := map[string]any{"evidence_hash": strings.Repeat("a", 64), "policy_id": "TEST_FIXTURE", "source_url": "https://example.invalid/fixture", "event_time_basis": "SOURCE_DATED_EVENT_RECORD", "publication_time_basis": "SOURCE_PUBLICATION_TIMESTAMP", "verified_at": "2026-01-08T16:00:00+08:00", "applies_from": "2026-01-05", "applies_to": "2026-01-06", "evidence_refs": []string{"TEST_FIXTURE_PROOF"}}
	p := resolverCacheRow(t, map[string]any{"receipt_id": "TEST_FIXTURE_PARENT", "status": "COMPLETE", "pagination_complete": true, "event_time": "2026-01-07T15:00:00+08:00", "source_published_at": "2026-01-07T15:00:00+08:00", "first_seen_at": "2026-01-08T16:00:00+08:00", "ingested_at": "2026-01-08T16:00:00+08:00", "historical_time_proof": proof, "instrument_ids": []string{"A"}, "business_sessions": []string{"2026-01-05", "2026-01-06", "2026-01-07"}})
	r, err := NewResolver([]c.Row{p}, "2026-01-07T20:00:00+08:00", "RECONSTRUCTED_HISTORICAL", "2026-01-07", []string{"2026-01-05", "2026-01-06", "2026-01-07"})
	if err != nil {
		t.Fatal(err)
	}
	row := resolverCacheRow(t, map[string]any{"instrument_id": "A", "session": "2026-01-06", "source_refs": []string{"TEST_FIXTURE_PARENT"}, "event_time": "2026-01-07T15:00:00+08:00", "source_published_at": "2026-01-07T15:00:00+08:00", "first_seen_at": "2026-01-08T16:00:00+08:00", "ingested_at": "2026-01-08T16:00:00+08:00"})
	scope := Scope{Instrument: "A", Session: "2026-01-06", RequireSession: true}
	for i := 0; i < 5; i++ {
		if got := r.Resolve(row, scope); !got.Allowed || !got.Reconstructed {
			t.Fatal(got)
		}
	}
	row["session"] = json.RawMessage(`"2026-01-07"`)
	scope.Session = "2026-01-07"
	if got := r.Resolve(row, scope); got.Reason != "HISTORICAL_TIME_PROOF_INVALID_OR_OUTSIDE_SCOPE" {
		t.Fatal(got)
	}
	row["session"] = json.RawMessage(`"2026-01-06"`)
	scope.Session = "2026-01-06"
	// Mutating the existing byte slice must invalidate a parsed scope too.
	p["instrument_ids"][2] = 'B'
	if got := r.Resolve(row, scope); got.Reason != "SOURCE_INSTRUMENT_SCOPE_MISMATCH" {
		t.Fatal(got)
	}
	p["instrument_ids"][2] = 'A'
	if got := r.Resolve(row, scope); !got.Allowed {
		t.Fatal(got)
	}
	p["status"] = json.RawMessage(`"PARTIAL"`)
	scope.Complete = true
	if got := r.Resolve(row, scope); got.Reason != "SOURCE_NOT_COMPLETE" {
		t.Fatal(got)
	}
}

func TestResolverCacheConstructionHonorsCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := NewResolverContext(ctx, nil, "2026-01-07T20:00:00+08:00", "TEST_FIXTURE", "2026-01-07", nil); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}
