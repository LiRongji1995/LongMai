"""Feature/Evidence Contract V1 export and comparison helpers.

The contract is the future boundary between a Go fact core and the existing
Python strategy.  It contains facts, statuses and provenance summaries, not
strategy internals or SQLite-specific storage details.
"""
from __future__ import annotations

from datetime import datetime
import csv
import hashlib
import json
from pathlib import Path

from ..common import utc_now
from .contracts import digest
from .derived_feature_store import FEATURE_SPEC_VERSION


CONTRACT_VERSION = "FEATURE_EVIDENCE_CONTRACT_V1"
BUILDER_VERSION = "PYTHON_CONTRACT_EXPORTER_R16_V1"
STATUS_KNOWN = "KNOWN"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
STATUS_UNVERIFIED = "UNVERIFIED"
ALLOWED_STATUSES = {STATUS_KNOWN, STATUS_UNKNOWN, STATUS_NOT_APPLICABLE, STATUS_UNVERIFIED}

PRICE_FIELDS = ("open", "high", "low", "close", "prev_close", "comparable_price", "price_basis")
RETURN_FIELDS = ("ret1_pct", "ret3_pct", "ret5_pct", "ret10_pct", "ret20_pct")
ROLLING_FIELDS = ("MA20", "MA60", "MA120", "MA250")
ACTIVITY_FIELDS = ("amount_cny", "turnover_fraction", "activity", "A")
CONTEXT_FIELDS = (
    "relative_strength_status",
    "RS_market_1d_pct",
    "RS_sector_1d_pct",
    "market_context_state",
    "market_compatibility_status",
    "sector_status",
    "funds_status",
    "capital_evidence_status",
)
EVENT_FIELDS = ("limit_event_status", "close_limit_up", "touched_limit_up", "close_limit_down")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False)


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _status(value, *, not_applicable=False, verified=True):
    if not_applicable:
        return STATUS_NOT_APPLICABLE
    if not verified:
        return STATUS_UNVERIFIED
    return STATUS_KNOWN if value is not None else STATUS_UNKNOWN


def _field(row, field, value, *, group, status=None, provenance=None, unit=None):
    status = status or _status(value, verified=bool(provenance))
    if status not in ALLOWED_STATUSES:
        raise ValueError("invalid contract field status")
    return {
        "contract_version": CONTRACT_VERSION,
        "instrument": row.get("instrument_id"),
        "exchange": row.get("exchange") or "UNKNOWN",
        "session": str(row.get("observation_date")),
        "eligibility": row.get("eligibility") or "ELIGIBLE",
        "eligibility_reason": row.get("eligibility_reason"),
        "field_group": group,
        "field": field,
        "value": value,
        "value_json": _json(value),
        "status": status,
        "unit": unit,
        "provenance": provenance or {"status": STATUS_UNVERIFIED, "reason": "SOURCE_FIELD_PROVENANCE_NOT_EXPORTED_IN_CURRENT_STRATEGY_ROW"},
    }


def _instrument_index(run):
    records = ((run.get("qualification") or {}).get("selected_records") or [])
    return {row.get("instrument_id"): row for row in records if row.get("instrument_id")}


def _stage_items(run):
    return (((run.get("results") or {}).get("STAGE_AWARE_MOMENTUM_V2") or {}).get("items") or [])


def _field_provenance(run, row, group):
    base = {
        "status": STATUS_UNVERIFIED,
        "source_policy_revision": run.get("source_hash"),
        "history_artifact_ref": run.get("history_artifact_ref"),
        "history_artifact_hash": run.get("history_artifact_hash"),
        "input_hash": run.get("input_hash"),
        "field_group": group,
    }
    detail = row.get("conditions") or {}
    if group == "ROLLING":
        base["status"] = STATUS_KNOWN
        base["source"] = "STAGE_AWARE_MOMENTUM_V2.trend_strength"
        base["reason_codes"] = ((detail.get("trend_state") or {}).get("reason_codes") or [])
    elif group == "PRICE":
        base["source"] = "STAGE_AWARE_MOMENTUM_V2.daily_path/current_bar"
        base["status"] = STATUS_UNVERIFIED if not row.get("comparable_basis") else STATUS_KNOWN
    elif group == "ACTIVITY":
        base["source"] = "STAGE_AWARE_MOMENTUM_V2.liquidity_bottom"
        base["status"] = STATUS_KNOWN if row.get("amount_cny") is not None or row.get("turnover_fraction") is not None else STATUS_UNKNOWN
    elif group in {"MARKET", "SECTOR", "CAPITAL"}:
        base["source"] = f"STAGE_AWARE_MOMENTUM_V2.{group.lower()}_context"
        base["status"] = STATUS_KNOWN if any(row.get(key) not in (None, "UNKNOWN") for key in CONTEXT_FIELDS) else STATUS_UNKNOWN
    return base


