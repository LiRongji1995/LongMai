package research

import (
	"context"
	"encoding/json"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"strconv"
	"time"
)

// This disposable state shares the existing bounded/integrity-checked cache.
// It never replaces evidence resolution or the published full-result closure.
const rollingFactContract = "AUTHENTICATED_HISTORY_AND_DECODED_INPUT_SINGLE_SESSION_EXACT_WINDOWS_V2"

type rollingStateCache interface {
	CacheGet(context.Context, string, string, string) ([]byte, bool, error)
	CachePut(context.Context, string, string, string, []byte) error
}
type rollingProof struct {
	Algorithm, Coverage, Instrument, Observation, Cutoff string
	CalendarHash, SecurityHash, SecurityParents, Basis   string
	SessionCount                                         int
	FirstSession                                         string
	Rows                                                 map[string]string
}
type rollingState struct {
	Contract     string
	Proof        rollingProof
	Sums         map[string]Number
	BelowMA5Days Number
}

var rollingMAs = []int{5, 10, 20, 60, 120, 250}

func rollingStateKey(algorithm, coverage, instrument, observation string) string {
	raw, _ := json.Marshal([]string{rollingFactContract, algorithm, InputContractVersion, coverage, instrument, observation})
	return Hash(raw)
}
func loadRollingState(ctx context.Context, cache rollingStateCache, algorithm, coverage, instrument, observation string) (*rollingState, error) {
	if cache == nil || !validHash(algorithm) {
		return nil, nil
	}
	key := rollingStateKey(algorithm, coverage, instrument, observation)
	raw, hit, err := cache.CacheGet(ctx, key, rollingFactContract, key)
	if err != nil || !hit {
		return nil, err
	}
	var state rollingState
	if json.Unmarshal(raw, &state) != nil || state.Contract != rollingFactContract ||
		state.Proof.Algorithm != algorithm || state.Proof.Coverage != coverage ||
		state.Proof.Instrument != instrument || state.Proof.Observation != observation {
		return nil, nil
	}
	return &state, nil
}
func storeRollingState(ctx context.Context, cache rollingStateCache, state rollingState) error {
	raw, err := json.Marshal(state)
	if err != nil {
		return err
	}
	p := state.Proof
	key := rollingStateKey(p.Algorithm, p.Coverage, p.Instrument, p.Observation)
	return cache.CachePut(ctx, key, rollingFactContract, key, raw)
}

