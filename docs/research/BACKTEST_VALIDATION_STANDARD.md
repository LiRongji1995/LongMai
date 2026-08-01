# 回测验证标准

## 文档元数据

| 项目 | 值 |
|---|---|
| 目的 | 规定可证伪、无前视、计入现实成本且可复现的策略回测与候选准入证据。 |
| 适用范围 | 研究假设、Prototype 之后的回测、walk-forward、样本外评估、Shadow/Paper 准入证据；不授权任何交易。 |
| authoritative_for | 回测实验设计、证据字段、偏差控制、淘汰条件与研究侧准入标准。 |
| 权威来源 | [Project Charter](../PROJECT_CHARTER.md)、[Data Governance](../DATA_GOVERNANCE.md)、[Testing Strategy](../TESTING_STRATEGY.md)、[Risk Policy](../RISK_POLICY.md) 与 [Review Bundle Policy](../REVIEW_BUNDLE_POLICY.md)。 |
| status | Active |
| 当前实现状态 | `CONTRACT_DECISION`。仓库只有冻结的 Native C# 基础设施 smoke 证据；当前没有真实策略、可信策略回测或已批准准入记录。 |
| owner role | `Research Governance Owner`；数据边界由 `Data Governance Owner`、风险边界由 `Independent Risk Owner`、准入证据由 `Independent Reviewer` 复核。 |
| 更新触发条件 | 验证方法、数据合同、成本模型、生命周期 gate、量化门槛或 Review Bundle 政策经批准变化。 |
| 失败行为 | 任一必需证据缺失、泄漏、版本未绑定或结果不可复现时 fail closed：淘汰或退回候选，不得提升生命周期状态。 |
| 测试或验证要求 | schema/manifest 校验、时间泄漏测试、成本与偏差检查、确定性复现、样本外隔离、相对链接与 Review Bundle 独立验证。 |

## 1. 权威边界与状态标签

本文件只定义研究验证合同；策略状态与批准以 [Strategy Lifecycle](STRATEGY_LIFECYCLE.md) 为准，数据时间和版本语义以 [Data Contracts](../contracts/DATA_CONTRACTS.md) 为准，Strategy/Portfolio/Risk 边界以 [Interface Contracts](../contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md) 为准。风险限制或资本授权不得由本文件设定。

- `IMPLEMENTED`：存在于当前 Git HEAD 且由测试或冻结报告证明。
- `CONTRACT_DECISION`：本轮确定的必须遵守的未来接口或流程，不表示代码已实现。
- `TBD`：尚未实现或批准；须在文末登记原因、owner role 与补充触发条件。

已实现事实仅包括：冻结 Round 1B 使用锁定 LEAN commit、SYNTH fixture、manifest 与 Golden 完成 Native C# 确定性 smoke。该算法采用零 commission、零 slippage 和立即 fill，只证明基础设施，不是盈利策略、`Backtest Candidate` 或策略有效性证据。参见 [Round 1B report](../reports/phase-01-round-01b-native-lean-foundation-report.md)。

## 2. hypothesis 与可证伪条件

每个实验必须在查看 validation/test 结果前登记：

1. `hypothesis_id`、经济或行为机制、目标 universe、预期持有期与决策频率；
2. 明确的输入、signal、target 与禁止使用的信息；
3. 主指标、次指标、benchmark、方向性预期和成本假设；
4. 可证伪条件，包括何种结果会拒绝假设；
5. train/validation/test 划分、walk-forward 方案、parameter search 空间与停止规则；
6. 已知混杂因素、数据限制、预期失败模式和独立审阅计划。

不得在观察结果后把探索性发现伪装成预注册假设。事后发现必须获得新的 `hypothesis_id` 与实验版本。

## 3. train / validation / test 与样本外

### 3.1 时间隔离

