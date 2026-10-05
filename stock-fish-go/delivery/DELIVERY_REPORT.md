# 四阶段工程交接与再次评估：2026-10-05

本轮状态为 **ENGINEERING_CANDIDATE_ACCEPTED_WITH_DECLARED_REAL_DATA_AND_PERFORMANCE_LIMITS**。用户明确要求继续完善、评估并同步代码，随后授权将项目总量上限从 50GiB 调整到 **57GiB**；其余次数、新增2GiB、RSS和空闲空间预算保持；用户已明确批准工程累计5400→6000秒，账本有效上限6000秒；其他预算不变。起始远端 main/HEAD 为 `6d98dc500d180f4d1a33fd703164a9f434ebe6bc`，上一轮源码提交为 `84cc09d404d953f12aada444531378786a90b43b`。最初容量拒绝及本轮所有失败均保留于原 task_id 账本，没有删除旧证据或重置额度。新包及数据库为私有验收产物，源码同步与生产激活分别处理。

## 已实现能力与实际验证边界

| 阶段 | 当前实现与实际工程验证 | 真实验收边界 |
|---|---|---|
| CLI 正确性 | 解析真实 cutoff、稳定同刻选择、业务身份兼容、共享作业停止等待与显式取消、SDK实际离线导入；CLI/daily/HTTP 定向race通过 | 整体竞态合并状态 PASS；在线批次未启动，缺合法历史 base 时在网络前阻断 |
| 查询、解释与基础复盘 | 一致只读事务内完整闭包深验，去除重复解压；保存条件树中文解释、table/JSON/CSV/Markdown、保守 diff；review 追加、幂等、修订、成熟状态与资格独立分母；CLI 持久化/重启/导出 fixture 通过 | 真实性能按下表独立报告；单股仍承担全量校验成本 |
| 增量与评价 | 有原字节/父来源/解码证明才跨输入复用；精确跨日 rolling windows 与 fixture 独立全量差分；修订/资格/calendar/basis/缺失/算法身份变化失效；原生入口注入事实身份；历史桥复用已验证 RAW 并要求 fresh factor/master/calendar | 旧包真实冷计算成功、暖计算失败，严格比较未执行；缓存修复不继承旧包成绩，真实跨日和完整旧库增量未验收 |
| 候选研究 | 提交后新读事务取得实际可用时间上界、版本化 proof/ledger 闭合；固定主窗口及独立 holdout，隔离≥10会话；全部尝试/latest失败优先；影子事件绑定两份记录ID/hash，失效后显式完整重入；fixture 持久化闭环通过 | 没有真实预登记/成熟后续资料就不准入；输入分布无预登记参照为 NOT_EVALUATED；生产晋级 DISABLED |

默认主动策略保持 `STAGE_AWARE_MOMENTUM_V2`；R1/V2 正式规则与配置原字节未改。R1 为 RESEARCH_ONLY，参数“尚未验证”，MARKET_EDGE=NOT_ESTABLISHED。准确数值、未知、全池范围及原安全断言保留。算法缓存命中只减少部分算术；resolver、资格、修订选择、公共全池基准、背景与策略仍重算。

## 本轮实际检查

