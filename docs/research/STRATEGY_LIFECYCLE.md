# 策略生命周期

## 文档元数据

| 项目 | 值 |
|---|---|
| 目的 | 定义策略从研究假设到退役的状态、允许/禁止活动、证据、审批、版本和回退规则。 |
| 适用范围 | 所有未来 strategy_id 及其 strategy version / parameter version；不把基础设施 smoke、文档或临时 notebook 自动视为策略。 |
| authoritative_for | lifecycle_state、合法状态转移、状态 gate、批准职责与版本继承规则。 |
| 权威来源 | [Project Charter](../PROJECT_CHARTER.md)、[Risk Policy](../RISK_POLICY.md)、[Review Bundle Policy](../REVIEW_BUNDLE_POLICY.md) 与 [Roadmap](../ROADMAP.md)。 |
| status | Draft；Round 1C 外部独立审阅冻结前不得标记 Active。 |
| 当前实现状态 | `CONTRACT_DECISION`。当前没有真实策略进入生命周期，没有状态 registry，没有真实风险预算。Round 1B Smoke 算法不是盈利策略，也没有 lifecycle_state。 |
| owner role | `Research Governance Owner`；独立风险批准由 `Independent Risk Owner`，运行批准由 `Human Approval Authority`，紧急暂停由独立控制执行。 |
| 更新触发条件 | 状态、gate、approval role、risk budget、版本规则、Shadow/Paper/Live 边界或 Review Bundle 政策经批准变化。 |
| 失败行为 | 状态、证据、版本、批准或风险预算不完整时 fail closed：保持/退回较低状态，停止晋级；未知运行状态进入 Suspended。 |
| 测试或验证要求 | 状态机 property tests、禁止转移、版本绑定、批准职责分离、过期证据、暂停/回滚/重启及 Review Bundle 验证。 |

## 1. 边界与当前安全状态

本文件定义状态，不证明任何状态已经启用。接口对象与 processing_state 由 [Strategy/Portfolio/Risk Contracts](../contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md) 定义；研究验证由 [Backtest Validation Standard](BACKTEST_VALIDATION_STANDARD.md) 定义。两类状态不得混用。

- 当前没有任何真实 `strategy_id`、策略批准或生命周期记录。
- Round 1B `Phase01DeterministicSmokeTest` 只验证 Native C# 基础设施，不属于本生命周期，不得进入 `Prototype` 或更高状态。
- `Shadow` 不发送 order，只记录隔离的 hypothetical 结果。
- `Paper` 仅代表未来 broker simulation，真实资金为 0；当前 `PAPER_TRADING_ENABLED=false`。
- `Limited Live` 必须在未来获得单独、明确的资本与风险批准；当前 `LIVE_TRADING_ENABLED=false`、`LIVE_ORDER_SUBMISSION_ALLOWED=false`。
- `Production` 不能由策略作者单独批准。
- 当前未批准任何真实风险预算；所有真实资本状态不可进入。

实现状态标签统一为 `IMPLEMENTED`、`CONTRACT_DECISION`、`TBD`。本文件所有未来状态定义均为 `CONTRACT_DECISION`，并不表示运行能力已实现。

## 2. 状态图

正常研究路径：

`Research Hypothesis → Prototype → Backtest Candidate → Validated Candidate → Shadow → Paper → Limited Live → Production`

控制状态：`Suspended`、`Retired`。`Suspended` 是可恢复的安全状态；`Retired` 是该 strategy version 的终态。

## 3. 各状态合同

### 3.1 Research Hypothesis

- **definition**：可证伪的研究问题，尚未形成可运行候选。
- **allowed activities**：描述机制、universe、signal、数据需要、benchmark、成本假设和可证伪条件。
- **prohibited activities**：策略有效性/盈利声明；使用 Paper/Live；发送 order；分配风险预算。
- **required inputs**：问题陈述、先验依据、预期失败模式、初始数据许可判断。
- **required evidence**：版本化 hypothesis 文档及与 [Backtest Standard](BACKTEST_VALIDATION_STANDARD.md) 一致的预注册草案。
- **approval_role**：`Research Governance Owner` 接受研究范围；不构成运行批准。
- **risk_budget**：`NO_REAL_CAPITAL`。
- **code_commit**：没有策略代码时为 `N/A`；所有文档仍绑定其 Git commit。
- **strategy_version**：`UNASSIGNED` 或研究命名空间中的草案标识，不得冒充批准版本。
- **parameter_version**：`N/A`。
- **data_version**：若引用数据必须记录；未使用数据时 `N/A` 并说明原因。
- **required_tests**：文档 schema、相对链接、Secret 与边界检查。
- **Review Bundle requirement**：中间研究 WIP 可按政策 `OPTIONAL`；作为正式 gate 提交时 `REQUIRED`。
- **exit criteria**：假设、可证伪条件、数据/许可需求和实验计划获研究治理接受，进入 Prototype；不可行则 Retired。

