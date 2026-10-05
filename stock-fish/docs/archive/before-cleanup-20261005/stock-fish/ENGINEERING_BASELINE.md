> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../README.md)查找。

# Phase 1 每日荐股闭环工程补充（2026-09-23）

【事实】新增冻结四类快照、轻量身份与只读日报，FOCUS/WATCH 的 T+1..T+20 每会话价格观察保留原基准、异常与分母；通过现有服务和 scheduler 接入原子保存、有界重试和独立后台工作。主动核心仍为 Python V2，Go 仍是影子，策略/evidence/readiness/旧 score 不降级。详见 [Phase 1 每日荐股闭环](../../../PHASE1_DAILY_RESEARCH_LOOP.md)。

【事实】快照/renderer/HTML 定向测试 20 passed、退出码 0，脚本语法检查退出码 0；其他本轮检查由统一交付记录补充。

【需要验证】本次源码交付未切换生产实例、未证明 provider 连续覆盖、盘前交付 SLA 或自然成熟 20 会话表现。下方测试数和运行记录继续保留原日期语境。

---

# Daily Primary Fast Decision v1 当前工程补充（2026-09-22）

Daily Primary normal latest refresh 现在在 `DAILY_PRIMARY_CRITICAL` capture 内加入 `DAILY_PRIMARY_FAST_DECISION_BOUNDARY_V1` evidence checkpoint。checkpoint 只复用现有 readiness 输出，判断已有证据是否足够结束第一次用户等待；当前 v1 只提前返回可审计 `NOT_READY`，不提前运行策略。若 `CURRENT_COMPARABLE_PRICE_COVERAGE` 低于 gate，且 pending adjustment-factor batch 的最大可能补齐仍无法达到 full-market/per-exchange gate，可提前停止长历史/maintenance 等待；若 missing factor batch 仍可能补齐，则继续 refresh。

本轮不改变 READY gate、eligibility、PIT、provenance、comparable basis、Stage Aware V2 任何 MA/slope/return window、Focus/Watch/Rejected/Unknown、排序、阈值、score 与 `MARKET_EDGE=NOT_ESTABLISHED`。`NOT_READY` 仍不启动策略、不发布正式推荐。详细边界见 [DAILY_PRIMARY_FAST_DECISION_V1.md](../../../DAILY_PRIMARY_FAST_DECISION_V1.md)。

---

# Daily Primary Critical Capture Boundary v1 当前工程补充（2026-09-22）

Daily Primary normal latest refresh 现在使用 `DAILY_PRIMARY_CRITICAL` capture purpose 进入既有 MainboardPipeline。该路径只执行 observation session、universe/master identity、eligibility/status、current raw price、comparable basis、adjustment factor evidence、V2 core history window 和 source/provenance receipts；`PublishedCandidateOutcomeCapture`、outcome maturity、theme、limit evidence、Tushare/funds enrichment、candidate/peer deep history 与 tracking artifacts 保留为 `BACKGROUND_RESEARCH`，不阻塞 readiness decision。RequestQueue 只新增 `purpose/purposes` metadata，不改变 provider identity、receipt、normalizer、history artifact 或 PIT 语义。

本轮不改变 Trend、START、ACCELERATION、HIGH_LEVEL_REPAIR、TREND_PULLBACK、FOCUS/WATCH/REJECTED/UNKNOWN、排序、阈值、score 与 `MARKET_EDGE=NOT_ESTABLISHED`。`UNKNOWN_HISTORICAL_STATUS`、缺 current price、缺 comparable basis 或缺 core history window 仍为 `NOT_READY`。详细边界见 [DAILY_PRIMARY_CRITICAL_CAPTURE_BOUNDARY_V1.md](../../../DAILY_PRIMARY_CRITICAL_CAPTURE_BOUNDARY_V1.md)。

---

# Daily Primary Reliability v1.1 当前工程补充（2026-09-21）

Daily Primary v1.1 只关闭 latest freshness closure：normal latest 解析 `expected_observation_session`，stale-but-complete input 以 `OBSERVATION_SESSION_STALE/STALE_INPUT` 阻断；refresh target 采用 expected session；refresh 后根据新数据合法可见时钟写入 `actual_decision_cutoff`，再重跑 freshness + readiness。显式 historical observation date / specific input 仍可按指定输入执行，不强制等于 latest。typed diagnostics 保留 `MISSING_AMOUNT`、`MISSING_TURNOVER`、`MISSING_ADJUSTMENT_FACTOR`，并把非阻断证据缺口与 REQUIRED blocker 分开。Trend、START、ACCELERATION、HIGH_LEVEL_REPAIR、TREND_PULLBACK、FOCUS/WATCH/REJECTED/UNKNOWN、排序、阈值、score 与 `MARKET_EDGE=NOT_ESTABLISHED` 不变；live provider E2E、多次 retry/timeout、scheduled automatic retry、性能、内存、Score、Enhanced Evidence 和 market-edge 仍为 OPEN/OUT OF SCOPE。

