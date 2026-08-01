# 数据接口合同

## 文档控制

- 文档状态：`Draft`
- `owner_role`：`Data Governance Owner`
- 目的：为项目中的 Bar、Tick、Corporate Action 与 Feature 定义统一、可审计的数据边界，以及时间、数值、质量、版本和失败语义。
- 适用范围：研究、回测以及未来受治理数据管道所交换或持久化的数据记录。本文件不定义数据供应商、许可条款、数据库实现或实时行情能力。
- `authoritative_for`：核心数据 Schema 字段；UTC 与 exchange timezone 语义；`event_time`、`availability_time`、`ingestion_time`、`as_of_time`；OHLCV 表示；数据质量枚举；`data_version` 与 data manifest 绑定；数据记录的 fail-closed 行为。
- 权威来源：[Data Governance](../DATA_GOVERNANCE.md)、[Architecture Baseline](../ARCHITECTURE.md)、[Project Charter](../PROJECT_CHARTER.md)、[Risk Policy](../RISK_POLICY.md)、[Testing Strategy](../TESTING_STRATEGY.md)、[ADR 0002](../adr/0002-lean-native-source-foundation.md) 及 [Round 1B 正式报告](../reports/phase-01-round-01b-native-lean-foundation-report.md)。若有冲突，遵循 `AGENTS.md` 规定的优先级；本文件不得降低上位治理要求。
- 更新触发条件：Schema 字段或枚举变化；时间、Decimal、质量或调整语义变化；新数据源/频率/Corporate Action/Feature 能力获准；manifest 格式变化；发现 look-ahead、survivorship、许可或数据完整性缺陷。
- 失败行为：未知 Schema 版本、无效字段、版本/manifest 不匹配或不能证明 point-in-time 可用性时，记录不得进入研究、回测或决策输入；不得静默修复、猜测或降级放行。
- 验证要求：本文所有 `json` fenced block 必须可被标准 JSON 解析器读取；未来实现必须满足本文的不变量、数据质量、时间、版本和 fail-closed 测试。

## 当前实现状态

| 分类 | 当前事实 |
|---|---|
| `IMPLEMENTED` | Round 1B 提交了固定 seed 的 SYNTH Daily LEAN fixture、map/factor 文件、fixture manifest、Golden 及 fixture 验证测试。它们只验证 Native C# 基础设施，且明确不是实际市场数据。 |
| `CONTRACT_DECISION` | 本文件定义未来数据边界的规范字段、时间、数值、质量、版本和失败语义。合同存在不等于运行时代码已经支持。 |
| `TBD` | 通用 Bar 管道、Tick、Corporate Action、Feature、生产数据源、稳定 instrument registry、完整 point-in-time 历史、交易日历服务及持久化实现均未实现或未批准。 |

当前 fixture 不追溯声明符合本文件的通用 Schema。fixture 中存在 factor 文件也不证明 split 或 dividend 处理能力；现有生成配置未生成这类事件。项目没有实时行情或完整 point-in-time 数据库。

## 公共表示规则

### Schema 与字段兼容性

- 每条记录必须携带 `schema_name` 和 `schema_version`。消费者只接受明确支持的版本。
- 字段名、枚举、reason code、版本值保持英文；说明文字以中文为主。
- 必填字段不得通过空字符串代替缺失。可空字段必须在对应 Schema 中明确允许 `null`。
- 新增可选字段必须保持旧消费者可拒绝或忽略的行为明确；删除、重命名、改变类型或改变语义需要新的 `schema_version`。
- `record_id` 是数据版本内的不可变记录标识；生成算法见 `TBD-DATA-008`。
- `instrument_id` 是跨 symbol rename 保持稳定的内部标识；登记机制尚未实现。

### 时间与 timezone

| 字段 | 规范语义 |
|---|---|
| `event_time` | 市场事件发生时间、Bar 区间结束时间或 Corporate Action 生效时间。 |
| `availability_time` | 在受治理来源和相应 entitlement 下，该事实最早可供本系统决策使用的时间。point-in-time 判断以此字段为准。 |
| `ingestion_time` | 本系统数据管道实际接收并记录该版本数据的时间。 |
| `as_of_time` | 构建快照、查询或回测视图的截止时间；它不是 `availability_time` 的别名。 |

