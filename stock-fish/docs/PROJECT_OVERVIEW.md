# Daily Primary Reliability v1.1 当前修订入口（2026-09-21）

本节优先于下方 R16-R1/R16 历史快照。Daily Primary v1.1 在既有可靠性屏障外补齐 latest freshness closure：normal latest 请求先用交易日历解析 `expected_observation_session`，内部完整但 observation date 陈旧的 input 会以 `OBSERVATION_SESSION_STALE/STALE_INPUT` 阻断；refresh 目标使用 expected session，refresh 完成后以新数据合法可见时钟更新 `actual_decision_cutoff` 并重跑 freshness + readiness。显式 historical observation date / specific input 路径保留历史语义，不被 latest barrier 误伤。GLOBAL REQUIRED 未就绪时终态为 `NOT_READY`，不启动策略、不发布正式推荐；instrument-level 缺失进入 typed diagnostics，amount/turnover/adjustment factor 保留各自 reason，并由既有策略语义保持 `UNKNOWN`，不静默改成 `REJECTED`。内部执行、持久化或导出失败在 readiness 通过后保留为 `FAILED`。本修订不改变 Trend、START、ACCELERATION、HIGH_LEVEL_REPAIR、TREND_PULLBACK、FOCUS/WATCH/REJECTED/UNKNOWN、排序、阈值或 `MARKET_EDGE=NOT_ESTABLISHED`；live provider E2E、多次 retry/timeout、scheduled automatic retry、性能 <60 秒、内存回归、Go Phase 2、Score、Enhanced Evidence、market-edge 和历史重审仍不属于本轮。

---

# R16-R1 当前修订入口（2026-09-14）

本节优先于下方 R16 历史快照。当前工作限 R16-R1：Contract/Comparator 修复、生产 oracle、corrected 历史、真实外层 trace 和有限重复工作优化；不开始 R17、不调选股语义、不启用 Go 生产。详见 [工程边界](R16_R1_ENGINEERING.md)、[生产 oracle](R16_R1_PRODUCTION_ORACLE.md)、[历史构建](R16_R1_CORRECTED_HISTORY.md) 与 [复现](REPRODUCE.md)。resolver/派生缓存使用 R16_R1_PIT_INSTANT 身份，旧输入和证据只读保留。实际 readiness/official/score/运行进程必须读取本次 post-commit 审计，不能沿用下文日期化数值。

---

# StockRecommendation 中文项目综述

本文是无需旧聊天历史即可阅读的项目讨论材料。日常执行先读短版 [Codex 日常研究操作协议](CODEX_DAILY_RESEARCH.md)；用户入口见 [README](../README.md)，项目执行约定见 [AGENTS.md](../AGENTS.md)。本文描述已经存在的规则、入口与限制，不授权改变策略，也不等于独立源码审计。

## 1. 文档身份、核验基础与版本

**最后核对：2026-09-10，Asia/Shanghai。** 当前交付在 `D:/stockfish-qlib-quant-system` 克隆中完成 R16 Go Migration Phase 0：在 R14 Daily Fast Path 与 R15 Derived Feature Store V2 之上新增 Contract V1、canonical source resolver、cache lifecycle、E2E trace、真实历史稳定性审计和 Go shadow equivalence 基础。阶段感知主动核心仍是 Python `STAGE_AWARE_MOMENTUM_V2`，日常 primary 与完整 research comparison 继续分离，`DAILY_RECOMMENDATION_SCORE_V1` 继续作为只读后验消费者；Go core 尚未接入正式荐股路径。`STAGE_AWARE_MOMENTUM_V1` 冻结为历史只读兼容，上一版 `THEME_CAPITAL_TREND_V1` 保留为对照。动态数据、任务、进程和模型状态会在此时间以后改变，使用前应重新读取小型状态摘要。

| 对象 | 核对基础及含义 |
|---|---|
| 当前交付仓库 | `D:/stockfish-qlib-quant-system`；远端 `git@github.com:LiRongji1995/stockfish-qlib-quant-system.git`；R16 在 `main` 上完成后推送，并从最终 commit 字节与提交后证据生成 review bundle |
| 本轮核心版本 | `STAGE_AWARE_MOMENTUM_V2`；R16 仅新增迁移契约、来源不变量、缓存生命周期、E2E trace 与 Go Phase 0 shadow fixture，不改策略语义；配置 [stage_aware_momentum_v2.yaml](../config/stage_aware_momentum_v2.yaml)，实现 [stage_aware_momentum.py](../stockfish_core/yaogu/stage_aware_momentum.py)，Go 边界 [ADR_GO_CORE_BOUNDARY.md](architecture/ADR_GO_CORE_BOUNDARY.md)，契约 [FEATURE_EVIDENCE_CONTRACT_V1.md](architecture/FEATURE_EVIDENCE_CONTRACT_V1.md)，快路径说明 [R14_DAILY_FAST_PATH.md](R14_DAILY_FAST_PATH.md)，R15 证据见 [R15_TRUE_INCREMENTAL_ARCHITECTURE.md](../../R15_TRUE_INCREMENTAL_ARCHITECTURE.md) 与 [R15_PERFORMANCE_RESULT.md](../../R15_PERFORMANCE_RESULT.md)，策略说明 [STAGE_AWARE_MOMENTUM_V2.md](STAGE_AWARE_MOMENTUM_V2.md)；V1 只读历史说明见 [STAGE_AWARE_MOMENTUM_V1.md](STAGE_AWARE_MOMENTUM_V1.md) |
| 每日成绩版本 | `DAILY_RECOMMENDATION_SCORE_V1`；实现 [recommendation_scoring.py](../stockfish_core/yaogu/recommendation_scoring.py)，说明 [DAILY_RECOMMENDATION_SCORE_V1.md](DAILY_RECOMMENDATION_SCORE_V1.md)；仅读保存的信号和追加 outcome，不参与策略计算 |
| 历史运行证据 | 旧 `D:/StockRecommendation` 服务与发布检出证据保留为历史核对材料；其 PID、端口和镜像身份是当时事实，使用前必须重新核对 |
| 本文规则身份 | `YAOGU_V1.1.0` / `YAOGU_FEATURES_V1_R03`；旧实验实现 `R05_STRATEGY_EXPERIMENTS_V4_CANONICAL_IDENTITY`；上一版新增实现 `R06_THEME_CAPITAL_TREND_V1`；当前核心策略保持 `STAGE_AWARE_MOMENTUM_V2`，R14/R15 execution profile 为 `DAILY_PRIMARY_FAST` / `RESEARCH_FULL`，R16 派生缓存身份为 `DERIVED_FEATURE_STORE_V2_FAMILY_SPECS_R16_CANONICAL_SOURCE`，V1 legacy ignored/read-only；初始参数状态 **尚未验证**，`MARKET_EDGE=NOT_ESTABLISHED` |

开发工作区、发布检出与运行进程分别核验。本次任务只提交当前授权文件，不覆盖私人数据库、缓存、报告或旧运行目录。正式 DAILY、旧 PV、旧焦点、DNA、THEME 的规则与历史结果保留；新增策略不以国芳集团或金健米业是否入选作为验收标准。

R14 修复日常 primary 仍被完整多臂实验拖慢的问题，新增 `run_active_daily_strategy(...)`、compact daily persistence、`/api/daily-primary/run` 和 scheduler 快路径。冻结 benchmark input 上，saved research full 的 V2 business hash 与 cold/warm daily fast path 完全一致；R14 same-input warm 为 59.760 秒、peak RSS 992,960,512 bytes，但它只是同一 frozen input 二次运行，不是 true T->T+1 daily incremental 证据。R15 将派生缓存升级为 `DERIVED_FEATURE_STORE_V2`：feature spec 与 strategy decision spec 分离，key 不再绑定 whole `input_hash`、whole `history_artifact_hash` 或 request `as_of_session`，而绑定 feature family、instrument、feature session、required source range 与 source dependency fingerprint；同时消除 `_limit_market_status` 的 percentile O(N^2) 热点。真实 2026-09-08 -> 2026-09-09 benchmark 因历史 consumed source dependencies 大面积变化，T+1 `reuse_ratio=0.0`、总时长 263.098 秒，最终标记 `NOT_MEETING_R15_SLA`。R16 将 source dependency 进一步收敛为共享 canonical resolved source view，并将 history stability audit 改为带进度、checkpoint 和 targeted canonical 复核的批量/set-based 对比；审计发现既有 2026-09-09 T+1 immutable history artifact 可出现 prior comparable factor composite rows 丢失，后续构建已通过 `history_base_ref` 保护，但旧 artifact 不自动改写。交易参数、Setup、优先级、Focus gate 和 score 公式均保持 V2。R12 的 score/history/prospective 修复仍见 [R12_SCORE_INTEGRITY_REPORT](R12_SCORE_INTEGRITY_REPORT.md)、[R12_HISTORY_ARTIFACT_REPORT](R12_HISTORY_ARTIFACT_REPORT.md)、[R12_PROSPECTIVE_INTEGRITY_REPORT](R12_PROSPECTIVE_INTEGRITY_REPORT.md)、[MEMORY_PROFILE](MEMORY_PROFILE.md)、[V2_DATA_READINESS](V2_DATA_READINESS.md)、[V2_HISTORICAL_REPLAY_REPORT](V2_HISTORICAL_REPLAY_REPORT.md) 与 [V2_PROSPECTIVE_VALIDATION_PROTOCOL](V2_PROSPECTIVE_VALIDATION_PROTOCOL.md)。

历史架构在同一权威 SQLiteStore 保存不可变内容寻址行与会话 manifest；日 MarketInput 保存近期证据与历史 ref/hash，按证券读取长期窗口。512×315 合成基准峰值 RSS 从约 2.59 GB 降至约 160 MB。真实 321,622,653-byte 旧输入已流式迁移，迁移峰值约 131 MB；此测量与合成旧路径基准分开报告。每日增量复用旧行，不重复持久化整个315日历史。

