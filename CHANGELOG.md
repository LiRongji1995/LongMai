# 2026-11-10 Removed `sf auction`: the call auction is read by the person

- Removed `sf/auction.py` and the `sf auction` command. Its SF_AUCTION_V1 rules came from DAILY_V1
  unvalidated and assumed an open-price entry: 一字/≥8% gaps were "观察" (no fill) and gaps ≤ -3%
  were "放弃". On 浙文影业 (11-03, 11-05, 11-06 一字) and 冰山冷热 (10-14 一字, 10-16 -4.2% open →
  limit-up, 11-10 一字) it would have rejected the strongest days. After V7 it also read the list group
  as `tier`, so the A/B gap bands never applied.
- The tool stays at pre-market screening and market temperature; auction and intraday entry decisions
  are the person's. The pre-market list is unchanged.

# 2026-11-10 List themes: 炒作 (news notes) before 板块 (membership)

- Problem: the role said "归属建筑节能" for 腾达科技 while the market traded it as 商业航天. Its Eastmoney
  boards do not include 商业航天, and both board-based rankings fail as attribution (z3 favours tiny
  boards; limit-up counts favour umbrella boards, e.g. 冰山冷热 → 通用设备); co-movement over 20 sessions
  was also tried and is dominated by market beta.
- New `sf/notes.py` and `sf note CODE THEME`: themes from news, stored in `meta/hype_notes.csv`, shown as
  `炒作<theme>(新闻MM-DD)` for 10 sessions; the board label is renamed `板块<board>` (membership only).
- Header: 市场高标 shows the noted theme; new 新闻题材 line (listed stocks grouped by noted theme) and
  待查题材 line (空间板, 3板+ and high-flyers without a note). Screening rules unchanged (still V7): the
  2026-11-10 list differs only in the role labels and these header lines.

# 2026-11-10 Slimming: V7-only `sf`, list in 2.6 s, hit-rate review

Review and scores: `docs/ARCHITECTURE_REVIEW_2026-11-10.md`. The list for 2026-11-10 is identical
before and after (golden text snapshot).

- Removed `premarket.py` (R2 list), `risk.py`, `ranking.py`, `backtest.py`, `research.py`, `outcomes.py`,
  `sf auto` and `bin/sf-schedule`, the commands `backtest`, `research`, `mainline`, `longmai`,
  `premarket --r2`, and the V1–V6 specs (all at tag `legacy-r2`; links to them below refer to that tag).
- `longmai.py` is V7 screening only with one flat `PARAMS`; list assembly, header and saving moved to
  `listing.py`; `mainline.py` keeps only the V7 mainline and leader rules.
- Speed: `store.wide()` builds the session × code frames in one numpy scatter, and the temperature reuses
  the mood indicators: list build 14.7 s → 2.6 s.
- `sf review` now reports entry-free hit rates (lu1, max3, out5, bigloss1) by list group against all
  main-board limit-ups, plus 妖股 recall; new `sf evaluate [screening|temperature]`.
- Fix: `max3` / `out5` at the end of the history used partial windows and counted recent lists as misses;
  they are now pending until all later sessions are stored (test2 recall 46.9% → 46.7%).
- Deleted the 630 MB R2 research dataset; data directory 1.0 GB → 377 MB.

# 2026-11-10 龙脉 V7: screening without an amount threshold, 连板梯队, 高波动, two-axis temperature

- Spec and first hit-rate check: `docs/STRATEGY_LONGMAI_V7.md`. Judged by entry-free hit rates, not P&L.
- Recall of realized 妖股 rises from 8–9% (V6) to 47–53%, with about 14 listed stocks per day. Mainline
  candidates without the 5亿 threshold: 5-session breakout 35.7 / 36.8 / 34.8% vs baseline 25.1 / 24.1 / 20.6%.
- `sf/temperature.py`: heat × pain reading printed as the 水温 line (cell, historical next-day
  loss-effect rate, pressure sources). The 短线资金 background line is folded into heat.
- List: liquidity note (< 1亿) and one-price-board note; candidate text no longer carries mechanical
  position and exit rules; groups 主线龙头 / 连板梯队 / 观察 / 高波动.

