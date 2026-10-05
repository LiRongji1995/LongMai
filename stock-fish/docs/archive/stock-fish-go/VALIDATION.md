> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../README.md)查找。

> 2026-10-03 后台验收收尾：本轮以 b728c60 为基线，缓存上下文与最终指标修复及能力边界见 [后台交接](BACKEND_READINESS_FOR_CLI.md)。2026-10-04 用户已授权本轮源码提交推送，生产未切换；以下既有记录按各自时间与源码身份理解。

# StockFish Go V2 本轮验收

## 2026-10-03 一致性修复：隔离发布成功，整体验收未通过

本轮基线为 `19dab9a0f4de35808ca487a30ab1e1ba6a82ade0`、分支 `review`，用户后续已明确授权将本轮代码、测试和维护文档提交推送到远端 main；私有数据与验收产物不随源码发布。当前私有证据入口为 `reports/codex/stockfish-v2-rebuild/consistency-20261003/REPORT.md`、`acceptance.json` 与 `budget.json`。原冻结源已完成新版全量验证，批次投影深验、正式隔离发布与新进程 readiness 通过；旧轮次未发布状态不再代表当前目标。最终 Go 源码身份为 `ecc990c6926b277a9ff9f597fca364185bd0d3228d25b5971180d54c4a48cf44`。

冻结源通过经过字节核验的私有独立视图消费；目标验证持 SQLite 写保留锁，已验证的迁入范围禁止追加。研究包闭合验证、发布及 `BUNDLE_V1` 账本同事务。真实输入回放式回退、bundle-only 恢复，以及新增原生结果/任务后的有损回退阻止和新文件恢复均已通过。隔离调度 PAUSED，生产未激活。

R1/V2 新库原生运行、Python 保存语义投影差分、Go 新旧存储精确差分及 R1 冷暖差分通过。六次全池额度包含一次保留的 RSS 失败，修复后五次成功。观察日 2026-09-30，cutoff 2026-10-02T20:10:00.478775+08:00。R1 为 6 CORE_PASS、41 CORE_UNKNOWN；V2 为 0 FOCUS、5 WATCH、1 UNKNOWN。两者必要资料、背景与选择可判定性仍 INCOMPLETE，均为 NOT_EVALUABLE_AS_COMPLETE_STRATEGY；不能将零 FOCUS 当作完整资料下的合法空集。

完整规模真实增量未执行：保留现有源、目标、备份和结果后，受核验源视图与耐久副本的峰值超过 50 GiB 准入上限，详见 `incremental-admission.json`；合成增量测试不替代该项。新增迁移预算 14400 秒与旧 7199.963 秒分开，未挪用策略预算。Windows 仅交叉编译，原生未验证；缓存字节指标的上下文缺失另列限制。R1 仍为 RESEARCH_ONLY，MARKET_EDGE=NOT_ESTABLISHED。本轮源码提交推送按后续明确授权执行，生产不切换。

本轮最终源码全套 Go 正式验收测试、适用 race、gofmt/vet、原生构建及 Windows 交叉编译通过；4 项 Python 工具单测通过。最终全套日志为 `memory-full-tests`，末次内存改动 race 为 `memory-native-race`；此前一致性/生命周期与解码末次 race 为 `final-lifecycle-race`、`decode-race`。逐项命令、退出码和失败保留在当前 `budget.json`。Windows 原生运行未验证。

## 2026-10-02 历史记录（旧验收状态与授权，不代表本轮）

基线为 `1ceb256e5331ec24ca9a5bb4185c52ebbdba4245`，检出分支 `review`，开始时工作区干净，实际 origin 为 `LiRongji1995/stockfish-qlib-quant-system`。用户后续授权检查后提交并推送 main，不授权生产切换。先前仅诊断实现的记录保留于 Git 历史。

该轮结束时整体验收 **BLOCKED_MIGRATION_BUDGET**，本次工程尚未全部完成。真实数据及逐股结果只保存在被忽略的 `reports/codex/stockfish-v2-rebuild/`，不随源码发布。测试 fixture 使用独立 DB/state/artifact，不写入真实历史。

### 2026-10-02 历史工程命令与证据

`GO` 为私有 `work/r1-go-tools/go/bin/go`（Go 1.27.1），`ORACLE` 为 `reports/codex/stockfish-v2-rebuild/TEST_FIXTURE-oracle-full-v3`。Python 仍使用原 pyqlib 0.9.7 wheel。

