# Strategy、Portfolio、Risk 与 Execution 接口合同

## 文档控制

- 文档状态：`Active`
- `owner_role`：`Architecture Owner`
- 目的：定义 `StrategySignal → PortfolioTarget → RiskDecision → ExecutionIntent` 的稳定、可审计且 fail-closed 的内部边界。
- 适用范围：未来研究、回测、Shadow 评估及受治理执行系统之间的内部对象。本文件不定义 Broker order、账户连接、真实订单提交或真实资金操作。
- `authoritative_for`：四个内部对象的 Schema 与语义；多策略输入和目标聚合边界；long-only/无杠杆不变量；Risk decision 枚举；幂等、过期、stale、duplicate、状态机及失败行为。
- 权威来源：[Architecture Baseline](../ARCHITECTURE.md)、[Risk Policy](../RISK_POLICY.md)、[Project Charter](../PROJECT_CHARTER.md)、[Testing Strategy](../TESTING_STRATEGY.md)、[Data Contracts](DATA_CONTRACTS.md) 及 [Round 1B 正式报告](../reports/phase-01-round-01b-native-lean-foundation-report.md)。风险限额、kill switch 与交易准入仍以 Risk Policy 和 `AGENTS.md` 为准。
- 更新触发条件：任一对象的字段、枚举、时间、版本、幂等或状态语义变化；Portfolio 聚合或 Risk policy 实现获准；ExecutionIntent 消费边界变化；发现风险绕过、重复处理或 stale 输入缺陷。
- 失败行为：任何缺失/未知版本、无效权重、过期或 stale 输入、重复冲突、不可用 Risk policy、无法证明 Portfolio 状态或 Risk 决策的情况，都不得产生可消费的 ExecutionIntent；失败必须有审计 reason code，不得静默降级。
- 验证要求：本文所有 `json` fenced block 必须可被标准 JSON 解析器读取；未来实现必须有 Schema、枚举、幂等、状态转换、long-only、无杠杆、过期和 Risk fail-closed 测试。

## 当前实现状态

| 分类 | 当前事实 |
|---|---|
| `IMPLEMENTED` | 与本合同直接相关的 StrategySignal、PortfolioTarget、RiskDecision 和 ExecutionIntent runtime 均未实现。仓库现有的 Round 1B C# Smoke 只验证 Native LEAN 基础设施，不是盈利策略，也不实现本边界。 |
| `CONTRACT_DECISION` | 本文件定义四个内部对象、唯一允许的 Risk decision 枚举、long-only/无杠杆不变量、幂等和 fail-closed 语义。 |
| `TBD` | 真实策略、Portfolio 聚合实现、独立 Risk engine、经批准的量化限额、ExecutionIntent consumer、OMS/order 映射和运行时状态存储均未实现或未批准。 |

当前没有真实策略进入该流程，没有 Broker 连接，没有 Paper 或 Live 启用，也没有真实订单或真实风险预算。本文 JSON 仅是合成合同示例，不能作为实现证据。

## 边界与职责

```text
StrategySignal
  → Portfolio validation and deterministic aggregation
  → PortfolioTarget
  → Independent Risk evaluation
  → RiskDecision
  → approved portion only
  → ExecutionIntent
```

| 边界 | `owner_role` | 可以做 | 禁止做 |
|---|---|---|---|
| Strategy | `Research Owner` | 根据已绑定数据产生策略提议 | 直接产生 order；绕过 Portfolio/Risk；控制 kill switch |
| Portfolio | `Portfolio Owner` | 校验、去重、聚合多个有效 signal，产生绝对目标仓位 | 将目标视为 Risk 批准；生成 Broker 字段；关闭 Risk 检查 |
| Risk | `Independent Risk Owner` | 独立评估一个 PortfolioTarget 并输出不可变 RiskDecision | 由 Strategy 作者绕过或篡改；在 policy/state 不明时默认批准 |
| Execution | `Execution Owner` | 仅把 APPROVED/PARTIALLY_APPROVED 的批准部分表达为 Broker-neutral ExecutionIntent | 消费 REJECTED 目标；把 intent 等同 order；自行扩大批准权重 |

kill switch 只属于独立控制，不属于 Strategy、Portfolio 或 Execution。本合同不授予任何运行或交易权限。

## 公共编码、时间与版本规则

