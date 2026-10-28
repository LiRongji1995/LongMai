# 盘前环境重新认证与缓存元数据恢复 V1

本协议用于已经明确授权的环境恢复及真实盘前补采。它新增运行认证、缓存元数据 epoch 和专用恢复入口，保留旧登记、旧 default、旧请求记录、原 RAW、原策略及已有 run。普通查询和下一次日常研究不会因此自动获得扩容、重采或重新登记权限。

截至 2026-10-28：原 58 包 daily/review 环境的七步检查通过；137 份旧 capture 通过原验证器重新认证，生成新元数据引用，继承 132 个原 attempt 目录的防重标记；发布目录最终六组定向测试 177 项通过。26→27 的完整复权与核心已经执行成功，但全流程超时；28→29 在复权采集中失败。两轮均未形成完整盘前清单，真实提速比例仍未测得，具体事实与恢复边界见下文。

相关入口：[日常研究协议](CODEX_DAILY_RESEARCH.md)、[完整盘前执行](PREMARKET_SHARED_EXECUTION_V1.md)、[共享聊天运行登记](CODEX_CHAT_WORKSPACE_V1.md)、[原环境合同](ISOLATED_DAILY_RUNTIME_V1.md)。

## 新增脚本的分工

| 脚本 | 职责 | 不代表的事实 |
|---|---|---|
| [`codex_runtime_recovery_v1.py`](../scripts/codex_runtime_recovery_v1.py) | 重新验收原解释器；只读恢复计划；追加新运行登记；跨旧、新 scope 防重与互斥 | 旧物理身份恢复、数据已齐备、服务已切换 |
| [`codex_capture_epoch_v1.py`](../scripts/codex_capture_epoch_v1.py) | 全文重验原三件套，在新缓存目录追加元数据引用及旧 attempt 标记 | 新下载、历史时钟补录、完整复权已经补齐 |
| [`codex_recovered_premarket_v1.py`](../scripts/codex_recovered_premarket_v1.py) | 使用显式恢复登记及有限预算，调用原完整盘前采集和计算流程 | 普通入口自动扩容、失败请求自动重试 |
| [`benchmark_real_premarket_v1.py`](../scripts/benchmark_real_premarket_v1.py) | 完整真实缓存就绪后，以独立新进程比较原、新 worker | 在线采集或完整聊天交付提速、投资有效性 |
| [`benchmark_real_premarket_v2.py`](../scripts/benchmark_real_premarket_v2.py) | 在完整成功封存的当日 original 作业后，比较保留相同来源未知的离线完整 worker | 来源 PARTIAL 变 COMPLETE、失败作业可以准入、已验证网关交付 |

旧 `stockfish_core` 动态认证集合及已有入口保持原字节。新实现放在 `scripts/`，有自己的执行身份。发布源码与实际运行实例分别验收。

## 环境认证与登记边界

本次原登记的六处路径及 inode 保持一致，设备号发生变化。旧登记没有足够卷身份材料证明历史物理连续性，因此不能把新设备号写回旧记录，或忽略物理字段继续使用旧认证。

`verify_environment(python, destination, report_dir, source_root)` 复用原 `bootstrap_daily_runtime.py` 验证器，对原虚拟环境执行：原生平台身份、`pip check`、安装分发与原 58 包锁的对应、rules/daily/review 三个 doctor、独立 `TEST_FIXTURE` 应用/CLI/SQLite 烟测。没有重新安装依赖。各 Python 子进程及 doctor、烟测内部的 Python 子进程都使用全新的未存在字节码查找路径，保留 `-I -B`；原脚本不改写。

验收记录固定解释器目标、相关脚本和锁文件的完整 SHA 与七字段物理身份，保存实际调用、退出码、开始/结束时间和子步骤。七字段为 `st_dev / st_ino / st_mode / st_size / st_mtime_ns / st_ctime_ns / st_flags`。同内容不同 inode、验收期间来源变动、失败步骤和测试替身不能作为生产验收通过。

新配置的 state 位于原 state 的 `epochs/<明确新名称>/`；报告根、解释器、源码路径及存储根沿用原位置。新私有配置单独保存，旧配置不覆盖。恢复计划明确列出旧、新存储政策与 worker limits；本轮有限扩容由用户的本次授权覆盖，不是以后自动扩容的依据。

`plan_recovery(...)` 是只读计划；`apply_recovery(...)` 在登记锁和旧、新物理资源联合锁内追加 epoch 回执及新 runtime。相同当前物理资源的报告或容量政策冲突仍拒绝。新 schema 使旧 loader 关闭此路径；必须显式调用 `recovery.load_runtime(new_name, registry_root)`。旧 default 不改，恢复不等于默认入口或后台服务切换。

