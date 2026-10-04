> 2026-10-03 后台验收收尾：本轮以 b728c60 为基线，缓存上下文与最终指标修复及能力边界见 [后台交接](stock-fish-go/BACKEND_READINESS_FOR_CLI.md)。2026-10-04 用户已授权本轮源码提交推送，生产未切换；以下既有记录按各自时间与源码身份理解。

# StockFish Go / SQLite V2 隔离交付（2026-10-02）

## 2026-10-03 一致性修复：隔离发布成功，整体验收未通过

本轮基线为 `19dab9a0f4de35808ca487a30ab1e1ba6a82ade0`、分支 `review`，用户后续已明确授权将本轮代码、测试和维护文档提交推送到远端 main；私有数据与验收产物不随源码发布。当前私有证据入口为 `reports/codex/stockfish-v2-rebuild/consistency-20261003/REPORT.md`、`acceptance.json` 与 `budget.json`。原冻结源已完成新版全量验证，批次投影深验、正式隔离发布与新进程 readiness 通过；旧轮次未发布状态不再代表当前目标。最终 Go 源码身份为 `ecc990c6926b277a9ff9f597fca364185bd0d3228d25b5971180d54c4a48cf44`。

冻结源通过经过字节核验的私有独立视图消费；目标验证持 SQLite 写保留锁，已验证的迁入范围禁止追加。研究包闭合验证、发布及 `BUNDLE_V1` 账本同事务。真实输入回放式回退、bundle-only 恢复，以及新增原生结果/任务后的有损回退阻止和新文件恢复均已通过。隔离调度 PAUSED，生产未激活。

R1/V2 新库原生运行、Python 保存语义投影差分、Go 新旧存储精确差分及 R1 冷暖差分通过。六次全池额度包含一次保留的 RSS 失败，修复后五次成功。观察日 2026-09-30，cutoff 2026-10-02T20:10:00.478775+08:00。R1 为 6 CORE_PASS、41 CORE_UNKNOWN；V2 为 0 FOCUS、5 WATCH、1 UNKNOWN。两者必要资料、背景与选择可判定性仍 INCOMPLETE，均为 NOT_EVALUABLE_AS_COMPLETE_STRATEGY；不能将零 FOCUS 当作完整资料下的合法空集。

完整规模真实增量未执行：保留现有源、目标、备份和结果后，受核验源视图与耐久副本的峰值超过 50 GiB 准入上限，详见 `incremental-admission.json`；合成增量测试不替代该项。新增迁移预算 14400 秒与旧 7199.963 秒分开，未挪用策略预算。Windows 仅交叉编译，原生未验证；缓存字节指标的上下文缺失另列限制。R1 仍为 RESEARCH_ONLY，MARKET_EDGE=NOT_ESTABLISHED。本轮源码提交推送按后续明确授权执行，生产不切换。

## 2026-10-02 历史记录（旧验收状态与授权，不代表本轮）

本轮在既有 `stock-fish-go/` 重建模块化单体，统一入口 `stockfish`；原生 R1 与当前主动 V2 独立消费公共事实。V2 仍为默认，R1 显式选择且保持 RESEARCH_ONLY。SQLite V2 是隔离目标库，真实搬迁已完成但完整验证耗尽7200s预算，尚未发布或承载正式研究。生产 Python 服务、旧数据库路径、调度、Qlib wheel 和模型均未切换。

构建、只读旧适配、新输入导入、研究、任务/取消、保存结果查询及完整性验证见 [Go 运行说明](stock-fish-go/README.md)；数据库映射见 [迁移说明](stock-fish-go/MIGRATION.md)，可执行隔离 preflight/cutover/export/rollback/restore 见 [恢复说明](stock-fish-go/ISOLATED_LIFECYCLE.md)。真实迁移与独立运行的实测证据、预算和未验证范围统一登记在 [验收记录](stock-fish-go/VALIDATION.md)。

正式服务激活仍需另行授权。本轮源码推送不修改生产进程或默认数据路径；不能运行旧管理脚本来假设切换已完成。APFS 恢复克隆仅适用于本任务的隔离路径和单写者租约，原生产数据快照仍使用 SQLite online backup。