# 2026-11-10 Entry-free evaluation (`sf/evaluate.py`): screening hit rates and temperature calls

Next-open P&L backtests are dropped: the person trading enters intraday (auction, 分时 pullbacks,
order book), so the system is judged on whether its picks broke out and its temperature calls came
true. Segments as before (cuts 2025-02-06 / 2025-12-17).

- Screening (V6 rules replayed daily; baseline = all main-board limit-ups that day):
  - candidates: 5-session breakout (≥ 2 limit-ups or +20% high) 27.7 / 32.3 / 30.9% vs baseline 25.1 / 24.1
    / 20.6%; next-day 大面 30 / 28 / 27% vs 16 / 15 / 16% (higher beta both ways);
  - crowded flag works: next-day limit-up 8 / 8 / 3%, breakout 10 / 15 / 6%;
  - vetoed (near severe abnormal) are the strongest and the most dangerous: next-day limit-up 45 / 35 / 24%,
    大面 45 / 58 / 38%;
  - recall: only 8–9% of realized 妖股 (≥ 3 limit-ups in the next 5 sessions) were listed the day before.
    75% of them were outside the top-3 mainlines, 52% outside the theme radar, 52% were first boards.
    Height is the strongest marker (winner rate 首板 4.9%, 2板 12.2%, 3板 18.0%, 4板+ 20.6%); all 2板+
    stocks (~12 per day) hold 48% of the winners. The 5亿 amount threshold removes winners: among 2板+,
    below-threshold winner rate 20 / 20 / 17% vs above 12 / 11 / 7.5%.
- Temperature: the SF_MOOD_V1 phase nodes mostly fail against real outcomes (next-day profit / loss effect
  = limit-up premium and 大面 count versus their trailing 250-day medians). Only 退潮 is consistently
  (modestly) right. New two-axis reading: heat (limit-ups, promotion rate, max height, short-term share)
  and pain (大面, limit-downs, weak failed-board premium, low up ratio). Next-day loss-effect rate by cell
  (train / test1 / test2): 热痛 54 / 50 / 57%, 热顺 38 / 41 / 46%, 冷痛 24 / 41 / 51%, 冷顺 17 / 17 / 27%
  (base 33 / 37 / 48%). Combined stress top quintile 55 / 40 / 66% vs bottom 16 / 17 / 33%.
  Indicator signs were chosen on train; the test segments were inspected before.

# 2026-11-10 龙脉 V6 backtest (rule proxy) — negative; the 空间板 class is the cause

- Spec `docs/STRATEGY_LONGMAI_V6.md` (commit d13642e), code 75adb86. 738 trades, −1.54% per trade;
  train / test1 / test2 −2.04% (t −3.3) / −1.82% (t −2.5) / +0.11%. Every robustness neighbour
  negative over the full history. Run: `backtests/SF_LONGMAI_V6-20261110T133743Z`.
- Split by candidate class: 空间板 trades (170) −4.89% / −6.53% / −1.70% per trade in train / test1 /
  test2, negative at every height (4: −5.3%, 5: −2.0%, 6: −8.6%, 7: −2.0%). Mainline trades (568)
  −1.37% / −0.04% / +0.73%, close to V5 (−1.23% / −0.48% / +1.10%). Buying the market's tallest
  board at the next open when it gaps up loses in every segment: the 空间板 is a sentiment anchor to
  watch, not a next-open buy.

# 2026-11-10 龙脉 V5 backtest (rule proxy) — negative over the full history, positive in test2

- 840 trades (190 crowded, 248 vetoed), −0.60% per trade after 0.15% cost, hit 37.9%, payoff 1.40;
  train / test1 / test2 −1.23% (t −2.6) / −0.48% (t −0.7) / +1.10% (t 1.0). By year 2022 −1.6%,
  2023 −2.0%, 2024 +0.2%, 2025 −0.6%, 2026 +1.2%. Run: `backtests/SF_LONGMAI_V5-20261110T131844Z`.
- Robustness: every neighbour negative over the full history (best: top_k 4 −0.19%, max_hold 5
  −0.18%); test2 positive for 23 of 25 neighbours. Not re-selected.
- Exits: theme fade 46%, failed limit-up 26%, churn 22%. Relative crowding blocks far fewer leaders
  than V4's absolute line (190 vs 1402).

