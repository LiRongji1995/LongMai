# 封存结果文件的显式内容恢复协议

`SEALED_RESULT_BYTES_RESTORED_V1` 是新增、显式启用的来源合同。它只证明原封存 result 文件的完整字节已恢复到当前稳定物理文件；原 inode、ctime 等物理身份仍标记 `LOST_NOT_RESTORED`。原默认 V1 继续要求原七项物理指纹，不接受这种恢复证明。协议不改变策略、价格、run、input、正式导出、原收集时间或历史 PIT 等级，也不是供应商签名认证。

恢复清单必须先单独保存并固定完整 ref4（绝对路径、SHA256、字节数、七项当前物理指纹）。其格式为：

```json
{
  "schema_version": "SEALED_RESULT_BYTES_RESTORED_V1",
  "archives": [
    {
      "archive_final_sha256": "<原 final-control.json SHA256>",
      "results": [
        {
          "original_reference": {"path": "<原绝对路径>", "sha256": "<原SHA256>", "bytes": 1, "fp": {}},
          "current_reference": {"path": "<相同绝对路径>", "sha256": "<相同SHA256>", "bytes": 1, "fp": {}}
        }
      ]
    }
  ]
}
```

示例中的 `fp` 必须替换为完整真实七项指纹，不能照抄为空对象。每个归档 scope 中只允许原 sealed core result；原 ref4 必须同时逐值存在于该 core item 和原封存 validation references 中。当前 ref 必须同路径、同 SHA、同字节数、不同原物理指纹。额外、重复、角色错误、缺失或未使用条目拒绝。不同归档 scope 可引用同一恢复文件，但每个归档分别校验。

原 RAW、receipt、producer、源码、控制文件、八件核心产物和 locator 保持原 V1 断言。完整严格 JSON、receipt preimage、请求身份、全部 rows/status/pagination/coverage/field version、原父来源时钟、normalizer、原 run/input 的完整 hash 与正式导出交叉检查保持不变。恢复文件按新 ref4 执行打开前、descriptor、读后指纹及完整 SHA 检查，并计入原会话读取、文件、内存和时间预算。恢复清单和当前文件均参与每次完整末验，包括写事务提交前的最后末验。

证书保留原 V1 envelope，实际使用恢复条目的归档额外保存：

- `source_protocol = {profile, manifest_reference, manifest_sha256}`，profile 为本协议版本。
- `original_result_physical_ownership = LOST_NOT_RESTORED`。
- 每条来源的 `originals.result` 仍是原 ref4，`result_restoration` 另存新旧 ref4、丢失事实及 `SEALED_BYTES_AND_CURRENT_PHYSICAL_PIN`。

协议和清单身份进入 bridge ID、证书/绑定碰撞检查、capability、会话缓存/借用证明、重新认证和恢复专用作业 policy。全局清单允许多个归档；单次只为实际认证归档 scope 建立证明。未使用恢复条目的当前归档仍按原 V1 读取，证书顶层没有恢复协议，能够与恢复旧归档在显式恢复作业内共同使用。

Python 显式接口为 `ArchiveVerificationSession(restoration_manifest_ref=ref4)`、`commit_bridge(service, capability, restoration_manifest_ref=ref4)` 和 `RestoredResultDailyClosure(service, restoration_manifest_ref=ref4, verification_profile=...)`。恢复专用每日作业使用 `SHORT_HORIZON_RESTORED_RESULT_DAILY_V1`、kind `short_horizon_restored_result_daily_v1`、ledger `short_horizon_restored_result_daily_ledgers_v1`、ID 前缀 `short_restored_daily_`。它沿用当日收盘后的原每日准入，plan/worker 都绑定原归档 seal 不晚于 cutoff，同时要求恢复清单及实际使用恢复文件的当前 ctime 不晚于 cutoff。此元数据顺序仅是当前 pin 已存在的必要条件，不提升历史可见性。

CLI 对归档命令仅在同时显式给出 `--source-protocol SEALED_RESULT_BYTES_RESTORED_V1 --restoration-manifest <path> --restoration-manifest-sha256 <sha>` 时启用；每日使用 `r2-restored-daily-plan/run/status/retry`，同样必须给出三项。资源 profile 独立选择；显式 `FULL_ARCHIVE_900S_V1` 成功不构成原默认 300 秒性能验收。

默认 import/commit、重新认证、load/borrow、普通每日作业不能推断恢复授权。保存的 `AUTHENTICATED_ORIGINAL_CSV` 导出需要显式授权的 service，继续返回原封存字节，缺失或不匹配协议时拒绝，不回退为重生成。普通保存结果查询仍返回原数值产品；来源合同通过恢复作业 ledger、证书和 CSV provenance 单独审计，查询本身不构成 V1 重新验收。新合同必须在新独立 DB/state 中验收，不更新旧证书或生产服务。

恢复专用 class 对非 `TEST_FIXTURE` 服务只接受实际 `utc_now`；自定义时钟与构造后改时钟都拒绝。工程测试可以在独立 fixture 中使用显式合成 calendar/clock adapter，真实文件物理时间与来源校验仍保留。这种测试不扩展生产官方日历，也不构成真实每日验收。
