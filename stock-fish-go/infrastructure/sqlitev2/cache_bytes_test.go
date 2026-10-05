package sqlitev2

import (
	"context"
	"testing"
	"time"

	resources "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
)

func TestCachePayloadBytesIgnoreCorruptMetadata(t *testing.T) {
	for _, storage := range []string{"blob", "text"} {
		for _, corruption := range []string{"size", "hash"} {
			t.Run(storage+"_"+corruption, func(t *testing.T) {
				s, r, _ := appFixture(t) // Independent TEST_FIXTURE temp database.
				b, err := resources.NewBudget(context.Background(), time.Minute, nil)
				if err != nil {
					t.Fatal(err)
				}
				defer b.Close()
				ctx := b.Ctx()
				raw := []byte("中文")
				var payload any = raw
				if storage == "text" {
					payload = string(raw)
				}
				size, hash := len(raw), appHash(raw)
				if corruption == "size" {
					size = 1
				} else {
					hash = "TEST_FIXTURE_CORRUPT_HASH"
				}
				key := "TEST_FIXTURE_UTF8_CORRUPT_METADATA"
				if _, err = s.db.ExecContext(ctx, "INSERT INTO app_fact_cache VALUES(?,?,?,?,?,?,?)", key, "v", "d", payload, hash, size, "2000-01-01"); err != nil {
					t.Fatal(err)
				}
				var actualStorage string
				if err = s.db.QueryRowContext(ctx, "SELECT typeof(payload) FROM app_fact_cache WHERE cache_key=?", key).Scan(&actualStorage); err != nil || actualStorage != storage {
					t.Fatal("fixture storage class", actualStorage, err)
				}
				r.SampleCache(ctx)
				if m := b.Snapshot(); m.CacheBytes == nil || *m.CacheBytes != 6 {
					t.Fatalf("actual UTF-8 occupancy must be six bytes despite corrupt metadata: %+v", m)
				}
				if err = r.CachePut(ctx, "TEST_FIXTURE_VALID", "v", "d", []byte("abc")); err != nil {
					t.Fatal(err)
				}
				if m := b.Snapshot(); m.CacheBytes == nil || *m.CacheBytes != 9 {
					t.Fatalf("write occupancy ignored actual existing payload bytes: %+v", m)
				}
				if _, hit, err := r.CacheGet(ctx, key, "v", "d"); err != nil || hit {
					t.Fatal("corrupt metadata accepted", hit, err)
				}
				var remaining int
				if err = s.db.QueryRowContext(ctx, "SELECT count(*) FROM app_fact_cache WHERE cache_key=?", key).Scan(&remaining); err != nil || remaining != 0 {
					t.Fatal("corrupt cache row was not deleted", remaining, err)
				}
				m := b.Snapshot()
				if m.CacheReadBytes != 6 || m.CacheCorrupt != 1 || m.CacheMisses != 1 || m.CacheBytes == nil || *m.CacheBytes != 3 {
					t.Fatalf("corrupt row integrity and byte accounting changed: %+v", m)
				}
			})
		}
	}
}
