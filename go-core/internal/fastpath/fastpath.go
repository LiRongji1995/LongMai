package fastpath

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"sort"
	"time"
)

var retWindows = []int{1, 3, 5, 10, 20}
var amountChangeWindows = []int{1, 3, 5, 10, 20}
var maWindows = []int{5, 10, 20, 50, 60, 120, 150, 200, 250}

type Decimal struct {
	Rat *big.Rat
}

func (d *Decimal) UnmarshalJSON(raw []byte) error {
	if bytes.Equal(raw, []byte("null")) {
		return nil
	}
	var text string
	if len(raw) > 0 && raw[0] == '"' {
		if err := json.Unmarshal(raw, &text); err != nil {
			return fmt.Errorf("invalid decimal string: %w", err)
		}
	} else {
		var number json.Number
		if err := json.Unmarshal(raw, &number); err != nil {
			return fmt.Errorf("invalid decimal: %w", err)
		}
		text = string(number)
	}
	if text == "" {
		return nil
	}
	rat, ok := new(big.Rat).SetString(text)
	if !ok {
		return fmt.Errorf("invalid/nonfinite decimal %q", text)
	}
	d.Rat = rat
	return nil
}

type SourceRow struct {
	InstrumentID     string   `json:"instrument_id"`
	Session          string   `json:"session"`
	ComparableClose  *Decimal `json:"comparable_close"`
	ComparableBasis  string   `json:"comparable_basis"`
	AmountCNY        *Decimal `json:"amount_cny"`
	TurnoverFraction *Decimal `json:"turnover_fraction"`
}

type Request struct {
	Sessions       []string    `json:"sessions"`
	TargetSessions []string    `json:"target_sessions"`
	Instruments    []string    `json:"instruments"`
	Rows           []SourceRow `json:"rows"`
}

type Pair [2]string

type RuntimeRow struct {
	InstrumentID string   `json:"instrument_id"`
	Session      string   `json:"session"`
	Returns      []*Pair  `json:"returns"`
	AmountChange []*Pair  `json:"amount_change"`
	MA           []*Pair  `json:"ma"`
	MAReasons    []string `json:"ma_reasons"`
}

type Response struct {
	Rows  []RuntimeRow           `json:"rows"`
	Stats map[string]interface{} `json:"stats"`
}

func rat(value *Decimal) *big.Rat {
	if value == nil {
		return nil
	}
	return value.Rat
}

func pair(value *big.Rat) *Pair {
	if value == nil {
		return nil
	}
	return &Pair{value.Num().String(), value.Denom().String()}
}

func validateDate(value string) error {
	_, err := time.Parse("2006-01-02", value)
	return err
}

