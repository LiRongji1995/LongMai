package evidence

import (
	"context"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"testing"
)

func TestRevisionIndexPreservesSelectedParallelOrigin(t *testing.T) {
	decode := func(raw string) c.Row {
		r, e := DecodeEvidenceRow(context.Background(), "bar", []byte(raw))
		if e != nil {
			t.Fatal(e)
		}
		return r
	}
	old := decode(`{"instrument_id":"TEST_FIXTURE","session":"2026-01-01","close":"10","event_time":"2026-01-01T15:00:00Z","source_published_at":"2026-01-01T15:00:00Z","first_seen_at":"2026-01-01T15:00:00Z","ingested_at":"2026-01-01T15:00:00Z"}`)
	next := decode(`{"instrument_id":"TEST_FIXTURE","session":"2026-01-01","close":"11","event_time":"2026-01-01T15:00:00Z","source_published_at":"2026-01-01T16:00:00Z","first_seen_at":"2026-01-01T16:00:00Z","ingested_at":"2026-01-01T16:00:00Z"}`)
	rows := []c.Row{old, next, old}
	origins := []string{"OLD_FIRST", "NEW_REVISION", "OLD_LAST"}
	index, e := SelectRevisionIndex(rows)
	if e != nil || index < 0 || origins[index] != "NEW_REVISION" {
		t.Fatal(index, e)
	}
	row, e := SelectRevision(rows)
	if e != nil || c.RowHash(row) != c.RowHash(rows[index]) {
		t.Fatal("old row-selection compatibility changed", e)
	}
	index, e = SelectRevisionIndex([]c.Row{next, next})
	if e != nil || index != 0 {
		t.Fatal("equal revision origin is nondeterministic", index, e)
	}
	conflict := decode(`{"instrument_id":"TEST_FIXTURE","session":"2026-01-01","close":"12","event_time":"2026-01-01T15:00:00Z","source_published_at":"2026-01-01T16:00:00Z","first_seen_at":"2026-01-01T16:00:00Z","ingested_at":"2026-01-01T16:00:00Z"}`)
	if index, e = SelectRevisionIndex([]c.Row{next, conflict}); e == nil || index != -1 {
		t.Fatal("same-clock conflict produced selected provenance", index, e)
	}
}
