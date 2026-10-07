# StockFish Live：仓库集成审计

审计日期：2026-10-07。审计基线为仓库提交 `a3e06ee8c1d29b9e66ec5c4a22ba2778c3b6960c`。审计通过当前文档、代码、现有测试定义和只读环境检查交叉核对，没有启动 Research 服务、调用在线行情、读取真实数据库内容或修改生产逻辑。

**Phase 0：完成。** 本文只证明集成边界已经审查。Phase 1 的新增代码、实际测试命令、退出码和 fixture replay 结果另见本项目验证记录；本文列出的旧测试是合同证据清单，不表示本次重新执行并通过。真实历史分钟数据、实时 provider 能力及 `VERIFIED_PIT` 分钟覆盖均为 **UNAVAILABLE**。

`MARKET_EDGE=NOT_ESTABLISHED`；`LIVE_TRADING=DISABLED`；`BROKER_CONNECTION=NONE`。

## 1. 当前架构和隔离决定

阅读入口为根 `README.md`、`CURRENT_STATUS.md`、`CURRENT_STRATEGY.md`、`DEPLOYMENT.md`、`ENGINEERING_BASELINE.md`，业务侧为 `stock-fish/ENGINEERING_BASELINE.md`、`stock-fish/docs/PROJECT_OVERVIEW.md` 及相关合同。现有 Python Web/API、单写者 SQLite、持久任务、日频调度继续承担 Research；Go 是独立候选工具，尚未替换生产。主动核心仍为 `STAGE_AWARE_MOMENTUM_V2`，DAILY/PV/R1 等保留各自冻结身份。

Live 采用同一 monorepo 中的独立应用，不搬迁 Research 代码，不修改现有策略语义。共享的是经过审查的**数据语义**，不是服务对象、数据文件、锁或生命周期。Live 的 Python 环境、数据根、Parquet、DuckDB、状态、运行配置、日志、调度与 HTTP 端口必须独立。默认只读/离线 replay；没有合法实时 provider 时明确失败 `LIVE_PROVIDER_UNAVAILABLE`。

| 现有模块 / 符号 | 核验事实 | Live 决定 |
|---|---|---|
| `stock-fish/app.py` / `create_app` | 模块导入即构建应用；主入口启动 CandidateScheduler 和 Waitress | 不导入或启动该入口 |
| `stock-fish/stockfish_core/service.py` / `ApplicationService`，`yaogu/candidate_service.py` / `CandidateService` | 前者组合注册、量化、推荐、存储和任务；后者承担候选日频业务 | 不复用服务对象或候选计算路径 |
| `stock-fish/stockfish_core/storage.py` / `SQLiteStore` | 构造即建库/迁移；WAL、FULL，写事务使用 `BEGIN IMMEDIATE` | 不用作分钟库，不打开 recommendation DB |
| `stock-fish/stockfish_core/jobs.py` / `JobService` | 对同一数据库使用 OS advisory lock 实现单写者 | 不共用 DB、锁或任务目录 |
| `stock-fish/stockfish_core/yaogu/scheduler.py` / `CandidateScheduler` | 日频 service/root/config/jobs 耦合；20 秒 tick、1800 秒主任务 deadline | Live 使用自己的分钟时钟与生命周期 |
| `stock-fish/stockfish_core/yaogu/request_queue.py` / `RequestQueue` | spot/limit pool 按 15:00 最终会话释放，daily 为 17:30，身份以业务日为主 | 不当作分钟轮询器，不沿用日频缓存释放条件 |
| `stock-fish/stockfish_core/yaogu/provider_budget.py` | 网络请求前记账，SQLite 跨进程预算，HTTP/BaoStock socket 分开 | 可借鉴限额原则，不共用既有预算账本 |

Live 不继承 `STOCKFISH_DB_PATH`、`STOCKFISH_CAPTURE_*` 或 Research 的数据目录默认值；也不以 Research 的 health、采集作业成功或 DAILY 就绪判断 Live 分钟覆盖。

