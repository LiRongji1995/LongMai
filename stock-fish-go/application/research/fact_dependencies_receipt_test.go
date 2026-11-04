package research

import (
	"encoding/json"
	"strings"
	"testing"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

type receiptKeyFixture struct {
	frozen    Frozen
	state     Row
	selected  []Row
	bindings  []c.SelectedEvidenceBinding
	resolver  *Resolver
	algorithm string
}

func newReceiptKeyFixture(name string) receiptKeyFixture {
	f := Frozen{Purpose: "TEST_FIXTURE", Cutoff: "2026-01-05T20:00:00+08:00", Input: Input{Coverage: "TEST_FIXTURE", Observation: "2026-01-05", Sessions: []string{"2026-01-02", "2026-01-05"}, HistoryHash: strings.Repeat("c", 64)}}
	state := fixtureRow(map[string]any{"instrument_id": "TEST_FIXTURE_A.SH", "source_refs": []string{"TEST_FIXTURE_MASTER", "TEST_FIXTURE_MASTER"}})
	rows := []Row{
		fixtureRow(map[string]any{"session": "2026-01-02", "close": "10.00", "source_refs": []string{"TEST_FIXTURE_PRICE", "TEST_FIXTURE_PRICE", "TEST_FIXTURE_MASTER"}}),
		fixtureRow(map[string]any{"session": "2026-01-05", "close": "11.00", "source_refs": []string{"TEST_FIXTURE_MASTER", "TEST_FIXTURE_PRICE", "TEST_FIXTURE_MASTER"}}),
	}
	resolver := &Resolver{Receipts: map[string]Row{
		"TEST_FIXTURE_MASTER": fixtureRow(map[string]any{"receipt_id": "TEST_FIXTURE_MASTER", "field_version": "TEST_FIXTURE_V1", "content_hash": strings.Repeat("a", 64)}),
		"TEST_FIXTURE_PRICE":  fixtureRow(map[string]any{"receipt_id": "TEST_FIXTURE_PRICE", "field_version": "TEST_FIXTURE_V1", "content_hash": strings.Repeat("b", 64)}),
	}, Conflicts: map[string]bool{}}
	algorithm := strings.Repeat("d", 64)
	switch name {
	case "reordered_refs":
		fixtureSet(rows[0], "source_refs", []string{"TEST_FIXTURE_MASTER", "TEST_FIXTURE_PRICE", "TEST_FIXTURE_PRICE"})
		rows[0], rows[1] = rows[1], rows[0]
	case "missing_parent":
		delete(resolver.Receipts, "TEST_FIXTURE_PRICE")
	case "conflicting_parent":
		resolver.Conflicts["TEST_FIXTURE_PRICE"] = true
	case "empty_parent_hash":
		resolver.Receipts["TEST_FIXTURE_PRICE"]["bad"] = json.RawMessage(`{`)
	case "unrecorded_algorithm":
		algorithm = "UNRECORDED_TEST_FIXTURE"
	}
	bindings := []c.SelectedEvidenceBinding{}
	for _, row := range rows {
		bindings = append(bindings, c.SelectedEvidenceBinding{ProjectionHash: c.RowHash(row), Identity: c.EvidenceIdentity{Origin: "HISTORY_ROW", ParentHash: f.Input.HistoryHash, RawSHA256: strings.Repeat("e", 64)}})
	}
	if name == "unbound_evidence" {
		bindings = nil
	}
	return receiptKeyFixture{f, state, rows, bindings, resolver, algorithm}
}

func (f receiptKeyFixture) key() (string, string) {
	return factCacheKeyWithIdentity(f.algorithm, f.frozen, "TEST_FIXTURE_INPUT", "TEST_FIXTURE_A.SH", f.state, f.frozen.Input.Sessions, f.selected, f.bindings, f.resolver)
}

// These key/scope pairs were executed against the original fact_dependencies.go
// SHA256 315436703cccaed7d4dba8a405d8863cdd18424e2a82c917cea5a6a8d82ebb7c.
// Missing/conflicting/malformed parents here preserve the internal key boundary;
// they do not assert source admission. The real admission chain is tested below.
func TestFactCacheParentReceiptOriginalParity(t *testing.T) {
	for _, tc := range []struct{ name, key, scope string }{
		{"repeated_refs", "0712850948beaa57446ca15bad699572a3c99b98c28327901bd7a744934be4a5", "RESOLVED_STOCK_ARITHMETIC_CROSS_INPUT"},
		{"reordered_refs", "b459bfd20f9fec98d187fad6b1f5789b7a7b7ce48d1bf26c40f332a40eb0f261", "RESOLVED_STOCK_ARITHMETIC_CROSS_INPUT"},
		{"missing_parent", "e4d1f9aa39fa025612c5fbf40a4e704c231e0a54611c192a13f16036fe040b00", "RESOLVED_STOCK_ARITHMETIC_CROSS_INPUT"},
		{"conflicting_parent", "0712850948beaa57446ca15bad699572a3c99b98c28327901bd7a744934be4a5", "RESOLVED_STOCK_ARITHMETIC_CROSS_INPUT"},
		{"empty_parent_hash", "56df9247bd90c21309d240f937776a1a46ec54353aa3f5fe84b79d1e872590ac", "RESOLVED_STOCK_ARITHMETIC_CROSS_INPUT"},
		{"unrecorded_algorithm", "4de331ef2dcda3e7ad322cf030aa7254ff96cf0f0a7ca182da105cee4794d4a8", "SAME_INPUT_ONLY_UNRECORDED_ALGORITHM"},
		{"unbound_evidence", "3dc36c254f9fdb8e1905ee7a47ed9420962d47a51f68a059592ece41cb3cfcd3", "SAME_INPUT_ONLY_UNBOUND_EVIDENCE"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newReceiptKeyFixture(tc.name)
			key, scope := f.key()
			if key != tc.key || scope != tc.scope {
				t.Fatalf("key/scope %s %s, original %s %s", key, scope, tc.key, tc.scope)
			}
		})
	}
}

