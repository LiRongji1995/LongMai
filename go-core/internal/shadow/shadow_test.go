package shadow

import (
	"testing"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/go-core/internal/contract"
)

func TestBuildFactsMechanicalSlice(t *testing.T) {
	var rows []SourceRow
	for day := 1; day <= 60; day++ {
		rows = append(rows, SourceRow{
			Instrument:       "TEST000",
			Exchange:         "SSE",
			Session:          "2026-03-" + two(day),
			Open:             float64(day),
			High:             float64(day),
			Low:              float64(day),
			Close:            float64(day),
			PrevClose:        float64(day - 1),
			ComparableClose:  float64(day),
			ComparableBasis:  "TEST",
			SourceRevisionID: "fixture",
		})
	}
	facts := BuildFacts(rows)
	hash, err := contract.NormalizedHash(facts)
	if err != nil {
		t.Fatal(err)
	}
	if hash == "" {
		t.Fatal("empty hash")
	}
	foundMA60 := false
	for _, fact := range facts {
		if fact.Field == "MA60" && fact.Status == "KNOWN" {
			foundMA60 = true
		}
	}
	if !foundMA60 {
		t.Fatal("expected MA60 fact")
	}
}

func two(day int) string {
	if day < 10 {
		return "0" + string(rune('0'+day))
	}
	return string(rune('0'+day/10)) + string(rune('0'+day%10))
}
