# 研究、执行证据与主动入口 V1/V2

本页说明新工程接口。工程测试不等于真实业务验收或投资有效性。所有新结果继续保留 `MARKET_EDGE=NOT_ESTABLISHED`；没有接入券商、没有下单，也不自动采集、激活运行环境或重试失败请求。

## 统一入口与明确的运行树

统一薄入口为 `scripts/codex_active_v1.py`。盘前与后验定位同一登记主动树；只读交付审计在同一已核主动树内执行，研究/账本等独立控制工具调用各自原模块。它不复制策略、估计器、成交模拟或恢复算法。

| 子命令 | 原实现 | 行为边界 |
| --- | --- | --- |
| `research-v2` | `codex_research_protocol_v2.py` | 标签隔离和条件统计；冻结、研究会追加指定私有账本 |
| `trial-ledger` | `research_trial_ledger_v1.py` | 显式创建、检查和追加本地试验账本 |
| `execution-evidence` | `execution_evidence_v1.py` | 本地成交事实对账；真实来源权威不足时正式曲线阻塞 |
| `restricted-history` | `restricted_history_v1.py` | 有界、只读历史检查；不恢复来源权威 |
| `metrics` | `durable_stage_metrics_v1.py` | 只读审计阶段日志 |
| `outcomes` | `active_outcome_pipeline_v1.py` | 共同请求/累计预算下重验原始档案，用原评估器生成五期限；失败不重算 |
| `delivery-audit` | `active_read_control_v1.py` → `daily_delivery_contract_v1.py` | 在登记主动树内只读重认单日原生交付，不发业务能力 |
| `recovery` | `codex_runtime_recovery_v2.py` | 原恢复 plan/read/apply；apply 仍需原显式激活参数 |
| `build` / `verify` | `active_runtime_v1.py` | 构建新树或核验完整树；构建不部署 |
| `delivery-protocol` | `daily_delivery_contract_v1.py` / `active_read_control_v1.py` | `plan`、显式 `freeze`；`inspect-sequence`固定只读主动树 |

盘前必须给出已构建主动树、原登记根与登记名称：

```text
python -I -B scripts/codex_active_v1.py premarket-plan \
  --source-root <已构建主动树绝对路径> \
  --runtime <原V2登记名> --registry-root <原登记根绝对路径>

python -I -B scripts/codex_active_v1.py premarket \
  --source-root <同一主动树绝对路径> \
  --runtime <同一登记名> --registry-root <同一登记根绝对路径> \
  --observation-date <观察日> --target-session <目标日> \
  --request-key <原固定请求标识>
```

入口先核验主动树与登记绑定，再用登记中的 Python `-I -B` 调用该树的 `codex_daily_v2.py`。默认 `engine=original`；`shared` 仍只适用于显式性能实验。缺树、未构建、登记指向另一树、未证明主动树身份或运行中源码变化都会拒绝。没有回退到开发树、冻结树或另一登记的分支；子进程非零结果原样返回，不更换 key 或 engine 重试。

发布源码中的 driver 是显式控制平面：研究、账本、构建、恢复计划、证据审计和协议文件操作从这里调用。生成主动树中的 driver 也能定位盘前，但必须指定它自身的同一树，并复用既有 gateway 的窄范围、已绑定 authority 读取。该读取不会给 worker 增加状态写豁免；实际业务 claim、阶段权限、费用预算、子进程引导和退出清理由原主动执行链承担。不会为了让离线控制工具在 active worker 内运行而扩大 worker 权限。

## 逐日后验与只读交付

后验使用原信号档案、后验来源档案及各自的 PREMARKET 成功业务。`outcomes`必填 `--source-root --runtime --registry-root --parent-business-id --signal-job --signal-final-sha256 --outcome-job --outcome-final-sha256 --destination --request-key`；不同档案另需 `--outcome-business-id`。只消费已存在原件，不采集。

需要跨日成熟标签时，显式 `--daily-archives <JSON> --daily-archives-sha256 <SHA>`，文件为 `{ "archives": [{ "business_id": "...", "job": "...", "final_sha256": "..." }] }`。每份档案必须有原成功业务并晚于信号；主后验档案是唯一末日。组合只取各档案当日证券状态和价格，保持原时钟及收据；晚日下载的历史UNKNOWN不升级为当时资格。缺日仍UNKNOWN，新manifest和派生输入另留原件链，原evaluator不变。

