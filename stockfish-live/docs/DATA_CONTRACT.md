# StockFish Live 数据合同

本合同对应独立 `stockfish_live` 当前工程实现：严格分钟存储/replay、PIT 上下文、指数/宽度/情绪/行业、历史相似预测、评价与标准 HTTP feed。真实 feed 未配置、未实际联网，真实数据覆盖与投资有效性尚未验证。`MARKET_EDGE=NOT_ESTABLISHED`、`LIVE_TRADING=DISABLED`、`BROKER_CONNECTION=NONE`。

合同模型冻结、拒绝未知字段及 NaN/Inf；所有时刻必须携带时区，接收后统一到 `Asia/Shanghai`。证券身份采用 `六位代码.SH/SZ/BJ`，裸代码不能猜交易所；合同中的 `instrument_id` 必须已经规范化，且与 `exchange`、`symbol` 一致。身份语法合法不证明证券真实存在或具有历史交易资格。代码见 [common.py](../stockfish_live/common.py) 和 [contracts/market.py](../stockfish_live/contracts/market.py)。

## 分钟与时钟

`LIVE_MINUTE_V1` 的 `timestamp` 是完整一分钟区间的**结束标记**，`event_time` 属性与它相同，不额外保存第二个事件时钟。

| 内容 | Phase 1 约定 |
|---|---|
| 上午完整 bar | `09:31 … 11:30`，120 根 |
| 下午完整 bar | `13:01 … 15:00`，120 根 |
| replay tick | `09:30 … 11:30 / 13:00 … 15:00`，242 个 |
| 09:30 / 13:00 | 开始收集或恢复时钟，没有对应零长度 bar |
| 午休 | 不生成分钟、不补 bar、不累计午休缺口 |
| 分钟精度 | 秒和微秒均为零 |

若来源使用 START 标签，`normalize_minute(timestamp_label="START", ...)` 仅将事件时间加一分钟；原 `source_timestamp` 和 `available_at` 保持原值。转换后 bar 尚未完成、尚未公布或尚未可用时直接拒绝，不能平移回执时间制造可见性。分钟日历代码见 [calendar.py](../stockfish_live/calendar.py)，转换代码见 [normalizer.py](../stockfish_live/ingest/normalizer.py)。本轮不将独立竞价快照转换为分钟 bar，也未验证真实 provider 的收盘分钟聚合规则。

## MinuteBar 全字段

| 字段 | 类型 / 单位 / 约束 |
|---|---|
| `schema_version` | 固定 `LIVE_MINUTE_V1` |
| `instrument_id` | 规范证券或指数 ID，如 `000001.SH` |
| `exchange` | `SH / SZ / BJ`，与 ID 后缀一致 |
| `symbol` | 六位代码，与 ID 前六位一致 |
| `asset_type` | `STOCK / INDEX`；不得由同一数字代码推断类别 |
| `timestamp` | timezone-aware 完整分钟 END |
| `open / high / low / close` | 可为空；非空时为有限正数；STOCK 为 CNY/股，INDEX 为指数点，由 asset_type 分开，不跨单位运算或隐式猜测倍率 |
| `volume` | 可为空；非空时为有限非负**股数 / 本分钟**，不是累计量 |
| `turnover` | 可为空；非空时为有限非负**人民币元 / 本分钟**，不是累计额 |
| `price_adjustment` | `RAW / QFQ / HFQ`，默认 `RAW`；有效采集流只接受 `RAW` |
| `provider` | 非空来源标识 |
| `source_timestamp` | 可为空；非空时保留来源时刻，不用历史业务日代替 |
| `source_timestamp_basis` | `PUBLISHED / RECEIVED / UNKNOWN`，默认 `UNKNOWN` |
| `available_at` | 本记录真实可供系统使用的时刻，timezone aware |
| `source_id` | 原始响应字节的 SHA256，64 位小写十六进制；绑定父 `SourceReceipt` |
| `quality` | `VALID / PARTIAL / STALE / GAPPED / INVALID`，默认 `PARTIAL` |
| `pit_status` | `VERIFIED_PIT / RECONSTRUCTED / RESEARCH_ONLY / TEST_FIXTURE`，默认 `RESEARCH_ONLY` |

布尔值不能作为 OHLC、成交量或成交额。已知的 `low <= high`，已知 open/close 必须落在已知上下界内；即使其他字段缺失，也拒绝已能判定的矛盾。零量可以保存，缺量仍是 `None`。`VALID` 要求 OHLC、volume、turnover 和 source_timestamp 全部存在；数据齐全与历史 PIT 真实性分别表达。

