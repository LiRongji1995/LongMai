# 隔离切换、回退与恢复

`infrastructure/lifecycle`仅管理带`.stockfish-isolated.json`标记的本任务私有目录。它不启动、停止或切换生产服务；源冻结快照和V2目标均须在此目录内，拒绝路径逃逸、符号链接和非回环监听。CLI组合入口负责持有`AcquireWriter`租约；存在租约时切换被拒绝，崩溃遗留租约不能自动删除。

流程为`Init -> Preflight -> Cutover`。Preflight核对源快照完整字节hash、目标PUBLISHED迁移与验证摘要、编译期schema checksum、应用schema、V2默认身份、scheduler暂停、未完成任务/未发布结果、单写者、10GiB可用余量及私有目录50GiB持久容量上限。它仅声明与指定冻结源一致，不声称实时追平活跃生产。

Cutover原子发布该隔离实例的`instance.json`，记录进入GO_V2前的变更账本sequence。CLI/API从同一ActiveDatabase打开应用；旧输入/结果不重算。配置发布前失败不会把半成品路径设为活动库。

`ExportChanges`先检查备份所需空间，再用SQLite在线备份API冻结完整V2恢复副本，逐项验证切换后INPUT、RUN、JOB和JOB_EVENT账本hash，写出changes.jsonl及最后发布的completion.json。RUN复用正式保存结果验证器；JOB_EVENT按不可变事件字段重建hash。备份包括全部原证据、输入、结果和任务事件，账本本身不是丢弃细节的替代品。`VerifyExport`重新核对备份文件hash、ledger文件hash、sequence边界/成员集合及每个内容引用。

回退有两种明确结果：

- 仅含兼容输入：以原冻结源再创建隔离旧库副本，校验新input及history引用，写candidate_inputs/indexes、旧新ID映射和冻结包装归档；按原字节hash验证同ID同内容幂等，同ID异内容报错；完整性检查后切至LEGACY_REPLAYED。原源及V2原库均保留。
- 含Go-only研究结果或任务事件：返回`BLOCKED_UNREPRESENTABLE`，保持GO_V2活动配置，保留新库、在线备份及delta。不能删除新数据或更改路径来声称无损旧格式回退。

`Restore`验证已发布export，在另一个全新V2文件恢复，再明确把QUEUED/RUNNING任务转换为INTERRUPTED并追加事件，不自动重跑。源快照无需修改；原V2文件和备份继续保留。回退旧副本后来发生写入时，下一次Preflight检测完整文件hash变化并报`UNHANDLED_LEGACY_INCREMENTAL_CHANGES`，要求显式增量迁移。

已执行TEST_FIXTURE演练：隔离旧快照→GO_V2→新增输入→兼容旧库回放→再次切入GO→新增结果→拒绝不可表达回退→模拟目标路径失联→从已验证备份恢复结果及输入；另测RUNNING任务恢复INTERRUPTED、writer竞争、空间不足注入、取消、路径逃逸、账本篡改、同ID内容冲突和未处理旧副本新增写入。`go test ./infrastructure/lifecycle -count=1`退出0（初轮3.641秒）；race/vet及CLI实际隔离演练以统一最终验收记录为准。未进行生产切换或Windows原生运行。

## APFS隔离恢复副本

macOS同一APFS卷的恢复副本使用`golang.org/x/sys/unix.Clonefile`直接调用系统`clonefile`，不通过cp，也不把普通复制描述为COW。仅上述内部lifecycle路径可以进入：必须位于隔离标记目录、持有当前进程writer租约，唯一允许checkpoint的源为配置内V2 target。先执行`wal_checkpoint(TRUNCATE)`且检查无busy/未完成帧，关闭该连接并确认WAL不存在或为空。冻结旧源和冻结恢复副本必须先SnapshotCheck且WAL为空，不执行源PRAGMA修改。CLI调用前应关闭其新库业务连接，任何其他遵循writer租约的写者不能并行进入。

源前后完整字节hash及目标hash必须一致；clone后执行integrity、foreign_keys和实际schema核验，V2还核验应用schema及发布元数据。原生产源的首次Snapshot继续使用只读SQLite在线backup，不切换为raw clone。非Darwin/非APFS继续原backup并预留完整复制空间。

COW仍检查至少10GiB真实空余，预留128MiB元数据/WAL余量并在完成后复查；50GiB逻辑持久容量上限不因共享块而放宽。克隆失败不自动退化为可能填满磁盘的复制。身份侧车版本`ISOLATED_APFS_CLONEFILE_V1`，另存`.clone-metrics.json`分别记录checkpoint/冻结源核验、各完整hash、clonefile、完整性/schema和耐久发布耗时。共享块未来写入仍消耗磁盘，写入前资源门禁继续有效。

已在本机APFS执行真实clonefile测试：提交的WAL数据可读、克隆后独立写入不改变源文件hash；245760-byte数据库的测试身份hash为`3637e9b34e378b4cedb54c54b5acdfda16bf6bd6180ace40587d771cb0ba4103`，该hash只标识当次TEST_FIXTURE。还验证无writer租约和冻结源非空WAL拒绝。命令`go test ./infrastructure/lifecycle -run 'TestActualAPFSClone|TestCloneRequires' -count=1 -v`退出0，1.432秒。真实大库COW演练由统一集成报告单独记录，不由小fixture替代。

增强后的最终小集成：HTTP/architecture/lifecycle普通测试分别1.024/1.535/3.616秒，全部退出0；随后三包go vet退出0。APFS实际clone与租约/WAL拒绝样本补跑race退出0（6.140秒）。

## 统一写者与封存的中断结果

`AcquireWriter`现在以全取或回滚方式取得`root/writer.lock`与`target_database.go-writer/owner.json`。后者与显式CLI `--db`共享同一目录租约；第二个声明失败会撤回刚创建的root租约，不触碰已有CLI租约。释放前验证token和文件内容；不删除外来/变化的租约。只读preflight也检查目标数据库租约；持有者内部preflight验证自身两个租约，不误拒自己。Restore的新目标在生成前另取得数据库租约，关闭恢复连接后释放，防止`--db`绕入新路径。

PREPARING结果只有在不可变变更账本中有`ABANDONED_RUN`并逐个通过`VerifyAbandonedRun`后，才从未解决工作计数中排除；报告单独给出verified_abandoned_runs。原PREPARING状态、预计item_count、已写prefix及原payload保留，不发布或补齐结果。delta验证同一封存hash；该kind属于Go-only，旧格式回退仍明确阻断。备份恢复后仍需原样复验，任何合法字节重写导致的seal hash变化也拒绝。

新增TEST_FIXTURE覆盖：真实CLI loopback临时serve与lifecycle双向竞争、第二租约失败无root泄漏、内部owned preflight、`--instance-root`不重复加锁；另使第51条保存明细失败，保留已提交50条prefix，验证未封存阻断、封存后预检、Go-only回退阻断、备份恢复50条且不可公开读取、修改封存header后拒绝。完整lifecycle普通测试及vet退出0（4.398秒）；最终统一race由集成报告记录。

普通CLI命令在打开既有数据库前先取绝对路径并解析全部符号链接；租约名使用该规范路径，`--db`符号链接别名不会获得另一把锁。跨入口回归包含alias启动CLI writer、lifecycle持锁时alias写入拒绝。SQLite数据库不支持通过不同硬链接路径并行访问；本轮未以inode注册表实现硬链接别名合并，部署约束必须禁止给同一库创建/使用多个硬链接入口。该限制不放宽符号链接路径保护。