func TestFactCacheParentReceiptHashedOncePerCallIncludingEmptyHash(t *testing.T) {
	for _, name := range []string{"repeated_refs", "empty_parent_hash"} {
		t.Run(name, func(t *testing.T) {
			f := newReceiptKeyFixture(name)
			original := RowHash
			if name == "empty_parent_hash" && original(f.resolver.Receipts["TEST_FIXTURE_PRICE"]) != "" {
				t.Fatal("invalid JSON must exercise the actual empty RowHash")
			}
			calls := map[string]int{}
			RowHash = func(row Row) string {
				if id := row.Text("receipt_id"); id != "" {
					calls[id]++
				}
				return original(row)
			}
			t.Cleanup(func() { RowHash = original })
			first, firstScope := f.key()
			for _, id := range []string{"TEST_FIXTURE_MASTER", "TEST_FIXTURE_PRICE"} {
				if calls[id] != 1 {
					t.Fatalf("%s hashed %d times in one call", id, calls[id])
				}
			}
			second, secondScope := f.key()
			if first != second || firstScope != secondScope {
				t.Fatal("identical input changed key")
			}
			for _, id := range []string{"TEST_FIXTURE_MASTER", "TEST_FIXTURE_PRICE"} {
				if calls[id] != 2 {
					t.Fatalf("%s hash was retained across calls: %d", id, calls[id])
				}
			}
		})
	}
}

func TestFactCacheParentReceiptMutationsAcrossCalls(t *testing.T) {
	f := newReceiptKeyFixture("repeated_refs")
	prior, scope := f.key()
	checkChanged := func(label string) {
		t.Helper()
		current, currentScope := f.key()
		if current == prior || currentScope != scope {
			t.Fatalf("%s did not rebind current receipt", label)
		}
		again, againScope := f.key()
		if again != current || againScope != currentScope {
			t.Fatalf("%s produced unstable identity", label)
		}
		prior = current
	}
	receipt := f.resolver.Receipts["TEST_FIXTURE_PRICE"]
	fixtureSet(receipt, "field_version", "TEST_FIXTURE_V2")
	checkChanged("existing row field replacement")
	raw := receipt["content_hash"]
	raw[1] = 'f'
	if &raw[0] != &receipt["content_hash"][0] {
		t.Fatal("fixture must mutate the original byte slice")
	}
	checkChanged("existing raw byte slice mutation")
	replacement := fixtureRow(map[string]any{"receipt_id": "TEST_FIXTURE_PRICE", "field_version": "TEST_FIXTURE_V3", "content_hash": strings.Repeat("f", 64)})
	f.resolver.Receipts["TEST_FIXTURE_PRICE"] = replacement
	checkChanged("same reference replaced with another row")
	replacement["bad"] = json.RawMessage(`{`)
	checkChanged("valid to empty hash")
	delete(replacement, "bad")
	checkChanged("empty hash to valid")
}

