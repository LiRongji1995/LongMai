# R16 Go 迁移 Phase 0 契约与可观测边界（2026-09-10）

本节优先于下方历史记录。R16 建立 Go 迁移前的 Python 权威契约、正确性不变量、E2E trace 和 shadow equivalence 基础；生产计算仍由 Python `STAGE_AWARE_MOMENTUM_V2` 执行，Go core 仅为 Phase 0 影子事实切片与契约校验骨架，未接入正式荐股路径。策略语义、Focus gate、排序、阈值和 `DAILY_RECOMMENDATION_SCORE_V1` 公式均未修改，`MARKET_EDGE=NOT_ESTABLISHED` 保持。

主要工程变更：

- 新增 `FEATURE_EVIDENCE_CONTRACT_V1`、JSON schema、Python baseline exporter、snapshot compare、schema validator 和 Go shadow fixture，要求未知/缺失显式为 `UNKNOWN`，不得默认为 0 或 FALSE；
- 新增 `CANONICAL_RESOLVED_SOURCE_VIEW_V1`，由 dependency fingerprint 和 feature materialization 共用同一 canonical source token，防止“计划看见一套来源、计算读取另一套来源”；
- `DERIVED_FEATURE_STORE_V2` 身份升级为 R16 canonical source 依赖，cache lifecycle 区分 `NEW`、`MISSING_CACHE_ROW`、`INVALIDATED_SOURCE_REVISION`、`INVALIDATED_DEPENDENCY/SPEC/POLICY` 与 `CORRUPT_OR_CONFLICT`；
- Daily Primary benchmark 与 CandidateService 实际构建路径记录 E2E stage trace，覆盖请求开始、交易日/数据新鲜度、provider/history、MarketInput、来源解析、依赖规划、缓存、特征、策略、持久化、导出、响应和结果就绪；
- 真实历史稳定性审计改为批量/set-based source record 对比，带进度日志、checkpoint、ETA 和 targeted canonical resolver 复核；审计定位到既有 2026-09-09 T+1 immutable history artifact 丢失早前 comparable factor composite rows，R16 修复后续 normalization 构建时传入 prior `history_base_ref`，但不静默重写既有生产 DB/artifact。

R16 不是性能达标声明，也不是 Go 替换完成声明。提交后证据以 `StockRecommendation_R16_GoMigrationPhase0_ReviewBundle_*` 为准，页面侧独立审阅后才能判定验收；普通研究请求仍不因此获得自动改代码或提交推送授权。

---

# R14/R15 Daily Fast Path 与性能架构整改（2026-09-10）

本节优先于下方历史记录。R14 在同一业务目录中分离日常 primary 与研究 full suite；R15 将日常派生缓存升级为按 feature dependency fingerprint 绑定的 `DERIVED_FEATURE_STORE_V2`，并消除 R14 cProfile 中 percentile O(N²) 热点。日常正式荐股只运行 active `STAGE_AWARE_MOMENTUM_V2`，完整 PV/DNA/THEME/THEME_CAPITAL/Simple Trend/V1-V2 comparison 仍保留给明确研究比较。`STAGE_AWARE_MOMENTUM_V2` 交易语义、Focus gate、排名、阈值和 `DAILY_RECOMMENDATION_SCORE_V1` 公式均未修改，`MARKET_EDGE=NOT_ESTABLISHED` 保持。

主要工程变更：

- R14 新增 `DERIVED_FEATURE_STORE_V1`；R15 新增独立 `DERIVED_FEATURE_STORE_V2`，按 feature family、instrument、feature session、required source range 与 source dependency fingerprint 缓存 deterministic derived facts，V1 作为 legacy ignored/read-only 保留，缓存可删除重建，不是第二行情权威库；
- 新增 `run_active_daily_strategy(...)` 与 `/api/daily-primary/run`，CandidateService 和 scheduler 的 20:30 official primary 改走 `DAILY_PRIMARY_FAST`；
- 日常 primary 保存 compact canonical run、分页 summary projection 和压缩 per-instrument detail，列表读取不反序列化巨大 run；
- `RESEARCH_FULL` 端点仍保留完整多臂实验，score/outcome 后置追加，不阻塞 primary；
- request queue retry 新增 `request_retries` 审计表，保留 `original_failure_category`、`retry_reason`、`retry_requested_at`、`retry_actor`。

冻结输入 benchmark：eligible 3041、factor ALL/SSE/SZSE 3041/3041、1627/1627、1414/1414，PV computable 3040/3041，Research Full V2 business hash 与 fast cold/warm 完全一致。STOP baseline 为 V2 约646.8秒、full suite 约1213.8秒、RSS 约5.2GB、单 run 约220MB；R14 warm daily fast path 为59.760秒、peak RSS 992,960,512 bytes，包含隔离 compact persistence/projection 6.139秒。报告见仓库根目录 `R14_PERFORMANCE_*`、`BEFORE_AFTER_RESULT_EQUIVALENCE.*`、`DATA_READINESS.*`、`SQLITE_SIZE_AUDIT.*` 和 `R14_CPROFILE_SUMMARY.*`。

