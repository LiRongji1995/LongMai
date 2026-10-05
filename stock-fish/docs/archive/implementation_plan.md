> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../CURRENT_STATUS.md)；原路径可在[归档索引](README.md)查找。

# StockRecommendation Phase 1：Daily Observation Monitor 实施计划

日期：2026-09-23。当前任务为只读真实运行观察入口；下方保留此前 Phase 1 实施计划作为历史记录。本计划先于本轮代码修改保存。

## 现有模块与复用点

【事实】`DailyResearchMixin.daily_research_delivery` 已依据原计划、attempt、job 和发布完成时间给出 READY / NOT_READY / FAILED / MISSED / PLANNED / RUNNING / QUEUED，并保留原 run 和身份。`daily_recommendation_snapshot` 可以读取新快照，也兼容旧 run 的只读投影；本观察入口必须另外识别已持久化快照，不能把历史投影说成原冻结 snapshot。

【事实】`daily_recommendation_outcomes` 和 `summarize_daily_research_outcomes` 已有交易日成熟判断、版本绑定、后验 revision 选择及缺失分母。现有 report 每次包含全历史分析，不适合为 20 日概览重复展开全库。`candidate_api.py` 与 `scripts/codex_research.py` 是既有 HTTP / CLI 入口；使用同一服务、数据库和交易日历。

## 新增内容

【建议】新增 `daily_observation.py` 只读消费者和一个 GET `/api/daily-research/observation`，接收 `target_session`、`days`（1..20）、可选带时区 `as_of`。复用原 delivery 判断、已持久化 snapshot 和原 outcome 查询，按目标交易日展示；不修改原服务状态机，不扫描并分析全部历史策略 run。

【建议】在既有 CLI 增加 `daily-observation` 命令，默认显示当前/最近交易日以及最近 20 个交易日；显式目标日可查历史或下一目标日。终端提供 JSON，按需输出简单 Markdown 和原 JSON 到新的私有目录。无复杂 UI、无新数据库、无观察任务调度或外部通知。

【建议】当前日展示身份、是否有运行证据、无 run 原因、blocker/error、冻结四类数量，以及 FOCUS/WATCH 的证券代码、setup、原 reason_summary。READY 且 FOCUS/WATCH 为 0 明确为正常合法空结果。不存在快照时数量保留 null/NOT_AVAILABLE，不能填成零。

【建议】历史概览按项目交易所日历选择最近 N 个交易日（不按工作日猜节假日）。原计划/运行不存在时展示 NOT_AVAILABLE，同时保留原 delivery.execution_status 和 reason；真实 MISSED 仍保持。日历越界明确覆盖不足，不补造日期或运行记录。

【建议】后验仅汇总状态：T+1/T+5/T+20 的到期数量、已记录数量与 pending/missing；同时按全部 T+1..T+20 的“推荐×交易日”单位给出计数。成熟表示交易日收盘已到，不代表已采集或收益有效；停牌、不可用、无标签、未成熟分开保存展示，不评价收益、不重算后验。

## 风险与防护

【需要验证】必须覆盖 READY 有推荐、READY 合法空、NOT_READY blocker、FAILED error、历史查询，以及 MISSED/PLANNED/RUNNING/QUEUED、历史无记录、日历越界、未到发布时间、已到期但未采集、异常样本和只读不变性。

【建议】所有 fixture 明确 TEST_FIXTURE，测试使用隔离 DB/state。GET 不调用 POST、不注册计划/epoch、不写 snapshot/outcome；同一次观察使用统一 as_of。旧快照缺失只报告，不补写。策略、setup、阈值、readiness 和 outcome 计算模块不修改。

【建议】先做定向测试与代码复核，再在原管理入口和原环境上核验工具可用；不引入新服务或第二份权威数据。源代码变更按已授权工程流程核对发布检出，只提交本任务文件；真实运行材料和数据库不发布。交付 `phase1_observation_monitor_report.md`，分别记录工程检查、真实读验证及尚未完成的连续 20 日观察。

---

## 历史：此前 Phase 1 闭环实施计划

# StockRecommendation Phase 1 implementation plan