### 3.2 Prototype

- **definition**：用于离线探索的最小实现，未达到受治理回测候选标准。
- **allowed activities**：在隔离环境、合成或获准研究数据上编写/测试研究代码，记录所有探索。
- **prohibited activities**：Paper/Live、真实资金、order、生产数据未经许可使用、对外验证结论。
- **required inputs**：接受的 Research Hypothesis、初始 schema/data contract 和实验边界。
- **required evidence**：可运行代码、探索日志、依赖/数据来源和已知限制。
- **approval_role**：`Research Owner`；提升仍由 `Research Governance Owner` 审核。
- **risk_budget**：`NO_REAL_CAPITAL`。
- **code_commit**：必填且工作树可从 Git 重建。
- **strategy_version**：必填 Prototype 版本；不得继承未来批准。
- **parameter_version**：影响输出的参数必须版本化。
- **data_version**：任何输入数据均绑定 data manifest；无合格版本则停止。
- **required_tests**：单元、时间边界、无网络/无 broker、确定性与基础数据质量测试。
- **Review Bundle requirement**：普通探索 `OPTIONAL`；正式申请 Backtest Candidate 时 `REQUIRED`。
- **exit criteria**：接口、假设、参数空间、数据/成本计划可审阅，进入 Backtest Candidate；否则回退 Research Hypothesis 或 Retired。

### 3.3 Backtest Candidate

- **definition**：预注册完成、可执行受治理回测但尚未通过验证的候选。
- **allowed activities**：按锁定方案运行 train/validation/test、walk-forward、sensitivity 和 stress test。
- **prohibited activities**：反复查看 test 调参；Shadow/Paper/Live；省略成本或 bias 仍作策略结论。
- **required inputs**：Prototype、预注册、合格 data version/manifest、benchmark、cost model 与 parameter search 计划。
- **required evidence**：所有运行、失败、参数尝试、随机种子、Git commit、规范化输出及淘汰检查。
- **approval_role**：`Research Governance Owner` 允许受治理回测；不授权运行阶段。
- **risk_budget**：`NO_REAL_CAPITAL`。
- **code_commit**：锁定且每次结果显式绑定。
- **strategy_version**：候选版本必填；逻辑变化升版并重新评估。
- **parameter_version**：每组决策参数必填；搜索历史不可删减。
- **data_version**：必填并绑定 data manifest SHA。
- **required_tests**：Backtest Standard 的泄漏、成本、复现、OOS、bias、final holdings 和风险约束测试。
- **Review Bundle requirement**：申请 Validated Candidate 时 `REQUIRED`。
- **exit criteria**：全部验证通过且证据接受独立审阅，进入 Validated Candidate；失败则回退 Prototype/Research Hypothesis 或 Retired。

### 3.4 Validated Candidate

- **definition**：回测证据已按标准完成并经独立审阅，但尚未获得 Shadow 或交易授权的候选。
- **allowed activities**：修复非语义缺陷、准备 Shadow 设计和冻结可复现证据。
- **prohibited activities**：自动进入 Shadow/Paper/Live；把通过回测解释为资金授权；未升版改变逻辑/参数。
- **required inputs**：Backtest Candidate 全部证据、独立审阅结论和正式 Review Bundle。
- **required evidence**：可复现实验 identity、OOS/成本/稳定性/淘汰结果、limitations 和批准记录。
- **approval_role**：`Independent Reviewer` 接受研究证据；`Independent Risk Owner` 复核 Shadow 风险边界。
- **risk_budget**：`NO_REAL_CAPITAL`。
- **code_commit**：冻结证据对应 commit。
- **strategy_version**：冻结且不可静默修改。
- **parameter_version**：冻结且与批准逐项绑定。
- **data_version**：冻结并绑定 manifest；新数据版本须影响分析。
- **required_tests**：完整质量 gate、clean-source/可复现验证及 Review Bundle validation。
- **Review Bundle requirement**：`REQUIRED`。
- **exit criteria**：Shadow 设计、隔离、观测与停止规则单独批准后进入 Shadow；证据失效则回退 Backtest Candidate 或 Suspended。