| 实际命令 | 结果及范围 |
| --- | --- |
| `STOCKFISH_R1_ORACLE=$ORACLE STOCKFISH_FORMAL_ACCEPTANCE=1 GO -C stock-fish-go test ./... -count=1` | 退出0；core 123.719s、CLI 20.061s、sqlitev2 10.798s、lifecycle 8.666s；后续恢复边界补修另有定向回归。`go-full-tests.log`。 |
| `GO -C stock-fish-go vet ./...` | 退出0，`go-vet.log`。 |
| `STOCKFISH_FORMAL_ACCEPTANCE=1 env -u STOCKFISH_R1_ORACLE GO -C stock-fish-go test ./core -run TestFullPythonSemanticDifferential -count=1` | 预期退出1，`FORMAL_ACCEPTANCE_BLOCKED_ORACLE_REQUIRED`；正式验收无 oracle 不能跳过。 |
| `STOCKFISH_R1_ORACLE=$ORACLE STOCKFISH_FORMAL_ACCEPTANCE=1 GO -C stock-fish-go test -race -p 1 ./... -count=1` | 首轮退出1：race 下每例原30s超时，core达默认10m总超时；并行新增文件导致后续编译文件集不一致。保留 `go-race-tests.log`。仅测试单例预算改为300s，样本/断言/生产预算未变；稳定源码重跑另录。 |
| `python -m pytest -q stock-fish/tests/r05_product/test_research_r1.py -k 'amount_boundary or ma20_boundaries or accel_or or accel_required_conditions or accel_known_fail_beats_missing or independent_setup_background_turnover_and_order or close_limit_safety'` | 退出0，23 passed、31 deselected，4.01s；冻结R1定向回归。 |
| `GO -C stock-fish-go test ./infrastructure/sqlitev2 -run '^$' -bench BenchmarkControlledIndexEvidence -benchtime=1x` | 退出0，37.682s；独立TEST_FIXTURE同结果hash对比，见 INDEX_BENCHMARK。 |
| `GOMAXPROCS=2 STOCKFISH_R1_ORACLE=$ORACLE STOCKFISH_FORMAL_ACCEPTANCE=1 GO -C stock-fish-go test -p 1 ./... -count=1 -timeout 30m` | 最终全套退出0，13个有测试包通过：core52.098s、sqlitev2 8.823s、CLI5.987s、HTTP1.335s。日志`go-final-gated-tests.log`。随后资源初始化失败返回终态的补修及就绪门禁4项定向测试退出0，0.734s。 |
| `GOMAXPROCS=2 STOCKFISH_R1_ORACLE=$ORACLE STOCKFISH_FORMAL_ACCEPTANCE=1 GO -C stock-fish-go test -race -p 1 ./... -count=1 -timeout 30m` | 最终稳定源码全套退出0，13个有测试包均通过：core580.651s、lifecycle51.690s、sqlitev2 124.255s、CLI60.071s、HTTP21.728s；无race报告。日志`go-final-gated-race-tests.log`。 |
| `GOMAXPROCS=2 GO -C stock-fish-go vet ./...`；全部Go文件`gofmt -l` | 最终均退出0，未格式化文件0；`go-final-gated-vet.log`、`gofmt-final.json`。 |
| `python -m unittest discover -s stock-fish-go/tools -p 'test_*.py'` | 退出0，4项保存结果读取/篡改测试，0.082s。 |
| `python stock-fish-go/tools/build.py --go GO --command stockfish --output PRIVATE_BINARY`（另构建stockfish-migrate；Windows设置`GOOS=windows GOARCH=amd64 CGO_ENABLED=0`） | 三次均退出0；统一源码hash `3f18e26cf8ef99ca9a2933525abc1c5fbbf411fc033bee593c7fecf301bdafbe`。Windows仅交叉编译，无原生运行。 |

14组原始fixture对照Python R1/V2的资格、选用模型行身份、事实、全部setup/条件树、理由、背景、覆盖及排序。使用精确数值及原显示舍入规则，不用epsilon。Python保存结果未暴露的所有历史bar原字节绑定，另由证据测试及旧/新存储逐项比较覆盖，不冒称异构DAG字节相等。详见 STRATEGY_PARITY。

