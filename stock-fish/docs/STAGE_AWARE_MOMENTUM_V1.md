# STAGE_AWARE_MOMENTUM_V1 阶段感知强势股研究框架

本文件对应用户指定的最新执行规格《StockRecommendation：阶段感知的强势股研究框架与落选反思》。它是现有系统上的增量研究臂，不替代、重命名或重写旧 DAILY、PV、焦点、DNA、THEME、`THEME_CAPITAL_TREND_V1` 及历史结果。两只指定股票是否入选不作为验收标准。

## 1. 用户硬性要求

- 从日期化合格的沪深主板 A 股全池开始，不从旧焦点 17 只开始。
- 排除科创板、创业板、北交所、B 股、非普通 A 股、ST、停牌、退市整理和未来状态不明证券。
- 保留旧策略、旧结果和落选记录，不以后验表现覆写历史规则。
- 先识别阶段，再按阶段看证据；不能把 `A>=x`、`CLV>=x` 作为所有阶段的统一硬门槛。
- 观察日成交额 `>= 500,000,000` 元、换手率 `>= 7%` 仍是正式研究名单的流动性底线。
- 完全没有可核验板块/题材链接时，不进入正式阶段感知名单。
- 板块资金不能沿用上一版 `2只/1亿/2000万/3天` 为统一硬门槛；这些数值只保留为统计候选，资金输出 `STRONG / MODERATE / WEAK / UNKNOWN`。
- `WEAK` 或关键资金证据完全 `UNKNOWN` 不进入正式名单。
- AI 负责解释阶段、证据、反证、旧规则与新框架差别、未知和过拟合风险；AI 不修改历史、不调参救个股。
- 每 5 个交易日做一次复盘，比较 PV、旧焦点、阶段感知及其他研究臂；输出“条件避免了什么、错过了什么”。

## 2. 本版初始研究参数

以下参数写入 [stage_aware_momentum_v1.yaml](../config/stage_aware_momentum_v1.yaml)，状态为 **尚未验证**。它们是为了实现硬性要求的可追踪初始研究参数，不是用户原有规则，也不是已验证最优条件。

- 阶段：`START`、`ACCELERATION`、`HIGH_LEVEL_DIVERGENCE`、`TREND_PULLBACK`。
- 主阶段展示顺序：`HIGH_LEVEL_DIVERGENCE`、`ACCELERATION`、`TREND_PULLBACK`、`START`；同时保存其它通过阶段和所有阶段候选状态。
- `START` 才使用 `A>=1.2` 作为种子证据；该条件不用于高位分歧或趋势整理的早筛。
- `ACCELERATION` 看 3/5/10 日收益、涨停/连板、高换手持续、市场高度和 RS；不要求成交额继续扩张。
- `HIGH_LEVEL_DIVERGENCE` 保存日内路径：前收、开高低收、低/高/收盘涨跌、振幅、CLV、修复点、低点到收盘反弹。当前没有硬阈值证明，只用种子参数识别研究候选。
- `TREND_PULLBACK` 看 MA5/10/20/60/120/250、收盘相对长均线、连续调整天数和到 MA5/10/20 的距离。MA250 不足时为 `UNKNOWN`。
- 主市场相对强弱固定为 `MAINBOARD_EQUAL_WEIGHT_PRICE_DIAGNOSTIC`，不事后选择最有利基准。SSE/CSI300 等官方指数若输入缺失，标 `UNKNOWN`。
- 题材保存所有可见主题，固定按 `theme_id` 展示，不挑最有利主题作结论。板块当日下跌不自动排除高位核心股。
- 资金等级：`STRONG`、`MODERATE` 才算正式资金通过；`WEAK` 与 `UNKNOWN` 阻断正式名单。代理资金单独保留，不当作公开龙虎榜或分类席位。

## 3. 已实现入口

- 配置：[stage_aware_momentum_v1.yaml](../config/stage_aware_momentum_v1.yaml)。
- 实现：[stage_aware_momentum.py](../stockfish_core/yaogu/stage_aware_momentum.py)。
- 实验 profile：`STAGE_AWARE_MOMENTUM_V1`。
- API、摘要、详情和 JSON/CSV 导出沿用现有 `strategy_experiments` 保存体系；旧 `PV_OBSERVATION_V1`、`PV_FOCUS_PREVIEW_V0.1` 和 `THEME_CAPITAL_TREND_V1` 不改名。
- Codex 薄入口 [codex_research.py](../scripts/codex_research.py) 已加入 `STAGE_AWARE_MOMENTUM_V1`。

