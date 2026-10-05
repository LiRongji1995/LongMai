> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../CURRENT_STATUS.md)；原路径可在[归档索引](README.md)查找。

# R15 Derived Store Growth Audit

Generated: 2026-09-10T17:40:10.615168+08:00

## Measured storage

| metric | bytes / rows |
| --- | ---: |
| Day T V2 store size | 1,369,141,248 bytes |
| Day T+1 V2 store size | 2,711,740,416 bytes |
| Incremental DB growth | 1,342,599,168 bytes |
| Day T V2 rows | 237,198 family rows / 79,066 instrument-session rows |
| Day T+1 V2 rows | 468,238 family rows / 82,106 instrument-session rows |
| Incremental payload growth | 170,606,517 bytes |

The actual Day T+1 run rebuilt nearly a full second copy because historical consumed source dependencies changed broadly. This is `NOT_MEETING_R15_SLA`; it is not acceptable as the normal steady-state growth pattern.

## Annual estimates

These are estimates, not a measured full year.

| model | basis | estimated annual growth |
| --- | --- | ---: |
| V1 current model | R14 payload 144,886,863 bytes/day x 250 sessions | 36,221,715,750 bytes payload, about 33.73 GiB |
| V2 actual measured T->T+1 | 1,342,599,168 DB bytes x 250 sessions | 335,649,792,000 bytes DB, about 312.60 GiB |
| V2 ideal append-only steady state | approx 9,120 new family rows/day from one new session | about 13,160,490,500 bytes DB/year, 12.26 GiB |

## Lifecycle recommendation

Keep V2 cache reconstructable and separate from source truth. Future work should add a bounded active recent cache, optional compaction, and a cleanup policy for obsolete dependency revisions, but this R15 task does not delete production cache or historical artifacts.

`MARKET_EDGE=NOT_ESTABLISHED`.
