# THEME_CAPITAL_TREND_V1 版本说明与首轮审计

本文件对应上一版执行规格《StockRecommendation：板块—资金—多头趋势策略与落选复盘》，此前《板块资金趋势筛选与落选反思》不合并执行。2026-09-07 后新增的最新研究框架为 [STAGE_AWARE_MOMENTUM_V1](STAGE_AWARE_MOMENTUM_V1.md)；本策略作为历史独立研究臂和对照保留，不与阶段感知规格合并执行。旧 DAILY、PV、焦点、DNA、THEME 与历史结果保留；两只指定股票是否入选不作为验收标准。

## 1. 用户硬性要求

- 只面向日期化合格的沪深主板 A 股；ST、停牌、退市整理、非主板或非 A 股不进入正式名单。
- 没有板块/题材联动证据时，不给新版正式名单；没有活跃板块资金证据时，也不给新版正式名单。
- 观察日成交额必须 `>= 500,000,000` 元；观察日普通换手率必须 `>= 7%`。
- 均线必须多头排列；收盘低于半年线或年线时排除。
- 允许围绕 MA5/MA10/MA20 的 1 到 3 日短回踩。
- 正式名单不固定只数，可以为 0。
- 落选、退出、坏样本仍记录、复盘和比较。
- 所有条件使用 `PASS / FAIL / UNKNOWN`；`UNKNOWN` 不当中性分、不填补，正式名单要求所有必需条件为 `PASS`。

## 2. 本版初始研究参数

以下参数写入 [theme_capital_trend_v1.yaml](../config/theme_capital_trend_v1.yaml)，状态为 **尚未验证**。它们是为了落地硬性要求的可追踪初始研究参数，不是用户原有规则，也不是已验证最优条件。

- 均线：MA5/10/20/60/120/250，统一使用同一可比价格口径。
- 多头排列种子条件：`MA5 > MA10 > MA20 > MA60 > MA120 > MA250`，且 `C >= MA60/MA120/MA250`。
- 趋势运行：满足公共条件且 `C >= MA5`。
- 回踩形态：`C < MA5`，连续低于 MA5 的交易日为 1、2 或 3；距离 MA5/10/20 最近一条线不超过 3%；回踩段内排列不断裂，收盘不低于 MA60/120/250，且不低于 `MA20 * 0.97`。
- 题材联动：同题材剔除自身后至少 5 个有效成员；5 日收益覆盖率至少 80%；同题材 peer 5 日收益中位数大于 0、上涨比例至少 60%、至少 2 个 peer 5 日涨幅不低于 10%、peer 中位数不低于主板中位数。
- 资金证据：最近 5 个交易日、同一题材、剔除候选自身；至少 2 个其他成员有供应商分类的活跃资金/席位事件；去重买入金额至少 1 亿元、净额为正、单席位买入至少 2000 万元，且最近 3 个交易日内有活动。仅代理资金流时标记资金待核验，不算 `PASS`。
- 排序：不引入复杂新分数；优先复用 PV 连续参考分，但不使用旧 PV 的 A/R 入围门槛和旧焦点门槛做预筛。最终按参考分降序、证券代码升序。

## 3. 已实现入口

- 新配置：`THEME_CAPITAL_TREND_V1`，见 [theme_capital_trend_v1.yaml](../config/theme_capital_trend_v1.yaml)。
- 新代码：见 [theme_capital_trend.py](../stockfish_core/yaogu/theme_capital_trend.py)。
- 实验结果新增三个研究臂：
  - `THEME_CAPITAL_TREND_TECHNICAL_ONLY_V1`：仅技术硬条件诊断，不是正式名单。
  - `THEME_CAPITAL_TREND_V1`：正式新版板块、资金、多头趋势名单。
  - `SIMPLE_TREND_TEMPLATE_REFERENCE_V1`：外部趋势模板参考臂，只作比较。
- API/导出沿用现有实验保存体系；旧 `PV_OBSERVATION_V1` 和 `PV_FOCUS_PREVIEW_V0.1` 不改名、不重算历史含义。
- 真实采集脚本兼容当前 PV 字段 `filter_passed` 与旧报告字段 `ranked`，避免完成采集后写报告时失败。

