package facts

import (
	"context"
	"testing"
)

func TestMissingComparableFieldDistinguishesMissingSession(t *testing.T) {
	w := Window{Sessions: []string{"1", "2"}, Bars: map[string]Bar{"1": {Basis: "B", Close: N("1")}, "2": {Basis: "B"}}}
	value, e := w.MA(context.Background(), 1, 2)
	if e != nil || value.Known() {
		t.Fatal(value, e)
	}
	if reason := w.maReason(1, 2, false); reason != "SAM_MA2_COMPARABLE_BASIS_MISMATCH" {
		t.Fatal(reason)
	}
	delete(w.Bars, "2")
	if reason := w.maReason(1, 2, false); reason != "SAM_MA2_PRICE_WINDOW_INCOMPLETE" {
		t.Fatal(reason)
	}
}
func TestLimitOHLCOrderHasOriginalReason(t *testing.T) {
	app := true
	b := Bar{Open: N("10"), High: N("9"), Low: N("8"), RawClose: N("9"), LimitUp: N("11"), LimitDown: N("8"), Tick: N(".01"), LimitApplicable: &app, LimitVerified: true}
	_, _, reason := Limits(&b)
	if reason != "INVALID_OHLC_ORDER" {
		t.Fatal(reason)
	}
}

func TestFundsDedupePreservesDecimalScaleIdentity(t *testing.T) {
	a := Row{"buy_amount_cny": []byte(`"70000000.0"`)}
	b := Row{"buy_amount_cny": []byte(`"70000000"`)}
	if fundFingerprint(a) == fundFingerprint(b) {
		t.Fatal("Decimal scale identity lost in disclosure conflict detection")
	}
	a["buy_amount_cny"] = []byte(`"1e2"`)
	b["buy_amount_cny"] = []byte(`"1E+2"`)
	if fundFingerprint(a) != fundFingerprint(b) {
		t.Fatal("equivalent Decimal exponent identity differs")
	}
}
