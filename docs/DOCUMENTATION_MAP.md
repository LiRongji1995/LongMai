# 项目文档地图

## 1. 目的

本文档登记项目文档的用途、唯一权威范围、生命周期、owner role、更新触发条件和替代关系，并定义文档冲突处理、更新与归档流程以及跨文档术语。它防止同一规则在多个文件中形成相互冲突的版本。

## 2. 适用范围与权威来源

- 适用范围：仓库中现有 41 份 tracked `.md` / `.txt` 文档，以及仅作未来规划登记的 Deferred 文档。
- 权威来源：当前 Round Prompt、[`AGENTS.md`](../AGENTS.md)、[`PROJECT_CHARTER.md`](PROJECT_CHARTER.md)、[`RISK_POLICY.md`](RISK_POLICY.md) 和 [`REVIEW_BUNDLE_POLICY.md`](REVIEW_BUNDLE_POLICY.md)。
- 本文档的 `status` 表示文档生命周期，不表示文档描述的系统能力已经实现，也不表示当前 Round 已完成最终外部复核或冻结。处于 `Active` 的政策与合同仍可描述未来 gate 和尚未实现的 runtime。
- 本文档处于 `Active`，是文档生命周期、权威路由和跨文档术语关系的当前权威来源。

## 3. 当前实现状态

- **已经实现：** 已登记全部 41 份 tracked 文档，并为每份文档提供全部治理字段；本 Round 六份合同/治理文档与 Round 1C 正式报告均为 `Active`。
- **合同决策：** 六份文档的 `Active` 表示其文档合同当前生效，不表示对应 runtime 已实现。Phase 2 Round 01 Corrective 正式报告是当前候选记录；最终外部复核前该 Candidate 仍未冻结。
- **未来规划：** Prompt 指定的未来文档仅以 `planning_state: Deferred` 登记，不创建占位文件，也不建立指向不存在文件的链接。
- 本文档没有未分配 owner role 或未定义触发条件的未决项。

## 4. 更新触发、owner、失败行为与验证

- **owner role：** `Governance Owner`。各领域文档由登记表中的 owner role 维护；跨领域冲突由 `Governance Owner` 协调，风险边界必须由 `Independent Risk Owner` 决定。
- **更新触发条件：** 文档新增、重命名、状态变化、权威范围变化、owner role 变化、替代关系变化或统一术语变化。
- **失败行为：** 缺少必填元数据、使用未允许的 `status`、出现两个相同范围的权威来源、存在无归属冲突或出现无效相对链接时，文档 gate 必须 fail closed；不得把该 Round 报告为正式完成。
- **验证要求：** 检查所有登记路径、相对链接、允许的状态值、唯一权威范围、替代关系双向一致性、owner role 和 update trigger；Deferred 项必须保持为普通代码文本而不是链接。

## 5. 生命周期状态

`status` 只允许以下值：

| status | 定义 |
|---|---|
| `Active` | 当前生效的文档。其权威仅限 `authoritative_for` 所列范围。 |
| `Draft` | 正在编写或等待批准；不取代 `Active` 文档，不证明能力已经实现。 |
| `Superseded` | 已被另一份生效文档替代；仅保留历史上下文。 |
| `Archived` | 不再维护的不可变历史记录；可作为当时证据，但不定义当前政策。 |

`Deferred` 不是 `status`。它只用于尚不存在的未来文档的 `planning_state`。

## 6. Owner role 词汇表

| owner_role | 职责范围 |
|---|---|
| `Governance Owner` | 文档治理、冲突裁决流程、Prompt 与正式报告约束。 |
| `Project Owner` | 项目 mandate、产品范围、Roadmap 和项目入口。 |
| `Repository Maintainer` | 贡献流程、PR 模板和协作规范。 |
| `Release Manager` | 版本与发布历史。 |
| `Architecture Owner` | 系统边界、依赖方向与接口架构。 |
| `Independent Risk Owner` | 风险政策、批准/拒绝边界、kill switch 与真实资金 gate。 |
| `Data Governance Owner` | 数据来源、许可、schema、血缘、质量与版本。 |
| `Security Owner` | secret、credential、依赖安全与安全事件边界。 |
| `QA Owner` | 测试战略、验证层级与质量 gate。 |
| `Review Bundle Maintainer` | Review Bundle 政策、生成和验证 machinery。 |
| `Configuration Owner` | 配置 schema、默认值和环境隔离。 |
| `Platform Owner` | Docker、构建环境和未来部署平台。 |
| `Build Owner` | 可重现构建、deterministic identity 与构建产物证据；不拥有运行准入。 |
| `LEAN Integration Owner` | 锁定的 LEAN 基础、fixture、Golden 与 Native 验证。 |
| `Research Governance Owner` | 回测标准、策略证据和策略生命周期。 |
| `Research Owner` | 研究实现、实验执行和成本/参数证据准备；不能独立批准自己的候选。 |
| `Portfolio Owner` | 未来 portfolio target 聚合、版本与组合不变量。 |
| `Execution Owner` | 未来 risk-approved execution intent 边界；不代表 Broker 或 order 能力已实现。 |
| `Operations Owner` | 运行手册、监控、恢复和运营流程。 |
| `Independent Reviewer` | 独立复核研究、实现、证据和 Review Bundle，不由策略作者替代。 |
| `Human Approval Authority` | 未来 Paper/Live、资本与运行 gate 的明确人工批准；当前未指派具体人员。 |
| `Execution/OMS Owner` | 未来 order、OMS、broker adapter 与 reconciliation 合同。 |
| `Product Owner` | 未来产品需求、用户范围和支持承诺。 |
| `API Owner` | 未来 public API 边界和版本。 |
| `Compliance Owner` | 未来监管、法律和数据使用边界。 |