# 2026-11-10 龙脉 V5 code; theme radar, high-flyers, attribution; human-only wording

- The system never trades: the list shows candidates ("候选（由你决定）") and a rule position cap; the
  backtest is a rule proxy. Main board only stays (account constraint).
- V5 implemented per `docs/STRATEGY_LONGMAI_V5.md` (concentration mainlines with overlap dedupe,
  amount threshold before ranking, relative crowding). `sf premarket` / `sf longmai` default to V5.
- `sf/radar.py` (information only, no rule): theme radar by 3-session limit-up concentration, so a
  running theme with a split day stays visible (e.g. 冰雪经济 with 2 limit-ups today, 8 in 3 days);
  市场高标 line for the tallest boards whatever their theme; each stock attributed to its most
  concentrated hot board (龙江交通 → 冰雪经济, not 光伏概念); a shared-name link whose stocks share a
  hot concept is shown as 同概念联动 instead of 玄学. Board data says what a stock belongs to, not why
  it rises: high-flyer reasons come from news, checked by the assistant on each daily run.

# 2026-11-10 龙脉 V4 (`SF_LONGMAI_V4`) — forward-test version; negative full-history backtest

- Spec frozen before the backtest: `docs/STRATEGY_LONGMAI_V4.md` (commit f5c1df3). V1 retired.
  Leaders by board height then turnover rate; amount only as a fixed threshold (today or 3-session
  mean ≥ 5亿); no 3-session abnormal veto; V3 crowding block kept. `sf premarket` / `sf longmai`
  default to V4.
- Backtest: 424 trades (1402 crowded skipped), −0.80% per trade, hit 38.4%, payoff 1.35;
  train / test1 / test2 −2.34% (t −3.0) / +1.38% (t 1.0) / +0.68% (t 0.4). By year 2022–2024
  negative (−2.0 / −2.5 / −1.4%), 2025–2026 positive (+0.4 / +0.8%), when market turnover roughly
  doubled. Robustness: every neighbour negative over the full history; removing the crowding block
  is the worst (−1.82%), so the block helps; 8亿 threshold −0.53%, 3亿 −1.39%.
- The full-history equity figures are additive per-slot sums and exceed −100%; per-trade averages
  are the meaningful statistic here.

# 2026-11-10 Forward test: 龙脉 V1; `sf crowding`

- V1 is the version under forward test (`longmai.FORWARD_VERSION = 1`); `sf premarket` and
  `sf longmai` default to it. V2/V3 stay available for comparison. V1 is unchanged, including its
  3-session abnormal veto, because the forward test needs the frozen spec.
- `sf crowding [--days N]`: daily short-term money temperature for review (market turnover vs
  20-day mean, short-term share and its 250-day percentile, high-turnover share, mainline shares
  with a 60-session-high flag). Context only; it does not select stocks or identify leaders.

# 2026-11-10 Crowding study (拥挤度) — no market-level threshold

- `sf/crowding.py`: market short-term share (`spec_share`, `hot_turn_share`, changes, amount vs
  20-day mean, top-3 mainline share) and theme share / 60-session percentile / 5-day change.
- Study pre-registered and reported in `docs/CROWDING_STUDY.md`. Negative for every market-level
  indicator (no threshold separates relay or leader outcomes; more money and more concentration
  were slightly better, not worse). Positive descriptive finding: a mainline at its 60-session
  turnover-share high fades within 2 sessions about 1.5x as often (train 33% / test1 23% / test2
  34% vs 19% / 16% / 21%), but leader returns do not differ consistently.
- Pre-market summary: each mainline shows its share of market turnover; a 题材拥挤 warning for a
  mainline at its share high; a 短线资金 background line. No rule or version change.

# 2026-11-10 龙脉 V3 (`SF_LONGMAI_V3`) — turnover identifies, crowding blocks entry (negative backtest)

- Spec frozen before the backtest: `docs/STRATEGY_LONGMAI_V3.md` (commit 30f1060). Leaders as in V2;
  crowded (turnover ≥ 30% or ≥ 2× the previous 5-session average) leaders are shown but not bought.
