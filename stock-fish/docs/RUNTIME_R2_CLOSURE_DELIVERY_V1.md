# 独立运行环境、standalone R2 衔接与每日后验交付 V1

日期：2026-10-16。本轮沿运行环境、来源衔接、每日后验的顺序推进。源码发布与运行服务切换分开；旧虚拟环境、locator、活动数据库、调度、冻结策略、原 run 和其他未提交修改保持。

## 交付内容

| 层次 | 本轮实现 | 明确边界 |
| --- | --- | --- |
| 运行环境 | 两个新目录按官方 PyPI 工件 SHA 和 58 包版本锁重建，实际导入、CLI、应用和独立库检查 | 仅原生 macOS x86_64 / CPython 3.12；不含 Qlib、Live、模型，不替换旧环境 |
| 归档桥接 | 外部明确封存 SHA、原件/回执/生产源码、规范输入、原 run 和五导出一致后，同事务追加服务库与凭证 | 只支持 standalone 原生完整 inline 核心；不从普通 JSON 恢复 owner、历史 PIT 或供应商真实性 |
| 每日后验 | 精确当日 input、原 run、交易日收盘和 H1/H2/H3/H5/H10；持久作业、逐项状态、批次事务、幂等与显式重试 | 不重新选股，不把 UNKNOWN/PENDING 算成零收益，不扩大采集、引入定时任务或下单 |
| 运行边界 | 只读计划/待办/状态；写入前持有现有作业锁；明确库及 state | 写入口遇活动服务锁拒绝，所有访问服务库的入口拒绝旧版结构；不自动迁库、恢复无关作业或切换服务 |

详细合同分别见[独立环境](ISOLATED_DAILY_RUNTIME_V1.md)、[原件桥接](SHORT_HORIZON_ARCHIVE_BRIDGE_V1.md)、[每日闭环](SHORT_HORIZON_DAILY_CLOSURE_V1.md)和[后验指标](SHORT_HORIZON_OUTCOME_V1.md)。

## 实际操作顺序

下列命令从仓库根执行，`RUNTIME_PYTHON` 是已验收新环境的绝对解释器路径。`SERVICE_DB` 与 `SERVICE_STATE` 必须是明确选择的已有服务库和对应 state；命令不会替你创建库、升级结构或停止现有服务。先按[部署说明](../../DEPLOYMENT.md)核对实例及备份。

### 1. 验证完整封存归档

`ARCHIVE_JOB` 指向原 standalone 任务目录，`ARCHIVE_SHA` 是独立核对的原 `final-control.json` SHA256。该 pin 认证本地保存字节的一致性，不代表供应商签名。只有 `COMPLETE` 或 `COMPLETED_WITH_UNKNOWNS` 且真实 worker 已退出、核心和来源已封存的任务可接入。

```sh
"$RUNTIME_PYTHON" -I -B stock-fish/scripts/stockfish.py r2-archive-verify \
  "$ARCHIVE_JOB" --expected-final-sha256 "$ARCHIVE_SHA"
```

源码已更新时，可用 `--source-overrides "$SOURCE_MAP"` 显式提供旧源码副本。映射结构如下；键只能是登记的相对路径，值必须是旧字节文件的绝对路径：

```json
{
  "schema_version": "SHORT_HORIZON_ARCHIVE_SOURCE_OVERRIDES_V1",
  "files": {
    "stockfish_core/yaogu/candidate_service.py": "/private/original-source/candidate_service.py"
  }
}
```

只接受与原登记 SHA 相同的副本，不执行旧代码，不用副本恢复原 inode 或历史来源能力。副本及映射留在私有目录。后续计划和执行仍重新读取这些原件；移动、损坏或丢失必须明确处理，不能只凭保存凭证跳过。原生 inline 档案可保留原 `PROSPECTIVE_CAPTURED` 或 `RECONSTRUCTED_HISTORICAL`，不互相改写；历史数据后取得的真实时间仍参与后验核验，不抬升为过去已经可见。

