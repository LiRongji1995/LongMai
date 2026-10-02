package core

import (
	"bytes"
	"context"
	"sort"
)

type Bar struct {
	Session, Basis, Hash                                          string
	Close, Amount, Turnover                                       Number
	Open, High, Low, RawClose, Previous, LimitUp, LimitDown, Tick Number
	LimitApplicable                                               *bool
	LimitVerified                                                 bool
	Quality                                                       string
	Sources                                                       []string
}

func ParseBar(r Row) (Bar, error) {
	b := Bar{Session: r.Text("session"), Basis: r.Text("comparable_basis"), Hash: RowHash(r), LimitApplicable: r.Bool("limit_applicable"), Quality: r.Text("quality"), Sources: r.Strings("source_refs")}
	if p := r.Bool("limit_verified"); p != nil {
		b.LimitVerified = *p
	}
	fields := map[string]*Number{"comparable_close": &b.Close, "amount_cny": &b.Amount, "turnover_fraction": &b.Turnover, "open": &b.Open, "high": &b.High, "low": &b.Low, "close": &b.RawClose, "reference_prev_close": &b.Previous, "limit_up": &b.LimitUp, "limit_down": &b.LimitDown, "tick_size": &b.Tick}
	for k, d := range fields {
		if raw, ok := r[k]; ok {
			if bytes.Contains(raw, []byte("/")) {
				return Bar{}, errCode("RAW_DECIMAL_REQUIRED")
			}
			if e := d.UnmarshalJSON(raw); e != nil {
				return Bar{}, e
			}
		}
	}
	if !b.Tick.Known() {
		b.Tick = N(".01")
	}
	for _, v := range []Number{b.Open, b.High, b.Low, b.RawClose, b.Close, b.Previous, b.LimitUp, b.LimitDown, b.Tick} {
		if v.Known() && !v.Positive() {
			return Bar{}, errCode("INVALID_POSITIVE_PRICE")
		}
	}
	for _, v := range []Number{b.Amount, b.Turnover} {
		if v.Known() && v.Cmp(N("0")) < 0 {
			return Bar{}, errCode("NEGATIVE_AMOUNT_OR_TURNOVER")
		}
	}
	if b.Turnover.Known() && b.Turnover.Cmp(N("10")) > 0 {
		return Bar{}, errCode("INVALID_TURNOVER")
	}
	return b, nil
}
func Limits(b *Bar) (up, down, quality string) {
	up, down = "UNKNOWN", "UNKNOWN"
	if b == nil {
		return up, down, "MISSING_BAR"
	}
	if b.LimitApplicable != nil && !*b.LimitApplicable {
		return "NOT_APPLICABLE", "NOT_APPLICABLE", "RULE_NOT_APPLICABLE"
	}
	if b.Quality == "INVALID" || !validOHLC(b) {
		return up, down, "INVALID_OHLC"
	}
	for _, v := range []Number{b.Open, b.High, b.Low, b.RawClose} {
		if !v.Div(b.Tick).r.IsInt() {
			return up, down, "OFF_TICK"
		}
	}
	if b.LimitApplicable == nil || !b.LimitVerified || !b.LimitUp.Known() || !b.LimitDown.Known() {
		return up, down, "LIMIT_RULE_UNVERIFIED"
	}
	if !b.LimitUp.Div(b.Tick).r.IsInt() || !b.LimitDown.Div(b.Tick).r.IsInt() {
		return up, down, "LIMIT_OFF_TICK"
	}
	if b.High.Cmp(b.LimitUp) > 0 || b.Low.Cmp(b.LimitDown) < 0 {
		return up, down, "PRICE_OUTSIDE_VERIFIED_LIMIT"
	}
	up, down = "FALSE", "FALSE"
	if b.RawClose.Cmp(b.LimitUp) == 0 {
		up = "TRUE"
	}
	if b.RawClose.Cmp(b.LimitDown) == 0 {
		down = "TRUE"
	}
	return up, down, "VALID"
}
func validOHLC(b *Bar) bool {
	return b != nil && b.Open.Positive() && b.High.Positive() && b.Low.Positive() && b.RawClose.Positive() && b.Low.Cmp(b.Open) <= 0 && b.Low.Cmp(b.RawClose) <= 0 && b.High.Cmp(b.Open) >= 0 && b.High.Cmp(b.RawClose) >= 0
}