---

# Mac 首次采集预算（2026-10-01）
冷启动可设置 `STOCKFISH_CAPTURE_FAST_DECISION=false`，跳过采集过程中的提前 NOT_READY 判定，避免在大量历史数据补齐前重复构造全量内存输入。默认 `true`；仅接受 true/false，显式 capture 参数优先。此设置不跳过采集结束后的完整 readiness 或正式荐股发布校验，报告记录 `fast_decision_enabled`。


MainboardPipeline 支持可选环境变量 `STOCKFISH_CAPTURE_TIMEOUT_SECONDS`（0–90 秒，默认 40）、`STOCKFISH_CAPTURE_MAX_SECONDS`（0–7200 秒，默认 600）、`STOCKFISH_CAPTURE_MAX_REQUESTS`（0–10000 次，默认 128）。时间下界均不含 0。显式传入 RequestBudget 时优先使用调用方预算。预算不修改策略、证券资格、来源身份或数据就绪门槛；实际请求超时仍受本轮剩余时间约束。

本机真实 Baostock 探针：2026-09-30 全证券目录约 46 秒、全 A 日线约 66 秒，均超过原 40 秒默认值。冷启动可显式使用 90 秒单请求预算，并复用既有缓存续采。失败重试使用 RequestQueue.retry_blocked 记录操作原因，不删除旧回执。采集成功不等于 READY，必须继续查询 Daily Primary 的正式终态。

可选 `STOCKFISH_CAPTURE_HISTORY_MODE=PER_SECURITY_RAW` 仅改变 Daily Primary 的首次历史传输计划：复用已登记的 Baostock `history` RAW adapter，完整当前主板目录每批 10 只，窗口仍由 V2 决定；当前日全市场日线、资格、复权因子和 READY 门槛不变。计划及报告记录实际 endpoint、窗口、粒度和输入身份，不把当前目录历史称为当时全市场回放。默认 `ALL_MARKET_DAILY` 和完整研究路径保持原计划。真实 3 只股票当前日 11 字段交叉核对一致、隔离采集窗口/股票池等价测试通过，仅是工程与传输证据，不代表完整真实 READY 或市场优势。

# Daily Observation Monitor 部署增量（2026-09-23）

【事实】新增只读 GET `/api/daily-research/observation` 与现有 CLI `daily-observation`，复用原实例、DB/state、日历和交付/后验服务；没有 schema migration、第二数据库或新后台任务。源码更新后需由原管理脚本重新加载实例才能提供新路由；Start 会复用健康旧进程，不能把源码落盘视为路由已经生效。按实际授权核对原 PID/环境/路径、作业空闲和备份后执行既有启停流程。使用和验收口径见 [观察工具说明](stock-fish/docs/PHASE1_OBSERVATION_MONITOR.md)。

【需要验证】工程检查与真实 HTTP/CLI 验证记录在本轮 observation monitor report；不以 READY 页面或测试 fixture 宣称自然 20 日已完成。以下记录按原日期和任务理解。

---

# Phase 1 每日荐股闭环部署边界（2026-09-23）

【事实】源码新增 `/daily-research` 只读页面与 `/api/daily-research/*` 日报/快照/后验入口，继续使用已有 Python 服务、单写入 SQLiteStore 和 state；Go 保持影子验证。新 snapshot 与原 run 同事务，后台 outcome 追加保存。现有 scheduler 增加有限重试、30 分钟协作期限及有界后台工作；口径与命令见 [Phase 1 文档](stock-fish/docs/PHASE1_DAILY_RESEARCH_LOOP.md)。

【需要验证】本轮没有升级/重启用户现有服务、切换 DB/state 或执行在线采集验收。实际部署需核对原实例并按已有授权流程执行；源码检查不代表每日自动运行已生效，也不代表自然 20 会话或收益验证完成。以下部署记录继续按原日期理解。

---

# Daily Primary Fast Decision v1 部署补充（2026-09-22）

