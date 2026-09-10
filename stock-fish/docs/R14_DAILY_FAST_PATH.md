# R14/R15 Daily Fast Path 与性能架构整改

核对日期：2026-09-10（Asia/Shanghai）。

R14/R15 是性能架构整改，不是策略开发。`STAGE_AWARE_MOMENTUM_V2` 的交易语义、Focus/Watch/Rejected/Unknown 判定、排序、阈值、Setup、`DAILY_RECOMMENDATION_SCORE_V1` 公式和 `MARKET_EDGE=NOT_ESTABLISHED` 均保持不变。

## 执行路径

日常正式荐股默认走 `DAILY_PRIMARY_FAST`：

```text
冻结 MarketInput / history artifact
  -> 资格与 PIT 上下文
  -> DERIVED_FEATURE_STORE_V2 读取或补建确定性特征
  -> 只执行 ACTIVE STAGE_AWARE_MOMENTUM_V2
  -> compact canonical run + summary projection + per-instrument detail
  -> tracking/outcome demand
  -> score/outcome 后置追加
```

普通“下一交易日荐股”“今天 V2 名单”“盘后正式名单”和 20:30 official primary 均应使用 `/api/daily-primary/run` 或 `CandidateService.submit_daily_primary_strategy()`。

完整 `RESEARCH_FULL` 路径仍保留给明确的研究/比较请求：PV、DNA、THEME、THEME_CAPITAL、Simple Trend、V1/V2 comparison 与大 diagnostics。该路径可以慢，但不得阻塞 daily primary。

## Derived Feature Store

R14 的 `DERIVED_FEATURE_STORE_V1` 是可删除、可重建的派生缓存，不是第二套行情权威库。权威仍为 MarketInput、history artifact、来源证据和 PIT 可见性。R15 新写入 `DERIVED_FEATURE_STORE_V2`，V1 在 daily fast path 中作为 legacy ignored/read-only 保留，不删除生产旧缓存，也不改写历史 run。

V1 缓存 identity 绑定：

- `input_hash`
- `history_artifact_hash`
- `feature_spec_hash`
- `strategy_dependency_version`
- `instrument_id`
- `session`
- `as_of_session`
- `comparable_basis`
- PIT policy / comparable policy

任一 V1 identity 变化都会 cache miss 或 rebuild，所以 R14 的 same-input warm 不能作为 true daily incremental 证据。

V2 缓存内容仍是 deterministic derived facts，例如 comparable close、ret1/3/5/10/20、MA5/10/20/60/120/250、成交额滚动事实和换手/活跃度 primitive；不缓存最终 FOCUS、Setup PASS 或排名来绕过策略计算。V2 key 不再绑定 whole `input_hash`、whole `history_artifact_hash` 或 request `as_of_session`，而绑定 `feature_family`、family spec hash、`instrument_id`、`feature_session`、required source session range 与该 instrument/source window 的 source dependency fingerprint。

V2 已分层：`PRICE_BASE`、`ROLLING_PRICE`、`ACTIVITY`、`SUPPLY`、`MARKET_RELATIVE`、`SECTOR_RELATIVE`。其中仅 `PRICE_BASE`、`ROLLING_PRICE`、`ACTIVITY` 当前 materialized；`SUPPLY`、`MARKET_RELATIVE`、`SECTOR_RELATIVE` 属于 PIT/as-of dependent family，未作为跨 as-of price cache materialized。feature spec 与 strategy decision spec 已分离，修改 Focus gate 或 ACCELERATION threshold 不会让 MA/ret/activity cache spec hash 自动变化。

V2 `put_many()` 为 immutable insert/reuse：同 key 同 payload hash 复用；同 key 不同 payload hash 记录 `DERIVED_FEATURE_INTEGRITY_CONFLICT` 并 raise，不静默覆盖。历史 replay 使用 feature session/source sessions 约束，禁止用今天完整 history 偷看未来。

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

R14 结果：冷 fast path 179.799 秒、same-input warm 59.760 秒（含隔离 compact persistence/projection 6.139 秒），热路径 peak RSS 992,960,512 bytes；V2 business hash 与 saved research full 完全一致。这里的 same-input warm 不是 true daily incremental。

R15 实测见 [R15_CACHE_IDENTITY_AUDIT.md](../../R15_CACHE_IDENTITY_AUDIT.md)、[R15_TRUE_INCREMENTAL_ARCHITECTURE.md](../../R15_TRUE_INCREMENTAL_ARCHITECTURE.md)、[R15_PERCENTILE_COMPLEXITY_AUDIT.md](../../R15_PERCENTILE_COMPLEXITY_AUDIT.md)、[R15_PERFORMANCE_RESULT.md](../../R15_PERFORMANCE_RESULT.md) 与 [R15_DERIVED_STORE_GROWTH_AUDIT.md](../../R15_DERIVED_STORE_GROWTH_AUDIT.md)。

R15 消除了 R14 cProfile 中 `percentile_rank_midpoint` 的 O(N²) 热点，旧 6082 次 midpoint scan 约 53.736 秒，R15 3041 population/6082 lookups 微基准为 0.019584 秒且逐项等价。真实 2026-09-08 -> 2026-09-09 benchmark 完成，但因历史 consumed source dependencies 大面积变化，T+1 `reuse_ratio=0.0`、总时长 263.098 秒、peak RSS 1,736,269,824 bytes，最终标记 `NOT_MEETING_R15_SLA`。冷启动和 provider/backfill 不计入 daily SLA，网络等待单独看作 external wait。
