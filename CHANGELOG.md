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