**2026-09-09 内存回归补充：** 四项补采取得9/8、9/4的目录和日线，08:58冻结输入42,538,681 bytes、95个历史分区，无9/9行情。09:00作业发生 `MemoryError` 后，仅修复市场聚合复用和哈希展开，未改阈值；重跑耗时1,414.672秒，在09:42:54 cutoff下于10:05:09生成、10:06:28.578864成功持久化，官方分页摘要及V2/PV CSV的hash/order/count核验通过。

V2全部3,041只中FOCUS0、WATCH0、REJECTED2851、UNKNOWN190，仍为 `SIGNAL_DATA_UNAVAILABLE`；PV只有284/3041（约9.339%）可评估，保留50条原序结果及1条肯特催化PV预览，不能冒充V2焦点。独立只读MA20/60/120/250覆盖均为0，MA20全市场缺8/12、8/24、8/27、8/28。

真实RSS峰值从失败时2,424,721,408升至完成时2,646,999,040 bytes，private峰值仍约3.427GB；单次哈希临时分配下降不代表端到端峰值下降，存储仍有大峰值，完整JSON deepcopy导出未验。最终核心984 passed、1 skipped（227.21秒）。新epoch正式尝试/可评价日数均0，旧9/8 primary质量不改写；10:07核验8001/PID40944及scheduler健康、今晚20:30计划存在。详情见 [R12 内存回归补充](R12_MEMORY_FOLLOWUP_20260909.md)。

本轮最近20日真实重建尝试已有1日构造，但当前仍无可评价的 price/trend 日。缺失日期化 master、当日 RAW 和连续核心历史窗口仍是实际证据阻塞；theme/capital 不完整不会阻塞 price/trend scope，历史数据不进入正式前瞻计数。队列逻辑请求按 provider/endpoint/规范参数/session 去重并优先当前身份、价格与长期核心历史，保留已有尝试与原预算。

持久服务使用单进程 Waitress、登记端口 `127.0.0.1:8001`、既有 DB/state 与唯一 scheduler。20:30 是触发时点，协议 SCHEDULED_2030_ACTUAL_COMPLETION 使用实际采集完成 cutoff，当前运行/epoch/计数以 [R12_PROSPECTIVE_INTEGRITY_REPORT](R12_PROSPECTIVE_INTEGRITY_REPORT.md) 的带时间核验为准。不可提前声明首个可计数正式信号；运行状态 API 与截止后只读检查入口均保留。

实验模块加载时检验批准配置的字节 SHA-256：`r05_strategy_experiments.yaml` 为 `8cd729cab31f80ec84d6486c733165360ba264fc3efb2d70c0c69f5a78148462`，`r05_completion_requirements.yaml` 为 `203325cad6cde76ca9399353af163e9b122b73b1ab2d3c8ecbfd1878fb3f5e11`。冻结 DAILY 配置为 `5863af482cc396a000477fdfcfe694aac162a935ece7fa11279a2e3ecc0376b6`。上游研究版本与批准文件锁见 [r05_source_lock.json](../config/r05_source_lock.json)。这些哈希标识内容，不是抵御具有完整本机写权限者的安全认证。

## 2. 产品目标、研究理念与边界

系统研究沪深主板普通 A 股，形成“发现候选 → 排序 → 解释依据 → 风险和证伪提示 → 持续关注 → 研究复盘”的闭环。默认排除创业板、科创板、北交所、B 股、ST、退市整理和未达到上市交易日数的证券。最终身份靠日期化证券证据，不能只看代码前缀。

用户偏好量价、波动、数天数板、板块联动、资金与龙虎榜。这些经验决定研究方向，不证明某种模式必然有效。当前公式是明确、可复算、可证伪的初始假设，统一标记 `INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED`。`MARKET_EDGE=NOT_ESTABLISHED`：工程可运行、真实历史有价格变化、程序能生成名单，都不等于存在已验证投资优势。

名单数量由资格与正式条件决定，可以为零。完整名单、焦点子集、网页分页、回复节选与 AI 意见是五个不同概念；不得凑 17、20 或 30 只，也不得因模型上下文预算删掉尾部。排序分不是上涨概率、涨停概率或交易胜率。高波动获得排序贡献不代表更安全。

用户自行决定是否交易和如何下单；日常研究不要求账户、现金、持仓或虚拟本金，不接券商和自动交易。项目保留的旧组合研究模块是另一条受约束路径，不能反过来成为本日常研究的前置条件。

普通研究委托允许在已有来源授权与预算内查询、必要增量取数、正式计算、保存报告、更新后验与解释事实。修改正式公式、窗口、门槛、权重、统计口径、源码、依赖、付费预算或发布代码需要明确实施授权。一次获准工程提交不赋予以后普通“给我名单”请求自动改代码、提交或推送的权限。

## 3. 实际技术架构

当前核心是 Python 3.12、Flask、Pydantic 数据契约、SQLite 持久存储、静态 HTML/CSS/JavaScript 页面以及进程内持久任务调度。Node 不是日常规则计算的依赖。主要链路如下：

```text
已登记交易日历、来源适配器与合法输入
  → 持久采集请求队列、原始回执、规范化与时点校验
  → 不可变 MarketInput / 来源与资格分区
  → CandidateService
      ├─ DAILY / FUNDS / PRICE_ONLY：engine.py + features/events/themes/emotion
      ├─ Daily Primary Fast Path：active STAGE_AWARE_MOMENTUM_V2 + DERIVED_FEATURE_STORE_V2
      └─ Research Full：PV / DNA / THEME / THEME_CAPITAL / V1/V2 comparison
  → SQLite 保存唯一运行、摘要投影、单股证据、任务状态
  → 后验、episode、复盘另行追加
  → Codex 通过现有 API 与轻量封装读取或触发；网页是可选视图
```

| 模块 | 实际职责与边界 |
|---|---|
| [app.py](../app.py)、[api.py](../stockfish_core/api.py) | 本地 Web/API 外壳；请求只能选择已登记输入 ID，不能通过 HTTP 声明任意文件可信或上传假收益 |
| [service.py](../stockfish_core/service.py)、[candidate_service.py](../stockfish_core/yaogu/candidate_service.py)、[daily_primary_readiness.py](../stockfish_core/yaogu/daily_primary_readiness.py) | 组装服务、调用正式计算、原子发布与持久结果；日常 primary 先做 GLOBAL REQUIRED / instrument-level readiness 判断，再调用 active V2 快路径并保存 compact summary/detail；research endpoint 仍保留完整多臂实验 |
| [data_pipeline.py](../stockfish_core/yaogu/data_pipeline.py)、[request_queue.py](../stockfish_core/yaogu/request_queue.py) | 全主板分层请求、缓存复用、续采、失败分类和预算；不是无限重试或每天全量重下 |
| [contracts.py](../stockfish_core/yaogu/contracts.py)、[visibility.py](../stockfish_core/yaogu/visibility.py) | 类型、单位、来源四时钟、父回执及逐行可见性；拒绝未来或无法核验的证据 |
| [jobs.py](../stockfish_core/jobs.py)、[scheduler.py](../stockfish_core/yaogu/scheduler.py) | SQLite 持久任务；核心单执行 worker、最多 20 个待执行任务；应用进程内唯一调度器；20:30 official primary 走 Daily Primary Fast Path |
| [static/core.js](../static/core.js) | 读取保存摘要、历史选择、详情、导出、触发计算/后验/跟踪；页面不是第二套计算权威 |
| [scripts/codex_research.py](../scripts/codex_research.py) | 本任务的薄调用封装，复用服务接口；不复制策略或标签算法，不另建数据库或常驻服务 |
| Docker / WSGI | Compose 是可选后台部署；[wsgi.py](../wsgi.py) 配合单进程 gunicorn 保证单调度器，持久卷保存状态；当前本机实际使用本地 Python |

“StockFish”是项目沿用的产品外壳和工程名称，不是国际象棋引擎替股票作决策。当前日常主板候选走 `stockfish_core/yaogu` 确定性规则。Qlib/LightGBM、模型注册、可信量化快照及旧组合风控链仍在 `stockfish_core/quant.py`、`recommendation.py`、`risk.py`、`backtest.py` 中；它们依赖另外的模型、CSI300 数据和可信 RAW/可交易状态，**不参与 PV 或 DAILY 的上述评分**。历史作者模型不称为 Microsoft 官方预训练模型，保留 `RESEARCH_ONLY` 的使用限制。

旧应用 LLM 研究模块、MiroFish 和 Bot 不拥有当前正式名单的最终排名权。MiroFish 仅在 `experimental-mirofish` Compose profile 中，默认不随核心启动；旧 Bot 缺规范发布适配且 `app.py --with-bot` 被明确拒绝。应用内聊天、自动模型路由、统一费用面板和常驻应用智能体尚未实现。当前 **Codex 本身承担用户委托的组织与解释**，无需新建一套应用内 Agent 平台。

## 4. 数据供给、时间与真实性

### 4.1 已接入来源及各自限制

连接器存在、真实调用成功、当日可消费、历史覆盖完整四者必须分别判断。以下为当前接线与已保存的真实证据范围；不承诺下一次网络、账号权限或供应商接口必然成功。

