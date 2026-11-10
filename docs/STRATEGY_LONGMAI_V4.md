# Strategy spec: 龙脉 V4 (`SF_LONGMAI_V4`)

Status: frozen specification, committed before the first backtest of this combination. V4 replaces
V1 as the version under forward test (the user's decision, 2026-11-10); V1 is retired and stays
only for comparison. Any change below creates V5 with a new forward-test clock.

## Changes from V1 (everything else is identical to `docs/STRATEGY_LONGMAI_V1.md`)

1. **No 3-session abnormal-volatility veto.** The severe-abnormal vetoes (severe today, the 10
   sessions after one, 10-session deviation within 30 points of +100%) and the Lhasa retail veto stay.
2. **Leader identification by turnover rate, not amount.** Inside each mainline, today's strong
   eligible members are ranked by board height, then turnover rate (higher first); leader rank ≤ 2.
   The whole-market amount-rank gate (≤ 30) is removed.
3. **Fixed amount threshold instead of an amount ranking.** A candidate counts only if today's
   amount ≥ 5亿 or the mean amount of the last 3 sessions (including today) ≥ 5亿.
   Choice of 5亿 (made from amount distributions only, not from returns): the yearly median amount
   of limit-up stocks is 2–4.5亿 (2022–2026); at 5亿 about 37% of limit-ups qualify (median 13–37
   per day by year). It removes illiquid small caps where a 1/4 position would move the price,
   without ranking big caps above small leaders. Reported robustness: 3亿 and 8亿 (not re-selected).
4. **Crowding (the user's choice C, as in V3)**: turnover only identifies the leader; a leader whose
   turnover is ≥ 30% or ≥ 2× its previous 5-session average is shown as crowded and not bought.
   Reported robustness: without this block (not re-selected).
5. Ranking when slots are scarce: mainline strength (3-session limit-ups), leader rank, then
   whole-market turnover-rate rank.

Market-level crowding (`sf crowding`) is context for the person trading and is not part of V4.

## Evaluation

As V1: portfolio backtest with slot limits, train / test1 / test2 = 60/20/20, 0.15% cost (also 0.30%),
robustness reported but never re-selected. The test segments were inspected during earlier research;
the forward test is the real exam: 40 trading days from 2026-11-11, pass if average return per closed
trade after 0.15% cost > 0 and realized max drawdown < 15% of capital.
