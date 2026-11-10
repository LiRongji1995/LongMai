# Strategy spec: 龙脉 V5 (`SF_LONGMAI_V5`)

Status: frozen specification, committed before the first backtest of this combination. V5 replaces
V4 as the version under forward test (the user's decision, 2026-11-10). Any change creates V6.

## Role of the system

The system never trades. Every buy and sell is decided and executed by a person. The list shows
**candidates** with the conditions under which this rule set would consider them. The backtest is a
rule proxy: it measures what the rules would have produced if a person had followed them mechanically
at the next open. It is not a simulation of an automated trader.

## Changes from V4 (everything else is identical to `docs/STRATEGY_LONGMAI_V4.md` / V1)

1. **Finer mainlines (concentration, not size).** For every board and session: traded members n and
   limit-ups k (all SSE/SZSE A shares). Over the last 3 sessions K3 = Σk, N3 = Σn, and the market
   limit-up rate r3 = all limit-ups / all traded stock-sessions. Concentration score
   z3 = (K3 − N3·r3) / sqrt(N3·r3·(1 − r3)). Eligible boards: ≥ 3 limit-ups today and ≥ 5 traded
   members. Boards are taken in descending z3; a board is skipped when ≥ 60% of its limit-ups today
   are already in the boards taken (the same wave seen through a wider board). Top 3 are the
   mainlines. Large umbrella boards (机器人概念, 新能源车) win only if their limit-up rate is truly
   abnormal, not because they are large.
2. **Threshold before ranking.** A strong eligible member enters the ranking only if today's amount
   ≥ 5亿 or the mean of the last 3 sessions ≥ 5亿; the remaining members are ranked by board height,
   then turnover rate; leader rank ≤ 2. (V4 ranked first and then dropped members below the
   threshold, so a small top-height stock left its theme without a leader.)
3. **Relative crowding.** A leader is crowded when its turnover is ≥ 2× its previous 5-session mean
   **and** it did not close at limit-up (放量分歧). A sealed limit-up with high turnover is a full
   hand-over (换手充分), not crowding. The absolute 30% line is removed.
4. **Main board only** (user constraint: the account can trade main-board stocks only); 20cm stocks
   still count in theme statistics.

## Evaluation

As V4. Robustness, reported and never re-selected: overlap 50% / 70%, crowding surge 1.5× / 2.5×,
amount threshold 3亿 / 8亿, plus the common V1 grid. Forward test: 40 trading days from 2026-11-11.
The forward record also logs what the person actually did, to compare rule candidates with real
decisions.
