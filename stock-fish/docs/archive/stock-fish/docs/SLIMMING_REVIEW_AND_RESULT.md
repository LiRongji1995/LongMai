> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../README.md)查找。

# 全项目瘦身与行为保持重构（2026-10-01）

## 检查点与交付边界

最初 HEAD 为 `d62bfa9953d9bb32684b7e6964f22800aa632b13`，分支 `review`，远端 `git@github.com:LiRongji1995/stockfish-qlib-quant-system.git`。用户随后明确要求先提交已有修改：8 文件已按原始内容指纹核对，相关测试 97 passed、退出 0，提交为 **`ba54d6cd176bb5357b3187cf523155fb8b34f463`**（完善采集预算与历史传输配置并修正正式导出校验）。瘦身以此为基线，未推送；用户随后再次明确要求本轮完成后提交，因此验收后的瘦身改动将独立本地提交；仍未授权推送。

业务目录为 `stock-fish/`。约定发布检出在当前工作区和上级均不存在。只通过进程/工作目录核对本业务 Python 实例，未请求其 API、读取其 DB、停启实例或修改部署配置。所有测试使用临时 SQLite/state 和显式 TEST_FIXTURE。源码所在目录存在运行实例，不代表其已重新加载本次模块，不能把工作区结果当作线上生效。

唯一原始证据目录为仓库根忽略目录 `review/slimming/`：`baseline.json` 保存完整 1248 个受控文件清单、原内容 SHA256、分类字节与初始 dirty；`initial.diff` 保存初始补丁；`before/` 保存业务源码/测试原件；`baseline-work/` 是 `git archive ba54d6c` 的普通临时源码副本，不是分支/worktree。`references.json` 记录 1000 个受控入口/引用文件的静态导入、动态注册调用、脚本与文档引用。未以扫描生产数据代替盘点。

证据标签：ACTUAL_EXECUTION 为真实命令；SYNTHETIC_TEST 为实际运行的合成测试，不等于真实行情验收；STATIC_SOURCE_REVIEW 为源码证据；旧文档性能数字仅为 PRIOR_REPORT_CLAIM；推断标为 INFERENCE；缺环境/样本的部分为 NOT_VERIFIED。`MARKET_EDGE=NOT_ESTABLISHED`。

当前检查点：主要代码改造已实现并分批验证；同环境前后完整套件已结束，仍存在基线失败和环境缺口，最终结果见验收记录。不继续扩展存储迁移、参考树删除或初始化架构。

## S01—S12 处理清单