本节优先于下方历史部署记录。生产仍为 Python Daily Primary；服务入口仍是 `/api/daily-primary/run` / `CandidateService.submit_daily_primary_strategy()` 与 active `STAGE_AWARE_MOMENTUM_V2`。normal latest refresh 走 `DAILY_PRIMARY_CRITICAL` capture，并在 critical request queue 进展后评估 `DAILY_PRIMARY_FAST_DECISION_BOUNDARY_V1`。该评估只可能提前结束 first user `NOT_READY`，不会提前运行策略或降低 READY gate。provider identity、receipt、normalization、history artifact、PIT 语义、DB/state 路径和调度入口不变。

---

# R16-R1 修订（2026-09-14）

本节优先于下方历史部署记录。生产仍为 Python Daily Primary；Go 只作验证切片，未切换服务或正式 DB。严格 contract、真实外层 trace、PIT instant resolver 与隔离 corrected history 的边界见 [R16-R1 工程说明](stock-fish/docs/R16_R1_ENGINEERING.md)。派生版本为 `DERIVED_FEATURE_STORE_V2_FAMILY_SPECS_R16_R1_PIT_INSTANT`，旧缓存保留。验收采用冻结 commit 后的外置 evidence，不把源码说明视为全量 PASS。只读审计不得调用会写计划/epoch 的 candidate-schedule 路径。

---

# R16 Go 迁移 Phase 0 部署补充（2026-09-10）

本节优先于下方历史部署记录。R16 不切换运行服务、不更换权威数据库、不启用 Go 生产计算。当前实际仓库仍为 `D:/stockfish-qlib-quant-system`，业务目录 `stock-fish`，权威数据库仍是 `stock-fish/data/stockfish.sqlite3`，state root 仍是 `stock-fish/data/core`；不提交真实 DB、state、cache、日志、模型或 review bundle。

Daily Primary 仍走 Python `/api/daily-primary/run` / `CandidateService.submit_daily_primary_strategy()` 与 active `STAGE_AWARE_MOMENTUM_V2`。R16 在该路径上增加 E2E trace，并将派生缓存身份升级为 `DERIVED_FEATURE_STORE_V2_FAMILY_SPECS_R16_CANONICAL_SOURCE`：dependency planning 与 feature build 共用 `CANONICAL_RESOLVED_SOURCE_VIEW_V1`，cache miss/invalidated/new 等生命周期分类进入运行结果。Go 目录 `go-core/` 仅用于 Contract V1 影子事实切片和 Python-vs-Go equivalence fixture；没有服务入口指向 Go。

后续采集 normalization 支持用最近已登记历史 artifact 作为 `history_base_ref` 继续 append，避免新 artifact 因当日 capture 缺少 factor evidence 而丢失既有 comparable rows。既有 2026-09-09 T+1 artifact 如已包含该问题，R16 不自动改写生产历史；真实数据 readiness 与 cache reuse 必须以提交后审计报告为准。

```powershell
pwsh -NoProfile -File ./Start-StockFish.ps1 -Port 8001 -DatabasePath ./stock-fish/data/stockfish.sqlite3 -StateRoot ./stock-fish/data/core
Invoke-RestMethod http://127.0.0.1:8001/api/health
Invoke-RestMethod http://127.0.0.1:8001/api/candidate-schedule
```

---

# R14/R15 日常快路径部署补充（2026-09-10）

本节优先于下方历史部署记录。当前实际仓库仍为 `D:/stockfish-qlib-quant-system`，业务目录 `stock-fish`，权威数据库仍是 `stock-fish/data/stockfish.sqlite3`，state root 仍是 `stock-fish/data/core`；不新建第二套业务库，也不提交真实 DB/state/cache。

R14/R15 后，20:30 official primary 和普通“下一交易日荐股”入口走 Daily Primary Fast Path：`/api/daily-primary/run` / `CandidateService.submit_daily_primary_strategy()`，只运行 active `STAGE_AWARE_MOMENTUM_V2`，使用 `DERIVED_FEATURE_STORE_V2` 派生缓存并保存 compact run、summary projection 和按需 detail。`DERIVED_FEATURE_STORE_V1` 作为 legacy ignored/read-only 保留，不在本轮删除。完整 `/api/strategy-experiments/run` 继续保留为 `RESEARCH_FULL`，仅用于明确的多策略比较、historical replay 或深度研究。