// History adapters authenticate original stored rows. Inline rows additionally
// need the strict decoder's private original-byte attestation; a pointer or a
// projection digest alone cannot qualify the rolling path.
func makeRollingProof(ctx context.Context, algorithm string, frozen Frozen, instrument string, state Row, sessions []string, start int, selected []Row, bindings []c.SelectedEvidenceBinding, resolver *Resolver) (*rollingProof, error) {
	if !validHash(algorithm) || !validHash(frozen.Input.HistoryHash) || len(selected) != len(bindings) {
		return nil, nil
	}
	origins := map[string]c.EvidenceIdentity{}
	for _, binding := range bindings {
		identity := binding.Identity
		if !validHash(identity.RawSHA256) || !validHash(identity.ParentHash) || !validHash(binding.ProjectionHash) {
			return nil, nil
		}
		switch identity.Origin {
		case "HISTORY_ROW":
			if identity.ParentHash != frozen.Input.HistoryHash {
				return nil, nil
			}
		case "FROZEN_INPUT":
			if !ev.MatchesDecodedInputBarBinding(frozen, identity.ParentHash, binding) {
				return nil, nil
			}
		default:
			return nil, nil
		}
		if _, duplicate := origins[binding.ProjectionHash]; duplicate {
			return nil, nil
		}
		origins[binding.ProjectionHash] = identity
	}
	parents := func(row Row) (string, bool) {
		refs := row.Strings("source_refs")
		if len(refs) == 0 {
			return "", false
		}
		values := map[string]string{}
		for _, ref := range refs {
			receipt, found := resolver.Receipts[ref]
			if !found || resolver.Conflicts[ref] {
				return "", false
			}
			values[ref] = RowHash(receipt)
		}
		raw, _ := json.Marshal(values)
		return Hash(raw), true
	}
	stateParents, ok := parents(state)
	if !ok {
		return nil, nil
	}
	calendar, _ := json.Marshal(sessions)
	proof := &rollingProof{Algorithm: algorithm, Coverage: frozen.Input.Coverage, Instrument: instrument,
		Observation: frozen.Input.Observation, Cutoff: frozen.Cutoff, CalendarHash: Hash(calendar),
		SecurityHash: RowHash(state), SecurityParents: stateParents, SessionCount: len(sessions),
		FirstSession: sessions[start], Rows: map[string]string{}}
	for _, row := range selected {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		projection := RowHash(row)
		origin, found := origins[projection]
		receiptHash, parentOK := parents(row)
		basis := row.Text("comparable_basis")
		if !found || !parentOK || basis == "" || !row.Num("comparable_close").Positive() {
			return nil, nil
		}
		if proof.Basis == "" {
			proof.Basis = basis
		}
		if proof.Basis != basis {
			return nil, nil
		}
		day := row.Text("session")
		if _, duplicate := proof.Rows[day]; duplicate {
			return nil, nil
		}
		raw, _ := json.Marshal([]string{projection, origin.RawSHA256, receiptHash, basis})
		proof.Rows[day] = Hash(raw)
	}
	// Every session of the original full arithmetic scope must be present.
	// The extra preceding guard is compared only, never used by the facts window.
	for _, day := range sessions[start:] {
		if _, ok := proof.Rows[day]; !ok {
			return nil, nil
		}
	}
	return proof, nil
}
func rollingPrefixMatches(previous *rollingState, current *rollingProof, sessions []string) bool {
	if previous == nil || current == nil || len(sessions) < 2 {
		return false
	}
	p := previous.Proof
	end := len(sessions) - 1
	prefix, _ := json.Marshal(sessions[:end])
	if previous.Contract != rollingFactContract || p.Algorithm != current.Algorithm ||
		p.Coverage != current.Coverage || p.Instrument != current.Instrument ||
		p.Observation != sessions[end-1] || p.SessionCount != end ||
		current.SessionCount != end+1 || p.CalendarHash != Hash(prefix) ||
		p.SecurityHash != current.SecurityHash || p.SecurityParents != current.SecurityParents ||
		p.Basis == "" || p.Basis != current.Basis {
		return false
	}
	first := end - 1 - 314
	if first < 0 {
		first = 0
	}
	if p.FirstSession != sessions[first] || len(p.Rows) != end-first {
		return false
	}
	for _, day := range sessions[first:end] {
		old, found := p.Rows[day]
		if !found || !validHash(old) || current.Rows[day] != old {
			return false
		}
	}
	oldCutoff, oldErr := time.Parse(time.RFC3339Nano, p.Cutoff)
	newCutoff, newErr := time.Parse(time.RFC3339Nano, current.Cutoff)
	shanghai := time.FixedZone("Asia/Shanghai", 8*3600)
	closeAt := func(day string) time.Time {
		d, _ := time.Parse("2006-01-02", day)
		return time.Date(d.Year(), d.Month(), d.Day(), 15, 0, 0, 0, shanghai)
	}
	return oldErr == nil && newErr == nil && !oldCutoff.After(newCutoff) &&
		!oldCutoff.Before(closeAt(p.Observation)) && !newCutoff.Before(closeAt(current.Observation))
}
func rollingSumKey(field string, n, offset int) string {
	return field + ":" + strconv.Itoa(n) + ":" + strconv.Itoa(offset)
}

// Seed from the independent full result, then move its exact sums backwards
// to the five prior endpoints. No rounded slope is inverted to recover an MA.
func seedRollingState(ctx context.Context, proof *rollingProof, w Window, end int, sf StockFacts) (*rollingState, error) {
	if proof == nil {
		return nil, nil
	}
	state := &rollingState{Contract: rollingFactContract, Proof: *proof, Sums: map[string]Number{}, BelowMA5Days: sf.Facts.Trend.Days}
	state.Proof.Rows = map[string]string{}
	for _, day := range w.Sessions {
		if day >= proof.FirstSession {
			state.Proof.Rows[day] = proof.Rows[day]
		}
	}
	for _, n := range rollingMAs {
		ma := sf.Facts.Trend.MA[n]
		if !ma.Positive() {
			return nil, nil
		}
		sum := ma.Mul(count(n))
		state.Sums[rollingSumKey("close", n, 0)] = sum
		for offset := 1; offset <= 5; offset++ {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
			leaving, entering := w.At(end-offset+1), w.At(end-offset-n+1)
			if leaving == nil || entering == nil || !leaving.Close.Positive() || !entering.Close.Positive() ||
				leaving.Basis != proof.Basis || entering.Basis != proof.Basis {
				return nil, nil
			}
			sum = sum.Sub(leaving.Close).Add(entering.Close)
			state.Sums[rollingSumKey("close", n, offset)] = sum
		}
	}
	for _, field := range []string{"amount", "turnover"} {
		for _, n := range []int{3, 5} {
			for offset := 0; offset <= 3; offset++ {
				v := w.Average(end-offset, n, field)
				if !v.Known() {
					return nil, nil
				}
				state.Sums[rollingSumKey(field, n, offset)] = v.Mul(count(n))
			}
		}
	}
	for offset := 0; offset <= 1; offset++ {
		v := w.HighTurnover(end-offset, 5)
		if !v.Known() {
			return nil, nil
		}
		state.Sums[rollingSumKey("high_turnover", 5, offset)] = v
	}
	return state, nil
}