恢复测试中断2601项结果的第51个已提交批次，保留2550项未发布数据。显式封存后旧结果不能查询/再发布，新作业可以完成；原字节、摘要和头信息篡改均拒绝。封存使用不可变账本，不删除旧证据。APFS测试实际调用clonefile，验证独立写入不改源、WAL已提交数据保存、租约/WAL条件失败；跨平台fallback保留空间门槛。

最终审阅补修：统一CLI/迁移命令与生命周期租约，规范现有文件及新文件父路径别名；锁后读取实例配置；排队执行和结果发布事务重新核对迁移/扩展就绪；进度SQL取消保留先发生的资源超限原因。HTTP旧fixture因未建立新增批次投影而失败3例，已改为在临时空源上实际Snapshot/Migrate/Verify/Backfill/Publish，保留原业务断言。故障注入确认已提交item之后撤销权威时保留PREPARING和原字节，不能发布。

未通过的中间检查也保留：`go-final-integrated-tests.log`为主动SIGINT中断，以释放本机2核/8GiB资源供有累计deadline的真实验证；不计通过。`go-final-stable-tests.log`为上述3例HTTP fixture缺扩展门禁失败。修复后采用稳定源码完整重跑，不删除失败记录或扩大skip。

### 2026-10-02 历史迁移与独立运行

旧权威库只读，通过SQLite online backup冻结，源SHA-256为 `f48ea2aa217173c34b20eda59be0f2a6409fd3b6858681a703390d202c4a1f4e`，7,752,036,352字节。快照/迁移/验证/恢复累计预算7200s；研究最多6次、单次1800s、累计10800s；新持久文件不超过50GiB、可用空间至少10GiB。每步实际时长保存在私有账本。

新schema将原字节证据、精确TEXT投影、内部成员、归档映射、结果/逐股摘要、任务/事件、缓存分开。旧派生缓存明确丢弃重建，不计为原始资料迁移。实际驱动 modernc.org/sqlite v1.60.1、SQLite 3.53.4；DDL、外键、不可变触发器、schema checksum均参与验证。

迁移实际SIGTERM后从同一冻结源checkpoint恢复，最终搬迁3358份documents（19个namespace）、2,155,121条历史证据、4,219,074条历史成员、8个任务、31个事件及1条旧schema记录；237,120条派生缓存明确丢弃重建。目标为私有 `reports/codex/stockfish-v2-rebuild/private/stockfish-v2.sqlite3`，未发布，不能研究。

最后完整验证命令为私有 `stockfish-migrate verify --source frozen-source.sqlite3 --target stockfish-v2.sqlite3 --seconds 1079`，进程退出1，`context deadline exceeded`。累计账本 **7199.963326802012s / 7200s**，包括此前失败和中断；没有重置预算。已完整核对：3358份document双向身份集合、全部历史身份与元数据、全部成员集合、1263份分区内容hash、61,039份独立evidence对象原字节、全部2,155,121条目标历史原字节与类型化投影。最终源payload hash仅核对251,132 / 2,158,479条；余下源原字节、关系投影/旧结果内层压缩引用、外键、integrity_check、最终VERIFIED标记均未完成。输出中默认的`foreign_keys=0`不是通过证据。

真实新库的批次投影扩展、外部R1包迁入、隔离增量、独立完整业务、切换后新写入及备份/回退/恢复尚未执行。真实全池预算使用0 / 6次，不能用尚余研究预算绕过迁移发布门禁。恢复需同一冻结源重新完整验证；当前实现不支持把中途验证计数拼接成完整证明。追加预算请求未得到确认前，停止所有真实迁移/验证/恢复操作。

最后验证的1053次外部采样峰值RSS695,775,232字节（663.54MiB），最小可用空间16,970,547,200字节（15.80GiB）；采样自monitor启动起，不能当作整个任务的保证峰值。实际资源/状态见私有`migration-ledger.json`、`storage-acceptance-status.json`、`verify.json`与`real-recovery-drill.json`。macOS sandbox已实际拒绝旧stock-fish路径、冻结旧库、Python执行并移除PATH；业务运行仍须单独验收，不能以隔离配置或拒绝探针代替成功。

### 2026-10-02 性能记录

阶段指标包含输入SQL、历史分区/raw/投影校验、JSON解码、来源/修订、事实、策略及输出压缩/保存复核。逻辑payload、唯一/重复证据、缓存字节是应用层计数，不是物理磁盘IO。