```powershell
pwsh -NoProfile -File ./Start-StockFish.ps1 -Port 8001 -DatabasePath ./stock-fish/data/stockfish.sqlite3 -StateRoot ./stock-fish/data/core
Invoke-RestMethod http://127.0.0.1:8001/api/health
Invoke-RestMethod http://127.0.0.1:8001/api/candidate-schedule
```

R14/R15 不修改 `STAGE_AWARE_MOMENTUM_V2` 交易语义，不修改 `DAILY_RECOMMENDATION_SCORE_V1` 公式。score/outcome 是后置追加消费者，不进入 primary critical path。R14 same-input warm 不是 true daily incremental 证据；R15 真实 2026-09-08 -> 2026-09-09 benchmark 已标记 `NOT_MEETING_R15_SLA`。性能、SQLite 与数据 readiness 证据见 `R14_PERFORMANCE_RESULT.*`、`R14_PERFORMANCE_BASELINE.*`、`BEFORE_AFTER_RESULT_EQUIVALENCE.*`、`DATA_READINESS.*`、`SQLITE_SIZE_AUDIT.*`、`R14_CPROFILE_SUMMARY.*`、`R15_PERFORMANCE_RESULT.*`、`R15_CACHE_IDENTITY_AUDIT.*`、`R15_TRUE_INCREMENTAL_ARCHITECTURE.md`、`R15_PERCENTILE_COMPLEXITY_AUDIT.md` 和 `R15_DERIVED_STORE_GROWTH_AUDIT.md`。

---

# R12 持久本机服务（2026-09-08）

本节优先于下方历史部署记录。当前实际仓库 D:/stockfish-qlib-quant-system，业务目录 stock-fish。同一既有权威数据库 stock-fish/data/stockfish.sqlite3，state root stock-fish/data/core；不新建第二套业务库。R12 长历史 artifact 也在此 SQLiteStore。升级前用 SQLite 在线备份或停服一致性备份保存既有库。

```powershell
pwsh -NoProfile -File ./Start-StockFish.ps1 -Port 8001 -DatabasePath ./stock-fish/data/stockfish.sqlite3 -StateRoot ./stock-fish/data/core
Invoke-RestMethod http://127.0.0.1:8001/api/health
Invoke-RestMethod http://127.0.0.1:8001/api/candidate-schedule
& ./stock-fish/.venv/Scripts/python.exe ./stock-fish/scripts/r12_post_cutoff_check.py
```

启动脚本把明确配置保存到忽略的 work/stockfish-service.json，随后启动不传参数也复用同一端口/DB/state。work/stockfish-process.json 登记 PID、创建时间、repo、DB、state、port、scheduler thread identity。Waitress 单进程四个 HTTP 线程，任务仍由同一 DB 锁保护的单 worker 执行；禁用 reloader，不开启第二 scheduler。重复启动核验并复用已登记进程。停止使用 Stop-StockFish.ps1，只处理精确命令行与创建时间匹配的进程。8000 的无关进程不受影响。

原未登记实例只有 -AdoptExisting 显式模式在 app/interpreter、监听 PID、健康返回 DB/state、存活 scheduler 全部匹配时才可登记；不同身份直接拒绝。日志在 logs/，服务独立于 Codex 任务存活，Windows 关机/登出后的恢复仍需要运行启动脚本；本轮未建立系统级自动登录启动任务。

20:30 触发采用实际完成 cutoff；真实协议与截止后核验见 [V2_PROSPECTIVE_VALIDATION_PROTOCOL](stock-fish/docs/V2_PROSPECTIVE_VALIDATION_PROTOCOL.md)。行情 quality 与健康状态分别核验，SUCCEEDED 只证明诊断/结果已保存，不证明数据可评价。历史迁移与原始回执、运行数据库、token 均不提交 Git。

---

# R05 本次统一整改与历史回放（2026-09-06）

