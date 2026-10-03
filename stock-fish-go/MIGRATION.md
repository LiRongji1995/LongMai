# SQLite V2 与原生 Go 迁移状态

## 2026-10-03 一致性修复：隔离发布成功，整体验收未通过

本轮基线为 `19dab9a0f4de35808ca487a30ab1e1ba6a82ade0`、分支 `review`，用户后续已明确授权将本轮代码、测试和维护文档提交推送到远端 main；私有数据与验收产物不随源码发布。当前私有证据入口为 `reports/codex/stockfish-v2-rebuild/consistency-20261003/REPORT.md`、`acceptance.json` 与 `budget.json`。原冻结源已完成新版全量验证，批次投影深验、正式隔离发布与新进程 readiness 通过；旧轮次未发布状态不再代表当前目标。最终 Go 源码身份为 `ecc990c6926b277a9ff9f597fca364185bd0d3228d25b5971180d54c4a48cf44`。

冻结源通过经过字节核验的私有独立视图消费；目标验证持 SQLite 写保留锁，已验证的迁入范围禁止追加。研究包闭合验证、发布及 `BUNDLE_V1` 账本同事务。真实输入回放式回退、bundle-only 恢复，以及新增原生结果/任务后的有损回退阻止和新文件恢复均已通过。隔离调度 PAUSED，生产未激活。

R1/V2 新库原生运行、Python 保存语义投影差分、Go 新旧存储精确差分及 R1 冷暖差分通过。六次全池额度包含一次保留的 RSS 失败，修复后五次成功。观察日 2026-09-30，cutoff 2026-10-02T20:10:00.478775+08:00。R1 为 6 CORE_PASS、41 CORE_UNKNOWN；V2 为 0 FOCUS、5 WATCH、1 UNKNOWN。两者必要资料、背景与选择可判定性仍 INCOMPLETE，均为 NOT_EVALUABLE_AS_COMPLETE_STRATEGY；不能将零 FOCUS 当作完整资料下的合法空集。

完整规模真实增量未执行：保留现有源、目标、备份和结果后，受核验源视图与耐久副本的峰值超过 50 GiB 准入上限，详见 `incremental-admission.json`；合成增量测试不替代该项。新增迁移预算 14400 秒与旧 7199.963 秒分开，未挪用策略预算。Windows 仅交叉编译，原生未验证；缓存字节指标的上下文缺失另列限制。R1 仍为 RESEARCH_ONLY，MARKET_EDGE=NOT_ESTABLISHED。本轮源码提交推送按后续明确授权执行，生产不切换。

## 2026-10-02 历史记录（旧验收状态与授权，不代表本轮）

本轮在现有工作区实现模块化 Go 主系统及真实数据库迁移，不改写旧 Python 策略/配置/研究身份，不修改旧数据库，不切换生产服务。旧 `go-core` 影子模块保留。总验收为 **BLOCKED_MIGRATION_BUDGET**：真实搬迁已完成，累计7199.963s达到7200s上限，完整验证未结束且目标未发布；真实全池差分、冷暖性能及独立运行/切换仍未完成。命令退出码由 [VALIDATION](VALIDATION.md) 汇总，不能由本表的“已实现”推导为真实验收通过。

统一入口是 [README](README.md) 的 `stockfish`。模块职责见 [ARCHITECTURE_V2](ARCHITECTURE_V2.md)，证据和输入边界见 [EVIDENCE_CONTRACT](EVIDENCE_CONTRACT.md)，策略逐条件与fixture范围见 [STRATEGY_PARITY](STRATEGY_PARITY.md)，隔离实例生命周期见 [ISOLATED_LIFECYCLE](ISOLATED_LIFECYCLE.md)。

## 能力映射