---

# R16-R1 当前工程补充

当前修订见 [R16-R1 工程说明](../../../R16_R1_ENGINEERING.md) 与 [复现命令](../../../REPRODUCE.md)。该说明优先于下方历史描述，最终验收由冻结源码后的外置 raw evidence 和页面端独立审阅决定。

---

# R05 本次整改入口（2026-09-06）

本节优先于下方保留的历史记录。当前精确规则、历史重建与复现入口见 [R05 规格整改与真实历史重建](../../../R05_RECTIFICATION_AND_REPLAY.md) 和 [原规格追踪表](../../../../strategy_spec_traceability.md)。保持 R05；旧 V2 的 0.40/0.30/0.30 权重及名次截断属于本次已纠正的不合规实现，不能继续作为策略定义。

当前 PV 使用原 0.45/0.35/0.20 权重，焦点使用 A≥2、R≥0.10、C≥O、CLV≥0.70 且没有名额限制。DAILY 原配置保持冻结。原错误版本运行、失败日志与审计历史保留，合规复盘按实现/规格身份隔离。

本次真实采集及回放证据在本机 `../review/r05-rectification/`；主窗口为 2026-08-31 至 2026-09-04，20 日尝试自 2026-08-10 起。原始下载时钟保留，历史资格受来源覆盖限制，不声称存档 PIT 或完整历史全市场。工程、供给、Docker、价格观察和投资优势分别见统一验收报告，原下方测试数量与 Docker daemon 状态是历史记录，不能当作本次结果。

本次最终核心回归实际为 752 passed、1 skipped；全仓收集仍因旧 MiroFish 缺少 locales/languages.json 失败。Docker runtime-09 的 23 条执行记录成功，镜像内 71 个关键文件与冻结宿主源码字节一致，隔离容器已停止，用户原服务未切换。随后仅为详情增加 SQLite 派生索引，相关 34 项回归和真实 API 复测通过。工程结果与真实行情及投资验证分别报告。

---

# R05 历史不合规实现与旧验收记录（仅供审计）

默认无账户候选仍保留原五族 `DAILY_V1`，不改名、不降级；参数状态仍为 `INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED`。R05 新增完整实验与研究反馈面：`GET /api/strategy-experiments/specs`、`POST /api/strategy-experiments/run`、分页 `GET /api/strategy-experiments/<run_id>/summary`、详情 `GET /api/strategy-experiments/<run_id>/items/<instrument_id>`、完整 JSON/CSV 导出、`/outcomes`、`/tracking`、`/review`、`GET /api/research-feedback/latest` 与 `GET /api/strategy-comparison/latest`。首页“R05实验策略”区域展示动态 PV 全样本、焦点名单、详情、导出、后验和研究周期按钮。

`PV_OBSERVATION_V1` 使用合格池 T 与前 5 个交易日完整同口径成交额、可比收盘和 high/low，按 Decimal 精确 midpoint percentile 计算 `S_PV=0.40*PR_mid(A)+0.30*PR_mid(R)+0.30*PR_mid(H)`，低于 98% 报 `LIMITED_SCOPE`，不凑满固定数量。`DNA_REVERSAL_V1`（兼容别名 `DNA_RERANK_V1`）要求 252 个交易日和 Wilson 下界可靠性门槛；`THEME_LIMITUP_V1`（兼容别名 `THEME_CONTEXT_V1`）要求有效题材成员不少于 5 且 peer 限价事件覆盖不少于 95%。缺历史、缺题材、未知限价不以 0 分替代。

`PV_FOCUS_PREVIEW_V0.1` 只是在 PV 全样本上的确定性焦点过滤：rank 1..30、`A>=2.0`、`R>=0.06`、`H>=0.035`、收盘区间位置 `>=0.70`。未选中样本保留在导出和后验分母中。后验支持 1/2/3/4/5/10/20 个交易日，研究 episode 支持 ACTIVE/COOLING/THESIS_INVALIDATED 等可变生命周期；调度器在收盘后发现同日已登记输入时自动排队实验后验与研究跟踪。

