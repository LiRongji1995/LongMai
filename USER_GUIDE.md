# User Guide — `sf` CLI

`sf` is the daily tool. Run it from anywhere with the wrapper:

```sh
stockfish-qlib-quant-system/bin/sf <command>
```

The wrapper uses the Python environment `../stockfish-env` next to the checkout (override with
`SF_PYTHON`). Data lives in `../stockfish-data` (override with `SF_HOME`). Tip: add an alias such as
`alias sf=/path/to/stockfish-qlib-quant-system/bin/sf`.

## Daily routine

| When (Shanghai) | Command | What you get |
|---|---|---|
| After 18:00 | `sf premarket` | Updates data, captures the day's limit-up/failed/limit-down pools and LHB, writes the list for the next session: `lists/<target>/<run>/盘前清单.csv` (name, turnover, advice) |
| 09:25–09:30 | `sf auction` | Re-ranks the latest list with call-auction data: 参与 / 观察 / 放弃 with the reason |
| Any time | `sf show [TARGET]` | Prints a saved list |
| Weekly | `sf review` | Forward review of every saved list (see below) |
| Weekly | `sf themes` | Refreshes concept/industry board membership (~20 min) |
| Any time | `sf mood` | Market sentiment indicators, percentile level and tier (低/中/高) with advice |
| Any time | `sf crowding [--days 20]` | Short-term money temperature: market turnover vs its 20-day mean, short-term share (stocks with a limit-up in 5 sessions), high-turnover share, mainlines with their turnover share (`!` = at a 60-session high). For review and context only |
| Any time | `sf seats --code 600000 [--date D]` | One stock's LHB seats for a session; `sf seats` prints the seat leaderboard |

**Unattended mode.** `bin/sf-schedule install` adds two macOS launchd jobs: the 09:26 auction re-rank and
the evening list plus review after 18:30 (Shanghai time). `sf auto` checks the trading calendar and the
time window itself, so the jobs can fire daily; logs go to `stockfish-data/logs/auto-<date>.log`.
`bin/sf-schedule status` / `uninstall`. The Mac must be awake at those times.

**Current version: 龙脉 V7** (`docs/STRATEGY_LONGMAI_V7.md`). The system never trades: the list is a
screening and temperature read for a person who decides entries intraday. It is judged by hit rates
(did listed stocks break out, were the realized 妖股 listed, did the temperature call come true), not
by next-open P&L. Groups: 主线龙头 (mainline leaders and the 空间板), 连板梯队 (all main-board 2板+),
观察, 高波动 (strongest and most dangerous: near severe abnormal volatility). Header lines: 水温
(heat × pain cell with the historical next-day loss-effect rate and pressure sources), 市场高标,
题材雷达, 同概念联动 / 玄学线索. Amount is displayed, not filtered.

`sf premarket --detail` shows tiers, scores, stages and seal facts. `sf status` shows data coverage.

The advice column ends with a risk context: the sentiment tier, abnormal-volatility status
("today triggered the 3-day rule", "a rise above X% tomorrow triggers it", "10-day deviation near the
severe level") and the LHB seat structure (Lhasa retail net buying, skilled hot-money buyers,
institution net buying, net selling). These are risk filters backed by out-of-sample evidence that
they rank days/stocks consistently; none of them makes buying profitable on its own.

## Sentiment, seats and abnormal volatility

- **Sentiment (`SF_MOOD_V1`)**: 12 daily indicators from bars (limit-ups/downs, failed-board rate,
  relay premium, promotion rate, "big loss" count, breadth, turnover ratio, ...), each turned into its
  percentile over the last 250 sessions. The composite uses the 5 indicators selected on the training
  segment only: limit-downs (−), promotion rate (−), big losses (−), failed-board premium (+), breadth (+).
  A high promotion rate predicts a worse next day: crowding. Tiers are terciles of the composite.
- **LHB seats**: full seat detail since 2022 (`pools/seats/`), two requests per session. Seat types:
  institution, northbound, Lhasa retail hubs, other branches. A seat's skill is the average
  next-open → following-open return of stocks it net-bought, using only outcomes already known on the
  day (two-session lag).
- **Abnormal volatility (`SF_ABNORMAL_V1`)**: 3-session cumulative deviation against the exchange
  benchmark, restarting after each trigger. Checked against the actual LHB listing reasons
  (2022-03 → 2023-11): precision 81%, recall 74%. Severe abnormal volatility (main board: 4 triggers
  in 10 sessions, or 10-day +100%, or 30-day +200%) is flagged with the distance to the trigger and the
  10-session window after it, when the exchanges may halve the monitoring thresholds.
- **Regulatory temperature**: SZSE weekly abnormal-trading cases per unit of market turnover, as a
  percentile of the last 52 reports; updated automatically by `sf premarket`. Rules, thresholds and
  enforcement history: [docs/REGULATION_NOTES.md](docs/REGULATION_NOTES.md). Order-level rules (resting
  limit-up orders, 3-minute price pushes, the 1-minute 10M yuan rule after severe volatility) need
  order-book data and are not modelled.

