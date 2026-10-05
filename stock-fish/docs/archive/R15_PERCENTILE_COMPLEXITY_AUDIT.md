> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../CURRENT_STATUS.md)；原路径可在[归档索引](README.md)查找。

# R15 Percentile Complexity Audit

Generated: 2026-09-10T17:40:10.615168+08:00

## Optimized hotspot

| location | old complexity | new complexity | equivalence |
| --- | --- | --- | --- |
| `stage_aware_momentum._limit_market_status` ret10/ret20 market percentile | per stock repeated population scan, about `O(N^2)` | one percentile map per signal day/window, `O(N log N)` sort plus `O(1)` lookup | covered by legacy-vs-map tests for ties, ordering, Decimal/Fraction and 3041 population |
| `stage_aware_momentum._stage_high_level_repair` pre-event ret10 percentile | repeated market population scan inside per-stock stage checks | run-local cached percentile map keyed by eligible universe/day/window | same midpoint lookup function as legacy |

## Runtime evidence

R14 cProfile measured `percentile_rank_midpoint` under `_limit_market_status` at `53.736s` cumulative for 6082 calls.

R15 microbenchmark with 3041 values and 6082 lookups:

- legacy midpoint scan: `9.861471s`
- map build plus all lookups: `0.019584s`
- speedup: `503.5x`
- exact equality: `True`

## Not rewritten

A search found sector/theme rank and percentile-style logic, but the R14 profile did not show it as the dominant daily fast-path hotspot and the real R15 runs had `visible_theme_count=0`. Those paths were audited and left unchanged to avoid widening this performance-only task into strategy logic changes.

`MARKET_EDGE=NOT_ESTABLISHED`.