| 项 | 状态 | 实施/保留与证据 |
|---|---|---|
| S01 运行资源 | IMPLEMENTED_UNVERIFIED（Docker）；宿主应用已验证 | `Dockerfile` 增加 `templates/` 明确白名单，未 COPY 全仓。`test_runtime_resources.py` 检查 literal 运行资源，并只复制白名单到临时目录，实际 import app、检查日报中文内容/脚本/API、401 鉴权和未知数量。无 docker 命令，未构建镜像，不能称容器通过。 |
| S02 依赖分层 | DONE_VERIFIED（最小日常入口） | 新增 `requirements-rules.txt`、`requirements-daily.txt`、`requirements-qlib.txt`。`requirements-core.txt` 改为共享规则+Qlib 引用，展开后的 15 个旧直接 pin 完全一致；Docker COPY 同步包括被引用文件。旧 Windows 锁和 requirements.txt/dev 兼容入口不变。pip 解析并实际安装全新最小环境；完整研究解析遇 Cargo v4 lock 不兼容阻塞，完整研究/集成及 Windows 未验收。没有宣称默认兼容镜像已经变小。 |
| S03 初始化 | RETAINED_WITH_REASON | `ApplicationService` 的 ModelRegistry/QlibQuantEngine 只建目录/轻量对象，ResearchEngine 无配置不调用 LLM；Qlib 原生执行在 worker 中。隔离 app 启动及 health 后没有导入 qlib/lightgbm/sklearn/openai。保留现有 JobService、接口、首次调用与关闭语义；没有把轻量构造器冒称大模型初始化，也未另造有锁的懒加载平台。 |
| S04 Qlib/qlib-zh | RETAINED_WITH_REASON | `qlib/` 619 文件、8,123,493 字节为参考树；固定来源见第三方通知，但项目补丁打包重建未实测，不能删除。`qlib-zh/data_runner.py` 被 API、Docker 与兼容测试直接引用；其余研究入口保留。未改 wheel 为 editable。 |
| S05 MiroFish | RETAINED_WITH_REASON | 39 文件、1,139,460 字节；已被 dockerignore 排除、Compose 已有实验 profile，不计作本轮收益。嵌入版本与项目补丁的独立可重建证据缺失，保留 AGPL 源码/许可证。 |
| S06 共用装配 | DONE_VERIFIED | 在 `candidate_service.py` 内提取纯 `_finalize_strategy_result`，两条 builder 显式传入 target/coverage 与 run identity 差异。Full 的 run ID 不新增 execution_profile；Fast 保留该字段。固定输入对比完整叶子差异，未忽略整个 metadata/reason/identity。 |
| S07 观察查询 | DONE_VERIFIED（合成） | 新增 `daily_read_context.py`：请求局部 shallow view、同一个 SQLite WAL 读事务、query_only 防写、缓存记录和 job 页、分批预取引用 job、后验按 run 建索引；复用原 delivery/outcome 状态机。请求结束显式清缓存。单日接口不变。仍有每请求 O(历史规模) 的 namespace 扫描，日期筛选未全部下推；不宣称完全有界。 |
| S08 启动扫描 | DEFERRED（整体）；缺失投影子项已验证 | `storage.missing_document_ids` 用 NOT EXISTS 查缺失关系，rebuild 不再逐条连接并解析已有 summary；最终仍一次流式校验其 payload 哈希；每个投影仍原子保存。测试新库、1001 条跨批、重复启动、中断回滚、旧时间插入、缺失 header。未加 migrated 标志或时间游标。SQLite 仍扫描元数据索引；`_init_daily_research` 的历史终态任务恢复未改；已有 header 但局部页损坏的完整修复仍缺校验协议，不能把本轮称为整个启动恢复完成。 |
| S09 投影去重 | DEFERRED | 实测旧格式 20/200/2000 行的页面、header、SQLite logical/WAL 大小。all 与 unselected 可重复，但缺规范格式迁移、旧读者回滚及部分损坏验收；未修改格式或迁移任何库。 |
| S10 历史材料 | RETAINED_WITH_REASON | `build_r14_reports.py` 仍按根文件名读取/写出成对报告；保留 R14/R15、DATA_READINESS、BEFORE_AFTER、profiling 和工作区材料。空 PRE_R14 patch 为既有历史配对材料，空 __init__ 属包边界；未盲删。README/综述增加当前工程入口，未改写旧结论，也未把仓内归档当字节节约。 |
| S11 测试边界 | DONE_VERIFIED（相关范围） | 共用 delivery 构造器从 test 模块提取到 `tests/support/daily_delivery.py`，旧模块重导出；原 test node 保持。`tests/README.md` 列核心、兼容、第三方、可选集成入口。依赖 pin 测试改为递归读取 -r，保留所有版本/排除断言。未删测试、扩大 skip 或改默认套件。其他共享 helpers 不做无关重排。 |
| S12 身份覆盖 | DONE_VERIFIED（合成） | helper 留在已被 protocol_source_hash 覆盖的 candidate_service.py 内；未移动到哈希名单之外。当前公式、config/spec/source-lock、评分实现与许可证指纹保持。epoch 自然变化，旧冻结 epoch 仍按冻结协议可读。详见身份段。 |

以上 DONE_VERIFIED 只限表中范围。没有真实投资、自然 20 会话、Windows 或容器验证结论。