| 来源/入口 | 字段和用途 | 已知可用事实与缺口 |
|---|---|---|
| BaoStock | 交易日历、全部证券/基础档、逐日与历史 RAW OHLC、成交量额、状态、复权因子及调整价；用于 PV、资格历史、价格后验 | 已有真实历史采集和重建资料；SDK login/query/logout 串行锁保护。源覆盖与历史身份认证范围有限，不自动获得完整 PIT 资格或限价证据 |
| AkShare 包装的交易所目录、Eastmoney、Sina | 沪深目录、现价、概念/行业定义与成员、资金流、龙虎榜、涨停池 | 部分来源真实成功与部分网络失败并存。当前成员只能标当前取得时点，不能回填成过去成分股；涨停池不是全主板母体 |
| Eastmoney 直接上下限价 | 单证券 `qt/stock/get` f51/f52，按上海业务日核验 | 已实现日期校验、逐股 checkpoint；既有真实请求出现代理/网络失败。未知限价不得按“未涨停”处理，也不能用当前限价证明历史成交 |
| Tushare Pro | `stock_basic/trade_cal/daily/daily_basic/adj_factor/stk_limit/limit_list_d/suspend_d/stock_st/top_list/top_inst/moneyflow/dc_index/dc_member/stk_auction/stk_auction_o` 共 16 个登记端点 | 端点参数、分页与部分规范化已实现；已保存真实授权探测是 `AUTH_REQUIRED`、真实 HTTP attempts 为 0。没有合法 Token 或权限即不可用，不因此阻断独立 PV 数据源；当前新增真实取数未获验证 |
| 官方规则/日历与本地注册证据 | 交易会话、板块与日期化限价制度、tick/舍入规则、历史时间证明 | 已登记 2026 日历；覆盖结束前需维护。供应商日历可作核对，不静默替换已冻结历史日历；无法确认下一交易日时不猜日期 |

Tushare 规范化按已核验单位处理：`daily` 成交量“手”×100 成股，千元成交额×1000；换手百分数÷100；资金万元×10000。`pre_close` 是当日除权参考前收，不等于前一日 RAW 收盘。可比价仅由同供应商 RAW 与有日期依据的因子组成，不把 BaoStock 与 Tushare 因子混乘。`stock_st` 只有完整当日集合才允许推断集合外非 ST；`top_list/top_inst` 的披露窗口不明仍不能评分。竞价端点即使盘后取得，也不能倒填成当时 09:25 已知。

新数据进入 [provider_normalization.py](../stockfish_core/yaogu/provider_normalization.py) 和 [provider_comparability.py](../stockfish_core/yaogu/provider_comparability.py)，保留原回执、字段版本、规范参数、条数、分页完整度与 SHA-256。大响应和原始缓存留在本机，不随 Git 分发。免费来源不等于免费无限调用或允许任意商业再分发；供应商具体许可另见 [provider_classification.json](../stockfish_core/provider_classification.json)。

### 4.2 四时钟、资格与复权

每条证据保留 `event_time`（事实时间）、`source_published_at`（来源公布）、`first_seen_at`（首次取得）、`ingested_at`（入库）；每次运行另有 `observation_date`、`decision_cutoff`、`generated_at` 与后续的 `evaluated_at`。datetime 带时区。首先检查父回执与业务范围，再核对证券行；晚到证据不能改写早前截止的榜。

日期化证券有效区间为 `[effective_from,effective_to)`。默认需 SSE/SZSE、MAIN、A_COMMON、非 ST、非退市整理、当日非停牌、上市至少 60 个**交易所会话**。资格未知、已知排除和目录缺失分别保留；未知不能悄悄缩小覆盖分母。DAILY 的独立 ST、次新观察开关默认关闭，不同时打开、不与主池混排；次新可从 6 会话保留观察身份，但仍不能虚构所需 60 会话历史。R05 固定原主池，不继承任意临时开关。

RAW 价格用于当日 OHLC、限价/封板、参考前收和开盘执行研究；跨日涨跌、均线、回收前高及价格后验用同 `comparable_basis` 的可比价格。`INVALID`、缺值、NaN/Inf 和冲突修订不成为有效因子。成交量另需自己的可比股数基准；“价格已复权”不能证明成交量可比，成交额也不能冒充成交量。

`PROSPECTIVE_CAPTURED` 是真实前瞻记录；`RECONSTRUCTED_HISTORICAL` 是后取历史资料且有来源文件/适用区间/hash 的明确历史证明；无法核验截止为 `UNVERIFIABLE_AS_OF`；合成为 `TEST_FIXTURE`，只准隔离测试。历史证明仅按许可放宽后下载的取得时间，不放宽来源事件/公布时间。旧日期文件名和晚下载记录不能变成当时留档。

### 4.3 板事件与采集失败

涨跌停事件用 Decimal 与已验证限价/tick 比较：C 等于上限是收盘封板，H 等于上限是触板，触板未收盘封住是炸板观察，O=H=L=C=上限是一字上限。结果为 TRUE/FALSE/UNKNOWN/NOT_APPLICABLE；越限、OHLC 错序、tick 不合规为无效/未知。规则推导必须绑定交易所、板块、ST、生效日、参考前收、舍入及已验证样本；未经认证的制度或例外不能直接套 10%/5%。注册器含 2026-07-06 制度边界；这是本项目已登记的规则证据，跨日期使用仍须核对 [rules.py](../stockfish_core/yaogu/rules.py) 适用范围。

严格连板按交易会话计数。已知停牌与已知不涨停都会打断且原因不同；未知前日不能造首板。左边界不可知只保留“至少 n 板”，不能当精确板数。M3/M5/M10/M20 为对应最近交易会话收盘封板次数；确认停牌可作已知零次事件，未知/无适用涨跌幅规则不能伪装完整。没有日内事件数据就不声称开板次数、封板时间、排队或实际可成交。

默认单次采集上限是 128 次任务/HTTP 保护、600 秒、单请求 40 秒、最多 2 次尝试，间隔至少 0.25 秒，具体以当前登记服务预算为准。HTTP、SDK operations、分页、任务重试、cache hits 分列；HTTP 准入前 SQLite 预扣，BaoStock SDK 不能算 HTTP。成功分页/证券子项原子 checkpoint；限流、网络失败、权限不足、预算耗尽与来源不支持分别存储，下一次有限续采复用成功部分。

流水线优先取得基本量价，再尝试逐股限价等增强；旧候选、活跃周期、退出影子所需证券与 MA20 前史合并进入取数需求。CLI 采集文件生成不等于激活生产输入；本地管理员登记需选择正确 DB/state，默认仅完整 DAILY 消费者通过 `COMPLETE/EMPTY` 才能激活。有限 PV 可以显式引用已登记历史 input_id，不强行伪装完整 DAILY。

## 5. 当前全部策略、公式与研究口径

### 5.1 策略身份和相互关系

| 策略 / profile | 当前定位与启用情况 | 主要数据门槛 |
|---|---|---|
| `YAOGU_V1.1.0` / `DAILY_V1` | 原五族六分项正式规则，冻结对照，未降级或改名 | 当期资格、板事件及足够历史、量价、主题、成熟 G、完整市场 M；本机最新真实输入尚不能完成全部六项 |
| `FUNDS_V1` | DAILY 的资金影子对照，默认 `funds_primary: false` | 全同族冻结候选的同源、同窗资金与龙虎榜证据全部可比；当前无完整真实评分证据 |
| `PRICE_ONLY` | B/V/G 的显式诊断，非完整 DAILY，非焦点名单 | 仍需对应 B/V/G 必需数据；不能用“只看价格”绕过缺板事件或成熟 G |
| `PV_OBSERVATION_V1` | 独立量价研究实验；日常量价名单入口，保存全部合格证券 | 日期化资格与连续 6 个会话的完整价格/金额；无需题材、资金、板事件、竞价、252日股性、账户、LLM 或 Qlib |
| `PV_FOCUS_PREVIEW_V0.1` | 完整 PV 的确定性焦点子集，非新增排序策略；正式周期只作用其选中项 | 原 PV + A/R/RAW 阳线与 CLV 条件，无配额 |
| `DNA_RERANK_V1` | 已实现的股性增强实验，在合格子群重算 PV 后混合 G | 连续 252 会话、可核验板事件、成熟样本与可靠性门槛；最新实际输入不足 |
| `THEME_CONTEXT_V1` | 已实现的事件题材增强实验，在合格子群重算 PV 后混合 T | 截止前可见完整 EVENT 主题、至少5成员、同伴价格/板事件覆盖≥95%；最新实际输入不足 |
| `THEME_CAPITAL_TREND_V1` | 上一版板块、资金、多头趋势独立研究臂，保留为对照 | 成交额、换手、长窗均线、题材联动、同题材活跃资金均需 `PASS` |
| `STAGE_AWARE_MOMENTUM_V2` | 当前主动核心阶段感知强势股策略；R14 起为 daily primary 默认 active strategy；V1 冻结为历史只读兼容 | 主板资格、成交额/换手底线、慢趋势状态、setup 识别、行为确认、市场兼容、同题材板块/资金和关键反证；最终输出 `FOCUS/WATCH/REJECTED/UNKNOWN` |

`DNA_REVERSAL_V1` 与 `THEME_LIMITUP_V1` 是旧客户端兼容别名；实际 canonical ID 分别为 DNA_RERANK/THEME_CONTEXT，不是两个新增策略，也不重复统计。配置 `execution_enabled: false` 指不启用交易执行，不代表禁止运行研究实验。日常以已确认 PV 路径处理量价请求，仍明确 DAILY 是冻结原基线，不拿 PV 结果冒充 DAILY 已齐备。

### 5.2 共同特征和 DAILY 五种结构

以下比例内部为小数，金额 CNY、成交量股、价格元。令 `C*` 表示可比收盘，`O/H/L/C` 表示 RAW OHLC，`ref` 是 T 当日参考前收。

```text
amount_ratio_5 = amount_T / mean(amount_T-5 ... amount_T-1)
volume_ratio_5 = volume_T / mean(volume_T-5 ... volume_T-1)
turnover_fraction = volume_shares / verified_float_shares（或核验同口径供应商值）
ret_k = C*_T / C*_(T-k) - 1，k=3/5/10
MA5 = 最近5交易会话可比收盘均值（含T）
amplitude = (H-L)/ref
CLV = close_location = (C-L)/(H-L)
upper_shadow = (H-max(O,C))/(H-L)
```

放大比的分母不含 T。DAILY 中 H=L 的合法单价：上限一字 CLV=1、下限一字=0、其他单价=.5，上影=0并记标志；PV 焦点的零区间另有“不适用、不入焦点”规则，二者不能混用。`median_amount20` 是最近20会话成交额中位数，`cmax60` 是有足够证据的最近60会话最高严格板数，不能用左删失下界冒充精确历史。

