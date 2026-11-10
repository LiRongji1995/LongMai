# Screening spec: 龙脉 V7 (`SF_LONGMAI_V7`)

Status: current version (user decision, 2026-11-10). The forward record starts with the 2026-11-11 session.

## Role and evaluation

The system never trades. It screens stocks and reads the short-term market temperature; a person
decides every entry and exit intraday (auction volume and price, 分时 pullbacks, order book). V7 is
therefore **not** judged by next-open P&L. It is judged by entry-free hit rates (`sf/evaluate.py`):

- Screening: share of listed stocks that continue (next-day limit-up), break out within 5 sessions
  (≥ 2 limit-ups or +20% high), or fall hard next day (大面), each compared with all main-board
  limit-ups that day. Recall: the share of realized 妖股 (≥ 3 limit-ups in the next 5 sessions) that
  were listed the day before.
- Temperature: next-day loss-effect rate per temperature cell, compared with all days.

Model adjustments are made from these hit rates (train segment first, then checked on the tests and
the forward record).

## Changes from V6

1. **No amount threshold.** Amount and turnover are displayed, with a liquidity note below 1亿 and
   a one-price-board note. Evidence: among 2板+ stocks the 5亿 threshold removed the stocks most
   likely to become 妖股 (winner rate below 20 / 20 / 17% vs above 12 / 11 / 7.5%).
2. **连板梯队.** Every main-board, eligible 2板+ stock that is not already a mainline leader is listed,
   sorted by height then turnover, with its theme attribution, crowding / high-volatility flags and
   distance to severe abnormal volatility.
3. **高波动 instead of 回避.** Stocks hitting a severe-abnormal veto are the strongest and the most
   dangerous (next-day limit-up 24–45%, 大面 38–58%); they are shown as 高波动 for intraday judgement.
4. **Two-axis temperature** (`sf/temperature.py`): heat × pain cells with their historical next-day
   loss-effect rate and the pressure sources; replaces the five SF_MOOD_V1 phase nodes, which failed
   against real outcomes (only 退潮 was modestly right).

Mainlines, leaders, relative crowding and the 空间板 class are as in V6; the mood tier still sets
the displayed position cap.

## First hit-rate check (V7 rules replayed, train / test1 / test2)

| group | next-day limit-up | 5-session breakout | next-day 大面 |
|---|---|---|---|
| all main-board limit-ups (baseline) | 24.9 / 24.3 / 20.0% | 25.1 / 24.1 / 20.6% | 15.7 / 14.8 / 16.4% |
| mainline leaders (candidates) | 36.9 / 36.1 / 32.1% | 35.7 / 36.8 / 34.8% | 26.8 / 25.6 / 24.9% |
| 连板梯队 | 40.6 / 40.6 / 33.4% | 38.7 / 40.2 / 32.0% | 31.0 / 28.0 / 24.5% |
| crowded | 11.3 / 14.3 / 5.3% | 16.1 / 21.4 / 5.3% | 40.3 / 21.4 / 36.8% |
| 高波动 | 55.5 / 41.8 / 46.2% | 57.5 / 34.3 / 45.6% | 36.8 / 53.7 / 28.8% |

Recall of realized 妖股: 52.7 / 47.3 / 46.9% (V6: 7.9 / 9.0 / 9.3%), with about 14 listed stocks per day.