恢复回执钉住旧登记、旧配置、default 及祖先操作的完整 SHA/七字段身份。`RecoveryRuntime` 在动作前及结束时复验，扫描祖先 scope；相同 request-key 或相同盘前观察日/目标日的已完成、失败及未知请求，都不能借新 epoch 重提。当前新 scope 内仍保留原 `codex_daily_v2._prior_request` 检查。换代码、配置、request-key 或 engine 都不是恢复失败请求的授权。

本版本只处理从原 V1 登记创建首个恢复 epoch。再次恢复已恢复 epoch 需要显式的多代继承协议，目前会拒绝；不静默丢弃祖先记录。

## 缓存元数据 epoch 如何重认

`codex_capture_epoch_v1.py plan` 持有原缓存 writer 锁，校验全部旧索引的内容地址、请求身份、原引用路径与完整 SHA，并记录当前文件树及物理身份。规划不联网、不改旧索引，也不把索引存在当作来源成功。

`create` 必须提交相同计划 SHA，且目标是尚不存在的新目录。它重新读取计划，随后将当前全文认证的原 RAW、result、receipt、producer 引用交给原 `CaptureCache.import_seed`。原验证器继续检查 RAW 与 result 行一致、receipt 前像、原始首见和完成时间顺序、证券及业务日期、来源代码哈希、完整分页与复权逐证券状态。旧失败、PARTIAL 和 UNKNOWN 保持原状态。

新目录只增加元数据、seed 引用及控制文件，不复制整套 RAW，不制造 result wrapper。旧非空 attempt 目录全部继承为新 epoch 的防重 claim；没有完整 result 的请求不能因此重新发起。原导入器可能推导出不同的 calendar 请求键，回执保存旧、新键映射，不能人为改写旧业务身份来制造相同请求。

完成前重新全文检查原件、producer、旧索引和原验证器登记的文件，并核对文件树未变。新引用记录的是本次真实认证后的物理身份；旧 RAW 的 `first_seen_at / ingested_at / started_at / completed_at` 不补写、不回拨。`historical_physical_continuity_restored`、`source_authority_restored`、`new_market_data`、`source_clocks_rewritten` 均保持 false。后续新计算仍按原来源合同重新认证和发行本次调用内能力。

`epoch-complete.json` 不能单独作为成功凭据：必须同时有 `create_epoch` 成功返回、实际进程退出码 0、完整末验及本次预算通过。最终发布实现将末次预算检查和原缓存锁退出纳入失败处理；这两个阶段异常时，会把本次候选完成文件移至 `epoch-rejected-completion.json`，撤销成功完成路径，并记录 `epoch-failed.json`、`FAILED_RETAINED_NO_IMPLICIT_RETRY` 和 `completion_accepted=false`。被撤销的原候选字节保留供审计，不能作为成功回执；失败目录保留，不能换目录自动再试。

## 显式使用流程

所有位置由私有配置或参数提供，以下变量是工程占位符。使用原登记解释器和已核对的发布源码，字节码路径必须全新且未存在；不在源码目录保存真实行情、登记表或验收包。

1. 记录原登记、原配置、旧 scope 操作和当前物理绑定，核对实际存储余量及本轮授权范围。
2. 调用 `verify_environment(...)`，将返回的文件引用作为后续恢复计划的 `environment_receipt`。报告目录须新建，七步全部成功后继续。
3. 准备本轮明确拥有的新 state 和私有配置；在容量准入及共享资源互斥下执行旧缓存 `plan`、`create`，核对返回、真实退出码和认证回执。
4. 调用 `plan_recovery(registry_root, old_name, new_profile, environment_receipt)`，审阅其中的政策差异；显式 `apply_recovery(plan)` 追加新登记。使用新 loader 验证登记，不修改旧 default。
5. 通过新恢复 runtime 调用原 `codex_premarket_preflight_v1.plan(runtime)` 记录真实来源缺口。这与普通 V2 只读计划使用相同规划器；恢复入口不会把未采集的图改称已认证或已经齐备。
6. 只有本轮已授权的补采使用下面的专用命令。它要求 `RecoveryRuntime`，在领取请求前和领取后的联合锁内完整认证原日历，并使用真实时钟核对观察日、目标日和上海自然日。原 collector 随后逐阶段认证、计算精确范围成本并准入。

