> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../README.md)查找。

# 2026-10-05 四阶段工程候选交付

当前工程候选验收已接受。CLI 正确性、保存结果查询与复盘、精确 rolling 和受控候选研究完成了相应工程检查；七个剩余正式 oracle 的 race 子例、原 256MiB 容量逐出样本及预算监督回归已补验。原完整普通/race/vet 命令的非零退出与定向修复/补验分别保留；race 合并覆盖包括明确登记的串行超时前缀推断。

新包保存查询各20个新进程样本：picks/explain P95 为5.676/5.649秒，1/2秒目标未达，完整闭包校验和数据库字节保留。旧构建真实冷计算成功、暖计算1700秒到期失败；两次研究额度用尽，新缓存全池研究未复验。真实跨日、在线刷新和完整旧库增量仍分别受资料、合法base及验收边界限制。

当前任务工程累计上限6000秒、项目总量57GiB已获明确授权，其余预算不变。源码/二进制身份、实际命令和限制见 [四阶段报告](../../../../stock-fish-go/delivery/DELIVERY_REPORT.md)。默认V2和R1冻结规则保持；MARKET_EDGE=NOT_ESTABLISHED，人工验收NOT_RUN_BY_USER，生产未激活。此处是提交前验收快照，实际源码同步以Git核验及交付消息为准；私有DB、SDK依赖、包和机器账本不提交。

# 2026-10-04 运行边界与日常 CLI 增量

发布源码身份 `0873c035ff05b586a5104628aafc95c7102a83517d634e7267d905799e818bb3`，已验收候选包对应源码身份 `e31cf80666b9bf1d900da17e116a0422ec451c1d00c0b7635962b99c3849bdab`，基线提交 6205848。发布前仅对 `interfaces/cli/integration_test.go` 做 gofmt，运行代码与策略没有改变；原包、完整普通/race/oracle 检查及真实运行证据保留。本轮本地实现与候选包验收已完成，用户随后明确授权提交推送源码、测试和说明；生产激活仍未授权。工程检查、真实只读、当前源码全池、在线刷新和投资有效性分别报告，见 [后台边界](../stock-fish-go/BACKEND_READINESS_FOR_CLI.md) 和 [用户说明](../stock-fish-go/delivery/USER_GUIDE.md)。

预算日志：锁内仅更新状态；一 worker、两条有界队列、丢弃/错误/最终送达状态；支持具备明确取消合同的 sink 和拥有的管道，任意外部 writer 不被暗中调用。单调耗时与墙钟等待独立记录，同一系统时刻成对取样避免采样间调度造成假差异。时钟异常在恢复后的预算检查及发布前阻止继续发布，不承诺全机暂停期间运行监督器。

CacheGet 不再逐股全表 SUM；scope 起止与占用改变后测量有时间/状态，CachePut 的容量 SUM 和淘汰不变。查询/解释以 mode=ro、已发布完整性验证、一致读事务运行，server 使用明确实例/数据库/协议，不静默回退。默认研究 300 秒，1 GiB RSS、2 GiB source、128 MiB 输出与 256 MiB 缓存约束保留。

---

> 2026-10-03 后台验收收尾：本轮以 b728c60 为基线，缓存上下文与最终指标修复及能力边界见 [后台交接](../stock-fish-go/BACKEND_READINESS_FOR_CLI.md)。2026-10-04 用户已授权本轮源码提交推送，生产未切换；以下既有记录按各自时间与源码身份理解。

# StockFish Go / SQLite V2 工程增量（2026-10-02）

## 2026-10-03 一致性修复：隔离发布成功，整体验收未通过

本轮基线为 `19dab9a0f4de35808ca487a30ab1e1ba6a82ade0`、分支 `review`，用户后续已明确授权将本轮代码、测试和维护文档提交推送到远端 main；私有数据与验收产物不随源码发布。当前私有证据入口为 `reports/codex/stockfish-v2-rebuild/consistency-20261003/REPORT.md`、`acceptance.json` 与 `budget.json`。原冻结源已完成新版全量验证，批次投影深验、正式隔离发布与新进程 readiness 通过；旧轮次未发布状态不再代表当前目标。最终 Go 源码身份为 `ecc990c6926b277a9ff9f597fca364185bd0d3228d25b5971180d54c4a48cf44`。

冻结源通过经过字节核验的私有独立视图消费；目标验证持 SQLite 写保留锁，已验证的迁入范围禁止追加。研究包闭合验证、发布及 `BUNDLE_V1` 账本同事务。真实输入回放式回退、bundle-only 恢复，以及新增原生结果/任务后的有损回退阻止和新文件恢复均已通过。隔离调度 PAUSED，生产未激活。

