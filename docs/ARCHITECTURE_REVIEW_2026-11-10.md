# Architecture Review — 2026-11-10

Scope: the whole repository after the move to the lean `sf` CLI and 龙脉 V7. Goals: as fast as
possible, as small as possible, a score, and the feasibility of turning this screening tool into a
quantitative trading system. The slimming plan below was carried out on the same day; the numbers
show the state before and after.

## 1. Facts

| Measure | Before (tag `legacy-r2`) | After the first cut | After slimming |
|---|---|---|---|
| Tracked files | 416 | 55 | 43 |
| Tracked lines | ~217k | ~6.8k | ~5.0k |
| `sf` package lines | — | 4,431 | 3,174 |
| `sf` modules (files) | — | 25 | 20 |
| CLI commands | — | 16 | 11 |
| Test lines / tests | — | ~560 / 38 | 494 / 33 |
| Data directory | 1.0 GB | 1.0 GB | 377 MB |
| Python environment | 404 MB | 404 MB | 404 MB |
| `sf premarket` list build (warm, no network) | — | 14.7 s | 2.6 s |
| `sf evaluate` (full history, screening + temperature) | — | — | 50 s |

The list for 2026-11-10 was rebuilt before and after every step and compared as text (header and all
32 rows): identical.

## 2. Issues found, ranked

1. **Dead strategy versions in the live path.** V1–V6 parameters and branches lived inside the
   functions that build today's list (`vetoes`, `crowded`, `decide`, `mainlines`, `signals`), plus a
   portfolio replay, robustness grids and next-open P&L code that the evaluation no longer uses. Every
   reader had to work out which branch V7 takes.
2. **Duplicate panel work.** The same long bar table was pivoted column by column twice (all A shares
   and the main board), and the mood indicators were computed twice (mood and temperature). This was
   about 9 of the 14.7 seconds.
3. **One module doing two jobs.** `longmai.py` mixed screening rules with list assembly, text and file
   output (624 lines).
4. **Legacy commands that measure the wrong thing.** `sf backtest`, `sf research`, `sf mainline`,
   `sf longmai` and `sf review` all scored next-open entries, which the person trading rarely takes.
5. **An unattended scheduler** (`sf auto`, `bin/sf-schedule`) that the user does not want.
6. **A 630 MB research dataset** for the retired R2 rules.
7. **Pending outcomes counted as misses.** In `evaluate.stock_outcomes`, `max3` / `out5` at the end
   of the history used whatever later sessions existed, so a list from the last days scored as "did not
   break out". Found while rewriting the tests; fixed (NaN until all later sessions are stored).

## 3. What was done

- Removed `premarket.py` (R2 list), `risk.py`, `ranking.py`, `backtest.py`, `research.py`,
  `outcomes.py`, `sf auto` and `bin/sf-schedule`, the commands `backtest`, `research`, `mainline`,
  `longmai` and `premarket --r2`, and the V1–V6 specs. All are at tag `legacy-r2`.
- `longmai.py` is now V7 screening only (163 lines): one flat `PARAMS`, `context`, `gate`,
  `mainline_table`, `vetoes`, `crowded`, `notes`, `decide`, `seat_day`.
- New `listing.py`: list assembly, header text, saving, and the saved-run lookups (`latest_run`,
  `all_runs`) used by `show`, `auction` and `review`.
- `mainline.py` is V7 only: panels, theme statistics, concentration, mainlines, leaders.
- `store.wide()` builds every session × code frame with one numpy scatter instead of repeated pivots;
  `temperature.table()` reuses the mood indicators.
- `sf review` reports entry-free hit rates by list group against all main-board limit-ups, plus the
  recall of realized 妖股. New `sf evaluate` runs the full-history screening and temperature report.
- Deleted the R2 research dataset (630 MB).

Remaining time in the 2.6 s build: loading 320 parquet files (0.8 s), building the panels (1.1 s),
the name scan (0.6 s) and the severe-volatility windows (0.6 s). Getting under 1 s needs an
incremental panel cache (append one session per day instead of rebuilding 320); not worth it yet.

## 4. Scores (0–10)

| Area | Score | Note |
|---|---|---|
| Core leanness | 7 | V7 path is short and single-version; `listing.build` is still one long function |
| Repository leanness | 2 → 7 | 416 files to 43; legacy only behind a tag |
| Speed | 6 → 8 | 14.7 s to 2.6 s; evaluation 50 s for four and a half years |
| Module boundaries | 5 → 7 | screening, list assembly, evaluation and data are separate modules |
| Data layer | 5 | daily snapshots are simple and point-in-time, but rebuilt from scratch on every run |
| Research discipline | 8 | segments, frozen specs, entry-free outcomes, golden snapshot for refactors |
| Tests | 5 → 6 | offline and synthetic; list assembly is covered only by the golden snapshot |
| Operability | 6 | one wrapper, no scheduler, explicit missing-data notes |
| **Overall** | **6.0 → 7.0** | |

## 5. From screening tool to quantitative trading system

**Feasibility of an unattended fully automatic system: 2 / 10.**

- **No edge in daily bars.** Every next-open replay of V1–V6 lost money over the full history. The
  screening has real skill at finding stocks that keep rising (candidates break out 1.4–1.7× as
  often as the average limit-up), but the same stocks have 1.5–1.7× its next-day 大面 rate. The
  money is made or lost in the entry, which the daily data cannot see.
- **The deciding data is missing.** The person enters on the call-auction volume and price path, the
  first minutes of trading, pullbacks to the 分时 line and the order book. None of this is stored; L1
  snapshots are only partly free, and L2 order-by-order data is paid.
- **Execution and compliance.** Retail automation goes through broker terminals such as QMT or PTrade.
  Under the CSRC programmatic-trading rules (in force since 2024-10), programmatic accounts must be
  reported to the broker, and high order rates face extra monitoring; the exchanges' abnormal-trading
  rules (resting limit-up orders, rapid price pushes) are aimed exactly at short-term limit-up play.
- **Main board only**, T+1, and limit-up queues that a retail order rarely reaches.

**A semi-automatic system is realistic: about 5–6 / 10 within a year,** in three steps:

1. **Collect what decides entries.** Save 09:15–09:25 auction snapshots and the first 30 minutes for
   every listed stock, and record the person's actual trades (time, price, reason). This gives the
   first honest dataset of the entries that are really taken.
2. **Auction and intraday alerts.** Rules learned from that data ("auction volume above X% of
   yesterday with the price holding", "first pullback to the 分时 line holds"), shown as alerts, judged
   by hit rates like the list.
3. **One-click confirmed orders** through a broker terminal API: the system prepares the order, the
   person confirms it. Unattended full automation stays at ≤ 3 / 10 unless steps 1–2 show a stable
   edge out of sample.

**Decision 2026-11-10 (after this review):** auction-level judgment stays with the person. `sf auction`
was removed, and step 2's auction alerts are not planned. Checked on 浙文影业 and 冰山冷热, the
inherited SF_AUCTION_V1 rules would have marked every 一字 day "观察" and the 冰山冷热 10-16 low open
(-4.2%, closed limit-up) "放弃".

Nothing here is investment advice. Engineering checks passing does not show investment value.