- 所有对象必须包含 `schema_name`、`schema_version` 和不可变对象 ID。
- ID 是 opaque、非空字符串；同一对象 ID 的 payload 不可变。身份生成和 canonical hash 算法见 `TBD-SPR-008`。
- 所有 timestamp 使用带 `Z` 的 ISO-8601 UTC 字符串。
- `valid_until` 是包含性截止时间；处理时刻晚于它即为 expired。下游 `valid_until` 不得晚于任一直接上游对象的 `valid_until`。
- target、approved、rejected、confidence 等十进制值在 JSON 边界使用规范 Decimal string；禁止 `NaN`、`Infinity` 和科学计数法。
- `strategy_version` 标识策略逻辑，不等同 Git commit；`model_or_parameter_version` 标识该 signal 使用的参数集或未来经批准的 model artifact。
- 当前没有 ML model 实现。非 ML 策略的 `model_or_parameter_version` 必须对应 lifecycle 中的 `parameter_version`；ML 细分规则见 `TBD-SPR-010`。
- `data_version` 与 `data_manifest_sha256` 的语义由 [Data Contracts](DATA_CONTRACTS.md) 定义。
- `policy_version` 标识不可变 Risk rule set；相同值不得对应不同规则。
- 对象只表达绝对目标权重，不表达 share/order delta。

## StrategySignal Schema

能力状态：`TBD`。Schema 是 `CONTRACT_DECISION`。

| 字段 | 类型 | 约束 |
|---|---|---|
| `schema_name` | string | 必须为 `StrategySignal`。 |
| `schema_version` | string | 必填。 |
| `signal_id` | string | 必填、不可变、幂等键之一。 |
| `strategy_id` | string | 必填；策略稳定身份。 |
| `strategy_version` | string | 必填；不可变逻辑版本。 |
| `symbol` | string | 必填；与输入数据相应时点的 symbol 一致。 |
| `direction` | enum | 只允许 `LONG` 或 `FLAT`；禁止 `SHORT`、`BUY`、`SELL`。 |
| `target_weight` | Decimal string | 策略提议的绝对权重，范围 `[0,1]`；不是最终 PortfolioTarget。 |
| `signal_timestamp` | UTC timestamp | signal 产生时间，也是该对象的事件时间语义。 |
| `valid_until` | UTC timestamp | 不早于 `signal_timestamp`。 |
| `confidence` | Decimal string | 范围 `[0,1]`；仅为研究元数据，不构成 Risk 批准。 |
| `reason_code` | string | 必填、版本化的策略解释 code；不得包含自由文本 Secret。 |
| `data_version` | string | 必填。 |
| `data_manifest_sha256` | string | 必填，64 位小写十六进制 SHA-256。 |
| `input_as_of_time` | UTC timestamp | 必填；不得晚于 `signal_timestamp`。 |
| `model_or_parameter_version` | string | 必填。 |

`direction=FLAT` 时 `target_weight` 必须为 `"0"`；`direction=LONG` 时必须大于零。`confidence` 不得被默认用作权重乘数或风险豁免。

合成合同示例；不是实际策略 signal：

```json
{
  "schema_name": "StrategySignal",
  "schema_version": "1.0.0",
  "signal_id": "signal-synthetic-example-001",
  "strategy_id": "strategy-synthetic-example",
  "strategy_version": "strategy-example-v1",
  "symbol": "EXAMPLE",
  "direction": "LONG",
  "target_weight": "0.10",
  "signal_timestamp": "2024-01-02T21:00:05Z",
  "valid_until": "2024-01-03T21:00:05Z",
  "confidence": "0.60",
  "reason_code": "RESEARCH_EXAMPLE",
  "data_version": "example-bars-v1",
  "data_manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "input_as_of_time": "2024-01-02T21:00:04Z",
  "model_or_parameter_version": "parameters-example-v1"
}
```

## PortfolioTarget Schema

能力状态：`TBD`。Schema 是 `CONTRACT_DECISION`。

