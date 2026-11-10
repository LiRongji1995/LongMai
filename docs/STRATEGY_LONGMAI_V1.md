# Strategy spec: 龙脉 V1 (`SF_LONGMAI_V1`)

Status: frozen specification, committed before the first full backtest of this exact combination.
Any change to a rule or parameter below creates V2 with a new forward-test clock.

## Thesis

Trade only the strongest stocks of the market's mainline themes; exit when trading becomes crowded.
Turnover identifies the leader: a stock without top-tier turnover is neither mainline nor leader.

## Layers

1. **Market gate** (before the open of the target session)
   - Sentiment tier from `SF_MOOD_V1` (train-selected composite, terciles of the trailing 250-session
     percentile): 高 → budget 1.0, 中 → 0.5, 低 → 0 (no new positions).
   - Regulatory temperature (SZSE weekly abnormal-trading cases per unit of turnover, percentile of the
     last 52 reports, known before the open): top tercile halves the budget.
   - Slots: budget 1.0 → 4 positions, 0.5 → 2, 0.25 → 1, 0 → none. Each position is 1/4 of capital.
2. **Mainlines**: concept/industry boards (noise boards removed, a board is used only after its index
   history starts) ranked by 3-session limit-up count, then today's limit-up count, then turnover share;
   top 3 with at least 3 limit-ups today.
3. **Leaders**: inside each mainline, today's strong eligible members (limit-up, or +5% with CLV ≥ 0.7;
   main board, non-ST, listed ≥ 60 sessions) ranked by board height then turnover; keep leader rank ≤ 2
   with whole-market turnover rank ≤ 30. The R2 stage (STRONG_START / STRONG_CONTINUATION /
   FIRST_PULLBACK) is attached as a descriptive tag only.
4. **Risk vetoes** (a vetoed leader goes to the 回避 group):
   - triggered the 3-session abnormal-volatility rule today;
   - severe abnormal volatility today, or within 10 sessions after one;
   - 10-session cumulative deviation within 30 percentage points of +100%;
   - Lhasa retail seats net-bought more than 2% of the LHB turnover today.
5. **Trading rules**
   - Entry at the target-session open only if the open is above the previous close (gap > 0) and not at
     limit-up; otherwise skip.
   - Ranking when slots are scarce: mainline strength (3-session limit-ups of its board), then leader
     rank, then turnover rank.
   - Exits (decided on a close, executed at the next open): failed limit-up (touched, closed below);
     churn (turnover ≥ 2× the prior 5-session average with a daily gain ≤ 2%); theme fade (the board's
     limit-ups fall below 34% of the signal day); stop (adjusted close ≤ entry −7%); time (10 sessions).
     There is no moving-average exit: the thesis exits on crowding, not on trend.
   - Cost 0.15% per round trip (reported again at 0.30%).

## Evaluation

- Full-history portfolio backtest with the slot limits above, realized equity curve, max drawdown,
  per-trade and per-day statistics for train / test1 / test2 (60/20/20 by observation day). The test
  segments were inspected during the research that produced these rules, so they are not clean.
- Robustness: each numeric parameter moved one step either way, reported, never re-selected.
- Forward test (the real exam): 40 trading days from freezing. Pass if average return per closed trade
  after 0.15% cost is above zero and the realized max drawdown stays below 15% of capital.

## Known biases

- Theme membership is today's snapshot (survivorship / look-ahead in the backtest).
- Limit-up pool facts (seal time, breaks) are not used: no history.
- Cost may be optimistic near limit prices.