func BuildRuntimeRows(req Request) (Response, error) {
	if len(req.Sessions) == 0 {
		return Response{}, errors.New("sessions required")
	}
	sessionPosition := make(map[string]int, len(req.Sessions))
	previous := ""
	for index, session := range req.Sessions {
		if err := validateDate(session); err != nil {
			return Response{}, err
		}
		if previous != "" && session <= previous {
			return Response{}, errors.New("sessions must be strictly ordered")
		}
		sessionPosition[session] = index
		previous = session
	}
	targets := append([]string(nil), req.TargetSessions...)
	sort.Slice(targets, func(i, j int) bool {
		return sessionPosition[targets[i]] < sessionPosition[targets[j]]
	})
	for _, session := range targets {
		if _, ok := sessionPosition[session]; !ok {
			return Response{}, fmt.Errorf("target session outside calendar: %s", session)
		}
	}
	byInstrument := map[string]map[string]SourceRow{}
	instruments := map[string]bool{}
	for _, row := range req.Rows {
		if row.InstrumentID == "" {
			return Response{}, errors.New("instrument_id required")
		}
		if _, ok := sessionPosition[row.Session]; !ok {
			return Response{}, fmt.Errorf("source row outside calendar: %s", row.Session)
		}
		if rat(row.ComparableClose) != nil && rat(row.ComparableClose).Sign() <= 0 {
			return Response{}, fmt.Errorf("non-positive comparable_close for %s/%s", row.InstrumentID, row.Session)
		}
		if rat(row.AmountCNY) != nil && rat(row.AmountCNY).Sign() < 0 {
			return Response{}, fmt.Errorf("negative amount_cny for %s/%s", row.InstrumentID, row.Session)
		}
		if rat(row.TurnoverFraction) != nil && rat(row.TurnoverFraction).Sign() < 0 {
			return Response{}, fmt.Errorf("negative turnover_fraction for %s/%s", row.InstrumentID, row.Session)
		}
		rows := byInstrument[row.InstrumentID]
		if rows == nil {
			rows = map[string]SourceRow{}
			byInstrument[row.InstrumentID] = rows
		}
		if _, exists := rows[row.Session]; exists {
			return Response{}, fmt.Errorf("duplicate source key %s/%s", row.InstrumentID, row.Session)
		}
		rows[row.Session] = row
		instruments[row.InstrumentID] = true
	}
	for _, iid := range req.Instruments {
		if iid == "" {
			return Response{}, errors.New("empty requested instrument")
		}
		instruments[iid] = true
	}
	ids := make([]string, 0, len(instruments))
	for iid := range instruments {
		ids = append(ids, iid)
	}
	sort.Strings(ids)
	result := make([]RuntimeRow, 0, len(ids)*len(targets))
	for _, iid := range ids {
		rows := byInstrument[iid]
		for _, session := range targets {
			position := sessionPosition[session]
			current, ok := rows[session]
			var currentClose *big.Rat
			currentBasis := ""
			if ok {
				currentClose = rat(current.ComparableClose)
				currentBasis = current.ComparableBasis
			}
			out := RuntimeRow{
				InstrumentID: iid,
				Session:      session,
				Returns:      make([]*Pair, len(retWindows)),
				AmountChange: make([]*Pair, len(amountChangeWindows)),
				MA:           make([]*Pair, len(maWindows)),
				MAReasons:    make([]string, len(maWindows)),
			}
			for index, window := range retWindows {
				previousIndex := position - window
				if previousIndex < 0 || currentClose == nil || currentBasis == "" {
					continue
				}
				prev := rows[req.Sessions[previousIndex]]
				prevClose := rat(prev.ComparableClose)
				if prevClose == nil || prevClose.Sign() <= 0 || prev.ComparableBasis != currentBasis {
					continue
				}
				value := new(big.Rat).Quo(currentClose, prevClose)
				value.Sub(value, big.NewRat(1, 1))
				out.Returns[index] = pair(value)
			}
			for index, window := range amountChangeWindows {
				previousIndex := position - window
				if previousIndex < 0 {
					continue
				}
				currentAmount := rat(current.AmountCNY)
				prevAmount := rat(rows[req.Sessions[previousIndex]].AmountCNY)
				if currentAmount == nil || prevAmount == nil || prevAmount.Sign() <= 0 {
					continue
				}
				value := new(big.Rat).Quo(currentAmount, prevAmount)
				value.Sub(value, big.NewRat(1, 1))
				out.AmountChange[index] = pair(value)
			}
			for index, window := range maWindows {
				if position-window+1 < 0 {
					out.MAReasons[index] = fmt.Sprintf("TCT_MA%d_WINDOW_UNAVAILABLE", window)
					continue
				}
				windowRows := make([]SourceRow, 0, window)
				windowComplete := true
				for _, sourceSession := range req.Sessions[position-window+1 : position+1] {
					row, exists := rows[sourceSession]
					if !exists {
						windowComplete = false
						break
					}
					windowRows = append(windowRows, row)
				}
				if !windowComplete {
					out.MAReasons[index] = fmt.Sprintf("TCT_MA%d_PRICE_WINDOW_INCOMPLETE", window)
					continue
				}
				basis := windowRows[0].ComparableBasis
				sum := new(big.Rat)
				sameBasis := basis != ""
				for _, row := range windowRows {
					closeValue := rat(row.ComparableClose)
					if closeValue == nil || closeValue.Sign() <= 0 || row.ComparableBasis != basis {
						sameBasis = false
						break
					}
					sum.Add(sum, closeValue)
				}
				if !sameBasis {
					out.MAReasons[index] = fmt.Sprintf("TCT_MA%d_COMPARABLE_BASIS_MISMATCH", window)
					continue
				}
				out.MA[index] = pair(new(big.Rat).Quo(sum, big.NewRat(int64(window), 1)))
			}
			result = append(result, out)
		}
	}
	return Response{
		Rows: result,
		Stats: map[string]interface{}{
			"instrument_count": len(ids),
			"target_sessions":  len(targets),
			"source_rows":      len(req.Rows),
			"runtime_rows":     len(result),
			"engine":           "go-core/internal/fastpath",
		},
	}, nil
}