### 2. 接入同一明确服务库

```sh
"$RUNTIME_PYTHON" -I -B stock-fish/scripts/stockfish.py r2-archive-import \
  "$ARCHIVE_JOB" --expected-final-sha256 "$ARCHIVE_SHA" \
  --db "$SERVICE_DB" --state "$SERVICE_STATE"
```

存在旧源码副本时同样传 `--source-overrides`。返回 `run_id`、原 `input_id`、`bridge_id` 和实际 `imported_at`；保存原 run/input 内容、五份导出身份和原截止不变。四份已鉴证 CSV 的 UTF-8 正文同事务保存，服务导出核对父 run、凭证及实际字节后返回原正文，避免数据库 JSON 键排序改变 CSV 内的 JSON 列；JSON 观察视图核对内容一致性。原 run 的 `source_database_written=false` 是原生成事实，导入事实另记凭证，不改写历史。重复相同接入返回已有凭证；冲突失败整笔回滚。

### 3. 收盘后发现待办并检查计划

当日资料沿原已授权 standalone 流程取得并完成接入后，使用其精确 `TODAY_INPUT_ID`。此处不自动采集，也不以 `latest` 代替明确输入。需真实上海交易日、收盘后截止、输入观察日及最后交易日均为当日；陈旧输入不能标成就绪。

```sh
"$RUNTIME_PYTHON" -I -B stock-fish/scripts/stockfish.py r2-daily-pending \
  --db "$SERVICE_DB" --state "$SERVICE_STATE" --input-id "$TODAY_INPUT_ID" \
  --limit 100 --offset 0

"$RUNTIME_PYTHON" -I -B stock-fish/scripts/stockfish.py r2-daily-plan \
  --db "$SERVICE_DB" --state "$SERVICE_STATE" --input-id "$TODAY_INPUT_ID" \
  --run-id "$ORIGINAL_RUN_ID"
```

待办包含全量计数和分页，不因默认每批 5 个 run 而丢弃尾部。发现待办只检查原保存记录完整性及当日输入原件，明确标记所选原档案在执行前仍须重新鉴证。最新后验须覆盖原池的全部证券乘全部五期限且均为 MATURE 才关闭；另可明确验证原资格完整的合法空池。只有 H10、部分证券、重复行、未知或未证合法的空池均不足。

### 4. 执行、查看与恢复

```sh
"$RUNTIME_PYTHON" -I -B stock-fish/scripts/stockfish.py r2-daily-run \
  --db "$SERVICE_DB" --state "$SERVICE_STATE" --input-id "$TODAY_INPUT_ID" \
  --run-id "$ORIGINAL_RUN_ID" --wait-seconds 360

"$RUNTIME_PYTHON" -I -B stock-fish/scripts/stockfish.py r2-daily-status "$JOB_ID" \
  --db "$SERVICE_DB" --state "$SERVICE_STATE"

"$RUNTIME_PYTHON" -I -B stock-fish/scripts/stockfish.py r2-daily-retry "$JOB_ID" \
  --db "$SERVICE_DB" --state "$SERVICE_STATE" --wait-seconds 360
```

重复 `--run-id` 可显式选择同批多个 run；默认上限 5，`--max-runs` 明确提高且最多 100，超限报错而不截断。重试也需明确足够的 `--max-runs`，不从旧 payload 自动提高额度。同日相同原 run 集合、当日 input、来源绑定和政策只创建一个根作业，首次受理截止冻结；重新调用不会悄悄重复执行。失败重试最多共 3 次尝试。

进程崩溃遗留的本类作业可通过显式 `r2-daily-retry` 恢复：只有新实例实际持有同库排他作业锁、目标不在当前工作线程中时，才将指定旧作业记为中断或取消、追加恢复事件，再沿原 payload 重试。无关任务和事件保持；不启动全局恢复。

