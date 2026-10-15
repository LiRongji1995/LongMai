# 运行安全、短周期后验与性能第一阶段

本轮依据架构与量化审视的优先级实施。源码发布、运行环境可用、真实数据闭环和投资验证分别验收；本轮不切换运行服务、不改真实数据库，不改变主动 SAM V2、DAILY、R1/R2 选股公式、阈值、配置及旧 run。参数仍为尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`。

## 运行与安全基线

标准库入口 `scripts/runtime_doctor.py` 显式检查 `rules / daily / qlib / review / live` 依赖 profile。默认检查当前解释器，也可用 `--python` 或显式 `--python-locator` 选择已安装解释器。它逐包在独立子进程实际 import，记录 Python、架构、安装位置、原生模块和需求文件摘要；不只检查发行包元数据。Qlib profile 另要求 pyqlib 0.9.7、wheel/RECORD、非 editable 及实际 import 来源一致；rules 不要求 Qlib 或应用 LLM。

```sh
# 从仓库根执行；PROJECT_PYTHON 是调用者明确选择的已安装解释器
"$PROJECT_PYTHON" -B stock-fish/scripts/runtime_doctor.py --profile rules --json
"$PROJECT_PYTHON" -B stock-fish/scripts/runtime_doctor.py --profile daily --json
"$PROJECT_PYTHON" -B stock-fish/scripts/runtime_doctor.py --profile live --python stockfish-live/.venv/bin/python --json
```

返回 0 表示所选 profile 的本次依赖与身份检查通过，1 表示失败；参数错误返回 2。报告包含绝对安装路径，应保留在私有目录。工具不导入应用、不打开业务 DB、不安装依赖、不启动服务、不执行联网测试。子进程在 site 初始化前安装 Python audit guard，阻止受审事件中的写入、DB、进程与网络访问；这不是针对恶意原生扩展的操作系统沙箱。urllib3 导入时尝试创建本地 socket 探测 IPv6 会被阻止，并单列 warning，不能据此证明联网成功。源码与 schema 摘要是选择性身份清单，不是完整构建闭包或运行实例身份。

调度器新增 `SCHEDULER_PROGRESS_HEALTH_V1`：最近开始、结束、成功时间，连续失败数、是否正在轮询，以及距成功轮询的单调时钟年龄。在没有成功轮询、线程退出、距成功超过三个 interval、轮询失败或失败诊断无法持久化时，`/health/ready` 返回未就绪。成功轮询后恢复。`/health/live` 仅证明进程响应；这些状态不证明行情、DAILY 或投资策略可用。进程创建时间无法读取时状态说明降为 `PARTIAL` 并记录异常类型，不再使整个调度说明崩溃。daily 依赖清单补充既有锁定版本 psutil 7.2.2。

Research 的 `/health/ready`、`/health/deep` 及 API 别名统一使用原有鉴权与同源检查；liveness 保持公开。每个应用实例最多接纳两条 SSE，为现有四线程 WSGI 入口保留短请求容量。超额返回 429 和 `Retry-After: 1`；生成器结束、异常、断开、HEAD 及未消费关闭都释放槽位。多进程部署时上限按各自进程计，本轮没有改变服务器线程数或部署方式。

Live 尚无远程身份认证，因此 HTTP 和 WebSocket 都要求实际客户端及监听地址为 loopback、合法 Host 和同源 Origin；不信任 forwarded 头。CLI 拒绝 `0.0.0.0`、`::` 和远程地址，`localhost` 固定绑定 `127.0.0.1`。默认每个 app 最多 16 条 WebSocket，超额在 accept 前拒绝握手：ASGI 内部 code 为 1013，现有 Uvicorn 将握手前拒绝映射为 HTTP 403，网络客户端不一定收到 WebSocket 关闭帧。断连或取消后释放容量。原测试使用显式 loopback，没有生产 `testserver` 例外。

Tushare 普通 REST 的[官方说明](https://tushare.pro/document/2?doc_id=40)仍给出 HTTP；尚未取得同一合同 HTTPS 的确认，不能用不同的 MCP 协议替代。因此新增凭据请求默认以 `TUSHARE_HTTPS_REST_NOT_VERIFIED` 失败，实际请求数 0。未来端点启用需要受审源码变更，不能由环境变量任意指定。已有完整 checkpoint 允许只读复用，明确本次 `NOT_REQUESTED / READ_ONLY_CHECKPOINT_REUSE`，原 HTTP/HTTPS 回执和原文件不改写。传输 helper 限固定 HTTPS 域、TLS 验证并禁止重定向；上游回显 token 在解析后的键和值中脱敏，包含引号、反斜线及非 ASCII 情况。此修复会使未缓存的 Tushare 采集明确不可用，其他提供方不因此变成已验证替代来源。

## R1 / R2 独立后验闭环

[SHORT_HORIZON_OUTCOME_V1](SHORT_HORIZON_OUTCOME_V1.md)使用原有作业和权威服务库，在一次原子完成中追加 outcomes、tracking、review 三种不可变产品。提交需显式指定保存 run 与已登记后验 input，支持 H1/2/3/5/10。原 run 的历史 `NOT_IMPLEMENTED` 字段不被覆写，服务层报告新版本能力；重复输入、可见性和成熟度保留首个修订，新的事实形成新修订。

逐交易会话核对冻结精确参考价、复权 basis、证券状态、日历与父来源实际可见时间。日历目前有限于已登记的 2026 沪深规则，不把漏日压缩为更近的 H1；缺日历时成熟度为 null 和 UNKNOWN，已知尚未收盘为 PENDING，窗口完整才为 MATURE。不可验证覆盖拒绝，历史重建不放宽评估时点后的取得时间。全池、research_selected 与正式 FOCUS 分开，个股详情与整体产品使用同一修订。

此闭环输出标价变化及日线 MFE/MAE 上下界，不是实际成交收益；不推断日内先后顺序、T+1 可成交性、交易费用、资金容量或市场优势。单个 run 的横截面不当成独立时间样本。standalone premarket 归档尚未完成带来源身份的服务库衔接，短周期也未进入旧自动采集队列；不能声称真实每日自动后验已经贯通。具体 API、修订选择、数据前提和容量限制见独立契约。

## SQLite 查询与迁移

V1 建表文本及其 checksum 保持不变，追加 V2 迁移：`documents(namespace,created_at,document_id)` 支持按命名空间稳定分页，`job_events(job_id,event_id)` 支持作业事件读取。原 `CandidateService` 已创建的 `strategy_item_observation_lookup` 纳入同一迁移登记，不创建重复的 run 表达式索引；同名但定义不同的索引会拒绝迁移。初审仅检查基础建表 SQL 时遗漏了这个原有索引，本轮据完整启动路径修正判断。

新 `documents_for_run` 在同一只读事务中限定 namespace/run/可选证券，仍逐件验证原 payload hash，并保留全部修订、NULL 和稳定排序。个股详情、策略后验/跟踪、复盘索引、候选后验和竞价复盘改用这一读取方法；跨 run 的统计聚合不被截为单 run。它显式选择现有 run 索引，避免 SQLite 为省排序而改扫整个 namespace；旧只读 V1 库没有该索引时可兼容读取，不会因此触发迁移。

隔离基准使用 12,000 份 `TEST_FIXTURE` 文档、300 个 run、SQLite 3.53.1；V1 对照已包含原 Candidate 索引，每项预热 3 次再测 20 次。先后顺序执行，操作系统缓存未控制，以下只说明该样本，不外推真实 API 的 P95。

| 查询 | V1 median | 本轮 median | 解释 |
|---|---:|---:|---|
| 命名空间元数据 100 行页 | 12.613 ms | 0.175 ms | 去掉全范围临时排序 |
| 单作业事件 | 1.433 ms | 0.492 ms | 全表扫描改为 job 索引查找 |
| 单 run 原始 SQL | 0.181 ms | 0.236 ms | 没有证明此项加速；目标 run 内仍需排序 |

从“读全 namespace 后过滤”改为 scoped helper 的结果完全一致；`checked_payload` 调用由 12,000 降至 40。单次观测为 0.819 秒与 0.00258 秒，不能当作延迟分布。初版索引导致 run 查询由约 0.120 ms 回退至 26.508 ms，已通过明确索引选择修复并保留失败证据；实际 helper SQL 的 EXPLAIN 另有回归断言。

迁移只在独立测试库执行过。真实部署前先作一致备份并在新恢复目录验迁移、可用磁盘空间和时间；索引会改变 SQLite 物理字节，不能继承旧快照/owner 的物理身份。旧 payload、content_hash 和 V1 migration checksum 不变。旧 Python 代码会拒绝 schema V2，回退须使用迁移前备份恢复的新目录，不能删 migration 记录来伪装降级。Go 读取器与备份程序的静态边界已检查；本轮未构建、切换或重认证任何真实 Go 包和历史库。

## 检查与边界

所有工程测试使用独立 `TEST_FIXTURE`，没有真实行情续采、生产 DB 迁移或策略回测。完整实际命令、绝对解释器路径、退出码和 stdout 保存在未发布的本轮审阅目录；本文件用环境变量代替私人路径。现有 Python 环境的损坏和缺包需要独立环境修复，不能以测试环境通过推断默认应用环境通过。

Research 测试使用显式登记的现有 premarket Python，先载入其可工作的 Pydantic，再将现有开发环境的 pytest 依赖加入路径。没有安装或修改任何环境。以下从 `stock-fish/` 执行，`PROJECT_PYTHON` 和 `EXISTING_PYTEST_SITE` 必须由调用者显式指定，不能直接复制变量名作为实际路径：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
"$PROJECT_PYTHON" -B -c \
'import pydantic,sys,runpy; sys.path.append(sys.argv.pop(1)); runpy.run_path("scripts/run_core_tests.py",run_name="__main__")' \
"$EXISTING_PYTEST_SITE" \
tests/contracts/test_http_security_capacity_v1.py \
tests/contracts/test_tushare_transport_security_v1.py \
tests/integration/test_scheduler_progress_health.py \
tests/integration/test_health_contract_separation.py \
tests/r02_product/test_scheduled_outcomes.py \
tests/r05_product/test_daily_research_scheduler.py \
tests/r05_product/test_strategy_experiments_and_tushare.py \
tests/r05_product/test_rectified_transport_and_deployment.py \
tests/unit/test_query_index_migration.py \
tests/unit/test_durable_storage.py tests/unit/test_durable_jobs.py \
tests/r02_correctness/test_backup_restore.py tests/unit/test_runtime_doctor.py \
-q -p no:cacheprovider
```

