package evidence

import (
	"context"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"testing"
)

func TestRawProjectionPrecedesDefaultsAndStrictValidation(t *testing.T) {
	raw := []byte(`{"instrument_id":"TEST_FIXTURE","session":"2026-01-01","close":"10","event_time":"2026-01-01T15:00:00Z","source_published_at":"2026-01-01T15:00:00Z","first_seen_at":"2026-01-01T15:00:00Z","ingested_at":"2026-01-01T15:00:00Z"}`)
	called := false
	got, err := DecodeEvidenceRowWithProjection(context.Background(), "bar", raw, func(r c.Row) error {
		called = true
		if _, ok := r["quality"]; ok {
			t.Fatal("raw absence replaced by default before projection")
		}
		if string(r["close"]) != `"10"` {
			t.Fatal("raw scalar changed")
		}
		return nil
	})
	if err != nil || !called {
		t.Fatal(err, called)
	}
	want, err := DecodeEvidenceRow(context.Background(), "bar", raw)
	if err != nil || c.RowHash(got) != c.RowHash(want) {
		t.Fatal("decoded semantics changed", err)
	}
	sentinel := errors.New("TEST_FIXTURE_PROJECTION_TAMPER")
	if _, err = DecodeEvidenceRowWithProjection(context.Background(), "bar", raw, func(c.Row) error { return sentinel }); !errors.Is(err, sentinel) {
		t.Fatal(err)
	}
	if _, err = DecodeEvidenceRowWithProjection(context.Background(), "bar", []byte(`{"instrument_id":"TEST_FIXTURE","close":true}`), func(c.Row) error { return nil }); err == nil {
		t.Fatal("projection bypassed strict contract")
	}
}
