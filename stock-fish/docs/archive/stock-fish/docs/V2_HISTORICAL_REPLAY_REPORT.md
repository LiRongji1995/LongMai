> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../README.md)查找。

# V2 Historical Replay Report — R12 最终来源续采后（2026-09-08）

**截至 17:15:48 Asia/Shanghai：真实 20 日尝试，1 日构造，0 日可评价。** 本次使用 17:13 完成的真实有界采集后的最新归档，重新调用最终 replay 脚本；没有以先前 16:36 结果代替续采后的验证。`MARKET_EDGE=NOT_ESTABLISHED`。

| 最新指标 | 实际结果 |
|---|---:|
| 尝试范围 | 2026-08-12 至 2026-09-08，20 日 |
| 来源构造 / 工程失败 | 1 / 0 |
| PRICE_TREND / FULL_CONTEXT 可评价日 | 0 / 0 |
| 唯一构造日 eligible / Focus / Watch / Rejected / Unknown | 0 / 0 / 0 / 0 / 0 |
| 9/8 暖机成功回执 / 非空会话 | 68 / 67，所需 255 |
| 暖机无成功回执 / 空回执 | 187 / 1 |
| 缺对应日期 master 的日期 | 19/20 |
| 缺对应日期 RAW 回执的日期 | 17/20 |
| 9/8 master / RAW 非空可用 | false / false；成功空回执存在 |
| Comparable source 缺口 | 1,501 个 NO_COMPARABLE_SOURCE |
| 构造后 daily input / history manifest | 1,882,171 / 165,327 bytes |

新增非空历史由 26 日增加至 67 日，但仍不是连续 255 日窗口。最新 source_contract 明确区分 `*_capture_present` 与 `*_available_nonempty`；空回执不再称为 available。该空 scope 仍允许保存真实诊断输入，所以 `source_constructed_count=1` 与 `evaluable_signal_day_count=0` 同时成立。MA20、MA60、MA120、MA250 的可评价分母仍为 0，observed=0、coverage=null、状态 UNKNOWN；不能展示成合法空选或 0% 覆盖。

最新研究 run：`historical_replay_34b5011cb23986c1857a547dc675d266f6ee00667948d1c3cf342b051b4c5679`。Input：`market_270929a86ab141c5c7a166aa04f8a5a7190cf3b3d664fed9709f61c76e01b19b`。History hash：`1707beb22c4393d2dffc97b02367ce688a08e15ece2f569ec243122b5070b212`。覆盖类型保持 `RECONSTRUCTED_HISTORICAL`，官方 capture/countable flag 均为 false。

本次运行耗时 17.679 s。仓库根目录的本机完整证据为 `reports/codex/2026-09-08/r12_integrity_history/replay-after-capture/V2_HISTORICAL_REPLAY_REPORT.json`、`attempts.json` 及日期子目录下的来源审计、输入和研究 run。响应存在性、非空可用性、成功响应和非空暖机分离的最终回归：22 passed，0 failed，0 skipped，2.39 s。真实采集内存及最终队列数见 [V2 Data Readiness](V2_DATA_READINESS.md)。

## 16:36 首次尝试快照（保留历史上下文，以下不代表最终来源状态）


本页以 R12 的真实来源重构结果为准：最近 **20 个交易日尝试，1 日构造，0 日可评价**。这是缺数据的研究记录，不是策略合法空选，也不是已经完成可评价历史回放。`MARKET_EDGE=NOT_ESTABLISHED`。

## R12 实际执行与范围

冻结来源快照的报告生成于 `2026-09-08T16:36:17.912199+08:00`，观察范围为 `2026-08-12` 至 `2026-09-08`。执行入口为 [r12_historical_replay.py](../../../../scripts/r12_historical_replay.py)，使用现有权威 SQLite DB 和已归档、逐字节校验的 provider evidence；本次没有网络采集或创建第二套数据库。

| 指标 | 实际结果 |
|---|---:|
| 尝试交易日 | 20 |
| 来源构造日 | 1 |
| 因日期化来源合同阻塞的日期 | 19 |
| `PRICE_TREND_REPLAY_SCOPE` 可评价日 | 0 |
| `FULL_CONTEXT_REPLAY_SCOPE` 可评价日 | 0 |
| 工程异常失败 | 0 |
| 构造日 eligible / Focus / Watch / Rejected / Unknown | 0 / 0 / 0 / 0 / 0 |
| 可计入正式 prospective 的日期 | 0 |

`2026-09-08` 生成了 `RECONSTRUCTED_HISTORICAL` 输入和研究 run，数据状态为 `INSUFFICIENT_IDENTITY`，当日 RAW 回执也为空。Source construction 表示输入及来源审计被真实保存；不代表存在非空可评价 universe 或完整趋势窗口。其官方 capture/countable flag 均为 false。

## 具体来源合同阻塞

