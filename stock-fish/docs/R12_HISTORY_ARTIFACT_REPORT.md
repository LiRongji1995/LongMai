# R12 — Long-History Architecture

本轮采用 `CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1`。沿用运行实例的同一个 SQLiteStore；新增不可变历史行和会话分区表，不创建第二个权威数据库，不变更 V2 Setup、阈值、优先级或 Focus 门槛。

## 存储与增量

`DailyBar` 和 `SecurityRecord` 按完整原始规范 JSON 计算 SHA256。行内容包括 comparable basis、provider/source refs，以及 event_time、source_published_at、first_seen_at、ingested_at；不回填首次取得时间，也不将研究重建升级为 prospective。相同字节只保存一次，SQL trigger 拒绝 UPDATE/DELETE。

每个交易会话的不可变分区包含按 kind/instrument/hash 排序的行身份。分区 hash 绑定成员顺序与完整成员集合；manifest 保存分区 hash、session、行数、bar 数、instrument coverage、来源 receipt 与版本。manifest 本身内容寻址。每天 `MarketInput` 只保存 `history_artifact_ref/hash` 及最近六个交易会话的 PIT 行和当前上下文；完整历史留在共享分区中。证券区间若从更早开始且仍有效，会保留在日输入中。

显式增量 writer 使用 `base_ref`，继承旧分区并仅创建新增/修订会话的分区。已有行复用；旧 manifest 永久只读。既有会话的真实修订保留为新 revision，不就地覆盖旧值。每日 current overlay 必须参与新 manifest；读取同时合并尚未登记的近期 overlay，不能静默丢弃新增日。

正常 provider normalization 可传 `history_store=HistoryArtifactStore(existing_store)`，按证券生成的历史规范行即时写入磁盘 staging，Python 仅保留近期行。该路径删除 full market Pydantic history 的分配，但 provider 采集结果和 raw-normalization 索引目前仍可能驻留内存；这项剩余成本没有宣称完全 streaming。

## 读取、完整性与策略

历史数据通过 instrument/window 索引读取，315-session 查询在单连接的有界 SQL 查询内完成。V2/量价上下文与 outcome context 的历史对象缓存最多八个证券。V2 另外在同一不可变上下文内复用 MA/return 等纯标量，只保留每证券 T 日一行，并设置严格 8 MiB 上限的逐证券压缩二级缓存；二级缓存仅解码当前上下文自己生成的内存字节，不持久化或读取外部 pickle。这样避免 sector/benchmark 横截面循环反复校验同一份 315-session 历史。DAILY 五族引擎也按证券读取历史价和历史资格；跨市场事件只保留 T/T−1。

EvidenceResolver 继续执行原证券身份、来源 scope 和四时钟可见性检查，并从校验后的 manifest 合并原始历史 receipts；当前 receipt 不得用同一个 ID 改写旧时钟。对于已绑定历史 artifact 的行，resolver 仅保留去重后的 32-byte 行 hash，不再额外保存整个历史 JSON；effective dependencies 保存 immutable artifact hash、实际选中行的逐证券 digest/count 和原 visibility policy，实际 cutoff 保存在 signal run 中。同一可见证据在一分钟后重算不会仅因时钟变化而改变 effective hash，真正新增可见行会改变选中集合 hash。信号 input identity 与保存的结果均显式绑定历史 hash；实现 source identity 也覆盖 history reader、contracts 与 visibility 依赖源文件。

读取绑定先验证 manifest 内容 hash，再 streaming 验证会话成员 hash 和引用目标存在性；按证券读取时验证每条 payload hash 和 instrument/session 索引一致性，LEFT JOIN 的缺失行明确报错，不能静默缩小样本。缓存命中也重新校验 manifest hash，调用者修改返回字典不能绕过完整性检查。损坏、缺失或未绑定 store 的 artifact 被拒绝。hash 用于检测本地损坏；与原 SQLiteStore 一样，并非抗拥有完整磁盘写权限者的数字签名。

