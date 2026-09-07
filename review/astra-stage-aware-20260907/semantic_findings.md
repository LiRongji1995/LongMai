# 阶段语义独立子审阅（2026-09-07）

权威仓库：`D:\stockfish-qlib-quant-system`；主审确认版本：`main` / `a9c6deec75319ce0fb2d6fabca0a4c6021323c05`。本子审阅只读生产源码和配置，不执行 Git 操作、不采集真实行情、不修改正式策略。证据脚本与结果仅写入本目录 `semantic_*` 文件。已读根目录/业务目录 AGENTS、DEPLOYMENT、两级工程基线、阶段说明、阶段/旧 PV/板块资金趋势源码及相关测试。

## 结论

**策略语义 PARTIAL。** A、CLV、收阳和 C≥MA5 已不再是新策略的统一入口门槛；四类判断不是四个放量阈值标签，具有不同输入结构。与此同时，START 缺乏“初次/突破”定义，ACCELERATION 能仅凭高换手和任意一个历史 RS 窗口通过，HIGH_LEVEL_DIVERGENCE 允许当前零修复收于跌停，仅用 5 日相对表现补足“修复”。健康的温和多头、当天高流动性、A≈1、尚未跌破 MA5 的状态可完全落在四阶段之外。不能把当前阶段标签直接解释成已确认的生命周期事实。

正确性优先项为 5 日换手错误算入第 6 日、缺限价却报告市场 LOW/0、不完整市场基准标 COMPLETE，以及阶段三值逻辑把缺失的可满足证据分支压成 FAIL。它们应先修正，再讨论阶段参数是否有效；本审阅未修复。

`P0` 在主任务中指正确性类别；此处不表示下单系统的事故等级，系统仍是人工研究工具。

## 实际执行与可复现证据

- **ACTUAL_EXECUTION**：在 `stock-fish` 工作目录运行 `.venv\Scripts\python.exe -B -X utf8 -m pytest tests/r05_product/test_stage_aware_momentum_v1.py -q -p no:cacheprovider`，退出码 **0**，**10 passed in 4.58s**。原测试涵盖加法式集成、旧结果一致、HLD 不限 A/CLV、阴线整理、缺 MA250、固定市场基准、剔除本股主题、流动性、资金等级与追加未来 bars。
- **SYNTHETIC_COUNTEREXAMPLE / ACTUAL_EXECUTION**：`.venv\Scripts\python.exe -B -X utf8 ..\review\astra-stage-aware-20260907\semantic_counterexamples.py`，最终退出码 **0**，**14 组证据断言全部完成**，耗时约 3.2 秒。结果为 [semantic_counterexamples.json](semantic_counterexamples.json)，脚本为 [semantic_counterexamples.py](semantic_counterexamples.py)。这些断言确认观测行为，**不表示错误行为获认可**。
- 两命令都显式设置 `STOCKFISH_DB_PATH=<本目录>\semantic_runtime\isolated.sqlite3`、`STOCKFISH_STATE_DIR=<本目录>\semantic_runtime\state`、`PYTHONDONTWRITEBYTECODE=1`、`PYTHONUTF8=1`。输入源是仓库现有 `TEST_FIXTURE`，没有实际创建/调用生产数据库；无网络调用。
- **NOT_VERIFIED**：本子任务未重放真实市场文件，真实发生率、对收益及落选分组的影响不由这些合成结果推断。金健与国芳由主审独立重算与引用真实证据。