## 2. 可复用语义和身份边界

| 语义 | 现有代码 / 符号 | 可复用内容与限制 |
|---|---|---|
| 显式证券身份 | `stock-fish/stockfish_core/data.py` / `canonical_instrument` | 接受显式 SH/SZ/BJ 形式，裸六位代码必须配 exchange；不能猜交易所 |
| 时区 | 同文件 / `SHANGHAI`、`aware` | 所有证据时钟 timezone aware，市场会话使用 Asia/Shanghai |
| 单位和价格口径 | 同文件 / `CanonicalMarketBar`、`normalize_market_bar` | 股数和人民币显式换算；RAW、QFQ、HFQ、QLIB_NORMALIZED 分离；不推断缺失价格/单位 |
| 日期化证券状态 | 同文件 / `SecurityState`、`SecurityMaster.resolve` | effective interval 与 `available_at` 双重过滤，歧义/未验证状态失败；不以当前状态回填历史 |
| 有限交易日历 | 同文件 / `TradingCalendar` | 只使用明确覆盖范围内会话，范围外失败；不把周一至周五当成完整交易日历 |
| PIT 财务修订 | 同文件 / `PITFundamentals.visible` | 按可见时钟和修订选取，不能用最新财务数据覆盖历史 |
| 来源四时钟 | `stock-fish/stockfish_core/yaogu/contracts.py` / `Clocked`、`SourceReceipt` | event、published、first_seen、ingested 分开；保留原始来源与修订身份 |
| 权威父来源 | `stock-fish/stockfish_core/yaogu/visibility.py` / `EvidenceResolver` | 可见性不只检查行上的时间，必须绑定来源、身份、业务日、完整性与父回执 |
| 不可覆写历史 | `stock-fish/stockfish_core/storage.py` 及来源归档 | 借鉴 immutable、内容哈希和冲突拒绝；Live 自己实现并验证 Parquet/DuckDB 合同 |

Live 应独立定义小型合同，文档记录上述来源和语义对应，避免通过 `sys.path`、Research 包安装或导入整个业务 runtime 建立隐式耦合。`000001.SH` 是上证指数，`000001.SZ` 是深市股票，ID 本身还需配 `asset_type=INDEX/STOCK` 和明确 provider 映射。现有 Research 沪深主板普通 A 股池不能默默成为 Live“全市场”的分母；沪深/北交、创业板、科创板范围、缺席证券和预期数量需逐次声明。

身份函数只做语法规范化，不证明证券真实存在、类型或资格。`quant.py` 中同名 `canonical_instrument` 仅接受已经 canonical 的形式，与 `data.py` 的输入规范化范围不同；不应把两个入口当作完全等价并直接导入。未知 ST/停牌等字段保持 `None`，不能由 ID 形态推断为安全。

## 3. Data source inventory

正式 endpoint 清单见 `stock-fish/stockfish_core/yaogu/provider_requests.py`。下列“存在适配器”不表示当前网络、权限、商业许可、分钟覆盖或历史 PIT 已验证。