- 完整普通检查仅一次：`go test -p 1 -count=1 ./...` 退出 **1**，耗时 424.213s；仅架构检查发现应用层依赖具体 buildidentity。其余包（含正式独立 R1 oracle）通过。修复为入口注入后，对 `./application/research ./infrastructure/native ./architecture` 定向普通检查退出 **0**，179.393s。没有重跑完整普通检查，也没有放宽架构断言。
- 完整竞态检查仅一次：`go test -race -p 1 -count=1 ./...` 退出 **-15**，1798.059s，状态 FAIL。application/research 和 core 触发单包默认10分钟超时，总守卫随后终止完整命令；原非零退出保留。research 11项定向 Go 退出0，记录器 TypeError 单列并从原事件恢复；CLI/daily/http 定向通过。core 的3项输入合同及 funds_proxy、low_amount 两个 oracle 先定向通过，其后实际追加记录按完成事件合并。竞态合并覆盖 **PASS**：原完整命令非零退出保持，原串行超时前缀的完成推断与后续明确PASS事件分别登记；core剩余正式oracle均已定向补验。 尚缺子例清单：`NONE`。原串行超时前缀属于从下一运行用例推断前序已返回，不能伪装成各项独立PASS日志；最终机器记录明确保留这一依据。完整 vet 仅一次：`go vet -p 1 ./...` 退出 **1**，71.321s，唯一测试结构体按位置初始化问题已改为字段名初始化，原值和断言保持；该测试及应用研究包 vet 定向复核退出 **0**，19.331s。Go 1.27.1 / darwin-amd64，普通检查正式模式使用14组已登记独立 oracle，离线 Go 依赖、不升级模块。
- Python bridge/worker 定向18项、actual HTTP admission 本地10项、预算/身份/固定依赖24项通过。HTTP fixture 覆盖 redirect、retry、失败连接、CONNECT及跨 worker 持久上限；它们不冒充供应商在线结果。
- 原 SDK 环境缺依赖的失败保留。一次受控官方依赖准备，47个精确版本及下载 SHA 已登记；下载64,383,030B、94次依赖HTTP、耗时69.863s，未改原环境。实际 SDK 离线导入及无网络最小调用通过；不等于 ONLINE_REFRESH 通过。
- 旧源码 `3f63b1b14f52051835338ba6fe0353abd3a52887e4b91407e48066520a88d8f4` 包外验收 **PASS**，共 24 条实际命令；中文/空格路径、帮助、init拒绝覆盖、doctor真实SDK检查、dry-run、缺配置/参数非零、四格式导出、持久化重启、原fixture DB字节不变，以及显式新 TEST_FIXTURE 安装与不完整包拒绝均有逐条退出码。新缓存候选包 `stockfish-darwin-amd64-cache-v2`，Go/runtime源码 `73fdc56605b4c6a03d0cdd3d9d7037d62bdebc70e19c9bc357f2c0267f1860fc`，二进制SHA256 `6d1a8b511f90d665a4ee0e51dc68bc98d7e85f8121432ac044efbba59062035b`；业务 `f12d4c2550918bd9fed61efb4ba86741ff37c6d469672f43ec56f3fa934716ab`；事实 `6698cc854d5ec2b24a7db4d7bfa4c9caa2d880aed0ac573b5ae40e0724e1e97a`；包外验收 **PASS**，24 条命令。 当前源码、构建业务/facts身份和实际二进制SHA最终复核 **PASS**。 旧包检查不继承给缓存修复。安装仅为工程 fixture，未替换现用二进制、改PATH、启服务或留下常驻任务。

中间失败（fixture前提、目录测量竞争、架构依赖等）均保留并以定向复核修正；未删除失败样本、扩大skip或降低断言。人工操作清单仍 **NOT_RUN_BY_USER**，Windows原生及交叉构建本轮 NOT_RUN；旧 Windows/旧包成绩不继承。

## 真实查询与研究

真实保存 run 为 `go_run_f3de7b267bd8e69167d1d8eec0b9e580`，R1完整保存集合3040条、原始明细约228MB，观察日2026-09-30、cutoff原文本2026-10-02T20:10:00.478775+08:00。每次 picks/explain 仍为 **FULL_PUBLISHED_RESULT_CLOSURE**，包括全部明细、摘要、成员和顺序；无 verified 标记、mtime信任、活跃 immutable 模式或静默降级。

旧3f63源码包（二进制 `5665f1cb1b18952f57bdd41e4d8a94978fe598f2bfb20a126c991e415aa0c790`）测量身份状态 **MATCHED_OLD_PACKAGE**；当前缓存包测量状态 **MEASURED_CURRENT_BINARY_READONLY_SAVED_RESULT**。两份数据各自绑定实际二进制，旧成绩不作为缓存修复后性能；当前记录仅在二进制匹配、状态MEASURED、同一run/config、完整闭包及主DB SHA前后相同时采用。

| 端到端场景 | 旧3f63包实测 | 当前缓存候选包独立实测 |
|---|---|---|
| CLI启动/version（无独立性能目标） | 20 次新进程；P95 0.039s，median 0.031s，max 0.046s；NO_TARGET | 20 次新进程；P95 0.046s，median 0.033s，max 0.137s；NO_TARGET |
| 保存候选清单（目标P95<1s） | 20 次新进程；P95 5.534s，median 4.760s，max 5.772s；NOT_MET | 20 次新进程；P95 5.676s，median 4.811s，max 5.677s；NOT_MET |
| 保存单股解释（目标P95<2s） | 20 次新进程；P95 5.337s，median 4.320s，max 5.705s；NOT_MET | 20 次新进程；P95 5.649s，median 5.068s，max 6.907s；NOT_MET |