## 行为、身份与恢复证据

`builders-before.json`/`builders-after.json` 为相同 TEST_FIXTURE（1 证券、260 会话、固定生成时钟）的 Full/Fast 原始输出。`builders-diff.json` 精确列出每个变化叶子；白名单仅为顶层/prospective_validation 的 validation_protocol_hash、validation_epoch_id，顶层 run_id、content_hash，以及 derived_feature_store 两处 timing_seconds 的数值叶子。业务 items、决策、理由、排序、readiness、截止与数据质量均保持。另实际从 ba54d6c 的源码副本生成 builders-initial.json，再与最终 builder 对照，只有同样 30 个白名单身份/耗时叶子变化（builders-initial-diff.json），完整起始版本也通过该对照。哈希重新计算匹配每份原始输出，旧 frozen identity 使用现代码读取仍返回旧 epoch；不将新输出写回旧 golden。

初始当前 epoch：`validation_epoch_24a489fda0b16cc2f0ea1f3896937e2b8819018ef5f62d8f10aef88b1e320a24`。
最终当前 epoch：`validation_epoch_942357756fcb5dc0cdcc5a105c776095111d779f1c02ab90b2234b0187857c14`。
只有 `validation_protocol_hash` 与 `validation_epoch_id` 两项 current identity 改变；signal/parent source、config/spec、score identity 保持。S08 与 S06 改动 candidate_service 文件导致协议源码 hash 改变，派生 run ID/content hash 随之变化。S06 单批 golden 的旧 epoch 是 S08 后的中间 epoch，原始值与差异保存在机器证据，不冒充全任务起点。`identity-and-equivalence.json` 记录完整身份。

观察回归覆盖 READY 推荐/合法空、无记录/缺 snapshot、NOT_READY/FAILED/MISSED、PLANNED/RUNNING/QUEUED、历史截止、未来/无时区错误、日历不足、未成熟/待捕获/停牌/异常后验、版本修订与原数据不变。新增真实 SQLite JobService 的 READY 对照，避免只靠内存任务替身；并发写入在旧读事务不可见，新请求可见，写操作被拒绝。观察、报告均不补写历史 snapshot。

投影恢复以 header 作为既有完成证据，缺失查询没有时间水位，旧时间新增/相同时间不会永久跳过。故障注入只在 TEST_FIXTURE 临时库中恢复原 trigger 后检查，不修改生产不可变断言。启动仍流式检查已有 header 的原始字节哈希和顶层 object 类型，发现损坏仍拒绝启动；省去每个 header 的单独事务与 JSON 对象展开。页级完整修复仍未实现，未声称跳过了历史 header 的字节读取。

## 指标

SYNTHETIC_TEST，macOS x86_64、CPython 3.12.14，同一业务解释器、同构临时 SQLite、20 日窗口、每规模重复 3 次。基准记录是合成 NOT_READY attempts 与已完成 summary headers，不是真实历史，也不是 FOCUS 大后验的生产基准。查询数/解析次数可直接比较；耗时受同机测试/原有实例负载影响，仅记录观察中位数，不作为稳定 SLA 或生产倍速。Python tracemalloc peak 不等于 RSS。

| 历史条数 | 观察 SELECT 前→后 | 已校验文档解析行前→后 | 观察秒数中位前→后 | Python peak bytes 前→后 | 已投影启动 SELECT 前→后 |
|---|---|---|---|---|---|
| 20 | 131 → 8 | 400 → 20 | 0.404 → 0.015 | 81431 → 101239 | 22 → 4 |
| 200 | 257 → 8 | 4000 → 200 | 1.087 → 0.058 | 465852 → 408977 | 202 → 4 |
| 2000 | 1539 → 13 | 40000 → 2000 | 9.608 → 0.551 | 3739688 → 3665956 | 2004 → 4 |