R14 warm 是 same-input rerun，不是 true daily incremental。R15 实测四类报告已生成：R15 cold 433.499秒、same-snapshot warm 182.878秒、真实 2026-09-08 -> 2026-09-09 T+1 263.098秒，T+1 `reuse_ratio=0.0`、peak RSS 1,736,269,824 bytes，最终分类 `NOT_MEETING_R15_SLA`。原因是真实 T+1 输入的历史 consumed source dependencies 大面积变化，安全缓存必须失效；fixture regression 仍证明 append-only T+1 可跨 session reuse。报告见仓库根目录 `R15_CACHE_IDENTITY_AUDIT.*`、`R15_PERCENTILE_COMPLEXITY_AUDIT.md`、`R15_TRUE_INCREMENTAL_ARCHITECTURE.md`、`R15_PERFORMANCE_RESULT.*` 和 `R15_DERIVED_STORE_GROWTH_AUDIT.md`。

---

# R05 本次统一整改与历史回放（2026-09-06）

本节优先于下方历史记录。本次在同一业务目录完成 F01—F13 规格整改，保持 R05。当前入口见 [R05 说明](stock-fish/docs/R05_RECTIFICATION_AND_REPLAY.md)、[业务基线](stock-fish/ENGINEERING_BASELINE.md)、[数据及 Docker 核验](stock-fish/docs/R05_DATA_AND_DOCKER_RECTIFICATION.md)。旧报告的统计数、公式和 daemon 状态只反映旧运行。

统一真实执行证据位于本机 `review/r05-rectification/`，主五日及 20 日逐日名单和后验报告在 `replay/`；原始全市场资料仅保留本机精确路径与 hash，不提交 Git。Git 按当前授权核对远端后同步发布目录 main，中文提交，不切换用户现有运行服务。

本次最终核心回归实际为 752 passed、1 skipped；全仓收集仍因旧 MiroFish 缺少 locales/languages.json 失败。Docker runtime-09 的 23 条执行记录成功，镜像内 71 个关键文件与冻结宿主源码字节一致，隔离容器已停止，用户原服务未切换。随后仅为详情增加 SQLite 派生索引，相关 34 项回归和真实 API 复测通过。工程结果与真实行情及投资验证分别报告。

---

# R05 工程基线

当前默认产品仍是沪深主板无账户规则候选研究。R05 新增独立实验层和数据供给 adapter，不改变 `DAILY_V1` 的名称、角色、五族语义或参数状态；`PV_OBSERVATION_V1`、`DNA_RERANK_V1`、`THEME_CONTEXT_V1` 均为 `INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED`。

R05 主要工程变更：

- 新增 `PV_OBSERVATION_V1` 真实量价观察控制组，按当前可信主板普通股合格池、六日完整字段、中位百分位和固定公式排序；
- 新增 `DNA_RERANK_V1` 与 `THEME_CONTEXT_V1`，只对 PV 入围项追加股性和题材上下文，覆盖不足时不回写 S1；
- 新增 Tushare Pro HTTP adapter 端点表、参数校验、字段白名单、分页、token 缺失错误和响应脱敏，当前状态为 `AUTH_REQUIRED / REAL_NOT_VERIFIED`；
- Eastmoney 直接限价按 Asia/Shanghai 业务日校验，worker 超时保留成功子项；
- 规则推导 VERIFIED 必须携带本地认证哈希和直接样本，CandidateRun summary 默认读取轻量投影；
- 首页新增“R05实验策略”区域，并暴露策略规格、实验运行和最近策略对照 API。

R05 实测状态：观察日 `2026-09-04`，真实 PV 采集 43/43 请求完成；当前主板合格池 3,043，只 3,039 只完成 PV 必需字段核验，缺失 4 只，最终入围 525 只。`DNA_RERANK_V1=INSUFFICIENT_COVERAGE`，`THEME_CONTEXT_V1=UNAVAILABLE`，`MARKET_EDGE=NOT_ESTABLISHED`。详见 `review/r05-data-strategy/final-report.md`。

验收：R04/R05 相关业务子集 213 项通过，核心业务测试脚本 665 项通过；直接 `pytest -q` 仍被旧 MiroFish `locales/languages.json` 缺失阻塞。运行数据、真实 `.env`、provider cache、私人 DB、日志、模型权重、完整市场输入和完整本机审阅包不属于源码发布内容。

---

# R04 工程基线

当前默认产品是沪深主板无账户规则候选研究，版本仍为 `YAOGU_V1.1.0` / `YAOGU_FEATURES_V1_R03`。五族公式、权重、阈值、展示语义和 `MARKET_EDGE=NOT_ESTABLISHED` 不因 R04 改动而改变。

R04 主要工程变更：

- 新增 `daily_limits_eastmoney` 直接逐日限价来源，默认 DAG 为全主板当前对象登记分批请求；
- direct published 限价行进入 normalizer，并要求证券、日期、close 与 RAW 行一致；
- `RULE_DERIVED_VERIFIED` 只在官方规则、普通/豁免状态、参考价语义、来源 hash、验证套件和样本证据齐备时成立；
- CandidateRun 默认读取改为分页 summary，单证券完整证据通过 detail API 延迟加载；
- CLI `capture_mainboard_data.py --rounds` 支持连续有限预算续采。

R04 实测状态：开发工作区全量业务测试 `656 passed in 179.94s`，默认服务烟测可启动/访问/停止；低预算真实网络烟测仍为 `consumer_status=UNAVAILABLE`，不是合法 EMPTY，也不是完整真实榜单。详见 `docs/r04-release.md`。

运行数据、真实 `.env`、provider cache、私人 DB、日志、模型权重和完整本机审阅包不属于源码发布内容。
