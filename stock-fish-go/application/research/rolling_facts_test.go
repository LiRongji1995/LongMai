package research

import (
	"context"
	"encoding/json"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"reflect"
	"strings"
	"testing"
	"time"
)

type rollingCacheEntry struct {
	Version, Dependencies, Hash string
	Raw                         []byte
}
type rollingFixtureCache struct {
	facts  fixtureFactCache
	states map[string]rollingCacheEntry
}

func newRollingFixtureCache() *rollingFixtureCache {
	return &rollingFixtureCache{fixtureFactCache{}, map[string]rollingCacheEntry{}}
}
func (m *rollingFixtureCache) GetFact(ctx context.Context, key string) (StockFacts, bool, error) {
	return m.facts.GetFact(ctx, key)
}
func (m *rollingFixtureCache) PutFact(ctx context.Context, key string, f StockFacts) error {
	return m.facts.PutFact(ctx, key, f)
}
func (m *rollingFixtureCache) CacheGet(_ context.Context, key, version, dependencies string) ([]byte, bool, error) {
	entry, ok := m.states[key]
	if !ok || entry.Version != version || entry.Dependencies != dependencies || Hash(entry.Raw) != entry.Hash {
		return nil, false, nil
	}
	return append([]byte{}, entry.Raw...), true, nil
}
func (m *rollingFixtureCache) CachePut(_ context.Context, key, version, dependencies string, raw []byte) error {
	m.states[key] = rollingCacheEntry{version, dependencies, Hash(raw), append([]byte{}, raw...)}
	return nil
}

type rollingFixtureHistory struct {
	rows     []Row
	raw      [][]byte
	identity string
}