| 来源 / 入口 | 仓库已有能力 | 分钟/PIT 限制 |
|---|---|---|
| BaoStock：`observed_data.py` / `BaostockRawDataAdapter`，`yaogu/provider_requests.py` | 有限交易日历、日线 RAW、master/factor 等，显式单位；旧独立适配器每次限制 1–10 个证券并在子进程取数 | 当前正式管线是日频，不是全市场分钟采集服务；不能据此宣称 Live 已接通 |
| AkShare / Eastmoney：`yaogu/provider_requests.py`、`provider_normalization.py` | spot、涨停池、主题、资金等日频输入和原始回执 | 当前快照不是过去分钟轨迹；当前主题/行业成员没有申万一级历史 PIT 保证 |
| 可选 Tushare：同上 | 日线、证券状态、竞价、资金/披露等请求合同 | 凭据/权限和本次可用性未检验；未发现可直接复用的正式分钟 bar 管线 |
| legacy `market_data/provider_adapter.py` | 报价适配，响应时刻使用 `datetime.now()`，标为 `RESEARCH_ONLY` | 响应时间不能冒充 vendor 发布时间或历史已知时间 |
| legacy `market_data/data_fetchers/pytdx_fetcher.py` | 注释列出分钟 category；实际历史调用为 `category=9` 日线 | 注释不构成已实现、已验收的分钟能力 |
| legacy AkShare / Efinance fetcher | `akshare_fetcher.py` 缓存 TTL 20 分钟；`efinance_fetcher.py` TTL 10 分钟 | 不能沿用为每分钟更新或 freshness 保证 |
| 本地 Qlib：`data.py` / `LocalQlibDataAdapter` | calendars/instruments/features manifest 与日频调整价格读取 | `RESEARCH_ONLY`，原始公布/取得时刻未知，不是 RAW 或分钟 PIT |
| 来源归档：`yaogu/providers.py` | 保存响应、SHA256、first_seen、ingested、provider/schema/scope；敏感参数拒绝 | 本地哈希是完整性检查，不是 vendor 认证或再分发许可 |
| 历史资产工具：`scripts/r16_r1_source_inventory.py` | 绑定两个历史 input ID 的专用只读核验器 | 不是 Live 通用数据发现器，不自动运行并据其声明分钟就绪 |

静态检查 `stock-fish/data` 中 `.parquet/.duckdb/.arrow/.feather` 文件未发现该范围内资产。该结论不证明其他私有目录没有分钟数据，更不构成真实历史覆盖验收；本轮没有读取或提交私人数据库、模型、行情包或凭据。

**真实分钟覆盖：UNAVAILABLE。** Phase 1 只使用明确的 `TEST_FIXTURE` 证明事件流和工程边界。fixture 不能被转换成市场事实、前瞻留档、模型有效性或真实全市场覆盖。

## 4. PIT inventory 和 fail-closed 要求

| 已有证据 / 代码 | 核验结论 | Live 要求 |
|---|---|---|
| `contracts.py` / `Clocked`、`HistoricalTimeProof`、`SourceReceipt` | 四时钟、范围、hash、来源 URL、证明版本各自保存 | 分钟至少保存 event/source/available，同时保留收取/入库时间和时间依据，不能用字段名暗示已验证 |
| `visibility.py` / `clock_visible`、`EvidenceResolver` | 原始行时钟谓词不授权未绑定来源；晚采与未来公布分开 | `timestamp <= simulated_now` 与 `available_at <= simulated_now` 同时必要；父源晚于 cutoff 时不可见 |
| `provider_normalization.py` / `_clock`、`normalize_capture_with_report` | 日线事件按 15:00；组合时钟取较晚父来源；cutoff 早于采集完成失败 | 不复制日频 15:00 时钟到分钟 bar，不把日期当 availability |
| `observed_data.py` 的实际观测 adapter | source_timestamp 为实际响应收取时间，available_at 为实际入库；没有原始 vendor 发布时间证明 | 记录 `source_timestamp_basis`；历史后取为 RECONSTRUCTED/RESEARCH_ONLY，不能自称 VERIFIED_PIT |
| `SecurityMaster.resolve`、日期化 identity | effective interval 和取得时钟一起限制适用性 | 当前 ST、停牌、上市、退市、指数成分、行业成员不能回填过去 |
| `ThemeSnapshot` / INDUSTRY_PROXY | 有当前主题分类与成员证据，未证明申万一级历史成员 | 概念或当前行业只作实时/RESEARCH_ONLY 展示，未有历史证据时排除模型 |
| 日线限价/情绪/阶段特征 | 多项盘中序列仅有 `DAILY_OHLC_ONLY` 或明确 unavailable；见 `stage_aware_momentum.py` | 日线触板不能复原炸板时间、连板轨迹、开盘弱转强或分钟 sentiment |
| Qlib manifest | 只能证明本次读取字节一致，不能恢复原始公布时间或证券状态 | Qlib 数据不提升为 VERIFIED_PIT，不按今时 known state 修改历史 |

