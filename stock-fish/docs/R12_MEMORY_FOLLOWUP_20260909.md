# R12 内存回归补充：2026-09-09

本文记录 9 月 9 日盘前补采、真实计算失败及其后的等价实现修复。**修复后的真实作业已成功落库，两种策略的分页摘要与 CSV 核验完成；最终核心检查 984 passed、1 skipped。** 这份输入的 V2 仍为 `SIGNAL_DATA_UNAVAILABLE`，不是可计数正式信号；真实端到端内存峰值也未下降。工程完成、数据质量与投资结论分别报告。

## 1. 补采与输入边界

用户要求补齐 9 月 8 日数据并生成 9 月 9 日盘前名单。使用原已登记 BaoStock 来源进行了两次有界补采，共 **4 个 adapter tasks、12 次 SDK operations**，分别取得 9 月 8 日与 9 月 4 日的 `master_all`、`market_daily`。四项均在各自第一次实际尝试取得 `COMPLETE` 且分页完整；原队列的失败尝试与旧回执保留。

| 业务日期 | 来源 | 原始行数 | 实际 first_seen，Asia/Shanghai |
|---|---|---:|---|
| 2026-09-08 | master_all | 7,369 | 2026-09-09 08:47:05.099006 |
| 2026-09-08 | market_daily | 5,216 | 2026-09-09 08:47:14.893049 |
| 2026-09-04 | master_all | 7,376 | 2026-09-09 08:56:29.106869 |
| 2026-09-04 | market_daily | 5,215 | 2026-09-09 08:56:41.026622 |

最终输入通过既有管理员入口登记至同一权威数据库：

- 观察日 `2026-09-08`，目标交易日 `2026-09-09`，文件 **42,538,681 bytes**。
- 输入 ID：`market_37236efb54f9c6760aea0ade645875485044011ffbe324c9daf1ad7eedfb3d01`。
- 历史引用：`history_7bd46c02d29800fb00843120cb9c84bda62395038e831445bde52a6d03bd1f14`。
- 近期行情 **27,595 行**，覆盖 9/1、9/2、9/3、9/4、9/7、9/8；没有 9/9 行情混入。
- 历史 manifest 共 **95 个会话分区**。与第一次 9/8 补采后的输入相比，原 94 个分区哈希全部保留，仅增加 9/4 分区的 4,599 条行情与 4,599 条证券证据。分区数不等于每只证券都有 95 个完整可比会话，更不证明 MA250 或所有策略依赖齐全。

输入覆盖类型为 `PROSPECTIVE_CAPTURED`，业务日期与实际取得时间分别保存，历史发布时间证明为空。这里表示今早实际取得并留档的证据；不能把它倒填为 9/8 晚上已经取得。9/8 原始 primary 当时缺日期化身份与当日行情，仍属不可评价；其输入、运行与评价历史不改写，也不因今早补采而追认计数。

输入核验使用只读数据库连接：最终输入及 manifest 身份一致，旧回执不变，近期父来源引用可解析；新增分区描述符完整核验，并抽查 6 条新增行的内容哈希与父来源。未再次遍历全部旧历史 payload。

## 2. 实际失败，不能以合成峰值代替

资料 cutoff 为 `2026-09-09T09:00:03.253916+08:00` 的 V2 作业 `job_eff8d4bd0df34f4ab27006af532360b6` 于 09:00 开始，约 24 分钟后在 **09:24:15** 以 `MemoryError` 失败，未发布成功结果。记录的进程峰值为：

| 实际进程测量 | bytes |
|---|---:|
| Peak RSS | 2,424,721,408 |
| Peak private memory | 3,426,811,904 |

RSS 是驻留物理内存，private memory 是进程私有内存指标，二者不能互换。此前 512×315 合成输入、旧文件迁移与真实 capture 的测量分别针对其他路径，不能证明这次全市场 V2 计算通过。

现存失败记录只有 `MemoryError` 类型，没有精确抛出位置。因此下面分别记录已证明的重复计算、临时内存放大及其定向修复，不将任一单项认定为整个故障的唯一根因。第4节记录真实重跑、持久化及已验证导出路径的结果与限制。

## 3. 保持策略语义的修复

### 市场聚合复用

阶段策略将同一实验 context 内反复计算的市场收益集合及基准结果缓存。键保留完整合格证券集合、窗口结束会话与回看长度；涉及覆盖判定的键仍绑定原覆盖门槛。缓存原精确数值与缺失结果，不扩大证券池，不把缺失当零，不跨输入或 cutoff 复用。

定向核验比较缓存与原始计算的完整输出，检查不同证券池、窗口及缺失情况。修复改变计算复用方式，未修改配置、交易公式、Setup、Focus gate、排序、来源/PIT 或覆盖阈值。

