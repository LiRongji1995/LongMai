package sqlitev2

import (
	"context"
	"database/sql"
	"encoding/json"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	f "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/legacy"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/native"
	resources "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
)

// A deliberately noncanonical payload with omitted model defaults demonstrates
// that authenticated source bytes and normalized model identity are distinct.
func identityHistoryFixture(t *testing.T) (source, target, manifestHash string, raw []byte) {
	t.Helper()
	source, _, target = fixture(t)
	raw = []byte("{\n  \"instrument_id\":\"600001.SH\", \"session\":\"2026-01-01\",\n  \"close\":\"+0010.00\", \"amount_cny\":\"5e8\",\n  \"source_refs\":[\"TEST_FIXTURE\"],\n  \"event_time\":\"2026-01-01T15:00:00+08:00\",\"source_published_at\":\"2026-01-01T15:00:00+08:00\",\n  \"first_seen_at\":\"2026-01-01T16:00:00+08:00\",\"ingested_at\":\"2026-01-01T16:00:00+08:00\"\n}\n")
	hash := c.Hash(raw)
	entry, _ := json.Marshal([]string{hash, "bar", "600001.SH", "2026-01-01"})
	partition := c.Hash(append(entry, '\n'))
	m, _ := json.Marshal(map[string]any{"artifact_version": "CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1", "partitions": []any{map[string]any{"partition_hash": partition, "row_count": 1, "session": "2026-01-01"}}, "receipts": []any{}})
	manifestHash = c.Hash(m)
	db, e := sql.Open("sqlite", URI(source, false))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	for _, q := range []string{"DELETE FROM documents", "DELETE FROM history_partition_members", "DELETE FROM history_evidence_rows"} {
		if _, e = db.Exec(q); e != nil {
			t.Fatal(e)
		}
	}
	if _, e = db.Exec("INSERT INTO documents VALUES('history_artifact_manifests',?,?,?,'TEST_FIXTURE')", "history_"+manifestHash, string(m), manifestHash); e != nil {
		t.Fatal(e)
	}
	if _, e = db.Exec("INSERT INTO history_evidence_rows VALUES(?,'bar','600001.SH','2026-01-01',?)", hash, string(raw)); e != nil {
		t.Fatal(e)
	}
	if _, e = db.Exec("INSERT INTO history_partition_members VALUES(?,?,'bar','600001.SH','2026-01-01')", partition, hash); e != nil {
		t.Fatal(e)
	}
	if e = db.Close(); e != nil {
		t.Fatal(e)
	}
	snapshot := filepath.Join(filepath.Dir(source), "TEST_FIXTURE_identity_snapshot.db")
	if _, e = Snapshot(context.Background(), source, snapshot); e != nil {
		t.Fatal(e)
	}
	source = snapshot
	return
}
func identityFrozen(t *testing.T, mh string, inline []json.RawMessage) []byte {
	t.Helper()
	clocks := func() map[string]any {
		return map[string]any{"event_time": "2026-01-01T15:00:00+08:00", "source_published_at": "2026-01-01T15:00:00+08:00", "first_seen_at": "2026-01-01T16:00:00+08:00", "ingested_at": "2026-01-01T16:00:00+08:00"}
	}
	receipt := clocks()
	for k, v := range map[string]any{"receipt_id": "TEST_FIXTURE", "provider": "TEST_FIXTURE", "dataset": "TEST_FIXTURE", "field_version": "TEST_FIXTURE_V1", "content_hash": strings.Repeat("a", 64), "row_count": 1, "pagination_complete": true, "status": "COMPLETE"} {
		receipt[k] = v
	}
	security := clocks()
	for k, v := range map[string]any{"instrument_id": "600001.SH", "exchange": "SSE", "board": "MAIN", "security_type": "A_COMMON", "listed_on": "2020-01-01", "effective_from": "2020-01-01", "st": false, "suspended": false, "source_refs": []string{"TEST_FIXTURE"}} {
		security[k] = v
	}
	universe := clocks()
	for k, v := range map[string]any{"snapshot_id": "TEST_FIXTURE", "securities": []any{security}, "source_refs": []string{"TEST_FIXTURE"}, "complete": true, "expected_count": 1} {
		universe[k] = v
	}
	end := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	sessions := []string{}
	for i := 59; i >= 0; i-- {
		sessions = append(sessions, end.AddDate(0, 0, -i).Format("2006-01-02"))
	}
	if inline == nil {
		inline = []json.RawMessage{}
	}
	input := map[string]any{"observation_date": "2026-01-01", "sessions": sessions, "universe": universe, "bars": inline, "receipts": []any{receipt}, "coverage_type": "TEST_FIXTURE"}
	if mh != "" {
		input["history_artifact_ref"] = "history_" + mh
		input["history_artifact_hash"] = mh
	}
	raw, e := json.Marshal(map[string]any{"purpose": "TEST_FIXTURE_BINDING", "cutoff": "2026-01-01T20:00:00+08:00", "input": input})
	if e != nil {
		t.Fatal(e)
	}
	return raw
}
func TestHistorySelectedIdentityAcrossAdapters(t *testing.T) {
	ctx := context.Background()
	source, target, mh, raw := identityHistoryFixture(t)
	if _, e := Migrate(ctx, source, target, Options{}); e != nil {
		t.Fatal(e)
	}
	if _, e := Verify(ctx, source, target, Options{}); e != nil {
		t.Fatal(e)
	}
	if e := Publish(ctx, target); e != nil {
		t.Fatal(e)
	}
	store, e := Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	defer store.Close()
	b, e := resources.NewBudget(ctx, time.Minute, nil)
	if e != nil {
		t.Fatal(e)
	}
	defer b.Close()
	old, e := legacy.OpenHistory(source, "history_"+mh, mh, b)
	if e != nil {
		t.Fatal(e)
	}
	defer old.Close()
	next, e := store.History(ctx, "history_"+mh, mh, b)
	if e != nil {
		t.Fatal(e)
	}
	defer next.Close()
	projection, e := ev.DecodeEvidenceRow(ctx, "bar", raw)
	if e != nil {
		t.Fatal(e)
	}
	if c.RowHash(projection) == c.Hash(raw) {
		t.Fatal("projection mislabeled as original bytes")
	}
	if projection.Text("quality") != "VALID" || projection.Text("tick_size") != "0.01" {
		t.Fatal("omitted defaults not exercised")
	}
	want := c.SelectedEvidenceBinding{ProjectionHash: c.RowHash(projection), Identity: c.EvidenceIdentity{Origin: "HISTORY_ROW", ParentHash: mh, RawSHA256: c.Hash(raw), Locator: c.Hash(raw), RawBytes: int64(len(raw))}}
	for _, reader := range []ports.HistoryIdentityReader{old, next} {
		if e := reader.(ports.HistoryReader).VerifyIndex(); e != nil {
			t.Fatal(e)
		}
		bindings := []c.SelectedEvidenceBinding{}
		if e := reader.BarsWithIdentity("600001.SH", "2026-01-01", "2026-01-01", func(r c.Row, id c.EvidenceIdentity) error {
			bindings = append(bindings, c.SelectedEvidenceBinding{ProjectionHash: c.RowHash(r), Identity: id})
			return nil
		}); e != nil {
			t.Fatal(e)
		}
		got, _ := json.Marshal(bindings)
		expected, _ := json.Marshal([]c.SelectedEvidenceBinding{want})
		if string(got) != string(expected) {
			t.Fatalf("binding mismatch\ngot %s\nwant %s", got, expected)
		}
	}
	// Native application serialization must also agree, including separately
	// attached provenance, after the full selection and facts-cache paths.
	input := identityFrozen(t, mh, nil)
	evaluate := func(factory native.HistoryFactory, cache ports.FactsCache) []c.Item {
		engine := native.NativeEngine{SourceHash: "TEST_FIXTURE_BINDING", History: factory, Cache: cache}
		in, e := engine.Validate(input)
		if e != nil {
			t.Fatal(e)
		}
		runCtx, close, e := engine.Start(ctx, 60)
		if e != nil {
			t.Fatal(e)
		}
		defer close()
		run, e := engine.Evaluate(runCtx, in, c.ResearchRequest{Strategy: c.R1, BudgetSeconds: 60})
		if e != nil {
			t.Fatal(e)
		}
		if len(run.Items) != 1 {
			t.Fatalf("expected one fixture item, got %d", len(run.Items))
		}
		var item struct {
			Fact f.StockFacts `json:"fact"`
		}
		if e = json.Unmarshal(run.Items[0].Payload, &item); e != nil {
			t.Fatal(e)
		}
		var manifest map[string]json.RawMessage
		if e = json.Unmarshal(run.Manifest, &manifest); e != nil {
			t.Fatal(e)
		}
		if len(manifest["selected_evidence_binding_contract"]) == 0 {
			t.Fatal("compact provenance contract missing")
		}
		if len(item.Fact.EvidenceBindings) != len(item.Fact.EvidenceHashes) {
			t.Fatal("binding index is not closed")
		}
		for i := range item.Fact.EvidenceBindings {
			binding := &item.Fact.EvidenceBindings[i]
			if binding.ProjectionHash != "" || binding.Identity.ParentHash != "" || binding.Identity.Locator != "" || binding.RawEqualsProjection {
				t.Fatal("noncanonical fixture compacted as raw-equal")
			}
			binding.ProjectionHash = item.Fact.EvidenceHashes[i]
			binding.Identity.ParentHash = mh
			binding.Identity.Locator = binding.Identity.RawSHA256
		}
		expectedBinding := want
		expectedBinding.Identity.RawBytes = 0
		got, _ := json.Marshal(item.Fact.EvidenceBindings)
		expected, _ := json.Marshal([]c.SelectedEvidenceBinding{expectedBinding})
		if string(got) != string(expected) {
			t.Fatalf("native selected bindings %s want %s", got, expected)
		}
		return run.Items
	}
	oldFactory := func(ctx context.Context, _ c.Frozen, b ports.ResearchBudget) (ports.HistoryReader, func(), error) {
		h, e := legacy.OpenHistory(source, "history_"+mh, mh, b)
		if e != nil {
			return nil, nil, e
		}
		return h, h.Close, nil
	}
	newFactory := func(ctx context.Context, _ c.Frozen, b ports.ResearchBudget) (ports.HistoryReader, func(), error) {
		h, e := store.History(ctx, "history_"+mh, mh, b)
		if e != nil {
			return nil, nil, e
		}
		return h, func() { h.Close() }, nil
	}
	cache := &identityPoisonCache{items: map[string]f.StockFacts{}}
	oldItems := evaluate(oldFactory, nil)
	cold := evaluate(newFactory, cache)
	warm := evaluate(newFactory, cache)
	if !reflect.DeepEqual(oldItems, cold) || !reflect.DeepEqual(cold, warm) {
		t.Fatal("old/new/cold/warm item bytes differ")
	}
	if cache.hits != 1 {
		t.Fatal("warm binding reattachment was not exercised")
	}
	// The original immutable bytes are still recoverable independently of the
	// normalized projection used by facts.
	var stored []byte
	if e := oldRaw(source, c.Hash(raw), &stored); e != nil {
		t.Fatal(e)
	}
	if string(stored) != string(raw) {
		t.Fatal("source raw bytes changed")
	}
}
func oldRaw(path, hash string, out *[]byte) error {
	db, e := sql.Open("sqlite", URI(path, true))
	if e != nil {
		return e
	}
	defer db.Close()
	return db.QueryRow("SELECT payload FROM history_evidence_rows WHERE row_hash=?", hash).Scan(out)
}