| 家族 | 正式结构资格 |
|---|---|
| ONE_TO_TWO | T收盘封板，严格 c=1，前日已知 |
| TWO_TO_THREE | T收盘封板，严格 c=2 |
| HIGH_BOARD | T收盘封板，严格 c≥3；已证明至少3板可保留结构预览，但精确 c 缺失会阻断依赖它的完整评分 |
| TREND_NM | T不封板，M5≥2 或 M10≥3；C*≥MA5、ret3≥0 |
| REBOUND_SETUP | T不封板，上一轮≥2板且结束距今1–3会话；C*≥MA5、RAW C≥O、CLV≥.6 |

主家族优先 HIGH_BOARD → TWO_TO_THREE → ONE_TO_TWO → REBOUND_SETUP → TREND_NM，其他命中为 secondary_tags，不重复计票。ONE_TO_TWO 已收复上一≥2板轮次结束的可比收盘可附 `REBOUND_COMPLETED`，不再混为尚待完成 setup。没有 PE、市值、累计涨幅或“超过几板永久禁入”的隐藏过滤。

合法 EMPTY 需要全资格池能够判定是否命中结构；当日齐价但历史触发条件未知，仍是 PARTIAL/UNAVAILABLE，并保存 `structural_screening_unknown_ids`。非候选只需判定结构所需数据，不强求每个未命中股票取得全部候选评分历史。

### 5.3 DAILY 六分项完整公式

统一分项为 0–100。`R(x;a,b)=100×clip((x-a)/(b-a),0,1)`，a<b。`Trap(x;a,b,c,d)` 在 x≤a 或 x≥d 为0，a→b线性升至100，[b,c]维持100，c→d线性降至0；缺失输出 NULL，不是0。下面为当前 [engine.py](../stockfish_core/yaogu/engine.py)、[themes.py](../stockfish_core/yaogu/themes.py)、[emotion.py](../stockfish_core/yaogu/emotion.py) 的实际数值。

**B：板结构/趋势匹配。** `c` 当前严格板数，`MN` 最近N会话封板次数，`p_c` 上一轮板数，`C*_end` 上轮结束可比收盘。

```text
B_ONE_TO_TWO = .40 R(M10;0,3) + .30 R(M20;0,5) + .30 R(cmax60;0,4)
B_TWO_TO_THREE = .40 R(M10;1,5) + .30 R(M20;2,7) + .30 R(cmax60;0,5)
B_HIGH_BOARD = .40 R(c;3,8) + .30 R(M10;3,7) + .30 R(M20;3,10)
B_TREND_NM = .50 R(M5;0,3) + .30 R(M10;0,5) + .20 R(ret10;0,.30)
B_REBOUND_SETUP = .40 R(p_c;2,6) + .30 R(M10;1,5) + .30 R(C*_T/C*_end;.90,1.02)
```

**V：量价匹配。**

```text
V = .30 Trap(amount_ratio_5;族参数)
  + .25 Trap(turnover_fraction;0,.03,.20,.45)
  + .25×100×CLV + .20 R(ret3;-.05,.15)
```

成交额 Trap 族参数：ONE_TO_TWO=(.3,.8,2.5,5)，TWO_TO_THREE/HIGH_BOARD=(.1,.35,1.5,4)，TREND_NM/REBOUND_SETUP=(.3,.8,2,4)。这些不是资金身份识别或实际买盘保证。

**T：主题强度；L：主题内相对地位。** DAILY 接受有明确产业/事件定义的 INDUSTRY 或 EVENT 主主题，不接受纯资格标签或 INDUSTRY_PROXY。主板有效成员≥5，剔除目标自身后，板事件、价格、ret3、严格板数等必需字段可用同伴≥95%。记 u 同伴封板数、c2 同伴至少2板数、p_pos 同伴当日 RAW C>ref 的比例、r3_med 同伴 ret3 中位数。

```text
T_theme = .35 R(u;0,5) + .25 R(c2;0,3)
        + .25 R(p_pos;.40,.80) + .15 R(r3_med;-.03,.09)
T = 可用主题最大 T_theme；并列按 theme_id 升序选主主题
P(x) = 100×(rank_average_ascending(x)-1)/(n-1)，n≥2
L = .50 P(M10) + .30 P(ret5) + .20 P(median_amount20)
```

L 的参考集是已冻结主主题完整当期资格成员，**含目标自身**；平均并列名次，完整参考字段缺失则 L 不可用，不按 TopN 或剩余少数样本重算。T 取最大主题后即用该主题 L，不能另挑 L 最好的主题。成员日与行情日分开：晚间取得当前成员配最后收盘价可以形成当前截止的新研究，需标 `CURRENT_MEMBERSHIP_AT_DECISION`；不能声称这些是旧观察日 PIT 成员。

**G：成熟封板事件股性。**

```text
G = .50 R(limit_up_count_last60;0,8) + .50×100×(s+1)/(n+2)
```

历史范围最多250会话、至少真实60会话；n 是截至当前已能观察下一交易会话可比收盘的历史封板事件数，s 是其次日可比收盘变化>0的次数。当前事件的未来标签不进入 G。停牌、缺价与不可比下一日单列 censored，不顺延。完整历史确无成熟事件时原始成功率 NULL、Beta(1,1)先验=.5；有事件但全部结果删失则 G 不可用，不能当成无事件。

**M：主板市场环境。** 使用完整身份分区的默认主板非 ST 范围：`master_expected → eligible / known_excluded / eligibility_unknown`，合法池再拆停牌、需要价格、已取价格与缺价。状态覆盖 `(eligible+known_excluded)/master_expected` 与价格覆盖 `price_observed/price_required` 均至少98%，并仍需来源/母体完整。N 是实际可核验价格/涨跌停状态的交易观察集合，不把98%声称100%。昨日封板 cohort 身份100%保留，今日 ST/停牌不将其删除；昨日身份未知则指标不可用。

```text
p = (s+1)/(n+2)
M = .30 R(p;.10,.50) + .25 R(m;-.03,.05)
  + .20 R(U/N;.003,.020) + .15 R(b;.30,.70)
  + .10 [100-R(D/N;.001,.010)]
```

这里 n 为冻结昨日封板群体数，s 为今天继续封板数，m 为该群体今日合法可比收益中位数，U/D 是当日涨/跌停家数，b 是当日上涨比例。确认停牌留在 n、不晋级；m 单列有效样本。完整零cohort时 raw p/m为NULL，p 使用明确先验，m子分50标 `PRIOR_NO_EVENT`；全cohort确认停牌时 m子分50标 `PRIOR_NO_TRADABLE_EVENT`。来源缺失不是零事件。原始各梯队晋级率与平滑 p 分开显示。

总分 `S_daily=Σ(权重×分项)/100`，冻结权重如下：

| 家族 | B | V | T | L | G | M |
|---|---:|---:|---:|---:|---:|---:|
| ONE_TO_TWO | 20 | 25 | 25 | 10 | 5 | 15 |
| TWO_TO_THREE | 25 | 20 | 25 | 10 | 5 | 15 |
| HIGH_BOARD | 20 | 15 | 25 | 15 | 5 | 20 |
| TREND_NM | 15 | 30 | 25 | 10 | 5 | 15 |
| REBOUND_SETUP | 20 | 30 | 20 | 10 | 5 | 15 |

每行合计100。缺任一项不可逐股删除缺项并重标满分；Qlib、LLM、PE、ROE不进入此分。完整同族以未舍入 S/T/V 降序、证券ID升序排序；跨族不当成一份概率总榜。页面保留两位小数，显示值不反向参与排序。

### 5.4 DAILY 情绪状态、层级和竞价证伪

`d=M_T-mean(M_T-1,M_T-2,M_T-3)`，严格使用三个交易会话各自在截止前成功发布的唯一最新合规版本；配置/特征、主池、覆盖政策与 coverage_type 要匹配。缺任一天则 d=NULL、`UNKNOWN_DELTA`，保存缺失日期；不拿自然日、同日三修订或缩短均值代替。当前 M 若完整仍可保留。

当前 `classify_emotion` 按以下优先级：RETREAT 若 `(d≤-12 且 M<55)` 或 `(D/N≥.008 且昨日n≥5且m≤-.02)`；否则 M<30为 COLD；30≤M<50且d≥8为 REPAIR；M≥75为 HOT；M≥50且d≥0为 FERMENT；其他为 SPLIT；M不可用才为 UNKNOWN。**d缺失本身不强制所有状态为UNKNOWN**，不把 `UNKNOWN_DELTA` 与总状态混为一谈。

FOCUS 要求 S≥70、V≥45、T≥50、数据完整、市场不在 COLD/RETREAT/UNKNOWN 且无观察专用风险；S≥55未进FOCUS为WATCH，严重异常波动/OBSERVE_ONLY也降为WATCH；更低完整分保留STRUCTURAL_MATCH，分数缺失为DATA_PENDING。≥7板只提示风险，没有永久禁止。严重异常波动公告不等于已确认停牌。

页面观察20、重点5为展示；按 ONE_TO_TWO、TWO_TO_THREE、TREND_NM、HIGH_BOARD、REBOUND_SETUP 轮询，完整同族集合持续保存。与 PV 焦点无人数限制相互独立。

前夜冻结对象、主题同伴、原分、公式、阈值和 `AUCTION_CONDITIONS_V1`。次日仅合格09:25撮合快照可复核：接力族 gap≤-3%标弱、FOCUS降WATCH；竞价额/前日额<.5%为参与弱；≥2%且gap>-3%为参与确认但不加分；gap≥8%或撮合价在涨停价提示进入不确定；冻结同伴上涨占比<30%且gap中位数<-2%为主题依据减弱。缺数据为NOT_EVALUATED，不以09:30或盘后字段伪装早期撮合；不改原始分和同族顺位。

### 5.5 FUNDS_V1 与 PRICE_ONLY

资金计算只用具有来源、同窗、金额单位及披露范围证明的白名单字段：