本节优先于下方历史记录。本次在同一业务目录完成 F01—F13 规格整改，保持 R05。当前入口见 [R05 说明](stock-fish/docs/R05_RECTIFICATION_AND_REPLAY.md)、[业务基线](stock-fish/ENGINEERING_BASELINE.md)、[数据及 Docker 核验](stock-fish/docs/R05_DATA_AND_DOCKER_RECTIFICATION.md)。旧报告的统计数、公式和 daemon 状态只反映旧运行。

统一真实执行证据位于本机 `review/r05-rectification/`，主五日及 20 日逐日名单和后验报告在 `replay/`；原始全市场资料仅保留本机精确路径与 hash，不提交 Git。Git 按当前授权核对远端后同步发布目录 main，中文提交，不切换用户现有运行服务。

本次最终核心回归实际为 752 passed、1 skipped；全仓收集仍因旧 MiroFish 缺少 locales/languages.json 失败。Docker runtime-09 的 23 条执行记录成功，镜像内 71 个关键文件与冻结宿主源码字节一致，隔离容器已停止，用户原服务未切换。随后仅为详情增加 SQLite 派生索引，相关 34 项回归和真实 API 复测通过。工程结果与真实行情及投资验证分别报告。

---

# Windows 部署：R05 数据供给与实验策略

默认 Web/API 仍是沪深主板无账户规则候选研究入口。R05 新增“R05实验策略”区域和策略实验 API，可查看 `DAILY_V1`、`PV_OBSERVATION_V1`、`DNA_RERANK_V1`、`THEME_CONTEXT_V1` 规格，并运行量价观察控制组。`DAILY_V1` 不改名、不降级；所有新增实验策略均标记 `INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED`，`MARKET_EDGE` 仍为 `NOT_ESTABLISHED`。

R05 专用 CLI 可运行真实量价观察采集：

~~~powershell
& $ProjectPython ./stock-fish/scripts/r05_pv_observation_capture.py --cache ./data/r05-cache --output ./data/r05-pv-capture
~~~

本机验收的最新真实输出为 `review/r05-data-strategy/actual-pv-capture-03/`，观察日 `2026-09-04`，请求 43/43 完成，预算未耗尽；当前主板合格池 3,043 只，资格未知 0，只 3,039 只完成 PV 必需字段核验，缺失 4 只，最终 `PV_OBSERVATION_V1` 入围 525 只。提交内只保留 `review/r05-data-strategy/` 的摘要文档和 Top20，完整市场输入、缓存、日志和 ZIP 不作为源码内容。

Tushare Pro HTTP adapter 已接入基础资料、交易日历、日线/复权、涨跌停、停复牌、ST、龙虎榜、资金、东财题材和竞价端点的配置、字段、分页、token 缺失错误和脱敏日志。当前环境无 token，真实状态为 `AUTH_REQUIRED / REAL_NOT_VERIFIED`。

R05 还关闭了四个审计缺陷：Eastmoney 上海业务日校验、worker 超时部分结果保留、规则推导 VERIFIED 本地认证哈希、CandidateRun summary 轻量投影。R04/R05 相关子集测试 213 项通过，核心业务测试脚本 665 项通过；直接 `pytest -q` 仍被旧 MiroFish `locales/languages.json` 缺失阻塞。

---

# Windows 部署：R04 无账户规则候选

默认 Web/API 是沪深主板五类候选研究入口。无需现金、持仓、LLM 或旧 Alpha158 推理，也不要求先下载模型。**当前真实验收仍为 UNAVAILABLE，尚不能输出完整 DAILY_V1 榜单或证明合法 EMPTY。** R04 变更与边界见 [R04 发布说明](docs/r04-release.md)，R03 基线见 [R03 发布说明](docs/r03-release.md)。

R04 后首页默认读取分页 summary：

- `GET /api/candidate-runs/<run_id>/summary?page_size=50`
- `GET /api/candidate-runs/<run_id>/items/<instrument_id>`

