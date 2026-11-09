# Project Review — Architect and Hot-Money Trader Perspectives (2026-11-09)

Scope: a full read-only review of the personal A-share short-term momentum ("yaogu") pre-market
recommendation system. Four questions were examined:

1. Is the strategy and information-collection design worth further optimization?
2. Is the post-trade review (复盘) detailed enough to drive strategy iteration?
3. Is there room to reduce time/space cost, especially the long pre-market list generation time?
4. What technical restructuring is recommended?

Method: code and documentation reading plus line-level spot checks of key claims. No profiling was
performed and no files were changed during the review. All paths are relative to `stock-fish/`
unless stated otherwise.

## 0. Overall verdict

Engineering effort and trading value are badly inverted. The repository holds ~46k lines of
production code and ~37k lines of tests. Of the ~200 commits in the last month, nearly all address
source authentication, repeated SHA verification, budget ledgers, one-shot continuation
authorizations, no-retry failure retention and UNKNOWN handling. The strategy itself has never been
changed or validated: every config still declares `MARKET_EDGE=NOT_ESTABLISHED`.

For a personal short-term tool this is bank-grade auditing guarding a strategy that has not yet been
shown to make money.

## 1. Strategy and information collection (trader view)

### 1.1 Only R2 runs daily, and R2 has no limit-up logic

- The pre-market workflow is hard-wired to R2 (`stockfish_core/yaogu/premarket_workflow_v1.py:27`).
  DAILY_V1 is hard-coded as `'UNKNOWN_NOT_EXECUTED'` (`premarket_workflow_v1.py:154`).
- The hot-money logic that exists (sentiment cycle, limit-up ladder, 1→2 / 2→3 board relays) lives in
  DAILY_V1 and V2, neither of which runs in the pre-market path.
- R2 is three OR-ed price/volume patterns (STRONG_START, STRONG_CONTINUATION, FIRST_PULLBACK) plus a
  "turnover ≥5% in one of the last 5 sessions" gate (`config/short_horizon_strength_r1.yaml`,
  `config/short_horizon_strength_r2.yaml`).
- Output is grouped by sector and sorted by **security code**; the spec says "not a return ranking"
  (`docs/SHORT_HORIZON_STRENGTH_R1.md:19`). FOCUS is zero by design (`short_horizon_strength_r2.yaml:44-45`).

Result: the user receives ~26 unranked research matches with no priority, entry trigger,
invalidation level or position guidance.

### 1.2 V2 gates exclude typical hot stocks

- A hard `STRONG_UPTREND` gate requires MA60/120/250 all rising (`stockfish_core/yaogu/stage_aware_momentum.py:808`).
  Most hot stocks start below the 250-day MA and are rejected.
- V2 requires an LHB seat-level funds grade, but seat detail is never collected, so V2 can
  effectively never reach FOCUS.
- Fixed 500M CNY amount and 7% turnover floors ignore float market cap.

### 1.3 Data a hot-money trader relies on

