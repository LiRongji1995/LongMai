package native

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
	"time"

	research "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application/research"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	f "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/buildidentity"
)

type factIdentityFixtureCache struct{ keys []string }

func (cache *factIdentityFixtureCache) GetFact(_ context.Context, key string) (f.StockFacts, bool, error) {
	cache.keys = append(cache.keys, key)
	return f.StockFacts{}, false, nil
}
func (*factIdentityFixtureCache) PutFact(context.Context, string, f.StockFacts) error { return nil }

func TestNativeFactsImplementationIdentityIsInjectedAndRecorded(t *testing.T) {
	input, history := refreshFixtureBase(t)
	decoded, err := ev.DecodeFrozen(input.Raw)
	if err != nil {
		t.Fatal(err)
	}
	decoded.Input.HistoryRef, decoded.Input.HistoryHash = "", ""
	decoded.Input.Receipts, decoded.Input.Bars = history.receipts, history.rows
	decoded.Input.Sessions = nil
	end, _ := time.Parse("2006-01-02", decoded.Input.Observation)
	for offset := 59; offset >= 0; offset-- {
		decoded.Input.Sessions = append(decoded.Input.Sessions, end.AddDate(0, 0, -offset).Format("2006-01-02"))
	}
	raw, err := json.Marshal(decoded)
	if err != nil {
		t.Fatal(err)
	}
	var envelope, market c.Row
	_ = json.Unmarshal(raw, &envelope)
	_ = json.Unmarshal(envelope["input"], &market)
	delete(market, "history_artifact_ref")
	delete(market, "history_artifact_hash")
	envelope["input"], _ = json.Marshal(market)
	raw, err = json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err = ev.DecodeFrozen(raw)
	if err != nil {
		t.Fatal(err)
	}
	cache := &factIdentityFixtureCache{}
	engine := NativeEngine{SourceHash: strings.Repeat("a", 64), Cache: cache}
	input, err = engine.Validate(raw)
	if err != nil {
		t.Fatal(err)
	}
	ctx, close, err := engine.Start(context.Background(), 60)
	if err != nil {
		t.Fatal(err)
	}
	defer close()
	run, err := engine.Evaluate(ctx, input, c.ResearchRequest{Strategy: c.R1, BudgetSeconds: 60})
	if err != nil {
		t.Fatal(err)
	}
	if len(run.Items) != 1 || len(cache.keys) != 1 {
		t.Fatal("TEST_FIXTURE did not reach stock arithmetic", len(run.Items), cache.keys)
	}
	var manifest struct {
		Facts string `json:"facts_implementation_id"`
	}
	if err = json.Unmarshal(run.Manifest, &manifest); err != nil || manifest.Facts != buildidentity.Facts() {
		t.Fatal("saved identity differs from composition identity", manifest, err)
	}
	expectedCache := &factIdentityFixtureCache{}
	expected, err := research.AnalyzeStrategyFrozenWithIdentity(decoded, c.Hash(raw), nil,
		refreshFixtureBudget{}, c.R1, expectedCache, buildidentity.Facts())
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(cache.keys, expectedCache.keys) {
		t.Fatal("native lost the exact injected facts identity", cache.keys, expectedCache.keys)
	}
	var summary struct {
		Scope string `json:"fact_cache_scope"`
	}
	if err = json.Unmarshal(run.Summary, &summary); err != nil || summary.Scope != expected.FactCacheScope {
		t.Fatal("native cache boundary differs from its manifest identity", summary, expected.FactCacheScope, err)
	}
	if buildidentity.Recorded(buildidentity.Facts()) {
		if summary.Scope != "RESOLVED_STOCK_ARITHMETIC_CROSS_INPUT" {
			t.Fatal("audited native facts identity fell back to an unrecorded wrapper", summary.Scope)
		}
	} else if summary.Scope != "SAME_INPUT_ONLY_UNRECORDED_ALGORITHM" {
		t.Fatal("unrecorded native build claimed cross-input compatibility", summary.Scope)
	}
}