### 3.5 Shadow

- **definition**：在实时或回放输入上隔离计算 hypothetical signal/target/risk decision/execution intent 的观测状态，绝不发送 order。
- **allowed activities**：记录延迟、数据质量、决策、漂移、故障和理论执行差异；演练暂停与恢复。
- **prohibited activities**：创建/发送 broker order、使用真实资金、绕过 Risk、把 execution intent 当 order。
- **required inputs**：Validated Candidate、隔离架构、只读数据许可、风险 policy version 和观测计划。
- **required evidence**：连续性、stale/duplicate、风险拒绝、故障注入、恢复、漂移和无 order path 证明。
- **approval_role**：`Independent Risk Owner` 与 `Operations Owner`；进入 Paper 还需 `Human Approval Authority`。
- **risk_budget**：`NO_REAL_CAPITAL`。
- **code_commit**：运行版本固定；变更走版本与回退规则。
- **strategy_version**：与 Validated Candidate 批准相同或作为新版本重新验证。
- **parameter_version**：固定；动态更新未批准。
- **data_version**：每个决策记录 source/data version 和 availability 语义。
- **required_tests**：长运行、断连、数据 stale、重复事件、风险 bypass、暂停/恢复与零订单断言。
- **Review Bundle requirement**：正式进入和退出 gate 均 `REQUIRED`。
- **exit criteria**：满足待批准的观察期和 operational gate 后方可申请 Paper；异常则 Suspended 或回退 Validated Candidate。

### 3.6 Paper

- **definition**：未来 broker simulation 状态；只用模拟资金，真实资本始终为 0。当前未实现且未批准。
- **allowed activities**：获批后测试 order lifecycle、partial/cancel/reject、reconnect、sync/reconciliation 与执行偏差。
- **prohibited activities**：真实资金、Live credential、真实 order submission、将环境名视作授权。
- **required inputs**：通过 Shadow gate、隔离 Paper 配置/权限、order/broker contract、reconciliation、kill switch 与恢复设计。
- **required evidence**：持续 Paper 结果、fills、偏差、未知状态阻断、reconciliation、restart 和 incident drills。
- **approval_role**：`Human Approval Authority`，并需 `Independent Risk Owner`、`Security Owner`、`Operations Owner` 签核。
- **risk_budget**：`SIMULATED_ONLY; real capital = 0`。
- **code_commit**：批准 commit 固定；运行记录逐次绑定。
- **strategy_version**：不得继承未经 Paper gate 的新版本。
- **parameter_version**：任何决策变更须升版并重新 gate。
- **data_version**：运行输入和重建数据均保留版本/manifest。
- **required_tests**：order state、幂等、fault/restart、reconciliation、kill switch、安全隔离及意外 Live 启用拒绝测试。
- **Review Bundle requirement**：`REQUIRED`。
- **exit criteria**：只能在未来单独 live-admission 审阅全部通过后申请 Limited Live；当前入口和出口均不可用。

### 3.7 Limited Live

- **definition**：受严格资本、symbol、时间和操作限制的未来真实风险状态；当前没有批准。
- **allowed activities**：仅在未来书面授权的最小边界内运行，并接受实时独立监控与人工停止。
- **prohibited activities**：超过批准预算/范围、自动扩容、策略作者单独批准、LLM 实时决策或 order path。
- **required inputs**：完整 Paper/Shadow 证据、live-admission checklist、资本授权、credential scope、reconciliation、kill switch、rollback 与 incident plan。
- **required evidence**：逐日风险、orders/fills、reconciliation、限额拒绝、异常、人工审阅和资本使用记录。
- **approval_role**：`Human Approval Authority` 与独立风险/安全/运营批准；策略作者不得单独批准。
- **risk_budget**：`TBD — NOT APPROVED`；本状态定义不批准任何金额或比例。
- **code_commit**：唯一批准 commit；未经审批的变更不得运行。
- **strategy_version**：唯一批准版本。
- **parameter_version**：唯一批准版本；热更新默认禁止。
- **data_version**：每个 decision/order audit trail 绑定。
- **required_tests**：全部 Paper 测试、live gate、limit/kill、reconciliation、credential、rollback 和恢复演练。
- **Review Bundle requirement**：`REQUIRED`，并需独立 validate_bundle。
- **exit criteria**：只有长期证据和新的正式批准可进入 Production；任何未知状态立即 Suspended。

### 3.8 Production