完整 run/export 仍保留用于审计。直接限价队列新增 `daily_limits_eastmoney`，通过 Eastmoney 单股 `qt/stock/get` f51/f52 尝试获取当日涨跌停价；未知或网络失败不会被当成可用限价。

## 1. 项目环境

准备 Windows x64、Python 3.12、PowerShell 7 和 Git。在仓库根目录执行：

~~~powershell
$ProjectRoot = (Get-Location).Path
py -3.12 -m venv ./stock-fish/.venv
$ProjectPython = Join-Path $ProjectRoot 'stock-fish/.venv/Scripts/python.exe'
& $ProjectPython -m pip install -r ./requirements-windows.lock.txt
& $ProjectPython -m pip check
~~~

没有 Python Launcher 时，使用已安装 Python 3.12 的路径代替 py -3.12。锁文件是实际 Windows 依赖版本快照，包含行情 adapter 和可选研究组件，不是依赖哈希锁。所有包安装在项目虚拟环境内。

运行使用 pyqlib 0.9.7 wheel。qlib/ 保留源码参考，不执行 editable install。规则候选不调用旧 Qlib 推理；环境中存在 Qlib 不表示模型已登记或获得发布授权。

## 2. 本地配置和运行数据

启动脚本要求存在 stock-fish/.env。首次可建立不含密钥或机器路径的最小配置；已有文件不覆盖：

~~~powershell
$ProjectEnv = Join-Path $ProjectRoot 'stock-fish/.env'
if (-not (Test-Path -LiteralPath $ProjectEnv)) {
    @('HOST=127.0.0.1', 'PORT=8000', 'DEBUG=False', 'PYTHONUTF8=1') |
        Set-Content -LiteralPath $ProjectEnv -Encoding utf8
}
~~~

在同一个 PowerShell 会话中，为本次部署设置数据库和 artifacts。下例使用仓库根目录下被忽略的 data/，也可以改为自己的独立运行目录：

~~~powershell
$ProjectState = Join-Path $ProjectRoot 'data/r04'
New-Item -ItemType Directory -Force -Path $ProjectState | Out-Null
$env:STOCKFISH_DB_PATH = Join-Path $ProjectState 'research.sqlite3'
$env:STOCKFISH_STATE_DIR = Join-Path $ProjectState 'core'
~~~

后续启动应使用相同路径。改变路径会选择另一个数据库，不会迁移旧记录。选择现有部署前先核对其数据库和 artifacts，完成备份再升级。真实 .env、token、运行数据和日志不提交到 Git。

默认规则来源不要求新增付费调用；网络、字段、权限与使用条件依实际来源为准。缺凭据、失败、未公布与没有数据分别报告。SDK 开源不授予底层行情再分发权。LLM 配置不是主链前提，系统不会自动下单。

## 3. 启动、采集与核验

~~~powershell
pwsh -NoProfile -File ./Start-StockFish.ps1 -Root $ProjectRoot
Invoke-RestMethod 'http://127.0.0.1:8000/api/health'
Invoke-RestMethod 'http://127.0.0.1:8000/api/strategy-configs/current'
Invoke-RestMethod 'http://127.0.0.1:8000/api/universe/coverage'
~~~