持久run的`summary.metrics`在保存前取快照，不能充当端到端耗时；输出压缩、保存和最终复核指标出现在资源scope结束时的完整日志。真实性能报告必须保留该日志，并用外层计时涵盖研究、重新验证与名单查询，不能只引用策略计算时间。截至 2026-10-02，该轮真实链路未运行；2026-10-03 的真实测量见当前私有 real-performance.json，仍不推断因果提速。

中点秩预排序/sector共享和的fixture基准约229倍/44倍；当前索引fixture窗口/横截面/摘要约41.30/25.85/6.88倍。这些仅证明局部优化。真实全链路须比较同输入/源码旧新存储冷暖运行，不能与旧615.57s诊断直接相除。

### 2026-10-02 历史完成矩阵

| 项目 | 当前判定 | 证据及边界 |
| --- | --- | --- |
| GO_ARCHITECTURE | IMPLEMENTED_TESTED | 域/证据/事实/独立策略/应用/端口/基础设施/CLI HTTP；依赖测试，core仅兼容。 |
| R1_PARITY | FIXTURE_PASS_REAL_PENDING | 完整规则/背景，14例差分；显式RESEARCH_ONLY。 |
| V2_COMPATIBILITY | FIXTURE_PASS_REAL_PENDING | V2默认身份及独立状态/排序；路由范围见HTTP_COMPATIBILITY。 |
| INPUT_EVIDENCE_CONTRACT | TESTED | 原字节先验、投影另记、四时钟/父源/修订/PARTIAL/重建用途、bar身份绑定。 |
| DATABASE_SCHEMA | IMPLEMENTED_TESTED_REAL_PARTIAL | 主/应用/bundle/批次证据投影版本约束、完整校验实现及查询计划；真实扩展回填尚未运行。 |
| REAL_DATA_MIGRATION | BLOCKED_MIGRATION_BUDGET | 真实全量搬迁完成；verify超时，未publish。 |
| NEW_DB_INDEPENDENT_OPERATION | BLOCKED_UNPUBLISHED_DB | 隔离拒绝旧库/Python已证明，业务闭环未运行。 |
| INCREMENTAL_AND_RECOVERY | FIXTURE_PASS_REAL_PARTIAL | 晚到修订/可变任务fixture通过；真实SIGTERM和checkpoint恢复搬迁通过，真实增量未执行。 |
| ISOLATED_CUTOVER_ROLLBACK | FIXTURE_PASS_REAL_BLOCKED | 单写者、备份、变更导出fixture；真实演练因迁移预算未执行。 |
| DIFFERENTIAL_TESTS | FIXTURE_PASS_REAL_BLOCKED | 跨语言/存储/缓存/binding fixture；真实全池未运行。 |
| REAL_SCALE_RUN | BLOCKED_UNPUBLISHED_DB | 0 / 6次；不把旧diagnostic或已有Python结果改名为新Go正式结果。 |
| PERFORMANCE | PARTIAL | 局部算法/索引有实测，全链路冷暖待运行。 |
| CROSS_PLATFORM | MACOS_TESTED_WINDOWS_CROSS_COMPILED | 最终源码Windows amd64交叉编译通过；Windows原生运行未验证。 |
| PRODUCTION_ACTIVATION | NOT_ACTIVATED_EXPECTED | 不切换生产服务、路径、默认策略或外部调度。 |

未执行Qlib训练、全仓Python测试、全历史回测或投资有效性验证。旧执行/训练与SDK保留Python路径；R1/V2规则、新库读写、公共事实不依赖Python子进程。参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`。

## 本轮真实 R1 资源失败及修复边界

`consistency-20261003/r1-new-cold.*` 保留第一次真实运行的失败证据：3040 只事实已经计算，但结果编码至约 48 MB 时外层 RSS 达 1,077,608,448 字节，超过 1 GiB，任务记录为 FAILED，未发布 run；该次仍计入六次全池上限。随后主 CLI 的 Go 堆软目标由 768 MiB 降至 512 MiB，原生引擎在事实转结果前释放输入引用，在每 16 MiB 编码后回收已经释放的对象，逐股释放 coverage support。编码字段、原始字节/投影检查、策略公式及资源硬门禁保持原语义。新 `result_encoding` 阶段单列编码时间；真实修复效果以最终预算账本、RSS 和保存结果差分为准，不由堆软目标推定。
