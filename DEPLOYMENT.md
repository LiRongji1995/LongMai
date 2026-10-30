# 部署与维护

新增[P0诊断与评价基础V1](stock-fish/docs/P0_DAILY_RELIABILITY_AND_EVALUATION_V1.md)，只新增scripts层入口及隔离测试。`codex_p0.py` 不启动worker、采集、DB或恢复任务；已有恢复登记须显式选择。私有报告仅写新文件，不覆盖旧报告，发布前后核输入载体。源码发布不切换服务、默认入口、预算或通知计划；执行评价Python API的条件归档模拟不等于真实来源认证。

新增[original 精确编码执行 V1](stock-fish/docs/ORIGINAL_CANONICAL_PERFORMANCE_V1.md)：`codex_daily_fast_v1.py` 是显式登记入口，复用原准入、固定 request-key、预算及完整 original 流程；默认入口和私有登记不变。恢复登记仍须显式选择既有 runtime，换入口或 key 不能重试旧失败请求。真实阶段性能与完整日常交付分别验收。

新增[失败题材冷存 V1](stock-fish/docs/FAILED_THEME_COLD_STORAGE_V1.md)，只显式处理已失败且 worker 已回收的 V2 派生 journal。真实五步已完成，归档及全件实体恢复核验后移除精确载荷，四根净逻辑减量约 404 MiB；原 manifest、失败控制、核心和 RAW 保持。恢复仅到新目录，保留原计划、工具身份、归档和回执；不恢复旧 inode、owner 或来源能力。日常入口不自动清理，源码发布不切换服务或重试盘前请求。

**2026-10-28 恢复现状：**原 daily/review 环境已重新验收，137 份旧采集元数据已按真实原件重认，并以显式恢复登记运行 original 全流程；旧 default 与失败记录保留。26→27 已完成完整复权和原核心，后续题材阶段触全流程限时；28→29 在复权采集遇到部分失败和缺原始首见时间，与合盖休眠重叠，尚无完整清单。真实端到端提速比例仍未测得。详见[环境恢复、真实执行与性能边界](stock-fish/docs/PREMARKET_ENVIRONMENT_RECOVERY_V1.md)。

默认 `codex_daily_v2.py premarket` 保留原题材执行器；`--engine shared` 是显式共享计算候选。小样本全流程曾测得回退，因此没有将其自动设为默认，也没有据此宣称真实新清单提速。

新增 [完整盘前执行V1](stock-fish/docs/PREMARKET_SHARED_EXECUTION_V1.md)：`codex_daily_v2.py premarket-plan` 只读准入，`premarket` 在同一登记根和原配置下调用完整工作流。新worker使用新schema及明确scripts闭包，旧core和旧入口原字节保留。源码发布不修改私有预算、不切换生产服务或调度。

[统一执行入口V1](stock-fish/docs/SYSTEM_EXECUTION_PIPELINE_V1.md)只新增 `scripts/` 执行器；不得把新模块放进旧 `stockfish_core` 动态认证集合而破坏历史归档。入口转入原登记解释器、源码目录和全新字节码查找位置。封存视图与私有HMAC密钥留在同一共享报告根；密钥丢失不重新生成以接管旧视图，需显式完整审计后处理。新计算采用原配置与新执行器限额的较小值，没有切换运行服务或自动启用任务。

[空间与时间优化V1](stock-fish/docs/SPACE_TIME_OPTIMIZATION_V1.md)使用独立 `codex_optimize.py`，按原登记源码和解释器启动新执行版本。V1原代码和归档身份保留；V2另行登记。容量和缓存预算不扩大，未迁移真实DB或切换服务；调度索引迁移、适配器服务启动均为显式操作。本轮源码发布不能解释为日常后端已自动升级。

本轮新增[完整结果归档、新输出压缩与复权尾窗](stock-fish/docs/SHARED_RESULT_COMPACT_INCREMENTAL_V2.md)，由已登记的解释器和共享路径执行。归档登记只增加小回执；压缩只处理本次拥有的新输出。显式执行档位不修改本机1 GiB总逻辑上限或原写入配置，尾窗诊断不自动合成完整因子，也不切换服务。

## Codex 聊天的共享运行登记（2026-10-25）

新日常入口见 [CODEX_CHAT_WORKSPACE_V1](stock-fish/docs/CODEX_CHAT_WORKSPACE_V1.md)。Codex 先核实原运行环境，再用私有 JSON 登记既有路径；登记只写小型位置和操作回执，不安装依赖、复制 DB、迁库、起服务或修改历史源码。默认登记表位于用户 state 目录，所有聊天共用；自定义时固定 `STOCKFISH_CHAT_STATE`，不能每个聊天另建。API 入口必须与 `/health/ready` 的实际 DB/state 身份一致。