- 划分必须按 `availability_time` 的时间顺序进行，禁止随机打乱造成未来信息进入过去。
- train 只用于拟合；validation 用于有限的模型/参数选择；test 在方案锁定后只运行一次。
- 标准化、特征选择、universe 选择和超参数选择只能在当时可用的 train 数据上拟合。
- validation 或 test 被用于调参即视为污染；必须记录污染并配置新的、未接触的样本外区间。
- 重叠标签、持有期和滚动特征必须设置 purge/embargo；具体长度取决于假设并进入实验 manifest。

### 3.2 walk-forward

walk-forward 必须预先记录 window 类型、train/validation/test 长度、滚动步长、是否 expanding、重新拟合频率和边界日期。每个 fold 独立保存参数、数据版本、成本模型与结果；最终结果不得只展示最优 fold。跨 fold 聚合必须同时报告离散度、最差区间和失败 fold。

### 3.3 样本外要求

样本外结果必须来自未参与假设形成或参数选择的数据，覆盖与目标持有期相称的独立交易机会，并保留完整失败记录。具体最小期限、交易次数与 regime 覆盖尚未批准，见 `BT-TBD-001`。

## 4. benchmark 与指标报告

benchmark 必须与假设、universe、币种、交易日历和收益定义相匹配；保存 benchmark 标识、来源、版本、`availability_time` 规则和 total-return/price-return 口径。若没有适当 benchmark，实验不得宣称超额收益。

任何 Sharpe、收益率、最大回撤、Sortino、波动率、turnover、fill rate、capacity 或超额收益都必须同时标注：

- 完整 interval 与样本角色；
- gross/net 口径及 commissions、spread、slippage、market impact；
- benchmark 与版本；
- annualization、缺失值和无风险利率处理；
- `strategy version`、`parameter_version`、`data version` 与 `cost model version`。

本标准不编造任何数值门槛。未批准门槛均为 TBD，不得以行业惯例代替批准。

## 5. 成本、流动性与 capacity

每个策略有效性回测必须显式建模并版本化：

| 项目 | 最低要求 | 缺失时行为 |
|---|---|---|
| commissions | 按市场、资产、订单/成交口径说明费率、最低费用和生效期 | 不得形成净收益结论 |
| spread | 使用 point-in-time bid/ask 或经批准的保守代理；记录 session 与 resolution | 标为基础设施实验并淘汰策略结论 |
| slippage | 与参与率、波动、size、订单风格和 session 相关 | fail closed |
| market impact | 记录模型、参数来源、校准区间与版本 | 不得声称 capacity |
| liquidity | point-in-time volume/quote、halt、价格与可交易性过滤 | 相关交易不得视为可执行 |
| capacity | 资金规模、ADV/参与率、turnover 和冲击敏感性曲线 | 不得进入 Shadow/Paper 评审 |

零成本、零 spread 或立即 fill 只允许明确标记为 infrastructure smoke；不得用来证明策略有效性或盈利能力。

## 6. 数据与偏差控制

### 6.1 point-in-time

决策时只能使用 `availability_time <= decision/as_of time` 的记录及当时有效的 universe、symbol、Corporate Action 和数据版本。`event_time` 不能替代 `availability_time`。每次回测绑定 data manifest；缺少该绑定即淘汰。

### 6.2 必查偏差

- **survivorship bias**：universe 必须包含当时存在、后来退市/更名的证券；当前能力 TBD。
- **look-ahead bias**：测试价格、特征、fundamental 发布时点、Corporate Action 公告/生效时点和日历边界。
- **universe selection bias**：保存每个 as-of 时点的成员资格、选择规则、版本和排除原因。
- **Corporate Action**：split、dividend、symbol rename 的 source、announcement/effective/availability 语义与调整版本可追溯。
- **数据修订**：使用当时可用版本，或明确标记实验无法支持策略结论。
- **缺失/duplicate/late/stale/invalid**：按 Data Contracts fail closed；不得静默填补或删除不利样本。