所有派生特征以最晚父来源可用时刻为下界。未知事件不记 FALSE，缺历史分母不记零；合法空集、缺覆盖、失败、未公布、未成熟分开。后验结果只能单独追加，不能进入当时特征或改写原预测。未来访问必须明确拒绝，不能悄悄截断请求再声称完整。

## 5. 交易日历与分钟陷阱

`data.py/TradingCalendar` 是有限**日**日历，不是分钟会话发生器。`observed_data.py/exchange_calendar_2026` 从带来源的 `exchange_calendar_2026.json` 建立 2026 年沪深会话，覆盖 2026-01-01 至 2026-12-31；该范围、市场和版本不能无声外推到示例 2025 年、2018 年或北交所。

Live 必须把 calendar version、coverage range、exchange scope、session date 及分钟标签约定写入数据集元数据。测试可用明确的 fixture 日历，但不得把 fixture 日期表标为正式交易所日历。未知日期报 `CALENDAR_OUT_OF_COVERAGE`，已知休市日与范围外未知日期分别处理。

分钟实现需额外处理：

1. Asia/Shanghai 本地日期、timezone-aware 时刻与 UTC 序列化一致；naive 时刻直接拒绝。
2. 09:30–11:30、13:00–15:00 两段连续竞价，午休不算 gap，也不填假 bar；盘后、周末和假期不能生成分钟。
3. bar 是起点标签还是终点标签需固定版本；例如 09:30 标签若代表 09:30–09:31 区间，09:30 时不能读取其最终 OHLC。不能因标签 `<=now` 忽略 bar 尚未完成或尚未发布。
4. 09:25 集合竞价、收盘集合竞价和分钟 bar 的关系需 provider 明示；Phase 1 不把竞价快照冒充连续交易分钟。
5. `+30m/+60m` 是交易分钟还是墙钟必须先定义并版本化，午休跨越结果不同；已过去或超出当前会话的 horizon 必须排除，不穿越下一交易日。
6. gap 依据显式 expected universe 和有效 session minutes 检查；数据中断、停牌、未上市、缺覆盖不能混为零量/平盘。
7. `earliest_trade_at` 是 Research 的日频最早交易辅助方法，不能作为 Live 盘中 forecast horizon 调度器。

Phase 1 推荐采用完成 bar 的终点标签 `09:31…11:30 / 13:01…15:00`，09:30 只代表采集开始；以明确 schema version 固定后才能检查完整 240 分钟。Go 日线 `calendar.Plan` 的 `IN_SESSION` 也覆盖午休，不适合作为 Live 分钟发生器。晚到修订只有在其实际收取/可用时刻之后的快照中可见，不改写此前 replay 状态。

## 6. Qlib、依赖与许可证边界

仓库保留 `qlib/` 上游源码快照 `79633dd9506ea689e5400dea0197717b5b3d74b7`，许可 MIT。当前部署约定使用 `pyqlib==0.9.7` wheel，不能改为相邻源码 editable install。`stock-fish/requirements-qlib.txt` 为可选研究依赖，不是 Live 的核心 dependency。

`LocalQlibDataAdapter.manifest` 对 calendars/instruments/features 文件逐一 SHA256，拒绝 symlink 和读时变更。`snapshot` 输出 `QLIB_NORMALIZED`、`RESEARCH_ONLY`、`source_timestamp=None`，`available_at` 为实际 manifest 观测时间，并标记 `RAW_PRICE_UNAVAILABLE`、`RAW_VOLUME_UNAVAILABLE`、`ORIGINAL_AVAILABILITY_UNKNOWN`、`SECURITY_STATE_UNAVAILABLE`。

