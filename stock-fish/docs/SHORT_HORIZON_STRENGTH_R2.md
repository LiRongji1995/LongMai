# 短线强度、近期参与度与动态成交情境 R2

`SHORT_HORIZON_STRENGTH_R2`、规格 `SHORT_HORIZON_STRENGTH_R2_SPEC_2`、特征 `SHORT_HORIZON_PARTICIPATION_DYNAMIC_LIQUIDITY_V2`，在原 [R1](SHORT_HORIZON_STRENGTH_R1.md) 的三阶段精确公式和安全约束上新增近期参与度，并提供动态成交软参考。用户反馈后明确批准初始公式：**参考成交额 = 1 亿元 + 0.02% × 最近五个完成交易会话沪深四板 A 股 RAW 成交额中位数**。固定 5 亿元硬门槛的草稿已废弃，不用于本版本选择。

初始参数按向前使用假设登记为 `INITIAL_HYPOTHESIS_FROZEN_FOR_FORWARD_USE`，仍是“尚未验证”，`MARKET_EDGE=NOT_ESTABLISHED`。没有通过后续价格拟合参数，不为保留某个样本或凑数量设名称、行业例外。R1 代码、配置、旧运行与历史来源保持原身份；本版本不自动切换 V2、DAILY、配置激活、验证 epoch 或运行服务。

## 最近五个会话参与度

窗口为截至观察日、包含当日的最近五个交易所会话。至少一个会话具有合法且真实换手率 **大于等于 5%**，参与度 PASS；五个会话全部合法且都低于 5%，参与度 FAIL；其余 UNKNOWN。当日缩量或换手较低，可以因此前活跃会话通过。缺失不填零，已有合法活跃事实不会因其他会话缺失而消失。

每条记录通过原生 R1 的选定修订行情接口、PIT 解析器、证券身份与会话一致性、完整父来源和分页、VALID 质量以及已识别的换手口径。换手比例精确比较 `1/20`，不使用展示四舍五入。未识别口径、缺字段、迟于资料截止、来源不完整或质量不足的记录不贡献 PASS，也不算合法低换手。生产认可口径为 `BAOSTOCK_TURN_PERCENT_FLOAT_SHARES`、旧提供者的 `BAOSTOCK_FLOAT_SHARE_TURN_PERCENT` 和 `TUSHARE_TURNOVER_RATE_PERCENT`；TEST_FIXTURE 口径仅用于明确测试覆盖。认可口径不授权 PARTIAL 父来源。不能由成交额、流通市值或行业自行估算换手率。

## 动态成交软参考

令 `M_s` 为某会话沪深主板、创业板、科创板普通 A 股的当日 RAW 交易额。口径排除北交所、B 股、基金、债券、指数；当日完整日期化 `master_all` 身份与 `market_daily` RAW 行的 A 股代码集合必须完全相同，不能把主板资格池或部分 normalized input 加总当成全市场。所有交易状态明确、真实交易额有限且非负的交易证券金额以有理数精确加总。来源明确的非交易证券单独记录 `NONTRADING_EXCLUDED`，其空金额不填零；非交易状态与正交易金额矛盾时该日 UNKNOWN。

最近五个完成会话均须有合法完整分母，才能得到精确中位数和 `L = 100000000 + (1/5000) × median(M)`。例如中位数 1.4 万亿元时 `L=3.8亿元`。任何一天缺来源、范围不全、身份重复、RAW 基准不明、交易状态未知、交易金额缺失或实际捕获晚于资料截止，整个中位数 UNKNOWN；不使用已有四天的中位数、不补零、不复用今日成员回填旧日期。

接口 `_source_payload_reader(receipt_id) -> bytes` 只提供原始完整采集文件的字节。核心验证 SHA 等于父 receipt 的 `content_hash`，COMPLETE、分页、字段版本、行数、原 first_seen 时钟一致，再重新核算成员集合和金额。原 bundle 的 receipts 与可选 `_source_receipts`（或 reader 的 receipts）建立独立解析层；同一 receipt ID 不同内容或元数据直接拒绝。每个父来源实际四时钟均不得晚于新情境截止，历史重建声明不能把晚抓资料变成过去已取得。额外源不修改原输入或 input hash，所用及被考虑的 receipts、hash、逐日范围计数、原内容 hash、理由与情境 hash 一并保存，正式包装另将该源层纳入运行身份。

