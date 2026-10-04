package sqlitev2

import (
	"context"
	"encoding/json"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	resources "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	"os"
	"os/exec"
	"runtime/debug"
	"sync"
	"testing"
	"time"
)

func TestCacheMetricsCorruptionCancellationAndEviction(t *testing.T) {
	// This deliberately materializes one 256 MiB invalid SQLite blob. Run the
	// unchanged sample in its own process, as the CLI does, rather than inherit
	// allocator pages retained by unrelated bundle fixtures in this package.
	if os.Getenv("STOCKFISH_TEST_CAPACITY_CHILD") != "1" {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestCacheMetricsCorruptionCancellationAndEviction$", "-test.v")
		cmd.Env = append(os.Environ(), "STOCKFISH_TEST_CAPACITY_CHILD=1")
		raw, e := cmd.CombinedOutput()
		if e != nil {
			t.Fatalf("isolated capacity sample: %v\n%s", e, raw)
		}
		t.Logf("isolated full-capacity sample: %s", raw)
		return
	}

	// Match cmd/stockfish's soft heap target and release preceding fixture garbage;
	// the complete 256 MiB SQLite capacity sample and the 1 GiB RSS gate remain.
	previous := debug.SetMemoryLimit(512 << 20)
	defer debug.SetMemoryLimit(previous)
	debug.FreeOSMemory()
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
		metrics, _ := json.Marshal(b.Snapshot())
		t.Fatalf("cache put: %v; budget cause=%v metrics=%s", err, b.Check(), metrics)
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
	if m.CacheBytes == nil || *m.CacheBytes != 3 || m.CacheSampleAt == "" || m.CacheErrors != 1 || m.CacheReadBytes != 3 {
		t.Fatal("cancelled lookup lost the timestamped prior sample or counted bytes", m)
	}
}
func TestSQLiteCacheLookupDoesNotSum(t *testing.T) {
	s, r, _ := appFixture(t)
	b, e := resources.NewBudget(context.Background(), time.Minute, nil)
	if e != nil {
		t.Fatal(e)
	}
	defer b.Close()
	ctx := b.Ctx()
	if e = r.CachePut(ctx, "fixture", "v", "d", []byte("payload")); e != nil {
		t.Fatal(e)
	}
	before := r.cacheOccupancyQueries.Load()
	for i := 0; i < 6; i++ {
		key := "fixture"
		if i%2 == 0 {
			key = "missing"
		}
		if _, _, e = r.CacheGet(ctx, key, "v", "d"); e != nil {
			t.Fatal(e)
		}
	}
	if got := r.cacheOccupancyQueries.Load() - before; got != 0 {
		t.Fatalf("six lookups executed %d SUM queries", got)
	}
	rows, e := s.db.QueryContext(ctx, "EXPLAIN QUERY PLAN SELECT coalesce(sum(size),0) FROM app_fact_cache")
	if e != nil {
		t.Fatal(e)
	}
	defer rows.Close()
	for rows.Next() {
		var id, parent, unused int
		var detail string
		if e = rows.Scan(&id, &parent, &unused, &detail); e != nil {
			t.Fatal(e)
		}
		t.Logf("actual SQLite plan: %s; six lookup SUM count=0", detail)
	}
	r.SampleCache(ctx)
	if r.cacheOccupancyQueries.Load() != before+1 {
		t.Fatal("boundary sample missing")
	}
	m := b.Snapshot()
	if m.CacheHits != 3 || m.CacheMisses != 3 || m.CacheReadBytes != 21 || m.CacheSamples != 2 || m.CacheSampleAt == "" {
		t.Fatal(m)
	}
}

// An anomalous restored scope may not create even a PREPARING result.
type testJumpClock struct {
	mu   sync.Mutex
	wall time.Time
	mono time.Duration
}

func (c *testJumpClock) Wall() time.Time     { c.mu.Lock(); defer c.mu.Unlock(); return c.wall }
func (c *testJumpClock) Mono() time.Duration { c.mu.Lock(); defer c.mu.Unlock(); return c.mono }
func TestPublicationRejectsClockJump(t *testing.T) {
	s, r, _ := appFixture(t)
	clock := &testJumpClock{wall: time.Now()}
	b, e := resources.NewBudgetWithClock(context.Background(), time.Minute, nil, clock)
	if e != nil {
		t.Fatal(e)
	}
	defer b.Close()
	clock.mu.Lock()
	clock.wall = clock.wall.Add(time.Hour)
	clock.mu.Unlock()
	e = r.SaveRun(b.Ctx(), c.Run{ID: "TEST_FIXTURE_EXPIRED", InputID: "TEST_FIXTURE", Strategy: c.R1, Manifest: json.RawMessage(`{}`), Summary: json.RawMessage(`{}`)})
	if e == nil || e.Error() != "CLOCK_OR_SUSPEND_ANOMALY" {
		t.Fatal(e)
	}
	var n int
	_ = s.db.QueryRow("SELECT count(*) FROM app_runs").Scan(&n)
	if n != 0 {
		t.Fatal("expired result was saved")
	}
}