前后 9 个完整 observation JSON（含 Markdown）逐字段相等。最终版本与 ba54d6c 在同一个 minimal-env 解释器中依次串行执行，原始结果为 `benchmark-final-before.json` / `benchmark-final-after.json`，`benchmark-final-equivalence.json` 记录 9 份输出完全相等。每请求全 namespace 缓存及全后验读取仍 O(N) 空间/读取；状态机对各日筛选仍有 O(日数×历史记录) 的 Python 遍历，不将减少解析误称全流程 O(1)。启动总 SELECT 为 4 条（两种缺失关系、两种完整性扫描）；2000 个已有 header 的哈希读取前后均为 102890 bytes，Python 文档展开从 2000 次降为 0，不等于跳过历史字节或索引扫描。

依赖：日常直接入口展开 12 项，pip 干净解析得到 44 包；加 review 测试工具后环境实际含 pip 共 56 包，`du -sh` 为 296M。原环境 302M 含不同工具/包集合，**不作可比缩减收益**。旧核心 15 项直接 pin 完全保留；默认 Docker 安装范围未缩减，镜像前后体积 NOT_VERIFIED。完整 Qlib 解析尝试失败：pyqlib→mlflow→cryptography 50.0.2 的 metadata build 使用 Cargo.lock v4，本机 Cargo 不识别（退出 1，full-core-resolution.log）；未改 pins 或升级系统工具链来掩盖此缺口。Windows 3.12/PowerShell 与完整原生模型运行 NOT_VERIFIED。另在同一干净日常环境、新空 TEST_FIXTURE DB 下，3 次子进程 ApplicationService import+启动的观察中位为 0.847→0.801 秒，平台 maxrss 中位为 34,103,296→34,136,064 bytes，前后可选重依赖 import 集均为空；只说明轻量启动本来就没有载入原生研究包，不宣称初始化优化。startup.json 记录原值。

旧投影合成测量（无迁移）：20/200/2000 行时 summary page payload 为 89,474 / 888,768 / 8,883,370 bytes，单份 scalar list 为 44,361 / 443,601 / 4,436,001 bytes；SQLite logical（含 schema、索引、header）为 196,608 / 995,328 / 9,027,584 bytes，三组 freelist/WAL 均 0。不能由这些合成页面重复推出生产 DB 减半。详情压缩和真实运行记录不在此样本中，详见 `projection-size.json`。

初始受控源码分类：业务/构建 361 文件 8,680,570 bytes；文档 90 / 755,530；Qlib 619 / 8,123,493；MiroFish 39 / 1,139,460；测试 139 / 5,365,359。分类以目录优先（第三方内文档/测试归第三方），不是运行盘占用。无删除文件、无历史材料移动、Git 历史不变；公共 helper 为内容提取而非文件删除，代码/测试总字节增加；这是依赖入口与执行成本改造，未宣称仓库字节缩减。最终精确工作树统计保存在 `final-inventory.json`（包含主报告的最终字节数）。

## 安装、验证与回退

从仓库根建立隔离环境（示例路径均相对根，不覆盖已有 `.venv`）：

```sh
python3.12 -m venv review/slimming/minimal-env
review/slimming/minimal-env/bin/python -m pip install -r stock-fish/requirements-daily.txt -r stock-fish/requirements-review.txt
review/slimming/minimal-env/bin/python -m pip check
review/slimming/minimal-env/bin/python stock-fish/scripts/run_core_tests.py tests/r05_product/test_daily_observation.py tests/r05_product/test_slimming_storage.py tests/r05_product/test_runtime_resources.py -q
```

Windows 相应解释器为 `review/slimming/minimal-env/Scripts/python.exe`，本轮未在 Windows 执行。既有 Windows 完整安装继续使用根 `requirements-windows.lock.txt`；完整核心/Qlib 使用原 `requirements-core.txt`，旧集成/开发使用原 `requirements-dev.txt`。新增 requirements-qlib.txt 是原版本组，不是新锁或新模型许可。可选功能未安装时现有 health 显示 unavailable；原生模型推理未在最小环境假装通过。

