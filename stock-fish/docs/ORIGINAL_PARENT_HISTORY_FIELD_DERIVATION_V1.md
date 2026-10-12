# ORIGINAL_PARENT_HISTORY_FIELD_DERIVATION_V1

本版本是显式、单次研究入口，特征版本 `LIVE_HISTORY_AND_ORIGINAL_FIELD_BINDING_V1`，初始参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`，未接默认服务。旧字段 V2、normalizer、history store、resolver、R1/R2 公式和旧输入保持原样。

## 入口与实际来源

`run_original_parent_history_field_r2(history_artifact, attempt_inventory, artifacts, observation_date, original_parent_as_of, registered_worker_deadline, *, requested_target_date=None, limits=None)` 只接路径和预登记身份，不接外部 bundle、reader、owner、callback、价格 override 或已保存能力 JSON。

`OriginalHistoryArtifact` 必须提供原冻结整件 `input_path/input_sha256`、原 typed `input_digest/input_cutoff`、既有 `database_path/history_ref/history_hash`。原封装六键 `input/cutoff/parent_input/purpose/supplement/target_session` 原样读取，不重包装。整件 SHA 与原 typed identity 都必须成立。

`OriginalAttemptInventory` 提供原 `queue_root`、真实 request/attempt 数及 `inventory_hash`。哈希定义为真实只读 SQL 的两个排序列表：requests 的 `request_id,payload,state,attempts,result_path,result_hash,failure_category,updated_at` 按 request_id 排序；attempts 的 `request_id,attempt,state,result_path,result_hash,recorded_at` 按 request_id、attempt 排序。对 `{'requests':列表,'attempts':列表}` 使用 `digest_json_stream`。只读共享原队列锁与 mode=ro snapshot，非 immutable；有结果路径/hash 的全部 attempt 必须对应完整真实读取件，成功缺件拒绝；未产出结果的原状态保留，不造 FETCH_FAILED、EMPTY 或 wrapper。每份 result 的 queue_request_id 必须等实际 attempt 的 request_id，provider、endpoint 和归一参数必须等真实 requests.payload。

允许显式读取归档根之外的原件定位件，但必须核其登记 byteSHA 和原 SQL canonical result 身份；RAW/receipt仍由真实 archive_root鉴权。本入口不单凭 SQL canonical hash 声称重新验证了与当前不可读旧 wrapper 的整字节复制关系；实际恢复/复制证明和旧路径当前不可读状态应由真实执行外层另行绑定。重新排序 JSON 能保留内容身份，不能凭此获得未经原 RAW/receipt鉴权的权限。

## 同进程历史 owner

本厂由 `ReadOnlySQLiteStore` → 官方 `BudgetHistory` 创建唯一 live readonly owner。同已登记 job 只能发行一次，失败仍占用该发行记录。先全引用分区 index 验证，再对真实日历最后25会话的所有引用历史 bar 调官方 `iter_rows`，检查 payload SHA、typed row、成员 IID/日期。逐行用原 mixedrefs 的未改 resolver 验 IID/session/四时钟/PIT，继而通过原 `InstrumentBars` 选择实际 revision。原 history manifest 的全部 receipt 与 fresh receipt 按 ID exact union；冲突拒绝，原失败与 PARTIAL 保留。

manifest 内部也先逐项 typed 校验同 receipt_id 的身份一致；不能通过后一个 COMPLETE 覆盖前一个 PARTIAL。原 manifest receipt 记录整体另存审计，便于区分原登记数量与合并后的唯一数量。

新投影是独立 inline 输入，不能将改了时钟/字段的行挂到旧 history_ref/hash 上。旧 ref/hash、整件输入、所有原分区和 receipts 只保留为来源封印与审计身份，官方 owner 持有同一只读 transaction 直到一次真实 R2 完成和来源终验。owner、PID/job/state/exact cutoff/input/字段审计/reader路径和原件 registry 同时封印；copy、deepcopy、token 重造、重消费、源码或源内容变更拒绝。保存普通 JSON不能恢复任何权限。

## 字段权限边界

fresh 原件只执行一次原 late normalizer，再复用冻结字段 V2 的真实 RAW exact-link、单一自足 COMPLETE 因子及全真实 attempt oracle。合法 PARTIAL 因子祖先留作硬鉴权审计依赖，不能污染已经另证的 fresh RAW 金额、换手或 COMPLETE 可比价格。

历史库的 payload SHA/typed/PIT 只证明原完整行，**不能凭 dataset 标签删掉 mixedrefs 的 PARTIAL 后授予较窄 RAW 权限**。没有对应真 RAW trio 时，原全父未满足 COMPLETE 的历史行仍 UNKNOWN，RAW/金额/换手及可比价不填零、不回退；不会以 UNKNOWN=0 为验收目标。原全父 COMPLETE 行保留经官方 owner 验证的存档 RAW 值；可比价格还须原 basis 与真实注册因子的新单 COMPLETE snapshot 同 basis/值 exact 相等，全部 attempt oracle无冲突，且每个价格的 `Fraction(存档可比价)==Fraction(存档RAW价)×Fraction(实际生效因子)`。两次 Decimal 舍入相同不能替代此精确乘积门禁。缺锚、缺 basis、不同 basis 不桥接；同 basis数值冲突硬失败。历史原 typed 换手 float 不冒充本次重新核验的 RAW百分数。

限价没有独立权限，保持 UNKNOWN。fresh 当前证券资格仍取原 normalizer，历史库完整不提升历史 board/ST/全年度资格。真实日历缺目标交易日时，requested target 独立 UNKNOWN；不延长原轴，不阻止合法观察日计算。旧父四时钟仍是实际原捕获，新派生 seen/ingested 为今天；不将派生时钟填到原 cutoff，历史PIT0、完整独立周期0。

## 资源与未执行范围

`DerivationLimits.budget_seconds` 只约束原 normalizer；`registered_worker_deadline` 是本次整个调用的绝对截止。官方 history owner 期限取其发行时实际剩余时间，最多7200秒。历史 consumed payload预算固定2GiB，官方计量包括重复 payload读取；这是合同上限，不是旧作业额度，也不是全部SQLite物理I/O。

此版本没有可扩大历史 payload预算的 API 参数：官方 owner 的 `byte_budget` 固定2GiB，返回 `history_read_metrics.payload_bytes_read` 为官方实际重复 payload计量。`BudgetHistory` 自身保持原 `memory_budget_bytes=1GiB` 与 `ENFORCE_WHEN_AVAILABLE_ELSE_BYTE_TIME_BUDGETS`，不能将外层2.5GiB OS-RSS额度描述成内部历史 guard已放大。外层可以采用更小额度；各阶段及进程树的实际观测仍另登记。

继承 `total_source_bytes` 仅计唯一 capture文件大小；base整件单独最多64MiB、正常原件按显式 per_file_bytes。registry的终读和源码身份读取会有额外I/O。因此返回不伪造实际累计physical_read、OS RSS或process-tree计量，真实研究必须另用已登记 supervisor观测总 wall、各阶段、实际累计读取和RSS。建议真实任务独立注册来源鉴权300秒、派生1200秒、R2/正式导出900秒，以及原件/历史/输出的各预算；它们不会自动成为本模块授权，不能复用旧失败额度。

工程验收只使用隔离 TEST_FIXTURE DB、真实 fixture trios和原生未改 R2，不连接SDK、网络或业务库。真实全市场执行与投资验证均须独立记录；本合同不宣称盘前当时已发出预测。
