# R16-R1 strict compatibility revision

This section supersedes the historical R16 description below. Schema revision is `FEATURE_EVIDENCE_SCHEMA_R16_R1`; old structurally plausible snapshots must pass independent validation and binding again. JSONL+manifest retain authoritative values and full provenance. Display CSV/Parquet are not equivalence inputs. Unknown fact extensions and duplicate object/logical keys are rejected.

`ret1_pct` is percent (4 decimal HALF_EVEN), OHLC 8 decimals, MA 6 decimals. Optional reduced-rational `exact_value` preserves the internal production value used by the test-only Strategy injection; its rounded display representation is independently checked. A tolerance never bypasses identity, unit, status, reason, exact value or source provenance.

Missing/null facts remain null+UNKNOWN. KNOWN requires the explicit verified source/PIT rules and an aware visible clock at or before the cutoff; a nonempty provenance object alone is insufficient. Manifest revisions bind actual calendar/source/spec identities. See [R16-R1 engineering policy](../R16_R1_ENGINEERING.md), [production oracle](../R16_R1_PRODUCTION_ORACLE.md), and the executable schema for field types, permitted metadata differences and slice limits. No strategy label migrates to Go.

---

# Feature Evidence Contract V1

Status: R16 Phase 0 boundary. Production Daily Primary still runs in Python.

`FEATURE_EVIDENCE_CONTRACT_V1` is the versioned boundary for future Go Core to
provide facts to Python Strategy. It is not a strategy score format and it is
not a copy of Python or Go internal storage.

## Boundary

Go Core provides facts. Python Strategy interprets facts.

Python Strategy must not depend on Go-internal database schema, provider cache
paths, BaoStock layouts, history artifact table shape, cache key formats, factor
merge implementation, or how a rolling value such as MA250 is computed.

## Physical Form

R16 writes:

- `feature_evidence_manifest_v1.json` for snapshot metadata.
- `feature_evidence_facts_v1.jsonl` for normalized field facts.
- `feature_evidence_facts_v1.parquet` when `pyarrow` is available.

The canonical schema is `schemas/feature_evidence_contract_v1.schema.json`.
SQLite internals are intentionally excluded from the contract.

## Snapshot Metadata

The manifest contains:

- `contract_version`
- `snapshot_id`
- `as_of_session`
- `decision_cutoff`
- `created_at`
- `universe_version`
- `universe_hash`
- `calendar_revision`
- `source_policy_revision`
- `feature_spec_revision`
- `source_manifest_hash`
- `provenance_root_hash`
- `builder_version`
- `coverage_summary`

## Instrument And Fact Rows

Each fact row contains:

- `instrument`
- `exchange`
- `session`
- `eligibility`
- `eligibility_reason`
- `field_group`
- `field`
- `value`
- `value_json`
- `status`
- `unit`
- `provenance`

Current field groups are `PRICE`, `RETURN`, `ROLLING`, `ACTIVITY`, `MARKET`,
`SECTOR`, `CAPITAL`, and `EVENT`.

## Current Fields

Price facts:

- `open`
- `high`
- `low`
- `close`
- `prev_close`
- `comparable_price`
- `price_basis`

Rolling facts:

- `MA20`
- `MA60`
- `MA120`
- `MA250`

Relative/activity facts:

- `ret1_pct`
- `ret3_pct`
- `ret5_pct`
- `ret10_pct`
- `ret20_pct`
- `amount_cny`
- `turnover_fraction`
- `activity`
- `A`
- `relative_strength_status`
- `RS_market_1d_pct`
- `RS_sector_1d_pct`

Market/sector/capital facts:

- `market_context_state`
- `market_compatibility_status`
- `sector_status`
- `funds_status`
- `capital_evidence_status`

Event extension facts:

- `limit_event_status`
- `close_limit_up`
- `touched_limit_up`
- `close_limit_down`

The event group is a versioned extension point for future limit, theme, auction,
capital and temporal events. R16 does not invent missing event data.

## Status Semantics

- `KNOWN`: value is populated from visible resolved evidence.
- `UNKNOWN`: evidence needed by the field is absent or insufficient.
- `NOT_APPLICABLE`: field is structurally not applicable to the row.
- `UNVERIFIED`: value exists but field-level provenance is not yet strong enough
  for `KNOWN`.

Missing values must not be converted to `0`. Missing events must not be
converted to `false`.

## Provenance Semantics

Every important field or field group carries a provenance object. R16 provenance
includes source policy revision, input hash, history artifact identity and the
originating Python strategy field group. Future Go rows must include the
resolved source row/revision identifiers used by Go Core.

## Equivalence

R16 comparator normalizes by:

`instrument + session + field_group + field`

It compares value JSON, status and provenance hash. Numeric tolerance is allowed
only when explicitly supplied to the comparator. The default is exact equality.

Business equivalence remains a separate gate:

Python facts -> Python Strategy -> Result A

Go facts -> same Python Strategy -> Result B

The gate must compare `FOCUS`, `WATCH`, `REJECTED`, `UNKNOWN`, setup, ranking,
important reasons and result hashes.
