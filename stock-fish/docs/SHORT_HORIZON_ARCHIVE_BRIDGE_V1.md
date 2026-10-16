# SHORT_HORIZON_ARCHIVE_BRIDGE_V1

本合同把既有 standalone premarket 的封存 R2 核心归档，经过当前重新鉴证后，追加到**显式选择的既有服务库**。冻结的 run、inline MarketInput、原始证券范围、research_selected 与 FOCUS、时点、配置和 `content_hash` 不变。导入时间单独记录。桥接不重新运行 selector，不采集、不交易，也不将新的导入时间当作过去取得数据的时间。

实现：`stockfish_core/yaogu/short_horizon_archive_bridge_v1.py`。后验仍使用独立的 [SHORT_HORIZON_OUTCOME_V1](SHORT_HORIZON_OUTCOME_V1.md)；每日闭环由独立 daily closure 模块组织，不修改 R1/R2 公式或默认主动策略。

## 1. 接受范围与明确前提

- 仅接受 `STANDALONE_PREMARKET_WORKFLOW_V1` 原生工作流成功封存的 `COMPLETE` / `COMPLETED_WITH_UNKNOWNS` job；worker 已回收、实现时期检查和来源终验成功、core 已验证。调用者必须显式提供原 `final-control.json` 的可信预期 SHA256。
- final seal 必须能绑定原 configuration、code registry、completion；完整保留八份核心产物：压缩 canonical run、压缩 input、manifest、四 CSV、一个 observation JSON。失败或缺失 core 的工作流不能补写标志后自动升级。
- 仅支持 `SHORT_HORIZON_STRENGTH_R2`、单 R2 全量池、inline `PROSPECTIVE_CAPTURED` 或原样保留的 `RECONSTRUCTED_HISTORICAL` input。后者不升级为 prospective/PIT。显式隔离的 `TEST_FIXTURE` 仅用于工程检查。history artifact、任何历史时间证明及 `UNVERIFIABLE_AS_OF` 不在本版导入范围，不恢复 historical owner。
- 原 RAW、receipt、result 三件套，以及生产该 result 的原 `provider_requests.py`、`provider_requests_worker.py`、`provider_budget.py` 必须可核验。普通 capture-summary 只是定位信息，不是来源资格；每一组都必须匹配封存 completion 中的完整物理引用，以及 manifest/input 中完全一致的 receipt 集合。
- 当前 normalizer 的明确依赖必须与归档登记的原源码逐字节一致，否则拒绝。不会执行归档中的旧 Python。允许的重新计算只包括原始输入归一化、日历验证、内容摘要和正式导出；**不重算原名单**。
- 本版只接核心归档，不声称已导入 theme/news 展示、任意自定义原生目录、历史只读说明文件，或全部私有报告。

SHA256 和物理引用提供本地完整性，不能代替数据供应商签名。预期 seal hash 是调用者明确指定的信任锚，不能把一个任意自签 JSON 当作独立来源认证。新能力是本次原件重新鉴证的、不可序列化的导入权限；它不是旧 `NativeCore` 或历史 owner 的恢复，也不能用于冒充原主题/历史能力。

## 2. 校验链

鉴证依次检查 seal 与成功状态、三个控制文件、八份核心产物、run/input 完整身份、五份正式导出、原源码闭包、全部核心三件套、原业务时点、日历和重新归一化后的完整 input hash。只有全部通过才签发 invocation 内有效的 `ArchiveBridgeCapability`。

来源验证包括：原 receipt 的 ID 前像、嵌入 receipt 的完全一致性、RAW hash、provider/endpoint/parameters、全量 rows、status、pagination、coverage、gaps、field version，以及 started ≤ first_seen ≤ ingested ≤ completed ≤ 原 decision cutoff。归档中可缺的物理 row_count 保持原约定；实际全量 rows 仍和 result、typed receipt 逐项核对，不把缺失当零行。

原件按完整 SHA256 和七个文件身份字段核对：device、inode、mode、size、mtime_ns、ctime_ns、flags。首次读取、认证结束、导入前、导入事务全部写入之后但提交之前，以及每日发布前均有终验；符号定位关系存在时继续验证原 symlink 及目录身份。文件读打开后还核对实际 descriptor，拒绝文件替换及非普通文件。事务期间发现原 RAW、旧源码或导出变动会回滚本次全部新增文档，不能依赖调用方在提交之后补做检查。