- 19/20 日没有成功的对应日期 `master_all` 归档。
- 17/20 日没有成功的对应日期 `market_daily` 回执；另有 `2026-09-08` 的 12:07 成功回执为零行。因此只有 2/20 日有非空 RAW 行（8/26、9/2），不能把 3 份成功回执当成 3 日价格覆盖。
- 唯一具有当天 master 请求成功回执的 `2026-09-08`，11:52 和 12:02 的回执均为 **零行**。零行响应不能证明完整合法的空 universe；16:11 的后续请求为抓取失败。
- 20/20 日均缺连续核心价格历史。`2026-09-08` 所需 MA250＋斜率暖机为 255 个交易日，只有 27 日成功回执，其中当日为零行，实际非空只有 26 日；228 日无成功回执，另 1 日空回执；“请求了窗口”不等于窗口已覆盖。
- 当日重构审计记录 1,501 个 `NO_COMPARABLE_SOURCE` 缺口；这些证券不能通过猜测复权基准获得可比历史。

由于合格证券集合不可评价，MA20 / MA60 / MA120 / MA250 均为 `UNKNOWN / 0 observed, 0 evaluable denominator`，覆盖比例为 `null`，不显示成 0% 或完整覆盖。Theme、capital 未知继续作为独立 context 缺口；它们没有阻止本次 price/trend 来源尝试。

## 可替换身份组合与 PIT 边界

R12 不再硬依赖 `stock_basic_all`。已注册的 BaoStock `stock_basic` 显式批次可以提供同一原始上市日/类型合同；完整、校验通过的沪深交易所 A 股目录也可以只提供原始上市日和类型佐证。本次已取得 4,600 份一致的原始身份事实。

这些当前目录不得提供过去的证券存在性、名称、ST、停牌或历史主板归属。历史成员仍须逐日 `master_all` 与 RAW 状态共同成立，并受当时有效的交易所板块规则约束。冲突的上市日/类型保持 UNKNOWN。实际使用的 source policy 只有已认证的深市日期化主板范围；没有追溯补造沪市板块授权。四时钟、原始 receipt、原始 bytes hash 均保留；历史下载不转换成过去已采集的 prospective evidence。

## 历史 artifact 与本机证据

本次真实重构使用 `CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1`，逐行写入现有 DB，daily input 只含近期记录和历史引用。

| 工件 | 大小 / 身份 |
|---|---|
| 重构 daily MarketInput | 1,772,086 bytes |
| 历史 manifest | 69,592 bytes |
| History artifact hash | `e55b571500fcf0687cc6a335f50151cc3c8531fee72ab85d0f6969bdc4fba8e9` |
| Input ID | `market_30431924ae54ba2dd91937eb978f4ad2a79c4cecfe867cd4b8743c981171b499` |
| Historical research run | `historical_replay_01cca269beb345f938e319f6f88a97521308b24c45d17f62b9a83804774e3d67` |
| Source policy hash | `52e85c6fdaf32e519c4a316c07f91cbb6e715cabdd84ed331ce1d3111b0d7c00` |

完整市场资料保留本机、不提交源码。相对业务目录的审计入口为 `reports/codex/2026-09-08/r12_integrity_history/replay-final/V2_HISTORICAL_REPLAY_REPORT.json`，逐日结果在同目录 `attempts.json`，构造日另存 `reconstruction-audit.json`、`market-input.json` 和 `signal-run.json`。本轮队列迁移及后续可恢复采集见 [V2 Data Readiness](V2_DATA_READINESS.md)。

工程验证：`python -m pytest -q tests/r03_pipeline tests/r05_product/test_historical_replay_adapter.py --disable-warnings`：67 passed，0 failed，0 skipped，29.23 s。该结果不证明市场有效性。

## R11 历史记录（只作前轮上下文）

以下原报告曾因缺少已重构输入而停止。R12 已实际识别源文档和可替换身份组合，并完成上述 20 日来源尝试；因此旧文中的“没有 source-policy 目录”不再是当前阻塞理由。


- audit_mode: `BLOCKED_REQUIRES_RECONSTRUCTED_HISTORICAL_INPUT`
- coverage_type: `PROSPECTIVE_CAPTURED`
- replay_input_hash: `b7bfe39df96b574b357348f3a506ea1e4edfa519a7053103976c132be6ff12c1`
- replay_input_observation_date: `2026-09-08`
- replay_range: `2026-08-12` to `2026-09-08`
- attempt_count: `20`
- source_constructed_count: `0`
- evaluable_signal_day_count: `0`
- unevaluable_signal_day_count: `20`
- failed_count: `0`
- legacy_success_count_alias: `0`
- Focus/Watch/Rejected/Unknown: `0` / `0` / `0` / `0`
- eligible_total: `0`
- PRICE_TREND_REPLAY_SCOPE evaluable days: `None`
- FULL_CONTEXT_REPLAY_SCOPE evaluable days: `None`
- compact_items_csv: `v2_historical_replay_items_compact.csv`
- MARKET_EDGE: `NOT_ESTABLISHED`
- blocker: `No RECONSTRUCTED_HISTORICAL MarketInput or verified historical source-policy supply directory is available in this workspace; using the 2026-09-08 PROSPECTIVE_CAPTURED input for past cutoffs would correctly hide all late-captured rows.`

Historical replay was not executed because: No RECONSTRUCTED_HISTORICAL MarketInput or verified historical source-policy supply directory is available in this workspace; using the 2026-09-08 PROSPECTIVE_CAPTURED input for past cutoffs would correctly hide all late-captured rows.