实测硬件：Intel(R) Core(TM) i5-7267U CPU @ 3.10GHz，内存 8.0GiB，4逻辑CPU，本轮GOMAXPROCS=2。每个样本启动新 native CLI；首次、minimum/median/P95/max、实际组件区间和命令保存于私有性能报告。OS缓存不受控，后续可能受益；**同进程暖查询 NOT_MEASURED**。内部区间会重叠，不相加冒充墙钟；SQL/逻辑source bytes不等于物理磁盘IO。主数据库查询前后 SHA 是否一致以本轮性能报告为准。若目标 NOT_MET，交付完整性正确的路径及实际成绩，不能缩小验证范围制造达标。

当前缓存候选包查询实测 **MEASURED_CURRENT_BINARY_READONLY_SAVED_RESULT**：picks 20 次新进程；P95 5.676s，median 4.811s，max 5.677s；NOT_MET；explain 20 次新进程；P95 5.649s，median 5.068s，max 6.907s；NOT_MET。同一保存run及config、完整闭包、主DB SHA前后一致，具体硬件/环境/逐次退出码见对应报告；这些是保存结果只读查询，不是当前包全池研究或暖计算。

新旧包picks/explain保存展示的逐次stdout SHA集合一致；各自样本数和唯一hash数量见机器记录。这证明本次已保存结果展示一致，不是全池研究、冷暖或跨日增量等价。

真实全池研究已使用 **2/2 次**，均属于旧3f63包。冷计算 **PASS**、退出 0；暖计算 **FAIL**、退出 1，1700秒作业预算到期，返回 `WAIT_BUDGET_EXCEEDED; operation: context deadline exceeded`，未发布可比较结果。冷/暖严格比较 **NOT_RUN_WARM_FAILED_NO_STRICT_COMPARISON**；没有比较器成功或业务等价结论。任务标识为 `{"real-full-cold": {"job_id": "job_5c65c6a9665c3cd6b8e9920e8695dd86", "state": "SUCCEEDED", "revision": 250, "progress": 3040, "run_id": "go_run_f0c71c2f718389817304472bcec3e693", "attempt_status": "PASS", "exit_code": 0, "go_source_hash": "3f63b1b14f52051835338ba6fe0353abd3a52887e4b91407e48066520a88d8f4"}, "real-full-warm": {"job_id": "job_f7c855638d8a909d9c775ec22580bd52", "state": "FAILED", "revision": 292, "progress": 2809, "attempt_status": "FAIL", "exit_code": 1, "go_source_hash": "3f63b1b14f52051835338ba6fe0353abd3a52887e4b91407e48066520a88d8f4"}}`。启动、失败均计次，不能退还暖计算额度或以修复为由重开两轮。

当前已修改 `infrastructure/sqlitev2/cache.go` 并新增缓存字节计量回归源码，避免BLOB容量统计/逐出路径强制读取完整载荷；字节计量、LRU与事务保持；修复后三项缓存定向普通/race及SQLite包vet状态 **PASS**，细节见独立记录。起初以551,550,976字节保守峰值预留申请准入，因 `STORAGE_ADMISSION_DENIED` 未启动；该拒绝保留。随后清理已终止编译任务的archive/object并保留所有诊断，在原存储限额内以600MiB保守峰值预留准入，执行当前源码原256MiB完整容量逐出normal/race，独立记录 **PASS**，来源SHA匹配当前cache.go、无失败/跳过，未改原断言或扩大skip；清理不退还时间。 **新缓存修复的真实冷/暖及研究性能未复跑**，状态 **NOT_RUN_RESEARCH_ATTEMPTS_EXHAUSTED**；保存结果只读查询按上表独立记录，不能替代全池研究或冷暖比较。不能由旧包冷成功、失败中的缓存命中数、兼容业务/facts身份、只读查询或 fixture 推导修复后研究收益。旧包 picks P95约5.53秒、explain P95约5.34秒，均未达到1秒/2秒目标。

SQL容量查询微测 **PASS_READONLY_SQL_COMPARISON**：Python SQLite 3.37.2，同一只读事务，旧/新SQL各20/20次交错配对；实际payload 116848732字节。旧CAST中位0.069582860s，新CASE中位0.018647558s，约3.73倍，仅限这两条SQL及此SQLite后端；主DB SHA前后一致状态为True，OS缓存不受控。不是native查询、全池研究、真实暖计算或修复后端到端性能。