## 4. 首轮真实输入审计

审计时间：2026-09-07。输入为本机已保存的 2026-09-04 PV 观察日数据，路径在被 Git 忽略的 `reports/codex/pv_observation_20260904_rebuilt_20260907T091708/`。该目录不提交远端；本文只记录聚合结果和两只个股的脱敏规则路径。

数据范围事实：

- 观察日：2026-09-04。
- 合格主板证券：3043。
- 旧 PV 可计算因子：3039，缺 4。
- 旧 PV 入围：525；旧焦点：17。
- 新版输入缺口：当前保存输入只有 PV 所需近 6 个交易日价格；没有可授权题材快照；没有可授权资金事件。

新版首轮结果：

| 研究臂 | 状态 | PASS | FAIL | UNKNOWN | 主要缺口 |
|---|---:|---:|---:|---:|---|
| `THEME_CAPITAL_TREND_TECHNICAL_ONLY_V1` | `INSUFFICIENT_EVIDENCE` | 0 | 2844 | 199 | MA10/20/60/120/250 窗口不足，无法确认形态 |
| `THEME_CAPITAL_TREND_V1` | `INSUFFICIENT_EVIDENCE` | 0 | 2844 | 199 | 技术长窗不足，题材与资金证据缺失 |
| `SIMPLE_TREND_TEMPLATE_REFERENCE_V1` | `INSUFFICIENT_EVIDENCE` | 0 | 0 | 3043 | MA50/150/200 与 MA200[t-20] 不足 |

同一输入上，观察日成交额 `>=5亿` 的合格股为 604 只，换手率 `>=7%` 的合格股为 294 只。由于均线长窗、题材和资金证据不足，新版正式名单为 0，不是合法证明“没有符合股票”，而是当前证据不足。

## 5. 国芳集团与金健米业路径

### 国芳集团 `601086.SH`

事实与旧规则计算：

- 日期化资格：2026-09-04 合格主板 A 股，上市交易会话数 535。
- 旧 PV：入围且排名第 1，`S_PV=99.238095`，`A=5.306857`，`R=0.516787`，`H=0.199017`，成交额约 15.50 亿元。
- 旧焦点：未入选，原因是 `CLOSE_LOCATION_BELOW_0_70`；保存值 `close_location=0.683128`，低于旧焦点 0.70 门槛。
- 持续跟踪：旧 pilot tracking 只对焦点形成正式 episode；国芳集团未进旧焦点，因此不形成旧焦点活跃 episode，但仍保留在旧 PV 全名单和新版诊断/复盘 ledger 中。

新版计算与未知：

- 新版成交额与换手硬条件为 `PASS`。
- MA10/20/60/120/250 均线窗口在当前输入中不足，技术形态为 `UNKNOWN`。
- 题材联动与同题材活跃资金证据均为 `UNKNOWN`。
- 因必需条件未全 `PASS`，不能进入 `THEME_CAPITAL_TREND_V1` 正式名单。
- 旧输入的 `close_limit_up` 为 `UNKNOWN`，无法验证用户提到的“反包涨停”是盘中触板还是收盘封板；也不能使用 2026-09-07 收盘后数据解释 2026-09-07 盘前判断。

### 金健米业 `600127.SH`

事实与旧规则计算：

- 日期化资格：2026-09-04 合格主板 A 股，上市交易会话数 535。
- 旧 PV 因子可计算：`A=1.031010`，`R=0.045030`，`H=0.127861`，成交额约 32.18 亿元。
- 旧 PV 未入围，明确拒绝原因：`PV_AMOUNT_RATIO_BELOW_1_2`。也因此没有旧焦点记录。
- 旧 PV 连续参考分按本版独立重算可得 `76.944719`，参考排名 400；这不是旧 PV 入围，只是新策略排序参考。

新版计算与未知：

- 新版成交额与换手硬条件为 `PASS`。
- MA10/20/60/120/250 均线窗口不足，技术形态为 `UNKNOWN`。
- 题材联动与同题材活跃资金证据均为 `UNKNOWN`。
- 因必需条件未全 `PASS`，不能进入 `THEME_CAPITAL_TREND_V1` 正式名单。