## 7. 文档冲突优先级

发生冲突时严格使用以下顺序：

1. 当前显式用户指令；
2. [`AGENTS.md`](../AGENTS.md)；
3. [`PROJECT_CHARTER.md`](PROJECT_CHARTER.md) 与 [`RISK_POLICY.md`](RISK_POLICY.md)；
4. [`REVIEW_BUNDLE_POLICY.md`](REVIEW_BUNDLE_POLICY.md)；
5. [`ROADMAP.md`](ROADMAP.md)；
6. 当前最新正式报告；当前为 [`phase-02-round-01-governed-bar-contract-kernel-report.md`](reports/phase-02-round-01-governed-bar-contract-kernel-report.md)；
7. [`README.md`](../README.md)。

同一优先级内先按 `authoritative_for` 的领域范围判断。仍无法消解时必须 fail closed，由对应领域 owner role 与 `Governance Owner` 共同解决；不得仅因文件较新就自动覆盖。正式报告记录特定 commit 和时间点的事实，不能改写政策；ADR 只能决定其明确范围，不能覆盖更高优先级 gate。

## 8. 唯一权威与禁止重复定义

1. 同一规则只能有一个 `Active` 权威来源。其他文档必须链接引用，不得复制后再独立演化。
2. 本地图负责文档路由、生命周期和术语索引，不重写风险、数据或 Review Bundle 的详细规则。
3. 本 Round 六份文档已经转为 `Active`；其 `authoritative_for` 当前生效，但不把任何 `TBD` 或 `CONTRACT_DECISION` 冒充 runtime 实现。
4. 当前唯一权威路由如下：

| 领域 | 唯一权威来源 |
|---|---|
| Agent 工作规则与冲突层级 | [`AGENTS.md`](../AGENTS.md) |
| mandate、V1 范围与最高层 gate | [`PROJECT_CHARTER.md`](PROJECT_CHARTER.md) |
| 风险默认值、风险 gate 与 kill switch | [`RISK_POLICY.md`](RISK_POLICY.md) |
| Review Bundle 触发、内容、验证与保留 | [`REVIEW_BUNDLE_POLICY.md`](REVIEW_BUNDLE_POLICY.md) |
| Phase 顺序与计划 gate | [`ROADMAP.md`](ROADMAP.md) |
| 逻辑架构与模块边界 | [`ARCHITECTURE.md`](ARCHITECTURE.md)；具体已接受决定由对应 ADR 负责 |
| 数据治理与许可 | [`DATA_GOVERNANCE.md`](DATA_GOVERNANCE.md) |
| 数据 schema 与时间语义 | [`DATA_CONTRACTS.md`](contracts/DATA_CONTRACTS.md) |
| Strategy→Portfolio→Risk→Execution 接口 | [`STRATEGY_PORTFOLIO_RISK_CONTRACTS.md`](contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md) |
| 回测验证与研究证据 | [`BACKTEST_VALIDATION_STANDARD.md`](research/BACKTEST_VALIDATION_STANDARD.md) |
| 策略状态与转移 | [`STRATEGY_LIFECYCLE.md`](research/STRATEGY_LIFECYCLE.md) |
| Native LEAN 本地验证步骤 | [`LOCAL_RUNBOOK.md`](operations/LOCAL_RUNBOOK.md) |
| secret 与 credential 安全 | [`SECURITY.md`](SECURITY.md) |
| 测试分类与 CI 原则 | [`TESTING_STRATEGY.md`](TESTING_STRATEGY.md) |
| 锁定的 Native LEAN source 决定 | [`ADR 0002`](adr/0002-lean-native-source-foundation.md) |
| 当前 Round 候选状态 | [`Phase 2 Round 01 Corrective`](reports/phase-02-round-01-governed-bar-contract-kernel-report.md)；本 Round 为 corrective 候选实施并等待外部独立审阅，尚未冻结 |
| 已冻结 Native Round 历史证据 | [`Round 1B 正式报告`](reports/phase-01-round-01b-native-lean-foundation-report.md) |
| 跨文档统一术语与详细权威路由 | 本文档第 12 节；短版 [`GLOSSARY.md`](GLOSSARY.md) 已被本地图取代并仅保留历史上下文 |