`quant.py/QlibQuantEngine` 使用内容寻址数据副本，每次 rank 校验全部字节。`quant_worker.py` 以 `kernels=1` 初始化 Qlib，固定 CSI300 Alpha158/schema/model hash；该流程是日频研究 worker。`model_registry.py/import_legacy` 保存旧模型原 hash、22 会话标签窗口及 `RESEARCH_ONLY/NOT_OOS_VALIDATED` 身份。这些安全原则可参考，既有模型、日频特征或全文件哈希路径不进入 Live 分钟热路径。

2026-10-07 只读环境检查（包版本为 `importlib.metadata` 发现值，不等同于可导入）：

| 环境 | 实际发现 |
|---|---|
| 现有 `stock-fish/.venv/bin/python` | Python 3.12.14；Pydantic 2.13.5、pytest 9.1.1、NumPy 2.5.2、Pandas 3.0.5；DuckDB、PyArrow、pyqlib、LightGBM 未安装 |
| bundled Python 3.12 runtime | Python 3.12.14；Pydantic 2.13.5、NumPy 2.3.5；DuckDB、PyArrow、pytest、FastAPI、Uvicorn、Polars、scikit-learn、pyqlib 未安装 |

旧 `stock-fish/.venv` 的实际 Pydantic 导入检查退出码为 1，报 `ModuleNotFoundError: pydantic_core._pydantic_core`；因此该环境的依赖可运行性未通过核验。版本元数据不能替代实际 import、doctor 或测试。

此表是审计时快照，不代表 Phase 1 创建的独立环境。审计没有安装或升级任何包。新应用使用独立 `pyproject.toml` 和虚拟环境；不把 Live 依赖加进 Research requirements，不要求 Qlib、模型权重、LLM 或实时 API key 才能 replay。

根 `THIRD_PARTY_NOTICES.md` 保持有效：StockFish MIT、Qlib MIT、嵌入 MiroFish 为 AGPL-3.0-only，整个 monorepo 不能称为纯 MIT。Live 不引用 MiroFish 实现；自己的新增代码和依赖许可范围在独立目录说明。若复制小型现有代码，保留来源、版权和适用许可；数据/API 使用与再分发许可单独核实，代码许可不授权行情再发布。

## 7. 性能与运行限制

根 `CURRENT_STATUS.md` 记录已有保存查询通常约 5–6 秒；真实暖计算曾在 1700 秒到期失败，连续跨日更新尚未完成验证。历史归档 `stock-fish/docs/archive/R15_PERFORMANCE_RESULT.md` 有约 182–433 秒冷/暖/增量和资源/数据库增长问题；这些是各自旧任务记录，不是本轮测量，也不是 Live SLA。

`yaogu/research_r1_storage.py` 的研究读取预算包含默认 300 秒、2 GiB source bytes、1 GiB RSS、64 MiB SQLite page cache。日频全量扫描、整池重算、Research 锁和较长缓存 TTL 均不适合分钟热路径。

Live 从单进程内存增量状态开始，模型盘前加载，盘中更新特征和推断；不每分钟重训、不引入 Kafka/Redis Cluster/GPU。将 provider latency 与 normalization/state/forecast/storage/push 的内部 latency 分开测量。Phase 1 replay 只证明 ingestion、guard、存储与确定性；尚未实现 forecast 或 dashboard 时不能声称完整 `state update + forecast`、P50/P95 或实时达标。真实分钟覆盖和全市场规模未知时不外推 fixture 性能。

## 8. 现有测试证据和 Phase 1 验证边界

以下通过阅读测试定义确认设计意图，**不是本次执行记录**：

