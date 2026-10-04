package sqlitev2

import (
	"context"
	"encoding/json"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/facts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"time"
)

// The engine constructs this key only after raw bytes, projections, visibility
// and revision selection have been verified. Cache hits reuse arithmetic only.
func (r *AppRepository) GetFact(ctx context.Context, key string) (facts.StockFacts, bool, error) {
	var out facts.StockFacts
	raw, hit, e := r.CacheGet(ctx, key, "GO_BUILD_FACTS_V2", key)
	if e != nil || !hit {
		return out, hit, e
	}
	started := time.Now()
	e = json.Unmarshal(raw, &out)
	ports.Observe(ports.BudgetFromContext(ctx), "cache_decode", time.Since(started).Seconds())
	if e != nil {
		ports.CacheObservation(ports.BudgetFromContext(ctx), "decode_corrupt", nil)
		_, e = r.db.ExecContext(ctx, "DELETE FROM app_fact_cache WHERE cache_key=?", key)
		return facts.StockFacts{}, false, e
	}
	return out, true, nil
}
func (r *AppRepository) PutFact(ctx context.Context, key string, f facts.StockFacts) error {
	raw, e := json.Marshal(f)
	if e != nil {
		return e
	}
	return r.CachePut(ctx, key, "GO_BUILD_FACTS_V2", key, raw)
}