- 四个时间字段均使用带 `Z` 的 ISO-8601 UTC 字符串。禁止无 timezone 的时间。
- `exchange_timezone` 使用 IANA timezone 名称，例如 `America/New_York`；不得用固定 UTC offset 代替交易所 timezone。
- `ingestion_time` 不得早于 `availability_time`；materialized view 中 `as_of_time` 不得早于 `ingestion_time`。
- 不定义全局 `event_time <= availability_time` 不变量。提前公告的 split、dividend 或 symbol rename 可以在生效前已知，即 `availability_time < event_time`。
- point-in-time 输入必须满足 `availability_time <= decision_time`，或在重建视图时满足 `availability_time <= as_of_time`。仅比较 `event_time` 不能防止 look-ahead。

### K 线时间语义

- Bar 覆盖半开区间 `[bar_start_time, bar_end_time)`。
- `event_time` 必须等于 `bar_end_time`。
- OHLCV 只汇总该区间和指定 `session` 内的事件。
- Daily Bar 的边界来自 exchange calendar 和 `exchange_timezone`，不得假设全年固定 UTC 收盘时间；半日市及 daylight-saving-time 必须由受治理日历决定。
- Bar 在 `availability_time` 之前不可供策略、Portfolio 或 Risk 使用。

### Decimal、整数与浮点

- 价格、现金、比率及其他要求十进制精确性的值，在 JSON 边界使用规范十进制字符串，例如 `"101.25"`；禁止科学计数法、`NaN` 和 `Infinity`。
- `volume`、`size`、`source_sequence` 等离散数量使用 JSON 整数且不得为负。
- Feature 或统计计算可以使用有限 JSON number，但必须声明 `value_type`、Feature 版本及输入版本。不得用浮点值生成记录身份、manifest checksum 或精确金额。
- 生产 Decimal precision、scale 和 rounding mode 尚未批准；在 `TBD-DATA-009` 解决前，不得宣称跨实现数值完全兼容。

## 数据层语义

| 术语 | 合同语义 |
|---|---|
| `Raw` | 来源 payload 与接收元数据的不可变保存；修订产生新记录/版本，不覆盖旧值。 |
| `Clean` | 可由 Raw 和版本化规范化规则重建的数据。 |
| `Adjusted` | 由 Raw/Clean 与受治理 Corporate Action 推导的数据；必须声明 `adjustment_method_version`，不得覆盖 Raw。 |
| `point-in-time` | 基于 `availability_time`、历史 universe、Corporate Action 和数据版本进行的选择约束；它不是独立价格调整层，也不等同于 Adjusted。 |

`price_basis` 只允许 `RAW` 或 `ADJUSTED`。`RAW` 记录的 `adjustment_method_version` 必须为 `null`；`ADJUSTED` 记录必须提供非空、不可变的方法版本。生产调整方法见 `TBD-DATA-004`。

## 公共质量与血缘字段

以下字段适用于四个核心 Schema；标为“条件”的字段由具体 Schema 决定是否必填。

| 字段 | 类型 | 要求 |
|---|---|---|
| `schema_name` | string | 必填；等于本节定义的 Schema 名称。 |
| `schema_version` | string | 必填；消费者必须显式支持。 |
| `record_id` | string | 必填；同一 `data_version` 内不可变且唯一。 |
| `instrument_id` | string | 必填；跨 symbol rename 稳定。当前登记能力为 TBD。 |
| `symbol` | string | 必填；表示相应 `event_time` 下有效的 symbol。 |
| `event_time` | UTC timestamp | 必填。 |
| `availability_time` | UTC timestamp | 必填。 |
| `ingestion_time` | UTC timestamp | 必填。 |
| `as_of_time` | UTC timestamp | 必填。 |
| `exchange_timezone` | IANA timezone | 必填。 |
| `session` | enum/null | Bar/Tick 必填；Corporate Action/Feature 可按 Schema 规定。 |
| `source` | string | 必填；受治理 source identifier，不得放 credential。 |
| `data_version` | string | 必填；不可变逻辑数据快照标识。 |
| `data_manifest_sha256` | string | 必填；对应 data manifest 原始字节的 64 位小写十六进制 SHA-256。 |
| `quality_status` | enum | 必填；`VALID`、`SUSPECT`、`QUARANTINED` 或 `REJECTED`。 |
| `quality_issues` | array[string] | 必填；无问题时为空数组。 |

`quality_issues` 只允许以下基础 reason code；扩展需要版本化合同：