- **definition**：未来持续、受治理的真实风险状态；当前不可用。
- **allowed activities**：仅在批准的策略/参数/资本/市场/时间/控制边界内运行。
- **prohibited activities**：作者单独批准、静默升版、超预算、绕过 Risk/kill switch/reconciliation、无证据扩容。
- **required inputs**：Limited Live 长期证据、独立风险与运营能力、正式资本和服务批准。
- **required evidence**：持续净表现、风险、执行/回测偏差、reconciliation、incident、SLO、恢复与审阅历史。
- **approval_role**：`Human Approval Authority`；必须职责分离，策略作者不能单独批准。
- **risk_budget**：`TBD — NOT APPROVED`；分阶段变更逐次批准。
- **code_commit**：受发布与回滚控制的批准 commit。
- **strategy_version**：批准版本；新版本不继承旧授权。
- **parameter_version**：批准版本；变化触发重新评审。
- **data_version**：所有决策可追溯；数据源变更触发 gate。
- **required_tests**：持续监控、风险/kill/reconciliation、故障/恢复、版本漂移、secret 与 gate tests。
- **Review Bundle requirement**：首次进入、material change 和正式审阅均 `REQUIRED`。
- **exit criteria**：维持 gate；异常进入 Suspended；不再支持或假设失效进入 Retired。

### 3.9 Suspended

- **definition**：因风险、数据、执行、安全、证据或治理异常而暂停新增/扩大风险的安全状态。
- **allowed activities**：保存证据、撤销/降低已批准风险（未来由独立控制执行）、诊断、reconciliation、修复和离线验证。
- **prohibited activities**：新增或扩大风险、自动恢复、绕过独立批准、删除审计证据。
- **required inputs**：暂停 reason code、触发时间、最后已知状态、open-risk 处置和责任角色。
- **required evidence**：根因、影响、状态重建、修复测试、reconciliation、回滚与重新准入审阅。
- **approval_role**：任何独立风险/安全控制可紧急暂停；重新启用需原状态的完整 approval roles。
- **risk_budget**：新增风险为 0；现有风险仅按独立控制批准的降低路径处理。
- **code_commit**：记录暂停时和候选恢复 commit。
- **strategy_version**：记录暂停版本；新版本不能自动恢复。
- **parameter_version**：记录暂停版本；任何变化升版。
- **data_version**：记录最后可信和异常版本。
- **required_tests**：根因回归、状态 reload、reconciliation、risk/kill、恢复和证据完整性。
- **Review Bundle requirement**：诊断 WIP 依政策；正式重新准入 `REQUIRED`。
- **exit criteria**：经原状态 gate 重新批准后回到不高于先前状态；无法安全恢复则 Retired。

### 3.10 Retired

- **definition**：某一 strategy version 不再使用的终态；保留全部历史与审计证据。
- **allowed activities**：只读研究、post-mortem、证据保留和新 hypothesis 的来源引用。
- **prohibited activities**：重新启用原版本、发送 order、分配预算、覆盖或删除历史。
- **required inputs**：退役原因、最后状态、open-risk 为零/已处置证明和证据保留计划。
- **required evidence**：最终状态、reconciliation（如适用）、结果/limitations、owner 与退役批准。
- **approval_role**：`Research Governance Owner`；曾进入运行状态的还需 `Independent Risk Owner` 与 `Operations Owner`。
- **risk_budget**：无。
- **code_commit**：保留最后 commit，不再运行。
- **strategy_version**：原版本终结；重新研究必须创建新版本。
- **parameter_version**：冻结为历史记录。
- **data_version**：冻结为历史重建输入。
- **required_tests**：证据完整性、无 active authorization、无 open-risk 与审计保留验证。
- **Review Bundle requirement**：正式退役 gate `REQUIRED`。
- **exit criteria**：无；Retired 不可恢复。新研究从 Research Hypothesis 开始。

## 4. 状态转移规则

| from | allowed to | 必需条件 |
|---|---|---|
| Research Hypothesis | Prototype, Retired | 本状态 exit criteria |
| Prototype | Research Hypothesis, Backtest Candidate, Retired | 版本化代码/证据；回退保留历史 |
| Backtest Candidate | Prototype, Validated Candidate, Retired | Backtest Standard 与独立证据 |
| Validated Candidate | Backtest Candidate, Shadow, Suspended, Retired | Shadow 单独 gate；证据失效回退 |
| Shadow | Validated Candidate, Paper, Suspended, Retired | Paper 单独 gate；当前不可进入 |
| Paper | Shadow, Limited Live, Suspended, Retired | Limited Live 单独 live-admission；当前不可进入 |
| Limited Live | Paper, Production, Suspended, Retired | 新资本/Production 批准；当前不可进入 |
| Production | Limited Live, Suspended, Retired | 降级或独立暂停 |
| Suspended | 不高于暂停前状态, Retired | 根因解决、证据更新和完整重新批准 |
| Retired | 无 | 终态；新版本重新从 Research Hypothesis 开始 |