旧证据 after preservation 核验状态 **PASS**，范围为原输入字节、六个旧run身份、数量、摘要/manifest/成员闭包hash与全部压缩明细、原history行数及不可变发布身份；没有声称再扫描全部历史payload。

真实库两份输入都为2026-09-30且cutoff同瞬、SHA不同；当前 refresh 没有严格早于目标观察日的合法 base。未来较晚目标也会遇到同瞬不同hash base 歧义，保守阻断，不能改日期/cutoff或cold绕过。**ONLINE_REFRESH=BLOCKED_NO_LEGAL_PRIOR_BASE，REAL_CROSS_DAY_INCREMENTAL=BLOCKED_NO_LEGAL_DISTINCT_OBSERVATION_INPUT，LEGACY_INCREMENTAL=NOT_RUN_SOURCE_AND_PEAK_UNVERIFIED**。历史桥与跨日引擎 fixture 能力不冒充这三项。长历史 native append 尚未实现，inline链仍受64MiB上限；超过明确阻断，不宣称无限增量扩展。

## 身份、预算与交接判断

新缓存候选包 `stockfish-darwin-amd64-cache-v2`，Go/runtime源码 `73fdc56605b4c6a03d0cdd3d9d7037d62bdebc70e19c9bc357f2c0267f1860fc`，二进制SHA256 `6d1a8b511f90d665a4ee0e51dc68bc98d7e85f8121432ac044efbba59062035b`；业务 `f12d4c2550918bd9fed61efb4ba86741ff37c6d469672f43ec56f3fa934716ab`；事实 `6698cc854d5ec2b24a7db4d7bfa4c9caa2d880aed0ac573b5ae40e0724e1e97a`；包外验收 **PASS**，24 条命令。 旧包 `stockfish-darwin-amd64` 的二进制SHA256 `5665f1cb1b18952f57bdd41e4d8a94978fe598f2bfb20a126c991e415aa0c790`；Go/runtime源码 `3f63b1b14f52051835338ba6fe0353abd3a52887e4b91407e48066520a88d8f4`；业务 `bfbf36bc22f10bec99f58571a0bedcadfc29337e744120312a67904ab62b09e3`；事实 `91a1a32b4e12e86eaff2efdd6516c99ef759e63e40d7e8659795c2d3289bfc3a`。这份旧包拥有本轮真实查询及冷成功/暖失败的证据，当前缓存源码不得复用其完整构建验收。BUILD.json、stockfish.identity.json 与 checksums.sha256 绑定各自实际构建；固定worker source manifest包含实际生产Python资源、四份公开冻结配置及需求文件，按原ROOT/config相对位置携带。旧输入、旧 manifest 与旧 run 不补写兼容身份。许可和 THIRD_PARTY_NOTICES 保留，MiroFish适用AGPL-3.0，仓库不称纯MIT。

原task_id **STOCKFISH_FOUR_STAGE_20261004**；材料生成时工程保守计费快照（含当时40秒收尾预留）为 5827.648/6000s（包含一次依赖准备），只读 710.743/1800s，真实研究 3186.997/3600s；在线 0/1批，供应商HTTP 0/100；训练/参数搜索/批量历史回测均0。最近终检项目 55.453/57GiB，新增计费 1.395/2GiB，卷空闲 18.866GiB；进程树采样峰值 0.990GiB。现有DB增长及保守新行预留单独入账；预留不是声称实际写入量。发布及最后材料检查还由原额度计费，最终私有 budget.json 为准。

再次自评：旧构建的核心正确性、证据边界、恢复/幂等及独立CLI使用已有本轮定向证据；当前缓存修复须按其自身构建和检查报告评价，已达到有明确限制的工程候选交付标准。原竞态超时与补验覆盖范围、真实暖计算失败、严格比较未执行、新缓存研究性能未复跑、各自包查询实测和目标状态及真实跨日/在线/完整旧库增量继续单列。工程接受与真实验收分别判断，不称成熟完备，不以工程PASS推导投资结论。本报告为提交前验收快照；实际提交/推送以源码提交、本机Git核验记录和交付消息为准。MANUAL_ACCEPTANCE=NOT_RUN_BY_USER，PRODUCTION_ACTIVATION=NOT_AUTHORIZED。