def facts_from_run(run):
    index = _instrument_index(run)
    rows = []
    for item in _stage_items(run):
        identity = index.get(item.get("instrument_id"), {})
        base = {**item, "exchange": identity.get("exchange") or item.get("exchange") or "UNKNOWN"}
        price_prov = _field_provenance(run, item, "PRICE")
        price_values = {
            "open": item.get("open"),
            "high": item.get("high"),
            "low": item.get("low"),
            "close": item.get("close"),
            "prev_close": (item.get("daily_path") or {}).get("prev_close"),
            "comparable_price": item.get("comparable_close"),
            "price_basis": item.get("comparable_basis"),
        }
        for field in PRICE_FIELDS:
            rows.append(_field(base, field, price_values.get(field), group="PRICE", provenance=price_prov))
        for field in RETURN_FIELDS:
            rows.append(_field(base, field, item.get(field), group="RETURN", provenance=price_prov, unit="percent"))

        trend = item.get("trend_strength") or {}
        ma_values = trend.get("ma_values") or {}
        rolling_prov = _field_provenance(run, item, "ROLLING")
        for field in ROLLING_FIELDS:
            rows.append(_field(base, field, ma_values.get(field[2:]), group="ROLLING", provenance=rolling_prov))

        activity_prov = _field_provenance(run, item, "ACTIVITY")
        activity_values = {
            "amount_cny": item.get("amount_cny"),
            "turnover_fraction": item.get("turnover_fraction"),
            "activity": item.get("activity_momentum"),
            "A": item.get("A"),
        }
        for field in ACTIVITY_FIELDS:
            rows.append(_field(base, field, activity_values.get(field), group="ACTIVITY", provenance=activity_prov))

        context_prov = _field_provenance(run, item, "MARKET")
        for field in CONTEXT_FIELDS:
            group = "CAPITAL" if "capital" in field or field == "funds_status" else "SECTOR" if "sector" in field else "MARKET"
            rows.append(_field(base, field, item.get(field), group=group, provenance={**context_prov, "field_group": group}))

        daily = item.get("daily_path") or {}
        event_prov = {**price_prov, "field_group": "EVENT", "extension_point": "limit/theme/auction/capital/temporal_events"}
        event_values = {
            "limit_event_status": item.get("limit_event_status") or daily.get("limit_event_quality"),
            "close_limit_up": daily.get("close_limit_up"),
            "touched_limit_up": daily.get("touched_limit_up"),
            "close_limit_down": daily.get("close_limit_down"),
        }
        for field in EVENT_FIELDS:
            rows.append(_field(base, field, event_values.get(field), group="EVENT", provenance=event_prov))
    return rows