日期：2026-09-23。范围：可信每日研究荐股闭环；先分析、再实现。

## 1. 现状与复用

【事实】当前生产为 Python Daily Primary；Go 仅保留已有事实契约和影子验证，不在本期迁移。

| 当前模块 | 复用内容 | 缺口 |
|---|---|---|
| candidate_service / storage | immutable run、compressed detail、summary、official primary 索引、原子保存 | 统一轻量身份、冻结解释、按目标交易日查询交付状态 |
| stage_aware_momentum | 原四类决策、setup、reason codes、conditions、counterevidence | 用户日报需要直接读取这些冻结结果，不能再决策 |
| history resolver / strategy_tracking | 来源可见性、价格可比基准、后验基础、原 revision | 新增 FOCUS/WATCH 每个 T+1..T+20 会话价格路径，不改旧 score/交易模拟 |
| data_pipeline | 已合并 V2 后验需求，保留退池证券采集 | 显式补足新闭环待办，记录未完成和资料缺口 |
| scheduler / jobs | 单写入服务、持久任务、离线 MISSED、readiness | 晚间入口 critical capture 接线、有界重试、超时/中断状态及去重 |
| candidate_api / codex_research | 同一权威服务和私有报告出口 | 每日状态、快照、20 会话后验、分组统计的统一只读出口 |

【推断】本期应增加现有数据库中的派生 artifact 与消费者，不需要新选股算法、数据库、事件平台或实验管理系统。

## 2. 不变量

【建议】实施中不得改 STAGE_AWARE_MOMENTUM_V2 源码/配置、四 setup、FOCUS/WATCH/UNKNOWN/REJECTED 语义、readiness 门槛或既有 score 公式。旧 run、旧 label、旧 epoch 只读保留。调度/服务源码身份改变时由既有机制产生新 validation epoch，不能覆盖旧身份。

【建议】不接券商、不下单、不预测收益、不自动调参、不生成投资建议。生产服务、真实 DB/state、供应商采集和部署切换不在本次工程执行中改动。

## 3. MVP 数据与接口合同

### 冻结快照

【建议】新增纯投影模块 daily_research.py。identity 包含 run_id、observation_date、target_session、strategy_version、config_version、data_cutoff、generated_at、execution_status；引用现有 input/hash/epoch。每股保留 instrument_id、decision_status、primary_setup、reason_codes、简明原判定解释、关键 evidence、风险/未知、run_id。完整四类保存，展示分组不改排序。历史缺失字段明确 UNKNOWN，不用当前配置补写旧身份。

【建议】新 run 保存时在同一事务保存快照 artifact（同一 SQLite）。旧 run 可只读派生快照，不重新跑策略、不在 GET 写入。正式发布状态与原 readiness/attempt 一致，不把手工研究自动升级为正式前瞻。报告 renderer 只展示冻结系统判断和有来源的统计。

### 每日价格后验

【建议】独立追加消费者 daily_research_outcomes.py 使用原 snapshot 和已登记 MarketInput，每个 FOCUS/WATCH 生成 T+1..T+20 共 20 个逻辑观察位置。用已核验交易会话；缺未来日历保持 unavailable，不能按工作日猜日期。

【建议】每行包含目标会话、RAW close、comparable close、相对原冻结 comparable close 的 close_return、未来窗口 comparable highest/lowest、MFE/MAE、normal/missing/suspended/unavailable/not_mature、逐指标状态、来源、原 snapshot/hash、input identity、计算时钟与 revision。窗口为 T+1..T+h，极值沿用有符号价格变化，不是最大回撤/可成交收益。原基准缺失不能从后验回填；终点变化与全窗口路径完整性分开；异常、退池、停牌保留。

【建议】提供 evaluate_daily_research_outcomes(snapshot,bundle,evaluated_at,sessions=...)、select_daily_outcome_revisions(rows,as_of)、summarize_daily_research_outcomes(snapshots,rows,as_of)；按版本/config/epoch/证据类型/setup/tier/offset分组，报告样本、分布、上涨比例、MFE/MAE、成熟率/有效覆盖。无自动强弱排序，验证状态保留 NOT_VERIFIED。

### 产品出口

