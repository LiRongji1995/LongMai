# R14 Daily Fast Path 与性能架构整改

核对日期：2026-09-10（Asia/Shanghai）。

R14 是性能架构整改，不是策略开发。`STAGE_AWARE_MOMENTUM_V2` 的交易语义、Focus/Watch/Rejected/Unknown 判定、排序、阈值、Setup、`DAILY_RECOMMENDATION_SCORE_V1` 公式和 `MARKET_EDGE=NOT_ESTABLISHED` 均保持不变。

## 执行路径

日常正式荐股默认走 `DAILY_PRIMARY_FAST`：

```text
冻结 MarketInput / history artifact
  -> 资格与 PIT 上下文
  -> DERIVED_FEATURE_STORE_V1 读取或补建确定性特征
  -> 只执行 ACTIVE STAGE_AWARE_MOMENTUM_V2
  -> compact canonical run + summary projection + per-instrument detail
  -> tracking/outcome demand
  -> score/outcome 后置追加
```

普通“下一交易日荐股”“今天 V2 名单”“盘后正式名单”和 20:30 official primary 均应使用 `/api/daily-primary/run` 或 `CandidateService.submit_daily_primary_strategy()`。

完整 `RESEARCH_FULL` 路径仍保留给明确的研究/比较请求：PV、DNA、THEME、THEME_CAPITAL、Simple Trend、V1/V2 comparison 与大 diagnostics。该路径可以慢，但不得阻塞 daily primary。

## Derived Feature Store

`DERIVED_FEATURE_STORE_V1` 是可删除、可重建的派生缓存，不是第二套行情权威库。权威仍为 MarketInput、history artifact、来源证据和 PIT 可见性。

缓存 identity 绑定：

- `input_hash`
- `history_artifact_hash`
- `feature_spec_hash`
- `strategy_dependency_version`
- `instrument_id`
- `session`
- `as_of_session`
- `comparable_basis`
- PIT policy / comparable policy

任一 identity 变化都会 cache miss 或 rebuild。缓存内容为 deterministic derived facts，例如 comparable close、ret1/3/5/10/20、MA5/10/20/60/120/250、成交额滚动事实、换手/活跃度/上影线/前高收复等 primitive；不缓存最终 FOCUS、Setup PASS 或排名来绕过策略计算。

第一版 daily incremental 语义是按当前 signal day 只确保 V2 消耗窗口所需的目标 session 行存在，缺失则补建，已有则命中；冷启动可在独立临时 cache 上重建，不能删除生产数据。历史 replay 使用同一 key 空间，但必须绑定各自历史 cutoff/history identity，禁止用今天完整 history 偷看未来。

## Compact Persistence

`DAILY_PRIMARY_FAST` 保存瘦身 canonical run：

- run header：策略身份、input/history hash、cutoff、counts、coverage、source/config identity；
- summary projection：分页列表读取，不反序列化 full run；
- item detail：每只证券的解释明细压缩存入 `strategy_experiment_item_details`，详情 API 按需展开；
- heavy diagnostics：不重复塞进 daily primary 主文档，research full path 仍可保留。

## 基准证据

冻结 benchmark input：

- saved full run：`strategy_experiment_314b72ca9c6e714267d968c50e6db392ab6b39917a8f5ae029816c5a1fcca78e`
- input：`market_63f83798f5098898debce30e0810a0c54f6a0daffdc75ac6404d47d5f8488a23`
- observation date：`2026-09-08`
- cutoff：`2026-09-09T08:56:58.518020+00:00`
- history artifact hash：`b217908207aea1cd2531e49ba81b09d3a152b641d3517b897d61419129d42372`
- eligible：3041
- Research Full V2 business hash：`38fcca80d6b03d860ac32031a3e1d66cf74151e3f5a8441cd337a803bcf859ef`

实测报告：

- [R14_PERFORMANCE_BASELINE.md](../../R14_PERFORMANCE_BASELINE.md)
- [R14_PERFORMANCE_RESULT.md](../../R14_PERFORMANCE_RESULT.md)
- [BEFORE_AFTER_RESULT_EQUIVALENCE.md](../../BEFORE_AFTER_RESULT_EQUIVALENCE.md)
- [DATA_READINESS.md](../../DATA_READINESS.md)
- [SQLITE_SIZE_AUDIT.md](../../SQLITE_SIZE_AUDIT.md)
- [R14_CPROFILE_SUMMARY.md](../../R14_CPROFILE_SUMMARY.md)

当前结果：冷 fast path 179.799 秒、热 cache daily fast path 59.760 秒（含隔离 compact persistence/projection 6.139 秒），热路径 peak RSS 992,960,512 bytes；V2 business hash 与 saved research full 完全一致。冷启动和 provider/backfill 不计入 daily SLA，网络等待单独看作 external wait。
