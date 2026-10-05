> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../CURRENT_STATUS.md)；原路径可在[归档索引](README.md)查找。

# R15 Performance Result

Generated: 2026-09-10T17:40:10.615168+08:00

Classification: `NOT_MEETING_R15_SLA`.

R15 implements Derived Feature Store V2 and removes the measured percentile O(N^2) hotspot, but the real 2026-09-08 -> 2026-09-09 benchmark did not achieve true daily incremental performance because historical consumed source dependencies changed broadly.

## Four-way benchmark

| class | total | bind | derived dependency | derived lookup | derived build/write | strategy compute | persist | peak RSS | cache reuse |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| LEGACY/R14 cold | 179.799s | n/a | n/a | n/a | n/a | n/a | n/a | 2,865,180,672 | 0/79,066 |
| LEGACY/R14 same-input warm | 59.760s | 16.408s | n/a | 3.090s | 0s | 32.274s | 6.139s | 992,960,512 | 79,066/79,066 |
| R15 cold | 433.499s | 17.490s | 74.933s | 3.764s | 278.491s | 396.371s | 10.761s | 1,930,452,992 | 0/79,066 |
| R15 same-snapshot warm | 182.878s | 35.312s | 104.891s | 10.840s | 0.035s | 136.616s | 6.032s | 2,476,277,760 | 79,066/79,066 |
| R15 TRUE_NEXT_SESSION_INCREMENTAL | 263.098s | 23.983s | 73.460s | 6.530s | 135.843s | 229.297s | 5.436s | 1,736,269,824 | 0/79,040 |

`strategy_compute` includes derived dependency/lookup/build time. SQLite query count and rows read were not measured (`sqlite_trace_not_enabled`); T+1 family rows written: 231,040.

## T+1 cache statistics

- requested: 79,040
- reused full instrument-session rows: 0
- newly built instrument-session rows: 79,040
- invalidated instrument-session rows: 79,040
- reuse ratio: 0.000000
- family hits/misses: 6,080 / 231,040
- integrity conflicts: 0

## Percentile runtime

- BEFORE R14 cProfile: `53.736s` cumulative under `percentile_rank_midpoint`.
- AFTER R15 microbenchmark: `0.019584s` for 3041 population and 6082 lookups, exact legacy equality true.

## Storage

- Day T V2 store size: 1,369,141,248 bytes.
- Day T+1 V2 store size: 2,711,740,416 bytes.
- Incremental DB growth: 1,342,599,168 bytes.
- Estimated annual V1 payload growth: 36,221,715,750 bytes (33.73 GiB).
- Estimated annual V2 actual DB growth from this T->T+1: 335,649,792,000 bytes (312.60 GiB), not acceptable as steady state.

## Business equivalence

R14 frozen Day T reference business hash: `38fcca80d6b03d860ac32031a3e1d66cf74151e3f5a8441cd337a803bcf859ef`.

- R15 cold Day T hash: `38fcca80d6b03d860ac32031a3e1d66cf74151e3f5a8441cd337a803bcf859ef`.
- R15 same-snapshot warm Day T hash: `38fcca80d6b03d860ac32031a3e1d66cf74151e3f5a8441cd337a803bcf859ef`.
- Equal: `True`.

No strategy thresholds, setup semantics, focus gates, or score formula were changed. `MARKET_EDGE=NOT_ESTABLISHED`.