run 验证覆盖 top/inner 策略身份、input ID/hash、原 run ID 前像、全量且不重复的 eligible IDs、证券名称、counts、selection policy、research_selected 顺序及 full inline details。五份导出必须与该冻结 run 的现行正式编码器输出一致；编码器不兼容时拒绝，不重写原导出。

## 3. 原源码发生变化时

默认要求 code registry 登记的旧源码路径仍满足旧 SHA 与七字段物理身份。若工程升级使旧路径不再满足，可显式提供源码字节副本：

```json
{
  "schema_version": "SHORT_HORIZON_ARCHIVE_SOURCE_OVERRIDES_V1",
  "files": {
    "stockfish_core/yaogu/candidate_service.py": "/absolute/original/source/candidate_service.py"
  }
}
```

键只能来自原 code registry 或冻结 run 的 `source_file_hashes`，必须是原业务根目录相对路径；不允许 `..`、绝对键、未登记键。值是显式绝对路径。副本必须匹配**原封存记录里的 hash**；程序登记副本现在的物理身份，并标明 `EXPLICIT_COPY_CONTENT_PROOF_NOT_ORIGINAL_PHYSICAL_OWNER`。这证明旧内容仍然存在，不声称复制件拥有旧 inode/owner。缺少旧内容时拒绝，不用当前实现替代。

归档中只登记内容 hash 的策略 config/spec 文件只获得内容证明，不虚构缺失的旧物理引用。

缓存三件套可能由更早、不同 checkout 的 worker 生产。其三份 producer 源码独立按封存 source ref、result 中原 implementation hash 和旧七字段物理身份认证，不要求等于本次 job 的 core code registry，也不以同名的新源码替代。若不是 job registry 中可明确映射的文件，本版要求其原 producer 路径仍完整存在，不猜测副本映射。

## 4. 同库原子导入与幂等

`commit_bridge(service, capability)` 要求真实、未关闭、持有该库现有 OS worker 锁的 `JobService`。CLI 在打开可写 store 前取得既有锁，避免与服务并发写入。导入使用一个事务：

1. 原样写入 `candidate_inputs`、既有 input index、`strategy_experiment_runs`，保持原不可变冲突规则，并完整校验既存字节 hash；不经过会压缩并改变 input ID 的 `register_input`。
2. 重新核对/补齐既有策略 summary/pages/implementation assessment；冲突或损坏会回滚整个事务。
3. 追加 `short_horizon_archive_sources_v1` 原件登记、`short_horizon_archive_bridge_certificates_v1` 新证书、`short_horizon_archive_run_bindings_v1` 和 `short_horizon_archive_input_bindings_v1` 定位绑定。
4. 从本 invocation 钉住的原引用重新读取四份 CSV，在 `short_horizon_archive_csv_exports_v1` 保存不可变 UTF-8 原文及其完整 SHA256、字节长度、selection、bridge/run/input 身份、原 run 完整 canonical hash、signal hash、seal hash 和原 artifact 引用；原件或已有导出冲突会一起回滚。

这些都是现有服务库中的来源和派生索引，不是第二套候选名单。输入和 run 的业务 ID 不变；不激活新 input、不改默认策略。旧 run 中 `source_database_written=False` 与原 artifact role 继续保留；新证书另记本次实际导入。

桥接 ID 包含原 seal SHA、run content hash、input hash 和当前鉴证实现 hash。同一身份重复导入保留第一次 `imported_at`；没有可传入的历史导入时间参数。相同 input 被多个不同 run 使用时，input 绑定保留第一个完整有效证明，run 分别保留各自证书。冲突的同一 run ID 不会被覆盖。