| 测试文件 | 代表性测试 / 约束 |
|---|---|
| `stock-fish/tests/data/test_data_contracts.py` | `test_explicit_identity_only`、`test_explicit_units_and_no_missing_synthesis`、`test_official_finite_calendar_and_earliest_entry`、`test_friday_state_never_implies_monday_status`、`test_execution_uses_available_state_without_future_daily_bar`、`test_pit_publication_ingestion_and_visible_revision`、`test_qlib_manifest_stable_and_prices_research_only` |
| `stock-fish/tests/r03_sources/test_provider_boundaries.py` | `test_adjusted_price_cannot_masquerade_as_raw`、`test_future_capture_cannot_be_backdated_by_business_session`、`test_current_identity_is_not_backfilled_into_history`、`test_future_factor_cannot_change_earlier_price`、组合 bar/security/theme 等待较晚父来源 |
| `stock-fish/tests/r03_evidence/test_visibility_and_funds.py`、`test_consumer_visibility.py` | 晚采需显式模式与证明；未来事件/公布不能靠 historical proof 放行；父来源身份、会话与收取时钟一致；未知后验不计零 |
| `stock-fish/tests/unit/test_durable_storage.py` | immutable、事务回滚、并发、payload/migration 篡改和损坏拒绝 |
| `stock-fish/tests/quant/test_quant_boundary.py` | 按 label realization purge；测试集不改变 fitted checkpoint；未来特征/hash/shape 拒绝；native child 失败不记成功；模型需等待验证标签实现 |

Phase 1 必须用新模块独立测试证明：09:45 无法访问 09:46 或晚到 09:45 bar；分钟时段/午休/coverage；乱序、重复、OHLC、负数、身份错配与 gap；provider timeout 失败保留；Parquet/DuckDB 读写正确、预测/事件不可覆盖、损坏拒绝；同日两次 replay 确定性。测试和烟测使用临时目录或显式 TEST_FIXTURE，不混入真实历史、不伪造实时成功。实际命令、退出码和未执行范围写入 `VALIDATION.md`，Phase 2–6 不因 Phase 1 通过而被视为完成。

## 9. 发布安全核验

审计时实际 checkout 位于当前共享工作区，分支为 `review`，origin 为 `git@github.com:LiRongji1995/stockfish-qlib-quant-system.git`。HEAD、本地 `origin/main` 与 `git ls-remote origin refs/heads/main` 均为 `a3e06ee8c1d29b9e66ec5c4a22ba2778c3b6960c`，ahead/behind 为 0/0。文档声明的 `publish/stockfish-qlib-quant-system/` 未在本机搜索范围内证实存在；不能假装已从该路径发布，也不据此复制整个开发目录。

既存变化为 `stock-fish/tests/fixtures/r16_r1_equivalence/BUSINESS_SLICE_RANK_TIE.input.json` 标记删除，以及对应 `.BUSINESS_SLICE_RANK_TIE.input.json.icloud` 未跟踪。这两项不是 Live 授权改动，必须保留，不恢复、不删除、不暂存。发布前需重新检查实际 dirty，因为多个协作任务共用工作区。

在当前已授权工程交付范围内，可在现有分支仅精确暂存本次文件、检查 staged diff、中文提交，再普通 `git push origin HEAD:main` 并用 `ls-remote` 核对远端提交。不新建分支/worktree、不切换分支、不强推、不 reset/clean/rebase、不盲目全量暂存，也不提交虚拟环境、运行 data/state、真实 DB/行情/模型/凭据或完整本机审阅包。若远端已前进或普通推送拒绝，停止推送并如实记录，不能改写历史。生产切换、部署启动、在线采集预算和外部消息仍需各自明确授权。

## 10. 实施范围

具体阶段顺序及文件清单见 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。本次先完成 Phase 0 和 Phase 1：独立合同、有限分钟日历、严格可见性、ReplayProvider、Parquet/DuckDB、CLI 与相应自动测试。Market State、similarity、forecast、dashboard、walk-forward 和真实 live provider 属于后续阶段，不通过占位成功或合成“真实行情”提前验收。