| 原权威资料/业务能力 | Go V2 实现 | 实现与验收边界 |
| --- | --- | --- |
| Fraction/Decimal、Python canonical hash、三态条件 | `domain/contracts` | 精确值与half-even，跨语言向量；原字节hash与model/canonical hash分别保存，不以重编码替代原字节 |
| 输入模型、资格、四时钟、父来源、范围、修订 | `evidence` | 严格解码、显式null/缺字段区别、日期与字段约束、父链与PARTIAL策略；拒绝未知不代表通过降级为假 |
| 收益/均线/市场/sector/funds/压力事实 | `facts` | 原生完整计算；共享排序/聚合、证据验证之后的事实缓存；数据UNKNOWN与实现验收状态分开 |
| R1_FULL 与 V2_CONTROL | `strategies/r1`, `strategies/v2`, `strategies/shared` | 两套独立决策；R1非默认、research only；V2不先运行R1；保留全setup/reason/rank与覆盖 |
| documents 全 namespace 与其他旧表 | `infrastructure/sqlitev2` | 全键盘点、原字节归档、稳定旧外部ID映射；识别但未支持的有效结构使迁移失败，不静默跳过 |
| history_evidence_rows / partition_members | 新库证据对象、TEXT精确投影、内部键与分区关系 | 全量raw hash、投影重建、成员集合/分区hash、缺失LEFT JOIN检查；数据库内可独立读取历史 |
| 旧结果/输入/任务历史 | 旧ID映射与只读归档查询 | 原历史任务不自动恢复执行；`legacy` 原内容查询，`import-legacy-input` 保留原cutoff与parent身份 |
| Python R1 result/manifest/index/shards 外部文件包 | `sqlitev2/bundle.go` | Go验证并保留所有文件原字节、canonical hash及引用闭合；原run_id不重算；分shard有界导入，最后publish |
| 新研究结果与查询 | `application`, `sqlitev2` 结果头/逐股payload/摘要投影 | 来源与参数可追溯、市场共享引用、结果追加只读；摘要/详情/导出同源；发布前完整校验 |
| 新任务与事件 | `application` 持久状态机 | 幂等提交、取消、崩溃标记INTERRUPTED、显式新尝试，事件追溯；不靠重跑查询 |
| 派生缓存 | `sqlitev2` FactsCache | disposable、有容量上限；版本、输入、history、cutoff、日期、证券、窗口及selected evidence入key；原始完整性不跳过 |
| CLI / HTTP | `interfaces/cli`, `interfaces/httpapi` | 同服务/DTO，默认V2、R1显式；loopback默认、写入认证、路径边界、持久任务及可分页查询 |
| 预算、取消与进度 | `ports`, `infrastructure/runtime` | SQL与纯计算context、逐记录/分块预算；RSS与heap分开；无法获得指标明示不可用 |
| 隔离切换/回退/恢复 | `infrastructure/lifecycle` | 带标记私有实例、单写者、备份与变更账本；不可表示的Go-only结果阻塞旧格式回退，保留数据 |

## 迁移顺序与恢复

从仓库根目录运行，所有路径由调用者提供。迁移目标和输出必须与旧源分离，先核对当前实例/源身份及可用空间。`snapshot` 使用 SQLite 原生一致性 backup，不复制正在变化的主文件假装快照。

```sh
work/stockfish database inventory --source /path/to/legacy.sqlite
work/stockfish database snapshot --source /path/to/legacy.sqlite --target /path/to/private/frozen-source.sqlite
work/stockfish database migrate --source /path/to/private/frozen-source.sqlite --target /path/to/private/v2.sqlite --batch-size 250 --budget-seconds 7200
work/stockfish database verify --source /path/to/private/frozen-source.sqlite --target /path/to/private/v2.sqlite --budget-seconds 7200
work/stockfish database publish --target /path/to/private/v2.sqlite
```

主 schema 之外，`SQLITE_BATCH_EVIDENCE_PROJECTION_V1` 以独立版本/checksum 管理题材成员、资金及回执类型化列，精确值为 TEXT，并保留字段缺失/null/非法状态、原字节引用与转换哈希。`publish` 在主验证通过后回填、全量核对该扩展再开放目标；`database project-batches --target ...` 可按 owner 事务恢复，`database verify-batches --target ...` 深度核验。输入写入与对应投影同一事务，readiness 检查已发布投影 inventory。2026-10-03 已通过真实 publish 入口完成此步，证据为 publish-real.stdout；不由此推导策略资料完整。

每个批次与检查点同事务；重复 migrate 只允许接续同一冻结源。相同旧键必须逐内容核对；同键异内容失败。迁移取消/中断后已提交批次保留，但未经完整verify/publish的目标不能成为活动权威库。命令单次参数不能重置任务累计预算；2026-10-03 新增 14400s 与旧 7199.963s 分开汇总，研究最多 6 次/10800s 独立且按策略逐次计。

增量使用显式 `database incremental --source ... --target ...`；先前未处理的源变更不能通过切换自动吞掉。外部R1包用 `bundle-import --input OLD_RUN_DIRECTORY` 导入，然后 `bundle-verify` 和向新目录 `bundle-export`；无需旧数据库或Python即可验证和恢复原文件内容。

## 已有定向证据与未完成边界

14例隔离原始输入已对照原Python R1与V2的资格/事实、setup整棵条件树、背景、coverage和排序，正式缺oracle失败；具体字段与限制在 [STRATEGY_PARITY](STRATEGY_PARITY.md)。额外证据合同、DB损坏、幂等/取消/恢复、bundle round-trip、隔离生命周期测试使用独立fixture，不能替代真实全库验收。

真实旧 `run-optimized` 已确认保存 R1_FULL 和 V2_CONTROL；新Go run需固定同原始输入/历史/cutoff比较。`compare_saved.py` 只读验证原共享DAG，并校验新库每项gzip原字节和成员身份；公开字段精度按原规则还原，不使用epsilon。旧保存结果未公开的每条历史bar原字节绑定必须通过独立原始证据/投影到原字节绑定与跨存储检查补充，不能声称整份异构JSON字节一致。

全池旧适配/新冷/新暖计时必须使用同语义、同母池、完整保存及验证边界。合成算法约229倍中点秩、44倍sector共享求和提升仅是局部基准，不代表生产研究整体提速。清缓存、重启、迁移中断恢复、查询/导出与不可表达回退结果均以实际记录为准。

Windows交叉编译不等于Windows原生运行；JSON单值编码/解码内部不能被context逐token抢占，记录边界及读写分块负责取消；强杀/断电不能保证failure文件写出。这些边界保留，不通过降coverage、扩大skip或覆写旧结果取得通过。