观察日个股金额独立核验当日选定 RAW 行的 PIT 身份、会话、完整父来源、VALID 质量以及 RAW OHLC/参考昨收正值与价格范围。`liquidity_status` 为 AVAILABLE 或 UNKNOWN，`liquidity_position` 为高于、等于、低于参考线或 UNKNOWN。保留精确当前金额、参考线、比值和展示用软分 `100×金额/(金额+参考线)`；该分值仅便于解释容量位置，不是收益概率或预测排名。**低于参考线和 UNKNOWN 均不剔除研究匹配；`liquidity_selection_gate=false`。** 原 R1 三阶段已有成交门槛及相对放量 A 仍按原配置执行。

R2 研究选择仅为原 R1 观察匹配与近期参与度 PASS 的交集。原 R1 UNKNOWN/REJECTED 不会升级，原 R1 已知收盘限价下跌反证继续拒绝。每行保留三阶段 `matched_arms`、`primary_stage`、原 `r1_decision_status`/`r1_research_selected`，新增参与度条件、成交情境与逐会话证据，兼匹配多模型不删。题材演化由独立 [情境模块](THEME_EVOLUTION_R1.md) 提供解释与概念展示，不作 R2 硬筛；证据不足不伪装为热点、游资或板块资金流入。

全资格池和证券身份保留，仅按研究匹配、证券代码稳定展示，不作 top-N 截断或收益排名。正式 FOCUS、CORE_PASS 始终为零，修复与可成交性仍未确认，题材资金与资金席位证据不能由价格形态自行推定。

## 原生计算与已验证冻结结果

常规 `run_short_horizon_strength` 总是执行原生 R1 collector/evaluator，再用同一 context 采集参与度和新成交情境；保留原 history store 能力绑定、ref/hash/manifest 断言。原生父来源标为 `IN_MEMORY_NATIVE_R1_RESULT_NOT_SAVED_RUN`，不会伪装已保存父运行。

冻结模式必须提供完整原生 `base_run` 及 `base_provenance`：`mode=VERIFIED_FROZEN_R1_INLINE_STYLE`、保存的 `base_run_id`、完整原运行 canonical `base_run_content_hash`、归档文件 `base_run_sha256`、原内层 R1 结果 canonical `base_result_hash`、`base_source_hash`、原输入 `input_id`/`input_hash`、观察日与截止。核心核验整份父运行 canonical hash、原生 stable run ID、内层结果、源/配置/规格身份、输入、资格全集、原选择 ID 与历史 ref/hash；外层保存运行认证器必须实际验证归档文件 byte SHA，核心不声称重读文件。

`prepare_inline_style_context` 仅为新增特征建立 `ONLY_ORIGINAL_INPUT_INLINE_BARS_AND_RECEIPTS` 视图：原输入 hash/ref/hash 不变，视图不读历史 DB、不重新计算 R1 或市场 RS、不授予原历史来源新权限。冻结父是已认证的原生权威层，新增参与度只由原输入 inline 行和 receipts 判定。inline 缺失按三值规则保留 UNKNOWN 或已证 PASS，不补史。输出明确 `original_r1_history_database_reverified_in_this_application=false`；原生 R1 不能使用该视图绕过历史断言。

新情境截止可以晚于父截止，但原父先按原截止验证，原完整输入、观察日、资格与最后 25 个会话的 inline 选定价格证据必须不变；较早新截止、后变资格或后来可见原价格修订均拒绝。结果分别保留 `base_decision_cutoff` 与 `new_context_cutoff`。旧盘前复原沿用原截止，晚收到的新来源不可用；新截止仅用于明确的新情境研究，不把新资料伪装为旧运行。历史回算采用后来制定的规则，不能声称当时已有 R2 预测名单。

新增过程中不修改原 R1 结果或输入；未改动的大型 R1 数值层只读复用，调用者不得修改共享嵌套映射。修改字段用独立容器，`items` 为全池逐证券诊断唯一位置。来源身份包含原 R1 依赖、新模块、配置、规格、路由、情境来源包装与题材模块，正式依赖缺失会明确失败。保存、详情和导出沿用现有实验体系，不建独立数据库。
