package shadow

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math/big"
	"sort"
	"time"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/go-core/internal/contract"
)

// Decimal distinguishes omitted/null from a real zero and keeps base-10 input exact.
type Decimal struct{ Rat *big.Rat }

func (d *Decimal) UnmarshalJSON(raw []byte) error {
	if bytes.Equal(raw, []byte("null")) {
		return nil
	}
	var number json.Number
	if err := json.Unmarshal(raw, &number); err != nil {
		return fmt.Errorf("invalid decimal: %w", err)
	}
	rat, ok := new(big.Rat).SetString(string(number))
	if !ok {
		return fmt.Errorf("invalid/nonfinite decimal %s", raw)
	}
	d.Rat = rat
	return nil
}

type SourceRow struct {
	Instrument       string                            `json:"instrument"`
	Exchange         string                            `json:"exchange"`
	Session          string                            `json:"session"`
	Open             *Decimal                          `json:"open"`
	High             *Decimal                          `json:"high"`
	Low              *Decimal                          `json:"low"`
	Close            *Decimal                          `json:"close"`
	PrevClose        *Decimal                          `json:"prev_close"`
	ComparableClose  *Decimal                          `json:"comparable_close"`
	ComparableBasis  string                            `json:"comparable_basis"`
	SourceRevisionID string                            `json:"source_revision_id"`
	CalendarSessions []string                          `json:"calendar_sessions"`
	FieldProvenance  map[string]map[string]interface{} `json:"field_provenance"`
}

func number(d *Decimal) *big.Rat {
	if d == nil {
		return nil
	}
	return d.Rat
}