R1/V2 新库原生运行、Python 保存语义投影差分、Go 新旧存储精确差分及 R1 冷暖差分通过。六次全池额度包含一次保留的 RSS 失败，修复后五次成功。观察日 2026-09-30，cutoff 2026-10-02T20:10:00.478775+08:00。R1 为 6 CORE_PASS、41 CORE_UNKNOWN；V2 为 0 FOCUS、5 WATCH、1 UNKNOWN。两者必要资料、背景与选择可判定性仍 INCOMPLETE，均为 NOT_EVALUABLE_AS_COMPLETE_STRATEGY；不能将零 FOCUS 当作完整资料下的合法空集。

完整规模真实增量未执行：保留现有源、目标、备份和结果后，受核验源视图与耐久副本的峰值超过 50 GiB 准入上限，详见 `incremental-admission.json`；合成增量测试不替代该项。新增迁移预算 14400 秒与旧 7199.963 秒分开，未挪用策略预算。Windows 仅交叉编译，原生未验证；缓存字节指标的上下文缺失另列限制。R1 仍为 RESEARCH_ONLY，MARKET_EDGE=NOT_ESTABLISHED。本轮源码提交推送按后续明确授权执行，生产不切换。

## 2026-10-02 历史记录（旧验收状态与授权，不代表本轮）

本轮基线 `1ceb256e5331ec24ca9a5bb4185c52ebbdba4245`，原工作区干净；用户明确授权检查后中文提交并推送 main，不授权部署切换。Go 主架构及依赖测试、新 schema/证据投影、原生 R1/V2、CLI/HTTP 应用服务和恢复协议见 [架构](../../../../stock-fish-go/ARCHITECTURE_V2.md) 与 [验收记录](../stock-fish-go/VALIDATION.md)。

新模块锁定 Go >=1.26（本机私有官方工具链1.27.1）、modernc.org/sqlite1.60.1，实际引擎3.53.4；未升级旧生产 Python SQLite。正式 oracle gate 使用 `STOCKFISH_FORMAL_ACCEPTANCE=1`，缺 oracle 必须失败。TEST_FIXTURE、真实迁移、真实全池研究与投资验证分别报告；未知数据不能当合法空集。原规则/配置未调参，MARKET_EDGE=NOT_ESTABLISHED。

真实数据库、快照、恢复副本、供应商资料和完整运行输出不提交。相邻 `go-core/` 旧影子调用链保留；新主系统位于 `stock-fish-go/`，`core/` 仅兼容别名与旧诊断入口。

本轮整体验收为 `BLOCKED_MIGRATION_BUDGET`。真实搬迁已完成，最后全量验证在累计7199.963/7200s处超时，目标未发布；真实独立业务、全池差分和切换恢复不能用合成测试代替。恢复状态已保留，未经新增预算授权不继续真实迁移操作。

---

# Phase 1 每日荐股闭环工程增量（2026-09-23）

【事实】本轮新增冻结快照/日报纯投影、T+1..T+20 独立价格后验与分组统计、现有服务/API/CLI 接线，以及晚间有限重试/超时/后台调度。原 `STAGE_AWARE_MOMENTUM_V2`、配置、readiness 和旧 score 口径不由新消费者改变；Go 仍是影子验证。字段、公式、分母和验证边界见 [PHASE1_DAILY_RESEARCH_LOOP.md](../../PHASE1_DAILY_RESEARCH_LOOP.md)。

【事实】快照/日报/HTML 定向测试已实际取得 20 passed、退出码 0，页面脚本 `node --check` 退出码 0；本轮其他测试结果由统一交付记录补充，不复用下方历史数字。

【需要验证】本轮未切生产服务、未执行在线 provider 连续验收、未完成真实自然 20 会话与市场优势验证。工程、真实数据和投资结论分别报告。

---

# Daily Primary Fast Decision v1（2026-09-22）

本节优先于下方 critical capture/v1.1/R16 历史记录。Daily Primary normal latest refresh 现在在 `DAILY_PRIMARY_CRITICAL` capture 内加入 `DAILY_PRIMARY_FAST_DECISION_BOUNDARY_V1` evidence checkpoint。checkpoint 复用现有 readiness 输出，只判断第一次用户决策是否已有足够证据；v1 可在 GLOBAL REQUIRED blocker 已经可审计且剩余无关 maintenance 不会改变结论时提前返回 `NOT_READY`，例如 comparable coverage 已低于 gate 且 pending adjustment-factor batch 的最大可能补齐仍不足。若 factor/history evidence 仍可能改变 REQUIRED gate，则继续 refresh。

本轮不降低 READY 标准，不改变 eligibility、PIT、provenance、comparable basis、Stage Aware V2 语义、Focus/Watch/Rejected/Unknown、排序、阈值、score、Enhanced Evidence 或 `MARKET_EDGE=NOT_ESTABLISHED`；`NOT_READY` 仍不启动策略、不发布正式推荐。详细边界见 [Daily Primary Fast Decision v1](../../DAILY_PRIMARY_FAST_DECISION_V1.md)。