显式单位转换支持 `volume_unit=SHARE/LOT`；LOT 必须提供正整数 `lot_size`，不默认一手等于 100 股。成交额支持 `CNY / CNY_1K / CNY_10K / CNY_100M`。缺字段不合成，累计量/累计额也不会自动差分为分钟量。QFQ/HFQ 可作为明确口径保存到合同和档案，Collector 拒绝它们进入 RAW 分钟状态。测试见 [test_contracts.py](../tests/test_contracts.py) 与 [test_normalizer.py](../tests/test_normalizer.py)。

## SourceReceipt 与来源完整性

| 字段 | 含义 |
|---|---|
| `source_id` | 原始响应字节 SHA256，与 bar 及归档一致 |
| `provider` | 与 bar 一致 |
| `source_timestamp` | 可为空的来源时刻 |
| `source_timestamp_basis` | 来源时刻依据，与 bar 一致 |
| `received_at` | 系统实际收到该响应的时刻 |
| `available_at` | 回执实际可用时刻，不能早于 received_at |
| `pit_status` | 来源证据等级，与对应 bar 一致 |

行时钟满足 `timestamp <= available_at`；存在来源时刻时满足 `timestamp <= source_timestamp <= available_at`。回执满足 `source_timestamp <= received_at <= available_at`（来源时刻为空时跳过该项）。父回执的收件不能早于完整 bar 的事件，父 received_at / available_at 不得晚于行 available_at；父来源时刻不得晚于行来源时刻。`validate_receipt` 同时核验 ID、provider、时间依据和 PIT 标记，实际消费再检查全部时钟不晚于 cutoff。

`PUBLISHED` 表示声明的来源发布时间，`RECEIVED` 表示响应收件时刻，`UNKNOWN` 保留未知依据。`VERIFIED_PIT` 必须存在 `PUBLISHED` 来源时刻；代码只校验合同声明与时钟，独立证据真实性仍需逐来源核实。当前交付没有验证真实分钟源，不能通过填写该标记取得真实历史就绪结论。

## Calendar 与 DatasetScope

`calendar.json` 必须是显式 `LIVE_CALENDAR_V1`：`sessions`、`coverage_start`、`coverage_end`、`exchanges`、`source`、`available_at`、`pit_status` 全部登记。空会话、重复、越界日期及无效时区拒绝；不按 weekday 推断交易日。`require_session` 分别校验范围、明确会话、日历实际可见时间和交易所范围。仅 ID 语法支持 BJ 不代表已有 BJ 日历；fixture 日期也不代表官方日历。

`dataset.json` 为 `LIVE_DATASET_V1`，字段为 `scope=DECLARED_SCOPE_ONLY`、`calendar_version`、`instruments[{instrument_id,asset_type}]`、`available_at`、`pit_status`。scope 不能为空、证券不得重复，calendar_version 必须匹配实际日历。scope 是**预期输入流**；它不证明全市场覆盖、ST/停牌/上市资格，也不提供任何证券的历史纳入或剔除依据。replay 从当日 09:30 开始，因此 scope 和日历必须在该时点已实际可见。测试见 [test_calendar.py](../tests/test_calendar.py) 和 [test_cli.py](../tests/test_cli.py)。

## MarketSnapshot 与采集状态

`LIVE_SNAPSHOT_V1` 字段为 `as_of`、可空 `data_cutoff`、`provider`、`bars`、`receipts`、`quality`、`warnings` 和固定 `scope=DECLARED_SCOPE_ONLY`。快照不可包含晚于 as_of 的行事件或 availability；Collector 继续验证父回执、声明身份类别、日历与来源等级。Provider 返回本轮新增可见记录，失败批次不推进已消费 cursor。

QualityMonitor 按到达顺序原子接收一批数据；缺父回执、未知身份、重复、旧 timestamp 回补、跨日重置、非 RAW 和 INVALID 均明确拒绝，整批失败不改变已接受状态。单个监控实例对应一个业务日。磁盘读取的排序不等于原始到达顺序，不能据此消除 late arrival。

研究 pipeline 的 Collector 使用 `LIVE_COLLECTION_V2` 配置和 `input_hash_algorithm=VISIBLE_BATCH_CHAIN_V1`，按已接受、实际可见的批次与父回执推进输入链。冻结 foundation 路径仍保留完整累计输入 hash。输入链的算法标记与配置一起保存，不能跨版本比较 hash 并推断输入相同。EMPTY 的原始来源也单独归档；其父时钟必须不晚于 snapshot.as_of。