## 4. 首轮真实输入审计

审计时间：2026-09-07。输入为本机已保存的 2026-09-04 历史重建 MarketInput：`reports/codex/pv_observation_20260904_rebuilt_20260907T091708/market-input.json`。首轮输出保存在被 Git 忽略的 `reports/codex/stage_aware_momentum_v1_initial_audit_20260907T111840/`。

事实：

- 观察日：2026-09-04；决策截止：2026-09-07T09:17:11+08:00。
- 日期化合格主板证券：3043。
- 旧 PV 可计算：3039；旧 PV 入围：525；旧焦点：17。
- 输入只有 6 个交易日价格，缺 10/20 日收益和 MA20/60/120/250。
- 输入没有可授权题材快照，也没有资金 payload。

规则计算：

| 项目 | 结果 |
|---|---:|
| `STAGE_AWARE_MOMENTUM_V1` 正式入选 | 0 |
| 流动性底线 PASS | 199 |
| 阶段识别 PASS | 648 |
| 题材链接 UNKNOWN | 3043 |
| 资金链接 UNKNOWN | 3043 |
| `START` PASS | 525 |
| `ACCELERATION` PASS | 221 |
| `HIGH_LEVEL_DIVERGENCE` PASS | 76 |
| `TREND_PULLBACK` UNKNOWN | 3043 |

判断：

- 这次正式名单为 0，是证据缺口导致的 `INSUFFICIENT_EVIDENCE`，不是证明阶段感知框架没有候选。
- 6 日输入足够解释 A、R、H、日内修复、1/3/5 日 RS 和部分阶段，但不足以验证趋势整理或长期均线。
- 外部网页补充了龙虎榜线索，但这些资料没有进入当时保存 MarketInput，不能悄悄转成正式资金证据。

未知：

- 当前输入没有官方指数 bars，SSE/CSI300 相对强弱为 `UNKNOWN`。
- 当前输入没有 PIT 题材成员、同题材资金和可解析官方交易公开信息，正式名单不能补填。
- 当前本地只有 2026-09-04 一个保存实验日期，无法完成 5 个交易日滚动复盘。

## 5. 国芳集团路径

事实与旧规则：

- `601086.SH` 国芳集团在 2026-09-04 是日期化合格主板 A 股。
- 旧 PV 已入围且排名第 1：`A=5.306857`，`R=0.516787`，`H=0.199017`，成交额约 15.50 亿元。
- 旧焦点未入选：`close_location=0.683128`，低于旧焦点硬门槛 0.70；旧规则执行正确，不是算法漏算。

阶段感知计算：

- 流动性底线 `PASS`，换手约 19.60%。
- 日内路径：低点约 -9.99%，高点约 +9.91%，收盘约 +3.60%，振幅约 19.90%，修复点约 13.60 个百分点。
- `RS_market_1d` 约 +3.67 个百分点。
- 阶段候选：`START / ACCELERATION / HIGH_LEVEL_DIVERGENCE` 均 `PASS`；主阶段按本版展示顺序为 `HIGH_LEVEL_DIVERGENCE`。
- 正式状态仍为 `UNKNOWN`，原因是 `SAM_SECTOR_DATA_UNAVAILABLE`、`SAM_FUNDS_WAITING_FOR_SECTOR_LINKAGE`。

讲人话：

旧焦点规则盯的是“收在全天位置要足够高”。国芳那天波动很大，最后虽然红了，也从深水修上来，但收盘位置只有 0.683，差旧门槛一点，所以旧焦点没放它进去。新版会把这种“高位大分歧后修复很强”的状态单独标出来；不过没有题材和资金证据时，它还不能变成正式名单。

外部补充：证券之星转载证券时报网的 2026-09-04 龙虎榜报道称，国芳集团当日涨 3.60%、换手 19.60%、成交额 15.50 亿元、振幅 22.11%，因日振幅上榜，并列出沪股通和营业部席位净买入数据。这是审计补充，未进入正式 MarketInput。