- Backtest: 164 trades (551 crowded signals skipped), −1.13% per trade, train / test1 / test2
  −1.13% / −2.44% / +0.18%. Better than V2 (−1.61%), far below V1 (+0.25%). All robustness neighbours
  negative (best: crowd surge 1.5×, −0.55%).
- Why: activity ranking admits smaller, higher-turnover stocks even below the crowding line. Median
  turnover of traded leaders: V1 10.2% (amount rank median 15), V3 20.2% (amount rank median 52).
  Leaders identified by traded amount, i.e. where large money concentrates, did better than leaders
  identified by turnover.

# 2026-11-10 龙脉 V2 (`SF_LONGMAI_V2`) — activity and no 3-day veto (negative backtest)

- Spec frozen before the backtest: `docs/STRATEGY_LONGMAI_V2.md` (commit bd09827). Activity = mean of the
  whole-market percentile ranks of traded amount and turnover rate; leader condition activity rank ≤ 30;
  the 3-session abnormal-volatility veto is removed. V1 stays reproducible (`sf longmai --version 1`).
- Backtest (same period and slots): 307 trades, −1.61% per trade, hit 37.5%, train / test1 / test2
  −1.20% / −3.56% (t=−3.3) / −0.55%. Every robustness neighbour is negative. Decomposition: V2 with
  activity switched off (amount only, no 3-day veto) is +0.03% per trade, so the turnover component
  causes most of the loss and dropping the 3-day veto costs about 0.2% per trade versus V1 (+0.25%).
  Reading: very high turnover on the signal day marks crowding — the condition the thesis exits on.
- Post-list name links (`namethemes.links`): each listed stock is checked against today's strong stocks
  for a shared word, character, homophone or 3-digit code suffix (binomial tail < 0.1%, strict because of
  multiple comparisons). Shown as 玄学线索 for human judgement; no effect on the decision.
- List columns: 成交额 now shows amount, turnover rate and activity rank.

# 2026-11-10 Name contagion (名字/谐音扩散)

- `sf/namethemes.py` (`SF_NAME_CONTAGION_V1`): groups built from every stock name — same character,
  same two-character word (e.g. 东方), same toneless pinyin (e.g. yang: 羊阳洋扬杨) — with industry,
  legal-form, commodity and delisting words excluded. A group is hot when its limit-ups are improbable
  under the market limit-up rate (binomial tail < 1e-4, at least 3 limit-ups). No seed keywords: all
  ~800 groups are scanned daily. Detected without prompting: the 羊/洋/阳 wave (2026-10-21 → 11-03), the
  东方 wave (11-06 → 11-09), 华 on 2026-09-21.
- Out of sample (next-open entry, next-open exit): limit-ups inside a hot name group do no better than
  all limit-ups (−0.22% / +0.29% / −0.44% excess); members not yet limit-up do worse the next day
  (test1 −0.52% t=−1.7, test2 −0.94% t=−2.1). Informational + risk note only; candidate mainline source
  for V2. The 龙脉 list shows up to three distinct waves with their leaders and tags followers.

# 2026-11-10 龙脉 V1 (`SF_LONGMAI_V1`) — first formal strategy

- Spec frozen before the backtest: `docs/STRATEGY_LONGMAI_V1.md` (commit d58af71). `sf premarket` now
  builds the 龙脉 list by default (header: sentiment, regulatory temperature, position budget, mainlines;
  groups 主线龙头 / 观察 / 回避; columns 名称 / 角色 / 成交额(排名) / 参与建议 / 风险提示). The R2 list
  stays available with `sf premarket --r2`. `sf longmai [--robust]` runs the portfolio backtest.
- Noise filter completed before accepting the backtest: earnings-status, valuation, style and ownership
  buckets (e.g. 2026中报首亏, 破增发价股, 超跌股, 风格, 基金增仓) were being picked as mainlines. The run
  before the fix (126→127 trades, +0.09%/trade) is kept in `backtests/longmai-v1-prefix-noise-run.log`.
- Backtest (2022-06 → 2026-11, 4 slots × 1/4, cost 0.15%): 126 trades, +0.25% per trade, hit 39.7%,
  payoff 1.63, realized total +8.0%, max drawdown −23.4%; train / test1 / test2 per-trade
  +0.07% / +0.21% / +0.71% (t ≈ 0). At 0.30% cost: +0.10% per trade. Essentially break-even.
