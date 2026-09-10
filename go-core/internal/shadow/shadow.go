package shadow

import (
	"encoding/json"
	"sort"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/go-core/internal/contract"
)

type SourceRow struct {
	Instrument       string  `json:"instrument"`
	Exchange         string  `json:"exchange"`
	Session          string  `json:"session"`
	Open             float64 `json:"open"`
	High             float64 `json:"high"`
	Low              float64 `json:"low"`
	Close            float64 `json:"close"`
	PrevClose        float64 `json:"prev_close"`
	ComparableClose  float64 `json:"comparable_close"`
	ComparableBasis  string  `json:"comparable_basis"`
	SourceRevisionID string  `json:"source_revision_id"`
}

func valueJSON(value interface{}) string {
	raw, _ := json.Marshal(value)
	return string(raw)
}

func fact(row SourceRow, group, field string, value interface{}, status string) contract.Fact {
	return contract.Fact{
		ContractVersion: contract.Version,
		Instrument:      row.Instrument,
		Exchange:        row.Exchange,
		Session:         row.Session,
		Eligibility:     "ELIGIBLE",
		FieldGroup:      group,
		Field:           field,
		Value:           value,
		ValueJSON:       valueJSON(value),
		Status:          status,
		Provenance: map[string]interface{}{
			"source":             "GO_SHADOW_SLICE_R16",
			"source_revision_id": row.SourceRevisionID,
			"comparable_basis":   row.ComparableBasis,
		},
		SourceLabel: "GO_SHADOW",
	}
}

func BuildFacts(rows []SourceRow) []contract.Fact {
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].Instrument == rows[j].Instrument {
			return rows[i].Session < rows[j].Session
		}
		return rows[i].Instrument < rows[j].Instrument
	})
	byInstrument := map[string][]SourceRow{}
	for _, row := range rows {
		byInstrument[row.Instrument] = append(byInstrument[row.Instrument], row)
	}
	var facts []contract.Fact
	for _, row := range rows {
		status := "KNOWN"
		facts = append(facts,
			fact(row, "PRICE", "open", row.Open, status),
			fact(row, "PRICE", "high", row.High, status),
			fact(row, "PRICE", "low", row.Low, status),
			fact(row, "PRICE", "close", row.Close, status),
			fact(row, "PRICE", "prev_close", row.PrevClose, status),
			fact(row, "PRICE", "comparable_price", row.ComparableClose, status),
			fact(row, "PRICE", "price_basis", row.ComparableBasis, status),
		)
		instrumentRows := byInstrument[row.Instrument]
		position := sort.Search(len(instrumentRows), func(i int) bool { return instrumentRows[i].Session >= row.Session })
		if position > 0 && instrumentRows[position-1].ComparableClose > 0 && row.ComparableBasis == instrumentRows[position-1].ComparableBasis {
			ret := row.ComparableClose/instrumentRows[position-1].ComparableClose - 1
			facts = append(facts, fact(row, "RETURN", "ret1_pct", ret, "KNOWN"))
		} else {
			facts = append(facts, fact(row, "RETURN", "ret1_pct", nil, "UNKNOWN"))
		}
		for _, window := range []int{20, 60} {
			field := "MA20"
			if window == 60 {
				field = "MA60"
			}
			if position-window+1 < 0 {
				facts = append(facts, fact(row, "ROLLING", field, nil, "UNKNOWN"))
				continue
			}
			sum := 0.0
			ok := true
			for i := position - window + 1; i <= position; i++ {
				if instrumentRows[i].ComparableBasis != row.ComparableBasis || instrumentRows[i].ComparableClose <= 0 {
					ok = false
					break
				}
				sum += instrumentRows[i].ComparableClose
			}
			if ok {
				facts = append(facts, fact(row, "ROLLING", field, sum/float64(window), "KNOWN"))
			} else {
				facts = append(facts, fact(row, "ROLLING", field, nil, "UNKNOWN"))
			}
		}
	}
	return facts
}
