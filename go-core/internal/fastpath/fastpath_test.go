package fastpath

import (
	"fmt"
	"testing"
)

func TestBuildRuntimeRowsComputesExactRollingFactsAndMissingRows(t *testing.T) {
	sessions := make([]string, 0, 20)
	rows := make([]SourceRow, 0, 20)
	for day := 1; day <= 20; day++ {
		session := fmt.Sprintf("2026-01-%02d", day)
		sessions = append(sessions, session)
		rows = append(rows, SourceRow{
			InstrumentID:     "600001.SH",
			Session:          session,
			ComparableClose:  mustDecimal(t, day),
			ComparableBasis:  "BAOSTOCK_ADJ",
			AmountCNY:        mustDecimal(t, 100+day),
			TurnoverFraction: mustDecimal(t, 1),
		})
	}
	resp, err := BuildRuntimeRows(Request{
		Sessions:       sessions,
		TargetSessions: []string{sessions[19]},
		Instruments:    []string{"600001.SH", "600002.SH"},
		Rows:           rows,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(resp.Rows) != 2 {
		t.Fatalf("rows=%d", len(resp.Rows))
	}
	first := resp.Rows[0]
	if first.InstrumentID != "600001.SH" {
		t.Fatalf("unexpected first instrument %s", first.InstrumentID)
	}
	if got := first.Returns[0]; got == nil || got[0] != "1" || got[1] != "19" {
		t.Fatalf("ret1 mismatch: %#v", got)
	}
	if got := first.MA[2]; got == nil || got[0] != "21" || got[1] != "2" {
		t.Fatalf("ma20 mismatch: %#v", got)
	}
	missing := resp.Rows[1]
	if missing.InstrumentID != "600002.SH" {
		t.Fatalf("unexpected missing instrument %s", missing.InstrumentID)
	}
	if got := missing.MAReasons[2]; got != "TCT_MA20_PRICE_WINDOW_INCOMPLETE" {
		t.Fatalf("missing MA reason mismatch: %s", got)
	}
	if missing.Returns[0] != nil {
		t.Fatalf("missing return should stay nil: %#v", missing.Returns[0])
	}
}

func TestBuildRuntimeRowsReportsIncompleteWindowBeforeBasisMismatch(t *testing.T) {
	sessions := make([]string, 0, 20)
	rows := make([]SourceRow, 0, 19)
	for day := 1; day <= 20; day++ {
		session := fmt.Sprintf("2026-02-%02d", day)
		sessions = append(sessions, session)
		if day == 7 {
			continue
		}
		basis := "BAOSTOCK_ADJ"
		if day == 20 {
			basis = ""
		}
		rows = append(rows, SourceRow{
			InstrumentID:     "600001.SH",
			Session:          session,
			ComparableClose:  mustDecimal(t, day),
			ComparableBasis:  basis,
			AmountCNY:        mustDecimal(t, 100+day),
			TurnoverFraction: mustDecimal(t, 1),
		})
	}
	resp, err := BuildRuntimeRows(Request{
		Sessions:       sessions,
		TargetSessions: []string{sessions[19]},
		Instruments:    []string{"600001.SH"},
		Rows:           rows,
	})
	if err != nil {
		t.Fatal(err)
	}
	if got := resp.Rows[0].MAReasons[2]; got != "TCT_MA20_PRICE_WINDOW_INCOMPLETE" {
		t.Fatalf("ma20 reason mismatch: %s", got)
	}
}

func mustDecimal(t *testing.T, value int) *Decimal {
	t.Helper()
	decimal := &Decimal{}
	if err := decimal.UnmarshalJSON([]byte(fmt.Sprintf("%q", fmt.Sprint(value)))); err != nil {
		t.Fatal(err)
	}
	return decimal
}