- `MISSING`：期望区间、symbol 或字段缺失。记录缺失通常登记在 interval/manifest 质量结果中，而不是伪造一条 Bar。
- `DUPLICATE`：同一逻辑身份出现多条记录。
- `LATE`：在相应政策允许的时限之后才到达。
- `STALE`：超过相应决策用途允许的数据新鲜度。
- `INVALID`：Schema、时间、数值、日历、OHLC 或血缘不变量失败。

`LATE` 和 `STALE` 的量化时限尚未批准；见 `TBD-DATA-007`。不得在本文编造秒数或分钟数。

## Bar Schema

能力状态：`TBD`。以下字段是 `CONTRACT_DECISION`，不是对现有通用 Bar 管道的声明。

除公共字段外，Bar 必须包含：

| 字段 | 类型 | 约束 |
|---|---|---|
| `bar_start_time` | UTC timestamp | 区间起点。 |
| `bar_end_time` | UTC timestamp | 区间终点；必须晚于起点。 |
| `resolution` | enum | 当前合同允许 `1m`、`1h`、`1d`；支持何种频率由数据版本声明。 |
| `open` | Decimal string | 非负。 |
| `high` | Decimal string | 非负，且不小于 open、low、close。 |
| `low` | Decimal string | 非负，且不大于 open、high、close。 |
| `close` | Decimal string | 非负。 |
| `volume` | integer | 非负。 |
| `price_basis` | enum | `RAW` 或 `ADJUSTED`。 |
| `adjustment_method_version` | string/null | `RAW` 时为 `null`；`ADJUSTED` 时必填。 |

合成合同示例；不是实际行情或已实现能力：

```json
{
  "schema_name": "Bar",
  "schema_version": "1.0.0",
  "record_id": "bar-example-001",
  "instrument_id": "instrument-synthetic-example",
  "symbol": "EXAMPLE",
  "bar_start_time": "2024-01-02T14:30:00Z",
  "bar_end_time": "2024-01-02T21:00:00Z",
  "event_time": "2024-01-02T21:00:00Z",
  "availability_time": "2024-01-02T21:00:01Z",
  "ingestion_time": "2024-01-02T21:00:02Z",
  "as_of_time": "2024-01-02T21:00:02Z",
  "exchange_timezone": "America/New_York",
  "session": "REGULAR",
  "resolution": "1d",
  "open": "100.00",
  "high": "102.50",
  "low": "99.75",
  "close": "101.25",
  "volume": 1000,
  "price_basis": "RAW",
  "adjustment_method_version": null,
  "source": "synthetic-contract-example",
  "data_version": "example-bars-v1",
  "data_manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "quality_status": "VALID",
  "quality_issues": []
}
```

## Tick Schema

能力状态：`TBD`。项目当前没有 Tick 或实时行情能力。以下 Schema 仅为 `CONTRACT_DECISION`。

除公共字段外，Tick 必须包含 `tick_type` 和 `source_sequence`：

- `tick_type=TRADE`：必须有 Decimal string `price` 和非负整数 `size`。
- `tick_type=QUOTE`：必须有 Decimal string `bid_price`、`ask_price` 以及非负整数 `bid_size`、`ask_size`；`bid_price` 不得高于 `ask_price`。
- `source_sequence` 必须是非负整数；若来源无稳定 sequence，替代身份规则须在 source contract 中解决，当前为 TBD。
- `session` 只允许 `REGULAR`、`PRE_MARKET` 或 `AFTER_HOURS`。

合成 TRADE 示例；不是实时行情：

```json
{
  "schema_name": "Tick",
  "schema_version": "1.0.0",
  "record_id": "tick-example-001",
  "instrument_id": "instrument-synthetic-example",
  "symbol": "EXAMPLE",
  "tick_type": "TRADE",
  "price": "101.25",
  "size": 25,
  "source_sequence": 1,
  "event_time": "2024-01-02T20:59:59.100Z",
  "availability_time": "2024-01-02T20:59:59.200Z",
  "ingestion_time": "2024-01-02T20:59:59.300Z",
  "as_of_time": "2024-01-02T20:59:59.300Z",
  "exchange_timezone": "America/New_York",
  "session": "REGULAR",
  "source": "synthetic-contract-example",
  "data_version": "example-ticks-v1",
  "data_manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "quality_status": "VALID",
  "quality_issues": []
}
```

## Corporate Action Schema

