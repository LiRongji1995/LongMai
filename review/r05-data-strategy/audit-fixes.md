# R05 缺陷关闭

| 缺陷 | 处理结果 |
| --- | --- |
| E01 时间/限价日期反例 | Eastmoney `f86` 只按 Asia/Shanghai 业务日期接受；规范化层再次检查 `source_business_date` 与 `source_timestamp`。UTC 日期碰巧一致但上海日期不一致的行被拒绝。 |
| E02 批次超时保留成功子项 | worker 每处理一只证券写 `worker-partial.json`；父进程超时时读取部分结果并返回 `PARTIAL / REQUEST_TIMEOUT_AFTER_PARTIAL`，成功子项不丢失。 |
| E03 规则认证服务不能由纯数学自报 VERIFIED | `derive_verified_limits` 不再接受单独 `branch_cross_validated=True`；必须由 `certify_rule_derived_limits` 生成本地认证哈希并携带直接样本。 |
| E04 summary 不能先反序列化大 CandidateRun | 候选任务发布时同步写 `candidate_run_summaries` 投影；summary API 先校验投影与发布权威，只在旧记录缺投影时回退完整读取。 |
