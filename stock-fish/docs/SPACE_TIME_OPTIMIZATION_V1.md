# 空间与时间优化 V1

本轮把复核发现的重复校验、解压临时副本、未来采集 RESULT 存储、容量增长、后验计时和调度任务查询接入显式工程入口 `scripts/codex_optimize.py`。旧源码、旧 run、RAW、回执和生产服务保持原身份；新增执行版本不改变策略、公式、参数、资格或 UNKNOWN。参数仍尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`。

## 执行入口与兼容性

从 `stock-fish/` 使用已登记的项目 Python；下文 `STOCKFISH_PYTHON` 指自己的既有解释器，不是安装新环境。共享根、源码和解释器仍读取原运行登记。统一入口用登记解释器、隔离模式和全新字节码查找位置执行，核对运行绑定并保留实际退出码。scheduler/outcome 的 DB/state 由其显式参数选择，`shared-daily` 只为这些命令提供源码和解释器绑定。默认聊天入口仍为 `codex_chat.py`，本轮工具需显式选择。

```sh
"$STOCKFISH_PYTHON" -I -B scripts/codex_optimize.py --runtime shared-daily storage-plan
"$STOCKFISH_PYTHON" -I -B scripts/codex_optimize.py --runtime shared-daily storage-record --sample-id before-operation
"$STOCKFISH_PYTHON" -I -B scripts/codex_optimize.py --runtime shared-daily storage-history
```

旧执行器文件不能直接覆写：历史鉴证记录钉住它们的字节、路径和物理身份。新适配器在独立函数全局空间中调用原状态机与断言；不会替换已导入模块的校验器，也不建立跨请求 hash 缓存。首次核心 C/128 编码继续使用原显式适配器及其恢复边界。旧归档与 V2 归档的登记身份分开，不能把 V1 ID 当作 V2 ID。

## 归档复用与新核心

`codex_core_archive_v2.py` 在执行原 RAW/RESULT/receipt 全部断言时保留本次解码得到的 `first_seen_at`，避免仅为取时钟再次读和解码整份 RAW。gzip JSON 解码改用有界 `bytearray`，避免同时保留全部块列表和拼接 bytes；仍有 Unicode 与完整 JSON 对象分配，不是恒定内存解析。

V2 新核心的八份压缩产物在同一语义鉴证阶段内逐份完整读取并校验逻辑 hash，不再先全部解压校验一遍、随后为语义检查再解压一遍。所有八种角色必须消费成功；最后仍重新读取每份物理压缩文件，核完整 SHA 和物理身份。写入阶段原 `compact_owned` 的完整回解与清理前验证仍保留；原 owned 清理、原件末验、来源集合、资格与摘要检查保留。

```sh
"$STOCKFISH_PYTHON" -I -B scripts/codex_optimize.py --runtime shared-daily archive-register \
  --manifest ORIGINAL_REQUEST --worker-dir ORIGINAL_WORKER --supervisor-dir ORIGINAL_SUPERVISOR \
  --profile FULL_CORE_ARCHIVE_READONLY_V2
"$STOCKFISH_PYTHON" -I -B scripts/codex_optimize.py --runtime shared-daily archive-reuse \
  --archive-id V2_ARCHIVE_ID --profile FULL_CORE_ARCHIVE_READONLY_V2
"$STOCKFISH_PYTHON" -I -B scripts/codex_optimize.py --runtime shared-daily core-run \
  --manifest CORE_REQUEST --profile FULL_CORE_COMPACT_VERIFY_V2