## Review: did the lists work?

```sh
sf review                 # all saved lists, entry at target-session open, sell next open, cost 0.15%
sf review --ret r_n1c     # sell at T+1 close instead
sf review --detail        # per-stock outcomes
```

Outcome labels (all measured from the target-session open, the realistic entry for a pre-market list):

| Label | Meaning |
|---|---|
| `r_n1o` | sell at next session's open (default) |
| `r_n1c` / `r_n2c` | sell at T+1 / T+2 close |
| `r_oc` | same-day open→close (reference only; not realizable under T+1) |
| `unbuyable` | opened at limit-up: excluded from returns, reported as a rate |
| `limit_up_T`, `broken_T` | closed at limit-up / touched and failed on the entry day |

Tables are split by cohort (selected / near-miss / other), tier, stage, sentiment regime and score
quintile, plus an equal-weight equity curve with max drawdown and losing streak. Lists whose future
sessions are not in the store yet are reported as pending, never as zero.

## Research: is a rule really useful?

```sh
sf research                                   # preset rules
sf research --rule "r2_selected and amount >= 1e9" "regime == 'COLD' and 0 <= ret1 <= 0.03"
sf research --scan                            # scan single + pairwise bucket rules
```

Statistics are per day (equal-weight basket of the day's matches; stocks on the same day are
correlated), with three consecutive time segments: train (60%), test1 (20%), test2 (20%). A rule is
marked **out-of-sample valid** only if both test segments are positive with t ≥ 1.5 and ≥ 20 days
each. `--scan` tests hundreds of rules, so a few will pass by chance; prefer rules with a clear reason.

## Backtests

```sh
sf backtest                                            # replay the pre-market list strategy
sf backtest --param STRONG_START.ret1_min=0.07         # one parameter override
sf backtest --grid STRONG_START.ret1_min=0.05,0.06,0.07 --walk-forward 4
sf mainline                                            # mainline-leader + crowded-exit strategy
sf mainline --vary amount_rank_max=50,200,10000        # compare relative-turnover thresholds
```

Results are saved under `stockfish-data/backtests/`. Historical replays cannot use the hot-money
pools (seal times, LHB) because Eastmoney only serves recent days; this is stated in the output.
Theme membership is today's snapshot (boards are only used after their index history starts).

## Data store

| Path (under `stockfish-data/`) | Content |
|---|---|
| `bars/<date>.parquet` | full-market daily snapshot (BaoStock): OHLC, exchange preclose, turnover, ST, status |
| `pools/{zt,zbgc,dtgc,lhb}/<date>.parquet` | limit-up / failed-board / limit-down pools, LHB rows |
| `meta/` | calendar, stock basics, industry, theme boards and members |
| `lists/<target>/<run>/` | `盘前清单.csv`, `picks.csv`, `facts.parquet` (every stock, all features and gate results), `summary.json`, auction files |
| `reviews/`, `backtests/`, `research/` | saved outputs; never overwritten |

Adjusted returns are reconstructed from the exchange preclose (`adj_t / adj_{t-1} = close_t / preclose_t`),
so no adjustment-factor download is needed. A missing session makes affected windows unknown (NaN).

## Strategies and their status

| Strategy | Version | Status |
|---|---|---|
| R2 short-horizon strength (pre-market list core) | `SF_R2_LEAN_V1` | Frozen thresholds from `config/short_horizon_strength_r1.yaml`/`_r2.yaml`. Historical replay (2025-11 → 2026-11): **negative expectancy** under T+1-open entry; use as a watchlist, not a buy list |
| Tier + score layer | `SF_TIER_SCORE_V1` | Heuristic; replay shows no monotonic edge |
| Auction re-rank | `SF_AUCTION_V1` | DAILY_V1 auction thresholds; not yet validated |
| Mainline leader + crowded exit | `SF_MAINLINE_LEADER_V1` | Research only: relative turnover rank helps monotonically, no variant positive in both test segments |
| Sentiment switch | `SF_MOOD_V1` | Risk filter: separates better/worse days out of sample; all tiers negative for relay |
| Abnormal-volatility status | `SF_ABNORMAL_V1` | Risk note; just-triggered stocks did worse next day |

Nothing here is investment advice. Engineering tests passing does not show investment value.

## Legacy pipeline (frozen)

`stock-fish/scripts/codex_daily_v2.py` and `stockfish_core/` are frozen since 2026-11-10. Their
historical documents (`CURRENT_STATUS.md`, `CURRENT_STRATEGY.md`, `DEPLOYMENT.md`) describe that
pipeline. To read old saved results, see those documents; use `--registry-root ../stockfish-legacy/registry`.