### 输入与有效依赖的流式哈希

实验结果末尾原来的 `digest(bundle)` 会先展开全部 Pydantic 行，再创建完整 canonical JSON 文本和 UTF-8 副本。现改为逐行序列化并更新 SHA-256；有效依赖的 JSON 哈希也使用迭代编码，保留相同的键排序、数值/时间/字符串表示和全部证据。

同一真实最终输入的新旧哈希均为 `37236efb54f9c6760aea0ade645875485044011ffbe324c9daf1ad7eedfb3d01`。这项变更不产生新的输入事实，也不改原历史内容寻址身份。

| 真实输入单次哈希测量 | 原完整展开 | 逐行流式 |
|---|---:|---:|
| tracemalloc 临时分配峰值，bytes | 285,035,334 | 111,743 |
| 无 tracing 的哈希耗时，秒 | 0.566 | 0.663 |
| 独立进程生命周期 peak RSS，bytes | 458,297,344 | 404,361,216 |

tracemalloc 在输入已经解析后，仅围绕哈希操作启停；它衡量 Python 可追踪分配，**不是进程 RSS**。独立进程 RSS 则包括先前的输入读取/解析，新路径的生命周期峰值仍受输入解析影响。两个指标的范围不同，不用临时分配的下降比例宣称整个作业同幅下降。

哈希与历史架构定向测试本次实际 **16 passed**，包括原式哈希严格一致、禁止全量 MarketInput/Universe dump、有效依赖内容变化仍改变哈希，以及既有不可变/PIT/历史读取回归。最终全核心检查另见第4节。

### 历史消费者的窄范围排查

使用真实输入只读构造 context 后，RSS 约 276.7 MB。对前 32 只合格证券查询三个窗口，连续三轮，并将测试进程的 L2 缓存临时限制为 32 KiB 以强制淘汰，RSS 约 279.9 MB 后保持稳定；`rejected_evidence=0`，已选择历史行哈希数保持稳定。

此探针未运行全市场策略，也未写数据库或修改生产缓存配置。它不支持“这份样本存在 rejected 列表泄漏”的判断，因此没有基于猜测改变 resolver、证据选择或历史缓存策略。它也不能代替全市场端到端测量。

### 完整结果的哈希峰值

服务构建结果及成功落库前的两处 `content_hash` 同样使用逐块 canonical JSON 哈希，保存内容、排序、状态及原哈希值保持一致。独立32只证券 `TEST_FIXTURE` 的完整结果为3,188,977 bytes，旧哈希追加 tracemalloc 峰值15,950,200 bytes，流式哈希为27,694 bytes，两者SHA256完全相同。无 tracing 的三次哈希耗时分别为0.0325–0.0338秒和0.1072–0.1092秒；这是较低临时内存与额外编码时间的取舍，不是完整市场作业的前后结果。

## 4. 工程检查、真实完成状态与证据位置

最终源码冻结后，工作目录 `stock-fish` 执行 `.venv/Scripts/python.exe -m pytest tests -q --tb=short --junitxml=<private-output>/full-core.xml`，**984 passed、0 failed、0 errors、1 skipped，pytest 227.21秒**。跳过项仍为Windows符号链接权限。证据为 `fix-tests-final/full-core.txt` 和XML；先前982项检查早于服务哈希两处修复，不冒充最终检查。服务哈希构建/保存及相关API回归另有53项通过，缓存与V2定向54项通过，历史定向16项通过。上述检查使用隔离测试数据，不代表投资验证。

### 真实计算与导出

修复后的官方 API 作业 `job_2b7dda9507654034ab8992bee60a11b6` 已 `SUCCEEDED`，测得运行 **1,414.672秒，约23分34秒**。保存运行 ID 为 `strategy_experiment_a4b33e34aebf0c167e5fc12332c5e2108efc582006a793f2da10753895770bf8`。

- 输入在 **08:58:00.615571** 冻结，资料 cutoff 为 **09:42:54.549595**，结果 `generated_at` 为 **10:05:09.064690**，成功持久化完成于 **10:06:28.578864**，均为 2026-09-09、Asia/Shanghai。
- 观察日仍是 9/8，输入没有 9/9 行情。这是盘前冻结资料、盘中完成的计算，不宣称名单在开盘前已生成。
- V2 处理全部 **3,041** 只合格证券：`FOCUS=0`、`WATCH=0`、`REJECTED=2,851`、`UNKNOWN=190`。质量为 `SIGNAL_DATA_UNAVAILABLE`，原因是 `CORE_STRATEGY_EVIDENCE_UNAVAILABLE` 与 `CURRENT_COMPARABLE_PRICE_UNAVAILABLE`；零 FOCUS 不能作为数据齐全的合法无推荐。
- PV 有 **284/3,041，约9.339%** 的证券具备所需可评估字段，状态为 `LIMITED_SCOPE`；保存原排序的 **50** 条 PV 结果。`PV_FOCUS_PREVIEW_V0.1` 有 **1** 条预览：肯特催化（603120.SH）。它是有限覆盖的 PV 预览，不是 V2 FOCUS。