func advanceRollingState(ctx context.Context, previous *rollingState, proof *rollingProof, w Window, end int) (map[string]Number, Number, bool, error) {
	if !rollingPrefixMatches(previous, proof, w.Sessions) {
		return nil, Number{}, false, nil
	}
	sums := map[string]Number{}
	update := func(field string, n, offset int) bool {
		before := previous.Sums[rollingSumKey(field, n, offset)]
		leaving, entering := w.At(end-offset-n), w.At(end-offset)
		if !before.Known() || leaving == nil || entering == nil {
			return false
		}
		a, b := leaving.Close, entering.Close
		switch field {
		case "amount":
			a, b = leaving.Amount, entering.Amount
		case "turnover":
			a, b = leaving.Turnover, entering.Turnover
		case "high_turnover":
			if !leaving.Turnover.Known() || !entering.Turnover.Known() ||
				!before.IsInt() || before.Cmp(N("0")) < 0 || before.Cmp(count(n)) > 0 {
				return false
			}
			a, b = N("0"), N("0")
			if leaving.Turnover.Cmp(N(".07")) >= 0 {
				a = N("1")
			}
			if entering.Turnover.Cmp(N(".07")) >= 0 {
				b = N("1")
			}
		}
		if !a.Known() || !b.Known() {
			return false
		}
		sums[rollingSumKey(field, n, offset)] = before.Sub(a).Add(b)
		return true
	}
	for _, n := range rollingMAs {
		for offset := 0; offset <= 5; offset++ {
			if err := ctx.Err(); err != nil {
				return nil, Number{}, false, err
			}
			if !update("close", n, offset) {
				return nil, Number{}, false, nil
			}
		}
	}
	for _, field := range []string{"amount", "turnover"} {
		for _, n := range []int{3, 5} {
			for offset := 0; offset <= 3; offset++ {
				if !update(field, n, offset) {
					return nil, Number{}, false, nil
				}
			}
		}
	}
	for offset := 0; offset <= 1; offset++ {
		if !update("high_turnover", 5, offset) {
			return nil, Number{}, false, nil
		}
	}
	bar := w.At(end)
	ma5 := sums[rollingSumKey("close", 5, 0)].Div(count(5))
	if bar == nil || !bar.Close.Known() || !ma5.Positive() {
		return nil, Number{}, false, nil
	}
	days := count(0)
	if bar.Close.Cmp(ma5) < 0 {
		old := previous.BelowMA5Days
		if !old.Known() || !old.IsInt() {
			return nil, Number{}, false, nil
		}
		n, err := strconv.Atoi(old.String())
		// Full Trend scans through the stopping bar's MA5, including its four
		// preceding sessions. A shifted 315-session scope may remove that context.
		if err != nil || n < 0 || n >= end || w.At(end-(n+1)-4) == nil {
			return nil, Number{}, false, nil
		}
		days = count(n + 1)
	}
	return sums, days, true, nil
}
func rollingMA(ctx context.Context, sums map[string]Number, end, i, n int) (Number, error) {
	if err := ctx.Err(); err != nil {
		return Number{}, err
	}
	return sums[rollingSumKey("close", n, end-i)].Div(count(n)), nil
}
func rollingAverage(sums map[string]Number, end, i, n int, field string) Number {
	return sums[rollingSumKey(field, n, end-i)].Div(count(n))
}
func rollingHighTurnover(sums map[string]Number, end, i, n int) Number {
	return sums[rollingSumKey("high_turnover", n, end-i)]
}

