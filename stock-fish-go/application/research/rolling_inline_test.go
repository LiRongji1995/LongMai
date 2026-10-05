package research

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
)

// Compose a legal envelope while preserving the exact original inline object
// bytes. json.Marshal of RawMessage would compact the whitespace counterexample.
func rollingInlineBytes(t *testing.T, f Frozen, bars []json.RawMessage) []byte {
	t.Helper()
	f.Input.Bars = []Row{}
	raw := fixtureFrozenBytes(t, f)
	var array bytes.Buffer
	array.WriteByte('[')
	for index, row := range bars {
		if index > 0 {
			array.WriteByte(',')
		}
		array.Write(row)
	}
	array.WriteByte(']')
	needle := []byte(`"bars":[]`)
	if bytes.Count(raw, needle) != 1 {
		t.Fatal("TEST_FIXTURE inline boundary not unique")
	}
	return bytes.Replace(raw, needle, append([]byte(`"bars":`), array.Bytes()...), 1)
}
func appendRollingInlineDay(t *testing.T, f *Frozen, rows *[]Row, inline *[]json.RawMessage, step int) {
	t.Helper()
	before := len(*rows)
	appendRollingFixtureDay(t, f, rows, step)
	for _, row := range (*rows)[before:] {
		raw, err := json.Marshal(row)
		if err != nil {
			t.Fatal(err)
		}
		*inline = append(*inline, raw)
	}
}
func analyzeRollingInline(t *testing.T, f Frozen, raw []byte, h *rollingFixtureHistory, cache FactsCache, strategy, inputHash string) Diagnostic {
	t.Helper()
	out, err := analyzeStrategyFrozen(f, inputHash, h, fixtureBudget{}, strategy, cache, strings.Repeat("b", 64))
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestRollingDecodedHistoryBaseWithMultiDayInlineDelta(t *testing.T) {
	for _, strategy := range []string{R1StrategyID, V2StrategyID} {
		t.Run(strategy, func(t *testing.T) {
			f, rows := rollingFixtureBase()
			base, h := newRollingFixtureHistory(t, f, rows, nil)
			f.Input.HistoryRef, f.Input.HistoryHash = base.Input.HistoryRef, base.Input.HistoryHash
			cache := newRollingFixtureCache()
			raw := rollingInlineBytes(t, f, nil)
			decoded, err := DecodeFrozen(raw)
			if err != nil {
				t.Fatal(err)
			}
			cold := analyzeRollingInline(t, decoded, raw, h, cache, strategy, Hash(raw))
			if cold.FactRollingUpdates != 0 || cold.FactCacheMisses != 2 {
				t.Fatal("initial calculation bypassed independent full path")
			}
			var inline []json.RawMessage
			for step := 1; step <= 12; step++ {
				appendRollingInlineDay(t, &f, &rows, &inline, step)
				raw = rollingInlineBytes(t, f, inline)
				decoded, err = DecodeFrozen(raw)
				if err != nil {
					t.Fatal(err)
				}
				incremental := analyzeRollingInline(t, decoded, raw, h, cache, strategy, Hash(raw))
				full := analyzeRollingInline(t, decoded, raw, h, nil, strategy, Hash(raw))
				assertFullIncrementalEqual(t, incremental, full)
				if incremental.FactRollingUpdates != 2 || incremental.FactCacheMisses != 2 || incremental.FactCacheHits != 0 {
					t.Fatalf("step %d fixed history base/inline delta failed to roll: %d", step, incremental.FactRollingUpdates)
				}
				if incremental.HistoryHash != h.identity {
					t.Fatal("inline delta replaced history identity")
				}
				for _, fact := range incremental.Facts {
					if len(fact.EvidenceBindings) != 315 {
						t.Fatal("original fact window changed")
					}
					foundInline := false
					for _, binding := range fact.EvidenceBindings {
						if binding.Identity.Origin == "FROZEN_INPUT" {
							foundInline = true
							if !ev.MatchesDecodedInputBarBinding(decoded, Hash(raw), binding) {
								t.Fatal("reported inline bytes were not decoded from this input")
							}
						}
					}
					if !foundInline {
						t.Fatal("fixture did not exercise actual inline delta")
					}
				}
				if step == 6 {
					restart := newRollingFixtureCache()
					for key, value := range cache.states {
						restart.states[key] = value
					}
					cache = restart
				}
			}
		})
	}
}

func TestRollingInlineOriginalBytesAndParentInputProofFallback(t *testing.T) {
	for _, mode := range []string{"original_bar_whitespace_changed", "wrong_input_hash", "direct_in_memory_without_proof", "mutated_projection"} {
		t.Run(mode, func(t *testing.T) {
			f, rows := rollingFixtureBase()
			base, h := newRollingFixtureHistory(t, f, rows, nil)
			f.Input.HistoryRef, f.Input.HistoryHash = base.Input.HistoryRef, base.Input.HistoryHash
			cache := newRollingFixtureCache()
			var inline []json.RawMessage
			for step := 0; step <= 1; step++ {
				if step > 0 {
					appendRollingInlineDay(t, &f, &rows, &inline, step)
				}
				raw := rollingInlineBytes(t, f, inline)
				decoded, err := DecodeFrozen(raw)
				if err != nil {
					t.Fatal(err)
				}
				out := analyzeRollingInline(t, decoded, raw, h, cache, R1StrategyID, Hash(raw))
				if step == 1 && out.FactRollingUpdates != 2 {
					t.Fatal("fixture did not seed both inline recursive states")
				}
			}
			oldProjection := Row{}
			_ = json.Unmarshal(inline[0], &oldProjection)
			appendRollingInlineDay(t, &f, &rows, &inline, 2)
			expected := 0
			if mode == "original_bar_whitespace_changed" {
				previous := append([]byte{}, inline[0]...)
				inline[0] = bytes.Replace(inline[0], []byte("{"), []byte("{ "), 1)
				var next Row
				_ = json.Unmarshal(inline[0], &next)
				if Hash(previous) == Hash(inline[0]) || RowHash(oldProjection) != RowHash(next) {
					t.Fatal("fixture must change original bytes while preserving projection")
				}
				expected = 1
			}
			raw := rollingInlineBytes(t, f, inline)
			decoded, err := DecodeFrozen(raw)
			if err != nil {
				t.Fatal(err)
			}
			inputHash := Hash(raw)
			switch mode {
			case "wrong_input_hash":
				inputHash = strings.Repeat("a", 64)
			case "direct_in_memory_without_proof":
				if err = json.Unmarshal(raw, &decoded); err != nil {
					t.Fatal(err)
				}
				decoded.SourceProof = nil
			case "mutated_projection":
				fixtureSet(decoded.Input.Bars[0], "comparable_close", "13.23000000001")
				expected = 1
			}
			incremental := analyzeRollingInline(t, decoded, raw, h, cache, R1StrategyID, inputHash)
			full := analyzeRollingInline(t, decoded, raw, h, nil, R1StrategyID, inputHash)
			assertFullIncrementalEqual(t, incremental, full)
			if incremental.FactRollingUpdates != expected {
				t.Fatalf("unsafe original-byte/parent proof reused: %d want %d", incremental.FactRollingUpdates, expected)
			}
		})
	}
}

func TestIncrementalDecodedInlineRawBytesSameObservation(t *testing.T) {
	f := incrementalFixture()
	var original []json.RawMessage
	for _, row := range f.Input.Bars {
		raw, err := json.Marshal(row)
		if err != nil {
			t.Fatal(err)
		}
		original = append(original, raw)
	}
	firstRaw := rollingInlineBytes(t, f, original)
	first, err := DecodeFrozen(firstRaw)
	if err != nil {
		t.Fatal(err)
	}
	cache := fixtureFactCache{}
	algorithm := strings.Repeat("b", 64)
	if _, err = analyzeStrategyFrozen(first, Hash(firstRaw), nil, fixtureBudget{}, R1StrategyID, cache, algorithm); err != nil {
		t.Fatal(err)
	}
	original[100] = bytes.Replace(original[100], []byte("{"), []byte("{ "), 1)
	secondRaw := rollingInlineBytes(t, f, original)
	second, err := DecodeFrozen(secondRaw)
	if err != nil {
		t.Fatal(err)
	}
	if RowHash(first.Input.Bars[100]) != RowHash(second.Input.Bars[100]) {
		t.Fatal("fixture changed arithmetic projection")
	}
	incremental, err := analyzeStrategyFrozen(second, Hash(secondRaw), nil, fixtureBudget{}, R1StrategyID, cache, algorithm)
	if err != nil {
		t.Fatal(err)
	}
	full, err := analyzeStrategyFrozen(second, Hash(secondRaw), nil, fixtureBudget{}, R1StrategyID, nil, algorithm)
	if err != nil {
		t.Fatal(err)
	}
	assertFullIncrementalEqual(t, incremental, full)
	if incremental.FactCacheHits != 1 || incremental.FactCacheMisses != 1 {
		t.Fatal("original inline byte revision did not invalidate just its security", incremental.FactCacheHits, incremental.FactCacheMisses)
	}
}