桥接后的正式 CSV 查询返回已鉴证原文，避免服务库 canonical JSON 对嵌套键排序后重新渲染产生字节差异。`saved_csv_export` 在同一读事务核对 binding、证书、四份原 CSV 的完整集合、原 artifact 引用、文本字节 hash，以及传入 run 与服务库实际完整 run 的 canonical hash；即使普通文档自 hash 被重新计算，错误来源或文本也不会获资格。缺任一份、绑定替换或碰撞均明确拒绝，不自动回退或重新渲染；只有完全未桥接且无桥接导出的旧 run 使用原 formatter。缺失导出只能通过完整 freshcap 导入修复。此查询核验已保存原件，不声称重新鉴证外部归档路径。Python 服务返回原 UTF-8 CSV 内容；既有 HTTP 下载层额外添加的 BOM 属于传输包装，不属于原 archive CSV 字节。原 `observation.json` 仍按完整 JSON 语义及原 signal hash 核对，不声称其重新序列化字节与源文件完全相同。

公共导出还要求实际 run 身份等于请求的 run ID。即使 run binding、证书和四 CSV 均被离线删除，只要本 run 的来源登记或输入绑定仍残留，也必须拒绝回退渲染；共享输入绑定指向另一 run 时，不把它误判为本 run 的桥接状态。

当前鉴证实现身份保守绑定整个 `stockfish_core` Python 包及配置文件。因此后续源码变化会使旧 V1 凭证重新认证失败；V1 **不提供自动跨版本升级或覆写旧 binding**。未来兼容升级须显式登记新合同/迁移和新证书，不能承诺旧凭证跨任意代码版本继续运行。

2026-10-16 首次真实只读检查发现既有原生归档实际为 inline `RECONSTRUCTED_HISTORICAL`，初稿误限 prospective 因而拒绝。正式合同改为验证原覆盖类型下完整重新归一化的相同 input hash，仍拒绝历史证明/owner/ref，保留原四时钟和未知状态。这是纠正接受范围，不是把历史下载变成过去已取得的资料。

## 5. 查询、重新认证与资源边界

公开 Python 接口：

```python
cap = authenticate_archive(job_dir, expected_final_sha256=seal_hash,
    source_overrides=files, allow_test_fixture=False)
summary = cap.audit()                     # 只读，不打开数据库
certificate = commit_bridge(service, cap) # 调用方先持有既有服务锁

session = ArchiveVerificationSession(checkpoint=check_cancelled)
signal = require_verified_bridge(service, run_id, session=session)
new_input = require_verified_bridge_input(service, input_id, session=session)
session.terminal()                       # 发布前必须终验
```

两种 `require` 返回 `bridge_id / run_id / signal_content_hash / input_id / input_hash / archive_final_sha256 / imported_at`；核对请求 key 与 payload ID、服务库父 run/input、来源登记，并重新读取归档原件。没有合格桥接证书的普通服务 run 或手工 JSON 不获资格。缺原件/源码会拒绝当前运行，不自动降级为已验证。

日计划可用 `authenticated_bridge_view(service, run_id=... 或 input_id=..., session=session)` 在同样完整鉴证及数据库实际字节核验后取得小型 `credential / run_metadata / input_metadata`；后者包含原观察日和完整登记 sessions。返回的普通字典只用于描述，不能作为授权。`borrow_run=True` 允许 worker 借用同一份完整原 run，避免重复展开。`load_verified_bridge_input(service, input_id, session=session)` 只接受本 invocation 私有登记过的真实输入证明，绑定原 store 对象、实际数据库路径及 inode，重新核对 binding、证书和实际库中原件 hash，再从被钉住的 gzip 直接构造 typed input；不会从普通 view 恢复权限。借出 run 与 typed input 均保留强引用和原 hash，终验重新计算完整 hash，嵌套篡改会拒绝发布。

`session.release_cached_archives()` 只释放未借出的重归档缓存；不清除文件、定位关系、小型鉴证登记、已用预算和借用对象终验守卫。同档案的 run/input 仅在两个真实 binding 均指向同一证书时共享小登记。多份借用大 run 仍会同时占用内存，受原共享预算约束，不会自动截断批次或提高上限。

一个 invocation 的 session 为所有 run 和新 input 共享预算，默认：实际文件读取 8 GiB、唯一文件 4 GiB、4096 文件、300 秒、进程 RSS 2 GiB；单文件 256 MiB、控制文件 16 MiB、解压文档 256 MiB。预算必须有限正数（字节和文件数为整数），不会因下一 run 自动重置或扩大。重复身份可共享读取登记，但发布前仍逐个唯一文件完整终验。检查取消回调在每文件、每 1 MiB 块、归一化前后和终验中执行。