- Robustness (one step either way, not re-selected): results move a lot (turnover rank 20: +1.24%,
  leader rank 1: −0.03%, gap ≥ −1%: −0.46%), so V1 is fragile. The gap filter and the vetoes matter
  (no vetoes: +0.01%); the market gate lowers return but also drawdown (no gate: +0.62%, DD −30%).
- Observed structural issue: the height leader (e.g. a 7-board small cap) and the turnover leader
  (中军) are usually different stocks, so V1 rarely trades (~28 trades a year). Candidate for V2.

# 2026-11-10 Exchange monitoring rules and regulatory temperature

- `docs/REGULATION_NOTES.md`: SSE/SZSE abnormal-trading real-time monitoring rules (2023 versions, still
  current), severe-abnormal-volatility definitions, measures ladder, weekly supervision reports and dated
  enforcement episodes, with source grades ([P] primary, [S] secondary, [?] unverified).
- `abnormal.severe`: severe abnormal volatility (4 three-day triggers in 10 sessions on the main board,
  3 on ChiNext/STAR; 10-day +100%; 30-day +200%), distance to the trigger and days since the last one.
- `sf/regulation.py`: regulatory temperature = SZSE weekly abnormal-trading cases per unit of market
  turnover, percentile over the last 52 reports, usable from the first session after publication; the
  weekly report is fetched and parsed automatically (`pypdf`).
- Out-of-sample (per day, next-open entry, sell at the following open):
  - Limit-ups with high vs low regulatory temperature: −1.20% / −1.15% / −1.23% vs
    −0.92% / −0.52% / −0.75% (train / test1 / test2).
  - Limit-ups within 30% of the 10-day severe trigger: −2.72% / −1.99% / −1.50% (all limit-ups:
    −1.11% / −0.75% / −0.71%).
  - Both are added to the risk notes; neither makes a buy rule positive.

# 2026-11-10 LHB seats, sentiment switch, abnormal-volatility status

- `sf seats`: full LHB seat detail since 2022-03 (1,127 sessions, Eastmoney datacenter, two requests
  per session), seat types (institution / northbound / Lhasa retail / other branch) and point-in-time
  seat skill (outcomes used only after they are known). Captured daily by `sf update`/`sf premarket`.
- `sf mood` (`SF_MOOD_V1`): 12 bar-based sentiment indicators as 250-session percentiles; the composite
  uses 5 indicators selected on the training segment only. A high promotion rate predicts a worse
  next day (crowding).
- `SF_ABNORMAL_V1`: 3-session deviation against the exchange benchmark with reset after a trigger;
  matches actual LHB listing reasons with precision 81% / recall 74%.
- The pre-market advice now ends with a risk context (sentiment tier, abnormal-volatility status, LHB
  seat structure). The list stays 3 columns.
- Out-of-sample findings (per day, entry at next open, sell at the following open, 0.15% cost):
  - Buying LHB stocks is negative in every segment (all: −0.92% / −0.68% / −0.67%). Seat information
    ranks consistently but never turns positive: institutions ≥2 −0.46% / −0.25% / −0.46%; Lhasa retail
    net buying −1.17% / −0.98% / −1.54% (worst); skilled hot-money net buying −0.89% / −0.58% / −0.30%.
  - Sentiment top tercile vs bottom tercile for the relay basket: −0.22% vs −0.85% (test1),
    −0.16% vs −1.01% (test2): a risk filter, not alpha.
  - Stocks that triggered the 3-day rule today did worse the next day in every segment.
  - None of the three filters makes the mainline-leader strategy positive in both test segments.

# 2026-11-10 Lean `sf` CLI; legacy pipeline frozen

- New `stock-fish/sf/` package and `bin/sf` wrapper replace the daily use of `codex_daily_v2.py`, which is
  frozen. AGENTS.md now rests on three invariants: point-in-time correctness, explicit missing data,
  reproducible results.