| 证据 JSON case | 本轮观测 | 判断 |
|---|---|---|
| `hld_bypasses_old_A_CLV` | A<1.2、CLV<0.7 的 HLD 正式 PASS | 已摆脱全局旧门槛 |
| `sixth_day_turnover_changes_formal_inclusion` | 最近五日仅 2 日高换手；仅改变窗口外第 6 日，报告值 2→3，ACCEL 和正式名单 FAIL→PASS | P0：影响选择的窗口错误 |
| `all_limit_events_unknown_becomes_market_LOW` | 所有涨停事件 UNKNOWN，市场涨停数 0、状态 LOW | P0：未知压为否定 |
| `benchmark_12point5pct_coverage_marked_COMPLETE` | 8 只合格股只有 1 只有当日价格，基准 coverage=0.125、status=COMPLETE | P0：覆盖状态错误及潜在不代表性 |
| `zero_recovery_close_at_limit_down_passes_HLD` | 当日 -10%、CLV=0、收盘=最低点、修复=0；5 日收益+12.5%，HLD 正式 PASS | P1：历史 RS 替代当日修复 |
| `positive_range_without_underwater_low_passes_HLD` | 当日最低=昨收、全天无深水，修复指标+7.5pp，HLD PASS | 指标并非专指深水修复；需要区分解释 |
| `start_only_A_and_tiny_positive_ret5` | A=1.2、5 日收益+0.01% 即 START PASS | P1：无初次/突破语义 |
| `fourth_below_MA5_day_rejected` | 完整交易日历下连续第 4 日 C<MA5，TREND_PULLBACK FAIL | 现有 1–3 日计数正确 |
| `known_session_missing_bar_is_UNKNOWN` | 日历含当日但个股 bar 缺失，TREND_PULLBACK UNKNOWN | 正确保留缺失 |
| `missing_whole_session_compresses_four_to_three` | 同一段中的一天同时从 sessions/bars 消失，4→3 日，正式 TREND_PULLBACK PASS | 条件性日历边界风险；需上游保证完整交易所日历 |
| `stage_gap_and_overlap_diagnostics` | 六条均线严格多头、C>MA5、A=1、当日换手8%、1/5日正收益且 RS PASS，仍四阶段均 FAIL；另保留 HLD/ACCEL 重叠 | P1：健康趋势运行空洞；重叠本身可接受 |
| `ret3_9point999_to_10point001_changes_primary` | 3 日收益仅从 9.999% 变为 10.001%，主状态 START→ACCELERATION | 边界敏感；未证明真实市场频繁抖动 |
| `partial_missing_stage_OR_evidence_is_FALSE` | HLD 的 ret10/percentile 缺失或 ACCEL 的 RS/限价缺失，可补足门槛的分支仍被判 FAIL | P0：三值逻辑失真 |
| `unqualified_sector_numeric_RS_supports_repair` | 主题 status=UNKNOWN，只要有某个窗口 RS>0 即可支持修复 | P0/P1：阶段证据质量未联动 |

## 实际决策流程与旧门槛残留

**STATIC_REVIEW**，`stock-fish/stockfish_core/yaogu/stage_aware_momentum.py:976` 起：从 `_prepare_context` 已日期化合格池 `ctx.eligible_ids` 开始，独立计算价格收益、RAW 日内路径、流动性、均线、市场地位、市场 RS、全部可见主题及同主题资金，再执行全部四阶段。新策略没有先跑旧 PV A/R 过滤。

正式名单要求四组条件全部 PASS：流动性底线、至少一阶段、板块链接、资金链接（`stage_aware_momentum.py:993`–1011）。流动性为成交额 ≥5亿元且换手率≥7%（350–366）；板块至少一个可见主题有≥2个其他合格成员，并且任一1/3/5日窗口收益覆盖≥60%（523–613）；资金要求至少一个合格主题为 STRONG 或 MODERATE。板块价格上涨本身不是正式门槛。

旧硬条件位置与用途明确如下：

| 条件 | 当前作用 | 证据 |
|---|---|---|
| A≥1.2、ret5>0 | START 条件；也仍是独立旧 PV 规则 | SAM:813–828；strategy_experiments.py:257–258 |
| A≥2、R≥10%、C≥O、CLV≥0.7 | 独立旧焦点规则，未在新策略全局使用 | strategy_experiments.py:278–301 |
| C≥MA5 | 新版没有此统一硬门槛，整理反而要求 C<MA5 连续1–3日 | SAM:426–436、913–935 |
| A、R、H | **仍控制新策略主要排序**：0.45/0.35/0.20 的旧 PV 连续百分位分；未做旧 A/R筛选 | theme_capital_trend.py:581–598；strategy_experiments.py:241–247、266；SAM:959–965 |
| 主阶段顺序 | 选主状态：HLD→ACCEL→PULLBACK→START；在名单排序中仅同 PV 分时生效 | SAM:946–965 |
| top_n 默认100 | 仅截 `top_ids` 展示；全部合格项保存在 qualified_ids/items | SAM:1094–1096 |
| max_count、rank_window | 旧焦点 max_count 已弃用、不限入选数量，rank_window=None | strategy_experiments.py:278–301 |

