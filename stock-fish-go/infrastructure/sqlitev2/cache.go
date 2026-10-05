package sqlitev2

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"time"
)

const FactCacheCapacity int64 = 256 << 20

// Capacity accounting V2 measures stored payload bytes, independently of the
// integrity metadata. SQLite can read a BLOB length without loading its body;
// legacy TEXT must be cast so multibyte UTF-8 is counted as bytes, not characters.
// This operational measurement does not change the fact arithmetic cache key.
const cachePayloadByteLengthSQL = "CASE WHEN typeof(payload)='blob' THEN length(payload) ELSE length(CAST(payload AS BLOB)) END"

// CacheIdentity includes evidence/policy/visibility/window identities. A caller
// must verify source bytes before consulting this derived, disposable store.
type CacheIdentity struct {
	Version     string   `json:"version"`
	InputHash   string   `json:"input_hash"`
	HistoryHash string   `json:"history_hash"`
	Cutoff      string   `json:"cutoff"`
	Instrument  string   `json:"instrument"`
	Window      string   `json:"window"`
	Basis       string   `json:"basis"`
	Policy      string   `json:"policy"`
	Evidence    []string `json:"selected_evidence"`
}

func CacheKey(identity CacheIdentity) (string, error) {
	if identity.Version == "" || identity.InputHash == "" || identity.Cutoff == "" || identity.Instrument == "" || identity.Window == "" || identity.Policy == "" {
		return "", errors.New("INCOMPLETE_FACT_CACHE_IDENTITY")
	}
	raw, e := json.Marshal(identity)
	return appHash(raw), e
}
func (r *AppRepository) CacheGet(ctx context.Context, key, version, dependencies string) ([]byte, bool, error) {
	b := ports.BudgetFromContext(ctx)
	started := time.Now()
	outcome := "error"
	defer func() { ports.CacheObservation(b, outcome, nil) }()
	defer func() { ports.Observe(b, "cache_lookup_and_integrity", time.Since(started).Seconds()) }()
	var raw []byte
	var stored string
	var size int64
	e := r.db.QueryRowContext(ctx, "SELECT payload,payload_hash,size FROM app_fact_cache WHERE cache_key=? AND version=? AND dependencies_hash=?", key, version, dependencies).Scan(&raw, &stored, &size)
	ports.Observe(b, "cache_query", time.Since(started).Seconds())
	if errors.Is(e, sql.ErrNoRows) {
		outcome = "miss"
		return nil, false, nil
	}
	if e != nil {
		return nil, false, e
	}
	ports.CacheRead(b, int64(len(raw)))
	validation := time.Now()
	valid := int64(len(raw)) == size && appHash(raw) == stored
	ports.Observe(b, "cache_integrity", time.Since(validation).Seconds())
	if !valid {
		outcome = "corrupt"
		if _, e = r.db.ExecContext(ctx, "DELETE FROM app_fact_cache WHERE cache_key=?", key); e != nil {
			return nil, false, e
		}
		r.SampleCache(ctx)
		return nil, false, nil
	}
	_, e = r.db.ExecContext(ctx, "UPDATE app_fact_cache SET last_access=? WHERE cache_key=?", appNow(), key)
	if e == nil {
		outcome = "hit"
	}
	return raw, true, e
}
func (r *AppRepository) CachePut(ctx context.Context, key, version, dependencies string, raw []byte) error {
	started := time.Now()
	defer func() {
		ports.Observe(ports.BudgetFromContext(ctx), "cache_write_capacity_and_eviction", time.Since(started).Seconds())
	}()
	if key == "" || version == "" || dependencies == "" || int64(len(raw)) > FactCacheCapacity {
		return errors.New("CACHE_CAPACITY_OR_IDENTITY_INVALID")
	}
	if e := r.resourceCheck(); e != nil {
		return e
	}
	tx, e := r.db.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	_, e = tx.ExecContext(ctx, "INSERT INTO app_fact_cache VALUES(?,?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET version=excluded.version,dependencies_hash=excluded.dependencies_hash,payload=excluded.payload,payload_hash=excluded.payload_hash,size=excluded.size,last_access=excluded.last_access", key, version, dependencies, raw, appHash(raw), len(raw), appNow())
	if e != nil {
		return e
	}
	var total int64
	r.cacheOccupancyQueries.Add(1)
	if e = tx.QueryRowContext(ctx, "SELECT coalesce(sum("+cachePayloadByteLengthSQL+"),0) FROM app_fact_cache").Scan(&total); e != nil {
		return e
	}
	for total > FactCacheCapacity {
		var victim string
		var n int64
		if e = tx.QueryRowContext(ctx, "SELECT cache_key,"+cachePayloadByteLengthSQL+" FROM app_fact_cache ORDER BY last_access,cache_key LIMIT 1").Scan(&victim, &n); e != nil {
			return e
		}
		if _, e = tx.ExecContext(ctx, "DELETE FROM app_fact_cache WHERE cache_key=?", victim); e != nil {
			return e
		}
		total -= n
	}
	if e = tx.Commit(); e != nil {
		return e
	}
	ports.CacheObservation(ports.BudgetFromContext(ctx), "", &total)
	return nil
}
func (r *AppRepository) CacheClear(ctx context.Context) error {
	_, e := r.db.ExecContext(ctx, "DELETE FROM app_fact_cache")
	return e
}

// Sampling is a scope boundary or completed occupancy mutation, never a lookup.
// Its cost is nested inside evaluate/cache_write, not additive to total time.
func (r *AppRepository) SampleCache(ctx context.Context) {
	b := ports.BudgetFromContext(ctx)
	if b == nil {
		return
	}
	started := time.Now()
	var total int64
	var measured *int64
	status := "UNAVAILABLE"
	r.cacheOccupancyQueries.Add(1)
	if r.db.QueryRowContext(ctx, "SELECT coalesce(sum("+cachePayloadByteLengthSQL+"),0) FROM app_fact_cache").Scan(&total) == nil {
		measured = &total
		status = "MEASURED_SCOPE_OR_MUTATION"
	}
	ports.Observe(b, "cache_occupancy_sample", time.Since(started).Seconds())
	if p, ok := b.(interface{ CacheSample(*int64, string) }); ok {
		p.CacheSample(measured, status)
	}
}
