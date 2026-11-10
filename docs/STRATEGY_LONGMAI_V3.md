# Strategy spec: 龙脉 V3 (`SF_LONGMAI_V3`)

Status: frozen specification, committed before the first backtest of V3. Identical to V2
(docs/STRATEGY_LONGMAI_V2.md) except for the crowding rule below. V1 and V2 stay reproducible
(`sf longmai --version 1|2`).

## Why

V2 used turnover both to identify leaders and, implicitly, as an entry condition: its leaders were all in
the top 4% of whole-market turnover and lost 1.61% per trade. Owner decision (2026-11-10): turnover
identifies the leader; when turnover is too high the trade is crowded and is not taken.

## Rule

- Identification as in V2: inside a mainline, strong members ranked by board height then activity
  (mean of whole-market percentile ranks of amount and turnover rate); leader rank ≤ 2 and activity rank
  ≤ 30.
- **Crowded → no entry** if, on the signal day, either
  - turnover rate ≥ 30% (absolute level; a whole-market percentile does not separate leaders, all of
    which are in the top 4%), or
  - turnover rate ≥ 2 × the stock's average turnover over the previous 5 sessions (same multiplier as
    the churn exit).
- A crowded leader keeps its leader rank: the slot is not passed to the next-ranked member.
- The 3-session abnormal-volatility rule is not a veto (as in V2). Severe-volatility vetoes and the
  Lhasa retail veto remain.

## Evaluation

As V1/V2. Robustness additionally moves the crowding thresholds one step (25% / 35%; 1.5× / 2.5×).
Forward test: 40 trading days from 2026-11-11 with the V1 pass criteria.