可用 `r2-daily-cycle` 将当日归档接入和选定原 run 的后验连起来，参数为 `r2-archive-import` 加 `--run-id`、`--max-runs` 和 `--wait-seconds`，由接入凭证提供精确当日 input。接入与后验是两个持久阶段：后验失败仍如实返回已成功的接入凭证，不声称整段已回滚。

`--wait-seconds` 是提交后的等待预算，默认 120 秒、范围 1–3600 秒，不含前置鉴证和计划。真实大档案单次鉴证曾耗时约 147 秒，worker 仍须独立重验，默认等待不足时会请求取消；上述大档案示例显式等待 360 秒，不提高归档会话的 300 秒资源上限或采集预算；超时请求取消后仍等待线程协作退出，不是整个命令的硬截止。归档鉴证另有资源预算，同一个验证会话由该批所有 run/input 共享，不代表 cycle 全部阶段合计预算。超时只请求取消本命令新发起的作业，状态与事件仍可查询，不取消他人的排队工作。读取计划、待办或状态成功即退出 0，结果内仍可能包含 `can_submit=false` 或 FAILED；运行命令等待到 SUCCEEDED 也退出 0，等待到其他终态或命令失败退出 3，等待超时退出 4，用户中断退出 130，参数错误退出 2。导入后的中断另返回已成功接入的 bridge。

作业 `SUCCEEDED` 只证明工程处理完成，仍要查看每个 run、期限及证券的 UNKNOWN/PENDING/MATURE。计划 DUE 只表示日历到期，不表示资料已齐备。账本 `COMPLETED_WITH_UNKNOWNS` 优先保留未知；存在未来期限时为 `COMPLETED_PENDING_MATURITY`。合法空池独立标识，未证合法的零行不会生成空收益或成功成熟产品。

## 真实检查记录

详细 argv、实际退出码、运行身份和全部成功/失败日志保存在私有 `reports/codex/2026-10-16-runtime-r2-closure/`；包含本机路径、来源或真实数据的记录不发布。以下分开记录环境、工程夹具和真实资料，不合并为“全部通过”。

### 环境验收

两个新私有目录均使用 CPython 3.12.14，58 个发行包版本集合一致，官方工件哈希及安装身份已记录。环境验收阶段在发布目录执行 `bootstrap_daily_runtime.py verify --destination "$NEW_RUNTIME"`，6 个步骤全部退出 0，耗时 27.03 秒：`pip check`、安装身份、rules/daily/review 三个 doctor 和应用/CLI/独立库烟测。没有启动监听器或调度器，fixture readiness 的 503 是正确结果。

早期 baostock 的 15 秒冷导入超时和一次系统 Python 3.10 读取 PyPI 元数据的证书链失败保留；最终验收明确使用每包 45 秒/profile 240 秒预算，没有跳过包或关闭 TLS。Qlib 不在这份 daily/review 锁内。

### 工程检查

最终源码在发布目录执行同一条扩大检查，**204 passed、20 subtests passed，退出 0，339.72 秒**；运行前后所有本轮非文档文件 SHA 一致。命令如下：

```sh
"$RUNTIME_PYTHON" -I -B stock-fish/scripts/run_isolated_tests.py \
  tests/unit/test_runtime_bootstrap.py tests/unit/test_runtime_doctor.py \
  tests/yaogu/test_r2_closure_cli.py \
  tests/yaogu/test_short_horizon_archive_bridge_v1.py \
  tests/yaogu/test_short_horizon_daily_closure_v1.py \
  tests/yaogu/test_premarket_native_core_v1.py \
  tests/yaogu/test_short_horizon_strength_r1_integration.py \
  tests/yaogu/test_short_horizon_strength_r2_integration.py -q
```

覆盖完整原生 TEST_FIXTURE 归档到服务库、跨日后验及详情、四 CSV 原字节保留、桥接状态缺失/损坏/替换拒绝、请求 run 身份替换、事务末 RAW/源码迟变回滚和普通 R1/R2/standalone 导出兼容。开发目录此前桥接主套件为 72 passed、退出 0、141.02 秒；最后三项身份/残留状态补验为 3 passed、72 deselected、退出 0、16.54 秒，最终发布目录组合已经完整覆盖全部 75 项桥接用例。