```text
F_money = .60 R(当日同源大单净额/当日成交额;-.10,.10)
        + .40 R(最近3交易会话同源大单净额之和/同窗成交额之和;-.10,.10)
F_lhb = R(不重叠披露净额/同披露窗成交额;-.05,.05)
F = .60 F_money + .40 F_lhb
S_funds = .90 S_daily + .10 F
PRICE_ONLY_score = .40 B + .50 V + .10 G
```

整个同族结构候选集合先冻结，全部资金证据/日窗/口径可比才发布该族 FUNDS 排序，否则整族不可用且保留 DAILY。三日必须恰为观察日结束的最后三个交易会话；多上榜原因、单日/三日重叠不能重复加钱。完整披露全集明确未上榜时 `NOT_DISCLOSED`、raw净额NULL、F_lhb=50；FETCH_FAILED/NOT_PUBLISHED不能当“没上榜”。席位别名只作有来源、有效期、样本量的证据，不能从营业部名推断自然人或按知名名字加分。披露后的1/2/5/10日结果不进当前特征/LLM证据白名单。

PRICE_ONLY 保留 `daily_score=NULL`、tier=DIAGNOSTIC，永不成为完整DAILY的FOCUS。资金在 R05 新实验中只是附加证据/分组分析，不进入 PV/DNA/THEME 权重。

### 5.6 PV、焦点：日常量价入口

通过当期资格后，证券必须具有连续 T-5…T 六交易会话的完整合法 RAW OHLC、非负同口径成交额、同基准正可比收盘与 T参考前收。前五会话总成交额必须>0。

```text
A = amount_T / mean(amount_T-5 ... amount_T-1)
R = C*_T / C*_(T-5) - 1
H_factor = (RAW high_T - RAW low_T) / reference_prev_close_T
基础入围：A≥1.2 且 R>0
PR_mid(x) = 100×[count(values<x)+0.5×count(values=x)]/n
S_PV = .45 PR_mid(A) + .35 PR_mid(R) + .20 PR_mid(H_factor)
```

百分位总体是**通过 A/R 条件的完整候选集合**，与 DAILY L 的 `(rank-1)/(n-1)` 不同。单证券或全并列取50。输入数值和评分用 Fraction 有理数，显示通常六位小数；同分按证券ID升序。`top_ids`、top_n、分页不截断保存的 items。

字段覆盖分母是全部资格证券，分子是六会话必需字段完整者，不能用入围数量作覆盖分子。母体完整且覆盖≥98%时，有排名为COMPLETE、无人入围为EMPTY；范围不完整但有可算样本为LIMITED_SCOPE，完全无可算样本为UNAVAILABLE。低覆盖仍可读真实受限名单，但不称全主板完整判断。限价未知只是 `UNVERIFIED_NOT_ZERO`，不因为缺板事件剥夺独立 PV；保留“振幅是风险”“分数非概率”“限价未核验”“未人工风险复核”等标记。

焦点从完整 PV 同顺序过滤：**A≥2、R≥.10、RAW C≥O、CLV≥.70**。没有 H_factor门槛、rank门槛、最低/最高人数或补位；不在焦点子集重算百分位。RAW H=L时CLV不适用，该股仍可属于PV但不进焦点。详情保留每个未选门槛原因，数值通过不是风险已清除。

### 5.7 DNA 股性增强完整定义

DNA只处理PV候选中已具足连续252交易会话和完整可核验收盘封板/触板事件的证券。窗口包含额外前日用于判断首板：成熟事件观察为 T前250会话（T-250…T-1），计数窗口为含T最近250会话（T-249…T）；当前T未来结果不进入成熟分母。

首板尝试：事件日触上限且前交易日未收盘封板。次日收益用该日 `RAW close/reference_prev_close-1`，开盘溢价用 `RAW open/reference_prev_close-1`，不误用旧RAW收盘跨除权。已知事件但次日参考价缺失只剔除对应成熟结果分母，不记失败。当前实现要求完整历史事件；历史行/事件本身未知使该证券增强不可用。

可靠性资格：首板尝试 n≥3、**原始首板封住率>70%**，且封板次日收益、首板封住次日收益和首板尝试次日开盘溢价三类必需分母均>0。它没有额外“Wilson下界必须>70%”门槛。

```text
Wilson_LB(s,n) = [p + z²/(2n) - z√(p(1-p)/n + z²/(4n²))] / [1+z²/n]
p=s/n，z=1.959963984540054，平方根使用50位Decimal
LB_PREMIUM5 = 历史收盘封板后次日close溢价>5%的Wilson下界
LB_FIRST_RED = 历史首板封住后次日close溢价>0的Wilson下界
LB_FIRST_SEAL = 历史首板尝试当日封住率的Wilson下界
m_pool = 同日冻结可靠性合格证券群全部有效首板尝试开盘溢价的事件加权均值
M_ADJ = (各股有效开盘溢价之和 + 5×m_pool)/(各股有效开盘样本n+5)
LIMIT_COUNT_250D = 含T最近250会话收盘封板次数
G_DNA = .25 PR_mid(LB_PREMIUM5) + .25 PR_mid(LB_FIRST_RED)
      + .25 PR_mid(LB_FIRST_SEAL) + .15 PR_mid(M_ADJ) + .10 PR_mid(LIMIT_COUNT_250D)
S_DNA = .60 S_PV_GENE_COHORT + .40 G_DNA
```

各百分位在固定 DNA 合格证券群计算，PV 的 A/R/H 也在该群重算，另存 original_S_PV。最终未舍入 S_DNA降序、ID升序。部分缺数标LIMITED_SCOPE；无合格增强样本为INSUFFICIENT_COVERAGE，诊断行不作为正常排名导出，不能拿PV原分冒充DNA完成。来源思想参考固定 `limit-up-sniper` commit `98edc5efcfe65f71e69f7bfca60e6dee88ee612e` 的 `core/gene_calculator.py`（MIT），本项目独立实现批准公式，不继承其账户、QMT、发信、昨日涨停剔除或交易规则。

### 5.8 THEME 事件题材增强完整定义

R05主题仅接受截止前可见且完整的 **EVENT** 主题修订；这是与DAILY可接收INDUSTRY的区别。成员先与当期完整资格池相交，至少5成员。先冻结当日有效唯一主题总体；每个目标在所有包含它的主题统计中剔除自身，不能只删目标所属的“最好主题”。同伴不要求自己通过PV条件。

每个同伴需连续6会话合法同基准价格与T可核验封板状态；有效同伴/应有同伴≥95%。若剔除目标后冻结总体任一主题统计变不可用，该目标本轮缺覆盖，不能静默缩小百分位总体。记同伴T封板数 u、ret5中位数 r5_med、当日可比收益>0比例 p_up：

```text
T_theme = .40 PR_mid(u) + .30 PR_mid(r5_med) + .30 PR_mid(p_up)
主主题 = 目标所属主题中最高T_theme，并列theme_id升序
S_THEME = .75 S_PV_THEME_COHORT + .25 T_theme
```

三个主题百分位的总体是同日冻结有效主题、对当前目标统一排自身后的各主题统计，不是只比目标的所属主题。随后在满足主题资格的PV子群重算A/R/H百分位，保留original_S_PV；最终未舍入S_THEME降序、ID升序。缺主题不补中性分，无增强名单不以PV替代。

情绪/昨日梯队晋级、封板队列收益/翻红/再封板、当日涨停/炸板宽度作为覆盖明确的上下文与分层分析，不加新的市场常数或硬否决，不覆盖DAILY M。主题构成变化与行情变化分开说明。来源只借鉴 `vibe-astock` commit `5764a22b4ec6cfdade0f35bd1d6fe66b3ef5e92c` 的 `duanxian/emotion_metrics.py`（Apache-2.0）上下文思想，.40/.30/.30与.75/.25是本项目假设，不称上游已验证选股模型。

## 6. 持续跟踪、后验与评价

### 6.1 研究周期和停止关注

当前 `TRACKING_POLICY_PILOT_V0.1` / `R05_RESEARCH_EPISODE_TRACKING_V2` 只对新焦点形成正式episode。保存首次signal、后续重复signal和稳定episode_id；重复入选不重启周期，不因为某日未上焦点、其他题材升温或达到固定关注天数就删除旧研究。失效/归档以后新合格信号形成关联previous_episode_id的新周期。

研究状态是ACTIVE、COOLING、THESIS_INVALIDATED、ARCHIVED，数据状态另列。每天对各自会话的价格计算含当日20个连续可比收盘的MA20，**连续两个有效交易会话 C*<各日自己的MA20**触发失效。未知打断已验证连续条件，不解释为通过或失败。

量缩条件用周期开始至当前的可比成交股数峰值：当前量/峰值≤30%的有效连续会话至少3个，且触发时ret5≤0，产生COOLING警示而非结束。实现中的计数要求各会话ret5可计算，非正判断在触发时；条件不再成立且数据可评价时可回ACTIVE。量的复权/无公司行动基准未知则量缩指标不可用，不能用成交额替代。缺价格、MA20或量基准分别保留原因。

人工归档已有 `POST /api/research-episodes/<episode_id>/archive`（reason必填），没有专用网页归档按钮。归档或失效停止默认活跃关注，但原始排名、信号和尚未完成后验继续保留；退出后另作5/10会话影子观察。退出不是从收益统计中剔除坏样本。归档是明确用户意图，普通查名单不自动执行。

### 6.2 价格后验、逻辑主键与成熟度

R05后验版本 `R05_STRATEGY_OUTCOME_OBSERVATION_V3`，默认 h=1/2/3/4/5/10/20，API支持自定义正整数h，均按交易会话。T信号的价格目标为T+h，待目标当日15:00上海时间结束才成熟。标签独立记录：

```text
close_reference_change = C*_(T+h)/保存的C*_T - 1
next_open_gap = O*_(T+1)/保存的C*_T - 1
mfe_bound = max(未来h会话可比high)/保存的C*_T - 1
mae_bound = min(未来h会话可比low)/保存的C*_T - 1
any_close_limit_up / down = 未来窗口至少一次可核验收盘涨/跌停
```