func TestFactCacheParentReceiptGuardsBeforeWarmCache(t *testing.T) {
	// Sixty sessions preserve real eligibility while keeping this new targeted
	// admission regression bounded. Existing 320-session differential cases remain.
	base := incrementalFixture()
	base.Input.Sessions = base.Input.Sessions[260:]
	base.Input.Bars = append(append([]Row{}, base.Input.Bars[260:320]...), base.Input.Bars[580:640]...)
	algorithm := strings.Repeat("b", 64)
	warmed := fixtureFactCache{}
	cold := fixtureAnalyze(t, base, warmed, algorithm, R1StrategyID)
	if cold.FactCacheMisses != 2 || len(cold.Facts) != 2 {
		t.Fatal("fixture failed initial eligibility")
	}
	for _, tc := range []struct {
		name, ref, reason string
		conflict, future  bool
	}{
		{"missing_price", "TEST_FIXTURE_PRICE_A", "SOURCE_REFERENCE_UNRESOLVED", false, false},
		{"conflicting_price", "TEST_FIXTURE_PRICE_A", "SOURCE_REFERENCE_CONFLICT", true, false},
		{"future_price", "TEST_FIXTURE_PRICE_A", "EVENT_OR_PUBLICATION_NOT_VISIBLE", false, true},
		{"missing_master", "TEST_FIXTURE_MASTER", "", false, false},
		{"conflicting_master", "TEST_FIXTURE_MASTER", "", true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := cloneFrozen(base)
			for ix, parent := range f.Input.Receipts {
				if parent.Text("receipt_id") != tc.ref {
					continue
				}
				if tc.conflict {
					duplicate := Row{}
					for key, raw := range parent {
						duplicate[key] = append(json.RawMessage(nil), raw...)
					}
					fixtureSet(duplicate, "field_version", "TEST_FIXTURE_CONFLICT")
					f.Input.Receipts = append(f.Input.Receipts, duplicate)
				} else if tc.future {
					fixtureSet(parent, "source_published_at", f.Input.Observation+"T21:00:00+08:00")
				} else {
					f.Input.Receipts = append(f.Input.Receipts[:ix], f.Input.Receipts[ix+1:]...)
				}
				break
			}
			cache := fixtureFactCache{}
			for key, value := range warmed {
				cache[key] = append([]byte(nil), value...)
			}
			cached := fixtureAnalyze(t, f, cache, algorithm, R1StrategyID)
			full := fixtureAnalyze(t, f, nil, algorithm, R1StrategyID)
			assertFullIncrementalEqual(t, cached, full)
			if tc.ref == "TEST_FIXTURE_MASTER" {
				if len(cached.Qualification.Eligible) != 0 || len(cached.Qualification.Unknown) != 2 || cached.Qualification.UniverseComplete || len(cached.Facts) != 0 || cached.FactCacheHits != 0 {
					t.Fatalf("invalid master became usable: %+v", cached.Qualification)
				}
				return
			}
			if cached.FactCacheHits != 1 {
				t.Fatalf("unaffected B should remain independently reusable: %d hits", cached.FactCacheHits)
			}
			checked := false
			for _, fact := range cached.Facts {
				if fact.ID != "TEST_FIXTURE_A.SH" {
					continue
				}
				checked = true
				if fact.Rejected[tc.reason] != 60 || len(fact.EvidenceBindings) != 0 || len(fact.EvidenceHashes) != 0 || fact.Facts.Close.Known() {
					t.Fatalf("unusable parent reached facts: rejected=%v bindings=%d hashes=%d close=%v", fact.Rejected, len(fact.EvidenceBindings), len(fact.EvidenceHashes), fact.Facts.Close)
				}
			}
			if !checked {
				t.Fatal("fixture A rejection was not examined")
			}
		})
	}
}