type identityPoisonCache struct {
	items map[string]f.StockFacts
	hits  int
}

func (x *identityPoisonCache) GetFact(_ context.Context, key string) (f.StockFacts, bool, error) {
	v, ok := x.items[key]
	if ok {
		x.hits++
		v.EvidenceBindings = []c.SelectedEvidenceBinding{{ProjectionHash: "TEST_FIXTURE_STALE_CACHE_BINDING"}}
	}
	return v, ok, nil
}
func (x *identityPoisonCache) PutFact(_ context.Context, key string, v f.StockFacts) error {
	x.items[key] = v
	return nil
}

type unboundHistory struct{}

func (unboundHistory) VerifyIndex() error                                   { return nil }
func (unboundHistory) Bars(string, string, string, func(c.Row) error) error { return nil }
func (unboundHistory) Receipts() []c.Row                                    { return nil }
func (unboundHistory) Identity() string                                     { return strings.Repeat("a", 64) }
func TestNativeRejectsHistoryWithoutAuthenticatedBindingPort(t *testing.T) {
	engine := native.NativeEngine{SourceHash: "TEST_FIXTURE_BINDING", History: func(context.Context, c.Frozen, ports.ResearchBudget) (ports.HistoryReader, func(), error) {
		return unboundHistory{}, nil, nil
	}}
	in, e := engine.Validate(identityFrozen(t, strings.Repeat("a", 64), nil))
	if e != nil {
		t.Fatal(e)
	}
	ctx, close, e := engine.Start(context.Background(), 60)
	if e != nil {
		t.Fatal(e)
	}
	defer close()
	if _, e = engine.Evaluate(ctx, in, c.ResearchRequest{Strategy: c.R1, BudgetSeconds: 60}); e == nil || !strings.Contains(e.Error(), "AUTHENTICATED_HISTORY_IDENTITIES_REQUIRED") {
		t.Fatalf("unauthenticated history accepted: %v", e)
	}
}

