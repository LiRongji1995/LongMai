package shadow

import (
	"encoding/json"
	"fmt"
	"math/big"
	"testing"
	"time"
)

func fixture(t *testing.T, n int) []SourceRow {
	calendar := []string{}
	start := time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC)
	for i := 0; i < n; i++ {
		calendar = append(calendar, start.AddDate(0, 0, i).Format("2006-01-02"))
	}
	payload := []map[string]interface{}{}
	for i, session := range calendar {
		close := fmt.Sprintf("%.1f", 10+float64(i)*0.2)
		prov := map[string]interface{}{"status": "KNOWN", "pit_status": "VISIBLE", "source_refs": []string{"TEST_FIXTURE"}, "source_revision_id": "TEST_FIXTURE_" + session, "visible_at": session + "T15:00:00+08:00", "decision_cutoff": session + "T20:30:00+08:00"}
		perField := map[string]interface{}{}
		for _, field := range []string{"open", "high", "low", "close", "prev_close", "comparable_price", "price_basis", "ret1_pct", "MA20", "MA60"} {
			perField[field] = prov
		}
		payload = append(payload, map[string]interface{}{"instrument": "000001.SZ", "exchange": "SZSE", "session": session, "open": close, "high": close, "low": close, "close": close, "prev_close": "10", "comparable_close": close, "comparable_basis": "TEST_FIXTURE", "calendar_sessions": calendar, "field_provenance": perField})
	}
	raw, _ := json.Marshal(payload)
	var rows []SourceRow
	if err := json.Unmarshal(raw, &rows); err != nil {
		t.Fatal(err)
	}
	return rows
}
func TestProductionPercentAndMissing(t *testing.T) {
	rows := fixture(t, 60)
	facts, err := BuildFacts(rows)
	if err != nil {
		t.Fatal(err)
	}
	foundMA := false
	for _, f := range facts {
		if f.Session == rows[1].Session && f.Field == "ret1_pct" {
			if f.ValueJSON != "2.0000" || *f.Unit != "percent" || f.ExactValue["numerator"] != "2" {
				t.Fatalf("wrong production percent: %+v", f)
			}
		}
		if f.Field == "MA60" && f.Status == "KNOWN" {
			foundMA = true
		}
	}
	if !foundMA {
		t.Fatal("MA60 missing")
	}
	rows[0].Open = nil
	facts, err = BuildFacts(rows)
	if err != nil {
		t.Fatal(err)
	}
	if facts[0].Status != "UNKNOWN" || facts[0].Value != nil {
		t.Fatal("missing became known zero")
	}
}
func TestRejectBadInput(t *testing.T) {
	for _, value := range []string{`true`, `"abc"`, `{}`, `[]`, `NaN`, `Infinity`} {
		var d Decimal
		if json.Unmarshal([]byte(value), &d) == nil {
			t.Errorf("accepted %s", value)
		}
	}
	rows := fixture(t, 2)
	rows[0].Close = &Decimal{big.NewRat(0, 1)}
	if _, err := BuildFacts(rows); err == nil {
		t.Fatal("accepted zero price")
	}
	rows = fixture(t, 2)
	rows = append(rows, rows[0])
	if _, err := BuildFacts(rows); err == nil {
		t.Fatal("accepted duplicate")
	}
}
func TestGapAndUnverified(t *testing.T) {
	rows := fixture(t, 21)
	rows = append(rows[:10], rows[11:]...)
	facts, err := BuildFacts(rows)
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range facts {
		if f.Field == "MA20" && f.Status == "KNOWN" {
			t.Fatal("calendar gap silently skipped")
		}
	}
	rows = fixture(t, 2)
	rows[0].FieldProvenance = nil
	facts, err = BuildFacts(rows)
	if err != nil {
		t.Fatal(err)
	}
	if facts[0].Status != "UNVERIFIED" {
		t.Fatal("missing provenance upgraded")
	}
}
func TestHalfEven(t *testing.T) {
	for _, example := range []struct {
		r    string
		want string
	}{{"1.23445", "1.2344"}, {"1.23455", "1.2346"}, {"-1.23445", "-1.2344"}} {
		r, _ := new(big.Rat).SetString(example.r)
		if got := string(RoundHalfEven(r, 4)); got != example.want {
			t.Fatalf("%s != %s", got, example.want)
		}
	}
}