打开 [本地页面](http://127.0.0.1:8000/)，点击“采集 / 续采全主板数据”。同一默认入口也可通过 API 调用：

~~~powershell
$CaptureJob = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/candidate-data-pipeline/capture' -ContentType 'application/json' -Body '{}'
Invoke-RestMethod ('http://127.0.0.1:8000/api/jobs/' + $CaptureJob.job_id)
Invoke-RestMethod 'http://127.0.0.1:8000/api/candidate-data-pipeline'
~~~

命令行可用同一 DAG 做连续有限预算续采：

~~~powershell
& $ProjectPython ./stock-fish/scripts/capture_mainboard_data.py --cache ./data/r04-cache --output ./data/r04-capture --rounds 3
~~~

配置 STOCKFISH_API_KEY 后，请求还需携带对应 Bearer 认证。HTTP 不接受任意文件路径、账户、来源批准或回填时间。

默认每次预算为 128 次 adapter 调用、600 秒、单调用最多 40 秒、每请求最多两次尝试。队列保存需求、结果、缓存命中和 pending，预算用尽保留待续采任务。发现范围是全池，不是涨停池前六只。

/api/health 检查应用、持久内容和任务服务，不在健康请求中重新计算行情覆盖。可选模型/LLM 缺失可能使总状态 degraded；启动脚本核对 application、tracker、jobs。行情就绪查看 /api/universe/coverage 和 run.status。作业 SUCCEEDED 表示真实诊断已保存，不代表其 quality=COMPLETE 或 activated=true。

五类完整评分需要合法历史资格、精确价格事件、量价可比依据、当时可见主题及情绪。缺关键证据时显示 UNAVAILABLE，不能把它当成策略未命中。陈旧输入可读取，但须显式选择历史日期/ID，不冒充今天。

## 4. 调度、停止与恢复

服务运行期间按交易所日历计划 16:10 初榜、20:30 晚间 revision 和次日 09:26 竞价复核。只有真实取得 09:25 撮合数据才评估；当前缺实际 adapter 时保持不可用。错过时点不补造旧快照。20 交易日前瞻计划不是已成熟结果，也不是删除更早未完成样本的期限。

~~~powershell
pwsh -NoProfile -File ./Logs-StockFish.ps1 -Root $ProjectRoot -Tail 80
pwsh -NoProfile -File ./Logs-StockFish.ps1 -Root $ProjectRoot -Follow
pwsh -NoProfile -File ./Stop-StockFish.ps1 -Root $ProjectRoot
~~~

三个脚本的 -Root 默认为脚本所在目录，支持从其他目录显式管理同一部署。日志和进程登记位于 <Root>/logs/ 和 <Root>/work/。脚本核验 PID、创建时间和命令行，不终止无关进程。默认端口 8000；已登记部署按 work/stockfish-service.json 核对配置端口；同一数据库或端口不要运行第二实例，不启用调试 reloader 或多 worker。

服务关闭时不会继续采集。中断任务保留历史并标为 INTERRUPTED，显式重试建立新任务。失败不覆盖旧 active；候选发布和成功任务终态在同一事务中保存。

备份目标必须是新目录：

~~~powershell
$ProjectBackup = Join-Path $ProjectRoot ('data/backups/r04-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
& $ProjectPython ./stock-fish/scripts/backup_restore_state.py backup --database $env:STOCKFISH_DB_PATH --artifact-root $env:STOCKFISH_STATE_DIR --destination $ProjectBackup
& $ProjectPython ./stock-fish/scripts/backup_restore_state.py verify --backup $ProjectBackup
~~~

恢复参数见该脚本 restore --help。SQLite 备份考虑已提交 WAL；外部 artifacts 应在停服或不可变条件下复制。恢复到新目录并核对路径引用，不覆盖旧库。hash 用于本地损坏检查，不是抵御完全磁盘写权限者的认证。

## 5. 开发验证

~~~powershell
& $ProjectPython -m pip install -r ./stock-fish/requirements-dev.txt
& $ProjectPython ./stock-fish/scripts/run_core_tests.py tests -q
& $ProjectPython -m pip check
~~~

测试使用隔离 DB/state 和明确 TEST_FIXTURE，不依赖个人运行库。合成样例、离线故障注入、真实来源调用和 Windows 启动核验是不同证据。R04 原工作环境最终业务 656 项通过（179.94 秒），这是该次记录，不承诺新机器已自动完成验收。

## 可选 Qlib 与历史记录

旧 CSI300 Alpha158 / LightGBM 仅为诊断研究对照。下载不等于登记，登记不等于样本外有效。模型审批、消费者校验和原 hash 保留，不能为规则候选指定未运行的 model_id。

早期结果位于 [历史执行清单](docs/verification/inference-execution.json)，来源和许可见 [第三方通知](THIRD_PARTY_NOTICES.md)。它们不证明 R04 数据完整或投资收益。完整模型、Qlib binary 行情和本机私有审阅数据不随源码发布迁移。Docker、真实竞价和完整历史投资验证未在 R04 完成。