真实数据接线状态分开报告：Tushare Pro HTTP adapter 已实现端点、字段、参数校验、接口专属分页上限、token 缺失和脱敏响应；本机无 token，实测为 `AUTH_REQUIRED`。Eastmoney 单证券直接限价探针已按上海业务日与 f51/f52 字段校验接线；本机网络经代理失败，实测为 `PROXY_CONNECTION_FAILED`。本轮未取得新的完整真实主板行情快照；附件中提到的 `/mnt/data/.../actual-pv-capture-03` 与本机 `../review/r05-data-strategy` 不存在，不能声称真实 PV 全市场运行通过。`MARKET_EDGE=NOT_ESTABLISHED`。

Docker 交付包含 `Dockerfile`、`docker-compose.yml`、`wsgi.py`、`requirements-docker.txt`、`Start-StockRecommendation-Docker.ps1` 与 `start-stockrecommendation-docker.sh`。Compose 保持非 root、命名卷、只读根、回环端口、healthcheck、日志轮转和单 worker gunicorn/wsgi 调度器；脚本首次生成被忽略的 `.env.docker.local`，不打印 API key。当前主机 Docker CLI/Compose 存在，但 daemon 不可达，因此 `DOCKER_RUNTIME_VERIFIED=false`；静态 `docker compose config --quiet` 通过。

本轮验证：独立 R05 反例探针 8/8 通过；R05 产品测试 6/6 通过；R04 直接限价与 summary 测试 8/8 通过；调度相关组合测试 14/14 通过；排除 MiroFish 的全量业务回归 665/665 通过。直接 `pytest -q` 仍被旧 MiroFish 原型 `locales/languages.json` 缺失阻塞。

---

# R04 当前入口

默认无账户候选仍为 `YAOGU_V1.1.0` / `YAOGU_FEATURES_V1_R03`，五族公式、权重、阈值和目标语义不调参。R04 只改变数据证据链、默认读取体验和续采工具：新增 Eastmoney 单股 `qt/stock/get` f51/f52 的 `daily_limits_eastmoney` 直接限价路径，默认 DAG 为全主板当前对象登记直接限价批次；新增严格审计门槛的 `RULE_DERIVED_VERIFIED` 辅助逻辑；新增 CandidateRun 分页 summary 和单证券 detail API，首页不再默认拉取完整大记录；CLI 支持 `--rounds` 连续有限预算续采。

R04 实际低预算烟测为 `../review/r04-gpt55-closure/evidence/actual-capture-01/`：观察日 `2026-09-04`、当前主板 3,194 只、剩余请求 393、直接限价 pending 160、`consumer_status=UNAVAILABLE`、候选 0。单股直接限价探针因 `PROXY_CONNECTION_FAILED` 失败；这不是合法 EMPTY 或完整 DAILY。MARKET_EDGE 仍为 `NOT_ESTABLISHED`。

审阅索引：`../review/r04-gpt55-closure/final-audit.md`。本轮全量业务测试 `656 passed in 179.94s`。发布提交遵循父目录 `AGENTS.md` 的后续授权：检查通过后中文提交并推送发布仓库 main。

---

# R03 当前入口

默认无账户候选采用 `YAOGU_V1.1.0` / `YAOGU_FEATURES_V1_R03`，五族公式、权重、阈值不调优。精确三交易日情绪差、资格分区、统一receipt/行可见性、资金窗口及raw/effective/semantic摘要分别核验。全主板采集使用 `data_pipeline.py` 的分层持久请求队列；Web、API、调度与CLI共享该实现，不再默认截取六只涨停样本。

当前源在原分支未提交；无commit/push或Qlib wheel替换。旧榜、配置、模型及账户模拟安全契约保留。`../review/r03-realdata-closure/final-audit.md` 是当前验收索引，实际数据、历史研究、竞价和前瞻成熟分别报告。完整原始市场数据不随源码分发，包内有hash清单及最小真实样例。MARKET_EDGE=NOT_ESTABLISHED。

---

# R02-FULL 当前入口

默认页面与候选 API 使用 `stockfish_core/yaogu/` 确定性规则核心，五族配置为 `config/yaogu_v1.defaults.yaml`。实际业务规格见 `STRATEGY_SPEC_V1.md`。候选不依赖账户、LLM、Qlib模型；原组合模拟另保留。用户数据未覆盖，本轮测试和运行核验在独立state。

Git HEAD仍是 e7e8d5125913e5929d6611eaaffecf1c4bf41dbe；R02为未提交工作区变更。完整证据在父目录 `review/r02-fullscope/`，打包时一并提供。真实数据覆盖、历史评价和市场效果应分别阅读，不以fixture通过替代市场边际。MARKET_EDGE=NOT_ESTABLISHED。

下方为原工程基线，保留历史语境。

---

# StockFish 可验证工程基线