| Item | Status |
|---|---|
| Limit-up / failed-board / limit-down pools (first seal time, board openings, seal amount, board count) | Not collected in pre-market. `stock_zt_pool_zbgc_em` is only named in `providers.py:33` |
| Call auction 09:15–09:25 | Missing. `auction.py` thresholds exist but are used only post hoc |
| LHB seat detail / known hot-money desks | Only a yes/no "listed" clue |
| Theme heat / sector co-movement | Cache-only; latest real run UNKNOWN |
| Sentiment cycle (limit-up count, max board height, yesterday's limit-ups today) | Implemented (`emotion.py`, V2 market state) but not run pre-market |
| Announcement catalysts | 8 articles from 2 sites, not linked to stocks |
| Float market cap | Absent from the pre-market path |
| Turnover | Present (BaoStock `turn`) |

### 1.4 Validation

All parameters are `INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED`. A walk-forward design is specified in
`config/yaogu_v1.defaults.yaml:46-62`, but the cited `evaluation.py` does not exist and no executed
backtest was found. There is no parameter-calibration loop.

### 1.5 Recommendations (prioritized)

1. **Sentiment-cycle master switch first.** Compute limit-up/limit-down counts, failed-board rate,
   max board height and yesterday's limit-up follow-through from bars already collected; decide
   attack / defend / stand aside before any stock selection. Zero new collection.
2. **Collect the three limit pools daily** (`stock_zt_pool_em`, `stock_zt_pool_zbgc_em`,
   `stock_zt_pool_dtgc_em`): three requests yield the board ladder, seal times and seal amounts.
3. **Re-rank after the 09:25 auction**: auction gap and auction amount ÷ previous-day amount as the
   actual entry trigger.
4. **One ranked, actionable list**: top 5–10 names tiered as leader / follower / first board, each
   with theme, board height, entry condition and invalidation level.
5. **Relax V2's MA250 gate**; make the funds-linkage optional until seat data exists.
6. **Collect LHB seat detail** and derive per-seat hit rates from history rather than a hand-written list.

## 2. Post-trade review (复盘)

### 2.1 What exists

Strong provenance: no look-ahead, append-only revisions, missing outcomes never counted as zero.
DAILY_V1's evaluator (`stockfish_core/yaogu/outcomes.py`) is rich: T+1/2/3/5/10, MFE/MAE, one-price
(一字) boards, structure labels.

### 2.2 Critical gaps

- **The list actually used (R2) gets the thinnest review.** `short_horizon_outcomes_v1.py` stores no
  limit-up, failed-board (炸板) or one-price fields, although `events.limit_event` already computes
  them (`stockfish_core/yaogu/events.py:25-30`).
- Returns are measured from the T close; the realistic entry for a pre-market list is the T+1 open.
  No costs or slippage.
- **No cross-day aggregation for R2.** `'distinct_signal_dates': 1` is hard-coded
  (`short_horizon_outcomes_v1.py:314`), so no rolling hit rate exists.
- No trading metrics: payoff ratio, expectancy, equity curve, max drawdown, losing streak. No index benchmark.
- No attribution by pattern, theme, sentiment state or score bucket. No near-miss records and no
  "rejected by gate X" cohorts, so each filter's value cannot be measured.
- Existing rollups (`recommendation_scores`, `strategy_review`, `daily_recommendation_analysis`)
  have no CLI entry; the owner sees raw JSON.
- No sensitivity, walk-forward or ablation code anywhere.

### 2.3 Recommendations

1. A read-only `review` command exporting CSV per (signal date, stock, pattern, horizon) plus daily
   and rolling summary tables.
2. Add to R2 outcomes: limit-up / failed-board / one-price flags, an "unbuyable at T+1 open" flag,
   returns from the T+1 open, and configurable transaction costs.
3. Attach attribution dimensions to each outcome row (pattern, theme, sentiment state, board height,
   score quintile) and report rank IC.
4. Record near-misses and the failing gate with its distance to threshold.
5. An offline parameter-grid + walk-forward harness writing separately versioned experiment results,
   never touching frozen strategies.

## 3. Time and space complexity

The 2026-11-09 refresh took 1129.6 s (18 m 50 s) while making only 6–7 new network requests, so
nearly all of it was local overhead.

1. **Cache capacity scan.** During the capture phase, every 0.5 s `Budget.check` walks the whole
   cache directory (~2.5 s per scan over 578 MB; `stockfish_core/yaogu/premarket_runtime_v1.py:509-521`).
   In the worst case this can consume up to ~80% of capture-phase wall time. News and theme-source
   reads also run under the capture phase. (Estimated from code; confirm with the job's
   `worker-completion.json` → `phase_timings` before optimizing.)
2. **Repeated verification.** Each core raw file is fully read and SHA-hashed ~15 times end-to-end and
   JSON-parsed 8–10 times (preflight twice, terminal checks three times, `owner.validate()` twice,
   result wrappers duplicating raw rows).
3. **Pure-Python object model.** 130k bars as pydantic objects with ~30 `Decimal` fields; a wildcard
   `field_validator('*')` runs ~4M times; strategy math uses `Fraction`. No numpy/pandas and no
   cross-day feature store: each day recomputes 25 sessions from raw JSON. Core: 293 s, 2.16 GB peak RSS.
4. **Adjustment factors re-downloaded from 1990 on every new trading day**
   (`stockfish_core/yaogu/collection_factor_scope.py:40`): ~3,050 codes, ~61 subprocesses, strictly
   sequential, a fresh process + BaoStock login per batch. Estimated 15–45 min per new trading day —
   the largest cost of cold and new-day runs.

Estimated effect:

- Items 1–2 plus removing redundant deepcopies/digests: same-day refresh from ~19 min to ~6–8 min,
  without touching strategy.
- Incremental factors (tail since last date, or only codes with ex-rights events, one BaoStock
  session) with a weekly full reconciliation: saves 15–40 min per new trading day.
- Columnar per-session store (parquet/duckdb) + vectorized features: core under 1 min, memory ~200 MB.
  Risk: float vs `Fraction` can change ties; requires an equivalence check against frozen outputs.

## 4. Recommended technical direction

**Principle: relax trust by one level.** A personal tool needs three invariants — point-in-time
correctness (no look-ahead), explicit missing data, and reproducibility. Multi-stage
re-authentication, 0.5 s capacity patrols, one-shot claim/continuation authorization chains and the
subprocess supervisor tree can go.

Roadmap:

1. **Week 1 — speed, no strategy change.** Section 3 quick wins, validated with real `phase_timings`.
   Target: same-day list in < 8 min.
2. **Week 2 — data layer.** Raw data stored once with a single SHA; a persistent columnar session
   store; incremental factors with weekly reconciliation; add the three limit pools and auction data.
3. **Weeks 3–4 — review first.** `review` command, R2 outcome fields, near-miss and attribution;
   replay 120–250 historical sessions to measure whether R2 and V2 have any edge under a
   T+1-open entry.
4. **Then — strategy iteration.** Sentiment switch, auction re-ranking, unified tiered ranking,
   relaxed V2 gates; every change registered as a new version and validated walk-forward.
5. **Cut complexity.** Remove execution paths unused by the pre-market flow; retire the
   continuation/recovery machinery. Expected to remove more than half of the code.

**The single most important first step:** replay R2 on existing historical daily bars and measure
expectancy after costs for "buy at T+1 open, sell at T+2". If negative, no engineering optimization
matters; if positive, proceed in the order above.

## Operational note (2026-11-10 cold start)

A fresh checkout with an empty cache cannot pass `premarket-plan`: the preflight requires an
authenticated trading calendar before admitting any collection, while the empty cache has none
(`AUTHENTICATED_CURRENT_CALENDAR_REQUIRED`). Missing `state/cache/{index,raw,raw-index,locators}`
directories also surface as the opaque `LOCAL_DEPENDENCY_OR_RESPONSE_SCHEMA_ERROR`, because
`codex_daily_v2._safe_reason` masks non-code exception messages. Both are first-run usability defects.
