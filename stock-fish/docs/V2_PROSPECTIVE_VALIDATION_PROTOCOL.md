# V2 Prospective Validation Protocol

- protocol_id: `VALIDATION_PROTOCOL_V2`
- strategy_id: `STAGE_AWARE_MOMENTUM_V2`
- implementation_version: `R10_STAGE_AWARE_MOMENTUM_V2_DATA_REPLAY_PROSPECTIVE_ACTIVATION`
- spec_revision: `R10_DATA_REPLAY_PROSPECTIVE_ACTIVATION`
- start_session: `None`
- primary_signal_cutoff: `20:30 Asia/Shanghai EVENING_FINAL`
- checkpoints: `[5, 20, 60, 120]`
- MARKET_EDGE: `NOT_ESTABLISHED`

Rules: append-only outcomes, no historical-to-prospective backfill, UNKNOWN remains separate from FAIL, and no tuning without explicit new authorization.