CSV 原字节接入之前，桥接大文档的 SQLite 回调传递及资源清理版本在发布目录执行：

```sh
"$RUNTIME_PYTHON" -I -B stock-fish/scripts/run_isolated_tests.py \
  tests/yaogu/test_r2_closure_cli.py \
  tests/yaogu/test_short_horizon_archive_bridge_v1.py -q
```

**76 passed，退出 0，203.06 秒**，包含 56 项桥接和 20 项 CLI 检查。每日编排 38 项、运行工具与 doctor 36 项也有对应通过记录，另 20 个子测试通过；这些历史组共覆盖 150 个不同用例，不伪称一次全仓检查。该 56 项桥接版本的开发目录定向检查为 56 passed、退出 0、100.65 秒。随后发现真实 CSV 字节差异，继续修复并扩大最终受影响检查。

此前释放/重读原 run 的版本在同一发布目录组合为 **73 passed，退出 0，181.95 秒**；随后真实内存验收仍失败，因而继续修复并执行当时的 76 项检查（CSV 修复前），没有以先前夹具通过代替最终产品验收。

发布目录的 22 文件扩展回归执行 `run_isolated_tests.py`（完整选择保存于私有 `root-regression-files.json`），最新一次结果为 **501 passed、2 failed、45 subtests passed，退出 1，324.04 秒**。其中完整 CLI 联调在后验入库后误调用不存在的详情方法，测试已改为现有 `strategy_experiment_item_detail`，保留逐证券五期限和原 run 完整性断言。另一项既有 `test_core_container_direct_pins_match_verified_runtime_metadata` 依赖完整 core 的 pyqlib；该项在本独立 daily/review 环境未通过，未删除、skip 或改变其要求，也未混入旧环境包。

跨日整链检验还发现原 67 会话窗口自然滚动会被误拒。本轮改为严格比较两份日历共同保留至原信号日的区间，仍拒绝共同区间缺日、插入假日和未来轴压缩；没有修改冻结后验计算公式。真实原件验证、规范化、数据库、作业及后验计算均由实际实现执行，联调仅使用显式 TEST_FIXTURE 和固定历史测试时钟。

此前的内存生命周期版本在发布目录执行以下受影响组合：

```sh
"$RUNTIME_PYTHON" -I -B stock-fish/scripts/run_isolated_tests.py \
  tests/unit/test_runtime_bootstrap.py tests/unit/test_runtime_doctor.py \
  tests/yaogu/test_r2_closure_cli.py \
  tests/yaogu/test_short_horizon_archive_bridge_v1.py \
  tests/yaogu/test_short_horizon_daily_closure_v1.py -q
```

结果为 **144 passed、1 failed、20 subtests passed，退出 1，228.24 秒**。唯一失败是新增离线损坏夹具直接 UPDATE，被原有不可变触发器提前拒绝；随后只修正该夹具的离线损坏设置，并在检查目标行为前恢复触发器，产品源码未改。发布目录单独重跑 `tests/yaogu/test_short_horizon_archive_bridge_v1.py::test_verified_view_rechecks_database_binding_after_release`：**1 passed，退出 0，4.75 秒**。这样 145 个不同受影响用例均取得通过结果，原组合的非零退出仍保留，不改写成整条命令通过。

上述 51 项 bridge 版本的完整开发目录定向检查为 **51 passed，退出 0，95.29 秒**；daily 为 **38 passed，退出 0，45.50 秒**。发布目录组合同时覆盖真实原件实现的跨日 CLI 全链路、重复 cycle、详情/待办只读、导入成功后失败/中断回执、当前结构/排他锁及不恢复无关作业。所有这些均为隔离 TEST_FIXTURE；没有把其数据或时钟混入真实历史。

