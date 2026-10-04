package sqlitev2

import (
	"context"
	resources "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	"testing"
	"time"
)

func TestCacheMetricsCorruptionCancellationAndEviction(t *testing.T) {
	s, r, _ := appFixture(t)
	b, err := resources.NewBudget(context.Background(), time.Minute, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	ctx := b.Ctx()
	if err = r.CachePut(ctx, "TEST_FIXTURE", "v", "d", []byte("abc")); err != nil {
		t.Fatal(err)
	}
	if _, err = s.db.Exec("UPDATE app_fact_cache SET payload_hash='TEST_FIXTURE_CORRUPT'"); err != nil {
		t.Fatal(err)
	}
	if _, hit, err := r.CacheGet(ctx, "TEST_FIXTURE", "v", "d"); err != nil || hit {
		t.Fatal(hit, err)
	}
	m := b.Snapshot()
	if m.CacheReadBytes != 3 || m.CacheCorrupt != 1 || m.CacheMisses != 1 || m.CacheBytes == nil || *m.CacheBytes != 0 {
		t.Fatalf("corrupt payload accounting: %+v", m)
	}
	// Explicit artificial full-occupancy row exercises capacity eviction without
	// materializing a 256 MiB Go payload. It is never consumed as a valid fact.
	if _, err = s.db.Exec("INSERT INTO app_fact_cache VALUES('TEST_FIXTURE_OCCUPANCY','v','d',zeroblob(?),'INVALID_TEST_FIXTURE',?,'2000-01-01')", FactCacheCapacity, FactCacheCapacity); err != nil {
		t.Fatal(err)
	}
	if err = r.CachePut(ctx, "TEST_FIXTURE_NEW", "v", "d", []byte("new")); err != nil {
		t.Fatal(err)
	}
	if _, hit, err := r.CacheGet(ctx, "TEST_FIXTURE_OCCUPANCY", "v", "d"); err != nil || hit {
		t.Fatal("capacity victim retained", err)
	}
	m = b.Snapshot()
	if m.CacheBytes == nil || *m.CacheBytes != 3 || m.CacheReadBytes != 3 {
		t.Fatal("eviction or miss accounting", m)
	}
	b.Cancel()
	if _, _, err := r.CacheGet(ctx, "TEST_FIXTURE_NEW", "v", "d"); err == nil {
		t.Fatal("cancel ignored")
	}
	m = b.Snapshot()
	if m.CacheBytes != nil || m.CacheErrors != 1 || m.CacheReadBytes != 3 {
		t.Fatal("cancelled measurement reported as zero or read", m)
	}
}