`state(now)` 返回 `quality`、`warnings`、`gap_count`、最多 20 项 `missing_examples`、可空 `last_event_time` 和 `bar_count`。缺口按声明池 × 当时已完整结束的合法分钟累计；partial 行已出现但字段不全，仍保留 PARTIAL。默认落后至少两根完整交易分钟触发 `DATA_STALE`，任一声明证券落后即可触发；13:00 参考 11:30，不把午休计为过期。缺口持续保留、不补平盘或零量。Collector 的本轮严重程度依次为 `INVALID > GAPPED > STALE > PARTIAL > VALID`，保留 provider 明示的质量警告；拒绝批次的错误与已接受输入分开记录。

## Parquet 与 DuckDB

运行数据使用独立根目录，分钟路径为 `minute/date=YYYY-MM-DD/part-<content_sha256>.parquet`。OHLC/volume/turnover/source_timestamp 列可空，时间列为带 `Asia/Shanghai` 的微秒时间。父来源保存在 `sources/<source_id>.raw`、`.receipt.json`、`.manifest.json`；分钟分片另有同名 `.manifest.json`，记录业务日、行数、文件/schema/content SHA256 与 source_id。

[ParquetStore](../stockfish_live/storage/parquet_store.py) 在写入边界重新验证模型、原始字节 hash 和父回执；一个 append 对应一个业务日及一个父回执，跨分片的 `(instrument_id,timestamp)` 不得重复。单写者 OS 锁、独占 staging、fsync 和不可覆盖的原子 hard link 用于发布。来源与 manifest 可先落盘，Parquet 最后发布；读取只发现已发布 Parquet，未发布分片的孤立 manifest 不构成数据。中断后可用相同内容继续发布，既有 manifest 必须逐字一致；已经完成的同一 bar 再写会被拒绝，原历史不被覆盖。支持该原子发布方式的本地文件系统是当前实现前提，不能据此宣称其他文件系统已验收。

读取校验原始响应、回执、分片、schema、内容、日期与身份；篡改或缺必要证据失败。空读不创建目录或数据库。磁盘合同校验不能代替 Collector 的交易日/分钟时段/RAW/预期 scope 校验，也不等于研究就绪。

当前 source_id 同时以原始字节内容寻址，一个 source_id 只绑定一个不可变回执。同一原始 payload 若在另一时刻重新收到且需要不同 received_at/available_at，会报 `SOURCE_RECEIPT_CONFLICT`。这是当前不可变父来源合同的限制；支持同字节多次收件身份需另外版本化合同，不能通过修改旧回执时间解决。

[DuckDBStore](../stockfish_live/storage/duckdb_store.py) 使用内存 DuckDB，对完整性检查后的 Arrow 表执行固定、参数化可见性查询；保留行与父回执时钟过滤，再逐行验证。当前读取整日分片并做 hash，有成本，尚未验证真实全市场规模性能。存储测试见 [test_storage.py](../tests/test_storage.py)。

## 未知和后续字段

新增 `LIVE_CONTEXT_V1` 独立提供资格/ST/停牌、昨收/限价、PIT 行业成员、日线和覆盖；其字段仍不能从 ID、缺分钟或当前成员猜测。未提供的上市/退市区间、指数成分、主题、财报、新闻及事件证据继续为 UNKNOWN/UNAVAILABLE。严格访问规则及信任边界见 [PIT_POLICY.md](PIT_POLICY.md)。


## 单日多来源分钟 V2

`ParquetStore.append_many(batches)` 的每个 batch 为 `(bars, SourceReceipt, raw_bytes)`，整个导入必须同一业务日、身份不重复。输出 `LIVE_MINUTE_PARQUET_V2` manifest 登记全部 source_ids，每行仍保留本身 source_id 与真实可见时钟；不会把全日行情绑定到某个盘前回执。原始字节、父回执、OHLC/单位/时钟、既有身份冲突逐项校验后发布单日分片。读取兼容原单来源 V1；V2 不改变 `LIVE_MINUTE_V1` 行语义。多来源 writer/cache 和本地原子发布能力不能代替真实源验收。

## `LIVE_CONTEXT_V1`

`ContextBundle` 的 receipts 与每个上下文 record 用 source_id 绑定，保留 provider、source_timestamp/basis、available_at、pit_status、effective_from/effective_to。有效区间为左闭右开；业务上“有效”不表示当时已取得。读取需同时满足实际来源/父回执不晚于 cutoff；同 identity 同 available_at 的冲突修订拒绝。