func TestNativeInlineBindingResolvesOriginalInputPointer(t *testing.T) {
	rawBar := json.RawMessage(`{ "instrument_id":"600001.SH", "session":"2026-01-01", "close":"+0010.00", "amount_cny":"5e8", "source_refs":["TEST_FIXTURE"], "event_time":"2026-01-01T15:00:00+08:00", "source_published_at":"2026-01-01T15:00:00+08:00", "first_seen_at":"2026-01-01T16:00:00+08:00", "ingested_at":"2026-01-01T16:00:00+08:00" }`)
	input := identityFrozen(t, "", []json.RawMessage{rawBar})
	engine := native.NativeEngine{SourceHash: "TEST_FIXTURE_INLINE_BINDING"}
	in, e := engine.Validate(input)
	if e != nil {
		t.Fatal(e)
	}
	ctx, close, e := engine.Start(context.Background(), 60)
	if e != nil {
		t.Fatal(e)
	}
	defer close()
	run, e := engine.Evaluate(ctx, in, c.ResearchRequest{Strategy: c.R1, BudgetSeconds: 60})
	if e != nil {
		t.Fatal(e)
	}
	if len(run.Items) != 1 {
		t.Fatal("fixture qualification failed", len(run.Items))
	}
	var item struct {
		Fact f.StockFacts `json:"fact"`
	}
	if e = json.Unmarshal(run.Items[0].Payload, &item); e != nil {
		t.Fatal(e)
	}
	if len(item.Fact.EvidenceBindings) != 1 || len(item.Fact.EvidenceHashes) != 1 {
		t.Fatal("inline provenance missing")
	}
	b := item.Fact.EvidenceBindings[0]
	if b.Identity.Origin != "FROZEN_INPUT" || b.Identity.Locator != "/input/bars/0" || b.Identity.RawSHA256 != "" || b.RawEqualsProjection {
		t.Fatal("invalid input binding", b)
	}
	var manifest map[string]any
	if e = json.Unmarshal(run.Manifest, &manifest); e != nil {
		t.Fatal(e)
	}
	if manifest["input_bytes_hash"] != c.Hash(input) {
		t.Fatal("pointer parent bytes unauthenticated")
	}
	var envelope struct {
		Input struct {
			Bars []json.RawMessage `json:"bars"`
		} `json:"input"`
	}
	if e = json.Unmarshal(input, &envelope); e != nil {
		t.Fatal(e)
	}
	located := envelope.Input.Bars[0]
	projected, e := ev.DecodeEvidenceRow(ctx, "bar", located)
	if e != nil {
		t.Fatal(e)
	}
	if c.RowHash(projected) != item.Fact.EvidenceHashes[0] {
		t.Fatal("original pointer cannot reproduce selected model projection")
	}
	if c.Hash(located) == item.Fact.EvidenceHashes[0] {
		t.Fatal("fixture raw/model difference was lost")
	}
}