能力状态：`TBD`。项目当前没有 Corporate Action ingestion、调整引擎或 point-in-time Corporate Action 数据库。以下 Schema 仅为 `CONTRACT_DECISION`。

公共字段之外必须包含 `action_id`、`action_type` 和类型化 `payload`。`event_time` 是 action 生效时间；`availability_time` 是 action 首次可知时间，两者允许前后顺序不同。

| `action_type` | 必填 payload | 语义 |
|---|---|---|
| `SPLIT` | `ratio_numerator`、`ratio_denominator` Decimal string | 新旧持仓数量比；两个值必须大于零。 |
| `DIVIDEND` | `cash_amount` Decimal string、`currency` | 每股现金金额及币种；金额不得为负。 |
| `SYMBOL_RENAME` | `old_symbol`、`new_symbol` | 同一 `instrument_id` 的 symbol 变更；两个 symbol 不得相同。 |

合成 SPLIT 示例；availability 早于生效时间是允许的：

```json
{
  "schema_name": "CorporateAction",
  "schema_version": "1.0.0",
  "record_id": "action-example-split-001",
  "action_id": "action-split-001",
  "instrument_id": "instrument-synthetic-example",
  "symbol": "EXAMPLE",
  "action_type": "SPLIT",
  "payload": {
    "ratio_numerator": "2",
    "ratio_denominator": "1"
  },
  "event_time": "2024-01-10T14:30:00Z",
  "availability_time": "2024-01-05T18:00:00Z",
  "ingestion_time": "2024-01-05T18:00:01Z",
  "as_of_time": "2024-01-06T00:00:00Z",
  "exchange_timezone": "America/New_York",
  "session": null,
  "source": "synthetic-contract-example",
  "data_version": "example-actions-v1",
  "data_manifest_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "quality_status": "VALID",
  "quality_issues": []
}
```

合成 DIVIDEND 示例：

```json
{
  "schema_name": "CorporateAction",
  "schema_version": "1.0.0",
  "record_id": "action-example-dividend-001",
  "action_id": "action-dividend-001",
  "instrument_id": "instrument-synthetic-example",
  "symbol": "EXAMPLE",
  "action_type": "DIVIDEND",
  "payload": {
    "cash_amount": "0.25",
    "currency": "USD"
  },
  "event_time": "2024-02-01T14:30:00Z",
  "availability_time": "2024-01-20T18:00:00Z",
  "ingestion_time": "2024-01-20T18:00:01Z",
  "as_of_time": "2024-01-20T18:00:01Z",
  "exchange_timezone": "America/New_York",
  "session": null,
  "source": "synthetic-contract-example",
  "data_version": "example-actions-v1",
  "data_manifest_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "quality_status": "VALID",
  "quality_issues": []
}
```

合成 SYMBOL_RENAME 示例：

```json
{
  "schema_name": "CorporateAction",
  "schema_version": "1.0.0",
  "record_id": "action-example-rename-001",
  "action_id": "action-rename-001",
  "instrument_id": "instrument-synthetic-example",
  "symbol": "OLDX",
  "action_type": "SYMBOL_RENAME",
  "payload": {
    "old_symbol": "OLDX",
    "new_symbol": "NEWX"
  },
  "event_time": "2024-03-01T14:30:00Z",
  "availability_time": "2024-02-20T18:00:00Z",
  "ingestion_time": "2024-02-20T18:00:01Z",
  "as_of_time": "2024-02-20T18:00:01Z",
  "exchange_timezone": "America/New_York",
  "session": null,
  "source": "synthetic-contract-example",
  "data_version": "example-actions-v1",
  "data_manifest_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "quality_status": "VALID",
  "quality_issues": []
}
```

## Feature Schema

能力状态：`TBD`。项目当前没有受治理 Feature registry 或 Feature pipeline。以下 Schema 仅为 `CONTRACT_DECISION`。

除公共字段外，Feature 必须包含：

| 字段 | 类型 | 约束 |
|---|---|---|
| `feature_name` | string | 稳定名称。 |
| `feature_version` | string | 绑定算法、参数和输入语义的不可变版本。 |
| `value` | finite JSON number、Decimal string 或 null | 类型由 `value_type` 决定；null 必须有质量 issue。 |
| `value_type` | enum | `FLOAT64`、`DECIMAL` 或未来版本明确增加的类型。 |
| `calculation_time` | UTC timestamp | 完成计算的时间；`availability_time` 不得早于全部输入可用时间和计算完成时间。 |
| `input_data_versions` | array[string] | 非空，列出全部输入数据版本。 |
| `input_manifest_sha256s` | array[string] | 与输入版本一一对应。 |