type Path struct {
	Available                                        bool
	CloseReturn, LowReturn, Amplitude, Recovery, CLV Number
	CloseLimitDown, TouchedLimitDown, LimitQuality   string
}

func DailyPath(b *Bar) Path {
	p := Path{CloseLimitDown: "UNKNOWN", TouchedLimitDown: "UNKNOWN"}
	if !validOHLC(b) || !b.Previous.Positive() {
		return p
	}
	p.Available = true
	p.CloseReturn = b.RawClose.Div(b.Previous).Sub(N("1"))
	p.LowReturn = b.Low.Div(b.Previous).Sub(N("1"))
	p.Amplitude = b.High.Sub(b.Low).Div(b.Previous)
	p.Recovery = p.CloseReturn.Sub(p.LowReturn)
	p.CLV = b.RawClose.Sub(b.Low).Div(b.High.Sub(b.Low)).Rounded(6)
	_, p.CloseLimitDown, p.LimitQuality = Limits(b)
	if b.LimitApplicable != nil && !*b.LimitApplicable {
		p.TouchedLimitDown = "NOT_APPLICABLE"
	} else if b.LimitVerified && b.LimitDown.Known() {
		p.TouchedLimitDown = "FALSE"
		if b.Low.Cmp(b.LimitDown) == 0 {
			p.TouchedLimitDown = "TRUE"
		}
	}
	return p
}

type Window struct {
	Sessions []string
	Bars     map[string]Bar
}

func (w Window) At(i int) *Bar {
	if i < 0 || i >= len(w.Sessions) {
		return nil
	}
	b, ok := w.Bars[w.Sessions[i]]
	if !ok {
		return nil
	}
	return &b
}
func (w Window) Return(i, n int) Number {
	a, b := w.At(i-n), w.At(i)
	if a == nil || b == nil || a.Basis != b.Basis || !a.Close.Positive() || !b.Close.Positive() {
		return Number{}
	}
	return b.Close.Div(a.Close).Sub(N("1"))
}
func (w Window) MA(ctx context.Context, i, n int) (Number, error) {
	if n <= 0 || i-n+1 < 0 {
		return Number{}, nil
	}
	sum := N("0")
	basis := ""
	for k := i - n + 1; k <= i; k++ {
		if e := ctx.Err(); e != nil {
			return Number{}, e
		}
		b := w.At(k)
		if b == nil || !b.Close.Positive() || b.Basis == "" || (basis != "" && basis != b.Basis) {
			return Number{}, nil
		}
		basis = b.Basis
		sum = sum.Add(b.Close)
	}
	return sum.Div(count(n)), nil
}
func (w Window) Average(i, n int, field string) Number {
	if i-n+1 < 0 {
		return Number{}
	}
	sum := N("0")
	for k := i - n + 1; k <= i; k++ {
		b := w.At(k)
		if b == nil {
			return Number{}
		}
		v := b.Amount
		if field == "turnover" {
			v = b.Turnover
		}
		if !v.Known() {
			return Number{}
		}
		sum = sum.Add(v)
	}
	return sum.Div(count(n))
}
func (w Window) HighTurnover(i, n int) Number {
	if i-n+1 < 0 {
		return Number{}
	}
	hits := 0
	for k := i - n + 1; k <= i; k++ {
		b := w.At(k)
		if b == nil || !b.Turnover.Known() {
			return Number{}
		}
		if b.Turnover.Cmp(N(".07")) >= 0 {
			hits++
		}
	}
	return count(hits)
}
func (w Window) PriorHigh(i, n int) Number {
	if i-n < 0 {
		return Number{}
	}
	var high Number
	for k := i - n; k < i; k++ {
		b := w.At(k)
		if b == nil || !b.Close.Known() {
			return Number{}
		}
		if !high.Known() || high.Cmp(b.Close) < 0 {
			high = b.Close
		}
	}
	return high
}

type Benchmark struct {
	Value    Number `json:"value"`
	Valid    int    `json:"valid_count"`
	Expected int    `json:"expected_count"`
	Coverage Number `json:"coverage"`
	Status   string `json:"status"`
}