| 记录 | 业务字段与未知语义 |
|---|---|
| `ReferencePrice` | instrument_id、trading_date、RAW previous_close/limit_up/limit_down；缺价格不从分钟倒推 |
| `SecurityStatus` | trading_eligible/is_st/suspended、LARGE/SMALL/UNKNOWN、前日限价/板高等已明确证据；未知布尔保留 None |
| `SectorMembership` | SW_LEVEL1、classification_version、sector_id/name、历史有效区间；当前成员不得回填历史 |
| `TurnoverHistory` | observation_date、1..240交易分钟序号、同位置累计人民币成交额；必须早于当日，默认至少5个历史日才给基准 |
| `DailyContext` | based_on_date、long_return_20d_pct/medium_return_5d_pct、昨日情绪；基准日早于当日，盘前实际可见 |
| `UniverseCoverage` | trading_date、明确instrument_ids、DECLARED_SCOPE_ONLY/COMPLETE_MARKET；完整市场声明与DatasetScope独立 |

`ContextStore` 追加 `context/bundle-<hash>.json` 与 manifest，父 raw/receipt 保存在 sources 并逐项核验。context 构造与归档不自动认证外部来源；VERIFIED_PIT 是需要独立证据的声明。主题完整性、证券身份真实性、RAW 行情、限价事件与历史资格继续分别判断。

## 市场状态 V2 主路径与 V1 参考兼容

输出含 as_of、data_cutoff、input_hash、config_hash、state_id、PIT 等级、scope、完成交易分钟数、indices、breadth、sentiment、sectors、environments、features、unavailable_features 和 warnings。五指数身份固定为 000001.SH/000300.SH/000905.SH/000852.SH/399006.SZ；缺指定指数保留UNAVAILABLE，不用同数字代码猜资产类别。

新演示默认 `MARKET_STATE_V2` / `MARKET_FEATURES_V2`。`StreamingMarketStateEngine.build(as_of, delta_bars, delta_receipts)` 按分钟追加统计，输出 `input_hash_kind=APPEND_ONLY_DELTA_CONTEXT_CHAIN_V1`，绑定实际 cutoff、已接受增量及可见 context；过去视图为 `FIRST_OBSERVED_CUTOFF`，不被后来的资料重算。滚动计算方式和过去视图语义参与特征 config_hash。

`MARKET_STATE_V1` / `MARKET_FEATURES_V1` 保留无状态 MarketStateEngine 对截至当时累计 prefix 的参考计算。私有 `research-config.json` 使用 `LIVE_RESEARCH_CONFIG_V1` 包装 ForecastConfig，由 `forecast.feature_version` 选择 V1/V2；配置缺失时沿用 V1 兼容默认。V2 与 V1 的字段单位一致，不代表特征时间语义或 hash 相同。新数据集、新 run 单独保存版本和 hash，不能只改版本字段复用旧训练材料。

指数 feature 包含 gap、昨收到当前收益、开盘以来收益、完整路径高低/回撤/区间位置、同位置成交额比、5/15/30交易分钟收益、实现波动和盘前长中期环境。指数价格是点数，股票价格是人民币/股；不跨价格单位相减。feature `_pct` 单位为百分数。缺完整窗口、prevclose、基准或 context 的字段保持 None。

宽度报告声明股票数、qualified_count、eligibility_unknown_count、实际收益分母和覆盖率；涨跌/中位数、成交额、限价、大小盘等各有自身可用性。完整市场字段只在独立完整inventory与声明股票池一致且资料完整时有效，声明池不升级为全市场。

情绪为 Live 专属初始规则且记录理由/输入缺项/config hash；行业为已声明历史版本成员的统计，变化必须过去/现在成员一致。未知标签不作FALSE，缺实际结果不填零。详细公式与阈值见 [MODEL_V1.md](MODEL_V1.md) 及对应 features 模块。

## feature、outcome 与 forecast

`HistoricalState` 使用 `SIMILARITY_STATE_V1`，含 state_id、规范instrument_id、decision_at、available_at、current_price、feature_version、features、context、pit_status、warnings。decision_at 是名义完整分钟标签；实际 available_at 不回拨。完整分钟内发生的真实 HTTP 延迟保留原秒/微秒，具体推理 as_of 同时保存。