## 旧资料迁移

`migrate_legacy_input(store, input_id)` 使用 `sqlite3.blobopen` 按 64 KiB 读取原数据库 TEXT 字节，再增量解码单条 JSON row。原 MarketInput 的完整 byte hash 校验通过后，才发布新 manifest。原 input ID 与旧内容不改写。

`migrate_legacy_file(store, path, observation_date=...)` 为已归档 raw capture 文件提供相同流式迁移，返回原文件 SHA256、byte 数、row 数和 compact bundle。它不自动宣称数据可评价，也不改变 coverage type。调用者需经正常 CandidateService 注册新的 compact input。无需将 321 MB 文件完整读入 Python 或调用 `MarketInput.model_validate_json`。

## 实测与边界

独立进程、显式 TEST_FIXTURE：512 个证券 × 315 会话，共 161,280 bars。原输入 120,061,527 bytes，R12 日输入 2,526,444 bytes；峰值 RSS 2,588,327,936 → 159,555,584 bytes。旧完整读入/复制/hash 用时 8.632 秒；新路径初次流式迁移 14.273 秒，重新加载并逐证券查询全部历史行 10.232 秒。一会话追加只写 512 个新 bar、379,904 bytes，未复制其余 315 会话。

这些数字是本轮真实执行的合成工程测量，不是全市场投资证据。另一次真实 2026-09-08 capture 文件迁移：原文件 321,622,653 bytes，150,626 bars、155,226 security rows；compact 日输入 10,018,721 bytes，保留 4,599 近期 bars、9,199 security rows；历史唯一行 payload 234,077,094 bytes。迁移耗时 25.146 秒，实测峰值 RSS 130,613,248 bytes。原文件 SHA256 为 `595666a2e22f3cfa7e15a6137edf8a1ac4e53112890846f5e9e67f10931834bc`，history hash 为 `bc16a125f35ae7e1801ff6bd895b2463aab34ffa1346283a535e1b07991eb10a`。该记录仅证明工程迁移，`signal_evaluated=false`，没有将采集完成当作 signal 可评价。真实文件没有跑旧路径 OOM 对照，因此不能把合成旧峰值包装成真实旧峰值。

SQLite 索引/page/WAL 另有成本；旧日输入保持 immutable，不删除以制造磁盘节约。详细定义与 JSON 数据见 `MEMORY_PROFILE.md` / `MEMORY_PROFILE.json`。重跑入口为 `scripts/r12_history_memory_profile.py --output <isolated-dir> --instruments 512 --sessions 315`。集成回归曾暴露 31 个证券反复跨池读取触发 LRU 抖动，修复后原 scheduler 完整用例 `1 passed in 6.20s`，未放宽原 15 秒 job 限时。

架构回归覆盖 immutable hash、重复 artifact 复用、一会话 append、原 basis/refs/四时钟保存、损坏拒绝、source identity 绑定、禁止 monolithic dump 的 MemoryError 注入、旧 SQLite/file 流式迁移，以及超过近期六会话的 D+20 outcome 等价性。真实回放、最新 MA 覆盖与运行实例状态由本轮总报告独立记录。

`MARKET_EDGE=NOT_ESTABLISHED`。

## 后续真实完整 capture 测量

2026-09-08 17:13 完成的真实 capture peak RSS 为 923,172,864 bytes、OS peak working set 为 923,463,680 bytes，端到端 767.454 s；SUCCEEDED 但 consumer UNAVAILABLE。它覆盖 provider/raw 索引、consumer 与保存，是完整采集成本；此前 130,613,248 bytes 只属实际流式迁移，159,555,584 bytes 属合成 artifact benchmark。当前仍保留 raw/index 驻留成本，不把较低子路径数字声称为整条真实采集峰值。最新 daily input 10,163,247 bytes、manifest 210,254 bytes；原始与重构证据均未因此变成可评价信号。详见 MEMORY_PROFILE.md/json 和 V2_DATA_READINESS.md。