func Market(values []Number, expected int) Benchmark {
	b := Benchmark{Expected: expected, Status: "UNKNOWN", Coverage: N("0")}
	sum := N("0")
	for _, v := range values {
		if v.Known() {
			sum = sum.Add(v)
			b.Valid++
		}
	}
	if expected > 0 {
		b.Coverage = count(b.Valid).Div(count(expected))
	}
	if b.Valid > 0 {
		b.Status = "INSUFFICIENT_COVERAGE"
		if b.Coverage.Cmp(N(".6")) >= 0 {
			b.Status = "COMPLETE"
			b.Value = sum.Div(count(b.Valid))
		}
	}
	return b
}
func Percentile(v Number, values []Number) Number {
	if !v.Known() {
		return Number{}
	}
	less, equal, n := 0, 0, 0
	for _, x := range values {
		if !x.Known() {
			continue
		}
		n++
		c := x.Cmp(v)
		if c < 0 {
			less++
		}
		if c == 0 {
			equal++
		}
	}
	if n == 0 {
		return Number{}
	}
	return count(2*less + equal).Mul(N("50")).Div(count(n))
}
func Momentum(recent, previous, recentRS, previousRS Number) string {
	state := func(n Number) string {
		if !n.Known() {
			return "UNKNOWN"
		}
		if n.Cmp(N(".001")) > 0 {
			return "RISING"
		}
		if n.Cmp(N("-.001")) < 0 {
			return "FALLING"
		}
		return "STABLE"
	}
	a, b := state(recent.Sub(previous)), state(recentRS.Sub(previousRS))
	if a == "UNKNOWN" || b == "UNKNOWN" {
		return "UNKNOWN"
	}
	if a == "FALLING" || b == "FALLING" {
		return "FALLING"
	}
	if a == "RISING" || b == "RISING" {
		return "RISING"
	}
	return "STABLE"
}

type Trend struct {
	Condition      Condition
	State          string
	Days, Distance Number
	MA             map[int]Number
	Slopes         map[int]Number
}

func (w Window) Trend(ctx context.Context, i int) (Trend, error) {
	t := Trend{State: "UNKNOWN", Condition: C(Unknown, "SAM_LONG_TREND_UNAVAILABLE"), MA: map[int]Number{}, Slopes: map[int]Number{}}
	for _, n := range []int{5, 10, 20, 60, 120, 250} {
		v, e := w.MA(ctx, i, n)
		if e != nil {
			return t, e
		}
		t.MA[n] = v
	}
	days := 0
	for k := i; k >= 0; k-- {
		if e := ctx.Err(); e != nil {
			return t, e
		}
		b := w.At(k)
		m, e := w.MA(ctx, k, 5)
		if e != nil {
			return t, e
		}
		if b == nil || !b.Close.Known() || !m.Known() {
			days = -1
			break
		}
		if b.Close.Cmp(m) >= 0 {
			break
		}
		days++
	}
	if days >= 0 {
		t.Days = count(days)
	}
	b := w.At(i)
	if b == nil {
		return t, nil
	}
	for _, n := range []int{5, 10, 20} {
		d := b.Close.Sub(t.MA[n]).Abs().Div(t.MA[n])
		if d.Known() && (!t.Distance.Known() || d.Cmp(t.Distance) < 0) {
			t.Distance = d
		}
	}
	t.Distance = t.Distance.Rounded(6)
	unknown := !b.Close.Known()
	for _, n := range []int{60, 120, 250} {
		past, e := w.MA(ctx, i-5, n)
		if e != nil {
			return t, e
		}
		t.Slopes[n] = t.MA[n].Sub(past).Div(past).Rounded(6)
		unknown = unknown || !t.Slopes[n].Known() || !t.MA[n].Known()
	}
	if unknown {
		return t, nil
	}
	broken := b.Close.Cmp(t.MA[120]) < 0 || b.Close.Cmp(t.MA[250]) < 0
	deteriorating := t.MA[60].Cmp(t.MA[120]) <= 0 || t.MA[120].Cmp(t.MA[250]) <= 0
	for _, v := range t.Slopes {
		deteriorating = deteriorating || v.Cmp(N("0")) < 0
	}
	t.State = "STRONG_UPTREND"
	t.Condition = C(Pass, "")
	if broken {
		t.State = "TREND_BROKEN"
		t.Condition = C(Fail, "SAM_LONG_CLOSE_BELOW_MA")
	} else if deteriorating {
		t.State = "TREND_DETERIORATING"
		t.Condition = C(Fail, "SAM_LONG_TREND_DETERIORATING")
	}
	return t, nil
}
func SortedIDs(m map[string]Window) []string {
	a := []string{}
	for k := range m {
		a = append(a, k)
	}
	sort.Strings(a)
	return a
}