def manifest_from_run(run, facts, *, snapshot_id=None, artifact_names=None):
    coverage = run.get("data_readiness") or run.get("data_coverage_diagnostics") or {}
    source_manifest = {
        "input_hash": run.get("input_hash"),
        "history_artifact_ref": run.get("history_artifact_ref"),
        "history_artifact_hash": run.get("history_artifact_hash"),
        "source_hash": run.get("source_hash"),
        "source_refs": run.get("source_refs") or [],
    }
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": snapshot_id or (run.get("input_id") or "snapshot_" + digest(run)[:16]),
        "as_of_session": str(run.get("observation_date")),
        "decision_cutoff": run.get("decision_cutoff"),
        "created_at": utc_now().isoformat(),
        "universe_version": (run.get("qualification") or {}).get("partition_version"),
        "universe_hash": run.get("universe_hash"),
        "calendar_revision": digest(run.get("observation_date")),
        "source_policy_revision": run.get("source_hash"),
        "feature_spec_revision": FEATURE_SPEC_VERSION,
        "source_manifest_hash": digest(source_manifest),
        "provenance_root_hash": digest([row["provenance"] for row in facts]),
        "builder_version": BUILDER_VERSION,
        "coverage_summary": coverage,
        "fact_row_count": len(facts),
        "field_groups": sorted({row["field_group"] for row in facts}),
        "unknown_semantics": {
            "KNOWN": "field value is populated from visible resolved evidence",
            "UNKNOWN": "evidence needed by the field was absent or insufficient",
            "NOT_APPLICABLE": "field is structurally not applicable for the row",
            "UNVERIFIED": "field exists but field-level provenance is not yet strong enough for KNOWN",
        },
        "physical_contract": {
            "manifest": "JSON",
            "bulk_facts": "JSONL; Parquet emitted when pyarrow is available",
            "sqlite_internal_schema_shared": False,
        },
        "artifacts": artifact_names or {},
    }
    manifest["manifest_hash"] = digest({k: v for k, v in manifest.items() if k != "manifest_hash"})
    return manifest


def write_contract_snapshot(run, output_dir, *, snapshot_id=None, source_label="PYTHON_BASELINE"):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    facts = facts_from_run(run)
    for row in facts:
        row["source_label"] = source_label
    facts_path = output / "feature_evidence_facts_v1.jsonl"
    with facts_path.open("w", encoding="utf-8", newline="\n") as fp:
        for row in facts:
            fp.write(_json(row) + "\n")
    csv_path = output / "feature_evidence_facts_v1.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fp:
        fieldnames = ["instrument", "exchange", "session", "eligibility", "eligibility_reason", "field_group", "field", "value_json", "status", "unit", "source_label"]
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in facts:
            writer.writerow({key: row.get(key) for key in fieldnames})
    artifacts = {"facts_jsonl": facts_path.name, "facts_csv": csv_path.name}
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pylist([
            {
                "instrument": row["instrument"],
                "exchange": row["exchange"],
                "session": row["session"],
                "eligibility": row["eligibility"],
                "eligibility_reason": row["eligibility_reason"],
                "field_group": row["field_group"],
                "field": row["field"],
                "value_json": row["value_json"],
                "status": row["status"],
                "unit": row["unit"],
                "source_label": row["source_label"],
            }
            for row in facts
        ])
        parquet_path = output / "feature_evidence_facts_v1.parquet"
        pq.write_table(table, parquet_path)
        artifacts["facts_parquet"] = parquet_path.name
    except Exception as exc:
        artifacts["facts_parquet"] = None
        artifacts["parquet_status"] = "NOT_WRITTEN"
        artifacts["parquet_error"] = type(exc).__name__
    manifest = manifest_from_run(run, facts, snapshot_id=snapshot_id, artifact_names=artifacts)
    manifest_path = output / "feature_evidence_manifest_v1.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"manifest": manifest_path, "facts_jsonl": facts_path, "facts_csv": csv_path, "manifest_hash": manifest["manifest_hash"], **artifacts}