该合并检查 **184 passed、1 skipped、20 subtests passed，退出 0**。唯一 skip 为已有 Windows 专用检查，没有扩大 skip。R2 合并回归使用同一包装，测试文件为 `test_short_horizon_outcomes_v1.py`、`test_short_horizon_strength_r1_integration.py`、`test_short_horizon_strength_r2_integration.py`（均位于 `tests/yaogu/`）和 `tests/r05_product/test_rectification_tracking_service.py`，最终 **76 passed，退出 0**。包括原始价格响应早于收盘、价格父角色未知的拒绝反例，以及早期辅助因子和实际晚下载历史资料的合法正例；没有为 fixture 放宽价格时点规则。

Live 从仓库根使用其现有 Python 3.12：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=stockfish-live \
stockfish-live/.venv/bin/python -B -m pytest -q -p no:cacheprovider \
stockfish-live/tests/test_local_access.py stockfish-live/tests/test_api.py \
stockfish-live/tests/test_history_dashboard.py stockfish-live/tests/test_cli.py \
stockfish-live/tests/test_cli_research.py
```

结果 **211 passed，退出 0**，保留一条既有 Starlette TestClient/httpx 弃用 warning。三组不重叠的顶层用例合计 **471 passed、1 skipped**，20 个 subtest 单列；不叠加独立复核和重复 smoke 的通过数。

仅同步本轮 37 个文件后的发布检出另行复核：Research 安全合同两文件、调度进展、索引迁移和短周期三文件共 **139 passed，退出 0**；Live 的 `test_local_access.py`、`test_api.py`、`test_cli_research.py` 共 **134 passed，退出 0**，同一既有 warning 保留。调用方式沿用上列包装与依赖解释器，cwd 和模块搜索路径明确指向发布检出。开发目录与发布目录间另外 4 个原有差异未同步；此轮复核针对最终发布的源码组合。

真实环境只读诊断与工程测试分别报告：

| 已有环境 / profile | 退出码 | 实际结果 |
|---|---:|---|
| 默认 `.venv` / rules | 1 | baostock、numpy、pandas、pydantic、pydantic-settings 导入失败 |
| 显式 premarket locator / rules | 0 | 通过依赖和身份检查 |
| 同一 premarket / daily | 1 | 缺 psutil，其余 12 包通过；只更新依赖清单，未安装 |
| Live Python 3.12 同时作为 doctor 主机与目标 / live | 0 | 通过 |
| Qlib / 真实环境 | 未执行 | wheel 身份策略只有 fixture 回归，不能宣称真实 Qlib 已就绪 |

首次 Live doctor 使用缺少 `tomllib` 的系统 Python 作为主机而失败，后用显式 Live Python 3.12 重跑通过；失败记录保留。R2 扩大回归也发现旧后台测试的 20:30 启动时间落在主任务保护窗口，原调度逻辑与本轮前相同；测试改为已错过当天主任务后的 21:30 启动，仍执行真实保护逻辑，105 项提交、10 秒不重试、61 秒重试及 106 条事件断言完整保留。

## 后续交付顺序

1. **完成可运行环境与部署验收**：在独立新环境补齐 daily 依赖，核对 Python 与原生 wheel 架构；rules、daily 及所需 profile 都经 doctor 后，再做真实进程身份、readiness 和只读查询验收。已有环境、运行服务与数据库继续保留。Tushare 需先取得并核实普通 REST 的 HTTPS 合同。
2. **贯通真实 R2 后验**：为 standalone 归档建立原始身份与来源可验证的服务库桥接，再接显式预算约束的未来会话证据登记和定时闭环；每个未来会话独立登记实际取得时间。验收包含成熟、未成熟、停牌、缺证据和修订，不能补零。2027 日历需新版本来源登记。
3. **验证投资假设**：累积前瞻、多信号日期的真实样本，再做按日期分组的样本外检验、市场/阶段基准与多重尝试记录；另行设计 T+1、涨跌停、成本、流动性与容量的可执行模型。当前描述性后验不证明收益或市场优势。
4. **推进端到端性能**：先测 API 与作业各阶段的 P50/P95、等待时间、JSON 解码量、数据提供方耗时与内存峰值，再确定下一个瓶颈。按 run 读取仍会解码该 run 的全部修订，后续可增加明确 latest/revision 分页契约。67 个会话的上游 I/O 和既有长盘前任务本轮未重测，不能宣称整体变快。
5. **部署前存储演练**：在生产备份的新恢复目录测 V2 索引空间、锁等待和迁移时间，再验证 Python/Go 共同读取、备份及回退。生产库升级、Go 运行认证、Windows、全仓回归、真实端到端流程与投资回测均不在本轮已完成检查内。

本轮仅同步明确清单中的源码、测试和文档到发布检出；原开发目录其他修改和发布目录的原有版本差异保留，不发布运行数据、私有审阅包、凭据或模型。
