# 显式封存 RESULT 本地复制件 V1

`SEALED_RESULT_LOCAL_COPY_V1` 使用不同路径的完整结果复制件重新核对封存内容。原封存 `path/SHA/bytes/7FP` 保留；原物理身份标记 `LOST_NOT_RESTORED`。它不修改、继承或放宽同路径 `SEALED_RESULT_BYTES_RESTORED_V1`，也不建立供应商签名、历史 PIT 或原 owner。真实存储位置是否位于文件供应商管理范围之外，须由独立环境验收证明；路径文字和目录名本身没有这种证明力。

只允许已封存的 core RESULT 角色。RAW、receipt、三个 producer、locator、控制、八个核心附件、完整输入/run、五份导出、归一化报告及全部来源末验继续执行原合同。策略、9 个 normalizer 和 evaluator 原字节不变；旧原件、旧 run/input/导出不改写。

manifest 的键必须精确如下。目录引用使用 `st_dev/st_ino/st_mode` 三字段；文件引用使用原有完整 ref4 和七字段物理身份。

```json
{
  "schema_version": "SEALED_RESULT_LOCAL_COPY_V1",
  "copy_root_reference": {"path": "/explicit/objects", "fp": {"st_dev": 1, "st_ino": 2, "st_mode": 16832}},
  "archives": [{"archive_final_sha256": "<sha256>", "results": [
    {"original_reference": "<original ref4 object>", "current_reference": "<copy ref4 object>"}
  ]}]
}
```

manifest 必须在复制目录之外。每份 current 文件必须在该目录内、使用不同的 canonical 路径、与原 SHA/bytes 完全相同、实际七字段身份相符，并具有 `UF_IMMUTABLE`。当前实现要求文件系统提供且实际保留这一标记；没有该能力不能宣称真实复制件合同成立。目录身份在协议检查和每次完整末验前后校验。不同原路径不得共用同一复制路径或物理文件；同一个原结果可以在多个归档 scope 中复用相同复制件。每个被认证 scope 必须与其 sealed core RESULT 集合精确配对，RAW 等角色不能借此迁移。未使用的其他归档 scope 不授予能力。

显式入口为：

```python
session = ArchiveVerificationSession(local_copy_manifest_ref=manifest_ref4)
capability = authenticate_archive(job, expected_final_sha256=final_sha, session=session)
certificate = commit_bridge(service, capability, local_copy_manifest_ref=manifest_ref4)
configure_local_copy_source_protocol(service, manifest_ref4)
```

`local_copy_manifest_ref` 与 `restoration_manifest_ref` 互斥，不从普通 JSON、DB 证书或文件名推断授权。默认 V1、旧同路径协议和未显式配置的 CSV 读取均拒绝本协议证书。协议描述包含 `profile/manifest_reference/manifest_sha256/copy_root_reference`，绑定 bridge ID、证书、capability/session/cache/借用证明、作业 policy 和 ledger。移除协议标记不能沿用原 bridge ID 降级为普通 V1。

会话 audit 使用 `result_local_copies`，来源登记使用 `result_local_copy`。每条包含原 ref4、current ref4、`original_physical_identity_status=LOST_NOT_RESTORED` 和 `proof_mode=SEALED_BYTES_AND_IMMUTABLE_LOCAL_COPY_PIN`。保存 CSV 的读取继续核对显式协议、manifest、复制根身份、完整 bridge/binding 和原 CSV 字节；这种保存事实读取不等同于当次完整归档重认证。授予后验能力的 import/plan/worker/load/borrow 仍核对所有原来源、DB 完整父 BLOB 和全部事务末验。

跨午夜后验采用独立 `LocalCopyLateCompletedSessionClosure`：

```python
closure = LocalCopyLateCompletedSessionClosure(service, local_copy_manifest_ref=manifest_ref4)
plan = closure.plan_late(run_ids, input_id, business_session=business_day, evaluated_at=actual_knowledge_cutoff)
job = closure.submit_late(run_ids, input_id, business_session=business_day, evaluated_at=actual_knowledge_cutoff)
```