全局弱注册表不强持有 session；同 session 缓存的能力在调用方释放后可整体回收。输入鉴证期间先释放原 JSON 再重新归一化，导入时才重新读取被钉住的原 input。既存大 run/input 的幂等检查逐块读取全部 SQLite payload，对照已鉴证原件的 canonical hash，而不是只信任数据库 metadata hash；不再为重复导入同时展开两套大对象。

新 run/input 写入时，使用与存储一致的 canonical JSON 参数逐块编码 UTF-8，暂存至明确服务 DB 同目录的私有临时文件。每份规范文档最多 256 MiB，完整字节 hash 必须等于先前已鉴证的原件 canonical hash；原 gzip 的空格和键顺序不会被直接当成存储规范字节。输入对象先释放，再通过临时的本连接私有零参数 SQLite 回调读取已核 hash 的暂存内容（长度上限、完整 hash 和单次调用再次校验），由 SQLite 复制返回 bytes，Python 返回对象在 JSON 索引求值前释放。正常 `INSERT ... CAST(... AS TEXT)` 继续执行全部 JSON、索引与事务检查，再逐块核对实际已存字节；不使用索引列的增量 BLOB 写入。回调不接受外部路径或普通 JSON 授权，不声明 deterministic；成功或失败后关闭 cursor 并注销函数，回调中的取消、预算或其他原异常会重新抛出，不被包装成普通成功结果。临时文件在成功、失败或取消退出时关闭清理。私有暂存 I/O 不计入来源文件读取字节，但仍消耗同一墙钟和 RSS 预算，不能据此宣称端到端总 I/O 已计量。

输入 INSERT 的 JSON 索引解析期间，导入器先核对完整 run hash，再释放 capability 缓存中的展开 run 与当前局部引用，仅保留已鉴证原 ref/hash；输入写入后从同一原件重新读取并完整核 hash，才继续 run 与投影写入。重读失败会回滚整笔事务，同一 capability 再次使用也必须重新核对原件，不会把缺失缓存当成成功。此前已经借出的 run/typed 终验守卫不会被清除来降低内存；其额外常驻量仍受原预算约束。

审计保存实际读量、唯一字节、文件数、耗时和 RSS。时间/RSS 为合作式检查：Python 解析/归一化内部没有逐行抢占，也不是 OS 内存沙箱；超预算不发布。数据库字节未计入文件读取计数，但会话墙钟持续累计，数据库操作耗时仍消耗该时间预算；CLI 另有提交后的等待预算。不能把文件预算称为端到端总 I/O 计量。

为保持原 input ID，本版保留完整 inline 输入；大档案的解析、归一化、原件终验仍可能明显耗时。失败应保留实际诊断，不扩大默认预算以宣称通过。跨多个大档案的常驻对象和原件读量是已知性能边界。

## 6. 用户入口

从业务根目录执行，`RUNTIME_PYTHON` 是已验收独立环境的绝对解释器路径：

```sh
"$RUNTIME_PYTHON" -I -B scripts/stockfish.py r2-archive-verify /absolute/sealed/job \
  --expected-final-sha256 ORIGINAL_FINAL_SHA256 \
  --source-overrides /absolute/source-overrides.json

"$RUNTIME_PYTHON" -I -B scripts/stockfish.py r2-archive-import /absolute/sealed/job \
  --expected-final-sha256 ORIGINAL_FINAL_SHA256 \
  --source-overrides /absolute/source-overrides.json \
  --db /absolute/existing/service.sqlite3 --state /absolute/existing/state
```

verify 不打开业务库；import 不创建默认库、不自动迁移、不启动服务或采集。导入后的 run ID/input ID 可供同库短周期后验和每日闭环使用。每日新增 input 同样须来自成功桥接；普通 JSON 不补足来源资格。

工程验证使用独立 `TEST_FIXTURE` 原件和临时 DB/state，覆盖成功、幂等、回滚、原件变动、缺源码、明确副本、导出篡改、封存失败、普通 JSON、错误 binding、关闭 worker、取消与预算。真实归档只读鉴证或隔离验收库结果须另列，不能用 fixture 通过推断真实来源就绪、策略收益或市场优势。账户、费用、组合、容量和执行仍未接入此合同。