当前不存在完整 point-in-time 数据库、Corporate Action pipeline 或历史 universe，故任何依赖这些能力的策略有效性结论均不得产生。

## 7. parameter search 与多重检验

parameter search 日志必须包含全部候选、搜索算法、参数空间、顺序、random seed、失败运行、提前停止和观察过的所有 validation/test 结果。不得只保留最终参数。

多重检验方法必须在实验前选择，并记录同时检验的假设族、校正方法、有效试验数和选择偏差。具体方法与阈值由 `Research Governance Owner` 批准，见 `BT-TBD-002`。若无法量化搜索自由度，结果保持探索性，不得晋级。

## 8. 稳定性、sensitivity、stress test 与市场状态

候选必须至少检查：

- 参数在邻域和简化版本中的 sensitivity；
- 不同起止日期、rebalancing 偏移和数据合理扰动；
- commissions、spread、slippage 与 impact 的恶化；
- volume 收缩、缺失数据、延迟、halt 和成交拒绝；
- bull、bear、sideways、高/低波动和流动性压力等市场状态；
- 单个证券、sector、时期或少量交易对结果的集中贡献；
- turnover、capacity 与持有期变化。

regime 的判定方法、数据和阈值必须在查看策略结果前版本化。未解释的符号翻转、局部尖峰或对单一时期依赖构成淘汰或退回原因。

## 9. reproducibility 与实验 manifest

每次回测至少绑定以下不可变身份：

| 字段 | 要求 |
|---|---|
| `experiment_id`, `hypothesis_id` | 唯一且不可重用 |
| `Git commit` | 40-character commit，代码必须可从 Git 对象重建 |
| configuration | 安全、去 Secret 的配置 SHA 与 schema version |
| interval | 明确开始/结束、timezone、resolution 与 session |
| benchmark | 标识、版本、来源与收益口径 |
| `data version`, data manifest | 不可变版本与 manifest SHA |
| `strategy version`, `parameter_version` | 分离版本并绑定 code commit |
| risk | `policy_version` 与本回测模拟的限制 |
| `cost model version` | commissions、spread、slippage、impact 的完整绑定 |
| `random seed` | 有随机性时必填；确定性实验为 `null` 并写明原因 |
| engine/toolchain | LEAN commit、.NET/Python 版本和依赖 lock |
| outputs | 原始/规范化结果 SHA、日志摘要和最终持仓 |

给定同一身份输入，规范化语义结果必须可重复。Golden 只验证已指定的机械语义与确定性，不是期望收益曲线，也不能替代样本外验证。

## 10. 淘汰与停止条件

任一条件成立即淘汰或退回，不等待漂亮指标抵消：

- look-ahead、point-in-time、survivorship 或 universe selection bias 未排除；
- 数据、代码、配置、成本、策略、参数或 manifest 版本未绑定；
- test 被污染且无新的独立样本；
- 结果不可从 Git commit 与受治理输入复现；
- 缺成本/流动性模型却形成策略有效性或 capacity 结论；
- 参数局部脆弱、市场状态不稳定或收益由极少观察主导且未解释；
- 风险规则被绕过、失败未 fail closed 或最终持仓不可信；
- 必需测试、独立审阅或 Review Bundle 未通过；
- 证据含 Secret、具体本机路径或不受许可的数据。

## 11. Shadow 与 Paper 入口证据

### 11.1 Shadow

进入 Shadow 前至少需要：Validated Candidate 状态；通过本标准的样本外/稳定性/成本/偏差证据；绑定的 code/data/strategy/parameter/risk/cost 版本；独立审阅；必需测试；正式 Review Bundle。Shadow 仅产生隔离的 hypothetical signal、target、risk decision 或 execution intent，不发送 order。

### 11.2 Paper