```

首次计算可显式选 `core-first-run` 和 `FIRST_CORE_REFERENCE_VERIFY_V2` 或 `FIRST_CORE_CANONICAL_128_VERIFY_V2`；其他参数遵循 `codex_first_core_v2.py --help`。这是新执行身份，首次计算与归档复用分别计量；不能用复用提速推断全市场首次计算提速。

## 新采集 RESULT 压缩

`codex_capture_compact_v1.py` 只压缩本次新采集、尚未发布的 RESULT 包装文件。真实 RAW、receipt 与 producer 保留原物理身份；压缩体单独记录存储 SHA 和完整逻辑 SHA。发布前完整回解并运行原鉴证，之后只删除自身刚创建且尚未发布的展开 RESULT。旧缓存命中直接复用原件；旧 RAW/RESULT 不搬迁、不替换、不追认。

新命令 `capture --request REQUEST` 默认拒绝网络缺口；仅明确附加 `--execute-network` 才可新采集。REQUEST 是原 RequestSpec 的五个字段：`endpoint`、`parameters`、`business_session`、`role`、`current`。`verify-capture --archive ARCHIVE` 完整验证新格式。`core-from-captures --request REQUEST` 接受 `CODEX_COMPACT_CAPTURE_CORE_REQUEST_V1`，包含 `compact_archives` 完整引用以及 `observation_date`、`target_session`、`cutoff`、`coverage_type`，经新逻辑读取器直接进入原 native core。核心输出继续 C/128 编码和 owned 压缩，复用完整验证八份产物及来源。

这些命令在独立进程组内执行，外部监督时间、进程树 RSS、控制文件和 owned 输出，避免仅依赖 Python 检查点。原 worker 预算与来源绑定同样执行。失败保留，禁止自动重试、删除失败目录后重采或把缓存缺失当合法 EMPTY。

本机既有 cache 约 187 MiB，已超过登记配置的 64 MiB；新采集协议还有保守上限，取值只收紧原限额。本轮不扩大预算，也不执行真实联网采集。因此本机新写入当前会被容量门禁挡住，不能宣称新压缩采集已投入日常运行。完整新 writer→鉴证→native core→压缩→复用链使用独立 `TEST_FIXTURE` 验证；只读 verify 不因写缓存上限而删除原件。

原 137 份 RESULT 的内存压缩回解试验：104,250,101 字节可编码为 14,337,640 字节，差 89,912,461 字节；这是旧字节的可压缩性测量，不是本轮已释放空间。RAW 也可压缩，但其物理身份是证据锚点，本协议保留。实际旧数据回收量为 **0**。

## 容量增长与保留策略

容量计划复用一次完整原目录元数据扫描，同时报告逻辑量、分配量、管理上限、各目标卷底线及下一次核心的峰值预留。`FULL_CORE_COMPACT_VERIFY_V2` worker 要求 260 MiB，计划取它与登记通用预计增长量的较大值，再加 2 MiB 控制文件余量；本机合计预留 262 MiB。可继续净增长量需要先扣除该预留，不把总剩余空间全部视为可用。它是观察时的保守建议，不能替代执行时准入；扫描不完整时返回未知并拒绝准入推断。

`storage-record` 只追加小型不可覆写的观察记录；相同 sample ID 返回原观察，不冒充新测量。`storage-history` 提供两次观察之间的有符号净变化，它包括该期间所有活动，不能归因于单个作业。样本最多 256 份，单份 64 KiB，不创建自动计划。

保留策略明确区分证据、环境、未知失败和新 owned 暂存；不会自动删除 RAW、回执、旧 run、失败作业或共享环境。未引用文件仍须逐项审查依赖，不能按时间或文件名直接回收。原 1 GiB 逻辑上限、10 GiB 卷余量保持。

真实前后观察与history命令均退出0，两次观察间逻辑净增长265,957字节（含第一次观察元数据和新鉴证记录，不是单一操作独占增长）；环境根净变化0。随后完整计划统计580,095,505逻辑字节，扣除262 MiB准入预留后，可继续净增长218,919,407字节，约208.78 MiB。该数字只对应声明的compact V2容量条件，不解除capture缓存门禁，也不保证资料或执行时间已就绪。

## 调度查询

`codex_scheduler_query_v1.py` 为既有 SQLite `jobs` 创建版本化的部分表达式索引：按 scheduled 类别和 root job ID 定位任务族，再按原创建时间、job ID 排序。仍使用原调度状态、取消和重试算法。普通查询不改变原全列表接口；拒绝含重复 `root_job_id` 的歧义 payload，查询参数须为非空字符串，存储的非字符串 root 不匹配，扩展 DDL 变化拒绝。

```sh
"$STOCKFISH_PYTHON" -I -B scripts/codex_optimize.py --runtime shared-daily scheduler-status --database EXISTING_DB
"$STOCKFISH_PYTHON" -I -B scripts/codex_optimize.py --runtime shared-daily scheduler-explain --database EXISTING_DB --root-job-id ROOT_ID
```

迁移需显式 `scheduler-migrate --database EXISTING_DB`，获取原服务排他锁并要求既有健康库；不创建业务 DB、不自动升级旧结构、不停服务。未来使用该查询适配器的服务入口是独立脚本的 `serve --database EXISTING_DB --state EXISTING_STATE --port PORT`，本轮没有启动它或迁移真实业务库。

独立 SQLite 夹具包含 30,000 个任务，其中 10,000 个 scheduled，目标族 3 个。三次查询旧耗时 1.431/2.058/2.008 秒，新耗时 0.0396/0.0497/0.0300 秒，完整结果及顺序一致；解码数 10,000→3。tracemalloc 峰值 2,915,551→189,137 字节（不是 RSS）。索引及扩展记录使 SQLite 逻辑页增加 720,896 字节，迁移约 0.130 秒。EXPLAIN 从全扫描/临时排序变为索引 SEARCH。以上不是生产负载测量。

## 后验耗时诊断

`outcome-profile` 只接受原 R2 `*-plan`、`*-status`、`*-pending` 及 `r2-archive-verify`，其后原样传原 CLI 参数。输出原结果、真实退出码和 cProfile CPU 分阶段数据；normalizer、hash、数据库父文档校验、重构、evaluator、完整 terminal 分列，包含时间可能重叠，不能求和。写入、worker、重试和导入命令在执行前拒绝。

计时有自身开销，不是无监测性能验收；原 300 秒门禁不改。此前完整后验的私有业务库已不存在，本轮没有重建来源或重跑真实后验，**默认 300 秒性能仍未通过**。诊断入口已具备，实际热点须在下一次明确授权且来源齐备的后验中验证。

## 验收与未执行范围

本轮检查记录在私有工程报告中，公开只保存聚合结果和可复现命令；不发布行情、真实路径、完整回执或测试 DB。工程测试、真实旧归档测量和投资结论分别记录。

真实旧归档为观察日 2026-10-23、目标会话 2026-10-26 的历史重构材料，不代表当天最新资料就绪。V2首次完整登记退出0，用时254.58秒，normalizer一次、selector/provider均零，仅新增小回执。随后串行 A/B/B/A，各自新进程、原登记解释器、全新字节码查找位置，测量完整reuse CLI（不含外层聊天分发）：

| 指标 | A：原V1 | B：显式V2候选 |
|---|---:|---:|
| 两次wall秒 | 60.753 / 64.760 | 63.274 / 67.298 |
| wall中位数秒 | 62.756 | 65.286 |
| 每次Python实际返回读取字节 | 889,065,516 | 818,563,162 |
| 校验器采样RSS峰值字节 | 1,335,009,280 / 1,090,113,536 | 1,279,508,480 / 1,299,406,848 |
| 完整terminal次数 | 2 / 2 | 2 / 2 |
| normalizer / selector / provider | 每次均0 | 每次均0 |

四次退出码均0，完整原summary、run/input、资格数量与导出绑定一致，全部原件末验通过。返回读取量降低7.93%，这不是内核磁盘I/O或联网字节；耗时中位数反而增加约4.03%，RSS采样也未呈现稳定改善。**本轮未证明真实归档延迟或峰值内存优化成功，V2保留显式候选，旧默认入口不替换。** 两次/臂仅是本机样本，不能外推生产性能。首次核心八产物验证的减少重复解压由隔离测试验证，本轮未重跑真实全市场首次核心。

早期只解码真实 input 的四次诊断受其他隔离测试并发影响，因此不作为性能验收依据；其完整input hash和末验通过也不能代替整条reuse性能。

发布检出目录最终组合回归：**152 passed、122 subtests passed，退出0**；pytest 155.19秒，外层157.62秒。全部使用独立DB/state/artifact及显式TEST_FIXTURE；包含V1/V2共存、完整摘要与八产物语义、同字节物理身份变化、认证后源变动、重封摘要攻击、压缩损坏/尾随数据、失败不重试、预算不扩展、参数缩写绕过、SQL顺序等价和容量边界。监督反例实际启动并终止owned子进程，RSS超限采用注入采样值，未施加真实2 GiB内存压力。

在 `stock-fish/` 使用原登记解释器，`NEW_EMPTY_LOOKUP` 指每次新建临时父目录下尚不存在的字节码查找路径；实际命令及输出另存私有报告：

```sh
"$STOCKFISH_PYTHON" -I -B -X "pycache_prefix=$NEW_EMPTY_LOOKUP" scripts/run_isolated_tests.py -q \
  tests/unit/test_codex_optimize_entry_v1.py \
  tests/unit/test_codex_storage_growth_v1.py \
  tests/unit/test_codex_verification_v2.py \
  tests/unit/test_codex_capture_compact_v1.py \
  tests/unit/test_codex_scheduler_query_v1.py \
  tests/unit/test_codex_capacity.py \
  tests/unit/test_codex_workspace.py \
  tests/r05_product/test_daily_research_scheduler.py \
  tests/unit/test_durable_storage.py
```

较早分组测试与上述组合有重叠，不累加测试数量。修复过程中发现并保留失败日志，包括fixture口径、监督路径、共享计量及参数边界；没有删除失败样本、放松旧断言或扩大skip来获得最终通过。

最终保护核对退出0：除明确更新的8份说明文档外，开发目录1,861份、发布目录1,857份、共享数据1,529份、登记根20份既有文件均保持完整SHA与七字段物理身份一致，没有意外变化。两处既有iCloud占位只做元数据识别，未打开或下载；发布只暂存本轮23个文件，既有缺失fixture/占位改动保留。`git diff --check` 退出0。

没有全历史回测、真实新采集、全市场首次核心重算、真实后验 worker、真实 DB 索引迁移、生产服务切换或投资有效性验证。旧失败和 UNKNOWN 保留；本轮交付不意味着 DAILY_V1 已就绪。