func newRollingFixtureHistory(t *testing.T, f Frozen, rows []Row, suffix map[string]string) (Frozen, *rollingFixtureHistory) {
	t.Helper()
	h := &rollingFixtureHistory{}
	for _, row := range rows {
		raw, err := json.Marshal(row)
		if err != nil {
			t.Fatal(err)
		}
		raw = append(raw, []byte(suffix[row.Text("instrument_id")+":"+row.Text("session")])...)
		projected, err := DecodeEvidenceRow(context.Background(), "bar", raw)
		if err != nil {
			t.Fatal(err)
		}
		h.rows = append(h.rows, projected)
		h.raw = append(h.raw, raw)
	}
	encoded, _ := json.Marshal(h.raw)
	h.identity = Hash(encoded)
	f.Input.Bars = []Row{}
	f.Input.HistoryRef = "history_" + h.identity
	f.Input.HistoryHash = h.identity
	return f, h
}
func (h *rollingFixtureHistory) Identity() string { return h.identity }
func (h *rollingFixtureHistory) Receipts() []Row  { return nil }
func (h *rollingFixtureHistory) VerifyIndex() error {
	encoded, _ := json.Marshal(h.raw)
	if Hash(encoded) != h.identity || len(h.raw) != len(h.rows) {
		return errCode("TEST_FIXTURE_HISTORY_IDENTITY_MISMATCH")
	}
	return nil
}
func (h *rollingFixtureHistory) Bars(id, from, to string, consume func(Row) error) error {
	return h.BarsWithIdentity(id, from, to, func(row Row, _ c.EvidenceIdentity) error { return consume(row) })
}
func (h *rollingFixtureHistory) BarsWithIdentity(id, from, to string, consume func(Row, c.EvidenceIdentity) error) error {
	for i, row := range h.rows {
		if row.Text("instrument_id") != id || row.Text("session") < from || row.Text("session") > to {
			continue
		}
		identity := c.EvidenceIdentity{Origin: "HISTORY_ROW", ParentHash: h.identity, RawSHA256: Hash(h.raw[i]), Locator: Hash(h.raw[i]), RawBytes: int64(len(h.raw[i]))}
		if err := consume(row, identity); err != nil {
			return err
		}
	}
	return nil
}
func rollingFixtureBase() (Frozen, []Row) {
	f := incrementalFixture()
	rows := append([]Row{}, f.Input.Bars...)
	last, _ := time.Parse("2006-01-02", f.Input.Sessions[len(f.Input.Sessions)-1])
	for len(f.Input.Sessions) < 365 {
		last = last.AddDate(0, 0, 1)
		if last.Weekday() != time.Saturday && last.Weekday() != time.Sunday {
			f.Input.Sessions = append(f.Input.Sessions, last.Format("2006-01-02"))
		}
	}
	// Immutable source parents explicitly cover the calendar. A new day's price
	// below uses its own newly visible receipt; old parents are never rewritten.
	for _, receipt := range f.Input.Receipts {
		fixtureSet(receipt, "business_sessions", f.Input.Sessions)
	}
	for index, row := range rows {
		ix := index % 320
		cents := 1000 + ix
		if index >= 320 {
			cents += 100
		}
		decimal := func(value int) string { return fmt.Sprintf("%d.%02d", value/100, value%100) }
		fixtureSet(row, "open", decimal(cents-1))
		fixtureSet(row, "high", decimal(cents+4))
		fixtureSet(row, "low", decimal(cents-4))
		fixtureSet(row, "close", decimal(cents))
		fixtureSet(row, "comparable_close", decimal(cents))
		fixtureSet(row, "amount_cny", fmt.Sprintf("%d", 500000000+(ix%11)*19000001))
		fixtureSet(row, "turnover_fraction", json.Number([]string{"0.069999999", "0.07", "0.070000001"}[ix%3]))
	}
	f.Input.Bars = nil
	return f, rows
}
func appendRollingFixtureDay(t *testing.T, f *Frozen, rows *[]Row, step int) {
	t.Helper()
	index := -1
	for i, day := range f.Input.Sessions {
		if day == f.Input.Observation {
			index = i
			break
		}
	}
	if index < 0 || index+1 >= len(f.Input.Sessions) {
		t.Fatal("TEST_FIXTURE calendar exhausted")
	}
	oldDay, next := f.Input.Observation, f.Input.Sessions[index+1]
	f.Input.Observation, f.Cutoff = next, next+"T20:00:00+08:00"
	for _, field := range []string{"event_time", "source_published_at", "first_seen_at", "ingested_at"} {
		fixtureSet(f.Input.Universe, field, next+"T15:00:00+08:00")
	}
	for ix, id := range []string{"TEST_FIXTURE_A.SH", "TEST_FIXTURE_B.SH"} {
		var old Row
		for _, row := range *rows {
			if row.Text("instrument_id") == id && row.Text("session") == oldDay {
				old = row
			}
		}
		if old == nil {
			t.Fatal("TEST_FIXTURE previous row missing")
		}
		row := Row{}
		for key, value := range old {
			row[key] = append(json.RawMessage{}, value...)
		}
		reference := fmt.Sprintf("TEST_FIXTURE_NEW_%s_%s", id, next)
		receipt := fixtureRow(map[string]any{"receipt_id": reference, "provider": "TEST_FIXTURE", "dataset": "TEST_FIXTURE", "field_version": "TEST_FIXTURE_V1", "content_hash": strings.Repeat("a", 64), "row_count": 1, "pagination_complete": true, "status": "COMPLETE", "instrument_ids": []string{id}, "business_sessions": []string{next}})
		for _, field := range []string{"event_time", "source_published_at", "first_seen_at", "ingested_at"} {
			fixtureSet(row, field, next+"T15:00:00+08:00")
			fixtureSet(receipt, field, next+"T15:00:00+08:00")
		}
		fixtureSet(row, "session", next)
		fixtureSet(row, "source_refs", []string{reference})
		cents := 1320 + step*3 + ix*100 + []int{0, -4, 3, -2}[step%4]
		decimal := func(value int) string { return fmt.Sprintf("%d.%02d", value/100, value%100) }
		fixtureSet(row, "open", decimal(cents-2))
		fixtureSet(row, "high", decimal(cents+8))
		fixtureSet(row, "low", decimal(cents-8))
		fixtureSet(row, "close", decimal(cents))
		fixtureSet(row, "comparable_close", decimal(cents))
		fixtureSet(row, "reference_prev_close", old.Text("close"))
		fixtureSet(row, "amount_cny", fmt.Sprintf("%d", 610000001+step*13000003+ix*110001))
		fixtureSet(row, "turnover_fraction", json.Number([]string{"0.07", "0.069999999", "0.070000001", "0.08"}[step%4]))
		*rows = append(*rows, row)
		f.Input.Receipts = append(f.Input.Receipts, receipt)
	}
}
func analyzeRollingFixture(t *testing.T, f Frozen, h *rollingFixtureHistory, cache FactsCache, algorithm, strategy string) Diagnostic {
	t.Helper()
	encoded, _ := json.Marshal(f)
	out, err := analyzeStrategyFrozen(f, Hash(encoded), h, fixtureBudget{}, strategy, cache, algorithm)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestRollingMultiSessionIndependentFullDifferential(t *testing.T) {
	algorithm := strings.Repeat("b", 64)
	for _, strategy := range []string{R1StrategyID, V2StrategyID} {
		t.Run(strategy, func(t *testing.T) {
			f, rows := rollingFixtureBase()
			frozen, h := newRollingFixtureHistory(t, f, rows, nil)
			cache := newRollingFixtureCache()
			cold := analyzeRollingFixture(t, frozen, h, cache, algorithm, strategy)
			if cold.FactRollingUpdates != 0 || cold.FactCacheMisses != 2 {
				t.Fatal("cold path bypassed full", cold.FactRollingUpdates, cold.FactCacheMisses)
			}
			for step := 1; step <= 12; step++ {
				appendRollingFixtureDay(t, &f, &rows, step)
				frozen, h = newRollingFixtureHistory(t, f, rows, nil)
				incremental := analyzeRollingFixture(t, frozen, h, cache, algorithm, strategy)
				full := analyzeRollingFixture(t, frozen, h, nil, algorithm, strategy)
				assertFullIncrementalEqual(t, incremental, full)
				if incremental.FactRollingUpdates != 2 || incremental.FactCacheHits != 0 || incremental.FactCacheMisses != 2 {
					t.Fatalf("step %d did not use distinct next-session arithmetic: updates=%d hits/misses=%d/%d", step, incremental.FactRollingUpdates, incremental.FactCacheHits, incremental.FactCacheMisses)
				}
				for _, fact := range incremental.Facts {
					if len(fact.EvidenceBindings) != 315 || len(fact.EvidenceHashes) != 315 {
						t.Fatal("guard changed original published scope")
					}
				}
				// A reconstructed cache instance carries only serialized durable bytes.
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

func TestRollingRevisionBasisMembershipAndMissingFallback(t *testing.T) {
	algorithm := strings.Repeat("b", 64)
	for _, strategy := range []string{R1StrategyID, V2StrategyID} {
		for _, tc := range []struct {
			name    string
			modify  func(*Frozen, *[]Row, map[string]string)
			updates int
		}{
			{"historical_price_revision", func(_ *Frozen, rows *[]Row, _ map[string]string) {
				fixtureSet((*rows)[100], "comparable_close", "12.000000000000000001")
			}, 1},
			{"late_revision_selected", func(f *Frozen, rows *[]Row, _ map[string]string) {
				copy := Row{}
				for key, value := range (*rows)[100] {
					copy[key] = append(json.RawMessage{}, value...)
				}
				fixtureSet(copy, "comparable_close", "12.000000000000000001")
				for _, field := range []string{"source_published_at", "first_seen_at", "ingested_at"} {
					fixtureSet(copy, field, f.Input.Observation+"T19:00:00+08:00")
				}
				*rows = append(*rows, copy)
			}, 1},
			{"raw_bytes_only_revision", func(_ *Frozen, rows *[]Row, suffix map[string]string) {
				r := (*rows)[100]
				suffix[r.Text("instrument_id")+":"+r.Text("session")] = "  \n"
			}, 1},
			{"old_guard_revision", func(_ *Frozen, rows *[]Row, _ map[string]string) { fixtureSet((*rows)[5], "comparable_close", "10.06") }, 1},
			{"mixed_basis", func(_ *Frozen, rows *[]Row, _ map[string]string) {
				fixtureSet((*rows)[100], "comparable_basis", "TEST_FIXTURE_NEW_HFQ")
			}, 1},
			{"complete_basis_change", func(_ *Frozen, rows *[]Row, _ map[string]string) {
				for _, row := range *rows {
					if row.Text("instrument_id") == "TEST_FIXTURE_A.SH" {
						fixtureSet(row, "comparable_basis", "TEST_FIXTURE_NEW_HFQ")
					}
				}
			}, 1},
			{"parent_receipt_changed", func(f *Frozen, _ *[]Row, _ map[string]string) {
				fixtureSet(f.Input.Receipts[1], "field_version", "TEST_FIXTURE_V2")
			}, 1},
			{"old_price_removed", func(_ *Frozen, rows *[]Row, _ map[string]string) { *rows = append((*rows)[:100], (*rows)[101:]...) }, 1},
			{"current_price_missing", func(f *Frozen, rows *[]Row, _ map[string]string) {
				out := []Row{}
				for _, row := range *rows {
					if row.Text("instrument_id") != "TEST_FIXTURE_A.SH" || row.Text("session") != f.Input.Observation {
						out = append(out, row)
					}
				}
				*rows = out
			}, 1},
			{"new_receipt_not_visible", func(f *Frozen, _ *[]Row, _ map[string]string) {
				fixtureSet(f.Input.Receipts[len(f.Input.Receipts)-2], "source_published_at", f.Input.Observation+"T21:00:00+08:00")
			}, 1},
			{"calendar_prefix_changed", func(f *Frozen, _ *[]Row, _ map[string]string) {
				f.Input.Sessions = append(f.Input.Sessions[:80], f.Input.Sessions[81:]...)
			}, 0},
			{"security_name_changed", func(f *Frozen, _ *[]Row, _ map[string]string) {
				var states []Row
				_ = json.Unmarshal(f.Input.Universe["securities"], &states)
				fixtureSet(states[0], "name", "TEST_FIXTURE_NEW_NAME")
				fixtureSet(f.Input.Universe, "securities", states)
			}, 1},
			{"member_suspended", func(f *Frozen, _ *[]Row, _ map[string]string) {
				var states []Row
				_ = json.Unmarshal(f.Input.Universe["securities"], &states)
				fixtureSet(states[0], "suspended", true)
				fixtureSet(f.Input.Universe, "securities", states)
			}, 1},
			{"member_removed", func(f *Frozen, _ *[]Row, _ map[string]string) {
				var states []Row
				_ = json.Unmarshal(f.Input.Universe["securities"], &states)
				fixtureSet(f.Input.Universe, "securities", states[1:])
				fixtureSet(f.Input.Universe, "expected_count", 1)
			}, 1},
			{"member_added", func(f *Frozen, rows *[]Row, _ map[string]string) {
				var states []Row
				_ = json.Unmarshal(f.Input.Universe["securities"], &states)
				extra := Row{}
				for key, value := range states[0] {
					extra[key] = append(json.RawMessage{}, value...)
				}
				fixtureSet(extra, "instrument_id", "TEST_FIXTURE_C.SH")
				fixtureSet(extra, "name", "TEST_FIXTURE_C.SH")
				fixtureSet(f.Input.Universe, "securities", append(states, extra))
				fixtureSet(f.Input.Universe, "expected_count", 3)
				add := []Row{}
				for _, row := range *rows {
					if row.Text("instrument_id") != "TEST_FIXTURE_A.SH" || row.Text("session") == f.Input.Observation {
						continue
					}
					copy := Row{}
					for key, value := range row {
						copy[key] = append(json.RawMessage{}, value...)
					}
					fixtureSet(copy, "instrument_id", "TEST_FIXTURE_C.SH")
					add = append(add, copy)
				}
				// The new member's current row is covered by the shared frozen parent.
				var current Row
				for _, row := range *rows {
					if row.Text("instrument_id") == "TEST_FIXTURE_A.SH" && row.Text("session") == f.Input.Observation {
						current = row
					}
				}
				copy := Row{}
				for key, value := range current {
					copy[key] = append(json.RawMessage{}, value...)
				}
				fixtureSet(copy, "instrument_id", "TEST_FIXTURE_C.SH")
				fixtureSet(copy, "source_refs", []string{"TEST_FIXTURE_PRICE_A"})
				*rows = append(*rows, append(add, copy)...)
			}, 2},
		} {
			t.Run(strategy+"/"+tc.name, func(t *testing.T) {
				base, rows := rollingFixtureBase()
				frozen, h := newRollingFixtureHistory(t, base, rows, nil)
				cache := newRollingFixtureCache()
				analyzeRollingFixture(t, frozen, h, cache, algorithm, strategy)
				appendRollingFixtureDay(t, &base, &rows, 1)
				suffix := map[string]string{}
				tc.modify(&base, &rows, suffix)
				final, h := newRollingFixtureHistory(t, base, rows, suffix)
				incremental := analyzeRollingFixture(t, final, h, cache, algorithm, strategy)
				full := analyzeRollingFixture(t, final, h, nil, algorithm, strategy)
				assertFullIncrementalEqual(t, incremental, full)
				if incremental.FactRollingUpdates != tc.updates {
					t.Fatalf("updates=%d want=%d", incremental.FactRollingUpdates, tc.updates)
				}
			})
		}
	}
}

func TestRollingSkipSessionUnknownAlgorithmAndCorruptStateFallback(t *testing.T) {
	for _, mode := range []string{"skip_session", "unknown_algorithm", "changed_algorithm", "corrupt_state", "unbound_frozen_input"} {
		t.Run(mode, func(t *testing.T) {
			algorithm := strings.Repeat("b", 64)
			f, rows := rollingFixtureBase()
			frozen, h := newRollingFixtureHistory(t, f, rows, nil)
			cache := newRollingFixtureCache()
			analyzeRollingFixture(t, frozen, h, cache, algorithm, R1StrategyID)
			previousDay := f.Input.Observation
			appendRollingFixtureDay(t, &f, &rows, 1)
			expected := 0
			switch mode {
			case "skip_session":
				appendRollingFixtureDay(t, &f, &rows, 2)
			case "unknown_algorithm":
				algorithm = "UNRECORDED"
			case "changed_algorithm":
				algorithm = strings.Repeat("c", 64)
			case "corrupt_state":
				key := rollingStateKey(algorithm, f.Input.Coverage, "TEST_FIXTURE_A.SH", previousDay)
				if err := cache.CachePut(context.Background(), key, rollingFactContract, key, []byte("{")); err != nil {
					t.Fatal(err)
				}
				expected = 1
			}
			frozen, h = newRollingFixtureHistory(t, f, rows, nil)
			if mode == "unbound_frozen_input" {
				frozen.Input.HistoryRef, frozen.Input.HistoryHash = "", ""
				frozen.Input.Bars = rows
				encoded, _ := json.Marshal(frozen)
				incremental, err := analyzeStrategyFrozen(frozen, Hash(encoded), nil, fixtureBudget{}, R1StrategyID, cache, algorithm)
				if err != nil {
					t.Fatal(err)
				}
				// Both paths intentionally receive the same unbound in-memory
				// input. A real DecodeFrozen would authenticate a different
				// serialized identity and add original-byte evidence to only full.
				full, err := analyzeStrategyFrozen(frozen, Hash(encoded), nil, fixtureBudget{}, R1StrategyID, nil, algorithm)
				if err != nil {
					t.Fatal(err)
				}
				assertFullIncrementalEqual(t, incremental, full)
				if incremental.FactCacheScope != "SAME_INPUT_ONLY_UNBOUND_EVIDENCE" {
					t.Fatal("unbound input incorrectly claimed cross-input arithmetic proof", incremental.FactCacheScope)
				}
				for _, fact := range incremental.Facts {
					for _, binding := range fact.EvidenceBindings {
						if binding.Identity.RawSHA256 != "" {
							t.Fatal("in-memory projection claimed independently authenticated original bytes")
						}
					}
				}
				if incremental.FactRollingUpdates != 0 {
					t.Fatal("input projection masqueraded as raw history identity")
				}
				return
			}
			incremental := analyzeRollingFixture(t, frozen, h, cache, algorithm, R1StrategyID)
			full := analyzeRollingFixture(t, frozen, h, nil, algorithm, R1StrategyID)
			assertFullIncrementalEqual(t, incremental, full)
			if incremental.FactRollingUpdates != expected {
				t.Fatalf("updates=%d want %d", incremental.FactRollingUpdates, expected)
			}
		})
	}
}

func setRollingFixtureClose(row Row, value string) {
	for _, field := range []string{"open", "high", "low", "close", "comparable_close"} {
		fixtureSet(row, field, value)
	}
}

func TestRollingBelowMA5StreakAndShiftedWindowBoundary(t *testing.T) {
	for _, mode := range []string{"known_streak", "stopping_context_expires", "unknown_streak_recovers"} {
		t.Run(mode, func(t *testing.T) {
			algorithm := strings.Repeat("b", 64)
			f, rows := rollingFixtureBase()
			for index, row := range rows {
				offset, indexInStock := index/320, index%320
				switch mode {
				case "known_streak":
					if indexInStock >= 310 {
						setRollingFixtureClose(row, fmt.Sprintf("%d", 20-(indexInStock-310)+offset))
					}
				case "stopping_context_expires":
					value := 400 + offset*100
					if indexInStock > 9 {
						value -= indexInStock - 9
					}
					setRollingFixtureClose(row, fmt.Sprintf("%d", value))
				case "unknown_streak_recovers":
					setRollingFixtureClose(row, fmt.Sprintf("%d", 400-indexInStock+offset*100))
				}
			}
			frozen, h := newRollingFixtureHistory(t, f, rows, nil)
			cache := newRollingFixtureCache()
			initial := analyzeRollingFixture(t, frozen, h, cache, algorithm, R1StrategyID)
			for _, fact := range initial.Facts {
				if mode == "unknown_streak_recovers" && fact.Facts.Trend.Days.Known() {
					t.Fatal("missing stopping history falsely known")
				}
				if mode == "stopping_context_expires" && fact.Facts.Trend.Days.String() != "310" {
					t.Fatal("wrong fixture stopping window", fact.Facts.Trend.Days)
				}
			}
			steps := 1
			if mode == "known_streak" {
				steps = 4
			}
			for step := 1; step <= steps; step++ {
				appendRollingFixtureDay(t, &f, &rows, step)
				for _, row := range rows {
					if row.Text("session") != f.Input.Observation {
						continue
					}
					offset := 0
					if row.Text("instrument_id") == "TEST_FIXTURE_B.SH" {
						offset = 1
					}
					value := 11 - step + offset
					switch mode {
					case "stopping_context_expires":
						value = 89 + offset*100
					case "unknown_streak_recovers":
						value = 600 + offset*100
					}
					setRollingFixtureClose(row, fmt.Sprintf("%d", value))
				}
				frozen, h = newRollingFixtureHistory(t, f, rows, nil)
				incremental := analyzeRollingFixture(t, frozen, h, cache, algorithm, R1StrategyID)
				full := analyzeRollingFixture(t, frozen, h, nil, algorithm, R1StrategyID)
				assertFullIncrementalEqual(t, incremental, full)
				expected := 2
				if mode == "stopping_context_expires" {
					expected = 0
				}
				if incremental.FactRollingUpdates != expected {
					t.Fatalf("updates=%d want=%d", incremental.FactRollingUpdates, expected)
				}
				for index, fact := range incremental.Facts {
					switch mode {
					case "known_streak":
						want := initial.Facts[index].Facts.Trend.Days.Add(count(step))
						if !fact.Facts.Trend.Days.Known() || fact.Facts.Trend.Days.Cmp(want) != 0 {
							t.Fatal("streak boundary changed", fact.Facts.Trend.Days, want)
						}
					case "stopping_context_expires":
						if fact.Facts.Trend.Days.Known() {
							t.Fatal("guard leaked into current full window")
						}
					case "unknown_streak_recovers":
						if fact.Facts.Trend.Days.String() != "0" {
							t.Fatal("current crossing did not legitimately recover")
						}
					}
				}
			}
		})
	}
}

func TestFullTrendMultipleNegativeSlopesHasStableReasonOrder(t *testing.T) {
	sessions := []string{}
	bars := map[string]Bar{}
	day := time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC)
	for i := 0; i < 320; i++ {
		session := day.AddDate(0, 0, i).Format("2006-01-02")
		sessions = append(sessions, session)
		price := count(400 - i)
		if i == 319 {
			price = N("230")
		}
		bars[session] = Bar{Session: session, Basis: "TEST_FIXTURE_STABLE_BASIS", Close: price}
	}
	window := Window{Sessions: sessions, Bars: bars}
	want := []string{"SAM_LONG_MA_BULLISH_ARRANGEMENT_FAIL", "SAM_MA60_SLOPE_NEGATIVE", "SAM_MA120_SLOPE_NEGATIVE", "SAM_MA250_SLOPE_NEGATIVE"}
	for repeat := 0; repeat < 32; repeat++ {
		trend, err := window.Trend(context.Background(), 319)
		if err != nil || trend.State != "TREND_DETERIORATING" || !reflect.DeepEqual(trend.Condition.Reasons, want) {
			t.Fatal("unstable full negative-slope reasons", trend, err)
		}
	}
}
