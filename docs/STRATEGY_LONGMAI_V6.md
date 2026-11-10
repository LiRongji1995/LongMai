# Strategy spec: 龙脉 V6 (`SF_LONGMAI_V6`)

Status: frozen specification, committed before the first backtest of this combination. V6 replaces
V5 as the version under forward test (the user's decision, 2026-11-10); the 40-session clock starts
with the 2026-11-11 session. Any change creates V7.

The system never trades; the list shows candidates for a person to decide on, and the backtest is a
rule proxy (see V5).

## Changes from V5 (everything else is identical to `docs/STRATEGY_LONGMAI_V5.md`)

1. **Mainline eligibility over 3 sessions.** A board is eligible when it has ≥ 5 limit-ups over the
   last 3 sessions, ≥ 1 limit-up today and ≥ 5 traded members (V5: ≥ 3 limit-ups today). Ranking is
   unchanged (3-session concentration z3, descending). The overlap dedupe now uses the boards'
   3-session limit-up sets (skip a board when ≥ 60% of them are already covered). The mainlines are
   therefore the top 3 rows of the theme radar.
2. **空间板 as its own candidate class.** Besides the mainline leaders, the market's tallest
   main-board stock is a candidate whatever its theme, if its height is ≥ 4 boards and it passes the
   pool, the 5亿 amount threshold, the vetoes and the crowding check like any leader. Ties at the top
   height: the two with the highest turnover rate. It ranks first when slots are scarce (sentiment
   anchor). It has no theme-fade exit (it is not tied to one board); the other exits apply.

## Evaluation

As V5. Robustness, reported and never re-selected: 3-session limit-ups 4 / 6, 空间板 minimum height
3 / 5, plus the V5 grid.