端点收盘变化不被中间缺一日或未知板事件自动抹掉；极值界需全部窗口可比高低价，板事件独立判定：已知一次真可判真，全部已知假才判假，否则NULL。每个metric有自己的成熟/缺数状态；整条标签CENSORED不意味着其中已核验端点收益不可用。日线无法判定止盈止损先后，开盘/极值是参考变化，不是成交结果。

逻辑标签主键含run、strategy、证券、观察日、h、规格/配置/实现及源码身份、metric_policy和label_kind。重评只追加revision，按报告cutoff选择最新有效版本；旧结果不覆盖。同一证券不同信号日保留，统计另以episode首次/窗口首次去重，不能把每次重复入选视作独立投资事件。未成熟、已成熟缺数、不可比和有效0涨跌分开：价格上涨率=有效成熟且收益>0个数/有效成熟样本数，0涨跌留分母但不算上涨，空分母返回NULL。

原DAILY结构目标独立：接力三族看下一交易会话收盘继续封板；TREND_NM看未来3会话至少一次封板；REBOUND_SETUP看未来3会话可比收盘收复上一轮结束收盘，另报3日封板。DAILY价格观察为1/2/3/5/10交易会话；更早旧组合记录的7/30/90自然日不改单位，也不混算。

### 6.3 模拟交易口径

[paper_execution.py](../stockfish_core/yaogu/paper_execution.py) 的 `NEXT_OPEN_HOLD_H_PAPER_V1` 是无账户研究情景。信号T，尝试入场 E=T+1开盘，计划退出 **E+h收盘，h≥1**。因此h=1退出T+2，不能拿价格T+1标签当同一持有期交易。买不到不追买；无下单指令。

入场需日期化停牌状态、可比开盘、已验证当日上下限价及合法日线；停牌为NO_FILL，未知限价/价格/状态或开盘触上下限为FILL_UNVERIFIED。日线边界内模型确认只是一种纸面可成交假设，不证明实际排队成交。退出遇停牌、跌停/无效限价或不可比价，保留OPEN_BLOCKED与每个受阻会话，尝试后续已结束且可核验收盘；保留市值参考和延期退出，不能删除受限持仓。

统一往返成本b=0/30/60bp，30bp主口径，买卖各一半且假设已含滑点：`net_return=(exit_C*/entry_O*)×(1-b/20000)/(1+b/20000)-1`。这是研究假设，不是当前券商税费报价。胜率只统计已验证闭合交易中净收益>0者；另列总信号、已尝试、可评价、closed、未成交/未证实/受阻与closed/attempted覆盖。无closed则模拟胜率NULL，不填0%，也不阻止有效价格研究。

### 6.4 对照、复盘与已完成历史范围

复盘 `R05_RESEARCH_FEEDBACK_V2` 提供5/20/60交易会话窗口；实际只有20个信号日时仍显示已观察天数，不能把请求60日当60日实证。并列报告合并signal、日期等权、唯一股票、episode首次与窗口首次，保留零名单、失败日期与未成熟样本。比较在同日期、共同资格、相同输入、持有期、成熟条件、成本下单列，同时保留各策略实际覆盖；不能只比较“各榜获胜者交集”。

R05历史诊断包括全资格池及成交额强度Top5/10/20等同条件对照，TopK只作诊断不改PV动态名单。DAILY历史评价配置保留252日历史/60日warmup、最多504请求会话；仅板数、ret5、amount_ratio与100次固定seed=20260905随机对照；bootstrap 1000次同seed；120/40/20 train/valid/test、步长20，按label_end做purge。±10%敏感性只研究，不自动上线最佳参数。是否实际跑足这些窗口必须查具体运行，不由配置存在推断成功。

既有R05已真实执行2026-08-31…09-04主五日，以及08-10…09-04共20个信号日的**历史重建**。主五日有沪深日期化资格认证，较早十五日仅深圳认证范围；全局仍LIMITED_SCOPE。主五日h=5/10/20在原回放截止未成熟，历史限价/可成交性不足，没有已验证闭合模拟成交胜率。正文不复述旧测试总数或单个收益百分比作为本轮结果；历史价格标签与独立工程golden、前瞻日常运行各属不同证据层次。

这些相关重复证券、短窗口及重叠标签不足以证明投资优势。AI回顾历史可能知道后续事实，仅限制资料日期不能证明模型忘记未来；目前没有本项目“PV对AI”的真实月度优势结论。

## 7. Codex日常操作、保存与维护

### 7.1 用户怎么委托，程序实际做什么

以后在本机 `D:/StockRecommendation` 项目中直接用Codex表达请求即可，网页无需打开。项目根指引引向业务短协议，业务仓库的AGENTS随仓维护；新克隆从仓库目录开始。当前会话已能读取指引并调用入口，本任务未声称启动并测试了另一型号的全新会话。

| 用户请求 | 实际执行链及交付 |
|---|---|
| “把明天的荐股清单给我” | 先取上海日期的下一自然日，再取该日或之后首个已登记交易会话，核对活动策略/输入水位；按相同输入+策略/实现+cutoff查有效保存run，能复用就复用；确需更新且有条件才有限增量取数、调用原计算。返回目标会话、真实观察日/截止、完整/焦点数、覆盖、run_id、理由风险和完整保存位置 |
| “给我总体短线市场与强势股报告” | 从真实覆盖、完整PV/焦点、旧run变化及已取证资料生成报告，明确事实/程序计算/Agent推断/未知；主板不冒充全市场，焦点下降不直接认定退潮，没取到板块/龙虎榜就不补造 |
| “昨天的候选后来怎么样” | 读昨天保存原run，按已有输入读取或追加后验/跟踪，保留旧依据、退出、缺数与未成熟；不按今日规则重造昨日名单 |
| “为什么某股入围/没入围” | 读同run单股detail、原因子与资格/焦点拒绝原因；资格不合格、基础未过、未进焦点、分页未显示分别解释 |
| “按新要求给我下一交易日名单” | 默认使用主动核心 `STAGE_AWARE_MOMENTUM_V2`；硬性条件和本版初始研究参数分开报告，初始参数标明**尚未验证**，`MARKET_EDGE=NOT_ESTABLISHED`。缺长窗均线、题材、资金、市场覆盖或行为确认时输出 `WATCH/UNKNOWN/INSUFFICIENT_EVIDENCE`，不借旧PV冒充新版正式名单 |
| “复盘最近一周：没选上的是否更好” | 同日期、同输入、同成熟窗口比较旧PV、旧焦点、新技术诊断、新正式策略和趋势模板；落选、退出、坏样本保留在ledger，不以指定股票是否入选验收 |
| “用了哪些指标/权重” | 先小型指引与当前配置，需要时展开本文和指定函数；通常不取新行情或回测 |
| “我想改策略” | 区分讨论与实施，整理真实现状、拟改部分与影响；未明确实施授权不改配置/源码 |
| “给我最新综述开新对话” | 定向核对文档新鲜度，获授权变化只更新对应章节；给出仓库中的本文和文末启动说明 |

“明天”非交易日时用已登记日历明确转为下一交易会话；只有旧数据时写“截至X观察日最后可用结果”和新资料未得原因，不称刚生成最新行情。名单很长可以节选回复，但必须保留全体并提供完整结果实际位置。资料不足与合法零候选分开；应用权限失败不转去不受约束的临时计算脚本。

具体封装命令以 [codex_research.py --help](../scripts/codex_research.py) 和 [CODEX_DAILY_RESEARCH.md](CODEX_DAILY_RESEARCH.md) 为准。`select --profile PV_OBSERVATION_V1 --target next-session` 查询最近可复用真实结果并保存完整导出/事实简报；`run --profile PV_OBSERVATION_V1 --input-id INPUT_ID` 名为run，实际只按input_id精确查找合规保存运行，**不新算名单**。原服务没有原子幂等运行接口；无可复用结果时，Codex先核实已登记真实输入、来源/日期/覆盖与在途任务，再按已授权范围调用一次原POST计算接口。响应不明先查job/run，不盲目重发或为了增加样本改cutoff。封装之下是真实产品接口：

| 操作 | 已有正式入口 |
|---|---|
| 健康/配置/实验规格 | GET `/api/health`、`/api/strategy-configs/current`、`/api/strategy-experiments/specs` |
| 数据进度与采集/续采 | GET `/api/candidate-data-pipeline`；POST同路径`/capture`，body `{}` |
| 查询保存实验 | GET `/api/strategy-experiments?limit=20&offset=0`；GET `/api/strategy-experiments/<run_id>/summary?strategy_id=PV_OBSERVATION_V1&selection=all&page=1&page_size=50` |
| 计算新实验 | POST `/api/strategy-experiments/run`，选择已登记input_id/observation_date |
| 详情、导出 | GET `/api/strategy-experiments/<run_id>/items/<instrument_id>`；同run `/export?format=csv或json&strategy_id=PV_OBSERVATION_V1&selection=all或focus或unselected`，新版可把 `strategy_id` 换成 `STAGE_AWARE_MOMENTUM_V2`、`STAGE_AWARE_MOMENTUM_V1`（历史只读）、`THEME_CAPITAL_TREND_V1`、`THEME_CAPITAL_TREND_TECHNICAL_ONLY_V1` 或 `SIMPLE_TREND_TEMPLATE_REFERENCE_V1` |
| 已保存后验/跟踪/复盘 | GET同run的`/outcomes`、`/tracking`、`/review`；GET `/api/research-feedback/latest`、`/api/strategy-comparison/latest` |
| 更新后验/跟踪 | POST同run的`/outcomes`或`/tracking`，指定已登记后续input_id，可给正整数horizons |
| 任务与调度 | GET `/api/jobs/<job_id>`、`/api/candidate-schedule`；失败原因明确后才按授权重试 |

