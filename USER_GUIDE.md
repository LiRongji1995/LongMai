# User Guide — `sf` CLI

`sf` is the daily tool. Run it from anywhere with the wrapper:

```sh
stockfish-qlib-quant-system/bin/sf <command>
```

The wrapper uses the Python environment `../stockfish-env` next to the checkout (override with
`SF_PYTHON`). Data lives in `../stockfish-data` (override with `SF_HOME`). Tip: add an alias such as
`alias sf=/path/to/stockfish-qlib-quant-system/bin/sf`.

The system never trades. The list is a screening and temperature read for a person who decides
entries and exits intraday (auction, 分时 pullbacks, order book). Nothing here is investment advice.

## Daily routine

| When (Shanghai) | Command | What you get |
|---|---|---|
| After 18:00 | `sf premarket` | Updates data, captures the day's limit-up/failed/limit-down pools and LHB, writes the list for the next session (`lists/<target>/<run>/盘前清单.csv` + `盘前概况.txt`) |
| 09:25–09:30 | `sf auction` | Re-ranks the latest list with call-auction data: 参与 / 观察 / 放弃 with the reason |
| Any time | `sf show [TARGET]` | Prints a saved list |
| Weekly | `sf review [--since D]` | Hit rates of every saved list (below) |
| Weekly | `sf themes` | Refreshes concept/industry board membership (~20 min) |
| Any time | `sf mood [--days N]` | Sentiment indicators, percentile level and tier (低/中/高) |
| Any time | `sf crowding [--days 20]` | Short-term money temperature: market turnover vs its 20-day mean, short-term share, high-turnover share, mainlines with their turnover share (`!` = at a 60-session high). Review context only |
| Any time | `sf seats --code 600000 [--date D]` | One stock's LHB seats; `sf seats` prints the seat leaderboard |
| Any time | `sf status` | Data coverage |
| After the news check | `sf note 001379 商业航天 [--date D] [--source URL]` | Records why a stock is really traded; `sf note` lists the records |

Nothing is scheduled automatically; run the commands yourself.

## The list (龙脉 V7)

Spec: [docs/STRATEGY_LONGMAI_V7.md](docs/STRATEGY_LONGMAI_V7.md). Main-board stocks only.

| Group | Content |
|---|---|
| 主线龙头 | Leaders (rank ≤ 2 by height, then turnover rate) of the top-3 mainlines (3-session limit-up concentration, overlapping waves merged), plus the 空间板 (tallest main-board stock, ≥ 4 boards) |
| 连板梯队 | Every other eligible main-board 2板+ stock |
| 观察 | R2 strength matches outside the mainlines, and name-wave leaders |
| 高波动 | Near or after severe abnormal volatility, or heavy Lhasa retail buying: the strongest and the most dangerous |

`拥挤` (turnover ≥ 2× its own 5-day mean without a sealed limit-up) is a flag, not a group. Amount is
displayed, not filtered. Header lines: 情绪 / 监管温度 / position ceiling, 主线, 题材拥挤, 水温 (heat × pain
cell with the historical next-day loss-effect rate and the pressure sources), 名字扩散, 市场高标,
题材雷达, 同概念联动 / 玄学线索, 新闻题材, 待查题材.

**Themes: 炒作 vs 板块.** Board data says what a stock belongs to, not why it rises, and the membership
snapshot misses new stories (on 2026-11-10 腾达科技 traded as 商业航天 but is not a member of that board).
So the role shows `炒作<theme>(新闻MM-DD)` when a note exists (`sf note`, from news and announcements,
used for 10 sessions) and `板块<board>` for the most concentrated hot board it is a member of, which is
membership only. The 待查题材 line names the 空间板, 3板+ and high-flyers with no note yet; the
新闻题材 line groups listed stocks by their noted theme. Notes live in `meta/hype_notes.csv`.

## Judging the system: hit rates, not P&L

```sh
sf review                 # saved lists: hit rates by group vs all main-board limit-ups, recall of 妖股
sf evaluate               # full history replay: screening hit rates and temperature calls
sf evaluate temperature   # temperature only (seconds)
```

Outcomes are measured from the observation close, with no entry price:

| Label | Meaning |
|---|---|
| `lu1` | limit-up on the next session |
| `max3` | highest high over the next 3 sessions |
| `out5` | "走出来": ≥ 2 limit-ups within 5 sessions, or a +20% high |
| `bigloss1` | next-day 大面: low ≤ −7% or close ≤ −5% |
| recall | of the realized 妖股 (limit-up today, ≥ 3 limit-ups in the next 5 sessions), the share listed |

Outcomes whose sessions are not in the store yet are pending, never misses. `sf evaluate` splits the
history into train / test1 / test2 (cuts 2025-02-06 / 2025-12-17); change rules on train evidence
first. Outputs go to `stockfish-data/reviews/` and `stockfish-data/evaluations/`, never overwritten.

## Sentiment, seats and abnormal volatility

- **Sentiment (`SF_MOOD_V1`)**: 12 daily indicators from bars, each as its percentile over the last 250
  sessions; the composite uses limit-downs (−), promotion rate (−), big losses (−), failed-board premium
  (+) and breadth (+). Tiers are terciles. Used for the position ceiling line and as context.
- **Temperature (`sf/temperature.py`)**: heat (limit-ups, promotion rate, max height, short-term share)
  and pain (大面, limit-downs, weak failed-board premium, low up ratio), each a mean percentile.
- **LHB seats**: full seat detail since 2022 (`pools/seats/`). A seat's skill uses only outcomes
  already known on the day (two-session lag).
- **Abnormal volatility (`SF_ABNORMAL_V1`)**: 3-session cumulative deviation against the exchange
  benchmark (precision 81%, recall 74% against the LHB reasons). Severe abnormal volatility (main board:
  4 triggers in 10 sessions, 10-day +100%, or 30-day +200%; daily deviations are summed) drives 高波动.
- **Regulatory temperature**: SZSE weekly abnormal-trading cases per unit of market turnover, as a
  percentile of the last 52 reports. Rules and history: [docs/REGULATION_NOTES.md](docs/REGULATION_NOTES.md).

## Data store

| Path (under `stockfish-data/`) | Content |
|---|---|
| `bars/<date>.parquet` | full-market daily snapshot (BaoStock): OHLC, exchange preclose, turnover, ST, status |
| `pools/{zt,zbgc,dtgc,lhb,seats}/<date>.parquet` | limit-up / failed-board / limit-down pools, LHB rows and seats |
| `meta/` | calendar, stock basics, theme boards and members |
| `lists/<target>/<run>/` | `盘前清单.csv`, `盘前概况.txt`, `picks.csv`, `leaders.csv`, `facts.parquet` (every stock with its features and list group), `summary.json`, auction files |
| `reviews/`, `evaluations/`, `research/` | saved outputs; never overwritten |

Adjusted prices are reconstructed from the exchange preclose (`adj_t / adj_{t-1} = close_t / preclose_t`).
A missing session makes affected windows unknown (NaN).

## Legacy

The R2/qlib pipeline, the next-open backtests (`sf backtest`, `sf research`, `sf mainline`,
`sf longmai`), the launchd scheduler and the V1–V6 specs and code were removed from `main` on
2026-11-10. They are preserved at git tag `legacy-r2` (`git checkout legacy-r2`).
