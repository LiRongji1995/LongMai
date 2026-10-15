# 部署与维护

先核对实际运行目录、Python或二进制身份、配置、数据库、状态文件、端口及在途任务。代码所在目录、发布检出和运行实例可能不同；切换路径不会自动迁移数据。本轮文档整理没有启动、停止或切换服务。

## 已有部署优先

Python服务继续使用项目虚拟环境、既有DB/state和单个调度器。日常规则不要求账户、应用LLM或旧Alpha158模型。采用pyqlib 0.9.7 wheel，不改相邻Qlib源码为editable安装。

启动前用明确选择的 Python 3.12 执行 `stock-fish/scripts/runtime_doctor.py --profile daily --json`（仓库根路径），或显式传 `--python` 检查目标解释器。规则、Qlib、审阅和 Live 可分别选对应 profile。工具只诊断，不自动修环境、迁库或启动应用；依赖导入失败时不要把旧健康记录当成新环境已通过。合同与实际缺口见[运行安全与后验第一阶段](stock-fish/docs/BASELINE_OUTCOME_PERFORMANCE_V1.md)。

Mac/Linux已有Python环境从业务目录启动：

```sh
cd stock-fish
.venv/bin/python app.py
```

这是前台启动方式；已有后台实例先核对端口和数据库，勿重复启动。HOST/PORT、数据库和state以自己的环境配置为准，服务保持本机监听。具体依赖分层见[技术索引](stock-fish/docs/README.md)。

Windows已有项目环境可使用仓库根管理脚本：

```powershell
pwsh -NoProfile -File ./Start-StockFish.ps1 -Root /你的仓库根目录
pwsh -NoProfile -File ./Logs-StockFish.ps1 -Root /你的仓库根目录 -Follow
pwsh -NoProfile -File ./Stop-StockFish.ps1 -Root /你的仓库根目录
```

这些脚本要求Windows虚拟环境布局 `stock-fish/.venv/Scripts/python.exe` 和已有本地配置。端口、数据库、state支持脚本参数或已有登记配置；默认端口8000不代表本机正在使用8000。脚本核对进程身份，只管理选定实例。

新Windows环境使用Python3.12、PowerShell7和项目锁文件；已有环境先核对，避免重复创建或升级。Docker是可选方式，维护入口为业务目录的 `Start-StockRecommendation-Docker.ps1` / `start-stockrecommendation-docker.sh`、`docker-compose.yml`；使用自己的私有 `.env.docker.local`，核对卷、地址及备份后部署。历史安装步骤可从[原操作手册的固定Git版本](https://github.com/LiRongji1995/stockfish-qlib-quant-system/blob/16d2fb810c7c921f55cd9b3f2f730627faec0d35/stock-fish/docs/archive/before-cleanup-20261005/stock-fish/README.md)追溯；其机器、依赖及步骤只适用于原日期，首次运行仍需按当前环境验证。

## 独立本地盘前命令

新增[独立终端研究V1](stock-fish/docs/STANDALONE_PREMARKET_WORKFLOW_V1.md)：在已有项目Python环境、`stock-fish/`目录执行 `python scripts/stockfish.py premarket --config PRIVATE_CONFIG.json`。它直接使用本地原件及公开来源，不要求启动HTTP服务或创建业务DB，也不自动切换现有部署。配置模板位于 `stock-fish/config/premarket.example.json`；默认用户状态目录或显式 `--state-root` 保存私有数据、排他锁及每次任务的最终控制文件。各阶段预算、实际进程资源和退出码分别保存；Windows进程树采样需现有锁定psutil。命令退出与真实连续交付分开验收，不把源码发布当作服务已加载。

## Go候选工具

Go工具目前绑定明确选择的已发布V2隔离库，生产未切换。查询和本地研究使用原生二进制；可选在线采集另需固定Python worker及独立预算。包内用户说明、身份、校验和与检查报告必须属于同一构建。

使用和构建见[Go入口](stock-fish-go/README.md)、[终端详细说明](stock-fish-go/delivery/USER_GUIDE.md)。`init`创建新配置而不创建业务数据库，不覆盖已有配置。旧包可显式读取对应保存run；不同构建的检查结果不能混用。

## 数据、停止和恢复

真实 `.env`、密钥、数据库、state、日志、模型、行情和完整审阅包保留在私有目录，源码发布不携带它们。同一数据库遵守单写者约定。服务停止、断网或休眠会影响采集；错过的时点不倒填快照，中断或失败保留原记录。

Python备份同时包含SQLite已提交状态及外部文件证据；目标必须是新目录。以下命令从业务目录执行，并将占位路径替换为实际路径：

```sh
.venv/bin/python scripts/backup_restore_state.py backup --database DB --artifact-root STATE --destination NEW_BACKUP
.venv/bin/python scripts/backup_restore_state.py verify --backup NEW_BACKUP
.venv/bin/python scripts/backup_restore_state.py restore --backup NEW_BACKUP --destination NEW_RESTORE
```

Windows使用同一项目的 `.venv/Scripts/python.exe`。恢复先进入新目录并核对历史路径引用，再按明确授权决定是否切换运行实例；直接复制活动SQLite主文件不构成一致性备份。Docker按现有维护脚本备份命名卷，不使用删除卷来排启动故障。

Go隔离库的备份、恢复、导出与回退按[隔离生命周期](stock-fish-go/ISOLATED_LIFECYCLE.md)和[迁移说明](stock-fish-go/MIGRATION.md)执行。生产切换仍需独立授权。

## 运行后核对

读取实际地址的 `/api/health`、覆盖与保存运行状态。健康仅表示服务可响应；采集成功不等于资料齐备，零焦点也不证明合法空推荐。观察日、资料截止、输入身份、全量/焦点数量和run_id需一致。

`/health/live` 公开且仅检查进程响应；`/health/ready` 与 `/health/deep` 和各 API 别名使用同一鉴权边界。readiness 要求成功调度轮询，失败、停滞或未开始不会因线程尚存而通过。SSE 每个应用实例限两条，超额 429；Live 面板仅允许本机同源访问，默认限 16 条 WebSocket。Tushare 普通 REST 在 HTTPS 合同确认前禁止新凭据请求，完整旧 checkpoint 可只读复用；不要为恢复采集改回明文端点。上述源码发布不会自动重启已有实例。

日常任务按已登记交易日历与实际配置执行，未来计划不等于已完成观察。新部署、在线成功、Windows原生运行与连续交易日稳定性各自验收，当前缺口见[当前状态](CURRENT_STATUS.md)。历次部署记录见[归档索引](stock-fish/docs/archive/README.md)。