全部业务套件入口仍 `python stock-fish/scripts/run_core_tests.py tests -q`；裸 `--collect-only` 会收集 MiroFish，不能与明确 `tests` 的数字混比。实际命令/日志和退出码见验收更新。

原始变更保护：8 个既有文件均先按指纹提交；本次在已提交综述上添加本轮入口，并为 critical capture 的“启用 fast decision”边界测试显式设置 fast_decision=True，避免继承操作者的禁用环境变量；禁用模式测试和原断言均保留。其余既有变更未修改。自有变更回退不使用整仓 reset/clean；交付时生成 `review/slimming/slimming-only.patch`（包括本次新增文件），先 `git apply -R --check review/slimming/slimming-only.patch`，确认当前仍匹配再 `git apply -R review/slimming/slimming-only.patch`。这保留 ba54d6c 的用户原修改；后续若有其他编辑导致检查失败应逐文件合并，不能强制覆盖。没有数据库 schema/投影格式迁移需回退。

## 当前剩余限制

Docker/Windows 无本机执行条件；Qlib 原生模型/完整集成依赖和第三方测试未验证。启动失败任务历史扫描、已有 header 的页级修复、日期过滤完全下推、原生完整环境隔离验收、第三方源码可重建外置、投影格式迁移都未完成，不计入收益。本次只交付源码工作区；瘦身已获后续本地提交授权，将与初始 8 文件提交分开交付；不推送、不部署、不迁移生产库。最终提交号见最终回复及 Git 历史。

## 最终验收记录

以下均为 ACTUAL_EXECUTION / TEST_FIXTURE。`P=review/slimming/minimal-env/bin/python` 表示本轮新建环境；除标注外命令从仓库根执行，脚本将测试根固定为业务目录。全部原始日志留在忽略目录，不提交运行产物。

| 检查 | 实际命令（P 为上文解释器） | 退出码与结果 |
|---|---|---|
| 初始用户改动 | `stock-fish/.venv/bin/python stock-fish/scripts/run_core_tests.py tests/r03_pipeline/test_request_queue.py tests/r05_product/test_codex_research.py tests/r05_product/test_daily_primary_critical_capture_boundary_v1.py -q` | 0；97 passed；随后提交 ba54d6c |
| 基线完整业务套件 | 在 `review/slimming/baseline-work/stock-fish` 执行 `../../minimal-env/bin/python scripts/run_core_tests.py tests -q` | 1；1387 passed / 12 failed / 1 skipped；full-core-before-clean.log |
| 改造阶段完整业务套件 | 在 `stock-fish` 执行 `../review/slimming/minimal-env/bin/python scripts/run_core_tests.py tests -q` | 1；1391 passed / 13 failed / 1 skipped；full-core-after.log；该批早于最终完整性测试和环境隔离修正，不冒称最终全量通过 |
| 最终存储、观察、运行资源 | `P stock-fish/scripts/run_core_tests.py tests/r05_product/test_slimming_storage.py tests/r05_product/test_daily_observation.py tests/r05_product/test_runtime_resources.py -q` | 0；40 passed；final-storage-query-resources.log |
| 最终 fast decision 环境隔离 | 前缀 `STOCKFISH_CAPTURE_FAST_DECISION=false`，执行 `P stock-fish/scripts/run_core_tests.py tests/r05_product/test_daily_primary_critical_capture_boundary_v1.py::test_fast_decision_waits_for_structure_history_before_comparable_not_ready tests/r05_product/test_daily_primary_critical_capture_boundary_v1.py::test_disabled_early_decision_preserves_final_readiness -q` | 0；3 passed；fast-mode-isolation-fixed.log |
| 最终 R14 串行复查 | 在 `review/slimming/final-work/stock-fish` 执行 `../../minimal-env/bin/python scripts/run_core_tests.py tests/r05_product/test_r14_daily_fast_path.py::test_evening_candidate_job_uses_daily_primary_builder -q` | 0；1 passed，9.30 秒；final-serial-r14.log |
| 最终测试收集 | `P stock-fish/scripts/run_core_tests.py tests --collect-only -q` | 0；1410，基线 1400；原节点无删除，增加资源 2、存储/一致读 7、真实任务观察 1；test-node-diff.json |
| 安装/CLI | `P -m pip check`；`P stock-fish/scripts/codex_research.py --help` | 各 0；minimal-check.log / cli-help.log |
| 串行合成对比 | 基线副本业务目录 `../../minimal-env/bin/python ../../benchmark.py ../../benchmark-final-before.json`；当前业务目录 `../review/slimming/minimal-env/bin/python ../review/slimming/benchmark.py ../review/slimming/benchmark-final-after.json` | 各 0；20/200/2000 条各重复 3 次，9 份观察输出完全相等 |

