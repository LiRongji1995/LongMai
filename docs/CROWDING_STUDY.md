# Crowding study (拥挤度) — pre-registration

Written before the first run. Indicators: `sf/crowding.py`.

## Question

Is there a level of short-term money (its share of whole-market turnover, its growth, its
concentration in the mainlines) beyond which no more incremental money arrives, so relay trades and
mainline leaders stop paying?

## Indicators (all known after the observation session's close)

Market: `spec_share` (amount of stocks with a limit-up in the last 5 sessions / market amount),
`hot_turn_share` (amount of stocks with turnover ≥ 15% / market amount), their 5-session changes,
`amount_ratio20` (market amount / previous 20-session mean), `top3_share` (amount of the top-3
mainline boards' members / market amount), plus `spec_pct250`: the 250-session rolling percentile
of `spec_share` (a regime-free version).
Theme (the leader's mainline board): `share`, `share_pct60`, `share_chg5`.
Stock: `leader_theme_share` = the leader's amount / its board's amount.

## Outcomes

1. Relay basket: next-open to next-next-open return of today's main-board limit-ups (per day).
2. Relay 5-day: mean relay basket over the next 5 observation days (does the money keep paying?).
3. Leader trades: every V1 leader signal (mainline rank ≤ 3, leader rank ≤ 2, amount rank ≤ 30),
   without the mood/regulation gate, vetoes or slot limits, simulated with the V1 entry/exit rules,
   0.15% cost. Per-day average across that day's signals.

## Method

Segments: observation days 60/20/20 (train / test1 / test2). For each indicator, quintile edges are
computed on train only and applied unchanged to the tests. An indicator is called a crowding signal
only if, in all three segments, its top quintile is worse than the middle three quintiles for the
outcome, and the train-selected direction holds in both tests. A threshold is reported as the train
quintile edge; nothing is tuned on test data.

Caveat: the test segments were inspected in earlier research (mood, V1–V3), so they are not clean.
Any rule produced here becomes a new strategy version with its own forward-test clock.

## Results (first run, 2026-11-10)

Data: 1128 sessions (2022-03 to 2026-11), segments cut at 2025-02-06 and 2025-12-17. 435 V1 leader
signals, 237 closed trades (145 / 49 / 43); 3190 mainline board-days (1915 / 639 / 636).
Scripts and panels: `stockfish-data/research/crowdscripts/`, `research/crowding_*.parquet`.

Market level — top quintile minus middle three, per segment (train / test1 / test2):

| indicator | next-day relay | leader trades |
|---|---|---|
| spec_share (raw) | −0.00 / −0.24 / +0.11 % | +0.3 / −1.7 / +9.4 % |
| spec_pct250 | −0.23 / −0.12 / −0.15 % (t ≈ −0.5; passes the sign rule, not significant) | −0.3 / −8.2 (n=2) / +7.4 % |
| hot_turn_share | −0.02 / −0.39 / +0.00 % | mixed |
| spec_share_chg5, hot_turn_chg5 | mixed signs | mixed |
| amount_ratio20 | +0.59 / +0.08 / +0.05 % (more money is better, not worse) | mixed |
| top3_share | +0.25 / +0.21 / +0.17 % (concentration is better) | passes sign rule; train diff −0.04 % |

Verdict: **no market-level crowding threshold**. The short-term share level shifts with the regime
(raw level is not comparable across years); its percentile passes the sign rule only by tiny,
insignificant margins; leader-trade cells hold 2–34 trades and swing ±10 %. Higher total turnover
and higher concentration in the top-3 mainlines go with *better* next-day relay, the opposite of
the crowding hypothesis. 5/10-day relay windows overlap, so their t-values are overstated and are
not used.

Theme level — mainline board-days, outcome "fade" = limit-ups two sessions later below 34 % of today:

| share_pct60 quintile (train edges .53 / .80 / .93 / .98) | Q1 | Q2 | Q3 | Q4 | Q5 |
|---|---|---|---|---|---|
| train fade % | 16.5 | 20.8 | 18.4 | 18.7 | **33.1** |
| test1 fade % | 13.0 | 15.2 | 14.5 | 17.5 | **22.6** |
| test2 fade % | 20.0 | 18.3 | 23.0 | 21.7 | **33.9** |

Within limit-up-count buckets (3–4, 5–7, 8–12, 13+) the top quintile fades more in 11 of 12
segment × bucket cells, so it is not just "many limit-ups today revert". `share_chg5` shows the same
direction more weakly. Raw `share` shows the opposite (big boards fade less). Returns of the board's
limit-ups over the next 3 sessions do not differ consistently (−1.23 vs −1.22 % train, −0.70 vs
−0.87 % test1, −2.23 vs −0.40 % test2), and V1 leader trades split by it are inconsistent.

Use: the pre-market summary flags a mainline whose share is at its 60-session high
(`crowding.HOT_PCT` = 0.983) and prints the market short-term share as background. Neither changes
any trading rule or the strategy version. A rule built on the theme fade (e.g. exit or skip when a
board is at its share high) would be a new pre-registered version with its own forward test.