### 真实资料验收

真实验收只使用已有封存资料，网络、生产数据库连接及新采集均被验收入口禁止。首次发布目录导入在 203.11 秒后触发原 2 GiB RSS 上限：峰值 2,369,785,856 字节，进程退出 1。鉴证通过后，新 input/run 的通用写入曾同时展开完整对象、Unicode、UTF-8 和 SQLite 绑定副本；最终守卫拒绝提交。独立只读核查 `documents=0`，事务完整回滚，失败库及日志保留。

第二次真实导入在 188.25 秒后仍触发相同上限，峰值 2,515,079,168 字节；导入前鉴证为 143.29 秒、峰值 2,075,922,432 字节。定位到 SQLite 对大 input 正常 INSERT/JSON 解析时，完整原 run 仍同时驻留；并非取消了数据库检查即可通过。第二次失败的隔离库同样为 `documents=0`，日志与库均另存，未覆盖第一次失败。

据此增加桥接局部的规范 UTF-8 私有暂存：逐块编码并比对原 hash，释放重读 input 后正常插入，再完整重验实际存储字节；保留 SQLite JSON/索引约束及原事务。没有提高预算或改变原 payload。后续每日流程也清理跨阶段 capability，引入完整认证后的日历摘要及受控单份 run 读取；其结果由最终夹具及真实验收分别记录。

第三轮进一步在写 input 前解除已核验 run 的缓存与局部强引用，只保留原件引用和 hash；input 正常插入后，在同一事务内重读原 run、核对完整 hash，再写原 run 及投影。已有借用对象守卫保留，重读失败仍整笔回滚。该轮在 185.82 秒触发相同 RSS 守卫，峰值降至 2,271,191,040 字节，仍超过 2 GiB；失败隔离库再次完整回滚。检查点显示 SQLite INSERT 瞬时解析与 mmap 驻留重叠是剩余峰值来源。

最终实现改用本连接临时零参数回调传递已经核对 hash 的规范字节，使 Python 返回字节在 SQLite 索引解析前释放，避免 mmap 在整个 SQL 执行期间驻留。隔离进程的 128 MiB TEST_FIXTURE 实验中，同 SQLiteStore 结构的峰值由 1,068,892,160 降至 563,073,024 字节，耗时分别为 5.72 和 6.60 秒，均退出 0；实际 TEXT 字节 hash 一致。它只证明该夹具的传递成本改善，不能替代完整真实鉴证或跨日性能验收。

UDF 版本在发布目录的第四轮真实完整导入通过，进程退出 0；验证会话耗时 231.05 秒、峰值 RSS 1,895,751,680 字节（约 1.77 GiB），保持原 300 秒、2 GiB 上限。读取并核验 674 个文件，来源实际读取 965,296,284 字节；在全新隔离库同事务追加原 input、run、119 组来源凭证和索引投影。原件 SHA 与七字段物理身份终验保持，保留原 `RECONSTRUCTED_HISTORICAL` 覆盖类型，没有执行 selector 或写生产库。

新进程重新鉴证后的重复接入同样退出 0：182.31 秒，峰值 RSS 2,085,392,384 字节（约 1.94 GiB）。首次 `imported_at`、完整凭证及各 namespace 文档数量保持一致，没有重复新增；原件再次完成终验。该轮距 2 GiB 仅约 59 MiB 余量，不能据此承诺更大归档、多个大型 run 或长期运行均已稳定。

但第四轮随后从服务库重生 `all.csv` 的字节回查失败，退出 1、153.43 秒；未继续运行当日计划。诊断全池 3041 行后，差异仅来自三个流动性 JSON 列的分子/分母键顺序：规范存储按键排序，旧导出按原字典顺序序列化，所有数值、行顺序和其他列一致。没有把逐字节断言改成语义断言。修复在导入事务中保存已鉴证的四份原 CSV，并把桥接 run 的服务 CSV 正文读取绑定这些不可变原字节；普通 run 的原格式逻辑保持。既有 HTTP 路由仍按原合同添加 UTF-8 BOM，本轮正文回查不声称 HTTP 响应体与无 BOM 原档逐字节相同。