// RoundHalfEven is the explicit contract adapter: percent 4, OHLC 8, MA 6 places.
func RoundHalfEven(r *big.Rat, places int) json.Number {
	scale := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(places)), nil)
	numerator := new(big.Int).Mul(r.Num(), scale)
	negative := numerator.Sign() < 0
	numerator.Abs(numerator)
	q, remainder := new(big.Int), new(big.Int)
	q.QuoRem(numerator, r.Denom(), remainder)
	comparison := new(big.Int).Lsh(remainder, 1).Cmp(r.Denom())
	if comparison > 0 || comparison == 0 && q.Bit(0) == 1 {
		q.Add(q, big.NewInt(1))
	}
	if negative {
		q.Neg(q)
	}
	return json.Number(new(big.Rat).SetFrac(q, scale).FloatString(places))
}
func fact(row SourceRow, group, field string, exact *big.Rat, text *string, reason string) contract.Fact {
	provenance := row.FieldProvenance[field]
	if provenance == nil {
		provenance = map[string]interface{}{"status": "UNVERIFIED", "pit_status": "NOT_VERIFIED", "source_revision_id": row.SourceRevisionID}
	}
	status := "UNKNOWN"
	var value interface{}
	var exactValue map[string]string
	var unit *string
	if field != "price_basis" {
		u := "CNY"
		if group == "RETURN" {
			u = "percent"
		}
		unit = &u
	}
	if exact != nil || text != nil {
		status = "UNVERIFIED"
		reason = "SOURCE_PROVENANCE_NOT_VERIFIED"
		if contract.VerifiedProvenance(provenance) {
			status = "KNOWN"
			reason = ""
		}
		if exact != nil {
			places := 8
			if group == "RETURN" {
				places = 4
			}
			if group == "ROLLING" {
				places = 6
			}
			value = RoundHalfEven(exact, places)
			exactValue = map[string]string{"numerator": exact.Num().String(), "denominator": exact.Denom().String()}
		} else {
			value = *text
		}
	}
	var reasonValue *string
	if reason != "" {
		reasonValue = &reason
	}
	raw, _ := json.Marshal(value)
	return contract.Fact{ContractVersion: contract.Version, SchemaRevision: contract.SchemaRevision,
		Instrument: row.Instrument, Exchange: row.Exchange, Session: row.Session, Eligibility: "ELIGIBLE",
		FieldGroup: group, Field: field, Value: value, ValueJSON: string(raw), ExactValue: exactValue,
		Status: status, Reason: reasonValue, Unit: unit, Provenance: provenance, SourceLabel: "GO_SHADOW"}
}
func BuildFacts(input []SourceRow) ([]contract.Fact, error) {
	rows := append([]SourceRow(nil), input...)
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].Instrument == rows[j].Instrument {
			return rows[i].Session < rows[j].Session
		}
		return rows[i].Instrument < rows[j].Instrument
	})
	byKey := map[string]SourceRow{}
	for _, row := range rows {
		if row.Instrument == "" || (row.Exchange != "SSE" && row.Exchange != "SZSE" && row.Exchange != "BSE") {
			return nil, fmt.Errorf("invalid source identity")
		}
		if _, err := time.Parse("2006-01-02", row.Session); err != nil {
			return nil, err
		}
		key := row.Instrument + "|" + row.Session
		if _, exists := byKey[key]; exists {
			return nil, fmt.Errorf("duplicate source key %s", key)
		}
		for name, d := range map[string]*Decimal{"open": row.Open, "high": row.High, "low": row.Low, "close": row.Close, "prev_close": row.PrevClose, "comparable_close": row.ComparableClose} {
			if number(d) != nil && number(d).Sign() <= 0 {
				return nil, fmt.Errorf("non-positive %s for %s", name, key)
			}
		}
		previous := ""
		for _, session := range row.CalendarSessions {
			if _, err := time.Parse("2006-01-02", session); err != nil {
				return nil, err
			}
			if session <= previous {
				return nil, fmt.Errorf("calendar must be strictly ordered")
			}
			previous = session
		}
		byKey[key] = row
	}
	facts := []contract.Fact{}
	for _, row := range rows {
		for _, entry := range []struct {
			name  string
			value *Decimal
		}{{"open", row.Open}, {"high", row.High}, {"low", row.Low}, {"close", row.Close}, {"prev_close", row.PrevClose}, {"comparable_price", row.ComparableClose}} {
			facts = append(facts, fact(row, "PRICE", entry.name, number(entry.value), nil, "SOURCE_VALUE_MISSING"))
		}
		var basis *string
		if row.ComparableBasis != "" {
			basis = &row.ComparableBasis
		}
		facts = append(facts, fact(row, "PRICE", "price_basis", nil, basis, "SOURCE_VALUE_MISSING"))
		position := sort.SearchStrings(row.CalendarSessions, row.Session)
		positionOK := position < len(row.CalendarSessions) && row.CalendarSessions[position] == row.Session
		var ret *big.Rat
		retReason := "RETURN_WINDOW_UNAVAILABLE"
		if positionOK && position > 0 {
			retReason = "MISSING_COMPARABLE_PRICE"
			prev, exists := byKey[row.Instrument+"|"+row.CalendarSessions[position-1]]
			if exists && number(row.ComparableClose) != nil && number(prev.ComparableClose) != nil {
				retReason = "COMPARABLE_BASIS_MISMATCH"
				if row.ComparableBasis != "" && row.ComparableBasis == prev.ComparableBasis {
					ret = new(big.Rat).Quo(number(row.ComparableClose), number(prev.ComparableClose))
					ret.Sub(ret, big.NewRat(1, 1))
					ret.Mul(ret, big.NewRat(100, 1))
				}
			}
		}
		facts = append(facts, fact(row, "RETURN", "ret1_pct", ret, nil, retReason))
		for _, window := range []int{20, 60} {
			var mean *big.Rat
			maReason := fmt.Sprintf("TCT_MA%d_WINDOW_UNAVAILABLE", window)
			if positionOK && position-window+1 >= 0 {
				maReason = fmt.Sprintf("TCT_MA%d_PRICE_WINDOW_INCOMPLETE", window)
				sum := new(big.Rat)
				ok := true
				for _, session := range row.CalendarSessions[position-window+1 : position+1] {
					source, exists := byKey[row.Instrument+"|"+session]
					if !exists {
						ok = false
						break
					}
					if number(source.ComparableClose) == nil || row.ComparableBasis == "" || source.ComparableBasis != row.ComparableBasis {
						maReason = fmt.Sprintf("TCT_MA%d_COMPARABLE_BASIS_MISMATCH", window)
						ok = false
						break
					}
					sum.Add(sum, number(source.ComparableClose))
				}
				if ok {
					mean = new(big.Rat).Quo(sum, big.NewRat(int64(window), 1))
				}
			}
			facts = append(facts, fact(row, "ROLLING", fmt.Sprintf("MA%d", window), mean, nil, maReason))
		}
	}
	for _, f := range facts {
		if err := contract.ValidateFact(f); err != nil {
			return nil, fmt.Errorf("%s/%s/%s: %w", f.Instrument, f.Session, f.Field, err)
		}
	}
	return facts, nil
}