当前核心保留 StockFish Web/API/SSE 外壳，由 `stockfish_core` 统一处理可信数据、量化、确定性推荐与风险、持久任务和不可变历史。当前默认是研究模式；模型分数不表示已校准的概率或预期收益。

```text
Versioned RAW data / feature snapshots / security master / calendar
    -> QuantEngine / QlibQuantEngine
    -> ResearchEngine evidence and typed LLM audit (no final vote)
    -> RecommendationEngine + deterministic RiskEngine + portfolio constraints
    -> one FinalRecommendation -> Top20 / Top5 / best / UI / report
    -> SQLite Tracker -> append-only outcome evaluation

The same recommendation policy -> next-open fills -> cash / fees / NAV
```

## 开发与离线测试

使用 Python 3.12 和 Node.js 24；Node 仅用于检查保留页面的 JavaScript 语法。Qlib 使用安装的 `pyqlib==0.9.7`，不依赖相邻 Microsoft Qlib main checkout。

```shell
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -r requirements-dev.txt
python scripts/run_core_tests.py tests -q
python -m pip check
```

核心离线套件已验收 207 项；LLM 与供应商故障注入使用显式 Mock，不需网络或密钥。生产数据、模型、数据库和本机验收证据不随代码提交。测试运行器可兼容原本机 `../review/test-tools`，新环境通过 `requirements-dev.txt` 安装 pytest 即可。

## 本地服务

从 `.env.example` 建立本地 `.env`，保持 `HOST=127.0.0.1`，然后运行 `python app.py`。应用入口为 `/`；`/api/health` 分别报告 application、market_data、quant、model_registry、research、llm、tracker、jobs。

没有已登记模型、可信数据快照或 LLM transport 时，相应组件会明确不可用，不生成假行情或正常推荐。非回环绑定要求 `STOCKFISH_API_KEY`；还应明确配置 `STOCKFISH_ALLOWED_HOSTS`。HTTP 使用 Bearer 身份验证，不能上传信任声明、任意模型路径或伪造收益。单个 SQLite 数据库只运行一个应用任务服务，不启用调试重载或多 worker。

## 状态与输入

| 项目 | 默认位置 / 配置 |
|---|---|
| SQLite | `data/stockfish.sqlite3` / `STOCKFISH_DB_PATH` |
| Core artifacts | `data/core` / `STOCKFISH_STATE_DIR` |
| Registry | `<core>/model_registry` |
| Quant runs/snapshots | `<core>/quant` |
| Qlib data | `../data/cn_data` / `QLIB_DATA_DIR` |
| RAW observation pointer | `<core>/active_snapshot.txt` |

可信输入见 [DATA_CONTRACT.md](../../../../stockfish_core/DATA_CONTRACT.md)。本地管理员通过 `scripts/core_admin.py --help` 登记已核验的 observation/snapshot/model；HTTP 请求只能引用已有 ID。数据更新发布独立快照，需要显式切换路径；不覆盖活跃数据。

默认推荐策略 `quant-hard-risk-v2`、风险 `risk-v2`；风险检查包含真实 RAW 标价、新鲜度、可卖股份、现金、持仓数、个股/行业敞口及手数。空目标 `{}` 可清仓；不可用目标 `null` 不调仓。回测执行还按当时已知开盘价和费用后 NAV 限制买入，不能让跳空突破硬约束。

历史记录及修订不可覆盖，评价单独追加并从已保存的 RAW 路径重新计算。支持 7/30/90 日观察；未到期不填收益。任务发布与成功终态原子保存；中断恢复为 INTERRUPTED，重试创建新 ID。

## 使用边界

- 原生 Alpha158 适配范围是已安装的 CSI300 特征与成员历史，不等于完整全 A 股 RAW/可交易性覆盖。
- 已审阅的历史作者模型保持 RESEARCH_ONLY；clean CPU train/refit/purged walk-forward 的工程正确性不等于投资 OOS 验证通过。
- 免费来源无法保证的财务 PIT、公司行动、交易状态保持不可用；数据商业权利需另行确认，见 [provider_classification.json](../../../../stockfish_core/provider_classification.json)。
- Docker 默认权限已收紧，但当前已执行的部署验收是 Windows；不宣称 Linux 容器已运行认证。MiroFish、旧 Bot 和独立最终排名阶段保持实验隔离。
- `scripts/capture_acceptance.py`、`verify_runtime.py`、`native_quant_smoke.py` 是原工作站验收工具，引用当地审阅目录/模型和历史记录；不作为新 clone 的通用测试入口。使用上面的离线测试命令。
- SQLite/产物备份、公共部署的 TLS/用户权限/负载验证及真实 LLM transport 配置由部署环境负责。没有实盘下单或收益承诺。