| 字段 | 类型 | 约束 |
|---|---|---|
| `schema_name` | string | 必须为 `PortfolioTarget`。 |
| `schema_version` | string | 必填。 |
| `target_id` | string | 必填、不可变。 |
| `portfolio_id` | string | 必填。 |
| `portfolio_version` | string | 目标计算规则版本。 |
| `portfolio_snapshot_id` | string | 绑定计算时使用的持仓、现金与估值快照。 |
| `symbol` | string | 必填。 |
| `target_weight` | Decimal string | 聚合后的绝对目标权重，范围 `[0,1]`。 |
| `input_signal_ids` | array[string] | 非空、去重、稳定排序，追踪全部输入。 |
| `strategy_versions` | array[object] | 对每个输入绑定 `strategy_id`、`strategy_version`、`model_or_parameter_version`。 |
| `aggregation_policy_version` | string | 必填；相同输入与版本必须产生相同 target。 |
| `calculation_timestamp` | UTC timestamp | 不早于全部输入的 `signal_timestamp`。 |
| `valid_until` | UTC timestamp | 不得晚于最早到期的输入 signal。 |
| `data_version` | string | 绑定用于目标计算的数据版本；多版本兼容规则必须显式。 |
| `data_manifest_sha256` | string | 必填。 |

合成合同示例；聚合数值只是算术示意，不是限额或策略建议：