```sh
"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$FRESH_ABSENT_PATH" \
  scripts/codex_recovered_premarket_v1.py \
  --runtime "$RECOVERED_RUNTIME_NAME" \
  --registry-root "$ORIGINAL_REGISTRY_ROOT" \
  --profile REAL_PREMARKET_RECOVERY_3H_V1 \
  --observation-date "$OBSERVATION_DATE" \
  --target-session "$TARGET_SESSION" \
  --request-key "$ORIGINAL_REQUEST_KEY"
```

这个恢复入口固定 `engine=original`，不提供切换 engine 的重试途径。原父、子进程监管、完整来源和代码闭包、正式产物校验继续执行；请求失败或结果未知保留原作业，禁止换 key 重试。普通新盘前入口仍遵守 `codex_daily_v2.py premarket-plan` 的原准入规则及[完整执行协议](PREMARKET_SHARED_EXECUTION_V1.md)。

完整交付须校验真实成功退出码与 final/completion 一致、core 已认证、完整正式导出、题材及展示产物、原 run_id 和日期、来源末验、请求记账及父进程终验。采集成功、环境通过、health 正常或作业已启动都不能替代这些条件。`DAILY_V1` 可用性单列；初始参数仍“尚未验证”，`MARKET_EDGE=NOT_ESTABLISHED`。

## 本次有限预算

`REAL_PREMARKET_RECOVERY_3H_V1` 是本次明确授权的恢复档位上限。私有配置可更严格；不能超过此上限或把 `max_seconds` 设为无限。共享登记仍执行独立容量准入，不把逻辑上限称作文件系统硬配额。

| 项目 | 本次明确上限 |
|---|---:|
| 完整工作流原监管计时 | 10,800 秒（3 小时；使用单调时钟，休眠期间与真实经过时间可能不同） |
| 原请求 / SDK 调用 / HTTP 尝试 | 1,024 / 10,000 / 32,768 |
| 单请求 HTTP 尝试 / 请求超时 | 64 / 90 秒 |
| 当前 epoch 缓存 / 单作业输出 | 1 GiB / 768 MiB |
| capture / core / theme 返回读取字节 | 各 32 GiB |
| presentation / supervisor 返回读取字节 | 4 GiB / 8 GiB |
| 单进程 / 进程树 RSS | 2.5 GiB / 3 GiB |
| 私有登记管理根总逻辑量 / 预计新增量 | 6 GiB / 2 GiB |
| 必须保留磁盘余量 | 10 GiB |

父入口捕获和末验的读取分配继续取 capture 与 supervisor 配额的较小值；源码和请求 bookkeeping 的实际检查不因阶段切换重置整个工作流时钟。原配置、原失败记录和旧容量政策仍保留，未自动购买服务或切换来源。

完整复权要求仍由原 Baostock 来源、原价格基准、完整左端锚点与逐证券覆盖验证决定。缩小下载日期不能被当作完整复权；旧因子加新尾窗也不能未经新协议自动合成为完整来源。此档位增加本次允许的有限采集与验证资源，没有改变复权语义、证券池或策略阈值。

## 已完成验证

本轮实际恢复发生于 2026-10-26 UTC，业务认证时钟为 2026-10-27 Asia/Shanghai。

- 原生环境及原 58 包锁对应检查、`pip check`、三个 doctor 和独立应用/CLI/SQLite 烟测共七个外层步骤全部退出 0；另记录 40 个实际嵌套 Python 调用。环境验收状态 `ACCEPTED`，没有安装包或修改运行登记。
- 旧 capture 重认证 137 份，RAW-only recipe 0 份，继承 attempt 目录 132 个。新市场请求、SDK、HTTP 调用均为 0；组件记录 98.056 秒、返回读取 1,189,492,396 字节、峰值自身 RSS 84,762,624 字节。该耗时是一次性原件重认成本，不是新交易日生成或网络采集速度。这次真实导入使用此前执行版本，已确认成功返回、实际退出码 0 和完整末验；实际执行的源码字节另存私有审计，不用后续源码替换其执行身份。
- 新恢复登记已追加，旧 default 保持。真实原日历门禁核定观察日 `2026-10-26`、目标交易日 `2026-10-27`，核验时间为 `2026-10-27T03:31:08.103164+08:00`；此事实仅证明该次日历门禁，不证明市场或因子已齐备。
- 10 月 26 日发布检出中的四组独立 `TEST_FIXTURE` 检查为 **84 passed，pytest 52.61 秒，完整进程 55.481 秒，退出 0**。其中包含缓存 epoch 的 15 项测试及 V1 基准的 29 项测试，覆盖失效完成回执撤销、旧成功 epoch 保护、父回执末次时间/输出超限、预留不足及中断撤回。
- 10 月 28 日新增 V2 及独立审阅修复后，发布检出最终六组组合检查为 **177 passed，pytest 82.73 秒，完整进程 84.319 秒，退出 0**，含原四组 84 项、V2 73 项和完整工作流 20 项。不同轮次不累加；这不是全仓测试、真实清单完成或投资验证。实际测试命令如下。