---

# Daily Primary Critical Capture Boundary v1（2026-09-22）

本节优先于下方 v1.1/R16 历史记录。Daily Primary normal latest refresh 现在通过 `DAILY_PRIMARY_CRITICAL` purpose 调用既有 MainboardPipeline，只捕获 readiness 与 active `STAGE_AWARE_MOMENTUM_V2` 所需关键输入：observation session、universe/master identity、eligibility/status、current raw price、comparable basis、adjustment factor evidence、V2 core history window 和 source/provenance receipts。RequestQueue 增加 `purpose/purposes` metadata，但 provider logical identity、receipt、normalization、history artifact、PIT 语义不变。Outcome/history maturity、theme、limit evidence、Tushare/funds enrichment、candidate/peer deep history 和 tracking artifacts 标记为 `BACKGROUND_RESEARCH`，继续可由 full capture / 后台研究路径处理，但不阻塞 Daily Primary readiness。

本轮不改变 Trend、START、ACCELERATION、HIGH_LEVEL_REPAIR、TREND_PULLBACK、FOCUS/WATCH/REJECTED/UNKNOWN、Focus gate、排序、阈值、score、Enhanced Evidence 或 `MARKET_EDGE=NOT_ESTABLISHED`。`UNKNOWN_HISTORICAL_STATUS`、缺 current price、缺 comparable basis、缺 core history window 仍正确阻塞 READY；background backlog 不得伪装成 critical 缺口。详细边界见 [Daily Primary Critical Capture Boundary v1](../../DAILY_PRIMARY_CRITICAL_CAPTURE_BOUNDARY_V1.md)。

---

# Daily Primary Reliability v1.1（2026-09-21）

本节优先于下方 R16-R1/R16 历史记录。Daily Primary 正式路径在 v1 屏障外补齐 v1.1 closure：normal latest 先解析 `expected_observation_session`，将 `OBSERVATION_SESSION_STALE/STALE_INPUT` 作为 GLOBAL REQUIRED blocker；refresh 目标固定为 expected session；refresh 完成后以刷新数据合法可见时钟更新 `actual_decision_cutoff`，再重跑 freshness + readiness，不能因 refresh request COMPLETE 直接启动策略。显式 historical observation date / specific input 路径不受 latest freshness barrier 误伤。GLOBAL REQUIRED 未就绪时记录 `daily_primary_attempts` 终态 `NOT_READY`，不启动 `STAGE_AWARE_MOMENTUM_V2`、不写正式 strategy run、不发布推荐；instrument-level 缺失记录 typed diagnostics，amount/turnover/adjustment factor 不再被压成 `MISSING_FACTOR`，并继续由现有策略产生 `UNKNOWN`。readiness 通过后若策略、持久化或导出失败，终态为 `FAILED`，不伪装成缺数。`READY` 需要 freshness/readiness、strategy、persistence、export、published 全部完成。

本轮不改变 Trend、START、ACCELERATION、HIGH_LEVEL_REPAIR、TREND_PULLBACK、FOCUS/WATCH/REJECTED/UNKNOWN、Focus gate、排序、阈值、score 或 `MARKET_EDGE=NOT_ESTABLISHED`。`READY_WITH_LIMITATIONS` 仅保留严格语义，v1.1 不用它掩盖 REQUIRED 缺失。live provider E2E、多次 retry/timeout、scheduled automatic retry、性能 <60 秒、内存回归、Go 迁移下一阶段、Score、Enhanced Evidence、market-edge 评估和历史全市场重审仍保持 OPEN/OUT OF SCOPE；验收以 post-commit Review Bundle 中 A/B/B2/C/D/E/F/G/H 场景机器证据和定向回归日志为准。

---

# R16-R1 可信证据修订（2026-09-14）

本节优先于 R16 历史记录。严格 Contract/Comparator、原生产 Python oracle 与事实注入、真实本地 normalization 修复、外层请求到结果 trace，以及有限 source/dependency 优化见 [R16-R1 工程说明](../../R16_R1_ENGINEERING.md)。版本、未知域和复现命令在该说明中明确；最终通过数、真实历史与性能判断只引用 post-commit Review Bundle。业务公式、正式信号/score 与 Go 生产边界保持。

---

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

本节优先于下方历史记录。本次在同一业务目录完成 F01—F13 规格整改，保持 R05。当前入口见 [R05 说明](../../R05_RECTIFICATION_AND_REPLAY.md)、[业务基线](../../../ENGINEERING_BASELINE.md)、[数据及 Docker 核验](../../R05_DATA_AND_DOCKER_RECTIFICATION.md)。旧报告的统计数、公式和 daemon 状态只反映旧运行。

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