```json
{
  "schema_name": "PortfolioTarget",
  "schema_version": "1.0.0",
  "target_id": "target-synthetic-example-001",
  "portfolio_id": "portfolio-synthetic-example",
  "portfolio_version": "portfolio-example-v1",
  "portfolio_snapshot_id": "snapshot-synthetic-example-001",
  "symbol": "EXAMPLE",
  "target_weight": "0.08",
  "input_signal_ids": [
    "signal-synthetic-example-001"
  ],
  "strategy_versions": [
    {
      "strategy_id": "strategy-synthetic-example",
      "strategy_version": "strategy-example-v1",
      "model_or_parameter_version": "parameters-example-v1"
    }
  ],
  "aggregation_policy_version": "aggregation-example-v1",
  "calculation_timestamp": "2024-01-02T21:00:06Z",
  "valid_until": "2024-01-03T21:00:05Z",
  "data_version": "example-bars-v1",
  "data_manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

## RiskDecision Schema

能力状态：`TBD`。Schema 与枚举是 `CONTRACT_DECISION`；当前没有独立 Risk engine 实现。

| 字段 | 类型 | 约束 |
|---|---|---|
| `schema_name` | string | 必须为 `RiskDecision`。 |
| `schema_version` | string | 必填。 |
| `decision_id` | string | 必填、不可变。 |
| `input_target_id` | string | 精确指向一个 PortfolioTarget。 |
| `decision` | enum | 只允许 `APPROVED`、`REJECTED`、`PARTIALLY_APPROVED`。 |
| `approved_target_weight` | Decimal string/null | 批准的绝对目标权重；REJECTED 时为 `null`。 |
| `rejected_weight` | Decimal string | 未批准的输入目标权重差额，非负。 |
| `risk_reason_codes` | array[string] | 必填；REJECTED/PARTIALLY_APPROVED 时非空。 |
| `decision_timestamp` | UTC timestamp | 不早于 target 计算时间。 |
| `valid_until` | UTC timestamp | 不得晚于 input target 的 `valid_until`。 |
| `policy_version` | string | 必填；绑定不可变 Risk rule set。 |
| `portfolio_snapshot_id` | string | 必须等于 input target 使用的 snapshot。 |

decision 不变量：

- `APPROVED`：`approved_target_weight` 等于 input `target_weight`，`rejected_weight="0"`。
- `PARTIALLY_APPROVED`：批准值非空且小于 input `target_weight`；`rejected_weight = input target_weight - approved_target_weight`，并至少有一个解释性 reason code。
- `REJECTED`：`approved_target_weight=null`，`rejected_weight` 等于 input `target_weight`，并至少有一个拒绝 reason code；不得产生 ExecutionIntent。
- Risk engine 不能把批准权重提高到 input target 之上。

本合同登记以下基础 `risk_reason_codes`；它们是未来合同 code，不代表已有 enforcement：

- `RISK_APPROVED`
- `RISK_LONG_ONLY_VIOLATION`
- `RISK_UNLEVERED_VIOLATION`
- `RISK_EXPIRED_INPUT`
- `RISK_STALE_INPUT`
- `RISK_DUPLICATE_CONFLICT`
- `RISK_DATA_QUALITY`
- `RISK_UNRECONCILED_STATE`
- `RISK_POLICY_UNAVAILABLE`
- `RISK_LIMIT_APPLIED`

增加或改变 reason code 语义需要新的 `policy_version`。不得把 [Risk Policy](../RISK_POLICY.md) 中尚属 hypothesis 的数值写成已批准阈值。

合成 APPROVED 示例；示例权重不是批准限额：

```json
{
  "schema_name": "RiskDecision",
  "schema_version": "1.0.0",
  "decision_id": "decision-synthetic-example-001",
  "input_target_id": "target-synthetic-example-001",
  "decision": "APPROVED",
  "approved_target_weight": "0.08",
  "rejected_weight": "0",
  "risk_reason_codes": [
    "RISK_APPROVED"
  ],
  "decision_timestamp": "2024-01-02T21:00:07Z",
  "valid_until": "2024-01-03T21:00:05Z",
  "policy_version": "risk-policy-example-v1",
  "portfolio_snapshot_id": "snapshot-synthetic-example-001"
}
```

## ExecutionIntent Schema

能力状态：`TBD`。Schema 是 `CONTRACT_DECISION`。ExecutionIntent 是内部、Broker-neutral 的目标表达，不是 order，不授权任何提交行为。

| 字段 | 类型 | 约束 |
|---|---|---|
| `schema_name` | string | 必须为 `ExecutionIntent`。 |
| `schema_version` | string | 必填。 |
| `intent_id` | string | 必填、不可变。 |
| `decision_id` | string | 必须指向 APPROVED 或 PARTIALLY_APPROVED RiskDecision。 |
| `input_target_id` | string | 必须等于 RiskDecision 的输入 target。 |
| `symbol` | string | 必须等于 input target symbol。 |
| `approved_target_weight` | Decimal string | 必须逐字等于 RiskDecision 批准值，不得扩大。 |
| `created_at` | UTC timestamp | 不早于 `decision_timestamp`。 |
| `valid_until` | UTC timestamp | 不得晚于 RiskDecision 的 `valid_until`。 |
| `execution_policy_version` | string | Broker-neutral intent 转换策略版本；当前实现为 TBD。 |
| `idempotency_key` | string | 同一批准决定只允许一个语义等价 intent。 |
| `processing_state` | enum | 初始只允许 `CREATED`；状态机见下文。 |

合成合同示例；不是 order 或运行授权：

```json
{
  "schema_name": "ExecutionIntent",
  "schema_version": "1.0.0",
  "intent_id": "intent-synthetic-example-001",
  "decision_id": "decision-synthetic-example-001",
  "input_target_id": "target-synthetic-example-001",
  "symbol": "EXAMPLE",
  "approved_target_weight": "0.08",
  "created_at": "2024-01-02T21:00:08Z",
  "valid_until": "2024-01-03T21:00:05Z",
  "execution_policy_version": "execution-contract-example-v1",
  "idempotency_key": "intent-key-synthetic-example-001",
  "processing_state": "CREATED"
}
```

## 多策略冲突与目标聚合

Portfolio 层必须按以下顺序处理：

1. 校验 Schema、版本、时间、data manifest、direction 和 Decimal。
2. 丢弃或拒绝 expired、stale、冲突 duplicate 以及不满足 point-in-time 的 signal，并记录 reason code。
3. 按 `portfolio_id`、`portfolio_snapshot_id` 和 symbol 分组。
4. 使用显式、版本化的 `aggregation_policy_version` 进行确定性聚合。
5. 对相同输入集合和版本产生相同 PortfolioTarget，并保留全部 `input_signal_ids`。
6. 将 target 送交独立 Risk；Portfolio 自身的校验不能替代 RiskDecision。

精确的策略预算、LONG 与 FLAT 冲突优先级、不同数据版本兼容、signal 缺失处理及 aggregation arithmetic 尚未批准，见 `TBD-SPR-002` 和 `TBD-SPR-003`。无法按已批准策略唯一确定结果时，不得猜测、按到达顺序取最后一条或产生 target。

## long-only、无杠杆与 exposure

- V1 StrategySignal、PortfolioTarget 和 RiskDecision 的目标权重均不得为负；`SHORT` 不属于允许枚举。
- `gross_exposure = Σ abs(approved_target_weight)`。
- `net_exposure = Σ approved_target_weight`。
- long-only 下必须有 `gross_exposure = net_exposure`。
- 无杠杆下批准后的 `gross_exposure <= 1`；未分配部分是 cash residual，不得自动放大其余 target。
- Portfolio 应拒绝明显违反上述不变量的输入，Independent Risk 必须再次独立检查；上游通过不构成风险豁免。
- 单一 symbol、sector、turnover、loss、drawdown、容量及其他量化阈值尚未批准。Risk Policy 中的初始假设不能写成已实现限制。

这些是不变量合同，不是对当前 runtime enforcement 的声明。

## 过期、stale、duplicate 与幂等

### 过期与 stale

- `processing_time > valid_until` 即 expired；expired signal 不得生成新 target，expired target 不得评估为批准，expired decision 不得生成 intent。
- stale 表示输入虽然尚未超过 `valid_until`，但数据、Portfolio snapshot 或 Risk context 已超过用途特定新鲜度。量化阈值见 `TBD-SPR-004`。
- 无 stale policy、时钟异常或 timestamp 不可比较时 fail closed。
- 下游重新评估必须产生新 ID 和版本化记录；不得改写旧决定。

### duplicate 与幂等

- 相同对象 ID 且 Schema 语义规范化后的 payload 完全相同：作为 replay 返回已有结果，不产生第二个下游副作用。
- 相同 ID 但 payload 不同：标记 `RISK_DUPLICATE_CONFLICT` 或对应层的冲突 reason code，停止该对象处理。
- 不同 signal ID 即使 payload 相同也不能自动视为同一业务意图；去重策略必须版本化。
- 一个 RiskDecision 最多产生一个语义等价 ExecutionIntent；重试必须使用相同 `idempotency_key`。
- 幂等不能绕过 expiry、stale 或当前 Risk state 的校验。

## processing state machine

这些是接口处理状态，不是 [Strategy Lifecycle](../research/STRATEGY_LIFECYCLE.md) 的策略准入状态。Lifecycle 文件尚未存在时，该相对引用必须在同一 Round 创建并验证。

| 对象 | 允许状态/转换 | 终止或失败状态 |
|---|---|---|
| StrategySignal | `RECEIVED → VALIDATED → PORTFOLIO_CONSUMED` | `REJECTED`、`EXPIRED`、`DUPLICATE_REPLAY`、`CONFLICT` |
| PortfolioTarget | `CREATED → RISK_PENDING → RISK_DECIDED` | `REJECTED`、`EXPIRED`、`CONFLICT`、`FAILED_CLOSED` |
| RiskDecision | 创建即不可变终态：`APPROVED`、`REJECTED` 或 `PARTIALLY_APPROVED` | 不允许原地改写；重新评估产生新 decision |
| ExecutionIntent | `CREATED → CONSUMED` | `EXPIRED`、`CANCELLED`、`FAILED_CLOSED` |

`CONSUMED` 只表示未来内部 Execution consumer 已接收 intent，不表示已经创建或提交 order。任何未列出的转换都禁止。故障恢复必须从不可变上游对象和审计记录重建，不得跳过 `RISK_PENDING/RISK_DECIDED`。

## fail-closed 行为

以下任一条件都不得生成可消费 ExecutionIntent：

- 未知 Schema/version、缺少必填字段、无法解析 Decimal/timestamp。
- signal/target/decision 已过期，或 stale policy/state 不可用。
- `data_version`/manifest 不匹配、数据质量不合格或 point-in-time 不可证明。
- duplicate ID 对应不同 payload。
- 多策略冲突无法由已批准 aggregation policy 唯一解决。
- direction/weight/exposure 违反 long-only 或无杠杆。
- Risk service、`policy_version`、Portfolio snapshot 或独立控制状态不可用。
- RiskDecision 是 REJECTED，或批准字段与 input target 不一致。
- Portfolio/account/order 状态未知或 unreconciled；具体账户/order 合同当前 Deferred。

失败必须记录对象 ID、Schema/version、reason code、timestamp 和适用 policy version，但不得记录 Secret。风险降低、强制退出及 kill-switch 行为由独立 Risk 控制；未解决的语义见 `TBD-SPR-009`，Strategy 不得自行替代。

## 测试与验证要求

当前没有上述 runtime，所以下列为实现前必须满足的合同测试，不是已通过能力：

- 本文四个 JSON 示例的标准解析器测试和每个 Schema 的必填/类型/枚举测试。
- Decimal 边界、负权重、超过 1、`NaN`/`Infinity`、direction/weight 一致性测试。
- signal→target→decision→intent 的 ID、版本、symbol、snapshot、data manifest 和有效期血缘测试。
- `valid_until` 逐层不延长、expired/stale/clock anomaly fail-closed 测试。
- 相同 ID/same payload 幂等 replay、same ID/different payload 冲突及并发重复测试。
- 多策略输入顺序扰动下聚合确定性和冲突拒绝测试。
- `APPROVED`、`REJECTED`、`PARTIALLY_APPROVED` 三种不变量及 REJECTED 不生成 intent 测试。
- Portfolio 漏检时 Independent Risk 仍拒绝 long-only/无杠杆违规的 bypass 测试。
- gross/net exposure property tests，以及未知 risk reason/policy version 拒绝测试。
- 允许和禁止 processing state 转换、重启重放和旧 decision 不可改写测试。
- ExecutionIntent 不含 Broker order 字段、批准权重不可扩大及 intent 不等同 order 的边界测试。

## TBD register

| tbd_id | 未实现或未确认内容 | 原因 | owner_role | 触发补充条件 | 未解决时的 fail-closed 行为 |
|---|---|---|---|---|---|
| `TBD-SPR-001` | 四个 Schema 的 transport、持久化、runtime 和审计实现 | 本轮只建立合同，当前无对应 pipeline | `Architecture Owner` | 任一 Strategy/Portfolio/Risk/Execution 代码开始前 | 合同对象不得被宣称可运行或可提交 |
| `TBD-SPR-002` | 多策略 deterministic aggregation arithmetic 与数据版本兼容规则 | 尚无真实策略、Portfolio 实现或验证证据 | `Portfolio Owner` | 第二个策略或首个 PortfolioTarget 实现进入评审前 | 无唯一聚合结果时不产生 target |
| `TBD-SPR-003` | 策略预算、LONG/FLAT 冲突优先级及 signal 缺失策略 | 需要受治理策略和风险预算，当前均未批准 | `Research Governance Owner` | 首个多策略候选被允许聚合前 | 不按到达顺序或 confidence 猜测；冲突输入拒绝 |
| `TBD-SPR-004` | signal、数据、Portfolio snapshot 与 Risk context 的 stale 量化阈值 | 需要测量延迟和用途证据，当前无批准值 | `Independent Risk Owner` | 首个运行时决策路径验收前 | 新鲜度无法证明时不产生新增风险 intent |
| `TBD-SPR-005` | concentration、sector、turnover、loss、drawdown、capacity 等量化 Risk rules | Risk Policy 当前只给出部分 hypothesis，未形成已批准 runtime policy | `Independent Risk Owner` | Phase 5 风险规则进入正式 gate 且有测试证据时 | 除 long-only/无杠杆外不得声称限额已实现；policy 缺失时拒绝 |
| `TBD-SPR-006` | `portfolio_snapshot_id` 的持仓、现金、估值与市场数据 Schema | 当前无 Portfolio state/reconciliation 实现 | `Portfolio Owner` | 首个 RiskDecision 实现设计前 | snapshot 不可重建时 Risk fail closed |
| `TBD-SPR-007` | ExecutionIntent consumer、OMS/order 映射与 order lifecycle | order/Broker contract 按用户要求 Deferred | `Execution Owner` | Paper Trading 前的独立 order contract Round 获批后 | intent 仅可记录/验证，不得转换或提交 order |
| `TBD-SPR-008` | 对象 ID、canonical payload、hash 与 idempotency-key 生成算法 | 需要 transport/persistence 技术决策 | `Architecture Owner` | 首个 runtime 实现合并前 | same ID/different payload 一律冲突；不得依赖不稳定序列化 |
| `TBD-SPR-009` | 风险降低、强制退出、kill switch 与 unreconciled state 下的精确目标语义 | 属于未来独立 Risk、OMS 和恢复合同，当前未实现 | `Independent Risk Owner` | 风险/执行联调或任何运行准入前 | 停止新增/扩大风险；Strategy 不得自发 order 或绕过控制 |
| `TBD-SPR-010` | ML model artifact 与 `model_or_parameter_version` 的细分 Schema | 当前无 ML model 或批准用途 | `Research Governance Owner` | 首个 ML 候选策略预注册前 | 只允许把字段解释为 parameter version；未知 model artifact 拒绝 |