因此“旧模板问题已全部解决”不成立：**旧资格门槛已移除，旧排序偏好仍然完整保留。** 旧 PV 百分位的分母还由“PV通过股”变为“全部可算股”，同样的 reference_score 不应直接等同旧 PV 原 run 分数。

## 四阶段各自含义、空洞和重叠

**START，PARTIAL。** 只有 A≥1.2 与 ret5>0，不计算首次入场、近期平台突破、第一次涨停、RS突然改善或启动之前状态。更准确的代码解释是“相对放量且五日上涨”。它与旧 PV 入围语义几乎相同（但不继承完整六日 RAW 验证的所有条件）；不是从旧列表选入，形式上的阶段独立已实现。

**ACCELERATION，PARTIAL。** 三/五/十日收益分别≥10%/15%/25%、最近高换手至少3日、当日涨停、任一1/3/5日市场RS>0，共六类点中满足两点（831–857）。收益点高度相关，且高换手与任一旧窗口相对收益即可过关，不要求价格加速、连涨、连板、当日相对强势或板块扩散。它与 START 有真正不同的输入语义，但标签过强。六日计数错误会直接扩大入选。市场高度/龙虎榜连续性没有直接进入该判定。

**HIGH_LEVEL_DIVERGENCE，PARTIAL。** “高位”来自 ret5≥10%、ret10≥18%、10日市场收益百分位≥80、或当日连板下界非零中的任一；“分歧”来自振幅≥7%、最低收益≤-4%、或触及跌停任一；“修复”来自从低至收≥5pp、从水下收红、任意1/3/5日市场RS>0、任意主题/窗口RS>0任一（860–910）。高位结构、分歧、修复三段确实不同于静态 K 线，但当日收于跌停仍能凭 5日RS 成立，已经实证标签过宽。字段所支持的是“近期强势中的大波动/相对韧性候选”，不能自动改写为“当日承接很好”。

**TREND_PULLBACK，PARTIAL，核心均线与计数较扎实。** MA5>10>20>60>120>250 严格多头，C≥MA120/250；C<MA5 连续1–3个输入交易日，距MA5/10/20任一≤3%（398–451、913–935）。阴线和 A低均不阻断；MA250不足为 UNKNOWN；只要 day 存在，窗口缺bar也UNKNOWN；均线要求全部窗口同一 comparable_basis（theme_capital_trend.py:127–137）。但并没有要求市场/板块RS有韧性，短均线严格多头也可能先于 1–3日调整条件否定正常趋势。第4日不会错误当3日，除非输入日历本身丢了整天。

健康多头空洞反例：C=23.95，MA5/10/20/60/120/250 为23.85/23.725/23.475/22.475/20.975/17.725，A=1，成交6亿元、当日换手8%、前5日换手1%，ret1=+0.2092%、ret5=+1.0549%、RS PASS；没有启动放量、没有加速两点、没有分歧、没有跌破MA5，正式 FAIL。此为“趋势运行未覆盖”的可复算事实，**不能直接推断该股值得选择**，也不建议为救该样本立刻加第五阶段。

重叠并非必然错误：HLD与ACCEL/START共存符合连续市场形态。保存有 primary_stage、alternate_stages、四阶段的 status/reason/evidence（939–955）。然而主次按固定顺序，没有转移历史、持有前一状态的机制、阶段距离或置信度；只保存了**条件与缺失信息**，没有概率意义上的不确定性。轻微阈值变化可切换主状态，本次只做边界反例，未做历史跳变频率或自动调参。

## RS、修复、板块与市场地位

**固定市场基准已实现。** 主基准是当前观察日合格股中每个窗口可算股的等权价格收益；个股同窗口收益减此均值（232–282）。没有选择表现最差指数作为比较对象。SSE/CSI300 目前是固定 UNKNOWN 占位，未实现真实 index bars 消费。各窗口基准成分可因可算性变化，并且只要有1只可算就 COMPLETE；相对强度用于入选却不检查市场基准覆盖。`RS status=PASS` 表示任意1/3/5日之一为正，**不表示当天或持续增强**。

