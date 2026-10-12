# ORIGINAL_PARENT_HISTORY_FIELD_DERIVATION_V2

本版本是独立、显式、单次研究入口，来源版本 `ORIGINAL_PARENT_HISTORY_FIELD_DERIVATION_V2`，特征版本 `LIVE_HISTORY_AND_CALENDAR_SCOPE_FIELD_BINDING_V2`，归档 schema 为 `ORIGINAL_PARENT_HISTORY_FIELD_R2_RESEARCH_ARCHIVE_V2`。初始参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`，未接默认入口或服务。冻结 [V1](ORIGINAL_PARENT_HISTORY_FIELD_DERIVATION_V1.md)、旧字段 V2、normalizer、history store、resolver、R1/R2 数值与旧 run 不改。

## 入口与身份

新模块的 `run_original_parent_history_field_r2(history_artifact, attempt_inventory, artifacts, observation_date, original_parent_as_of, registered_worker_deadline, *, requested_target_date=None, limits=None)` 保持 V1 参数契约。只接真实整件路径、预登记 SHA/typed digest、真实队列库存身份和原三件定位；不接 bundle、reader、owner、callback、价格 override 或能力 JSON。`OriginalHistoryArtifact`、`OriginalAttemptInventory` 是本版本的 exact 类型定位对象，声明本身不授能力。

`source_identity()` 保留冻结 V1 的完整实现身份及其 53 份依赖参考，再绑定本版本源码与规格。新来源身份、未修改的原 R1/R2 数值身份分别保存，不宣称二者相同。源、实现、PID、exact cutoff、input、reader、registry、字段与审计仍由唯一真实工厂封印；JSON、copy/deepcopy、token 重建、同工厂重用和内容/来源/代码变化拒绝。

## 日历声明范围修复

真实整件 SHA 和原 typed input digest 仍先验真，官方 readonly history owner仍核完整分区 index，并对最终真实25会话执行原 `iter_rows` 的 payload SHA、typed row、IID/日期/PIT 和原 receipt 验证。日历修复不能替代这些验收。

base 与 fresh 各须有且只有一份无冲突、COMPLETE、分页完整、四时钟在原截止内可见的 Baostock calendar receipt；发布日期及业务事实不得晚于原 first_seen。参数必须是实际 start/end，原 row_count 须等完整自然日范围，声明交易轴须在该范围内并以本对象的 observation 结尾。fresh 仍由未改的真实 normalizer 从原 RAW/physical receipt/result 的完整自然日旗标发行；缺行、重复、范围缺口或开闭冲突仍按原契约拒绝。

base observation 必须早于 fresh observation。共同覆盖内（截至旧 observation）的两个 typed 开放轴必须逐日 exact 一致，**即使冲突日在研究窗外也拒绝**。fresh 最后25个真实会话必须足25且以 observation 结束，不压缩缺日或补邻日。旧轴多出的日期只允许在 fresh 原 request start 之前，且全在25会话研究窗之外；旧右端、共同范围漏日、窗内 extra/missing、无共同覆盖、PARTIAL、分页未知和晚到时钟拒绝。

仅给 original history resolver view 使用两个原 typed 轴的 union，以容纳既有完整 index；官方 history base 验证仍使用未改的原 base。最终 native R2 输入保持 fresh 真实研究轴，旧 prefix 不加入最终 input，不延长目标日历。requested target 未被 fresh 原日历覆盖时仍为 UNKNOWN，不以 union 证明目标交易日。

审计 `history_window.calendar_axis_scope` 保存 base/fresh 原 receipt、receipt digest、参数范围、typed 轴摘要/hash、共同覆盖、旧/新差集与实际25会话。角色为 `TYPED_CALENDAR_AXIS_SCOPE_NOT_TWO_RAW_FLAG_AUTHENTICATION`：冻结 base 的声明约束不发行其旧 RAW 权限，未声称两份原 RAW 的开闭旗标已验证，也不能把 union、归档 JSON 或声明 hash 当来源能力。

## 原字段、时钟和风险边界

全真实 request/attempt 原库存、成功 trios、失败和 PARTIAL 祖先仍完整认证，未产出结果的状态不造 wrapper。fresh 只运行一次未改 normalizer 和一次原字段 projection，数值由自足 COMPLETE 父及全部 attempt oracle 另证。没有真 RAW trio 的历史 mixed PARTIAL 行继续 UNKNOWN，不能删 refs 或放宽 require_complete。可比价格仍要求同 basis、全部 attempt无冲突及真实 factor exact乘积；不可比时不回退 RAW。限价、历史资格及成员不由日历兼容性提升。

原父四时钟保持实际捕获；新派生 seen/ingested 是今天，不回填旧截止。原 history ref/hash只作来源封印，派生行仍是独立 inline输入。唯一同进程 readonly owner保持 transaction到一次未改 R2及终态验真；终验失败不算来源或策略成功。历史PIT0、完整独立周期0，工程通过不证明当时发出预测、预测有效或可成交收益。

## 资源与验证

资源语义沿冻结 V1：`DerivationLimits.total_source_bytes`只限制唯一 capture 文件大小，非实际累计IO；normalizer预算与本次绝对 worker deadline分开。官方 history payload累计计量固定2GiB，内部 `BudgetHistory.memory_budget_bytes=1GiB`保持，不等于外层OS/process-tree额度。registry终读、源码hash等另有读取，SQLite页及磁盘物理IO无真实计量时为 None，不能估成已测。

真实全市场任务须独立注册 wall/实际累计读取/RSS/输出预算，不继承旧失败作业额度。工程仅使用独立 TEST_FIXTURE三件、history DB与真实未改R2消费者；正例证明窗外旧 prefix可兼容且原 V1仍拒绝，反例保持共同轴、缺窗、时钟、范围、owner/原件/输入/审计及 once保护。没有运行真实全市场、采集、部署或修改旧 run。