实际测试以登记解释器、发布业务目录、全新字节码查找路径和独立 `TEST_FIXTURE` basetemp 执行：

```sh
"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$FRESH_ABSENT_PATH" \
  scripts/run_isolated_tests.py -q --basetemp "$NEW_TEST_FIXTURE_DIRECTORY" \
  tests/unit/test_codex_runtime_recovery_v1.py \
  tests/unit/test_codex_capture_epoch_v1.py \
  tests/unit/test_codex_recovered_premarket_v1.py \
  tests/unit/test_benchmark_real_premarket_v1.py \
  tests/unit/test_benchmark_real_premarket_v2.py \
  tests/yaogu/test_premarket_shared_workflow_v1.py
```

完整 argv、退出码、机器路径、配置、认证原件及日志保存在私有审计目录；源码文档只列可分享的命令结构与结果，不发布私人路径、数据库、缓存或全市场审阅包。

## 实际性能验收口径

首次联网补采、一次性恢复、首次完整计算、完整成果复用分别计时。真实清单完全通过后，才可执行独立的 cache-only worker 实验；原阶段失败或数值对照拒绝时，后续 engine 不运行，不把切换 engine 作为日常请求重试。

`benchmark_real_premarket_v1.py` 固定原 `offline=True / retry_failed=False`，不替换网络适配器，不复制来源，不回拨时钟，不扩预算。明确选择 `original-ref / original / shared` 后，每组使用独立新进程，保留各自真实 cutoff、run_id 和原正式文件。实验持有原盘前作业锁，不能与真实采集并发。

可比性要求包括完整来源引用、市场输入、入选证券、正式 CSV 单元格、核心数值/null/bool、题材值、展示数量及所有阶段。仅在确认四个 universe 根层组装时钟各自等于对应真实 cutoff 后，从对照投影排除这四个字段；CSV 对照排除各自 run_id。其他来源、证券、价格、题材时钟保留，不宣称正式导出逐字节相同。任何投影不同都拒绝计算提速比。

实验报告 worker 墙钟、CPU、阶段读取、RSS、输出量及独立对照开销；不包含网关准入、在线采集和普通 supervisor。单一运行顺序未控制操作系统热缓存偏差，即使结果可比，也只能报告该次 cache-only worker 差异，不能外推整个盘前清单交付速度。

两份父回执的完整发布大小、写后与重命名后检查、最后源码认证均受同一 arm 时限和输出额度约束。失败诊断事先在原额度内预留 16 KiB；尾部超限时保留 rejected 证据，两份正式父回执记 `FAILED`、清空比较证明并停止后续 engine。worker 自身完成不替代父回执末验。实验 summary 是单列的聚合元数据，不能把其落盘当作新一轮来源认证。

## 两次真实执行的结果

两次均使用显式恢复登记、同一原登记根及 `engine=original`，对应不同的真实交易日请求。没有以换 key、换 engine 或修改旧状态重试其中任何一次。

| 观察日 → 目标交易日 | 已完成部分 | 最终状态与未完成部分 |
|---|---|---|
| 2026-10-26 → 2026-10-27 | 64 批完整复权、3,194 只证券；原核心 `PASS`，全池 3,042、研究 WATCH 22、FOCUS 0 | `BUDGET_EXCEEDED`；题材尚未封存时达到 10,800 秒全流程限制，没有完整展示清单 |
| 2026-10-28 → 2026-10-29 | 16 批完整复权、800 只证券；两日行情与资格各两份，以及三份当前官方目录 | `FAILED`；最后一批超时 RAW 缺原始首见时间，认证拒绝，core/theme/presentation 均未运行 |

第一轮核心 `run_id=strategy_experiment_a042c7c6da308617484c85c6`，资料截止为 `2026-10-26T21:22:00.609875+00:00`。22 个 WATCH 的正式状态均为 UNKNOWN，不能作为已确认 FOCUS。该 run 仅对应原日期；本节没有将旧核心另行认证交付为 28→29 清单。按原日志与阶段产物时间估算，首次冷启动采集约 109 分钟、core 约 7 分钟、新闻及题材来源约 23 分钟，随后题材获得约 40 分钟执行时间；这些为阶段近似计时；题材日志虽然写出 479,457 行、13 分片，但没有最终题材父与展示产物。首次复权因子补齐不能算成引擎提速。