**板块为机械计算，不依赖 AI。** 按 theme_id 排全部可见主题，统计剔除本股的 peers，全部主题/窗口保留；主展示主题按字典序稳定选择（523–613）。但阶段支持使用“任意主题任意窗口RS为正”，并且不检查该主题 status（804–809）。这是预先固定的 OR，不能称为历史事后人工挑题材；仍存在多主题多窗口带来的宽松匹配与显示主主题/实际支持主题不一致。板块日状态按当日 peer平均与上涨比例分 BROAD_STRENGTH/DIVERGENCE/FADE/REPAIR；没有时间序列阶段识别，不能分别证明板块首次启动、持续加速、高位分化或修复先后次序。

能够形式上表达“板块跌、核心抗跌/修复”：板块均值≤0且个股更强标 DIVERGENCE，HLD不要求板块涨，深水修复连续指标入库。无法证明“核心”身份、率先修复的日内先后、持续资金承接，且 day_state=FADE 本身也不阻断正式名单。**有板块链接及可算 peers ≠ 有板块效应**，这是用户理念与实现的重要偏差。

修复定义准确保存为 `recovery_points=(C-L)/prev_close`，low-to-close另存`C/L-1`，振幅为`(H-L)/prev_close`，CLV为`(C-L)/(H-L)`（285–339）。数学上 `recovery_points = amplitude × CLV`，并非独立的日内时序证据；高振幅天然可得高修复点，全天普通上行也可满足，OHLC无法确认先高后低还是先低后高。低点收益连续值能区分深水，但当前 repair OR不强制深水。推荐保留连续值与理由，先纠正标签与证据质量，禁止搜索“最佳恢复阈值”。

| 市场地位字段 | 当前分类 | 限制 |
|---|---|---|
| 当日涨停/触板/炸板、连板长度及下界 | A：已有可复算结构；真实可靠性依赖限价证据 | 未知事件本身保留，但汇总计数丢失未知 |
| 10/20日收益与全市场百分位 | A：同口径时可复算 | 窗口不足为null；非板块内排名 |
| 近5日高换手计数 | 存在P0窗口错误，修后归B | 当前实际算6日；只是次数，并非连续天数 |
| 市场HIGH/MEDIUM/LOW | B：参数未验证且未知处理错误 | 50/15涨停或5/3板为源码硬编码；全市场状态非个股地位 |
| “高位核心”“高辨识度”“承接” | C：可辅助解释，不是已结构化事实 | 不可拿自然语言顶替数据条件 |
| 近期10/20日涨停次数、断板/反包事件、持续龙虎榜、板块内位次、日内修复先后 | D：此阶段模块没有可靠实现/输入证据 | 不因数据schema其他模块可能存在字段就宣称本模块使用 |

## 复杂度与维护边界

**STATIC_REVIEW / ACTUAL_EXECUTION**：配置递归数值 leaf 为 **63**，其中 `research_seed_parameters` 为 **59**；完整路径和值在证据 JSON。该计数包括窗口列表、重复数值、版本号、复盘周期，**不是63个独立可优化参数，也不是统计自由度估计**。源码另有市场HIGH/MEDIUM/LOW的50/15/5/3硬编码及板块日上涨比例3/5，未包含在YAML数值计数中。

复杂度暂未表现为模型堆砌：固定规则、分数沿用旧 PV、无 AI调参、无新训练。但四阶段内部的大量 OR、重复收益窗口、跨主题择一、资金门槛与共享历史函数使逻辑交互复杂；代码配置里“尚未验证”标注充分，不代表参数可维护性和方法已验证。当前风险主要是语义/缺失逻辑，而非需要引入更复杂模型。

- **5.5足够**：已确定字段的输出/解释、资料导航、轻量JSON/CSV、明确公式的普通维护。
- **5.5 + 独立反例测试**：修复窗口计数、UNKNOWN传播、基准覆盖、交易日历、均线、阶段条件和排序；复用本轮反例但不要把观测错误断言当新验收标准。
- **再次Astra独立审阅**：阶段本体与修复语义改变、是否引入TREND_RUN、市场/板块/资金如何共同决定名单、评价目标或AI决定权变化。

**不建议现在做**：凭国芳/金健两个赢家改门槛，寻找收益最优的recovery阈值，增加复杂阶段评分，把主阶段改写成上涨概率。当前整体是否选择A/B/C由主审合并真实数据、时点和评价证据决定；本子审阅不反对“C，暂不建议将当前正式新策略投入日常使用”，仍可保留带UNKNOWN和反例警示的研究诊断。