它继承 `LateCompletedSessionClosure` 的正式日历、实际 cutoff、收盘及认证前后最新已完成会话门禁。业务日必须同时为截止与新准入时点的最新已完成会话；例如 2026 年 10 月 24 日上海周末的业务日为 23 日，不能改用 20 日。执行时钟不回拨；非显式 `TEST_FIXTURE` 禁止注入时钟。原归档 seal 以及 manifest/current copy 的 ctime 必须不晚于知识截止；这只是本机证明生成顺序，不是历史可见性证明。迟处理上下文与来源字段共同保存，不覆盖原 late 语义。

新命名空间：`SHORT_HORIZON_LOCAL_COPY_LATE_COMPLETED_V1`、`short_horizon_local_copy_late_completed_v1`、`short_horizon_local_copy_late_completed_ledgers_v1`、`short_local_late_`。CLI 使用 `r2-local-copy-late-plan/run/status/retry`，完整指定 `--source-protocol SEALED_RESULT_LOCAL_COPY_V1 --local-copy-manifest PATH --local-copy-manifest-sha256 SHA`，plan/run 同时指定原 late 的 `--business-session/--knowledge-cutoff`。归档 verify/import 同样支持这组显式参数。若使用 archive daily-cycle，本协议还必须显式提供 late 的业务日与知识截止。

默认完整认证资源仍为 300 秒、2 GiB，所有原读/hash/末验及事务回滚保留；显式 900 秒 profile 独立报告。夹具通过、复制件可用、真实输入、实际每日后验与双 run 性能分别验收；本文及新代码不证明真实闭环成功或 300 秒达标，也不切换生产服务。
# 封存的非权威失败新闻 HTTP 附件

`SEALED_NONAUTHORITATIVE_NEWS_HTTP_ATTACHMENTS_V1` 是独立的附件完整性契约。
它不改变 `SEALED_RESULT_LOCAL_COPY_V1` 的来源描述符，不恢复原文件的物理身份，
也不授予 SOURCE、EVENT、题材、新闻完整性或历史可见时间能力。

通用 `_ref` 和 `ArchiveVerificationSession.read` 仍要求文件字节数严格大于零。
仅在封存 completion 的平面 validation refs 中出现零字节项时，桥接器才检查：

- `completion.news` 的 metadata、summary 与 validation refs 完整绑定；正字节
  `news/reported-materials.json` 必须为 `PREMARKET_REPORTED_NEWS_V1`，与原 run、
  观察日、目标日一致，且不签发 SOURCE/EVENT 能力、不猜测业务归因。
- metadata 的 `original_HTTP_captures` 与平面封存 refs 逐项对应，限制为 1–10 次；
  每个正字节 `http-NN.receipt.json` 的完整规范 JSON 必须等于 metadata 内嵌 capture，
  其中 raw ref 与同序 `news/http-NN.html` 的完整 ref4 一致。
- 仅 `FETCH_FAILED`、HTTP status 为 null、实际返回字节为整数 0、
  `raw_capture_complete=false`、`archived_prefix_only=false` 的响应可为空。
  `COMPLETE_HTTP_BODY` 的空响应不在本契约内。请求仅限原新闻采集器的两个主站和
  有界文章路径；回执保留错误及请求、首次可见、入库时间，时间顺序须成立。
- 空 raw 必须真实存在于原 job/news 内，大小为整数 0、SHA256 为真实空内容 SHA、
  七项物理指纹精确匹配。专用只读 reader 使用 `O_NOFOLLOW`、open 后 fstat、
  实际 EOF 读取及读取后路径/指纹复验；每次 terminal 再走同一条专用读取路径。
  正 metadata、回执及其它本次 news raw 同样进入完整 SHA/7FP 注册及 terminal。
  零字节照实计入文件数，独立、累计读取字节数仍为 0，现有时间/RSS/文件预算不扩大。
- 其它空 ref、未绑定附件、重复或缺失 refs、空 metadata/回执、代码、控制、core、
  RAW 行情与价格来源仍拒绝。附件不能借来源本地副本协议获得通用空文件权限。

契约描述符写入 bridge audit、证书和 bridge identity，session budget audit 列出
本次所有明确附件契约；剥离或改写描述符会使桥接身份或重新认证不匹配。
它只描述原封存失败附件的完整性，保留 `news=PARTIAL`、采集未完成、
`EVENT_confirmation=UNKNOWN_NOT_ISSUED`、业务归因与新闻覆盖 UNKNOWN。
`NEWS_PRESENTATION` 仍在排除范围中，策略公式、选股结果、原始 input/run、
四份 CSV 与观察 JSON 不因这一附件契约重新计算。
