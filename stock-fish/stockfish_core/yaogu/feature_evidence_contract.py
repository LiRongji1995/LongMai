"""Feature/Evidence Contract V1 export and comparison helpers.

The contract is the future boundary between a Go fact core and the existing
Python strategy.  It contains facts, statuses and provenance summaries, not
strategy internals or SQLite-specific storage details.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import csv
import hashlib
import json
import re
from pathlib import Path

from ..common import utc_now
from .contracts import digest
from .derived_feature_store import FEATURE_SPEC_VERSION


CONTRACT_VERSION = "FEATURE_EVIDENCE_CONTRACT_V1"
BUILDER_VERSION = "PYTHON_CONTRACT_EXPORTER_R16_R1"
SCHEMA_REVISION = "FEATURE_EVIDENCE_SCHEMA_R16_R1"
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

# Physical V1 records are immutable. R16_R1 is a strict validation revision;
# legacy records remain readable for diagnosis but are not accepted by this gate.
FIELD_SPECS = {}
for _group, _fields in (("PRICE", PRICE_FIELDS), ("RETURN", RETURN_FIELDS),
                         ("ROLLING", ROLLING_FIELDS), ("ACTIVITY", ACTIVITY_FIELDS),
                         ("EVENT", EVENT_FIELDS)):
    for _name in _fields:
        _kind = "string" if _name in {"price_basis", "activity", "limit_event_status"} else "boolean" if _group == "EVENT" else "number"
        _unit = "percent" if _group == "RETURN" else "CNY" if _group in {"PRICE", "ROLLING"} or _name == "amount_cny" else "fraction" if _name == "turnover_fraction" else "ratio" if _name == "A" else None
        if _kind != "number":
            _unit = None
        FIELD_SPECS[(_group, _name)] = {"type": _kind, "unit": _unit}
for _name in CONTEXT_FIELDS:
    _group = "CAPITAL" if "capital" in _name or _name == "funds_status" else "SECTOR" if "sector" in _name else "MARKET"
    FIELD_SPECS[(_group, _name)] = {"type": "number" if _name.endswith("_pct") else "string", "unit": "percent" if _name.endswith("_pct") else None}


def _verified_provenance(provenance):
    """Verification is explicit, cutoff-aware and tied to actual source identity."""
    if not isinstance(provenance, dict) or provenance.get("status") != STATUS_KNOWN or provenance.get("pit_status") != "VISIBLE":
        return False
    refs = provenance.get("source_refs")
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref for ref in refs):
        return False
    if not any(isinstance(provenance.get(name), str) and provenance[name]
               for name in ("source_revision_id", "source_manifest_hash")):
        return False
    try:
        visible = datetime.fromisoformat(provenance["visible_at"].replace("Z", "+00:00"))
        cutoff = datetime.fromisoformat(provenance["decision_cutoff"].replace("Z", "+00:00"))
        return visible.tzinfo is not None and cutoff.tzinfo is not None and visible <= cutoff
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


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


def _field(row, field, value, *, group, status=None, provenance=None, unit=None, reason=None):
    provenance = dict(provenance or {"status": STATUS_UNVERIFIED, "reason": "SOURCE_FIELD_PROVENANCE_NOT_EXPORTED_IN_CURRENT_STRATEGY_ROW"})
    spec = FIELD_SPECS[(group, field)]
    if group == "EVENT" and spec["type"] == "boolean" and isinstance(value, str):
        provenance["source_value"] = value
        if value in {"TRUE", "FALSE"}:
            value = value == "TRUE"
        elif value in {"UNKNOWN", "NOT_APPLICABLE"}:
            status, value = value, None
            reason = reason or "EVENT_" + status
    if value is None:
        status = status if status in {STATUS_UNKNOWN, STATUS_NOT_APPLICABLE} else STATUS_UNKNOWN
    else:
        status = status or _status(value, verified=_verified_provenance(provenance))
    if status not in ALLOWED_STATUSES:
        raise ValueError("invalid contract field status")
    return {
        "contract_version": CONTRACT_VERSION,
        "schema_revision": SCHEMA_REVISION,
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
        "reason": reason or (None if status == STATUS_KNOWN else provenance.get("reason") or "EVIDENCE_" + status),
        "unit": spec["unit"] if unit is None else unit,
        "provenance": provenance,
    }


def _instrument_index(run):
    records = ((run.get("qualification") or {}).get("selected_records") or [])
    return {row.get("instrument_id"): row for row in records if row.get("instrument_id")}


def _stage_items(run):
    return (((run.get("results") or {}).get("STAGE_AWARE_MOMENTUM_V2") or {}).get("items") or [])


def _field_provenance(run, row, group):
    # A saved strategy label is not proof of per-field PIT verification. Export
    # original metadata losslessly, without upgrading it based on populated values.
    supplied = (row.get("field_provenance") or {}).get(group)
    if isinstance(supplied, dict):
        return dict(supplied)
    base = {
        "status": STATUS_UNVERIFIED,
        "source_policy_revision": run.get("source_hash"),
        "history_artifact_ref": run.get("history_artifact_ref"),
        "history_artifact_hash": run.get("history_artifact_hash"),
        "input_hash": run.get("input_hash"),
        "field_group": group,
        "source_refs": run.get("source_refs") or [],
        "decision_cutoff": run.get("decision_cutoff"),
        "pit_status": "NOT_VERIFIED",
        "reason": "DIAGNOSTIC_POST_STRATEGY_EXPORT_NO_FIELD_LEVEL_PIT_PROOF",
    }
    detail = row.get("conditions") or {}
    if group == "ROLLING":
        base["source"] = "STAGE_AWARE_MOMENTUM_V2.trend_strength"
        base["reason_codes"] = ((detail.get("trend_state") or {}).get("reason_codes") or [])
    elif group == "PRICE":
        base["source"] = "STAGE_AWARE_MOMENTUM_V2.daily_path/current_bar"
    elif group == "ACTIVITY":
        base["source"] = "STAGE_AWARE_MOMENTUM_V2.liquidity_bottom"
    elif group in {"MARKET", "SECTOR", "CAPITAL"}:
        base["source"] = f"STAGE_AWARE_MOMENTUM_V2.{group.lower()}_context"
    return base


def facts_from_run(run):
    index = _instrument_index(run)
    rows = []
    for item in _stage_items(run):
        identity = index.get(item.get("instrument_id"), {})
        base = {**item, "exchange": identity.get("exchange") or item.get("exchange") or "UNKNOWN"}
        price_prov = _field_provenance(run, item, "PRICE")
        price_values = {
            "open": (item.get("daily_path") or {}).get("open"),
            "high": (item.get("daily_path") or {}).get("high"),
            "low": (item.get("daily_path") or {}).get("low"),
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

        for field in CONTEXT_FIELDS:
            group = "CAPITAL" if "capital" in field or field == "funds_status" else "SECTOR" if "sector" in field else "MARKET"
            rows.append(_field(base, field, item.get(field), group=group, provenance=_field_provenance(run, item, group)))

        daily = item.get("daily_path") or {}
        event_prov = {**_field_provenance(run, item, "EVENT"), "limit_event_quality": daily.get("limit_event_quality"), "extension_point": "limit/theme/auction/capital/temporal_events"}
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
        "calendar_revision": run.get("calendar_revision"),
        "schema_revision": SCHEMA_REVISION,
        "identity_status": {key: "KNOWN" if run.get(key) else "UNVERIFIED" for key in ("calendar_revision", "source_policy_revision")},
        "source_policy_revision": run.get("source_policy_revision"),
        "feature_spec_revision": run.get("feature_spec_revision") or FEATURE_SPEC_VERSION,
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
            "bulk_facts": "JSONL is authoritative and lossless; CSV/Parquet are display projections only",
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
        fieldnames = ["instrument", "exchange", "session", "eligibility", "eligibility_reason", "field_group", "field", "value_json", "status", "reason", "unit", "source_label", "provenance_json"]
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in facts:
            writer.writerow({key: _json(row["provenance"]) if key == "provenance_json" else row.get(key) for key in fieldnames})
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
                "reason": row["reason"],
                "provenance_json": _json(row["provenance"]),
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
        for line_number, line in enumerate(fp, 1):
            if line.strip():
                try:
                    rows.append(_strict_json(line))
                except ValueError as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return rows


def validate_manifest(manifest):
    if not isinstance(manifest, dict):
        return {"valid": False, "reason": "MANIFEST_NOT_OBJECT"}
    required = {
        "contract_version", "snapshot_id", "as_of_session", "decision_cutoff", "created_at",
        "universe_hash", "calendar_revision", "source_policy_revision", "feature_spec_revision",
        "source_manifest_hash", "provenance_root_hash", "builder_version", "coverage_summary", "schema_revision",
    }
    missing = sorted(required - set(manifest))
    if missing:
        return {"valid": False, "reason": "MISSING_MANIFEST_FIELDS", "missing": missing}
    for field in ("snapshot_id", "as_of_session", "decision_cutoff", "created_at", "feature_spec_revision", "builder_version"):
        if not isinstance(manifest[field], str) or not manifest[field]:
            return {"valid": False, "reason": "INVALID_MANIFEST_FIELD_TYPE", "field": field}
    for field in ("universe_hash", "calendar_revision", "source_policy_revision", "source_manifest_hash", "provenance_root_hash"):
        if manifest[field] is not None and (not isinstance(manifest[field], str) or not manifest[field]):
            return {"valid": False, "reason": "INVALID_MANIFEST_FIELD_TYPE", "field": field}
    for field in ("coverage_summary", "identity_status", "artifacts"):
        if field in manifest and not isinstance(manifest[field], dict):
            return {"valid": False, "reason": "INVALID_MANIFEST_FIELD_TYPE", "field": field}
    if type(manifest.get("fact_row_count")) is not int or manifest["fact_row_count"] < 0:
        return {"valid": False, "reason": "INVALID_FACT_ROW_COUNT"}
    if manifest["contract_version"] != CONTRACT_VERSION:
        return {"valid": False, "reason": "UNSUPPORTED_CONTRACT_VERSION"}
    if manifest["schema_revision"] != SCHEMA_REVISION:
        return {"valid": False, "reason": "UNSUPPORTED_SCHEMA_REVISION"}
    try:
        if date.fromisoformat(manifest["as_of_session"]).isoformat() != manifest["as_of_session"]:
            raise ValueError("noncanonical session")
        for field in ("created_at", "decision_cutoff"):
            if datetime.fromisoformat(str(manifest[field]).replace("Z", "+00:00")).tzinfo is None:
                raise ValueError("naive timestamp")
    except (TypeError, ValueError):
        return {"valid": False, "reason": "INVALID_MANIFEST_TIME"}
    for field in ("calendar_revision", "source_policy_revision"):
        if not manifest[field] and (manifest.get("identity_status") or {}).get(field) != STATUS_UNVERIFIED:
            return {"valid": False, "reason": "MISSING_IDENTITY_MUST_BE_UNVERIFIED", "field": field}
    if manifest.get("manifest_hash") != digest({key: value for key, value in manifest.items() if key != "manifest_hash"}):
        return {"valid": False, "reason": "MANIFEST_HASH_MISMATCH"}
    return {"valid": True}


def validate_snapshot_binding(manifest, facts):
    errors = []
    if not isinstance(manifest, dict) or not isinstance(facts, (list, tuple)) or any(not isinstance(row, dict) for row in facts):
        return {"valid": False, "errors": ["INVALID_SNAPSHOT_SHAPE"]}
    if type(manifest.get("fact_row_count")) is not int or manifest.get("fact_row_count") != len(facts):
        errors.append("FACT_ROW_COUNT_MISMATCH")
    if manifest.get("provenance_root_hash") != digest([row.get("provenance") for row in facts]):
        errors.append("PROVENANCE_ROOT_HASH_MISMATCH")
    return {"valid": not errors, "errors": errors}


def compare_manifests(left, right):
    if not validate_manifest(left)["valid"] or not validate_manifest(right)["valid"]:
        return {"matched": False, "differences": [{"reason": "INVALID_MANIFEST"}]}
    allowed = {"created_at", "builder_version", "artifacts", "manifest_hash"}
    # Operational labels may differ. Effective source/cutoff/calendar/spec never do.
    fields = set(left) | set(right)
    differences = [{"field": name, "left": left.get(name), "right": right.get(name)}
                   for name in sorted(fields - allowed) if left.get(name) != right.get(name)]
    return {"matched": not differences, "differences": differences,
            "allowed_runtime_differences": sorted(allowed)}


def _strict_json(value):
    def reject_constant(value):
        raise ValueError("non-finite JSON number: " + value)
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key: " + key)
            result[key] = value
        return result
    return json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique_pairs)


def _numeric(value):
    # Decimal strings are accepted losslessly; booleans are never numbers.
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("invalid numeric type")
    text = str(value)
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", text) is None:
        raise ValueError("invalid JSON decimal syntax")
    number = Decimal(text)
    if not number.is_finite():
        raise ValueError("non-finite number")
    return number


def _equal_value(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return _numeric(left) == _numeric(right)
    return type(left) is type(right) and left == right


def exact_fraction(row):
    exact = row.get("exact_value")
    if not isinstance(exact, dict) or set(exact) != {"numerator", "denominator"}:
        raise ValueError("invalid exact rational")
    numerator, denominator = exact["numerator"], exact["denominator"]
    if not isinstance(numerator, str) or not isinstance(denominator, str):
        raise ValueError("rational integers must be strings")
    value = Fraction(int(numerator), int(denominator))
    if str(value.numerator) != numerator or str(value.denominator) != denominator:
        raise ValueError("exact rational must be canonical and reduced")
    return value


def validate_facts(facts):
    if not isinstance(facts, (list, tuple)):
        return {"valid": False, "errors": [{"row": None, "reason": "FACTS_NOT_ARRAY"}]}
    errors, seen = [], set()
    required = {"contract_version", "schema_revision", "instrument", "exchange", "session",
                "eligibility", "eligibility_reason", "field_group", "field", "value", "value_json",
                "status", "reason", "unit", "provenance"}
    for index, row in enumerate(facts):
        def error(reason, **detail):
            errors.append({"row": index, "reason": reason, **detail})
        if not isinstance(row, dict):
            error("ROW_NOT_OBJECT")
            continue
        for field in sorted(required - row.keys()):
            error("MISSING_FIELD", field=field)
        for field in sorted(row.keys() - required - {"exact_value", "source_label"}):
            error("UNDECLARED_FACT_FIELD", field=field)
        key = tuple(row.get(name) for name in ("instrument", "session", "field_group", "field"))
        if not all(isinstance(value, str) and value for value in key):
            error("INVALID_LOGICAL_IDENTITY")
            continue
        if key in seen:
            error("DUPLICATE_LOGICAL_KEY", key=key)
        seen.add(key)
        if row.get("contract_version") != CONTRACT_VERSION or row.get("schema_revision") != SCHEMA_REVISION:
            error("UNSUPPORTED_SCHEMA_OR_CONTRACT_VERSION")
        try:
            if date.fromisoformat(row["session"]).isoformat() != row["session"]:
                raise ValueError()
        except ValueError:
            error("INVALID_SESSION")
        if not isinstance(row.get("exchange"), str) or row["exchange"] not in {"SSE", "SZSE", "BSE", "UNKNOWN"} or not isinstance(row.get("eligibility"), str) or not row["eligibility"]:
            error("INVALID_IDENTITY")
        if "source_label" in row and not isinstance(row["source_label"], str):
            error("INVALID_SOURCE_LABEL")
        spec = FIELD_SPECS.get(key[2:])
        if spec is None:
            error("UNSUPPORTED_FIELD", key=key)
            continue
        if row.get("unit") != spec["unit"]:
            error("INVALID_UNIT", expected=spec["unit"], actual=row.get("unit"))
        value, status = row.get("value"), row.get("status")
        if not isinstance(status, str) or status not in ALLOWED_STATUSES:
            error("INVALID_STATUS")
            continue
        if status in {STATUS_UNKNOWN, STATUS_NOT_APPLICABLE} and value is not None:
            error("UNKNOWN_OR_NOT_APPLICABLE_REQUIRES_NULL")
        if status == STATUS_KNOWN and value is None:
            error("KNOWN_REQUIRES_VALUE")
        for name in ("reason", "eligibility_reason"):
            if row.get(name) is not None and not isinstance(row[name], str):
                error("INVALID_REASON_TYPE", field=name)
        if status != STATUS_KNOWN and (not isinstance(row.get("reason"), str) or not row["reason"]):
            error("NON_KNOWN_REQUIRES_REASON")
        if not isinstance(row.get("provenance"), dict):
            error("INVALID_PROVENANCE")
        elif status == STATUS_KNOWN and not _verified_provenance(row["provenance"]):
            error("KNOWN_REQUIRES_VERIFIED_VISIBLE_PROVENANCE")
        try:
            parsed = _strict_json(row.get("value_json"))
            if not _equal_value(value, parsed):
                error("VALUE_VALUE_JSON_CONFLICT")
            _json(row.get("provenance"))
        except (ValueError, TypeError, InvalidOperation):
            error("INVALID_JSON_OR_NON_FINITE")
        if value is not None:
            try:
                if spec["type"] == "number":
                    number = _numeric(value)
                    if key[2] in {"PRICE", "ROLLING"} and number <= 0:
                        error("NON_POSITIVE_PRICE")
                elif spec["type"] == "boolean" and type(value) is not bool:
                    error("INVALID_BOOLEAN_TYPE")
                elif spec["type"] == "string" and not isinstance(value, str):
                    error("INVALID_STRING_TYPE")
            except (ValueError, InvalidOperation):
                error("INVALID_NUMBER")
        if row.get("exact_value") is not None:
            try:
                if value is None or spec["type"] != "number":
                    raise ValueError("exact requires numeric value")
                exact = exact_fraction(row)
                places = 4 if key[2] == "RETURN" else 8 if key[2] == "PRICE" else 6
                # Same public formatting helper as existing production (Fraction ->
                # Decimal at precision 50, then HALF_EVEN quantization).
                from .strategy_experiments import _number
                if _numeric(_number(exact, places=places)) != _numeric(value):
                    raise ValueError("exact/display mismatch")
            except (ValueError, TypeError, ZeroDivisionError, InvalidOperation):
                error("INVALID_EXACT_VALUE")
    return {"valid": not errors, "errors": errors}


IDENTITY_FIELDS = ("contract_version", "schema_revision", "exchange", "eligibility", "eligibility_reason", "status", "reason", "unit")
ALLOWED_RUNTIME_DIFFERENCES = {"source_label": "builder label only; effective source identity is in provenance"}


def normalized_rows(facts):
    validation = validate_facts(facts)
    if not validation["valid"]:
        raise ValueError("INVALID_FACTS: " + _json(validation))
    return {
        (row["instrument"], row["session"], row["field_group"], row["field"]): {
            **{name: row.get(name) for name in IDENTITY_FIELDS},
            "value_json": _json(row["value"]), "provenance": row["provenance"],
            "exact_value": row.get("exact_value"),
        } for row in facts
    }


def normalized_row_items(facts):
    return [{"instrument": key[0], "session": key[1], "field_group": key[2], "field": key[3], **value}
            for key, value in sorted(normalized_rows(facts).items())]


def snapshot_hash(facts):
    return _sha256_bytes(_json(normalized_row_items(facts)).encode("utf-8"))


def compare_facts(left_facts, right_facts, *, numeric_tolerances=None, compare_provenance=True):
    numeric_tolerances = numeric_tolerances or {}
    validations = {"left": validate_facts(left_facts), "right": validate_facts(right_facts)}
    mismatches = []
    for side, validation in validations.items():
        for error in validation["errors"]:
            original = (left_facts if side == "left" else right_facts)
            if error["row"] is not None:
                original = original[error["row"]]
            mismatches.append({"difference": "VALIDATION_FAILURE", "side": side, **error, "fact": original})
    if not mismatches:
        left, right = normalized_rows(left_facts), normalized_rows(right_facts)
        for key in sorted(set(left) | set(right)):
            lrow, rrow = left.get(key), right.get(key)
            detail = {"instrument": key[0], "session": key[1], "field_group": key[2], "field": key[3], "left": lrow, "right": rrow}
            if lrow is None or rrow is None:
                mismatches.append({**detail, "difference": "MISSING_ROW"})
                continue
            for name in IDENTITY_FIELDS:
                if lrow[name] != rrow[name]:
                    mismatches.append({**detail, "difference": "IDENTITY_DIFFERENCE", "component": name})
            equal = _equal_value(_strict_json(lrow["value_json"]), _strict_json(rrow["value_json"]))
            tolerance = numeric_tolerances.get(key[2:], numeric_tolerances.get(key[3]))
            if not equal and tolerance is not None and FIELD_SPECS[key[2:]]["type"] == "number":
                try:
                    tol = _numeric(tolerance)
                    if tol < 0:
                        raise ValueError("negative tolerance")
                    equal = abs(_numeric(_strict_json(lrow["value_json"])) - _numeric(_strict_json(rrow["value_json"]))) <= tol
                except (ValueError, InvalidOperation):
                    equal = False
            if not equal:
                mismatches.append({**detail, "difference": "VALUE_DIFFERENCE"})
            if lrow["exact_value"] != rrow["exact_value"]:
                mismatches.append({**detail, "difference": "EXACT_VALUE_DIFFERENCE"})
            if compare_provenance and _json(lrow["provenance"]) != _json(rrow["provenance"]):
                mismatches.append({**detail, "difference": "PROVENANCE_DIFFERENCE"})
    return {
        "comparison_version": "FEATURE_EVIDENCE_CONTRACT_COMPARATOR_R16_R1",
        "left_validation": validations["left"], "right_validation": validations["right"],
        "left_hash": snapshot_hash(left_facts) if validations["left"]["valid"] else None,
        "right_hash": snapshot_hash(right_facts) if validations["right"]["valid"] else None,
        "matched": not mismatches, "mismatch_count": len(mismatches),
        "mismatches": mismatches[:1000], "mismatch_truncated": len(mismatches) > 1000,
        "numeric_tolerances": {str(key): value for key, value in numeric_tolerances.items()},
        "allowed_runtime_differences": ALLOWED_RUNTIME_DIFFERENCES,
    }