## 6. 复盘 ledger 与比较口径

新版结果把未通过的证券作为诊断行保留在 JSON 导出的 `coverage_diagnostics` / `diagnostics` 中；有 rank 的行才是对应研究臂的实际名单。后验和跟踪仍沿用现有保存 run、同一观察日、同一输入、同一交易会话成熟度的口径。

后续有合格长窗价格、题材和资金输入后，比较固定为：

| 代号 | 研究臂 | 用途 |
|---|---|---|
| A | `PV_OBSERVATION_V1` | 旧 PV 全名单 |
| B | `PV_FOCUS_PREVIEW_V0.1` | 旧焦点 |
| C | `THEME_CAPITAL_TREND_TECHNICAL_ONLY_V1` | 技术硬条件诊断 |
| D | `THEME_CAPITAL_TREND_V1` | 新版正式名单 |
| E | `SIMPLE_TREND_TEMPLATE_REFERENCE_V1` | 外部趋势模板参考 |

第一阶段先使用最近 5 个已完成交易日；若本地已有输入和预算允许，再扩展到 20 个交易日。AI 复盘只给调整建议，不自动修改正式策略。

## 7. Codex 日常请求短语

用户可直接说：

- “按新要求给我下一交易日名单。”
- “为什么国芳集团/金健米业没有入围？”
- “复盘最近一周：没选上的是否更好？”
- “比较原PV、旧焦点、新策略和趋势参考。”
- “给我策略调整建议，但不要修改正式策略。”

Codex 需要在回复中写明观察日、目标交易日、数据缺口、仍在跟踪的股票、事实/规则计算/判断/未知四类边界。无合格数据时不改用旧策略冒充新版正式名单。

## 8. 外部参考与差异

访问日期均为 2026-09-07。

- [myhhub/stock](https://github.com/myhhub/stock)：开源 A 股数据、指标、形态、选股、回测工具；本项目只作为来源与复现边界参考，不复制代码，不把其选股逻辑作为已验证规则。
- [tkfy920/qstock](https://github.com/tkfy920/qstock)：个人量化投研分析包，覆盖数据、可视化、选股、回测模块；本项目不依赖其运行时，不把数据接口返回视为无需时点校验。
- [Qlib Workflow](https://qlib.readthedocs.io/en/latest/component/workflow.html) 与 [Qlib Recorder](https://qlib.readthedocs.io/en/v0.9.7/component/recorder.html)：用于对照实验配置、记录和 artifact 管理理念；本策略仍保留 StockFish 自身的不可变输入、source hash 与保存 run。
- [Freqtrade lookahead-analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/)：用于提醒回测/重放中未来函数风险；本项目继续要求 cutoff、可见性和后验成熟度分离。
- [Tushare Pro 文档](https://tushare.pro/document/2?doc_id=106)：用于公开接口字段和交易日期纪律参考；本项目不提交 Token，也不把权限不可得时的数据缺口补成中性。
- [AKShare 股票数据文档](https://akshare.akfamily.xyz/data/stock/stock.html)：用于公开数据接口目录参考，特别是龙虎榜相关接口；本项目仍以本地可追溯回执、可见时点和来源完整性为准。

## 9. 本次工程验证

验证日期：2026-09-07。

- `.venv/Scripts/python.exe -X utf8 -m pytest tests/r05_product/test_theme_capital_trend_v1.py tests/r05_product/test_r05_spec_goldens.py tests/r05_product/test_strategy_experiments_and_tushare.py tests/r05_product/test_codex_research.py -q`：75 passed。
- `.venv/Scripts/python.exe -X utf8 -m pytest tests/r05_product -q`：146 passed, 1 skipped。
- `.venv/Scripts/python.exe -X utf8 -m pytest tests/yaogu -q`：115 passed。

覆盖重点包括：成交额/换手边界、MA250 缺口不填补、1 到 3 日回踩与 4 日失败、题材 peer 剔除自身、同题材资金不能跨题材混用、代理资金保持 `UNKNOWN`、重叠资金窗口不重复汇总、旧 PV A/R 拒绝不阻止新版独立评估、实验保存新增研究臂、Codex profile 导出，以及旧 R05 V4 PV 只读兼容。