两次完整套件共有的 12 个失败节点见 `full-failure-comparison.json`：pyqlib 元数据缺失 1；legacy 路径缺 tenacity 3；旧 fake JobService.register 不接收 on_terminal 2；reliability D/E 等待超时 2；已有 Markdown 未包含 MARKET_INPUT_MISSING 1；R14 10 秒等待 1；macOS psutil.Process.io_counters 不可用 1；scheduler 预期 105 实得 0 为 1。最小依赖入口不是完整研究/集成验收环境，这些失败不能当作通过。递归 requirements 修改后一度暴露的 pin 文件解析 ValueError 已修复，恢复为缺 pyqlib 的明确环境失败。

改造阶段多出的 fast_decision 失败，在 **ba54d6c 原代码** 配置 `STOCKFISH_CAPTURE_FAST_DECISION=false` 时复现（1 failed，baseline-fast-flag-false.log），原因是测试没有声明它要测试启用模式；现在只为该 fixture 显式传 true，false 模式两例仍通过。最终隔离副本的扩大受影响检查为 153 passed / 2 failed（final-isolated-affected.log）：分别为上述已有 Markdown 断言和 R14 超时；后者随后独立串行复查通过，未扩大超时或削弱断言。最终没有再次运行整个 1410 节点套件，因此本轮交付结论是相关改动已按上述范围验证，**不是完整套件全绿**。

原始命令索引为 `commands.json`；初始裸收集因 MiroFish flask_cors 和 review psutil 缺依赖出现 4 个 collection errors，与本表明确限定 tests 的业务收集范围不同。第三方/可选模型执行、Docker 构建、Windows 和真实行情验证仍 NOT_VERIFIED；没有修改默认套件排除项来消除失败。

最终源码清单（包含新增测试与本报告，不含忽略环境/证据）：1248 → 1258 文件，24,063,972 → 24,107,468 bytes。新增 10 文件、修改 12 文件，无删除/文件移动；final-inventory.json 保存每个最终文件 SHA256 与字节数。原 11 项配置/规格/来源锁/许可证指纹和核心 15 项直接 pin 不变；preservation.json 与 Git diff 可复核。依赖组为规则 10 直接项、日常 12 直接项（含规则，解析 44 包）、Qlib 5 直接项、完整核心仍 15 直接项；默认镜像大小 NOT_VERIFIED。

交付文件索引：依赖与资源为 Dockerfile、requirements-core/rules/daily/qlib.txt；生产源码为 storage.py、yaogu/candidate_service.py、daily_observation.py、daily_read_context.py、daily_research_service.py；测试为 contracts/test_data_security_boundary.py、r05_product 下 observation/delivery_boundaries/critical_capture/runtime_resources/slimming_storage、support/daily_delivery.py 和包入口；文档为根 README、PROJECT_OVERVIEW、本报告与 tests/README。具体修改均包含于单独瘦身提交。参考树、历史报告、许可证、模型和既有运行文件保留原因见 S04/S05/S10；没有源码删除收益，没有生产部署收益。