- Data: one parquet snapshot per session (BaoStock full-market daily), 1,127 sessions 2022-03 → 2026-11
  (318 MB); adjusted prices from the exchange preclose chain, so no adjustment-factor downloads.
  Daily Eastmoney limit-up / failed-board / limit-down pools and LHB rows; theme boards with
  board-visibility dates to limit look-ahead.
- Speed: core R2 computation 293 s → ~3 s; a full pre-market run is ~1.5 min (mostly network).
- Commands: update, premarket (3-column list), show, auction, review, backtest, research, themes,
  mainline, status. Offline tests in `tests/sf`.
- Research findings (per-day statistics, 60/20/20 time split, entry at target open, 0.15% cost):
  - R2 selections: negative in every segment (train −0.57%/day t=−4.7; tests −0.17%, −0.13%).
  - Raising the absolute turnover floor (1/3/5/10/20 亿) does not make R2 positive out of sample.
  - Buying yesterday's limit-ups at the open is consistently negative (first board t=−6.0/−2.2/−2.3).
  - Buying on COLD sentiment days was flat 2022–2025 and positive only in the last year: not robust.
  - A scan of 367 single/pairwise bucket rules × 3 exits found no rule valid in both test segments.
- Mainline leader + crowded exit (`SF_MAINLINE_LEADER_V1`, `sf mainline`): leader of a top-3 theme by
  limit-up count, entry at next open, exits on failed board / churn / MA / theme fade / stop / time.
  - Relative turnover rank matters monotonically (avg per trade: no limit −2.21%, top 200 −1.61%,
    top 100 −1.48%, top 50 −1.16%, top 30 −0.85%), but every variant is still negative overall.
  - Pre-declared variants (top-30 rank, gap ≥ 0, leader rank 1, MA5/MA10/no MA exit, churn 2×/3×):
    none is positive in both test segments. Best: top 30 + gap ≥ 0 (test1 −1.26% t=−0.84,
    test2 +3.30% t=1.70). The strategy stays research-only; it is not used for the pre-market list.
  - Noise concept boards (price-action/popularity buckets) are now filtered when themes are loaded;
    `trend_ma` makes the MA exit configurable (0 disables).

# 2026-11-09 按日因子审查与重复处理优化

- 核对按日因子事件接口及SDK分页边界，未取得完整历史修订保证，保持原全锚定复权采集，不接入不充分的日增量。
- 单次归一化内有界复用时钟投影，可见性检查只序列化必需时间字段；不改变原来源、数学口径、名单和UNKNOWN。
- 核心来源认证在单次事务内复用已验证字节，后续认证及终验继续读取SHA与长度；历史producer文件不变。
- 核心复用已有同字节编码函数，保留所有摘要重算与资源检查；修复并发认证在锁外保存临时读取方法的问题，正常及异常退出均恢复原方法。
- 缓存容量仍按原频率全量统计并持共享锁，消除冗余路径和取得锁前的重复检查；预算、请求账本及失败记录不变。
- 阶段工程基准与真实新交易日完整耗时分别报告，详见CURRENT_STATUS；未将旧结果重读计为新日提速。

# 2026-11-09 同交易日一次增量刷新

- 在原已成功续跑的operation下追加固定刷新事务，复用已有完整来源，只补必要的新缺失请求；不提供旧失败重试或新预算。
- 累计扣除旧续跑的调用、读取、耗时与全部job输出，并为本次准备及终验预留额度；保留原失败、UNKNOWN、旧报告和原登记。
- 新刷新绑定目标交易日09:30上海时间截止；同一claim失败、中断不重开，成功只读复用。策略与供应商producer文件保持原样。
- 工程测试与真实更新、完整耗时分开记录，实际边界见CURRENT_STATUS。

# 2026-11-07 龙虎榜业务日期兼容与封存初筛复核

- 严格兼容供应商的午夜日期表示，复用同一投影函数；不截断任意后缀，不放宽窗口、证券身份、完整性或实际可见时间校验。
- 唯一daily入口增加已封存任务的只读初筛复核，保留原计数并输出复算计数；使用同份完整原件，零新增采集，不改策略、预算或历史任务。
- 真实续跑、工程验证及复算边界见CURRENT_STATUS。

# 2026-11-07 祖先复权UNKNOWN显式核对登记