POST返回202/job_id表示已接受，不代表完成。任务终态SUCCEEDED后仍核对业务status、覆盖、保存run；FAILED/CANCELLED/INTERRUPTED不算成功。只读历史和导出不调用LLM、不重算排名。同run的完整canonical排名是唯一权威，报告与Agent意见是派生材料，不新建平行“权威名单”。

阶段感知强势股主动核心说明见 [STAGE_AWARE_MOMENTUM_V2.md](STAGE_AWARE_MOMENTUM_V2.md)，旧 V1 首轮审计保留在 [STAGE_AWARE_MOMENTUM_V1.md](STAGE_AWARE_MOMENTUM_V1.md)。本轮已接入 `STAGE_AWARE_MOMENTUM_V2`；旧PV/焦点、DAILY、DNA、THEME、`THEME_CAPITAL_TREND_V1` 不改名、不重写历史。2026-09-04 已保存真实输入重放时，V2 保留全 eligible 诊断，正式 FOCUS 只来自完整通过的决策链；缺题材、资金、市场覆盖或行为确认时进入 `WATCH/UNKNOWN/INSUFFICIENT_EVIDENCE`，这是证据不足，不是证明市场中没有合格股票。国芳集团、金健米业只作为诊断样例，不作为入选验收目标。上一版板块资金趋势策略说明仍见 [THEME_CAPITAL_TREND_V1.md](THEME_CAPITAL_TREND_V1.md)。

2026-09-07 R09 语义完成版的实际输入和派生报告在本机 `stock-fish/reports/codex/v2_semantic_completion_20260907_20260907T190336/`。数据 readiness 显示 MA5/MA10、RET1/3/5/10 与 RS1/3/5/10 在 eligible 池完整；MA20/60/120/250、长均线斜率、PIT 题材、资金、竞价与日内时间戳仍为 UNKNOWN/PARTIAL。历史 replay 报告登记了 60 个目标会话，但当前工作区没有可核验 `RECONSTRUCTED_HISTORICAL` MarketInput 或 source-policy supply 目录，不能用 9.7 晚到前瞻输入倒填历史，因此该段以唯一客观 blocker 关闭。

### 7.2 自动与需要触发的步骤

应用进程内调度约20秒检查一次；交易会话计划为16:10 PRELIMINARY、20:30 EVENING、次日09:26 AUCTION_REVIEW（目标09:25撮合）。在线且符合数据条件时进行采集与DAILY发布；盘后有当天登记输入时为已有未完成运行追加后验/研究跟踪。观察任务每轮有数量上限，积压留后轮；无当天输入记录NO_CURRENT_SESSION_REGISTERED_INPUT，缺旧候选行情仍缺数。

**新的PV研究名单仍需Codex或网页触发**；“调度存在”不代表新PV每天已无人值守自动生成。用户不必亲自点页面，但Codex收到新名单委托时需协调实际入口。没有独立常驻Codex心跳、自动报告发送或综述自动维护进程。

服务关闭、电脑休眠/关机、网络失败会影响采集；错过时点标MISSED_SERVICE_OFFLINE，不倒填假捕获。重启发现执行中任务标INTERRUPTED，需要明确重试，服务恢复不等于任务恢复。20个会话前瞻计划不是删除迟到后验的到期日，退出关注也不停止应完成标签。

### 7.3 启停、数据位置、备份和磁盘边界

本机正确实例已运行时复用，先检查端口、进程身份、实际目录和健康状态，不能从目录名推断版本。服务未运行且既有启动授权允许时，Codex可调用本机入口：

```powershell
pwsh -File D:\StockRecommendation\Start-StockFish.ps1
pwsh -File D:\StockRecommendation\Stop-StockFish.ps1
```

本机上述脚本绑定现有D盘部署；发布仓库根也已版本管理`Start-StockFish.ps1`/`Stop-StockFish.ps1`，支持`-Root`且默认脚本所在目录，新克隆应按发布根README配置环境后使用它们，不能假设已经带有本机虚拟环境或私有数据。需要前台启动时，在配置后的`stock-fish/`业务目录运行 `python app.py`，以Ctrl+C停止。原脚本保留进程ID与创建时间核验，不根据任意PID杀进程。只关闭浏览器不停止后台，也不删除已保存结果。当前不自动切换到Docker或重建镜像；若另行部署Docker，现有入口为 [Start-StockRecommendation-Docker.ps1](../Start-StockRecommendation-Docker.ps1) / [start-stockrecommendation-docker.sh](../start-stockrecommendation-docker.sh)，按README选择并核对同一实例，不能让两个应用写同一DB。

| 内容 | 当前实现的默认/本机位置及管理规则 |
|---|---|
| 主数据库 | 本轮核验为`D:/StockRecommendation/stock-fish/data/stockfish.sqlite3`；可由`STOCKFISH_DB_PATH`指定。SQLite documents保存已注册市场输入、candidate_runs、strategy_experiment_runs、research_episodes、research_episode_signals/observations、strategy_tracking_runs、后验与复盘，以及持久任务 |
| 核心产物 | 本轮核验为`D:/StockRecommendation/stock-fish/data/core`，可由`STOCKFISH_STATE_DIR`指定；候选服务在`<state>/yaogu` |
| 原始取数/缓存/队列 | `<state>/yaogu/raw_captures`、`<state>/yaogu/provider_cache`；内含持久request-queue.sqlite3、原始回执/checkpoint。大数据和缓存不提交Git |
| 模型/旧Qlib | `<state>/model_registry`、`<state>/quant`；默认Qlib数据另在项目根`data/cn_data`或`QLIB_DATA_DIR`，不是PV必需材料 |
| 日常导出、报告、伴随元数据 | 开发目录内`reports/codex/`，即本机`D:/StockRecommendation/stock-fish/reports/codex/`；必须写明run_id/input_id、观察日、截止、规则/实现身份及完整文件位置，不把聊天节选当唯一保存。该私有运行输出不默认提交Git |
| 本机启动记录与日志 | `D:/StockRecommendation/work/stockfish-process.json`、`D:/StockRecommendation/logs`；只用于实际进程身份/故障诊断，不打印凭据、不随仓分发 |
| 既有完整历史重建 | 本机`D:/StockRecommendation/review/r05-rectification/replay`及相邻真实供应目录；正文仅引用范围/口径，完整资料未在远端仓库 |
| Docker可选状态 | 容器`/app/data/stockfish.sqlite3`和`/app/data/core`，命名stockfish-state卷；Qlib另卷。实际卷名带Compose项目作用域，不能仅凭模板名称删除 |

备份工具 [backup_restore_state.py](../scripts/backup_restore_state.py) 已存在：`backup --database <DB> --artifact-root <root> --destination <新目录>`，`verify --backup <备份目录>`，`restore --backup <备份目录> --destination <新目录>`。SQLite Online Backup包含已提交WAL，也覆盖嵌套队列库；外部产物需停服或不可变条件保持一致。恢复拒绝覆盖已有目的目录，旧不可变绝对引用不自动重写，跨目录需核对path-map。不能裸拷主库冒充一致备份、覆盖旧库或删除Docker卷。

没有默认自动清理研究历史、数据库或卷的用户功能；磁盘维护另按明确范围授权。用户已有磁盘节约要求，不因本任务新建大型审阅包、下载全部原始行情、复制大库或重建冗余镜像。Git仅收源码、必要测试/模板、中文说明与简短脱敏验证，真实.env、Token、私人库、原始供应商响应、完整缓存/模型/日志不上传。

### 7.4 故障下仍可使用什么

| 状态 | 可用能力与诚实输出 |
|---|---|
| 没新行情/网络失败/预算耗尽 | 查询已有保存名单、详情、导出、规则与历史报告；显示最后观察日、失败类型与pending。无新数据不能证明今日变化 |
| DAILY缺主题/板事件/M/G | 解释缺口；已具独立数据的PV仍可使用，不把PV改名为完整DAILY |
| DNA/THEME/FUNDS缺必需证据 | 保留增强不可用原因与基础PV/DAILY对照；不补中性分或用基础分假称增强成功 |
| 应用LLM未配置 | PV/DAILY确定性计算、保存、后验/跟踪、导出不需要LLM；Codex当前Agent仍能组织和解释，不能声称应用AI已接通 |
| 后验未成熟或无模拟closed | 保存研究、风险与可得价格指标可照常使用；相应率为NULL/NOT_MATURED/FILL_UNVERIFIED，不是0% |
| 后台未启动/身份认证失败 | 按已授权方式启动已核验实例；必须人工启动Docker或补合法凭据时只报告最小阻塞，不读出密钥或要求购买权限 |

## 8. 模型、成本与协作

Codex是当前日常Agent入口，负责解析委托、按权限调用现有程序、补充获授权公开资料并解释。网页端ChatGPT可承接理念/方案讨论和独立审阅；讨论明确后再给Codex明确实施授权。网页端AI不保证能直接读取私有仓库，不能为访问方便将仓库公开。

下表为用户选择下的**初始尝试分工，非能力保证或已跑过的成本基准**。沿用用户当前模型，不自动切换/升级、购买或增加调用预算。

| 任务 | 初始方式 |
|---|---|
| 已保存名单、详情、导出、固定指标 | 程序完成，当前Agent组织自然语言 |
| 规则查询、README/综述维护 | 优先尝试GPT-5.5或已经验证的较低成本模型 |
| 普通日报、资料整理、日常任务组织 | 优先尝试5.5或其他符合用户预算的模型 |
| 范围明确小修复 | 较低成本模型实施，配合相关定向测试 |
| 策略公式、历史时点、统计口径高风险修改 | 明确授权与严格验证，必要时请求更强模型定向处理 |
| 多次未解决的跨模块问题 | 整理最小问题材料，征求是否升级到Astra |

不写死模型价格、实际可用型号或推理参数；具体需要时核对当时官方与当前环境。Codex登录、ChatGPT订阅与应用内API额度不能假定通用。当前确定性主链无需应用内API；旧LLM研究需要另外配置真实transport/模型凭据且本机未核验可用，本任务没有新增API架构或付费研究。