context 保留 feature `configuration_hash`、input_hash 和 data_cutoff；模型仅匹配相同 feature_version 与特征配置 hash 的更早 state。forecast 的 configuration_hash 是模型配置，和特征配置 hash 分别追溯，不相互替代。

`MatureOutcome` 是独立 `MATURE_OUTCOME_V1`，含原 state/证券/decision、horizon、target_at、available_at、anchor_price/target_price、regime/version、future_path、pit_status、source_ids。须 decision_at < target_at <= available_at；path 有序、无重复，终点与目标价一致。由 `mature_outcomes` 生成时必须对应窗口每个合法交易分钟和父来源完整可见。缺 label 没有记录，不能代表零收益。

`ForecastConfig` 使用 `SIMILARITY_CONFIG_V1`，默认 K=50/min_samples=50，候选50/100/200、可选feature_names及距离/区间/freshness参数，全部 UNVALIDATED。configuration_hash 随配置内容变化。

`ForecastResult` 使用 `INTRADAY_PATH_V1`，含 forecast_id、model/feature_version、instrument/decision/as_of、current_price、state_id、input_hash/data_cutoff、configuration_hash、horizons、status、confidence、warnings、pit_status。每个 horizon 保存 target_at、available/selected样本数、五个return/price quantile、up_probability、六类regime分母/概率、距离、区间、训练维度/归一化/ID、top10解释与model_hash。分位单调、价格为正，price_quantile与当前价锚点一致；非空regime概率和为1，未知label不进分母。INSUFFICIENT没有伪造分布。

pipeline snapshot 登记 `forecast_record_semantics=APPEND_ONLY_DECISION_DELTAS`，`forecasts` 只放本轮 5 分钟决策新增记录，非决策分钟为空；`latest_forecast_ids` 是各指数最近记录的引用。完整预测保存在 run 的私有 ResearchStore。读取旧时点时只能解析截至所选时点已产生的记录，不能用后来的最近预测替换。

forecast/outcome return 采用小数收益（0.01=1%），与 feature `_pct` 单位分开。horizon为30m/60m/11:30/14:00/15:00，过期省略，+30/+60使用交易分钟。初始参数、市场优势与来源等级持续可见，不由有效合同升级为统计证据。

## ResearchStore、revision 与评价

独立 `LIVE_RESEARCH_PARQUET_V1` 分别追加 features/outcomes/forecasts，列为 record_id、event_time、available_at、instrument_id、pit_status、payload及payload_hash；manifest绑定schema/文件/内容/行数hash。读者按可信 now/cutoff 检查实际时钟；重复ID和fixture/真实来源混用拒绝。run内feature和forecast与私有历史材料分开保存，评价JSON在research/evaluations不可覆盖追加。

研究 pipeline 启动及 evaluate 另执行 `LIVE_LABEL_PROOF_V1`：核验不可变 raw/receipt、每个合法未来分钟及 source_ids、父来源实际 availability、state 价格锚点、horizon target/目标价、保存路径和 regime，以及最弱来源等级。缺父证据、标签提前可见或内容与真实归档不符均拒绝，不能只靠合法 ResearchStore manifest 使用标签。该校验仍不认证外部供应商或数据许可。

`FORECAST_REVISION_V1` 比较同一未来15:00目标的绝对q50价格，附5/15/30交易分钟lookback、原forecast ID、当前/原预期价、realized anchor change与remaining-return change。连续warning要求连续5分钟预期价一致变化。旧预测不改写。

`VALIDATION_REPORT_V1` 保存 chronological method、as_of、config/input hash、训练/验证ID、K选择、OOS行、缺项原因、baseline与指标、按证券/时间/horizon分组及限制。真实VERIFIED_PIT OOS、合成工程诊断、PIT未证明研究结果分别保存。没有真实样本时NOT_EVALUATED，真实指标为空，不引用fixture数字作投资结论。

标准HTTP feed的严格schema、实际capture时钟、EMPTY与无默认外部源见 [PROVIDER_FEED.md](PROVIDER_FEED.md)。本机没有真实feed连通或真实分钟覆盖证据。


`SAVED_FORECAST_EVALUATION_V1` 是独立保存预测后验合同：只消费原ForecastResult与已验证的state/outcome，保留forecast/config/model/input/actual-clock lineage、原distribution与成熟actual，分别返回真实/fixture/PIT未证明指标、分组和skipped。原预测不可用不补造，缺结果不计零，未保存baseline不重新计算。它与VALIDATION_REPORT_V1的回顾W/F诊断分别登记，CLI放在saved_forecast_posterior。
