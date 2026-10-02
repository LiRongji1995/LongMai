package facts

import "context"

func BuildFacts(ctx context.Context, id, name string, w Window, end int) (StockFacts, error) {
	sf := StockFacts{ID: id, Name: name, Facts: Facts{Returns: map[int]Number{}, RS: map[int]Number{}}}
	f := &sf.Facts
	for _, n := range []int{1, 3, 5, 10, 20} {
		f.Returns[n] = w.Return(end, n)
	}
	f.PriorReturn5 = w.Return(end-1, 5)
	f.PriorReturn10 = w.Return(end-1, 10)
	f.PriorHigh = w.PriorHigh(end, 5)
	f.PriorTurnover = w.HighTurnover(end-1, 5)
	f.RecentTurnover = w.HighTurnover(end, 5)
	sf.Recent3 = w.Return(end, 3)
	sf.Previous3 = w.Return(end-3, 3)
	bar := w.At(end)
	if bar != nil {
		f.Close = bar.Close
		sf.Amount = bar.Amount
		f.AmountRatio = bar.Amount.Div(w.Average(end-1, 5, "amount")).Rounded(6)
	}
	f.Path = DailyPath(bar)
	f.Strength = StrengthMomentum{Calculation: "EQUAL_WINDOW_RECENT_MINUS_PREVIOUS_3_SESSION_RETURN_AND_RS_V1", RecentAmount: w.Average(end, 3, "amount"), PreviousAmount: w.Average(end-3, 3, "amount"), RecentTurnover: w.Average(end, 3, "turnover"), PreviousTurnover: w.Average(end-3, 3, "turnover")}
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
	f.Strength.ActivityState = DeltaState(f.Strength.ActivityRaw, "RISING", "STABLE", "FALLING")
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
	sf.CurrentMA20, e = w.MA(ctx, end, 20)
	if e != nil {
		return sf, e
	}
	sf.PriorMA20, e = w.MA(ctx, end-5, 20)
	if e != nil {
		return sf, e
	}
	f.Trend, e = w.Trend(ctx, end)
	return sf, e
}