[第二阶段](stock-fish/docs/SHARED_RUNTIME_INCREMENTAL_V1.md)已在本机恢复固定的 daily/review 58 包共享环境，通过原哈希锁、三个 doctor profile 及隔离应用/CLI/SQLite 验收，并登记聊天默认实例 `shared-daily`。恢复由独立的有界安装工具完成，登记本身仍只保存位置；解释器、配置与状态路径保存在私有登记中，不按聊天另建环境。`runtime-status` 确认定位与绑定，行情日期、完整性和原盘前结果另验；本次未启动或切换生产服务，也不代表 Qlib/Live 环境已恢复。源码发布、共享聊天登记和生产部署是分别记录的动作。

容量模板中的 20 GiB 总逻辑上限、10 GiB 余量与 2 GiB 单次预计增长只是初始配置示例，须结合真实用量及原 worker 预算核实。准入不是文件系统硬配额；API 返回只是入队，后台增长仍受原 worker 预算管理。没有切换生产部署；新控制文件的安全目录扫描与锁在本机 macOS/POSIX 验证，Windows 安全扫描不可用时拒绝写入，不宣称跨平台已验收。

此前日期化后验轮次采用独立运行环境、明确源码身份与新owned DB/state/artifact；生产服务、生产默认入口、权威库及调度未切换。源码提交不等于实例已升级。显式本地复制协议只支持封存RESULT的同字节不同路径复制件，RAW/receipt/producer/locator及完整终验仍严格；原物理身份和历史PIT没有恢复。

900秒late-completed新库14步已闭合，默认300秒实验使用两个独立owner/inode快照，继承900产品不能算300验收。V1因侧文件存在拒绝，V2另按空WAL、原样钉住SHM和完整源组合同准入；只复制主库，未删除/checkpoint侧文件，也未打开源SQL。V2初始化通过后单/双run均时间门禁失败，均无新增产品/ledger。等待时长、外层watchdog、磁盘余量和认证预算相互独立，不能调大等待冒充300达标。

运行与故障细节见[本轮交付](stock-fish/docs/CONTINUATION_DELIVERY_20261023.md)和[性能记录](stock-fish/docs/REAL_DAILY_ACCEPTANCE_PERFORMANCE_V1.md)。以下历史部署步骤保留，不构成自动切服授权。

## 已有部署优先

[独立 daily/review 环境合同](stock-fish/docs/ISOLATED_DAILY_RUNTIME_V1.md)采用官方 PyPI 工件及完整哈希锁，目标为原生 macOS x86_64、CPython 3.12。该合同记录前轮两个独立目录的 58 包安装、三个 doctor profile 和隔离应用烟测；旧目录清理后，本次固定共享环境按同一合同重新恢复验收，现状以上述第二阶段记录为准。其他平台须另行锁定验收，不能直接套用本锁。

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

[独立源码 bundle V2](stock-fish/docs/PREMARKET_FACTOR_CHECKPOINT_SOURCE_BUNDLE_V2.md)提供显式离线构建/校验工具：只创建新目录，不修改原 worker、request、budget，不自动更新 locator 或服务。既有32项隔离检查与源码快照离线校验分别保留。其后已按单独授权从该快照执行[10月21日盘前尝试](stock-fish/docs/PREMARKET_BOUNDED_RETRY_20261021.md)：3个官方目录完成后触采集读取限额，capture余额为0，退出4；没有核心run、名单或后验，未切换部署。继续采集不能仅凭其余请求/时间余额启动，须另行处理预算与授权。旧 cache 引用的三个 producer 原路径须继续保留，不能以副本替换旧物理身份。

新增[独立终端研究V1](stock-fish/docs/STANDALONE_PREMARKET_WORKFLOW_V1.md)：在已有项目Python环境、`stock-fish/`目录执行 `python scripts/stockfish.py premarket --config PRIVATE_CONFIG.json`。它直接使用本地原件及公开来源，不要求启动HTTP服务或创建业务DB，也不自动切换现有部署。配置模板位于 `stock-fish/config/premarket.example.json`；默认用户状态目录或显式 `--state-root` 保存私有数据、排他锁及每次任务的最终控制文件。各阶段预算、实际进程资源和退出码分别保存；Windows进程树采样需现有锁定psutil。命令退出与真实连续交付分开验收，不把源码发布当作服务已加载。

## R2 归档衔接与每日后验

新增[历史闭环部分验收](stock-fish/docs/CLOSED_SESSION_PARTIAL_ACCEPTANCE_20261020.md)区分当天准入与显式 `r2-closed-session-*` 历史重放。后者使用明确业务会话和历史资料截止，系统与作业的实际时钟不回拨，evaluator 显式以该历史截止判断可见性和成熟度；不能把历史重放改称当天交付。

