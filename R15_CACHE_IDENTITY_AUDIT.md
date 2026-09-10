# R15 Cache Identity Audit

Generated: 2026-09-10T17:40:10.615168+08:00

## Finding

R14 `DERIVED_FEATURE_STORE_V1` could not be a true cross-session daily cache. Its cache key bound every derived row to the whole `input_hash`, whole `history_artifact_hash`, and request `as_of_session`. Therefore Day T and Day T+1 miss even when the underlying historical feature content is unchanged.

## V1 key fields

`store_version`, `input_hash`, `history_artifact_hash`, `feature_spec_hash`, `strategy_dependency_version`, `as_of_session`, `instrument_id`, `session`, `comparable_basis`.

## Actual sample

Instrument/session: `000010.SZ` / `2026-09-07`; comparable basis: `UNAVAILABLE`. The compact source values and row hash are identical across Day T and Day T+1.

| run | input_hash | history_hash | as_of | V1 key | V2 PRICE_BASE key |
| --- | --- | --- | --- | --- | --- |
| T | `63f83798f5098898debce30e0810a0c54f6a0daffdc75ac6404d47d5f8488a23` | `b217908207aea1cd2531e49ba81b09d3a152b641d3517b897d61419129d42372` | `2026-09-08` | `derived_feature_ea11e1f9e3a5fd7ccea89ff1db57c4c92851b142dd9a4dcd7f9c48003d6f7846` | `derived_feature_v2_65a3a4d6e115793b1b2592224893493388563ceb439391b023ca606f6241b17a` |
| T+1 | `04d95df6f6f699c7f88353d181308159526865877a666a7c3efb84be01b9c08d` | `5c09fa5b2482ce527a7ffb7d986803eed80c206d11826bed1c75c6a4ee493248` | `2026-09-09` | `derived_feature_5d047542d85ed16f1d53ffa01822bceea49b27c99b115239e3619e1a99b7d2a3` | `derived_feature_v2_65a3a4d6e115793b1b2592224893493388563ceb439391b023ca606f6241b17a` |

V2 keeps `input_hash` and `history_artifact_hash` as observed audit fields, but they are not in the feature cache key. The V2 sample key is stable because the PRICE_BASE dependency fingerprint is the same: `341e95a0778be339dd5a90cb1c695bd271178e2bf02ed3e07c4b24ccb4051bee`.

## Real T+1 note

The real `2026-09-09` benchmark did not reuse full instrument-session rows: `reuse_ratio=0.0`, `invalidated=79040`. This is not caused by V1-style global input/as_of binding. It is caused by changed consumed historical source dependencies; for example `000001.SZ` / `2026-09-07` has a Day T HFQ comparable row that is absent from the Day T+1 artifact, so affected rolling features must be rebuilt.

`MARKET_EDGE=NOT_ESTABLISHED`.
