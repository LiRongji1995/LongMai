# StockFish Live Phase 1 数据合同

本合同对应独立 `stockfish_live` 的当前实现。Phase 1 交付分钟存储、来源校验和严格 replay；预测、全市场宽度、情绪、行业状态及实时 provider 尚未实现。`MARKET_EDGE=NOT_ESTABLISHED`、`LIVE_TRADING=DISABLED`、`BROKER_CONNECTION=NONE`。

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

`state(now)` 返回 `quality`、`warnings`、`gap_count`、最多 20 项 `missing_examples`、可空 `last_event_time` 和 `bar_count`。缺口按声明池 × 当时已完整结束的合法分钟累计；partial 行已出现但字段不全，仍保留 PARTIAL。默认落后至少两根完整交易分钟触发 `DATA_STALE`，任一声明证券落后即可触发；13:00 参考 11:30，不把午休计为过期。缺口持续保留、不补平盘或零量。Collector 的本轮严重程度依次为 `INVALID > GAPPED > STALE > PARTIAL > VALID`，保留 provider 明示的质量警告；拒绝批次的错误与已接受输入分开记录。

## Parquet 与 DuckDB

运行数据使用独立根目录，分钟路径为 `minute/date=YYYY-MM-DD/part-<content_sha256>.parquet`。OHLC/volume/turnover/source_timestamp 列可空，时间列为带 `Asia/Shanghai` 的微秒时间。父来源保存在 `sources/<source_id>.raw`、`.receipt.json`、`.manifest.json`；分钟分片另有同名 `.manifest.json`，记录业务日、行数、文件/schema/content SHA256 与 source_id。

[ParquetStore](../stockfish_live/storage/parquet_store.py) 在写入边界重新验证模型、原始字节 hash 和父回执；一个 append 对应一个业务日及一个父回执，跨分片的 `(instrument_id,timestamp)` 不得重复。单写者 OS 锁、独占 staging、fsync 和不可覆盖的原子 hard link 用于发布。来源与 manifest 可先落盘，Parquet 最后发布；读取只发现已发布 Parquet，未发布分片的孤立 manifest 不构成数据。中断后可用相同内容继续发布，既有 manifest 必须逐字一致；已经完成的同一 bar 再写会被拒绝，原历史不被覆盖。支持该原子发布方式的本地文件系统是当前实现前提，不能据此宣称其他文件系统已验收。

读取校验原始响应、回执、分片、schema、内容、日期与身份；篡改或缺必要证据失败。空读不创建目录或数据库。磁盘合同校验不能代替 Collector 的交易日/分钟时段/RAW/预期 scope 校验，也不等于研究就绪。

当前 source_id 同时以原始字节内容寻址，一个 source_id 只绑定一个不可变回执。同一原始 payload 若在另一时刻重新收到且需要不同 received_at/available_at，会报 `SOURCE_RECEIPT_CONFLICT`。这是 Phase 1 明确限制；支持多次收件身份需后续版本化合同，不能通过修改旧回执时间解决。

[DuckDBStore](../stockfish_live/storage/duckdb_store.py) 使用内存 DuckDB，对完整性检查后的 Arrow 表执行固定、参数化可见性查询；保留行与父回执时钟过滤，再逐行验证。当前读取整日分片并做 hash，有成本，尚未验证真实全市场规模性能。存储测试见 [test_storage.py](../tests/test_storage.py)。

## 未知和后续字段

当前合同不包含 ST、停牌、上市/退市区间、行业/主题成员、指数成分、涨跌停事件、连板、财报、新闻或真实全市场资格。这些输入状态为 `UNAVAILABLE`，不能从 ID、缺分钟、当前成员或日线结果推断为 FALSE/零。Phase 2 引入时须增加版本、历史有效区间、父来源和实际可见时间。严格访问规则及信任边界见 [PIT_POLICY.md](PIT_POLICY.md)。