同一run的OUTCOME家族冻结累计预算：新日原始输入才可能成为新修订；旧失败、未知、运行中、重新组合旧输入或额度变化均阻断。单日合成与旧单档案使用同一业务身份，不能靠切换模式重试；成功复用原产品并标明本次未重认来源。`outcome_family_budget(run_id)`可只读检查家族，原单业务`remaining_limits`不能当作总余额。

`delivery-audit`及`delivery-protocol inspect-sequence`都必填同一`--source-root --runtime --registry-root`。审计子进程仅原Authority的只读连接，不给写入、采集、服务、子进程能力；父进程只能发布新的私有审计报告。缺MATURE来源权威时，只展示描述性成熟计数，不计真实验收。完整示例与迁移/环境验收见[实施记录](REMEDIATION_20261105.md)。

## 标签隔离与跨计划试验账本

使用 `scripts/contracts/research_protocol_v2.template.json`。V1 公式、成对比较和区块估计器保持不变；V2 增加下列约束：

- 每个观察日和期限都保留真实交易日轴上的 `label_start`、`label_end`。跨入下一阶段、embargo 或冻结 holdout 结束后的标签列为 `PURGED`；日历外列为 `UNKNOWN`。原信号仍在报告，不删除不利样本。
- `discovery_labels` 必须逐份匹配既有 discovery ID 与原内容 hash，显式声明已消费标签的完整起止日期。不能用新 H1 计划假设旧探索只看过 H1；未知跨度拒绝冻结。这仍是本地完整性声明，不能证明未披露的线下探索不存在。
- 有限日历只使用现有登记版本，不生成工作日代替交易所日历，不外推 2027。新年份必须另取得权威资料并按新版本接入。
- `stopping_policy=ONE_EVALUATION_PER_PHASE_NO_RETRY` 是实际执行限制；旧 `stopping_rule` 文本仍保留。每个 plan 的 validation、holdout 各只能消费一次；holdout 要求 validation 已留下终态结果。

操作顺序如下，所有文件必须使用新的私有路径与原 hash：

1. 显式 `trial-ledger init --root ... --scope-id ... --output ...` 创建选定研究范围；日常查询不会自动建立账本。
2. `research-v2 design-hash --input ... --input-sha256 ... --output ...` 取得全部设计字段及标签政策的 hash。
3. 在同一账本追加 `attempt`，使用该设计 hash 和检查得到的 `--expected-head`。FAILED、UNKNOWN、已完成尝试都保留。
4. 草案 `attempts` 包含账本全部尝试，再调用 `research-v2 freeze --ledger-root ... --expected-head ... --input ... --input-sha256 ... --output ...`。每个新 plan 都需要尚未被使用、绑定全部设计字段的新 PLANNED attempt。
5. `research-v2 study` 必须给出 `--ledger-root`、`--expected-head`、`--plan/--plan-sha256`、`--input/--input-sha256`、`--phase` 和新 `--output`。batch 结构为 `{ "plan_hash": "...", "pairs": [...] }`；pair carrier 格式沿用 V1 原文件与 SHA。

`study` 在读取后验 carrier 和计算之前，先追加绑定 plan、phase、输入 hash 的不可回退 CLAIM。计算失败、未知或输出失败不能通过换输出路径重新窥看。完成报告也追加到账本；已提交但报告发布失败时，用 `recover-study --ledger-root ... --plan-id ... --phase ... --output ...` 只读恢复同一结果，不重新估计。仅有 CLAIM、没有已完成结果时，恢复仍返回 UNKNOWN/拒绝。冻结后的计划可用 `recover` 同样只读恢复。

账本使用原子新文件、文件锁、连续事件序号和 hash 链，拒绝过期 head。报告列明冻结时和本次的试验数量；多重性包含所有登记尝试及同一计划额外阶段窥看，包括未知尝试。Holm 之后的本地试验 Bonferroni 是保守诊断。`offline_or_other_scope_attempts=UNKNOWN`，另建目录不能证明全局探索已经登记。结果不因此成为独立样本或已验证市场优势。恢复报告保留原计算时的账本 head 与数量，不把旧估计冒充按新试验族重算。

## 执行事实对账和资本边界

使用 `execution_evidence_v1.template.json` 与 `execution_evidence_records_v1.template.json`。接口消费精确 hash 绑定的本地原始文件；分别要求带实际取得时间的日历、身份、规则、费用、公司行动覆盖、订单、实际成交、订单终态和估值。额外的 JSON `verified=true`、自称来源权限或任意 owner 对象不会授予真实来源权威。