合成 Feature 示例；不是已实现 Feature 或投资信号：

```json
{
  "schema_name": "Feature",
  "schema_version": "1.0.0",
  "record_id": "feature-example-001",
  "instrument_id": "instrument-synthetic-example",
  "symbol": "EXAMPLE",
  "feature_name": "synthetic_return_example",
  "feature_version": "feature-example-v1",
  "value": 0.0125,
  "value_type": "FLOAT64",
  "calculation_time": "2024-01-02T21:00:03Z",
  "input_data_versions": [
    "example-bars-v1"
  ],
  "input_manifest_sha256s": [
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  ],
  "event_time": "2024-01-02T21:00:00Z",
  "availability_time": "2024-01-02T21:00:03Z",
  "ingestion_time": "2024-01-02T21:00:04Z",
  "as_of_time": "2024-01-02T21:00:04Z",
  "exchange_timezone": "America/New_York",
  "session": "REGULAR",
  "source": "synthetic-contract-example",
  "data_version": "example-features-v1",
  "data_manifest_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "quality_status": "VALID",
  "quality_issues": []
}
```

## split、dividend 与 symbol rename 处理

- Raw 记录永不因 Corporate Action 被原地重写。
- Adjusted 数据必须由明确的 Raw/Clean 版本、Corporate Action 版本和 `adjustment_method_version` 重建。
- 回测在任一决策时点只能使用当时已满足 `availability_time` 的 Corporate Action 知识；未来修订不得追溯改变已冻结 backtest，而应产生新 `data_version`、manifest 和结果。
- split ratio、dividend 金额及 symbol rename 链必须可以从 manifest 指向的输入重建。
- symbol rename 前后的记录共享同一 `instrument_id`；在相应 `event_time` 前后分别使用历史有效 symbol。
- split/dividend 调整方向、除息方法、total-return 处理和 rounding 尚未批准，见 `TBD-DATA-004`。

## session 语义

- `REGULAR`、`PRE_MARKET`、`AFTER_HOURS` 是唯一基础枚举。
- 不同 session 的 Bar/Tick 不得在未声明的情况下混合。
- exchange holiday、half-day、daylight-saving-time 和异常闭市由受治理 exchange calendar 决定。
- 数据版本必须说明是否包含 pre-market/after-hours；缺少声明时不得假定包含。

## data version 与 manifest 绑定

- `data_version` 标识一个不可变逻辑数据快照；相同值不得指向不同字节、Schema、时间区间或质量结果。
- `data_manifest_sha256` 绑定对应 manifest 的原始字节。manifest 至少记录 source/license identifier、instrument coverage、Schema/version、interval、timezone/calendar、文件 checksum、质量结果和生成/接收血缘。
- 每个 backtest 必须记录其完整 data manifest；仅记录目录名、最近版本或可变别名不合格。
- 数据修订、补录、去重、调整方法或质量状态变化均产生新 `data_version` 和新 manifest；不得修改已冻结 manifest 的业务值。
- manifest 不存在、hash 不匹配、文件 checksum 不一致或引用未知版本时必须失败。

## 不变量与 fail-closed 行为

1. 所有必填时间可解析为 UTC，且满足各 Schema 的时间关系。
2. Bar 满足 `low <= open <= high`、`low <= close <= high`，区间严格递增且 OHLCV 类型有效。
3. Decimal 字符串可被 Decimal 解析器无损读取；JSON number 必须有限。
4. 同一数据版本内记录身份唯一。相同 `record_id` 且规范化 payload 相同可作为幂等 replay；相同 ID 但 payload 不同是完整性冲突。
5. `quality_status=VALID` 时 `quality_issues` 必须为空；有 `INVALID` 时不得为 `VALID`。
6. `QUARANTINED`、`REJECTED` 或包含 `INVALID` 的记录不得进入下游研究、回测或决策输入。
7. `SUSPECT`、`LATE` 或 `STALE` 只有在用途特定、版本化政策明确允许时才可使用；政策缺失时 fail closed。
8. 缺失数据不得未经版本化规则静默 forward-fill、backfill 或插值。
9. late 修订不得改变已冻结结果；需要新数据版本、manifest 和重新验证。
10. Adjusted 记录必须可追溯至 Raw/Clean、Corporate Action 和调整方法版本。
11. point-in-time 查询无法证明 `availability_time` 时，该记录不可用于相应决策时点。