回测通过不等于允许进入 Paper Trading。Paper 入口还要求 Shadow 运行证据、状态和故障测试、重启/恢复、reconciliation、风险拒绝与 kill switch 测试、安全隔离以及 `Human Approval Authority` 的明确批准。具体观察期与数值门槛为 TBD。Paper 不涉及真实资金，且当前保持 `PAPER_TRADING_ENABLED=false`。

Limited Live 或 Production 不属于回测自动准入结果；真实风险预算当前为零且未批准。

## 12. 独立审阅与 Review Bundle

策略作者可以准备证据，但不能独立批准自己的候选。`Independent Reviewer` 必须复核预注册、数据时间、成本、搜索自由度、样本外隔离、复现、淘汰条件和 residual risks。

任何对外提交、正式 gate 或 `PASS` / `PASS WITH LIMITATIONS` 结论均依 [Review Bundle Policy](../REVIEW_BUNDLE_POLICY.md) 生成 Git-object-derived Review Bundle。Bundle 必须记录实际状态，不得把基础设施 smoke 或旧 Round 证据冒充策略验证证据。

## 13. 测试与验证要求

最低自动化测试包括：时间边界/property tests、manifest/SHA 绑定、look-ahead 注入、duplicate/missing/out-of-order、split/dividend/symbol rename、universe as-of、成本恶化、参数日志完整性、random seed、重复运行语义比较、final holdings、risk bypass 与错误状态 fail closed。人工复核必须覆盖假设可证伪性、benchmark 适当性、数据许可、异常结果和独立批准。

## 14. TBD register

| tbd_id | 未实现或未确认内容 | 原因 | owner role | 触发补充条件 | 未解决时的 fail-closed 行为 |
|---|---|---|---|---|---|
| BT-TBD-001 | 最小 OOS 期限、交易数、regime 覆盖及 Sharpe/收益/回撤/稳定性门槛 | 尚无真实策略、受治理数据或经审阅分布 | `Research Governance Owner` | 首个策略在锁定数据和成本模型上申请 `Validated Candidate` 前，经 `Independent Reviewer` 批准 | 仅保留探索性，不得晋级 |
| BT-TBD-002 | 多重检验方法、有效试验数和显著性/错误发现阈值 | 搜索族和统计假设尚未定义 | `Research Governance Owner` | 首个 parameter search 方案预注册时 | 不得将搜索结果作为确认性证据 |
| BT-TBD-003 | production-like commissions、spread、slippage、market impact、liquidity 与 capacity 模型及参数 | 尚无批准的数据源、执行 venue 或校准样本 | `Research Owner`，由 `Independent Risk Owner` 复核 | Phase 3 credible backtest 前 | 不得形成净收益、可执行性或 capacity 结论 |
| BT-TBD-004 | point-in-time universe、delisting、Corporate Action 与修订历史实现 | 当前仅有 SYNTH smoke fixture | `Data Governance Owner` | 首个非合成数据源接入且 Data Contracts 测试完成时 | 依赖这些数据的策略结论全部拒绝 |
| BT-TBD-005 | 市场状态定义、分类数据与 stress severity | 尚无批准 regime taxonomy | `Research Governance Owner` | 首个候选预注册 regime 分析前 | 不得声称跨市场状态稳定 |
| BT-TBD-006 | Shadow 最短观察期、覆盖率、漂移和 operational gate | 连续 Shadow 尚未实现 | `Operations Owner` 与 `Independent Risk Owner` | Shadow 基础设施可用且首个候选申请进入时 | 不得进入 Paper |
| BT-TBD-007 | Paper 入口数值门槛与批准表单 | Paper 边界、reconciliation 和故障演练尚未实现 | `Human Approval Authority` | Phase 6 开始且 Paper 设计接受独立审阅时 | Paper 保持关闭 |
| BT-TBD-008 | 独立审阅者指派、利益冲突声明和审批记录 schema | 当前只有角色合同，无组织指派 | `Governance Owner` | 首个真实候选请求正式 gate 时 | 不得签发正式候选批准 |