## 9. 现有文档登记

### 9.1 根目录、协作与目录说明

| document | purpose | authoritative_for | status | owner_role | update_trigger | related_documents | supersedes | superseded_by |
|---|---|---|---|---|---|---|---|---|
| [`.github/pull_request_template.md`](../.github/pull_request_template.md) | 提醒 PR 范围、证据和安全检查 | PR 提交界面的最低检查项，不改写正式 gate | Active | Repository Maintainer | PR gate 或必需证据变化 | `AGENTS.md`; `CONTRIBUTING.md`; `REVIEW_BUNDLE_POLICY.md` | — | — |
| [`AGENTS.md`](../AGENTS.md) | Agent 开发宪章 | 任务开始/结束、检查、提交与冲突层级 | Active | Governance Owner | 明确治理批准或 gate 变化 | Charter; Risk Policy; Review Bundle Policy; Roadmap | — | — |
| [`CHANGELOG.md`](../CHANGELOG.md) | 记录版本变化 | 已发布或已打 tag 的变化历史 | Active | Release Manager | 发布、tag 或正式版本变化 | `README.md`; reports index | — | — |
| [`CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md) | 定义协作行为 | 社区行为底线 | Active | Repository Maintainer | 社区治理规范变化 | `CONTRIBUTING.md`; `SECURITY.md` | — | — |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md) | 提供贡献入口 | 分支、测试、安全和提交基本流程 | Active | Repository Maintainer | 开发流程或质量 gate 变化 | `AGENTS.md`; PR template; Testing Strategy | — | — |
| [`README.md`](../README.md) | 项目介绍与入门 | 高层概览和首次使用入口；不作为当前 Phase 真相源 | Active | Project Owner | Phase、能力、依赖、CLI、布局或入门命令变化；当前已触发 | Charter; Roadmap; ADR 0002; Round 1B report | — | — |
| [`config/README.md`](../config/README.md) | 说明配置目录 | 示例用途与环境隔离原则 | Active | Configuration Owner | 配置目录、profile、flag 或安全边界变化 | Security; Risk Policy; Local Runbook | — | — |
| [`data/README.md`](../data/README.md) | 说明数据目录 | Git 数据排除和数据层目录边界 | Active | Data Governance Owner | 数据目录、保留、ignore 或分层政策变化 | Data Governance; Data Contracts | — | — |
| [`docker/README.md`](../docker/README.md) | 说明 Docker scaffold | 当前容器能力和排除内容 | Active | Platform Owner | Dockerfile、Compose、profile 或入口变化 | `README.md`; Security; ADR 0002; Local Runbook | — | — |
| [`integrations/lean/README.md`](../integrations/lean/README.md) | 说明 Native LEAN 目录 | smoke foundation、外部 source 与合成 fixture 边界 | Active | LEAN Integration Owner | 算法、fixture 布局、结果位置或 source 发现方式变化 | ADR 0002; fixture README; Local Runbook; Round 1B report | — | — |
| [`integrations/lean/data/README.txt`](../integrations/lean/data/README.txt) | 声明 SYNTH fixture | fixture 来源、seed、组成和禁止用途 | Active | LEAN Integration Owner | fixture、seed、LEAN commit、manifest 或允许用途变化 | fixture manifest; Golden; LEAN README; Local Runbook | — | — |
| [`tests/integration/README.md`](../tests/integration/README.md) | 说明 integration tests 目录 | isolated fixture 和禁止 broker order 的原则 | Active | QA Owner | integration scope、fixture 或外部依赖规则变化；当前 Phase 描述已触发更新 | Testing Strategy; LEAN README; Local Runbook | — | — |

### 9.2 核心治理文档

| document | purpose | authoritative_for | status | owner_role | update_trigger | related_documents | supersedes | superseded_by |
|---|---|---|---|---|---|---|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 定义逻辑架构 | 模块边界、控制流、风险不可绕过和追溯原则 | Active | Architecture Owner | 组件边界、依赖方向或控制路径变化 | Charter; Risk Policy; ADRs; interface contracts | — | — |
| [`DATA_GOVERNANCE.md`](DATA_GOVERNANCE.md) | 定义数据治理 | 数据层、来源许可、血缘、manifest 和质量政策 | Active | Data Governance Owner | source、license、schema、质量、保留或 lineage 变化 | Data Contracts; Backtest Standard; data README | — | — |
| [`GLOSSARY.md`](GLOSSARY.md) | 保存原短术语表 | 激活本地图之前的术语历史上下文；不再定义当前跨文档术语 | Superseded | Governance Owner | 不原地更新；当前术语只在 Documentation Map 或对应详细权威中更新 | Documentation Map; contracts; Lifecycle; Risk Policy | — | `docs/DOCUMENTATION_MAP.md` |
| [`PROJECT_CHARTER.md`](PROJECT_CHARTER.md) | 定义项目 mandate | V1 范围、成功条件、非目标和最高层 gate | Active | Project Owner | mandate、范围、时间线或最高层 gate 经批准变化 | Roadmap; Architecture; Risk Policy | — | — |
| [`REVIEW_BUNDLE_POLICY.md`](REVIEW_BUNDLE_POLICY.md) | 定义 Clean Review Bundle 政策 | 触发、内容、状态、来源、验证、命名与保留 | Active | Review Bundle Maintainer | 显式用户要求、安全缺陷或验收所需 machinery 缺陷 | `AGENTS.md`; reports index; Local Runbook | — | — |
| [`RISK_POLICY.md`](RISK_POLICY.md) | 定义风险政策 | deny-by-default、risk gate、kill switch 与真实资金准入 | Active | Independent Risk Owner | 风险限制、gate、kill switch 或资本准入经批准变化 | Charter; Architecture; interface contracts; Lifecycle | — | — |
| [`ROADMAP.md`](ROADMAP.md) | 定义 24 周计划 | Phase 顺序、目标和计划 gate；不表示当前完成状态 | Active | Project Owner | 经批准的 Phase 顺序、范围或 gate 变化 | Charter; current formal report | — | — |
| [`SECURITY.md`](SECURITY.md) | 定义安全政策 | secret、credential、日志和泄露处置基本规则 | Active | Security Owner | credential、日志、依赖安全或事件政策变化 | Config README; Review Bundle Policy; deferred Incident Response | — | — |
| [`TESTING_STRATEGY.md`](TESTING_STRATEGY.md) | 定义测试战略 | 测试分类与 CI 安全原则；不作为当前测试清单 | Active | QA Owner | 测试层级、CI、必需 gate 或故障模型变化；当前实现描述已触发更新 | `AGENTS.md`; contracts; Backtest Standard; Local Runbook | — | — |

### 9.3 ADR、Prompt 与报告索引

| document | purpose | authoritative_for | status | owner_role | update_trigger | related_documents | supersedes | superseded_by |
|---|---|---|---|---|---|---|---|---|
| [`docs/adr/README.md`](adr/README.md) | 说明 ADR 用途 | ADR 管理入口 | Active | Architecture Owner | ADR 流程或新增 ADR | ADR 0001; ADR 0002 | — | — |
| [`ADR 0001`](adr/0001-initial-architecture.md) | 记录初始架构假设 | 尚未完成审计的 Python、Alpaca 等假设 | Draft | Architecture Owner | 各假设被接受、拒绝或由新 ADR 取代 | Architecture; Roadmap; ADR 0002 | — | ADR 0002（仅 LEAN foundation 部分） |
| [`ADR 0002`](adr/0002-lean-native-source-foundation.md) | 记录 Native LEAN 决定 | 锁定 LEAN commit、SDK、外置只读 source 和 foundation 限制 | Active | LEAN Integration Owner | LEAN commit、SDK、build path、Docker 或 Python.NET 决定变化；应新增 ADR | ADR 0001; LEAN README; Phase 1 audits; Round 1B report | ADR 0001（仅 LEAN foundation 部分） | — |
| [`docs/prompts/README.md`](prompts/README.md) | 定义 Prompt 治理入口 | Prompt 数据、secret 与 LLM 实时边界 | Active | Governance Owner | Prompt 数据分类或 LLM 安全边界变化 | `AGENTS.md`; Security; Charter | — | — |
| [`docs/reports/README.md`](reports/README.md) | 说明正式报告用途 | 报告目录与最低证据入口 | Active | Governance Owner | 报告格式、证据字段或状态治理变化 | `AGENTS.md`; Review Bundle Policy | — | — |

### 9.4 正式报告与技术审计

| document | purpose | authoritative_for | status | owner_role | update_trigger | related_documents | supersedes | superseded_by |
|---|---|---|---|---|---|---|---|---|
| [`phase-00-bootstrap-report.md`](reports/phase-00-bootstrap-report.md) | 保存 Phase 0 bootstrap 历史验收 | 当时 scaffold、检查、限制和 Git 事实 | Archived | Project Owner | 不原地更新；实质纠正使用新报告 | Charter; Roadmap; `README.md` | — | — |
| [`phase-00-clean-review-bundle-report.md`](reports/phase-00-clean-review-bundle-report.md) | 保存最初 Bundle round 证据 | 原 Bundle round 在当时的结果与限制 | Superseded | Review Bundle Maintainer | 不原地更新 | Review Bundle Policy; remediation reports | — | `phase-00-review-bundle-remediation-report.md` |
| [`phase-00-review-bundle-remediation-report.md`](reports/phase-00-review-bundle-remediation-report.md) | 保存第一轮 Bundle remediation 证据 | 第一轮 remediation 的当时状态 | Superseded | Review Bundle Maintainer | 不原地更新 | preceding/following Bundle reports | clean-review-bundle report | `phase-00-review-bundle-final-remediation-report.md` |
| [`phase-00-review-bundle-final-remediation-report.md`](reports/phase-00-review-bundle-final-remediation-report.md) | 保存 evidence-derived status remediation 证据 | 该 remediation commit 的当时状态 | Superseded | Review Bundle Maintainer | 不原地更新 | preceding/following Bundle reports | remediation report | `phase-00-review-bundle-implementation-fix-report.md` |
| [`phase-00-review-bundle-implementation-fix-report.md`](reports/phase-00-review-bundle-implementation-fix-report.md) | 保存 Phase 0 Bundle implementation fix 历史 | 最后一轮 Phase 0 Bundle 修复的历史事实 | Archived | Review Bundle Maintainer | 不原地更新；新缺陷使用新报告 | Review Bundle Policy; prior remediation reports | final-remediation report | — |
| [`phase-01-lean-dependency-audit.md`](reports/phase-01-lean-dependency-audit.md) | 记录锁定 LEAN 的 dependency audit | 当前 pinned commit 的 upstream 风险与后续准入阻断 | Active | Security Owner | LEAN commit、dependency graph、upstream advisory 或准入变化 | ADR 0002; Round 1B report; Security | — | — |
| [`phase-01-lean-result-encoding-audit.md`](reports/phase-01-lean-result-encoding-audit.md) | 记录 LEAN result encoding audit | malformed summary 的当前信任边界 | Active | LEAN Integration Owner | LEAN commit、host encoding、result writer 或 normalizer 变化 | ADR 0002; Round 1B report; Testing Strategy | — | — |
| [`phase-01-round-01b-native-lean-foundation-report.md`](reports/phase-01-round-01b-native-lean-foundation-report.md) | 保存已冻结 Round 1B 报告 | Round 1B 范围、结果、限制和 Bundle 要求的历史证据 | Archived | LEAN Integration Owner | 不原地更新；Native 基线变化使用后续正式报告 | ADR 0002; two audits; LEAN README; Review Bundle Policy; Round 1C report | — | — |

全部 tracked 文档状态计数为：`Active 33`、`Draft 1`、`Superseded 4`、`Archived 3`，合计 41。

## 10. 本 Round 文档登记

本节六份合同/治理文档与正式报告均为 `Active`。文档状态生效不等于 runtime 已实现，也不等于 Round 1C 已通过最终外部复核或冻结。

| document | purpose | authoritative_for | status | owner_role | update_trigger | related_documents | supersedes | superseded_by |
|---|---|---|---|---|---|---|---|---|
| [`docs/DOCUMENTATION_MAP.md`](DOCUMENTATION_MAP.md) | 登记文档、冲突规则和统一术语 | 文档治理元数据与跨文档术语路由 | Active | Governance Owner | 文档、status、authority、owner、关系或术语变化 | all registered documents | `docs/GLOSSARY.md` | — |
| [`docs/contracts/DATA_CONTRACTS.md`](contracts/DATA_CONTRACTS.md) | 定义数据接口合同 | schema、时间、质量、版本与 manifest 语义 | Active | Data Governance Owner | schema、时间语义、质量枚举、source 或 manifest 绑定变化 | Data Governance; data README; Backtest Standard | — | — |
| [`docs/contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md`](contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md) | 定义 Strategy→Portfolio→Risk→Execution 边界 | interface 字段、risk decision 枚举、状态机和 fail-closed 行为 | Active | Architecture Owner | interface、risk decision、聚合或状态机变化；风险变化须 Independent Risk Owner 批准 | Architecture; Risk Policy; Lifecycle | — | — |
| [`docs/research/BACKTEST_VALIDATION_STANDARD.md`](research/BACKTEST_VALIDATION_STANDARD.md) | 定义研究验证标准 | hypothesis、OOS、成本、偏差、复现和 Review Bundle 证据 | Active | Research Governance Owner | 验证方法、成本模型、证据、量化门槛获批或 gate 变化 | Charter; Data Contracts; Testing Strategy; Lifecycle | — | — |
| [`docs/research/STRATEGY_LIFECYCLE.md`](research/STRATEGY_LIFECYCLE.md) | 定义策略生命周期 | 状态、转移、证据、approval role 和禁止活动 | Active | Research Governance Owner | lifecycle 状态、gate、approval role、risk budget 或版本规则变化 | Risk Policy; Backtest Standard; interface contracts | — | — |
| [`docs/operations/LOCAL_RUNBOOK.md`](operations/LOCAL_RUNBOOK.md) | 定义 Native 验证步骤 | 本地工具、fixture、build、Run C、Golden 和 Bundle 流程 | Active | LEAN Integration Owner | script、工具链、pinned commit、环境变量、fixture、Golden 或 Bundle 流程变化 | ADR 0002; LEAN README; Round 1B report; Review Bundle Policy | — | — |
| [`docs/reports/phase-01-round-01c-documentation-interface-contract-baseline-report.md`](reports/phase-01-round-01c-documentation-interface-contract-baseline-report.md) | 记录 Round 1C 当前正式候选 | Round 1C scope、evidence、limitations、commit 和下一 gate | Active | Governance Owner | 最终 evidence、commit、Bundle 或外部复核事实变化；冻结后不原地改写 | six Round 1C documents; Review Bundle Policy | — | — |
| [`phase-02-round-01-governed-bar-contract-kernel-report.md`](reports/phase-02-round-01-governed-bar-contract-kernel-report.md) | 记录 Phase 2 Round 01 Corrective Synthetic Bar contract runtime kernel 候选 | 本 Round 的 fail-closed quality、policy identity、manifest verification、quarantine evidence、synthetic-only scope、evidence 和 limitations | Active | Data Governance Owner | Round evidence、candidate commit、Review Bundle 或外部复核事实变化；冻结后不原地改写 | `DATA_CONTRACTS.md`; Review Bundle Policy | — | — |

本节八份文档状态计数为 `Active 8`。Round 1C 与 Phase 2 Round 01 Corrective 正式报告均明确保留“等待最终外部复核、Round 尚未冻结”的 gate。

## 11. Deferred 文档登记

下列项目只有 `planning_state: Deferred`，没有文档生命周期 `status`。文件尚不存在，因此名称不得写成链接。

| planned_document | planning_state | purpose | authoritative_for | owner_role | creation_trigger | related_documents | supersedes | superseded_by |
|---|---|---|---|---|---|---|---|---|
| `ORDER_BROKER_CONTRACTS.md` | Deferred | 未来定义 Order/OMS/Broker 合同 | order schema、状态、幂等、broker mapping 与 reconciliation | Execution/OMS Owner | Paper Trading 开发前获得独立 Phase 批准 | Strategy/Portfolio/Risk contracts; Risk Policy; Lifecycle; Architecture | — | — |
| `INCIDENT_RESPONSE.md` | Deferred | 未来定义事件响应 | 事件分类、升级、隔离、恢复和沟通 | Security Owner | 连续 Shadow/Paper 前或发生需制度化处理的事件 | Security; Risk Policy | — | — |
| `OBSERVABILITY_AND_SLO.md` | Deferred | 未来定义可观测性 | metrics、logs、alerts、SLO 和 error budget | Operations Owner | 连续 Shadow/Paper 运行前 | Architecture; Testing Strategy | — | — |
| `BACKUP_AND_DISASTER_RECOVERY.md` | Deferred | 未来定义备份和灾难恢复 | backup、restore、RPO/RTO 和恢复演练 | Operations Owner | 引入持久状态或 Paper 前 | Data Governance; Deployment Runbook | — | — |
| `CONFIGURATION_REFERENCE.md` | Deferred | 未来定义完整配置参考 | 配置字段、默认值、环境隔离和验证规则 | Configuration Owner | 配置 schema 稳定或 Paper 前 | Security; Risk Policy; Local Runbook | — | — |
| `DEPLOYMENT_RUNBOOK.md` | Deferred | 未来定义部署流程 | deployment、verification、rollback 和环境晋级 | Platform Owner | 部署目标确定且 Shadow/Paper 获准前 | Local Runbook; Configuration Reference | — | — |
| `PRODUCT_REQUIREMENTS.md` | Deferred | 未来定义产品需求 | 用户、业务和系统需求基线 | Product Owner | 产品范围或用户工作流获批 | Charter; Roadmap | — | — |
| `USER_ROLES_AND_PERMISSIONS.md` | Deferred | 未来定义用户权限 | RBAC、职责分离和 approval 权限 | Security Owner | 多用户控制面或共享环境开发前 | Security; Risk Policy | — | — |
| `PUBLIC_API_SPEC.md` | Deferred | 未来定义 public API | API schema、authentication、versioning 和 errors | API Owner | public API 获明确批准 | Architecture; Security | — | — |
| `COMPLIANCE_AND_LEGAL_BOUNDARIES.md` | Deferred | 未来定义合规法律边界 | 监管、交易、数据使用和法律限制 | Compliance Owner | 使用真实数据、面向外部用户或真实资本前 | Charter; Security; Risk Policy | — | — |
| `DATA_AND_LICENSE_REGISTER.md` | Deferred | 未来登记实际数据与许可 | source、license、entitlement、owner 和 allowed use | Data Governance Owner | 获取或使用首个非合成数据源前 | Data Governance; Data Contracts | — | — |
| `SUPPORT_AND_SLA.md` | Deferred | 未来定义支持承诺 | support scope、response level 和 SLA | Operations Owner | 面向外部用户或形成服务承诺前 | Product Requirements; Observability/SLO | — | — |
| `COST_AND_CAPACITY_MODEL.md` | Deferred | 未来定义成本与容量模型 | 数据、基础设施、交易成本和 capacity 方法 | Research Governance Owner | credible backtest、扩容或资本准入前 | Backtest Standard; Risk Policy | — | — |

## 12. 跨文档统一术语

本节给出当前生效的跨文档短定义和详细权威路由；详细字段与行为仍以所链接的领域文档为准。

| 术语 | 统一定义 | 详细权威来源 |
|---|---|---|
| `signal` | 版本化策略基于已治理数据产生的方向或目标建议；不是 order，也不具有风险批准。 | [Strategy/Portfolio/Risk Contracts](contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md) |
| `target` | Portfolio 聚合后交给 Risk 的目标对象；它表达期望状态，不构成执行许可。 | [Strategy/Portfolio/Risk Contracts](contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md) |
| `target weight` | 目标资产占 portfolio 净值的比例；JSON 字段使用 `target_weight`。风险批准后的权重必须与原始目标区分。 | [Strategy/Portfolio/Risk Contracts](contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md) |
| `portfolio` | positions、cash、targets 和 aggregation rule 组成的组合状态；Strategy 不直接拥有执行权限。 | [Architecture](ARCHITECTURE.md)；[Strategy/Portfolio/Risk Contracts](contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md) |
| `risk decision` | Independent Risk 对一个 target 的版本化决定；仅允许 `APPROVED`、`REJECTED`、`PARTIALLY_APPROVED`。 | [Risk Policy](RISK_POLICY.md)；[Strategy/Portfolio/Risk Contracts](contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md) |
| `execution intent` | 仅由风险批准后目标产生的候选执行意图；它不是 broker order，也不证明已提交。 | [Strategy/Portfolio/Risk Contracts](contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md) |
| `order` | 未来 OMS/adapter 管理的有状态执行对象。当前没有获批的真实 order path。 | Deferred `ORDER_BROKER_CONTRACTS.md` |
| `event_time` | 源事件实际发生或生效的时间。 | [Data Contracts](contracts/DATA_CONTRACTS.md) |
| `availability_time` | 该事件最早可被系统合法观察和用于决策的时间。 | [Data Contracts](contracts/DATA_CONTRACTS.md) |
| `point-in-time` | 在 `as_of_time` 只使用 `availability_time` 不晚于该时点的信息，并保留当时可知的 universe、symbol 和 corporate action 状态。 | [Data Governance](DATA_GOVERNANCE.md)；[Data Contracts](contracts/DATA_CONTRACTS.md) |
| `Golden` | 与固定 fixture、commit、configuration 和 manifest 绑定的确定性预期输出；不是盈利 benchmark。 | LEAN expected artifacts；[Local Runbook](operations/LOCAL_RUNBOOK.md) |
| `manifest` | 版本化 provenance、identity、checksum、配置和输入记录。 | Data Governance；具体 fixture 以 fixture manifest 为准 |
| `data version` | 不可变数据状态或 manifest 的标识；schema 字段使用 `data_version`。 | [Data Contracts](contracts/DATA_CONTRACTS.md) |
| `strategy version` | 策略逻辑版本标识，必须与 code commit 绑定，并与 parameter version 分开。schema 字段使用 `strategy_version`。 | [Strategy/Portfolio/Risk Contracts](contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md) 与 [Lifecycle](research/STRATEGY_LIFECYCLE.md) |
| `Raw` | 从已登记 source 获得且保持不可变的原始层。 | [Data Governance](DATA_GOVERNANCE.md)；[Data Contracts](contracts/DATA_CONTRACTS.md) |
| `Clean` | 从 Raw 可重建、经过规范化和质量标记的数据层。 | [Data Governance](DATA_GOVERNANCE.md)；[Data Contracts](contracts/DATA_CONTRACTS.md) |
| `Adjusted` | 按记录的方法处理 split、dividend 等 corporate action 的数据层。 | [Data Governance](DATA_GOVERNANCE.md)；[Data Contracts](contracts/DATA_CONTRACTS.md) |
| `Features` | 从已治理输入和版本化逻辑完全可重现的派生特征。 | [Data Governance](DATA_GOVERNANCE.md)；[Data Contracts](contracts/DATA_CONTRACTS.md) |
| `OMS` | 管理 order lifecycle、状态和审计的未来 Order Management System；当前架构中的该层不表示已实现。 | Architecture；Deferred `ORDER_BROKER_CONTRACTS.md` |
| `kill switch` | 由独立控制拥有、可阻止新增风险或 order submission 的全局控制；Strategy 不得拥有或绕过它。 | Risk Policy |
| `Shadow` | 产生信号、目标和对比证据但不发送 order 的生命周期状态。 | [Lifecycle](research/STRATEGY_LIFECYCLE.md) |
| `Paper Trading` | 使用 broker simulation 且不涉及真实资金的未来状态；当前未获批准并保持关闭。 | [Risk Policy](RISK_POLICY.md)；[Lifecycle](research/STRATEGY_LIFECYCLE.md) |
| `Limited Live` | 未来需要单独批准、独立风险预算和完整 gate 的受限真实资金状态；当前没有批准。 | [Charter](PROJECT_CHARTER.md)；[Risk Policy](RISK_POLICY.md)；[Lifecycle](research/STRATEGY_LIFECYCLE.md) |

`signal_timestamp` 是 signal 产生时间，不得无条件等同于数据的 `event_time`。prose 术语 `target weight`、`data version`、`strategy version` 分别映射到 schema 字段 `target_weight`、`data_version`、`strategy_version`。

## 13. 已知状态差异与解释规则

1. [`README.md`](../README.md) 仍描述 Phase 0 scaffold、未集成 LEAN 和下一步 Phase 1；当前实现事实必须以 ADR 0002、Round 1B 历史报告、Round 1C 正式报告和当前 Prompt 为准。
2. [`TESTING_STRATEGY.md`](TESTING_STRATEGY.md) 与 [`tests/integration/README.md`](../tests/integration/README.md) 的 Phase 0 实现描述不再代表当前仓库；其测试原则仍然有效。
3. ADR 0001 仍为 `Draft`；其 LEAN foundation 部分已被 ADR 0002 的已接受决定取代，Python 和 Alpaca 等其余假设仍未获接受。
4. Round 1B 报告是已冻结、已归档的历史证据；其中“Round 1C 尚未启动”只描述该报告形成时的事实。当前 Round 状态以 Round 1C 正式报告为准。
5. Phase 0 报告中的 Python、Docker、network 和 host 限制只描述当时环境，不得当作当前环境事实。
6. Roadmap 中的 Alpaca 内容是未来计划，不构成连接、Paper 或 Live 准入授权。
7. Risk Policy 中的百分比和 capital stage 是 hypothesis / planned path，不是当前已批准的真实风险预算。

## 14. 文档更新与归档流程

1. owner role 确认 `update_trigger` 已发生，并确认单一目标和权威范围。
2. 修改前检查冲突优先级；领域规则只在唯一权威文档中更新，引用方仅更新链接或摘要。
3. 架构决定变化使用新 ADR；历史正式报告保持原貌，纠正通过新报告及双向关系记录。
4. 新文档先设为 `Draft`。完成验证并经所需外部审阅冻结后，方可转为 `Active`。
5. replacement 转为 `Active` 时，旧文档改为 `Superseded`，并同时填写双方的 `supersedes` / `superseded_by`。
6. 不再定义当前规则但仍需作为历史证据的文档改为 `Archived`；其历史内容和 commit 语义保持不变。
7. 每次状态迁移必须更新本地图、检查相关链接和术语路由，并按 Review Bundle Policy 判断是否需要新 Bundle。

本轮已将本地图激活，并在本地图登记 [`GLOSSARY.md`](GLOSSARY.md) 为 `Superseded`。跨文档短定义与详细权威路由由本节负责；具体字段、枚举和行为仍由所链接的 Active 领域文档负责。
## Phase 2 Round 01 Corrective 2 Documentation Update

The registered Phase 2 Round 01 report remains the authoritative report for the governed synthetic Bar kernel. Its current addendum records Corrective 2, based on Candidate `a6946603c3aa9d5cfef1a1e467cc037e21078639`, with Review Bundle `REQUIRED`, pending independent review, and no freeze. Earlier Candidate failure history remains preserved in that report.