func buildRollingFacts(ctx context.Context, id, name string, w Window, end int, sums map[string]Number, days Number) (StockFacts, error) {
	sf := StockFacts{ID: id, Name: name, Facts: Facts{Returns: map[int]Number{}, RS: map[int]Number{}}}
	f := &sf.Facts
	for _, n := range []int{1, 3, 5, 10, 20} {
		f.Returns[n] = w.Return(end, n)
	}
	f.PriorReturn5 = w.Return(end-1, 5)
	f.PriorReturn10 = w.Return(end-1, 10)
	f.PriorHigh = w.PriorHigh(end, 5)
	f.PriorTurnover = rollingHighTurnover(sums, end, end-1, 5)
	f.RecentTurnover = rollingHighTurnover(sums, end, end, 5)
	sf.Recent3 = w.Return(end, 3)
	sf.Previous3 = w.Return(end-3, 3)
	bar := w.At(end)
	if bar != nil {
		f.Close = bar.Close
		sf.Amount = bar.Amount
		f.AmountRatio = bar.Amount.Div(rollingAverage(sums, end, end-1, 5, "amount")).Rounded(6)
	}
	f.Path = DailyPath(bar)
	f.Strength = StrengthMomentum{Calculation: "EQUAL_WINDOW_RECENT_MINUS_PREVIOUS_3_SESSION_RETURN_AND_RS_V1", RecentAmount: rollingAverage(sums, end, end, 3, "amount"), PreviousAmount: rollingAverage(sums, end, end-3, 3, "amount"), RecentTurnover: rollingAverage(sums, end, end, 3, "turnover"), PreviousTurnover: rollingAverage(sums, end, end-3, 3, "turnover")}
	f.Strength.AmountRaw = f.Strength.RecentAmount.Div(f.Strength.PreviousAmount).Sub(N("1"))
	f.Strength.TurnoverRaw = f.Strength.RecentTurnover.Sub(f.Strength.PreviousTurnover)
	values := []Number{}
	for _, v := range []Number{f.Strength.AmountRaw, f.Strength.TurnoverRaw} {
		if v.Known() {
			values = append(values, v)
		}
	}
	if len(values) > 0 {
		sum := N("0")
		for _, v := range values {
			sum = sum.Add(v)
		}
		f.Strength.ActivityRaw = sum.Div(count(len(values)))
	}
	f.Strength.ActivityState = deltaState(f.Strength.ActivityRaw, "RISING", "STABLE", "FALLING")
	sf.AmountChanges = map[int]Number{}
	for _, n := range []int{1, 3, 5} {
		old := w.At(end - n)
		if bar != nil && old != nil {
			sf.AmountChanges[n] = bar.Amount.Div(old.Amount).Sub(N("1"))
		}
	}
	if bar != nil {
		sf.Turnover = bar.Turnover
	}
	sf.CurrentLimit = WindowLimit(w, end)
	sf.PreviousLimit = WindowLimit(w, end-1)
	sf.Supply = SupplyPressure(w, end)
	up, _, _ := Limits(w.At(end - 1))
	f.PriorLimitUp = up == "TRUE"
	var e error
	sf.CurrentMA20, e = rollingMA(ctx, sums, end, end, 20)
	if e != nil {
		return sf, e
	}
	sf.PriorMA20, e = rollingMA(ctx, sums, end, end-5, 20)
	if e != nil {
		return sf, e
	}
	f.Trend, e = rollingTrend(ctx, w, end, sums, days)
	return sf, e
}

func rollingTrend(ctx context.Context, w Window, i int, sums map[string]Number, days Number) (Trend, error) {
	t := Trend{State: "UNKNOWN", Condition: C(Unknown, "SAM_LONG_TREND_UNAVAILABLE"), MA: map[int]Number{}, Slopes: map[int]Number{}}
	for _, n := range []int{5, 10, 20, 60, 120, 250} {
		v, e := rollingMA(ctx, sums, i, i, n)
		if e != nil {
			return t, e
		}
		t.MA[n] = v
	}
	t.Days = days
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
		past, e := rollingMA(ctx, sums, i, i-5, n)
		if e != nil {
			return t, e
		}
		t.Slopes[n] = t.MA[n].Sub(past).Div(past).Rounded(6)
		unknown = unknown || !t.Slopes[n].Known() || !t.MA[n].Known()
	}
	if unknown {
		return w.Trend(ctx, i)
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