## 5. 禁止转移

以下一律 fail closed：跳过任一正常 gate；Backtest Candidate/Validated Candidate 直接进入 Paper 或 Live；Shadow 创建 order；Paper 使用真实资金；Suspended 新增风险；Retired 原版本重新启用；新 strategy/parameter/data version 继承旧批准；作者单独批准 Production；一次 Boolean 或环境名称授予运行权限。

## 6. 回退、回滚、暂停与重新启用

- **回退（lifecycle regression）**：证据不足或假设变化时移动到较低研究状态，保留原记录，禁止覆盖历史。
- **代码回滚**：切回旧 commit 只改变候选代码，不自动恢复旧批准；必须重新绑定策略/参数/数据和 gate。
- **暂停**：计划性停止进入 Suspended；记录 reason code、影响与恢复条件。
- **紧急暂停**：独立 kill switch/risk/security control 可无需策略作者同意阻止新增风险。真实控制尚未实现，但职责边界已确定。
- **退役**：版本进入 Retired 后不可恢复。
- **重新启用**：仅适用于 Suspended，且不得高于暂停前状态；全部过期证据、版本和批准重新验证。

## 7. 参数与版本规则

1. 策略逻辑、特征定义、signal 语义或依赖行为变化必须产生新 `strategy_version` 与 `code_commit`。
2. 任何影响决策的参数变化必须产生新 `parameter_version`；相同版本不得对应不同规范化 payload。
3. 批准绑定 `(strategy_id, strategy_version, parameter_version, code_commit, data_version, data manifest, policy_version)`，不绑定显示名称。
4. 新版本默认没有旧版本批准。material change 应回退到何种研究状态由 `SL-TBD-003` 决定；未决时按最高风险回退至 Backtest Candidate。
5. dynamic/online parameter update 当前禁止；未来需单独合同、测试与批准。

## 8. 测试与验证要求

实现状态机前必须以表驱动/property tests 覆盖全部合法与禁止转移、幂等 replay、同 ID 不同 payload、过期批准、版本变化、职责分离、未知状态、紧急暂停、回滚、Retired 终态和无授权默认拒绝。Shadow 测试必须断言无 order；Paper 测试必须断言真实资本为 0；Live gate 测试必须在当前默认配置下全部拒绝。

## 9. TBD register

| tbd_id | 未实现或未确认内容 | 原因 | owner role | 触发补充条件 | 未解决时的 fail-closed 行为 |
|---|---|---|---|---|---|
| SL-TBD-001 | lifecycle registry、event schema、持久化和审计实现 | 当前只有文档合同 | `Architecture Owner` | 首个真实策略记录创建前 | 不创建真实 lifecycle record，不晋级 |
| SL-TBD-002 | 各 gate 的具名审批人、职责分离和电子批准 schema | 当前只定义逻辑角色 | `Governance Owner` 与 `Human Approval Authority` | 首个 Validated Candidate 正式申请前 | 不签发批准 |
| SL-TBD-003 | strategy/parameter/data 变化的 materiality 分类及最低回退状态 | 尚无真实变更样本 | `Research Governance Owner` 与 `Independent Risk Owner` | 首个已验证候选发生版本变化时 | 默认回退 Backtest Candidate |
| SL-TBD-004 | Shadow/Paper/Limited Live/Production 的观察期、可靠性和数值 gate | 运行基础设施与数据尚未实现 | `Independent Risk Owner` 与 `Operations Owner` | 对应 Phase 开始且证据模型获审阅时 | 对应入口保持关闭 |
| SL-TBD-005 | Limited Live/Production risk budget、资本授权与扩容步骤 | 当前没有真实风险预算或资本批准 | `Human Approval Authority` | 未来 live-admission 独立审阅时 | 真实资本状态不可进入 |
| SL-TBD-006 | 暂停 reason codes、open-risk 降低动作和重新启用表单 | execution/reconciliation/kill switch 尚未实现 | `Independent Risk Owner` | Phase 5–6 独立控制实现前 | 未知状态保持 Suspended；不新增风险 |