## 6. 金健米业路径

事实与旧规则：

- `600127.SH` 金健米业在 2026-09-04 是日期化合格主板 A 股。
- 旧 PV 因子可计算：`A=1.031010`，`R=0.045030`，`H=0.127861`，成交额约 32.18 亿元。
- 旧 PV 未入围，拒绝原因为 `PV_AMOUNT_RATIO_BELOW_1_2`；因为未入 PV 正式排名，也不会有旧焦点记录。

阶段感知计算：

- 流动性底线 `PASS`，换手约 40.76%。
- 日内路径：低点约 -10.02%，高点约 +2.76%，收盘约 -2.92%，振幅约 12.79%，修复点约 7.10 个百分点。
- 5 日收益约 +4.50%，但 `RS_market_1d` 约 -2.86 个百分点。
- 阶段候选：`ACCELERATION` 为 `PASS`，`HIGH_LEVEL_DIVERGENCE` 为 `FAIL`，`TREND_PULLBACK` 为 `UNKNOWN`。
- 正式状态仍为 `UNKNOWN`，原因同样是题材与资金证据缺失。

讲人话：

金健不是“没人交易”，恰恰相反，它绝对成交额和换手都很高。旧 PV 卡它，是因为旧 PV 的 A 看的是“今天比前五天平均成交额有没有明显放大”，它只有约 1.03，没有到 1.2。也就是说旧规则认为它已经连续热了一段，今天没有继续明显加码，所以没进旧 PV。新版不再用 A 统一卡死这类高换手高热度票，但它当天收跌、相对主板偏弱，不能硬塞成“高位分歧修复很强”；更像加速段或高热度阶段里的分歧日，需要板块和资金确认。

外部补充：东方财富 Choice 数据文章称，金健米业 2026-09-04 因日换手率达 20% 上榜，收报 12.30 元、涨跌幅 -2.92%、换手率 40.76%、成交额 32.18 亿元；同花顺页面列出 2026-09-04 及此前多日龙虎榜买卖额。这些也只是审计补充，未进入正式 MarketInput。

## 7. 5 日复盘格式

每 5 个交易日复盘固定为：

```text
现象
数据
原因假设
反例
最小修改建议
可能新增错误
后续验证计划
```

比较对象至少包含 `PV_OBSERVATION_V1`、`PV_FOCUS_PREVIEW_V0.1`、`THEME_CAPITAL_TREND_V1` 和 `STAGE_AWARE_MOMENTUM_V1`。统计按排除原因分组，记录 1/2/3/5/10 日均值、中位数、下行、极端错过/避开样本和题材集中。AI 只提出调整建议，不自动修改正式策略。

## 8. 日常命令

用户可直接对 Codex 说：

- “把下一个交易日的荐股清单给我。”
- “给我一份当前短线强势股和市场状态报告。”
- “为什么国芳集团当时没有进焦点？”
- “金健米业为什么被A过滤？”
- “哪些股票处于高位分歧，但修复很强？”
- “哪些股票属于趋势整理？”
- “最近一周没选上的股票是不是反而更强？”
- “比较原PV、旧焦点和阶段感知策略。”
- “给我策略调整建议，但不要改正式策略。”

每次输出必须标明观察日、目标交易日、数据日期、缺口、仍在跟踪的股票，并区分事实、规则计算、Codex 判断和未知。不得声称阶段感知策略已证明胜率更高。

## 9. 本次工程验证

验证日期：2026-09-07。

- `.venv/Scripts/python.exe -X utf8 -m pytest tests/r05_product/test_stage_aware_momentum_v1.py -q`：10 passed。
- `.venv/Scripts/python.exe -X utf8 -m pytest tests/r05_product/test_theme_capital_trend_v1.py tests/r05_product/test_codex_research.py tests/r05_product/test_strategy_experiments_and_tushare.py -q`：59 passed。
- `.venv/Scripts/python.exe -X utf8 -m pytest tests/r05_product -q`：156 passed, 1 skipped。
- `.venv/Scripts/python.exe -X utf8 -m pytest tests/yaogu -q`：115 passed。
- `.venv/Scripts/python.exe -X utf8 -m compileall -q stockfish_core scripts tests/r05_product`：通过。
- `git diff --check`：通过。
