> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../CURRENT_STATUS.md)；原路径可在[归档索引](README.md)查找。

# R15 True Incremental Architecture

Generated: 2026-09-10T17:40:10.615168+08:00

R15 is a performance/cache correctness change only. It does not change `STAGE_AWARE_MOMENTUM_V2` trading semantics, `DAILY_RECOMMENDATION_SCORE_V1`, gates, thresholds, setup ranking, or market/sector/capital meanings.

## Derived Feature Store V2

`DERIVED_FEATURE_STORE_V2` writes a new table, `yaogu_derived_features_v2`, separate from V1. V1 remains legacy ignored/read-only for this path; no production cache or historical run is deleted.

V2 materializes only cross-as-of-safe deterministic price facts:

- `PRICE_BASE`: comparable close / price primitive identity.
- `ROLLING_PRICE`: ret windows and MA windows.
- `ACTIVITY`: amount, turnover, rolling amount/turnover facts.

The family registry also names `SUPPLY`, `MARKET_RELATIVE`, and `SECTOR_RELATIVE`, but they are not materialized as cross-as-of-safe derived price cache rows. PIT/as-of-dependent families must carry their own policy identity before any future materialization.

## Cache identity

V2 key policy: `FEATURE_SPEC_INSTRUMENT_FEATURE_SESSION_REQUIRED_SOURCE_RANGE_DEPENDENCY_FINGERPRINT`.

Each family row binds:

- `feature_family`, `feature_spec_id`, `family_spec_hash`
- `instrument_id`
- `feature_session`
- `required_source_start/end` and source session range
- exact source-data dependency fingerprint using the history row-hash set for the instrument/source sessions in the consumed window
- comparable basis policy identity
- PIT/source-policy identity
- provider/source revision identity

The request `input_hash`, whole `history_artifact_hash`, and request `as_of_session` are audit fields, not cache key fields.

## Immutable cache semantics

`put_many()` is insert-only for a dependency key:

- missing key: `INSERT`
- same key and same payload hash: reuse existing row
- same key but different payload hash: record `yaogu_derived_feature_integrity_conflicts_v2` and raise `DERIVED_FEATURE_INTEGRITY_CONFLICT`

The cache remains reconstructable; MarketInput, history artifact, and source evidence remain authoritative.

## PIT / future replay

Feature sessions and dependency sessions are bounded to `<= feature_session`. A future replay test first computes a newer day, then replays Day T and requires the Day T Stage-Aware slice to match a cold Day T run. Unsafe PIT-dependent families are not cross-as-of materialized.

## R15 status

Architecture goals are implemented, but measured real 2026-09-08 -> 2026-09-09 data does not prove a fast successful daily incremental path. Historical consumed dependencies changed broadly, so V2 safely invalidated rather than reused. Final classification: `NOT_MEETING_R15_SLA`.

Top remaining hotspots:

1. Dependency plan generation for 79k instrument-session rows: 73-105s in measured segments.
2. Build/write on broad real invalidation: 135.8s on T+1 and 278.4s cold.
3. MarketInput load/bind: 17-35s on Day T, 24s on T+1.

`MARKET_EDGE=NOT_ESTABLISHED`.