使用[本轮统一操作与交付记录](stock-fish/docs/RUNTIME_R2_CLOSURE_DELIVERY_V1.md)中的显式本地命令，先鉴证 standalone 封存任务的完整来源、原代码字节、原 run/input 和五份正式导出，再向同一明确选择的服务库追加桥接凭证。原 run 与输入保持，不执行选股。只有完整 inline 原生归档在本版本范围内，保留原 `PROSPECTIVE_CAPTURED` 或 `RECONSTRUCTED_HISTORICAL` 覆盖类型；普通 JSON、历史 owner、时间证明或缺失原件不恢复来源能力，历史下载不抬升为过去可见。

命令要求已有当前结构数据库和 state，遇旧结构或活动服务锁立即拒绝，不自动迁库、回收其他任务或停启服务。每日后验必须使用已鉴证的精确当日 input、明确 run 列表、交易日收盘后实际截止；计划、待办和状态查询只读。默认每批最多 5 个 run，超限拒绝而不截断；失败重试需显式调用。没有新建定时任务或扩大采集预算。本轮真实与夹具验收范围及尚未完成的连续每日交付分别见交付记录。

大归档的[原执行记录](stock-fish/docs/REAL_DAILY_ACCEPTANCE_PERFORMANCE_V1.md)及[后续部分验收](stock-fish/docs/CLOSED_SESSION_PARTIAL_ACCEPTANCE_20261020.md)保留同次鉴证内 typed 复用、数据库双父全文 hash、全部来源及事务末终验。默认128行编码与显式闭环1024行 profile 分别绑定身份；快速路径仍限精确模型、tuple和精确行类型，其他整输入走原参考。仅全5期限 PENDING、无 DUE/UNKNOWN 且私有鉴证证明池非空时避免展开未借出的 run，空池继续原完整检查。9个 normalizer 文件不变。此前16文件对应676项组合回归通过，行批次与完整 C Unicode 编码不保证恒定内存。

历史10月16日单 run 在相同源码的隔离克隆中显式重试成功，随后幂等、三产品回读和独立 H1 通过；原正式失败不改判。双 run 两次仍触及300秒且无新产品/ledger。每次完整鉴证共享300秒、RSS 2 GiB、唯一原件4 GiB、实际读取8 GiB和4096文件上限；外层等待或子进程 watchdog 不扩大这些额度。默认5条或显式1–100上限不构成容量承诺，不能据单次通过切换生产。事务末终验在同一写事务内持锁完成，并发写与锁等待尚未经过生产负载验收。

### 日期化身份后验的显式入口

新增[日期化输入与真实数值 H1](stock-fish/docs/DATED_IDENTITY_NUMERIC_H1_20261024.md)仅在明确的新 owned DB/state 中运行，不切换服务。完成原归档鉴证后，`r2-dated-input-register` 显式选择 `--base-input-id` 和可重复的 `--identity-input-id`；随后使用 `r2-dated-late-plan`、`r2-dated-late-run`、`r2-dated-late-status` 或显式 `r2-dated-late-retry`。这些命令由既有 `scripts/stockfish.py` 分发，须同时给出 DB/state、`--source-protocol SEALED_RESULT_LOCAL_COPY_V1`、本地复制 manifest 及其 SHA；具体参数以对应 `--help` 为准。

新输入先追加提交，再写实际时钟的 fsync seal，最后登记完成；半成品保留且拒绝使用。计划及 worker 每次完整重认原来源、重构全输入并核 hash/lineage；发布前和写事务内末验不省略。seal 严格绑定原源代码路径、inode、七字段物理身份及实际 ctime。把测试源码快照复制到发布目录不会迁移旧私有 seal；新 source/owner epoch 必须重新鉴证、登记，不能复制 JSON 接权或回拨截止。

默认仍为 300 秒／2 GiB，显式 `FULL_ARCHIVE_900S_V1` 的正确性成功不能当默认性能通过。本轮独立 H1 的 300 秒验收与 native worker 是两个范围；性能候选、旧失败及生产未切换分别保留。

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

最新日采集与首次核心执行采用显式[有界入口V1](stock-fish/docs/FIRST_RUN_LATEST_CAPTURE_V1.md)：先核最新已闭市日缺口，只补原始所需请求；25日核心来源与67会话题材附件分阶段，UNKNOWN保留。`latest-plan/latest-sync`、`core-sources-plan/core-sources-build`和`core-first-run`均走已登记共享环境；新执行档位不改旧配置、策略、原件或已存run。首次计算与缓存复用分别验收，完整复权缺口仍须原来源补齐。