【建议】同一 CandidateService 增加 run 快照、后验、分组统计、按 target_session 查询日报（JSON/Markdown）能力，API/CLI复用；日报包含今日状态、市场原判断及未知、完整FOCUS/WATCH、UNKNOWN/REJECTED原因摘要、成熟样本/覆盖。失败日无 run_id 时明确为空，而非制造虚假策略run。

## 4. 自动执行与失败闭环

【建议】保留原 scheduler 和 JobService。晚间自动 capture 显式使用 DAILY_PRIMARY_CRITICAL，再走原 readiness 与 V2；完整采集/后验作为后台工作保留。NOT_READY 保存 blocker/timestamp/missing evidence；FAILED 保存脱敏 error/stage/timestamp；MISSED 保存 reason。GET 不创建计划或补造历史。

【建议】固定版本化调度策略：初次加最多两次重试，5/15分钟退避，目标会话08:45前停止；仅未发布且可重试的 NOT_READY/FAILED/INTERRUPTED，用户取消不自动重试，漏跑不伪造原时点采集。重试沿用逻辑日/epoch，独立attempt ID，保留真实完成cutoff。每attempt 30分钟协作式期限，保留 provider 现有限时；不强杀写库线程，不声称不可中断代码可以被硬超时终止。

【建议】每日后验使用 pending_daily_recommendation_outcomes() 和 submit_daily_recommendation_outcomes(run_id,input_id,job_id=...) 接入调度。基于run/input/policy去重，小批次调度，在primary窗口不新提交后台任务；后台失败不撤销已发布名单。保存到期积压与缺失状态。

## 5. 实施分工与顺序

1. 本文件先落地；核对源码基础及原策略/配置hash。
2. 并行实现纯快照/日报投影、20会话后验/统计、scheduler可靠性，各自独立测试。
3. 根任务整合 CandidateService 原子artifact、持久需求、读API、CLI与用户报告入口。
4. 集成测试状态、来源/未知、重复运行、重试/超时、旧run只读、20会话路径和分母；复跑相关既有回归。
5. 同步 PROJECT_OVERVIEW、CODEX_DAILY_RESEARCH、部署/工程基线及验证记录。
6. 仅暂存本任务文件，核对实际main远端和差异，中文提交、正常推送、核对远端commit；保护既有未跟踪审阅包。不新建分支/worktree、不强推、不切运行服务。

## 6. 验收与未验证范围

【需要验证】所有工程测试使用临时隔离DB/state和显式TEST_FIXTURE。至少覆盖：四决策原样保存；未来资料不污染解释；全1..20位置；完整/缺失/停牌/不可比/未成熟；T终点独立于路径缺口；修订按as_of选择；同输入幂等；有界重试及重复调度；NOT_READY/FAILED/MISSED报告；日报统计不混版本、不删异常、不填零。

【需要验证】保留本次实际测试命令、退出码和未执行范围。真实在线自动运行、供应商连续覆盖、生产部署和自然成熟20会话需要后续真实观察，不能用fixture替代，也不作收益有效性结论。

## 7. 本轮实施结果（2026-09-23）

【事实】上述代码步骤已完成：复用既有库/任务/输入，新增冻结快照、只读日报/API/CLI、FOCUS/WATCH 完整 20 会话后验、分组描述统计及自动调度的有界重试/漏跑记录。原策略、配置与评分文件的 Git blob 未变。实现口径见 [升级方案](../PHASE1_DAILY_RESEARCH_LOOP.md)。

【事实】最终相关回归 297 passed、退出码 0；页面脚本语法检查和差异检查通过。命令、边界用例与保护文件哈希见 [工程验收记录](stock-fish/docs/PHASE1_DAILY_RESEARCH_VALIDATION.md)。

【事实】最终交付状态补足了生成后、提交前的时间边界；人工重试在途状态不会被前一次失败遮盖；只对登记过的计划判断 MISSED。旧记录只读，未登记历史不补造漏跑；合法无 FOCUS/WATCH 不制造 outcome 待办。

【需要验证】生产服务尚未切换，真实连续运行与自然成熟 20 会话尚未验收。本轮工程通过不等同于线上稳定性或投资有效性结论。