def read_facts_jsonl(path):
    rows = []
    with Path(path).open("r", encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def validate_manifest(manifest):
    required = {
        "contract_version", "snapshot_id", "as_of_session", "decision_cutoff", "created_at",
        "universe_hash", "calendar_revision", "source_policy_revision", "feature_spec_revision",
        "source_manifest_hash", "provenance_root_hash", "builder_version", "coverage_summary",
    }
    missing = sorted(required - set(manifest))
    if missing:
        return {"valid": False, "reason": "MISSING_MANIFEST_FIELDS", "missing": missing}
    if manifest["contract_version"] != CONTRACT_VERSION:
        return {"valid": False, "reason": "UNSUPPORTED_CONTRACT_VERSION"}
    try:
        datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
    except ValueError:
        return {"valid": False, "reason": "INVALID_CREATED_AT"}
    return {"valid": True}


def validate_facts(facts):
    errors = []
    for index, row in enumerate(facts):
        for field in ("contract_version", "instrument", "session", "field_group", "field", "status", "value_json", "provenance"):
            if field not in row:
                errors.append({"row": index, "reason": "MISSING_FIELD", "field": field})
        if row.get("contract_version") != CONTRACT_VERSION:
            errors.append({"row": index, "reason": "UNSUPPORTED_CONTRACT_VERSION"})
        if row.get("status") not in ALLOWED_STATUSES:
            errors.append({"row": index, "reason": "INVALID_STATUS", "status": row.get("status")})
        if row.get("value") in (False, 0) and row.get("status") != STATUS_KNOWN:
            errors.append({"row": index, "reason": "MISSING_VALUE_COERCED_TO_FALSE_OR_ZERO"})
    return {"valid": not errors, "errors": errors}


def normalized_rows(facts):
    return {
        (row.get("instrument"), row.get("session"), row.get("field_group"), row.get("field")): {
            "value_json": row.get("value_json"),
            "status": row.get("status"),
            "provenance_hash": digest(row.get("provenance") or {}),
        }
        for row in facts
    }


def normalized_row_items(facts):
    rows = normalized_rows(facts)
    return [
        {
            "instrument": key[0],
            "session": key[1],
            "field_group": key[2],
            "field": key[3],
            **value,
        }
        for key, value in sorted(rows.items())
    ]


def snapshot_hash(facts):
    return _sha256_bytes(_json(normalized_row_items(facts)).encode("utf-8"))


def compare_facts(left_facts, right_facts, *, numeric_tolerances=None, compare_provenance=True):
    numeric_tolerances = numeric_tolerances or {}
    left = normalized_rows(left_facts)
    right = normalized_rows(right_facts)
    mismatches = []
    for key in sorted(set(left) | set(right)):
        lrow = left.get(key)
        rrow = right.get(key)
        instrument, session, group, field = key
        if lrow is None or rrow is None:
            mismatches.append({
                "instrument": instrument,
                "session": session,
                "field_group": group,
                "field": field,
                "difference": "MISSING_ROW",
                "python_value": None if lrow is None else lrow["value_json"],
                "shadow_value": None if rrow is None else rrow["value_json"],
            })
            continue
        if lrow["status"] != rrow["status"]:
            mismatches.append({
                "instrument": instrument,
                "session": session,
                "field_group": group,
                "field": field,
                "difference": "STATUS_DIFFERENCE",
                "python_status": lrow["status"],
                "shadow_status": rrow["status"],
                "python_value": lrow["value_json"],
                "shadow_value": rrow["value_json"],
            })
            continue
        tolerance = numeric_tolerances.get((group, field)) or numeric_tolerances.get(field)
        if tolerance is not None and lrow["value_json"] != rrow["value_json"]:
            try:
                left_num = float(json.loads(lrow["value_json"]))
                right_num = float(json.loads(rrow["value_json"]))
                if abs(left_num - right_num) <= tolerance:
                    continue
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        if lrow["value_json"] != rrow["value_json"]:
            mismatches.append({
                "instrument": instrument,
                "session": session,
                "field_group": group,
                "field": field,
                "difference": "VALUE_DIFFERENCE",
                "python_value": lrow["value_json"],
                "shadow_value": rrow["value_json"],
            })
            continue
        if compare_provenance and lrow["provenance_hash"] != rrow["provenance_hash"]:
            mismatches.append({
                "instrument": instrument,
                "session": session,
                "field_group": group,
                "field": field,
                "difference": "PROVENANCE_DIFFERENCE",
                "python_provenance_hash": lrow["provenance_hash"],
                "shadow_provenance_hash": rrow["provenance_hash"],
            })
    return {
        "comparison_version": "FEATURE_EVIDENCE_CONTRACT_COMPARATOR_R16_V1",
        "left_hash": snapshot_hash(left_facts),
        "right_hash": snapshot_hash(right_facts),
        "matched": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:1000],
        "mismatch_truncated": len(mismatches) > 1000,
        "numeric_tolerances": {str(key): value for key, value in numeric_tolerances.items()},
    }