成本控制是操作约定：简单请求先读短指引/摘要，不默认全综述+全仓；复用相同输入与截止的有效结果；只按需增量取数；日常查询不重跑所有测试、不默认全量历史回放、不额外调用模型证明当前模型完成。AI预算不能削掉正式候选。有界数据请求预算已经实现；统一模型预算开关/费用面板尚未实现，没有真实token统计不报虚构消耗。

## 9. 真实状态、限制与已知文档差异

### 9.1 当前证据分层

| 层次 | 已核实范围与不能推断的结论 |
|---|---|
| 已实现 | 规则核心、不可变输入/run、全名单/焦点、详情/导出、持久任务/续采、后验/周期/归档/复盘、纸面执行、可选Docker、备份恢复、Codex薄入口与项目文档 |
| 已有真实运行证据 | 真实有限主板历史输入、R05最近5/20信号日重建、已保存525 PV/17焦点的9月4日运行；本次通过保存结果完成无浏览器委托验证，具体结果见本轮验证记录 |
| 工程或合成证据 | 边界golden、资金/题材/DNA缺数与可靠性、来源故障/分页/预算等测试；Tushare规范化合成响应被PV消费，不等于合法Token真实取数；历史Docker隔离运行使用明确工程输入，不等于全市场DAILY成功 |
| 外部/环境阻塞 | 新采集曾因网络失败/预算用尽留下pending；Tushare合法凭据/权限未证实；完整历史板事件、题材PIT与资金同窗数据不足，部分竞价数据时点不满足 |
| 当前价格/模拟限制 | 历史重建并非当时存档PIT；早期范围仅深圳；后验按h分别成熟；无可验证闭合模拟成交样本，不能报模拟胜率 |
| 未验证优势 | 真实投资边际、长期OOS优势、PV对AI优势、长期无人值守采集可靠性、特定较低成本模型能力/费用差异均未成立 |
| 用户明确暂缓 | 新策略、调公式/权重/窗口、重写跟踪与交易模拟口径、新智能体架构、自动交易、账户平台、大规模开发轮次和自动升级模型 |
| 仅讨论未授权实现 | 应用聊天、常驻应用研究Agent、多专家自动分流、统一模型费用面板、自动参数冠军替换；不列作本次必须继续建设的任务 |

本轮只读核验最新保存真实名单为观察日2026-09-04的 `strategy_experiment_785520198d0b86b14548bb5c`，2026-09-06 23:33:07+08:00生成，资料截止23:32:27+08:00；完整525只、焦点17只，六会话必需字段覆盖3039/3043（约99.87%），仍因母体/历史认证范围为`RECONSTRUCTED_HISTORICAL / LIMITED_SCOPE`。17个跟踪周期为ACTIVE/PARTIAL，该run已保存24976条标签中成熟为0；标签含PV与资格诊断的7个周期，**不是24976只股票或独立样本**。5/20/60复盘窗口各仅1个已登记信号日。该库的首用保存范围不能与私有20日历史重建目录混称。

上述运行**不是9月7日实时行情，也不是静态截图里抄出的新名单**。本轮复用需展示真实日期、输入/策略身份、有限覆盖及目标下一交易会话，不因为跨日更改保存的观察日。最新已保存采集执行记录为87次HTTP、614.406秒、待续397且未激活；600秒是配置预算，614.406秒是该次实际墙钟，不能混称，更不能把预算字段当已获得完整数据。当前应用、数据库和任务核心健康，应用LLM未配置。上述事实是读取已有运行记录，不是本轮重新成功联网采集的证明；基于名单的解释不补造未取得公告、板块或龙虎榜。

### 9.2 需要保留而非擅自统一的差异

1. `ENGINEERING_BASELINE.md`、`DEPLOYMENT.md` 下部保留多轮历史状态，包括错误实验权重 .40/.30/.30、焦点rank≤30/R≥6%/H≥3.5%、旧Docker daemon不可达和旧测试数字。各文件顶部当前R05整改段、批准配置、V4源码优先；旧运行不覆盖，旧V2标SPEC_NONCONFORMANT并排除合规复盘，其余不能充分绑定的旧记录为SPEC_NOT_VERIFIED。
2. 实验YAML保留 `source_commit: RESOLVE_AND_PIN_BEFORE_IMPLEMENTATION` 原文，实际固定版本已经写入source lock并绑定运行。这不是授权编辑冻结YAML占位值；上游固定身份见第5.7/5.8节。
3. 配置跟踪data_states枚举CURRENT/PARTIAL/PAUSED_NETWORK/SUSPENDED/UNAVAILABLE/UNKNOWN；当前`build_strategy_tracking`实际产生CURRENT/PARTIAL/UNAVAILABLE，其他状态不能因为在配置出现就声称页面已逐一实现。后验、采集另有自己的缺数/网络/停牌状态。
4. 当前情绪d缺失标UNKNOWN_DELTA但不必然令总情绪UNKNOWN；按第5.4节源码分类，勿将概括性历史文字扩写为新硬否决。本次不调整分类逻辑。
5. 跟踪量缩配置将“连续可评价量缩会话”与ret5非正并列；实际源码在连续计数期间要求ret5可计算，并在触发当日要求ret5≤0，不要求连续三个ret5都≤0。本文明确记录真实实现，不把更强文字解释用于修补策略。
6. 开发目录脏工作区与发布Git历史不同；新文档/封装完成不代表当前常驻服务已重载任何业务源码。本次不需改策略源码或自动切换服务，运行版本应结合文件核对与启动记录判断。

发现文档过时先定向核对并修正文档；发现实现与已批准规格冲突则记录问题、解释受影响结果，未得实施授权不修改正式算法。后续正式策略、关键配置、来源、存储、运行方式、主要功能或协作边界发生获授权变更时，Codex应同步更新本文受影响章节。普通日报/名单变化单独保存，不将本文变成每日流水账；这是维护约定，尚无自动更新进程。

## 10. 证据定位与开启网页端新对话

| 需要进一步核对的事项 | 仓库内最小材料 |
|---|---|
| 操作入口与权限 | [README](../README.md)、[AGENTS](../AGENTS.md)、[Codex协议](CODEX_DAILY_RESEARCH.md)、[轻量封装](../scripts/codex_research.py)、[本轮首用验证](CODEX_FIRST_USE_VERIFICATION.md) |
| 冻结数值与已签发规格 | [yaogu配置](../config/yaogu_v1.defaults.yaml)、[实验配置](../config/r05_strategy_experiments.yaml)、[增补配置](../config/r05_completion_requirements.yaml)、[source lock](../config/r05_source_lock.json)、[规格追踪](../strategy_spec_traceability.md)、[DAILY规格](../STRATEGY_SPEC_V1.md) |
| DAILY公式/资格与数据时点 | [engine](../stockfish_core/yaogu/engine.py)、[features](../stockfish_core/yaogu/features.py)、[events](../stockfish_core/yaogu/events.py)、[themes](../stockfish_core/yaogu/themes.py)、[emotion](../stockfish_core/yaogu/emotion.py)、[visibility](../stockfish_core/yaogu/visibility.py) |
| PV/FOCUS/DNA/THEME/阶段感知与板块资金趋势 | [strategy_experiments](../stockfish_core/yaogu/strategy_experiments.py)、[stage_aware_momentum](../stockfish_core/yaogu/stage_aware_momentum.py)、[阶段感知 V2 配置](../config/stage_aware_momentum_v2.yaml)、[阶段感知 V2 说明](STAGE_AWARE_MOMENTUM_V2.md)、[theme_capital_trend](../stockfish_core/yaogu/theme_capital_trend.py)、[板块资金配置](../config/theme_capital_trend_v1.yaml)、[独立golden](../tests/r05_product/test_r05_spec_goldens.py) |
| 标签/周期/统计/纸面执行 | [strategy_tracking](../stockfish_core/yaogu/strategy_tracking.py)、[paper_execution](../stockfish_core/yaogu/paper_execution.py)、[replay_statistics](../stockfish_core/yaogu/replay_statistics.py)、[R05整改与回放说明](R05_RECTIFICATION_AND_REPLAY.md) |
| 供给/预算/部署 | [provider_requests](../stockfish_core/yaogu/provider_requests.py)、[provider_normalization](../stockfish_core/yaogu/provider_normalization.py)、[request_queue](../stockfish_core/yaogu/request_queue.py)、[data_pipeline](../stockfish_core/yaogu/data_pipeline.py)、[数据Docker说明](R05_DATA_AND_DOCKER_RECTIFICATION.md)、[compose](../docker-compose.yml) |
| 运行保存一致性 | [candidate_service](../stockfish_core/yaogu/candidate_service.py)、[candidate_api](../stockfish_core/yaogu/candidate_api.py)、[storage](../stockfish_core/storage.py)、[scheduler](../stockfish_core/yaogu/scheduler.py)、[备份工具](../scripts/backup_restore_state.py) |

本文正文足以讨论当前研究理念与实现边界。独立源码审阅还需上表对应文件、准确版本和必要的脱敏实际运行/输入定位；本文不是“已经独立审计”的替代证据。完整真实市场输入、数据库和本机审阅目录没有随远端源码发布；需要追加数据证据时再按许可提供最小范围，不能要求先导出整个仓库或把私人数据库上传。

可复制的新对话启动说明：

> 我在使用StockRecommendation，一个面向沪深主板A股的候选发现、解释、风险提示、持续跟踪和复盘系统，日常由Codex调用现有程序。请先读发布仓库最新的`stock-fish/docs/PROJECT_OVERVIEW.md`；它于2026-09-07依据R05冻结规则、实际工作文件及保存结果核对，开发脏工作区/发布版本/运行实例和历史重建限制已分别说明。先按正文理解真实规则与未实现功能，不把综述当作你已独立审计源码，不把研究排序分当上涨概率或交易胜率，不擅自提出已经实施的策略变更。需要验证实现时请明确索取最小对应源码或脱敏证据。当前准备讨论的问题由我补充：____。