通过官方分页 summary 读取全部 3,041 条 V2 结果，并取得 V2 全量 CSV、PV 50条 CSV；核对同一 run/source hash、证券身份、数量、状态与原顺序一致。**没有请求完整 JSON 的 deepcopy 导出路径，该剩余路径未验证。** 分页摘要汇总 JSON 不冒充完整 JSON 导出。

### 端到端峰值仍高

| 真实路径 | 原失败作业 | 修复后完成作业 |
|---|---:|---:|
| Peak RSS，bytes | 2,424,721,408 | 2,646,999,040 |
| 进程生命周期 peak private memory，bytes | 3,426,811,904 | 3,427,647,488 |

修复后 RSS 峰值更高，private 峰值与原先接近；不能声称端到端峰值下降。流式哈希确实消除了单次哈希的大量临时分配，但存储 `canonical_json` 仍会构造大 JSON，持久化阶段仍有大峰值。**本次证明这份真实输入完成了计算、落库和上述导出，未证明所有内存问题彻底解决或长期运行稳定。**

### 独立均线覆盖检查

使用同一输入、同一 09:42:54.549595 cutoff 和既有 `theme_capital_trend._ma_value`，独立只读核验全部 **3,041** 只证券。每只历史仅加载一次供四窗口共用，耗时 **74.865秒**，观察到的最高 RSS 为 **347,750,400 bytes**。MA20、MA60、MA120、MA250 可用数均为 **0/3,041**；原消费者均返回 `PRICE_WINDOW_INCOMPLETE`，未用历史行数替代连续会话、可比基准或四时钟校验。

MA20 所需窗口中，全市场缺 **2026-08-12、08-24、08-27、08-28** 四个日期的价格行；这已足以造成该窗口不完整。MA60、MA120、MA250 窗口内全市场无价格行的日期数分别为32、44、162。该检查是数据覆盖探针，不是策略重算、官方信号或完整投资评价。

### 前瞻周期与服务状态

本次运行角色为 `MANUAL_OR_REPLAY`，已持久化但不可计数为正式 primary。当前 epoch 为 `validation_epoch_690396a86e98955c3b6aae3ce20f694837dbe16b8d70bb36a0f62755dca52a4e`，核验时正式尝试日数 **0**、可评价正式信号日数 **0**；旧 9/8 primary 的不可评价质量与原周期记录保留。

2026-09-09 **10:07** 核验服务端口8001、PID40944，scheduler线程存活，今晚 **20:30** 的正式采集计划存在，目标交易日为9/10。它是带时间戳的当前计划，不是提前声明今晚已完成。`MARKET_EDGE=NOT_ESTABLISHED`。

本机私有证据位于仓库根目录相对路径 `reports/codex/2026-09-09/premarket/`，不随源码分发：

- `core-refresh/report.json`、`core-refresh-2026-09-04/report.json`：四项补采的预算、实际回执与 SDK 计数。
- `assembled-final/assembly-manifest.json`、`import-receipt-final.json`、`input-audit-final.json`：最终输入身份、登记及有界只读核验。
- `strategy-final-submission.json`、`strategy-complete-final.json`：09:00 作业提交及真实失败状态。
- `final-evidence-summary.json`、`retry-memory-fix/completion.json`：修复后真实成功作业、生成/持久化时钟、质量、进程内存及服务状态。
- `verified-list/manifest.json`、`pv-reference/manifest.json`：官方分页摘要、V2/PV CSV 的来源哈希、顺序和数量核验。
- `MA-coverage.json`：独立全合格池均线覆盖及具体会话缺口。
- `memory-regression/history-real-before.json`：32 只证券的只读历史探针。
- `memory-regression/history-input-digest-before.json`、`history-input-digest-after.json`、`history-input-digest-tracemalloc.json`、`history-memory-summary.json`：哈希操作范围、精确身份及内存测量。

相关背景：[R12 历史架构](R12_HISTORY_ARTIFACT_REPORT.md)、[各路径内存测量](MEMORY_PROFILE.md)、[项目综述](PROJECT_OVERVIEW.md)、[前瞻计数协议](V2_PROSPECTIVE_VALIDATION_PROTOCOL.md)。