- 在用户明确授权后，将唯一旧UNKNOWN与已有同请求完整原件的关系封签至原continuation；保留全部旧记录和未知计量，不重采旧请求。
- 原观察日的新复权补采仅对获授权的精确证券集合取得有界重叠许可；原key、其他日期、额外不明attempt或证据变化继续拒绝。
- 沿用原完整锚点、策略、预算、scope和单次深交所恢复限制；验证及真实结果边界见CURRENT_STATUS。

# 2026-11-07 限定一次来源恢复续跑

- 唯一daily入口增加原失败任务的只读续跑核算和显式一次执行；原key、scope、失败与请求记录保留，不创建平行运行账本。
- 只接受明确授权的同日深交所名册精确超时来源；一次claim不可重开，其余成功数据复用，新必需来源失败落账后停止。
- 先扣除原请求预留与读取、耗时、输出，再预留协调校验开销；工作进程及终验绑定同一剩余额度，不改登记预算和策略。
- 修复导入成功索引可能掩盖祖先未提交复权失败的检查缺口；新的重叠日期/批次继续阻断，精确缓存复用和不相交请求保留。
- 测试及真实执行边界见CURRENT_STATUS；工程发布不代表真实初筛完成。

# 2026-11-06 明确恢复证据兼容补充

- 只读认证既有明确恢复的完整控制与原件链，识别已精确覆盖的旧失败attempt；不删除失败、不增加新重试能力。
- 从原runtime scope向预检、工作进程和最终验证传递已绑定的恢复引用，仅扩展本job配置，不改登记配置或预算。
- 16文件冻结回归275项测试与12个子测试通过。11月6日真实运行新增7个请求，其中深交所名册超时，未生成初筛；真实失败及预算保留，实际边界见CURRENT_STATUS。

# 2026-11-06 旧恢复登记兼容与失败诊断

- 只读认证旧epoch继承标记的原请求范围，解除已完成日历请求对龙虎榜/复权的误阻；失败、缺链及不明尝试继续阻断。
- 复权守卫识别同一原请求已有且经完整认证的恢复结果；保留旧PARTIAL及全部attempt，不增加重试权限或修改预算。
- 监督层保留工作进程的原始失败原因和completion封签，部分展示不冒充成功；成功来源及源码校验不变。
- 修正周末敏感的测试时钟，保留断言；最终12文件组合209项测试及12个子测试通过。实际验证边界见CURRENT_STATUS。

# 2026-11-06 采集范围优化

- 核心主动采集全市场窗口由67会话收敛到原策略必需的25会话；可选解释额外42会话不再触发补采。
- 复用原normalizer和日期化资格规则，仅跳过明确排除证券的复权；保留所有合资格及UNKNOWN证券、完整复权锚点和原批次缓存。改批次或日期不能绕过原失败及未完成请求。
- 新闻和题材详情在龙虎榜或成交额初筛后处理；题材只使用既有同日完整成员和历史缓存，缺源如实UNKNOWN，保留原策略名单和题材分母。
- 只读计划同步实际请求范围、资格预览和可选零网络预算。冷缓存少84个行情/master逻辑请求，不宣称整体耗时或真实投资效果。
- 测试与实际边界见CURRENT_STATUS；真实DB、历史原件、失败、预算及运行登记不变。

# 2026-11-05 个人自用精简

当前工作树只保留本机股票研究所需实现。精简前已发布版本为 `099803532717c60c64f37f9285b56da5c63b83ce`；此前未提交源码及两份检出差异另有经过 SHA 验证的私有备份，不进入仓库。本轮保留流程依赖的既有 SHA/长度校验修订一并纳入交付。

## 保留模块的职责