最终版本另建隔离库，完整真实导入通过，退出 0：244.67 秒、峰值 RSS 1,897,230,336 字节（约 1.77 GiB），核验 674 个文件，来源实际读取 1,202,790,070 字节。四份原 CSV、原 run/input、119 组来源凭证及投影在同一事务内提交，新增事务末完整原件重验也通过；没有提高原 300 秒、2 GiB 上限。第四轮的成功与失败均保留其实际源码身份，不替代最终版本证明。

最终版本在新进程中重复接入同样退出 0：178.86 秒、峰值 RSS 2,045,726,720 字节（约 1.91 GiB），首次导入时间、完整凭证及各 namespace 文档数量完全一致。没有重新选择股票、改变原 run/input 或重复新增导出。

最终版本的新进程导出回查退出 0：233.71 秒、峰值 RSS 2,051,399,680 字节（约 1.91 GiB）。实际服务读取的 `all.csv`、`observation.csv`、`focus.csv`、`unselected.csv` 与原归档逐字节一致；`observation.json` 内容一致。原件 SHA 与七字段物理身份终验再次通过。它证明保存结果及导出一致性，不代表新增市场观察或投资表现验证。

最终版本的实际日期门禁验收退出 0，状态为 `PASS_BOUNDARY_REPORTED`：整个计划检查 162.36 秒，内部鉴证会话 159.37 秒、峰值 RSS 2,081,628,160 字节（约 1.94 GiB）。实际上海日期为 2026-10-16，原输入观察日仍为 2026-10-15；返回 `can_submit=false`、`INPUT_NOT_CURRENT_SESSION`，正确拒绝陈旧输入。计划前后数据库 SHA、尺寸、inode、device、mtime/ctime、各 namespace 文档数量均一致，jobs 为 0→0。没有伪造当天输入、执行真实每日 worker 或生成成熟收益。

最终四阶段都使用同一冻结源码、新进程和同一新隔离库，退出码均为 0；最高峰值距 2 GiB 仅约 63 MiB，仍需保留容量与长期稳定性缺口。最终发布范围为 26 个源码、测试、锁和文档文件；其余 656 个基线文件与 204 条未提交状态保持。真实归档、数据库、环境、日志和全部本机验收包不发布。

## 保留的缺口

1. 默认旧环境、locator、活动服务和真实业务库未切换或迁移。本锁仅覆盖原生 macOS x86_64 / Python 3.12；Windows、ARM、Qlib 和 Live 未由本轮环境检查验收。
2. 桥接只认证原生完整 inline R2 核心及五导出，不接入 compact/history 引用、历史时间证明及 owner、题材/报道附件或账户执行。历史下载型原生 inline 档案保留原覆盖类型与真实时钟。代码升级的鉴证身份兼容需显式版本设计，不能用新 hash 静默覆盖旧绑定。
3. 每日闭环已有显式入口；连续真实交易日的新资料、足够后验窗口、性能与长期可靠性仍需逐日积累。默认最多 5 个 run 是数量边界，不保证多个大型归档能共同满足资源预算；批次仍保留借用 run 和待提交产品。300 秒从验证会话创建计时，包含后验计算经过的时间；2 GiB 在 macOS 检查进程历史峰值。二者均为协作检查阈值，不是操作系统强制配额。没有启动新的计时器、收费服务、全历史采集或回测。
4. 缺证券身份、可比价 basis、行情、完整交易日或父来源可见时间仍为 UNKNOWN。限价、费用、容量和账户成交属于尚未验证的执行层，不因标价观察成熟而通过；本 V1 也不把缺限价事件自动变成全部标价指标未知。工程通过不说明 DAILY_V1 齐备，更不证明可成交收益或市场优势。`MARKET_EDGE=NOT_ESTABLISHED`。
