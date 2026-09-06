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

三个脚本的 -Root 默认为脚本所在目录，支持从其他目录显式管理同一部署。日志和进程登记位于 <Root>/logs/ 和 <Root>/work/。脚本核验 PID、创建时间和命令行，不终止无关进程。默认固定检查 8000 端口；同一数据库或端口不要运行第二实例，不启用调试 reloader 或多 worker。

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