## 测试与验证要求

当前 Round 1B 测试只证明固定 SYNTH fixture 的有限不变量。未来实现至少需要：

- 本文每个 JSON 示例的标准解析器测试，以及 Schema 必填/类型/枚举测试。
- UTC、IANA timezone、DST、holiday、half-day 和 K 线半开区间测试。
- `event_time`/`availability_time`/`ingestion_time`/`as_of_time` 正反例和 point-in-time 防泄漏测试。
- OHLC、非负 volume/size、Decimal precision、非有限浮点拒绝测试。
- missing、duplicate、out-of-order、late、stale、invalid 和 quarantine/fail-closed 测试。
- 相同 ID 幂等 replay 与冲突 payload 拒绝测试。
- manifest hash、文件 checksum、数据修订、新版本和 frozen backtest 绑定测试。
- split、dividend、symbol rename、历史 symbol、调整可重建性测试。
- Raw 不可变、Clean/Adjusted/Feature 可重建和 Feature 输入血缘测试。

## TBD register

| tbd_id | 未实现或未确认内容 | 原因 | owner_role | 触发补充条件 | 未解决时的 fail-closed 行为 |
|---|---|---|---|---|---|
| `TBD-DATA-001` | 生产数据源、license/entitlement、instrument coverage 与 retention | 当前只有合成 fixture，尚未完成受治理数据源准入 | `Data Governance Owner` | 首个非合成数据源进入 Phase 2 准入前 | 不接收非合成数据，不声称有市场数据能力 |
| `TBD-DATA-002` | 四个核心 Schema 的持久化、transport 和兼容实现 | 本轮只建立文档合同，未实现数据管道 | `Architecture Owner` | 首个 Schema 实现设计或代码开始前 | 合同示例不得作为运行时 API；未知 payload 拒绝 |
| `TBD-DATA-003` | Tick source、sequence/identity 和新鲜度模型 | 当前无 Tick 或实时数据能力 | `Data Governance Owner` | Tick 数据进入任何研究或决策路径前 | 不接收 Tick，不降级伪装成实时行情 |
| `TBD-DATA-004` | split/dividend 调整方向、除息、total-return、rounding 及方法版本 | 当前 Corporate Action 和调整引擎未实现 | `Data Governance Owner` | 首个 Adjusted 数据集或 Corporate Action pipeline 准入前 | 仅可保留 Raw；不得生成或声明受治理 Adjusted 数据 |
| `TBD-DATA-005` | Feature registry、Feature 计算规范及输入兼容矩阵 | 当前无 Feature pipeline | `Research Governance Owner` | 首个 Feature 用于候选策略回测前 | Feature 不得进入候选策略证据 |
| `TBD-DATA-006` | 完整 point-in-time 历史、历史 universe、delisting 与修订查询 | 当前没有完整 point-in-time 数据库 | `Data Governance Owner` | 首个声称 point-in-time 的候选回测前 | 不得作 point-in-time、无 survivorship bias 或可晋级声明 |
| `TBD-DATA-007` | `LATE`/`STALE` 的用途特定量化阈值 | 需要数据频率、来源延迟和风险用途证据，当前没有批准值 | `Independent Risk Owner` | 数据进入 Portfolio/Risk 决策路径且有测量证据后 | 阈值缺失或状态不明时数据不可用于新增风险 |
| `TBD-DATA-008` | `instrument_id`/`record_id` 的生成、rename 链及冲突处理 | 当前无 instrument registry | `Data Governance Owner` | 首个多 symbol 或 Corporate Action 数据集准入前 | 无稳定身份的记录隔离，不跨 symbol 拼接 |
| `TBD-DATA-009` | Decimal precision、scale 和 rounding mode | 需结合数据源与计算实现独立验证 | `Data Governance Owner` | 首个通用 Schema 持久化实现合并前 | 不声称跨实现数值等价；精确值不得转为二进制浮点 |
| `TBD-DATA-010` | exchange calendar 来源及 pre-market/after-hours/half-day 规则 | 当前没有受治理日历服务 | `Data Governance Owner` | 首个非 fixture Bar 数据集或 session-aware 回测前 | 无法证明 session 边界时记录不得进入候选回测 |
