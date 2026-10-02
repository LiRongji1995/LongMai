package facts

import (
	"bytes"
	"context"
	"sort"
	"strconv"
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
	if b.Quality == "INVALID" || !b.Open.Known() || !b.High.Known() || !b.Low.Known() || !b.RawClose.Known() {
		return up, down, "INVALID_OHLC"
	}
	if !validOHLC(b) {
		return up, down, "INVALID_OHLC_ORDER"
	}
	for _, v := range []Number{b.Open, b.High, b.Low, b.RawClose} {
		if !v.Div(b.Tick).IsInt() {
			return up, down, "OFF_TICK"
		}
	}
	if b.LimitApplicable == nil || !b.LimitVerified || !b.LimitUp.Known() || !b.LimitDown.Known() {
		return up, down, "LIMIT_RULE_UNVERIFIED"
	}
	if !b.LimitUp.Div(b.Tick).IsInt() || !b.LimitDown.Div(b.Tick).IsInt() {
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
	Reasons                                          []string `json:"reason_codes"`
	Available                                        bool
	CloseReturn, LowReturn, Amplitude, Recovery, CLV Number
	CloseLimitDown, TouchedLimitDown, LimitQuality   string
}

func DailyPath(b *Bar) Path {
	p := Path{CloseLimitDown: "UNKNOWN", TouchedLimitDown: "UNKNOWN"}
	if b == nil {
		p.Reasons = []string{"SAM_DAILY_BAR_UNAVAILABLE"}
		return p
	}
	if !validOHLC(b) || !b.Previous.Positive() {
		p.Reasons = []string{"SAM_RAW_DAILY_PATH_UNAVAILABLE_OR_INVALID"}
		return p
	}
	p.Reasons = []string{}
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
	MatchedMA    int            `json:"matched_ma"`
	ShortHealth  string         `json:"short_term_trend_health"`
	LongBullish  bool           `json:"long_term_bullish_arrangement"`
	ShortBullish bool           `json:"short_term_bullish_arrangement"`
	Bullish      bool           `json:"bullish_arrangement"`
	Distances    map[int]Number `json:"dist_ma"`

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
		b = &Bar{}
	}
	for _, n := range []int{5, 10, 20} {
		d := b.Close.Sub(t.MA[n]).Abs().Div(t.MA[n])
		if d.Known() && (!t.Distance.Known() || d.Cmp(t.Distance) < 0) {
			t.Distance = d
			t.MatchedMA = n
		}
	}
	t.Distances = map[int]Number{}
	for _, n := range []int{5, 10, 20, 60, 120, 250} {
		t.Distances[n] = b.Close.Sub(t.MA[n]).Div(t.MA[n]).Rounded(6)
	}
	t.LongBullish = t.MA[60].Known() && t.MA[120].Known() && t.MA[250].Known() && t.MA[60].Cmp(t.MA[120]) > 0 && t.MA[120].Cmp(t.MA[250]) > 0
	t.ShortBullish = t.MA[5].Known() && t.MA[10].Known() && t.MA[20].Known() && t.MA[5].Cmp(t.MA[10]) > 0 && t.MA[10].Cmp(t.MA[20]) > 0
	t.Bullish = t.LongBullish && t.ShortBullish && t.MA[20].Cmp(t.MA[60]) > 0
	t.ShortHealth = "UNKNOWN"
	if t.MA[5].Known() && t.MA[10].Known() && t.MA[20].Known() {
		t.ShortHealth = "PRESSURED"
		if t.ShortBullish && b.Close.Known() && b.Close.Cmp(t.MA[20]) >= 0 {
			t.ShortHealth = "HEALTHY"
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
		reasons := []string{}
		if !b.Close.Known() {
			reasons = append(reasons, "SAM_COMPARABLE_CLOSE_UNAVAILABLE")
		}
		for _, n := range []int{60, 120, 250} {
			if !t.MA[n].Known() {
				reasons = append(reasons, w.maReason(i, n, false))
			}
			if !t.Slopes[n].Known() {
				past, _ := w.MA(ctx, i-5, n)
				if !past.Known() {
					reasons = append(reasons, w.maReason(i-5, n, true))
				}
			}
		}
		sort.Strings(reasons)
		t.Condition = Condition{Status: Unknown, Reasons: reasons}
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
		reasons := []string{}
		for _, n := range []int{120, 250} {
			if b.Close.Cmp(t.MA[n]) < 0 {
				reasons = append(reasons, "SAM_CLOSE_BELOW_MA"+strconv.Itoa(n))
			}
		}
		t.Condition = Condition{Status: Fail, Reasons: reasons}
	} else if deteriorating {
		t.State = "TREND_DETERIORATING"
		reasons := []string{}
		if t.MA[60].Cmp(t.MA[120]) <= 0 || t.MA[120].Cmp(t.MA[250]) <= 0 {
			reasons = append(reasons, "SAM_LONG_MA_BULLISH_ARRANGEMENT_FAIL")
		}
		for _, n := range []int{60, 120, 250} {
			if t.Slopes[n].Cmp(N("0")) < 0 {
				reasons = append(reasons, "SAM_MA"+strconv.Itoa(n)+"_SLOPE_NEGATIVE")
			}
		}
		t.Condition = Condition{Status: Fail, Reasons: reasons}
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

// Percentiles computes midpoint tie ranks once per population. Missing values
// never contribute to the denominator. Exact rational keys preserve thresholds.
type Percentiles map[string]Number

func NewPercentiles(values []Number) Percentiles {
	sorted := make([]Number, 0, len(values))
	for _, v := range values {
		if v.Known() {
			sorted = append(sorted, v)
		}
	}
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Cmp(sorted[j]) < 0 })
	out := Percentiles{}
	for i := 0; i < len(sorted); {
		j := i + 1
		for j < len(sorted) && sorted[i].Cmp(sorted[j]) == 0 {
			j++
		}
		out[sorted[i].String()] = count(i + j).Mul(N("50")).Div(count(len(sorted)))
		i = j
	}
	return out
}
func (p Percentiles) Lookup(v Number) Number {
	if !v.Known() {
		return Number{}
	}
	return p[v.String()]
}

func (w Window) maReason(i, n int, slope bool) string {
	prefix := "SAM_MA" + strconv.Itoa(n) + "_"
	if slope {
		prefix += "SLOPE_"
	}
	if i-n+1 < 0 {
		return prefix + "WINDOW_UNAVAILABLE"
	}
	basis := ""
	for j := i - n + 1; j <= i; j++ {
		b := w.At(j)
		if b == nil {
			return prefix + "PRICE_WINDOW_INCOMPLETE"
		}
		if !b.Close.Positive() || b.Basis == "" || (basis != "" && b.Basis != basis) {
			return prefix + "COMPARABLE_BASIS_MISMATCH"
		}
		basis = b.Basis
	}
	return prefix + "WINDOW_UNAVAILABLE"
}