| 模块组 | 必要功能 |
|---|---|
| `codex_daily_v2`、`codex_workspace`、`codex_capacity` | 唯一用户入口，原登记/operations/祖先账本锁、容量、防重与有限执行 |
| `codex_premarket*`、单个 V3 进程监督器 | 原盘前计划和工作流、超时/资源限制、拥有的进程树回收 |
| `codex_core*`、`codex_result_view`、`codex_verification` | 核心输入、保存成果内容认证、历史读取及导出；无候选执行树 |
| `yaogu/provider*`、`request_queue`、`premarket_capture/runtime*`、normalizer/来源/日期资格模块 | 更新及复用所需数据、父来源与时间完整性、采集账本 |
| `engine`、`strategy_experiments`、`stage_aware_momentum`、R1/R2及其事实/评分/题材数学依赖 | 原 PV、V2、冻结 DAILY 与既有 R2 的确定性研究和解释 |
| `candidate_service`、`storage`、`jobs`、结果存储/导出模块 | 不可变历史、原SQLite结构、业务作业状态和只读结果 |
| 后验/评分、archive bridge及旧manifest读取器 | 原run的成熟度、必要统计、来源鉴证与历史格式读取 |
| `snapshot_backup`、`file_integrity` | 含WAL/来源/账本的备份、新目录恢复与SHA/长度检查 |

## 实际移除

- Go、Live、Qlib上游镜像与训练/推理包装、MiroFish、LLM/飞书/模拟桥、网页/API与其依赖。
- `codex_chat`、旧daily/research/stockfish入口、shell/PowerShell包装、未启用active树和authority平台。
- 多代环境恢复/源码bundle构建/性能基准/共享候选engine/独立研究实验、重复监督版本及旧调度入口。
- Docker、Windows安装与跨平台发布工具、分层冗余requirements、复杂CI选测/多组件构建。
- 旧交付文档和archive源码文档。没有把删除内容搬入新的archive目录；真实结果、RAW、失败、预算、凭据与备份不清理。
- 仅服务已删除功能的测试随功能删除；有效策略/来源/存储/后验断言改为直接业务接口测试。测试删减不计入源码减量。

## 统计口径

以本轮开始时两份检出的私有SHA清单与最终工作树比较，不按较早Git版本抹掉既有未提交实现：

| 口径 | 精简前 | 精简后 |
|---|---:|---:|
| 项目应用Python源码 | 435文件／164,148行 | 103文件／41,349行 |
| 全仓实现源码，另含上游和多语言 | 1,002文件／296,245行 | 103文件／41,349行 |
| 根协作说明推荐的日常Python用户入口 | 4 | 1 |
| 日常直接运行依赖，递归展开requirements去重 | 13 | 6 |
| 原总requirements直接依赖 | 27 | 6 |
| 自有操作与规格文档 | 163 | 21 |
| 原登记环境已安装分发包，含传递及测试依赖 | 58 | 42 |

应用源码统计`stock-fish/`下`.py`，含维护脚本及被ignore遮住但仍被Git跟踪的旧记忆库，排除`tests/`和上游`MiroFish/`；物理行包括注释和空行。全仓实现统计py/go/js/ts/tsx/jsx/vue/html/css/sh/ps1/pyx/pxd，排除test/tests/testdata/fixtures路径、test_前缀和_test.go，也排除受保护的review历史证据复算脚本。两项均是当前工作树实现量，不声称全部旧代码曾日常运行，也不把删除测试计入源码减量。Qlib示例notebook另行删除，不折算为源码行；旧模型文件原字节留在本机，已从新发布树取消跟踪。

入口按本轮开始时根AGENTS的命令路径去重：daily_v2、daily、chat、research收敛为daily_v2。内部worker和测试运行器不算用户入口。文档只计md/rst，排除上游、Go/Live、测试说明及受保护历史研究结果。未将源码搬进archive，历史数据未删除。

明确补缺接入同一盘前入口的`--collect-missing`，不增设平行采集器。保留原预算、失败账本与锁；修复通用后验绕开R1/R2入口及旧tracking请求防重、恢复路径穿越与符号链接边界。策略公式及9件配置的原字节保持不变。验证和发布边界见 [CURRENT_STATUS](CURRENT_STATUS.md)。
# 2026-11-06 个股资讯采集初筛

在原盘前入口增加近5交易会话龙虎榜汇总及 `COLLECTION_PRESCREEN_V1`：龙虎榜或强成交额通道入围，候选之外不采个股资讯，未知单列。沿用原请求、有限预算和内容验证；全池行情、复权、题材、策略公式和原名单不裁减。无新依赖、数据库或运行树。具体使用及边界见 USER_GUIDE；工程验收与未执行范围见 CURRENT_STATUS。