当前工程可以核对部分成交、未成交/撤销、费用、现金、T+1、持仓、延迟退出和逐日条件 NAV。BUY 成交只允许冻结的目标交易日，不把跨日补买偷偷算作次日买入。最低佣金按同一订单同一会话累计，不对每笔部分成交重复收取；实际退出延迟单独报告。未知订单、缺估值、公司行动覆盖缺失等保留为缺口，不补零收益。初始资本是调用者情景，明确标为 `UNVERIFIED_CALLER_INITIAL_CASH_SCENARIO`；现金名为 `conditional_cash_cny`，未解决执行状态会使其成为未知。

尚无可信的原生执行来源生产者及完整规则/费用/公司行动授权适配，因此 `account_curve`、`strategy_account_return` 仍为 null，正式状态为 `BLOCKED_PROVIDER_AUTHORITY`。独立小例对账通过只证明条件算术和安全断言；不证明真实成本、可成交量、容量或投资回报。CLI 即使条件算术完成也返回 4，禁止将它解释为已获正式执行验证。

## 连续 20 个真实交易日协议

```text
python -I -B scripts/codex_active_v1.py delivery-protocol plan \
  --first-target-session <首目标日> --acceptance-scope PROSPECTIVE_OPERATIONAL_DELIVERY \
  --output <新私有协议JSON>

python -I -B scripts/codex_active_v1.py delivery-protocol inspect-sequence \
  --source-root <登记主动树> --runtime <原V2登记名> --registry-root <原固定登记根> \
  --protocol <协议JSON> --protocol-sha256 <原文件SHA> \
  --requests <请求描述JSON> --requests-sha256 <原文件SHA> \
  --authority-database <原权威数据库> --workspace-id <原工作区标识> \
  --output <新私有审计JSON>
```

`plan` 默认从现有有限官方日历产生 20 日协议，不登记 authority、不采集、不证明完成。可同时提供 `--calendar-state-root`、`--calendar-record`、`--calendar-record-sha256`，只读重验已存在原生缓存并将其精确行集合绑定到协议；选择窗口还必须与有限官方轴一致。正式序列审计应再次提供相同原生日历载体，以及原 `--protocol-business-id`；未提供时明确 NOT_ESTABLISHED。请求文件为 `{ "requests": [...] }`，每项沿用原 `audit_delivery` 的 business_id、job、journal_directory、run/outcomes 路径和 SHA 参数，并提供 `outcome_business_id`，值为原 OUTCOME 计算业务 ID；不接受替换内部审计预算。注意：`delivery-audit --outcome-business-id` 同样指原 OUTCOME 计算业务，而前述 `outcomes --outcome-business-id` 指后验来源的 PREMARKET 业务，二者不能互换。

需要权威事前冻结时，另调用 `delivery-protocol freeze`，明确提供 `--authorize-freeze`、`--authorization-id`、`--epoch-id` 及原协议/authority 定位。只有该子命令会调用原 `freeze_protocol`；plan 和 inspect-sequence 永不隐式冻结。CLI 授权标识随新输出保存，实际权威登记和事前时钟仍由原合同检查。

日历必须在首次冻结时就覆盖全部观察日、末日 H10 自然成熟和计划最终复核日期。默认 32 个自然日前瞻可能不足；不能在最终验收时换一个扩窗日历 hash 绕过原冻结。没有足够已授权原生日历时，仍可保存计划，正式验收保持阻塞；不得自行抓取或外推。

V2必须显式事前冻结验收范围。`PROSPECTIVE_OPERATIONAL_DELIVERY`验原定日期实际执行、及时封存、原生来源与成熟后验；不改变原`RECONSTRUCTED_HISTORICAL`覆盖，也不建立历史特征PIT。`STRICT_PROSPECTIVE_PIT`保留严格历史覆盖要求；省略scope保持V1，不自动升级旧协议。

运行完成率与标签成熟分开报告。完整 security × 五期限格子中的 PENDING、UNKNOWN、NOT_PROVIDED 都不能计入总验收成功。末日 H10 要等额外真实交易日；20 日运行成功本身不等于全部后验成熟。没有请求证据的日期保留 NOT_RUN，用户实际收到报告的证据另行处理。原生日历与审计控制读取成本分别列示，不将部分测量外推全市场。

## 退出码和恢复纪律

各薄路由保留原命令退出码。V2 研究/执行及序列审计通常用 0 表示该命令自身完成、4 表示已产生明确阻塞结果、2 表示拒绝或失败；某些原模块有自己的 3/7 等退出码，直接保留。0 不授予投资权威。发生不确定提交时先检查原账本或 authority，恢复只读已保存结果；不自动重跑，不切换树、版本、engine 或 request-key 绕过旧失败。
