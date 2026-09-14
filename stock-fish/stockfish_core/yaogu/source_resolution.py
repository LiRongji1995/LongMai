"""Canonical source resolution for feature dependency identity and payloads.

R16 makes the selected price source view the shared boundary between dependency
planning and feature computation.  A feature cache key and the resulting payload
must be derived from the same resolved rows.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json

from ..storage import IntegrityError
from .contracts import digest
from .history_artifacts import daily_bar_payload, history_receipts


CANONICAL_SOURCE_RESOLVER_VERSION = "CANONICAL_RESOLVED_SOURCE_VIEW_V2_R16_R1_PIT_INSTANT"
PRICE_SOURCE_REQUIREMENT = "PRICE_DAILY_BAR_WITH_COMPARABLE_AND_ACTIVITY_FIELDS"
MISSING_VISIBLE_BAR = "MISSING_VISIBLE_BAR"
CONFLICTING_PRICE_REVISION = "CONFLICTING_PRICE_REVISION"

PRICE_VALUE_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "reference_prev_close",
    "comparable_open",
    "comparable_high",
    "comparable_low",
    "comparable_close",
    "comparable_basis",
    "volume_shares",
    "amount_cny",
    "turnover_fraction",
    "limit_up",
    "limit_down",
    "limit_applicable",
    "limit_verified",
    "rule_id",
    "quality",
)
REVISION_FIELDS = ("source_published_at", "ingested_at", "first_seen_at")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False)


def _date_text(value):
    return value.isoformat() if isinstance(value, date) and not isinstance(value, datetime) else str(value)[:10]


def _text(value):
    if value is None:
        return None
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    return value


def _row_hash(value, provided=None):
    return provided or digest(value)


def _revision_key(row):
    # Offset-bearing timestamps must sort by instant. Keep the original text in
    # source_revision; only the comparison key is normalized to UTC.
    values = []
    for field in REVISION_FIELDS:
        value = row.get(field)
        clock = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("SOURCE_REVISION_REQUIRES_AWARE_TIME")
        values.append(clock.astimezone(timezone.utc).isoformat(timespec="microseconds"))
    return tuple(values)


def _value_projection(row):
    return {field: _text(row.get(field)) for field in PRICE_VALUE_FIELDS}


def _receipt_index(bundle):
    receipts = {}
    for receipt in history_receipts(bundle):
        value = receipt.model_dump(mode="json") if hasattr(receipt, "model_dump") else dict(receipt)
        receipts[value["receipt_id"]] = {
            "receipt_id": value.get("receipt_id"),
            "provider": value.get("provider"),
            "dataset": value.get("dataset"),
            "content_hash": value.get("content_hash"),
            "field_version": value.get("field_version"),
            "definition_version": value.get("definition_version"),
            "source_type": value.get("source_type"),
            "status": value.get("status"),
            "pagination_complete": value.get("pagination_complete"),
            "source_published_at": value.get("source_published_at"),
            "first_seen_at": value.get("first_seen_at"),
            "ingested_at": value.get("ingested_at"),
        }
    return receipts


def _provenance_for(row, receipts):
    refs = sorted(row.get("source_refs") or [])
    summaries = [receipts.get(ref, {"receipt_id": ref, "status": "UNRESOLVED"}) for ref in refs]
    return {
        "source_refs": refs,
        "source_receipt_hashes": {
            item["receipt_id"]: digest(item)
            for item in summaries
            if item.get("receipt_id")
        },
        "source_providers": sorted({str(item.get("provider")) for item in summaries if item.get("provider")}),
        "source_datasets": sorted({str(item.get("dataset")) for item in summaries if item.get("dataset")}),
        "source_field_versions": sorted({str(item.get("field_version")) for item in summaries if item.get("field_version")}),
        "source_statuses": sorted({str(item.get("status")) for item in summaries if item.get("status")}),
    }


def _candidate(source_kind, row, row_hash, receipts, *, revision_key=None, provenance=None):
    values = _value_projection(row)
    provenance = _provenance_for(row, receipts) if provenance is None else provenance
    revision_key = _revision_key(row) if revision_key is None else revision_key
    return {
        "source_kind": source_kind,
        "row_hash": row_hash,
        "revision_key": revision_key,
        "source_revision": {
            "source_published_at": str(row.get("source_published_at")),
            "ingested_at": str(row.get("ingested_at")),
            "first_seen_at": str(row.get("first_seen_at")),
        },
        "provenance": provenance,
        "values": values,
        "value_hash": digest(values),
        "dependency_material_hash": digest({
            "source_kind": source_kind,
            "row_hash": row_hash,
            "revision_key": revision_key,
            "provenance": provenance,
            "values": values,
        }),
        "runtime_row": {
            "instrument_id": row.get("instrument_id"),
            "session": _date_text(row.get("session")),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "reference_prev_close": row.get("reference_prev_close"),
            "comparable_close": row.get("comparable_close"),
            "comparable_basis": row.get("comparable_basis"),
            "amount_cny": row.get("amount_cny"),
            "turnover_fraction": row.get("turnover_fraction"),
            "source_refs": tuple(row.get("source_refs") or ()),
        },
    }


def _selected_token(iid, session, candidates):
    base = {
        "resolver_version": CANONICAL_SOURCE_RESOLVER_VERSION,
        "requirement": PRICE_SOURCE_REQUIREMENT,
        "instrument_id": iid,
        "session": session,
    }
    if not candidates:
        return {
            **base,
            "status": "MISSING",
            "row_hash": MISSING_VISIBLE_BAR,
            "source_kind": None,
            "revision_key": None,
            "provenance": {},
            "values": {},
            "value_hash": MISSING_VISIBLE_BAR,
        }, None
    latest = max(candidate["revision_key"] for candidate in candidates)
    choices = [candidate for candidate in candidates if candidate["revision_key"] == latest]
    if len({choice["row_hash"] for choice in choices}) != 1:
        return {
            **base,
            "status": "CONFLICT",
            "row_hash": CONFLICTING_PRICE_REVISION,
            "source_kind": "CONFLICT",
            "revision_key": latest,
            "conflicting_row_hashes": sorted(choice["row_hash"] for choice in choices),
            "provenance": {
                "source_refs": sorted({ref for choice in choices for ref in choice["provenance"].get("source_refs", [])})
            },
            "values": {},
            "value_hash": CONFLICTING_PRICE_REVISION,
        }, None
    chosen = choices[0]
    return {
        **base,
        "status": "SELECTED",
        "source_kind": chosen["source_kind"],
        "row_hash": chosen["row_hash"],
        "revision_key": chosen["revision_key"],
        "source_revision": chosen["source_revision"],
        "provenance": chosen["provenance"],
        "values": chosen["values"],
        "value_hash": chosen["value_hash"],
        "dependency_material_hash": chosen["dependency_material_hash"],
    }, chosen["runtime_row"]


def _read_history_rows(ctx, wanted_sessions, wanted_ids, consider):
    owner = ctx.bundle._history_store
    manifest = owner.manifest(ctx.bundle.history_artifact_ref, ctx.bundle.history_artifact_hash)
    partitions = [part["partition_hash"] for part in manifest["partitions"] if part["session"] in wanted_sessions]
    wanted_id_list = sorted(wanted_ids)
    with owner.store.transaction(write=False) as con:
        for left in range(0, len(partitions), 250):
            batch = partitions[left : left + 250]
            if not batch:
                continue
            placeholders_p = ",".join("?" for _ in batch)
            for id_left in range(0, len(wanted_id_list), 500):
                id_batch = wanted_id_list[id_left : id_left + 500]
                placeholders_i = ",".join("?" for _ in id_batch)
                cursor = con.execute(
                    "SELECT m.row_hash,m.instrument_id,m.session,r.payload "
                    "FROM history_partition_members m "
                    "JOIN history_evidence_rows r ON r.row_hash=m.row_hash "
                    f"WHERE m.partition_hash IN ({placeholders_p}) AND m.kind='bar' "
                    f"AND m.instrument_id IN ({placeholders_i}) "
                    "ORDER BY m.instrument_id,m.session,m.row_hash",
                    [*batch, *id_batch],
                )
                for row in cursor:
                    payload = row["payload"]
                    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != row["row_hash"]:
                        raise IntegrityError("CORRUPT_HISTORY_ROW")
                    consider(json.loads(payload), "HISTORY_ARTIFACT", row["row_hash"])


def canonical_price_source_view(ctx, source_sessions, instrument_ids):
    """Return one PIT-resolved price source view for planning and computation.

    All revisions undergo PIT validation. Lower revisions do not enter dependency
    identity, so harmless inline rows do not invalidate cached payloads.  A
    selected revision, conflict, or disappearance changes the token.
    """
    source_sessions = tuple(_date_text(session) for session in source_sessions)
    instrument_ids = tuple(instrument_ids)
    cache = getattr(ctx, "scalar_cache", None)
    key = (
        "R16_CANONICAL_PRICE_SOURCE_VIEW",
        ctx.bundle.history_artifact_ref,
        ctx.bundle.history_artifact_hash,
        tuple(source_sessions),
        tuple(instrument_ids),
        str(ctx.cutoff),
        str(ctx.bundle.coverage_type),
    )
    if cache is not None and key in cache:
        return cache[key]

    wanted_sessions = set(source_sessions)
    wanted_ids = set(instrument_ids)
    receipts = _receipt_index(ctx.bundle)
    grouped = defaultdict(list)
    rejected = []
    provenance_cache = {}  # Scoped to this immutable receipt index, never global.
    work = {"rows_considered": 0, "pit_checks": 0, "candidate_constructions": 0,
            "lower_revision_pruned_after_pit": 0, "provenance_constructions": 0, "provenance_reuses": 0}

    def consider(value, source_kind, provided_hash=None):
        row = daily_bar_payload(value)
        session = _date_text(row.get("session"))
        iid = row.get("instrument_id")
        if session not in wanted_sessions or iid not in wanted_ids:
            return
        work["rows_considered"] += 1
        row_hash = _row_hash(value, provided_hash)
        if row.get("quality") == "INVALID":
            rejected.append({
                "kind": "BAR",
                "instrument_id": iid,
                "session": session,
                "source_kind": source_kind,
                "row_hash": row_hash,
                "reason_codes": ["INVALID_PRICE_QUALITY"],
            })
            return
        work["pit_checks"] += 1
        proof = ctx.resolver.resolve(row, instrument_id=iid, session=session, require_session=True, record_hash=row_hash)
        if not proof.allowed:
            rejected.append({
                "kind": "BAR",
                "instrument_id": iid,
                "session": session,
                "source_kind": source_kind,
                "row_hash": row_hash,
                "reason_codes": list(proof.reason_codes),
            })
            return
        # The resolver above records used/rejected evidence even for lower
        # revisions. Prune only the expensive candidate after that decision.
        revision = _revision_key(row)
        choices = grouped[(iid, session)]
        if choices and revision < choices[0]["revision_key"]:
            work["lower_revision_pruned_after_pit"] += 1
            return
        if choices and revision > choices[0]["revision_key"]:
            choices.clear()
        refs = tuple(sorted(row.get("source_refs") or []))
        if refs not in provenance_cache:
            provenance_cache[refs] = _provenance_for(row, receipts)
            work["provenance_constructions"] += 1
        else:
            work["provenance_reuses"] += 1
        choices.append(_candidate(source_kind, row, row_hash, receipts,
                                  revision_key=revision, provenance=provenance_cache[refs]))
        work["candidate_constructions"] += 1

    if ctx.bundle.history_artifact_ref:
        _read_history_rows(ctx, wanted_sessions, wanted_ids, consider)
    for bar in ctx.bundle.bars:
        consider(bar, "INLINE_MARKET_INPUT")

    selected = {}
    tokens = {}
    conflicts = []
    for iid in instrument_ids:
        for session in source_sessions:
            token, row = _selected_token(iid, session, grouped.get((iid, session), ()))
            tokens[(iid, session)] = token
            if row is not None:
                selected[(iid, session)] = row
            elif token["status"] == "CONFLICT":
                conflicts.append({
                    "kind": "BAR",
                    "instrument_id": iid,
                    "session": session,
                    "reason_codes": [CONFLICTING_PRICE_REVISION],
                    "conflicting_row_hashes": token.get("conflicting_row_hashes", []),
                })
    if rejected:
        ctx.rejected_evidence.extend(rejected)
    if conflicts:
        ctx.rejected_evidence.extend(conflicts)
    result = {
        "resolver_version": CANONICAL_SOURCE_RESOLVER_VERSION,
        "requirement": PRICE_SOURCE_REQUIREMENT,
        "source_sessions": list(source_sessions),
        "instrument_ids": list(instrument_ids),
        "tokens": tokens,
        "selected": selected,
        "rejected_count": len(rejected),
        "conflict_count": len(conflicts),
        "source_mode": "CANONICAL_RESOLVED_PRICE_SOURCE_VIEW",
        "work_counters": work,
    }
    if cache is not None:
        cache[key] = result
    return result


def source_token(view, instrument_id, session):
    return view["tokens"].get((instrument_id, _date_text(session))) or {
        "resolver_version": CANONICAL_SOURCE_RESOLVER_VERSION,
        "requirement": PRICE_SOURCE_REQUIREMENT,
        "instrument_id": instrument_id,
        "session": _date_text(session),
        "status": "MISSING",
        "row_hash": MISSING_VISIBLE_BAR,
        "source_kind": None,
        "revision_key": None,
        "provenance": {},
        "values": {},
        "value_hash": MISSING_VISIBLE_BAR,
    }


def selected_rows(view):
    return dict(view["selected"])
