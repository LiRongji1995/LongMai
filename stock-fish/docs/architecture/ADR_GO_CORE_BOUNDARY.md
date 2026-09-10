# ADR: Go Core Boundary

Status: Accepted for R16 Phase 0.

## Decision

R16 introduces a strangler boundary: Go Core will provide versioned feature and
evidence facts, while Python Strategy remains the production decision engine.

The boundary is `FEATURE_EVIDENCE_CONTRACT_V1`: manifest JSON plus normalized
fact rows in JSONL and optional Parquet.

## Rationale

The Python data, feature and cache layers have become the main architecture
bottleneck, but a big-bang rewrite would create too much correctness risk.
The existing strategy behavior, thresholds and `STAGE_AWARE_MOMENTUM_V2`
semantics remain unchanged.

The contract prevents Python Strategy from depending on Go internals and
prevents Go Core from inheriting the current SQLite document/cache layout as a
hidden API.

## Why Not Shared SQLite Schema

SQLite tables currently encode Python implementation details: immutable
documents, history artifact partitions, cache keys, dependency fingerprints and
lineage rows. Sharing those tables with Go would freeze accidental internals and
make correctness harder to review.

The contract instead exposes resolved facts, statuses and provenance. Internal
storage can change behind that boundary as long as the contract and equivalence
gates hold.

## Why Not Big-Bang Rewrite

Daily Primary has live prospective-validation and audit semantics. Rewriting
ingestion, history, feature derivation and strategy in one step would mix
performance work with business semantics. R16 therefore only creates:

- canonical source resolution for Python cache correctness
- Contract V1
- Python baseline exporter
- normalized comparator
- Go skeleton and a tiny mechanical shadow slice

## Future Migration Order

1. Move canonical source resolution and history row selection into Go shadow
   mode.
2. Move rolling price/activity feature computation behind Contract V1.
3. Add store design that avoids duplicating current Python cache metadata and
   lineage overhead.
4. Run Python facts vs Go facts equivalence on frozen golden cases.
5. Only after equivalence and business gates pass, allow production strategy to
   consume Go-generated facts.

`MARKET_EDGE=NOT_ESTABLISHED` remains unchanged.