第一轮题材来源共有 84 个行业成员请求和 175 个概念成员请求；其中 10 个概念为 `PARTIAL`，原因为 `PAGINATION_OR_EMPTY_RESULT_NOT_VERIFIED`。这些来源缺口必须保留，不能用全工作流完成状态将其升级为完整。新闻为 `PARTIAL`、无已解析报道材料；失败和未知分别保留。

第二轮共 25 次新请求：23 个 COMPLETE、1 个已索引 PARTIAL、1 个认证失败未索引。已索引的部分复权批次覆盖 50 只证券，仅 12 只完成，38 只记录 `BAOSTOCK_ERROR_10002007`；最后一批 50 只证券留下 17 只完成检查点，但超时 RAW 缺 `first_seen_at`。原验证器返回 `ORIGINAL_RAW_FIRST_SEEN_REQUIRED_NOT_BACKFILLED` 是正确拒绝，不能以归档时 receipt 的时间补成原始首见钟。17 个检查点不是 17 份已认证完整来源。

第二轮进程开始、结束的实际 UTC 时间差为 **2,526.572 秒（42 分 6.6 秒）**，进程单调时钟记录 **1,637.395 秒（27 分 17.4 秒）**；不能将后者当作用户实际等待时间。系统电源记录明确使用 UTC（+0000）：16:24:11 合盖休眠，16:25:37—16:39:45 维护休眠，以及 16:45:54—16:46:17 再次休眠。这些时段与两批不完整请求重叠，但尚不能排除提供方独立错误，因此不把休眠说成唯一已证明的原因。

两轮均没有成功的完整盘前交付；真实同来源全流程性能基线仍缺失。`DAILY_V1=UNKNOWN_NOT_EXECUTED`，`MARKET_EDGE=NOT_ESTABLISHED`，没有新的投资有效性结论。原件、实际命令、退出码、失败控制文件、电源事件节选及精确身份均留在私有审计目录。

## 失败后的显式恢复边界

现有 `run_once` 会拒绝 `FAILED_OR_UNKNOWN`，新配置、版本或 engine 也受相同日期和原 request-key 防重检查。底层 `retry_failed=True` 不能替代上层恢复授权；不得直接调用 standalone 绕过原登记。

下一步恢复必须先有明确的一次恢复授权及新的恢复协议：绑定原 request-key、原 operation/job 与失败证据，保留旧 FAILED；完整认证并复用已有成功采集，只对两条不完整来源新增有限 attempt，并按实际当日范围准入尚未发出的请求。保持原解释器、来源、original 引擎及有限额度，重新核日历、容量和来源身份；失败后停止，不能循环重试。当前缺口总数仍需完整只读规划，不能用 26 日的 3,194 只推断 28 日范围。

运行时保持开盖；可为本次拥有的进程加临时防空闲休眠断言，进程退出后释放。它不能保证阻止合盖休眠，也不应通过改系统永久电源设置来代替这一条件。现有三份 producer 源码与旧 capture hash 绑定，不能直接修改旧文件，否则已完成缓存会失去认证；旧 RAW、receipt、checkpoint 的时间也不得回写。本轮没有自动实施这项恢复或改变电源设置。

## 保留来源未知的性能实验 V2

V1 要求采集、核心、题材来源、题材和展示五阶段 COMPLETE，原文件保持；离线新闻本就允许 UNKNOWN。V2 为不同的显式实验合同：必须先取得当日成功封存的 original 完整 worker，并由调用者另验网关的 `complete_daily_delivery`、真实退出码及 `RECORDED`。V2 自身不会把 worker 成功冒充网关交付认证；本次两个失败作业均不满足前置条件。

V2 保留 V1 的完整输入、选择、CSV、数值、67 会话及四个组装时钟守卫，额外比较完整目录、所有节点、来源引用、分页缺口和未知台账。题材成员来源与离线比较臂只允许原生分页证据缺失的 PARTIAL 作为相同未知保留；这些阶段的采集执行、网络超时、预算或 I/O 失败不能因此放行。成功封存的线上原作业可保留新闻 PARTIAL/UNKNOWN，包括未取得报道的 HTTP 失败，但不得有未处理的阶段执行失败。离线新闻须为原生 UNKNOWN，实际网络调用、文章和捕获均为零；线上新闻证据保留，但不把它与离线新闻声称等价。

每臂独立新进程，保留原三引擎和原阶段限额；每个实验产物最多 2 GiB。支持单臂及跨实验读取原件重新计算比较证明，不能只信摘要 hash。最终原件全文 SHA/七字段身份、父回执、日期、完整预算及所拥有进程回收均需通过。当前没有启动真实 V1/V2 benchmark，不能给出全市场、在线采集或端到端提速百分比。
