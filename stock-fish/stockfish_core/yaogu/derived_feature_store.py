"""Rebuildable derived facts for daily stage-aware evaluation.

The authoritative facts remain MarketInput, history artifacts and source
evidence.  This cache stores deterministic price primitives only.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
import logging
import sqlite3
import time

from ..common import utc_now
from ..storage import IntegrityError, SQLiteStore
from .contracts import digest


LEGACY_STORE_VERSION = "DERIVED_FEATURE_STORE_V1"
STORE_VERSION = "DERIVED_FEATURE_STORE_V2"
FEATURE_SPEC_VERSION = "DERIVED_FEATURE_STORE_V2_FAMILY_SPECS_R16_R1_PIT_INSTANT"
PRICE_BASE_FEATURE_SPEC_ID = "DERIVED_PRICE_FEATURE_SPEC_V1"
ROLLING_PRICE_FEATURE_SPEC_ID = "ROLLING_PRICE_FEATURE_SPEC_V1"
ACTIVITY_FEATURE_SPEC_ID = "ACTIVITY_FEATURE_SPEC_V1"
SUPPLY_FEATURE_SPEC_ID = "SUPPLY_FEATURE_SPEC_V1"
MARKET_RELATIVE_FEATURE_SPEC_ID = "MARKET_RELATIVE_FEATURE_SPEC_V1"
SECTOR_RELATIVE_FEATURE_SPEC_ID = "SECTOR_RELATIVE_FEATURE_SPEC_V1"
RET_WINDOWS = (1, 3, 5, 10, 20)
AMOUNT_CHANGE_WINDOWS = (1, 3, 5, 10, 20)
MA_WINDOWS = (5, 10, 20, 50, 60, 120, 150, 200, 250)
ROLLING_WINDOWS = (5, 10, 20)
RET_INDEX = {value: index for index, value in enumerate(RET_WINDOWS)}
AMOUNT_CHANGE_INDEX = {value: index for index, value in enumerate(AMOUNT_CHANGE_WINDOWS)}
MA_INDEX = {value: index for index, value in enumerate(MA_WINDOWS)}
CONTEXT_KEY = "R14_DERIVED_FEATURES"
CONTEXT_STATS_KEY = "R15_DERIVED_FEATURE_STATS"
LEGACY_CONTEXT_STATS_KEY = "R14_DERIVED_FEATURE_STATS"
MISSING = object()
SOURCE_BATCH_INSTRUMENTS = 250
CACHE_LIFECYCLE_CATEGORIES = (
    "CACHE_HIT",
    "NEW",
    "INVALIDATED_SOURCE_REVISION",
    "INVALIDATED_DEPENDENCY",
    "INVALIDATED_SPEC",
    "INVALIDATED_POLICY",
    "MISSING_CACHE_ROW",
    "CORRUPT_OR_CONFLICT",
    "UNKNOWN",
)

FEATURE_FAMILY_SPECS = {
    "PRICE_BASE": {
        "feature_spec_id": PRICE_BASE_FEATURE_SPEC_ID,
        "fields": ("comparable_close",),
        "dependency": "FEATURE_SESSION_ONLY",
        "cross_as_of_reuse": "SAFE_WHEN_SOURCE_DEPENDENCY_FINGERPRINT_MATCHES",
    },
    "ROLLING_PRICE": {
        "feature_spec_id": ROLLING_PRICE_FEATURE_SPEC_ID,
        "ret_windows": RET_WINDOWS,
        "ma_windows": MA_WINDOWS,
        "dependency": "CONTIGUOUS_PRICE_WINDOW_UP_TO_MAX_RET_OR_MA",
        "cross_as_of_reuse": "SAFE_WHEN_SOURCE_DEPENDENCY_FINGERPRINT_MATCHES",
    },
    "ACTIVITY": {
        "feature_spec_id": ACTIVITY_FEATURE_SPEC_ID,
        "amount_change_windows": AMOUNT_CHANGE_WINDOWS,
        "rolling_windows": ROLLING_WINDOWS,
        "dependency": "CONTIGUOUS_ACTIVITY_WINDOW_UP_TO_MAX_AMOUNT_OR_ROLLING",
        "cross_as_of_reuse": "SAFE_WHEN_SOURCE_DEPENDENCY_FINGERPRINT_MATCHES",
    },
    "SUPPLY": {
        "feature_spec_id": SUPPLY_FEATURE_SPEC_ID,
        "dependency": "LIMIT_AND_RECLAIM_HISTORY",
        "cross_as_of_reuse": "PIT_DEPENDENT_NOT_MATERIALIZED_IN_PRICE_CACHE",
    },
    "MARKET_RELATIVE": {
        "feature_spec_id": MARKET_RELATIVE_FEATURE_SPEC_ID,
        "dependency": "MARKET_POPULATION_AND_BENCHMARK_SESSIONS",
        "cross_as_of_reuse": "PIT_DEPENDENT_NOT_MATERIALIZED_IN_PRICE_CACHE",
    },
    "SECTOR_RELATIVE": {
        "feature_spec_id": SECTOR_RELATIVE_FEATURE_SPEC_ID,
        "dependency": "PIT_THEME_MEMBERSHIP_AND_PEER_SESSIONS",
        "cross_as_of_reuse": "PIT_DEPENDENT_NOT_MATERIALIZED_IN_PRICE_CACHE",
    },
}
MATERIALIZED_FAMILIES = ("PRICE_BASE", "ROLLING_PRICE", "ACTIVITY")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False)


def _payload_hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _date_text(value):
    return value.isoformat() if isinstance(value, date) and not isinstance(value, datetime) else str(value)[:10]


def _frac(value):
    try:
        if value is None:
            return None
        if isinstance(value, Fraction):
            return value
        if isinstance(value, Decimal):
            return Fraction(value)
        return Fraction(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError, ZeroDivisionError):
        return None


def _enc(value):
    value = _frac(value)
    return None if value is None else {"numerator": value.numerator, "denominator": value.denominator}


def _dec(value):
    if value is None:
        return None
    try:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return Fraction(int(value[0]), int(value[1]))
        return Fraction(int(value["numerator"]), int(value["denominator"]))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _runtime_pair(value):
    if value is None:
        return None
    try:
        return (int(value["numerator"]), int(value["denominator"]))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _runtime_row(row):
    features = row.get("features") or {}
    return (
        tuple(_runtime_pair((features.get("returns") or {}).get(str(window))) for window in RET_WINDOWS),
        tuple(_runtime_pair((features.get("amount_change") or {}).get(str(window))) for window in AMOUNT_CHANGE_WINDOWS),
        tuple(_runtime_pair((features.get("ma") or {}).get(str(window))) for window in MA_WINDOWS),
        tuple((features.get("ma_reasons") or {}).get(str(window)) for window in MA_WINDOWS),
    )


def feature_family_spec_hash(family):
    return digest(FEATURE_FAMILY_SPECS[family])


def feature_spec_hash():
    from .source_resolution import CANONICAL_SOURCE_RESOLVER_VERSION

    return digest({
        "store_version": STORE_VERSION,
        "feature_spec_version": FEATURE_SPEC_VERSION,
        "source_resolver_version": CANONICAL_SOURCE_RESOLVER_VERSION,
        "family_spec_hashes": {
            family: feature_family_spec_hash(family)
            for family in sorted(FEATURE_FAMILY_SPECS)
        },
        "materialized_families": MATERIALIZED_FAMILIES,
        "strategy_decision_spec_excluded": True,
        "facts_are_not_final_decisions": True,
    })


def strategy_dependency_version():
    from .source_resolution import CANONICAL_SOURCE_RESOLVER_VERSION

    return (
        f"{STORE_VERSION}:"
        + ":".join(FEATURE_FAMILY_SPECS[family]["feature_spec_id"] for family in MATERIALIZED_FAMILIES)
        + f":{CANONICAL_SOURCE_RESOLVER_VERSION}"
    )


def family_cross_as_of_safe(family):
    spec = FEATURE_FAMILY_SPECS[family]
    return spec.get("cross_as_of_reuse") == "SAFE_WHEN_SOURCE_DEPENDENCY_FINGERPRINT_MATCHES"


def identity_for_context(ctx, input_hash):
    from .source_resolution import CANONICAL_SOURCE_RESOLVER_VERSION

    history_hash = ctx.bundle.history_artifact_hash or input_hash
    return {
        "store_version": STORE_VERSION,
        "feature_spec_version": FEATURE_SPEC_VERSION,
        "source_resolver_version": CANONICAL_SOURCE_RESOLVER_VERSION,
        "legacy_store_version": LEGACY_STORE_VERSION,
        "input_hash_observed": input_hash,
        "input_hash_in_cache_key": False,
        "history_artifact_hash_observed": history_hash,
        "history_manifest_hash_in_cache_key": False,
        "feature_spec_hash": feature_spec_hash(),
        "strategy_dependency_version": strategy_dependency_version(),
        "request_as_of_session": _date_text(ctx.observation_date),
        "feature_as_of_session_policy": "CACHE_KEY_USES_FEATURE_SESSION_NOT_REQUEST_SESSION",
        "feature_family_specs": {
            family: {
                "feature_spec_id": spec["feature_spec_id"],
                "feature_spec_hash": feature_family_spec_hash(family),
                "materialized": family in MATERIALIZED_FAMILIES,
                "cross_as_of_safe": family_cross_as_of_safe(family),
            }
            for family, spec in sorted(FEATURE_FAMILY_SPECS.items())
        },
        "comparable_basis_policy": "BOUND_BY_SOURCE_DEPENDENCY_FINGERPRINT_AND_PAYLOAD_BASIS",
        "pit_policy": "SOURCE_VISIBILITY_RESOLVED_BEFORE_FEATURE_MATERIALIZATION",
        "cache_key_policy": "FEATURE_SPEC_INSTRUMENT_FEATURE_SESSION_REQUIRED_SOURCE_RANGE_DEPENDENCY_FINGERPRINT",
        "dependency_completeness_invariant": "CACHE_FINGERPRINT_AND_FEATURE_PAYLOAD_SHARE_CANONICAL_RESOLVED_SOURCE_VIEW",
    }


def _source_session_range(ctx, session, family):
    session = _date_text(session)
    session_texts = [_date_text(value) for value in ctx.sessions]
    position = session_texts.index(session)
    if family == "PRICE_BASE":
        left = position
    elif family == "ROLLING_PRICE":
        left = min(
            position - max(RET_WINDOWS),
            position - max(MA_WINDOWS) + 1,
        )
    elif family == "ACTIVITY":
        left = min(
            position - max(AMOUNT_CHANGE_WINDOWS),
            position - max(ROLLING_WINDOWS) + 1,
        )
    else:
        left = position
    left = max(0, left)
    return tuple(session_texts[left:position + 1])


def _history_dependency_index(ctx, source_sessions, instrument_ids):
    source_sessions = tuple(_date_text(session) for session in source_sessions)
    instrument_ids = tuple(instrument_ids)
    cache = getattr(ctx, "scalar_cache", None)
    key = (
        "R15_HISTORY_ROW_DEPENDENCY_INDEX",
        tuple(source_sessions),
        tuple(instrument_ids),
    )
    if cache is not None and key in cache:
        return cache[key]
    if not ctx.bundle.history_artifact_ref:
        return None
    wanted_sessions = set(source_sessions)
    wanted_ids = set(instrument_ids)
    result = defaultdict(list)
    owner = ctx.bundle._history_store
    manifest = owner.manifest(ctx.bundle.history_artifact_ref, ctx.bundle.history_artifact_hash)
    partitions = [part["partition_hash"] for part in manifest["partitions"] if part["session"] in wanted_sessions]
    wanted_id_list = sorted(wanted_ids)
    with owner.store.transaction(write=False) as con:
        for left in range(0, len(partitions), 250):
            batch = partitions[left:left + 250]
            if not batch:
                continue
            placeholders_p = ",".join("?" for _ in batch)
            for id_left in range(0, len(wanted_id_list), 500):
                id_batch = wanted_id_list[id_left:id_left + 500]
                placeholders_i = ",".join("?" for _ in id_batch)
                cursor = con.execute(
                    "SELECT m.row_hash,m.instrument_id,m.session "
                    "FROM history_partition_members m "
                    f"WHERE m.partition_hash IN ({placeholders_p}) AND m.kind='bar' "
                    f"AND m.instrument_id IN ({placeholders_i}) "
                    "ORDER BY m.instrument_id,m.session,m.row_hash",
                    [*batch, *id_batch],
                )
                for row in cursor:
                    result[(row["instrument_id"], row["session"])].append(row["row_hash"])
    result = {
        key_: {"row_hashes": sorted(set(row_hashes))}
        for key_, row_hashes in result.items()
    }
    if cache is not None:
        cache[key] = result
    return result


def _inline_dependency_index(ctx, source_sessions, instrument_ids):
    source_sessions = tuple(_date_text(session) for session in source_sessions)
    instrument_ids = tuple(instrument_ids)
    cache = getattr(ctx, "scalar_cache", None)
    key = (
        "R15_INLINE_DEPENDENCY_INDEX",
        tuple(source_sessions),
        tuple(instrument_ids),
    )
    if cache is not None and key in cache:
        return cache[key]
    wanted_sessions = set(source_sessions)
    wanted_ids = set(instrument_ids)
    grouped = defaultdict(list)
    for value in ctx.bundle.bars:
        row = value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
        session = _date_text(row.get("session"))
        iid = row.get("instrument_id")
        if session not in wanted_sessions or iid not in wanted_ids:
            continue
        if row.get("quality") == "INVALID":
            continue
        proof = ctx.resolver.resolve(value, instrument_id=iid, session=session, require_session=True)
        if not proof.allowed:
            continue
        grouped[(iid, session)].append((_revision_key(row), digest(row), sorted(row.get("source_refs") or [])))
    result = {}
    for key_, rows in grouped.items():
        latest = max(row[0] for row in rows)
        choices = [row for row in rows if row[0] == latest]
        hashes = {row[1] for row in choices}
        if len(hashes) == 1:
            result[key_] = {
                "row_hash": choices[0][1],
                "source_refs": choices[0][2],
            }
        else:
            result[key_] = {
                "row_hash": "CONFLICTING_PRICE_REVISION",
                "source_refs": sorted({ref for row in choices for ref in row[2]}),
            }
    if cache is not None:
        cache[key] = result
    return result


def _source_token_for_view(source_view, iid, source_session):
    from .source_resolution import source_token

    token = source_token(source_view, iid, source_session)
    return {
        "resolver_version": token.get("resolver_version"),
        "requirement": token.get("requirement"),
        "session": token.get("session"),
        "status": token.get("status"),
        "source_kind": token.get("source_kind"),
        "row_hash": token.get("row_hash"),
        "revision_key": token.get("revision_key"),
        "source_revision": token.get("source_revision"),
        "provenance": token.get("provenance"),
        "values": token.get("values"),
        "value_hash": token.get("value_hash"),
        "dependency_material_hash": token.get("dependency_material_hash"),
    }


def _source_summary_for_range(source_view, source_mode, iid, source_sessions, *, token_cache=None, work=None):
    hasher = hashlib.sha256()
    hasher.update(_json({"source_mode": source_mode, "instrument_id": iid}).encode("utf-8") + b"\n")
    first_token = None
    last_token = None
    missing_count = 0
    conflict_count = 0
    for source_session in source_sessions:
        if token_cache is None:
            token = _source_token_for_view(source_view, iid, source_session)
            encoded = _json(token).encode("utf-8") + b"\n"
        else:
            token, encoded = token_cache[source_session]
        if work is not None:
            work["window_token_visits"] += 1
        if first_token is None:
            first_token = token
        last_token = token
        if token.get("status") == "MISSING" or token.get("row_hash") == "MISSING_VISIBLE_BAR":
            missing_count += 1
        if token.get("status") == "CONFLICT":
            conflict_count += 1
        hasher.update(encoded)
    return {
        "source_mode": source_mode,
        "resolver_version": source_view.get("resolver_version"),
        "requirement": source_view.get("requirement"),
        "source_token_count": len(source_sessions),
        "source_token_digest": hasher.hexdigest(),
        "missing_source_token_count": missing_count,
        "conflicting_source_token_count": conflict_count,
        "first_source_token": first_token,
        "last_source_token": last_token,
    }


def _dependency_identity(ctx, family, iid, session, source_sessions, source_view, source_mode, *, token_cache=None, work=None):
    source_summary = _source_summary_for_range(source_view, source_mode, iid, source_sessions, token_cache=token_cache, work=work)
    spec = FEATURE_FAMILY_SPECS[family]
    source_fingerprint = digest({
        "resolver_version": source_view.get("resolver_version"),
        "requirement": source_view.get("requirement"),
        "source_mode": source_mode,
        "instrument_id": iid,
        "source_token_digest": source_summary["source_token_digest"],
    })
    value = {
        "store_version": STORE_VERSION,
        "feature_family": family,
        "feature_spec_id": spec["feature_spec_id"],
        "feature_spec_hash": feature_family_spec_hash(family),
        "instrument_id": iid,
        "feature_as_of_session": _date_text(session),
        "required_source_session_range": {
            "start": source_sessions[0] if source_sessions else _date_text(session),
            "end": source_sessions[-1] if source_sessions else _date_text(session),
            "sessions": list(source_sessions),
        },
        "comparable_basis_identity": "SOURCE_ROWS_INCLUDE_COMPARABLE_BASIS_AND_PAYLOAD_RECORDS_SELECTED_BASIS",
        "source_data_dependency_fingerprint": source_fingerprint,
        "pit_policy_identity": digest({
            "policy": "PRICE_FACTS_CROSS_AS_OF_SAFE_ONLY_WHEN_SOURCE_DEPENDENCY_FINGERPRINT_MATCHES",
            "coverage_type": ctx.bundle.coverage_type,
            "feature_as_of_session": _date_text(session),
            "request_cutoff_in_cache_key": False,
            "unsafe_pit_families_materialized": [],
        }),
        "provider_revision_identity": digest({
            "source_mode": source_mode,
            "source_token_digest": source_summary["source_token_digest"],
        }),
        "source_dependency_mode": source_mode,
        "source_dependency_summary": source_summary,
        "cross_session_reuse_safe": family_cross_as_of_safe(family),
    }
    value["dependency_fingerprint"] = digest({
        "feature_spec_hash": value["feature_spec_hash"],
        "instrument_id": iid,
        "feature_as_of_session": value["feature_as_of_session"],
        "required_source_session_range": value["required_source_session_range"],
        "source_data_dependency_fingerprint": source_fingerprint,
        "pit_policy_identity": value["pit_policy_identity"],
        "provider_revision_identity": value["provider_revision_identity"],
    })
    return value


def dependency_plan_for_context(ctx, sessions, instrument_ids, *, source_view=None):
    sessions = tuple(_date_text(session) for session in sessions)
    instrument_ids = tuple(instrument_ids)
    session_positions = {_date_text(value): index for index, value in enumerate(ctx.sessions)}
    ranges = {(session, family): _source_session_range(ctx, session, family)
              for session in sessions for family in MATERIALIZED_FAMILIES}
    source_sessions = sorted({
        source_session
        for session in sessions
        for family in MATERIALIZED_FAMILIES
        for source_session in ranges[(session, family)]
    }, key=session_positions.__getitem__)
    if source_view is None:
        from .source_resolution import canonical_price_source_view

        source_view = canonical_price_source_view(ctx, source_sessions, instrument_ids)
    source_mode = source_view.get("source_mode", "CANONICAL_RESOLVED_PRICE_SOURCE_VIEW")
    plan = {}
    work = {"token_serializations": 0, "window_token_visits": 0, "range_constructions": len(ranges)}
    for iid in instrument_ids:
        # Reuse the exact original projected token bytes across overlapping
        # windows. Retain only one instrument's serialized material at a time.
        token_cache = {}
        for source_session in source_sessions:
            token = _source_token_for_view(source_view, iid, source_session)
            token_cache[source_session] = (token, _json(token).encode("utf-8") + b"\n")
            work["token_serializations"] += 1
        for session in sessions:
            plan[(iid, session)] = {
                family: _dependency_identity(
                    ctx,
                    family,
                    iid,
                    session,
                    ranges[(session, family)],
                    source_view,
                    source_mode,
                    token_cache=token_cache,
                    work=work,
                )
                for family in MATERIALIZED_FAMILIES
            }
    source_view["dependency_work_counters"] = work
    return plan


def _family_rows(row):
    features = row.get("features") or {}
    base = {
        "comparable_close": features.get("comparable_close"),
    }
    rolling = {
        "returns": features.get("returns") or {},
        "ma": features.get("ma") or {},
        "ma_reasons": features.get("ma_reasons") or {},
    }
    activity = {
        "amount_cny": features.get("amount_cny"),
        "turnover_fraction": features.get("turnover_fraction"),
        "amount_change": features.get("amount_change") or {},
        "amount_mean": features.get("amount_mean") or {},
        "amount_median": features.get("amount_median") or {},
        "turnover_mean": features.get("turnover_mean") or {},
    }
    payloads = {
        "PRICE_BASE": base,
        "ROLLING_PRICE": rolling,
        "ACTIVITY": activity,
    }
    result = []
    for family in MATERIALIZED_FAMILIES:
        result.append((family, {
            "store_version": STORE_VERSION,
            "feature_family": family,
            "family_spec_id": FEATURE_FAMILY_SPECS[family]["feature_spec_id"],
            "family_spec_hash": feature_family_spec_hash(family),
            "instrument_id": row["instrument_id"],
            "session": _date_text(row["session"]),
            "comparable_basis": row.get("comparable_basis") or "UNAVAILABLE",
            "features": payloads[family],
            "cache_role": "DETERMINISTIC_DERIVED_FACTS_NOT_STRATEGY_DECISIONS",
        }))
    return result


def _combined_payload(payloads):
    features = {}
    for family in MATERIALIZED_FAMILIES:
        features.update((payloads.get(family) or {}).get("features") or {})
    sample = next(iter(payloads.values()))
    return {
        "store_version": STORE_VERSION,
        "feature_spec_version": FEATURE_SPEC_VERSION,
        "instrument_id": sample["instrument_id"],
        "session": sample["session"],
        "comparable_basis": sample.get("comparable_basis") or "UNAVAILABLE",
        "features": features,
        "cache_role": "DETERMINISTIC_DERIVED_FACTS_NOT_STRATEGY_DECISIONS",
    }


class DerivedFeatureStore:
    def __init__(self, store: SQLiteStore):
        self.store = store
        self.last_get_many_stats = {}
        self.last_put_many_stats = {}
        with self.store.transaction() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS yaogu_derived_features_v2 (
                    cache_key TEXT PRIMARY KEY,
                    feature_family TEXT NOT NULL,
                    family_spec_id TEXT NOT NULL,
                    family_spec_hash TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    feature_session TEXT NOT NULL,
                    required_source_start TEXT NOT NULL,
                    required_source_end TEXT NOT NULL,
                    dependency_fingerprint TEXT NOT NULL,
                    dependency_identity TEXT NOT NULL CHECK(json_valid(dependency_identity)),
                    comparable_basis_identity TEXT NOT NULL,
                    pit_policy_identity TEXT NOT NULL,
                    provider_revision_identity TEXT NOT NULL,
                    payload TEXT NOT NULL CHECK(json_valid(payload)),
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS yaogu_derived_features_v2_lookup "
                "ON yaogu_derived_features_v2(feature_family,family_spec_hash,instrument_id,feature_session,dependency_fingerprint)"
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS yaogu_derived_feature_integrity_conflicts_v2 (
                    conflict_id TEXT PRIMARY KEY,
                    cache_key TEXT NOT NULL,
                    feature_family TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    feature_session TEXT NOT NULL,
                    existing_payload_hash TEXT NOT NULL,
                    new_payload_hash TEXT NOT NULL,
                    dependency_identity TEXT NOT NULL CHECK(json_valid(dependency_identity)),
                    recorded_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def cache_key(identity, instrument_id, session, comparable_basis=None, *, family=None, dependency_identity=None):
        if family is None:
            raise ValueError("feature family required for derived feature v2 cache key")
        if dependency_identity is None:
            raise ValueError("dependency identity required for derived feature v2 cache key")
        return "derived_feature_v2_" + digest({
            "store_version": STORE_VERSION,
            "feature_family": family,
            "feature_spec_id": FEATURE_FAMILY_SPECS[family]["feature_spec_id"],
            "family_spec_hash": feature_family_spec_hash(family),
            "instrument_id": instrument_id,
            "feature_session": _date_text(session),
            "required_source_session_range": dependency_identity["required_source_session_range"],
            "dependency_fingerprint": dependency_identity["dependency_fingerprint"],
        })

    def get_many(self, identity, instrument_ids, sessions, *, dependency_plan=None):
        wanted = set((iid, _date_text(session)) for session in sessions for iid in instrument_ids)
        if not wanted:
            self.last_get_many_stats = {
                "requested_rows": 0,
                "row_hits": 0,
                "row_misses": 0,
                "family_requested": 0,
                "family_hits": 0,
                "family_misses": 0,
                "family_lifecycle": {category: 0 for category in CACHE_LIFECYCLE_CATEGORIES},
                "family_by_family": {
                    family: {"requested": 0, "hits": 0, "misses": 0, "lifecycle": {category: 0 for category in CACHE_LIFECYCLE_CATEGORIES}}
                    for family in MATERIALIZED_FAMILIES
                },
                "row_lifecycle": {
                    "requested_rows": 0,
                    "full_row_reused": 0,
                    "partial_family_reused": 0,
                    "new_rows": 0,
                    "invalidated_rows": 0,
                    "missing_cache_rows": 0,
                    "corrupt_or_conflict_rows": 0,
                    "unknown_rows": 0,
                },
                "invalidated_rows": 0,
                "invalidated_family_rows": 0,
                "missing_cache_rows": 0,
            }
            return {}
        if dependency_plan is None:
            raise ValueError("derived feature v2 lookup requires dependency_plan")
        expected = {}
        for iid, session in sorted(wanted):
            family_plan = dependency_plan[(iid, session)]
            for family in MATERIALIZED_FAMILIES:
                dep = family_plan[family]
                cache_key = self.cache_key(identity, iid, session, family=family, dependency_identity=dep)
                expected[cache_key] = (iid, session, family, dep)
        result = {}
        by_row = defaultdict(dict)
        found_keys = set()
        family_statuses_by_row = defaultdict(dict)
        family_lifecycle = {category: 0 for category in CACHE_LIFECYCLE_CATEGORIES}
        family_by_family = {
            family: {
                "requested": 0,
                "hits": 0,
                "misses": 0,
                "lifecycle": {category: 0 for category in CACHE_LIFECYCLE_CATEGORIES},
            }
            for family in MATERIALIZED_FAMILIES
        }
        lifecycle_samples = []
        with self.store.transaction(write=False) as con:
            keys = list(expected)
            for left in range(0, len(keys), 500):
                chunk = keys[left:left + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = con.execute(
                    "SELECT cache_key,feature_family,instrument_id,feature_session,payload,payload_hash "
                    f"FROM yaogu_derived_features_v2 WHERE cache_key IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    value = json.loads(row["payload"])
                    if _payload_hash(value) != row["payload_hash"]:
                        raise IntegrityError("DERIVED_FEATURE_CACHE_HASH_MISMATCH")
                    found_keys.add(row["cache_key"])
                    iid, session, family, _dep = expected[row["cache_key"]]
                    family_statuses_by_row[(iid, session)][family] = "CACHE_HIT"
                    key = (row["instrument_id"], row["feature_session"])
                    by_row[key][row["feature_family"]] = value
            missing_expected = {
                key: expected[key]
                for key in sorted(set(expected) - found_keys)
            }
            existing_by_family_row = self._existing_rows_for_missing(con, missing_expected)
            for cache_key, (iid, session, family, dep) in missing_expected.items():
                status = self._classify_family_miss(
                    dep,
                    existing_by_family_row.get((family, iid, session), ()),
                    family,
                )
                family_statuses_by_row[(iid, session)][family] = status
                if len(lifecycle_samples) < 50:
                    lifecycle_samples.append({
                        "cache_key": cache_key,
                        "instrument_id": iid,
                        "session": session,
                        "feature_family": family,
                        "lifecycle": status,
                        "dependency_fingerprint": dep.get("dependency_fingerprint"),
                        "provider_revision_identity": dep.get("provider_revision_identity"),
                    })
            for cache_key, (iid, session, family, _dep) in expected.items():
                status = family_statuses_by_row[(iid, session)].get(family, "UNKNOWN")
                family_lifecycle[status] = family_lifecycle.get(status, 0) + 1
                family_stats = family_by_family[family]
                family_stats["requested"] += 1
                family_stats["lifecycle"][status] = family_stats["lifecycle"].get(status, 0) + 1
                if status == "CACHE_HIT":
                    family_stats["hits"] += 1
                else:
                    family_stats["misses"] += 1
            invalidated_family_rows = sum(
                count for category, count in family_lifecycle.items()
                if category.startswith("INVALIDATED_")
            )
        for key, payloads in by_row.items():
            if all(family in payloads for family in MATERIALIZED_FAMILIES):
                result[key] = _runtime_row(_combined_payload(payloads))
        row_misses = wanted - set(result)
        row_lifecycle = self._row_lifecycle(wanted, family_statuses_by_row)
        self.last_get_many_stats = {
            "requested_rows": len(wanted),
            "row_hits": len(result),
            "row_misses": len(row_misses),
            "family_requested": len(expected),
            "family_hits": len(found_keys),
            "family_misses": len(expected) - len(found_keys),
            "family_lifecycle": family_lifecycle,
            "family_by_family": family_by_family,
            "row_lifecycle": row_lifecycle,
            "invalidated_rows": row_lifecycle["invalidated_rows"],
            "invalidated_family_rows": invalidated_family_rows,
            "missing_cache_rows": row_lifecycle["missing_cache_rows"],
            "lifecycle_samples": lifecycle_samples,
        }
        return result

    def _existing_rows_for_missing(self, con, missing_expected):
        if not missing_expected:
            return {}
        grouped = defaultdict(lambda: {"sessions": set(), "instruments": set()})
        for _cache_key, (iid, session, family, _dep) in missing_expected.items():
            grouped[family]["sessions"].add(session)
            grouped[family]["instruments"].add(iid)
        result = defaultdict(list)
        for family, values in grouped.items():
            sessions = sorted(values["sessions"])
            instruments = sorted(values["instruments"])
            for session_left in range(0, len(sessions), 200):
                session_chunk = sessions[session_left:session_left + 200]
                session_placeholders = ",".join("?" for _ in session_chunk)
                for instrument_left in range(0, len(instruments), 500):
                    instrument_chunk = instruments[instrument_left:instrument_left + 500]
                    instrument_placeholders = ",".join("?" for _ in instrument_chunk)
                    rows = con.execute(
                        "SELECT feature_family,family_spec_id,family_spec_hash,instrument_id,feature_session,"
                        "dependency_fingerprint,comparable_basis_identity,pit_policy_identity,"
                        "provider_revision_identity,payload_hash "
                        "FROM yaogu_derived_features_v2 "
                        "WHERE feature_family=? "
                        f"AND feature_session IN ({session_placeholders}) "
                        f"AND instrument_id IN ({instrument_placeholders})",
                        [family, *session_chunk, *instrument_chunk],
                    ).fetchall()
                    for row in rows:
                        result[(row["feature_family"], row["instrument_id"], row["feature_session"])].append(dict(row))
        return result

    @staticmethod
    def _classify_family_miss(dep, existing_rows, family):
        if not existing_rows:
            return "NEW"
        same_spec = [
            row for row in existing_rows
            if row.get("family_spec_id") == FEATURE_FAMILY_SPECS[family]["feature_spec_id"]
            and row.get("family_spec_hash") == feature_family_spec_hash(family)
        ]
        if not same_spec:
            return "INVALIDATED_SPEC"
        same_policy = [
            row for row in same_spec
            if row.get("pit_policy_identity") == dep.get("pit_policy_identity")
            and row.get("comparable_basis_identity") == dep.get("comparable_basis_identity")
        ]
        if not same_policy:
            return "INVALIDATED_POLICY"
        same_provider = [
            row for row in same_policy
            if row.get("provider_revision_identity") == dep.get("provider_revision_identity")
        ]
        if not same_provider:
            return "INVALIDATED_SOURCE_REVISION"
        same_dependency = [
            row for row in same_provider
            if row.get("dependency_fingerprint") == dep.get("dependency_fingerprint")
        ]
        if not same_dependency:
            return "INVALIDATED_DEPENDENCY"
        return "MISSING_CACHE_ROW"

    @staticmethod
    def _row_lifecycle(wanted, family_statuses_by_row):
        result = {
            "requested_rows": len(wanted),
            "full_row_reused": 0,
            "partial_family_reused": 0,
            "new_rows": 0,
            "invalidated_rows": 0,
            "missing_cache_rows": 0,
            "corrupt_or_conflict_rows": 0,
            "unknown_rows": 0,
        }
        for key in wanted:
            statuses = [family_statuses_by_row[key].get(family, "UNKNOWN") for family in MATERIALIZED_FAMILIES]
            if all(status == "CACHE_HIT" for status in statuses):
                result["full_row_reused"] += 1
            elif any(status == "CACHE_HIT" for status in statuses):
                result["partial_family_reused"] += 1
            if all(status == "NEW" for status in statuses):
                result["new_rows"] += 1
            if any(status.startswith("INVALIDATED_") for status in statuses):
                result["invalidated_rows"] += 1
            if any(status == "MISSING_CACHE_ROW" for status in statuses):
                result["missing_cache_rows"] += 1
            if any(status == "CORRUPT_OR_CONFLICT" for status in statuses):
                result["corrupt_or_conflict_rows"] += 1
            if any(status == "UNKNOWN" for status in statuses):
                result["unknown_rows"] += 1
        return result

    def _count_invalidated(self, con, missing_expected):
        if not missing_expected:
            return 0
        table = con.execute("SELECT count(*) AS n FROM yaogu_derived_features_v2").fetchone()
        if not table or int(table["n"]) == 0:
            return 0
        if len(missing_expected) > 20000:
            by_family = defaultdict(set)
            for _cache_key, (_iid, session, family, _dep) in missing_expected.items():
                by_family[(family, feature_family_spec_hash(family))].add(session)
            count = 0
            for (family, spec_hash), sessions in by_family.items():
                session_list = sorted(sessions)
                for left in range(0, len(session_list), 250):
                    chunk = session_list[left:left + 250]
                    placeholders = ",".join("?" for _ in chunk)
                    row = con.execute(
                        "SELECT count(*) AS n FROM yaogu_derived_features_v2 "
                        "WHERE feature_family=? AND family_spec_hash=? "
                        f"AND feature_session IN ({placeholders})",
                        [family, spec_hash, *chunk],
                    ).fetchone()
                    count += int(row["n"]) if row else 0
            return count
        count = 0
        by_family = defaultdict(list)
        for _cache_key, (iid, session, family, dep) in missing_expected.items():
            by_family[(family, feature_family_spec_hash(family))].append((iid, session, dep["dependency_fingerprint"]))
        for (family, spec_hash), rows in by_family.items():
            for left in range(0, len(rows), 250):
                chunk = rows[left:left + 250]
                clauses = []
                args = [family, spec_hash]
                for iid, session, fingerprint in chunk:
                    clauses.append("(instrument_id=? AND feature_session=? AND dependency_fingerprint<>?)")
                    args.extend((iid, session, fingerprint))
                if not clauses:
                    continue
                row = con.execute(
                    "SELECT count(*) AS n FROM yaogu_derived_features_v2 "
                    "WHERE feature_family=? AND family_spec_hash=? AND ("
                    + " OR ".join(clauses)
                    + ")",
                    args,
                ).fetchone()
                count += int(row["n"]) if row else 0
        return count

    def put_many(self, identity, rows):
        now = utc_now().isoformat()
        inserted = 0
        reused = 0
        conflicts = []
        with self.store.transaction() as con:
            for row in rows:
                family_rows = _family_rows(row)
                dependency_identities = row.get("dependency_identities") or {}
                for family, payload in family_rows:
                    dep = dependency_identities[family]
                    payload_hash = _payload_hash(payload)
                    cache_key = self.cache_key(
                        identity,
                        row["instrument_id"],
                        row["session"],
                        family=family,
                        dependency_identity=dep,
                    )
                    existing = con.execute(
                        "SELECT payload_hash FROM yaogu_derived_features_v2 WHERE cache_key=?",
                        (cache_key,),
                    ).fetchone()
                    if existing:
                        if existing["payload_hash"] == payload_hash:
                            reused += 1
                            continue
                        conflict = {
                            "cache_key": cache_key,
                            "feature_family": family,
                            "instrument_id": row["instrument_id"],
                            "feature_session": _date_text(row["session"]),
                            "existing_payload_hash": existing["payload_hash"],
                            "new_payload_hash": payload_hash,
                            "dependency_identity": dep,
                        }
                        conflict_id = "derived_feature_integrity_conflict_" + digest(conflict)
                        con.execute(
                            """
                            INSERT OR IGNORE INTO yaogu_derived_feature_integrity_conflicts_v2(
                                conflict_id,cache_key,feature_family,instrument_id,feature_session,
                                existing_payload_hash,new_payload_hash,dependency_identity,recorded_at)
                            VALUES(?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                conflict_id,
                                cache_key,
                                family,
                                row["instrument_id"],
                                _date_text(row["session"]),
                                existing["payload_hash"],
                                payload_hash,
                                _json(dep),
                                now,
                            ),
                        )
                        conflicts.append(conflict)
                        continue
                    con.execute(
                        """
                        INSERT INTO yaogu_derived_features_v2(
                            cache_key,feature_family,family_spec_id,family_spec_hash,
                            instrument_id,feature_session,required_source_start,required_source_end,
                            dependency_fingerprint,dependency_identity,comparable_basis_identity,
                            pit_policy_identity,provider_revision_identity,payload,payload_hash,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            cache_key,
                            family,
                            FEATURE_FAMILY_SPECS[family]["feature_spec_id"],
                            feature_family_spec_hash(family),
                            row["instrument_id"],
                            _date_text(row["session"]),
                            dep["required_source_session_range"]["start"],
                            dep["required_source_session_range"]["end"],
                            dep["dependency_fingerprint"],
                            _json(dep),
                            dep["comparable_basis_identity"],
                            dep["pit_policy_identity"],
                            dep["provider_revision_identity"],
                            _json(payload),
                            payload_hash,
                            now,
                        ),
                    )
                    inserted += 1
        self.last_put_many_stats = {
            "inserted_family_rows": inserted,
            "reused_existing_family_rows": reused,
            "integrity_conflicts": len(conflicts),
            "conflicts": conflicts,
        }
        if conflicts:
            raise IntegrityError("DERIVED_FEATURE_INTEGRITY_CONFLICT")
        return self.last_put_many_stats

    def clear_identity(self, identity):
        with self.store.transaction() as con:
            con.execute("DELETE FROM yaogu_derived_features_v2")

    def count_identity(self, identity):
        with self.store.transaction(write=False) as con:
            row = con.execute(
                "SELECT count(*) AS n FROM yaogu_derived_features_v2",
            ).fetchone()
        return int(row["n"])


def _revision_key(row):
    return (str(row.get("source_published_at")), str(row.get("ingested_at")), str(row.get("first_seen_at")))


def _selected_compact_bars(ctx, source_sessions, instrument_ids, *, source_view=None):
    if source_view is None:
        from .source_resolution import canonical_price_source_view

        source_view = canonical_price_source_view(ctx, source_sessions, instrument_ids)
    selected = source_view["selected"]
    return {(iid, _date_text(session)): selected[(iid, _date_text(session))]
            for iid in instrument_ids for session in source_sessions if (iid, _date_text(session)) in selected}


def _same_basis(rows):
    basis = rows[0].get("comparable_basis") if rows else None
    closes = [_frac(row.get("comparable_close")) for row in rows]
    return bool(basis) and all(row.get("comparable_basis") == basis for row in rows) and all(value is not None and value > 0 for value in closes)


def _median(values):
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _feature_row(identity, iid, session, row, sessions, bars_by_iid, position, dependency_identities):
    basis = row.get("comparable_basis") if row else None
    comparable_close = _frac((row or {}).get("comparable_close"))
    amount = _frac((row or {}).get("amount_cny"))
    turnover = _frac((row or {}).get("turnover_fraction"))
    features = {
        "comparable_close": _enc(comparable_close),
        "amount_cny": _enc(amount),
        "turnover_fraction": _enc(turnover),
        "returns": {},
        "amount_change": {},
        "ma": {},
        "ma_reasons": {},
        "amount_mean": {},
        "amount_median": {},
        "turnover_mean": {},
    }
    instrument_bars = bars_by_iid[iid]
    for window in RET_WINDOWS:
        previous_index = position - window
        previous = instrument_bars.get(sessions[previous_index]) if previous_index >= 0 else None
        previous_close = _frac((previous or {}).get("comparable_close"))
        value = comparable_close / previous_close - 1 if comparable_close is not None and previous_close and previous_close > 0 and basis and previous and previous.get("comparable_basis") == basis else None
        features["returns"][str(window)] = _enc(value)
    for window in AMOUNT_CHANGE_WINDOWS:
        previous_index = position - window
        previous = instrument_bars.get(sessions[previous_index]) if previous_index >= 0 else None
        previous_amount = _frac((previous or {}).get("amount_cny"))
        value = amount / previous_amount - 1 if amount is not None and previous_amount and previous_amount > 0 else None
        features["amount_change"][str(window)] = _enc(value)
    for window in MA_WINDOWS:
        if position - window + 1 < 0:
            features["ma"][str(window)] = None
            features["ma_reasons"][str(window)] = f"TCT_MA{window}_WINDOW_UNAVAILABLE"
            continue
        rows = [instrument_bars.get(day) for day in sessions[position - window + 1:position + 1]]
        if any(value is None for value in rows):
            features["ma"][str(window)] = None
            features["ma_reasons"][str(window)] = f"TCT_MA{window}_PRICE_WINDOW_INCOMPLETE"
            continue
        if not _same_basis(rows):
            features["ma"][str(window)] = None
            features["ma_reasons"][str(window)] = f"TCT_MA{window}_COMPARABLE_BASIS_MISMATCH"
            continue
        closes = [_frac(value.get("comparable_close")) for value in rows]
        features["ma"][str(window)] = _enc(sum(closes, Fraction(0)) / window)
        features["ma_reasons"][str(window)] = None
    for window in ROLLING_WINDOWS:
        if position - window + 1 < 0:
            continue
        rows = [instrument_bars.get(day) for day in sessions[position - window + 1:position + 1]]
        amounts = [_frac((value or {}).get("amount_cny")) for value in rows]
        turnovers = [_frac((value or {}).get("turnover_fraction")) for value in rows]
        if len(amounts) == window and all(value is not None for value in amounts):
            features["amount_mean"][str(window)] = _enc(sum(amounts, Fraction(0)) / window)
            features["amount_median"][str(window)] = _enc(_median(amounts))
        if len(turnovers) == window and all(value is not None for value in turnovers):
            features["turnover_mean"][str(window)] = _enc(sum(turnovers, Fraction(0)) / window)
    return {
        "store_version": STORE_VERSION,
        "feature_spec_version": FEATURE_SPEC_VERSION,
        "feature_spec_hash": identity["feature_spec_hash"],
        "strategy_dependency_version": identity["strategy_dependency_version"],
        "request_as_of_session": identity["request_as_of_session"],
        "instrument_id": iid,
        "session": session,
        "comparable_basis": basis or "UNAVAILABLE",
        "dependency_identities": dependency_identities,
        "features": features,
        "cache_role": "DETERMINISTIC_DERIVED_FACTS_NOT_STRATEGY_DECISIONS",
    }


def _target_indices(ctx):
    from .stage_aware_momentum import CONFIG

    end_index = ctx.sessions.index(ctx.observation_date) if ctx.observation_date in ctx.sessions else None
    if end_index is None:
        return []
    seed = CONFIG["research_seed_parameters"]
    slope_lookback = int(seed["trend_state"]["slope_lookback_sessions"])
    strength_window = int(seed["strength_momentum"]["equal_window_sessions"])
    indices = set(range(max(0, end_index - 25), end_index + 1))
    for value in (end_index - 1, end_index - slope_lookback, end_index - strength_window):
        if value >= 0:
            indices.add(value)
    return sorted(indices)


def build_missing_features(ctx, identity, missing_keys, *, dependency_plan=None, source_view=None):
    if not missing_keys:
        return []
    target_sessions = sorted({session for _, session in missing_keys}, key=ctx.sessions.index)
    target_indices = [ctx.sessions.index(session) for session in target_sessions if session in ctx.sessions]
    if not target_indices:
        return []
    left = max(0, min(target_indices) - max(MA_WINDOWS) + 1)
    right = max(target_indices)
    source_sessions = ctx.sessions[left:right + 1]
    instrument_ids = sorted({iid for iid, _ in missing_keys})
    selected = _selected_compact_bars(ctx, source_sessions, instrument_ids, source_view=source_view)
    bars_by_iid = defaultdict(dict)
    for (iid, session), row in selected.items():
        bars_by_iid[iid][session] = row
    rows = []
    for iid, session in sorted(missing_keys):
        position = ctx.sessions.index(session)
        row = bars_by_iid[iid].get(session)
        dependency_identities = dependency_plan[(iid, _date_text(session))] if dependency_plan else {}
        rows.append(_feature_row(identity, iid, session, row, ctx.sessions, bars_by_iid, position, dependency_identities))
    return rows


def _ensure_stage_aware_derived_features_batch(ctx, store: DerivedFeatureStore, *, input_hash, mode, instrument_ids):
    from .e2e_tracing import measured_stage
    started = time.perf_counter()
    identity = identity_for_context(ctx, input_hash)
    sessions = [ctx.sessions[index] for index in _target_indices(ctx)]
    session_by_text = {_date_text(session): session for session in sessions}
    instrument_ids = list(instrument_ids)
    session_positions = {_date_text(value): index for index, value in enumerate(ctx.sessions)}
    source_sessions = sorted({
        source_session
        for session in sessions
        for family in MATERIALIZED_FAMILIES
        for source_session in _source_session_range(ctx, session, family)
    }, key=session_positions.__getitem__)
    source_view_started = time.perf_counter()
    from .source_resolution import canonical_price_source_view

    with measured_stage("SOURCE_RESOLUTION", item_count=len(source_sessions) * len(instrument_ids)) as span:
        source_view = canonical_price_source_view(ctx, source_sessions, instrument_ids)
        span["metadata"] = {**source_view.get("work_counters", {}), "instrument_count": len(instrument_ids),
            "first_instrument": instrument_ids[0] if instrument_ids else None,
            "last_instrument": instrument_ids[-1] if instrument_ids else None}
    after_source_view = time.perf_counter()
    dependency_plan_started = time.perf_counter()
    with measured_stage("DEPENDENCY_PLANNING", item_count=len(sessions) * len(instrument_ids)) as span:
        dependency_plan = dependency_plan_for_context(ctx, sessions, instrument_ids, source_view=source_view)
        span["metadata"] = source_view.get("dependency_work_counters", {})
    after_dependency_plan = time.perf_counter()
    with measured_stage("CACHE_LOOKUP") as span:
        cached = store.get_many(identity, instrument_ids, sessions, dependency_plan=dependency_plan)
        span["cache_stats"] = getattr(store, "last_get_many_stats", {})
    after_lookup = time.perf_counter()
    wanted = {(iid, _date_text(session)) for iid in instrument_ids for session in sessions}
    missing = [(iid, session_by_text[session]) for iid, session in sorted(wanted - set(cached))]
    built_count = 0
    put_stats = {
        "inserted_family_rows": 0,
        "reused_existing_family_rows": 0,
        "integrity_conflicts": 0,
        "conflicts": [],
    }
    chunk_size = 250
    computation_seconds = 0.0
    write_seconds = 0.0
    after_build = time.perf_counter()
    if missing:
        missing_by_instrument = defaultdict(list)
        for iid, session in missing:
            missing_by_instrument[iid].append((iid, session))
        missing_iids = sorted(missing_by_instrument)
        for left in range(0, len(missing_iids), chunk_size):
            chunk_iids = missing_iids[left:left + chunk_size]
            chunk_missing = [
                pair
                for iid in chunk_iids
                for pair in missing_by_instrument[iid]
            ]
            chunk_started = time.perf_counter()
            with measured_stage("FEATURE_COMPUTATION", item_count=len(chunk_missing)):
                built = build_missing_features(ctx, identity, chunk_missing, dependency_plan=dependency_plan, source_view=source_view)
            computation_seconds += time.perf_counter() - chunk_started
            built_count += len(built)
            if built:
                chunk_started = time.perf_counter()
                with measured_stage("FEATURE_CACHE_WRITE", item_count=len(built)):
                    chunk_put_stats = store.put_many(identity, built)
                write_seconds += time.perf_counter() - chunk_started
                put_stats["inserted_family_rows"] += chunk_put_stats.get("inserted_family_rows", 0)
                put_stats["reused_existing_family_rows"] += chunk_put_stats.get("reused_existing_family_rows", 0)
                put_stats["integrity_conflicts"] += chunk_put_stats.get("integrity_conflicts", 0)
                put_stats["conflicts"].extend(chunk_put_stats.get("conflicts", []))
                cached.update({(row["instrument_id"], _date_text(row["session"])): _runtime_row(row) for row in built})
            del built
    after_write = time.perf_counter()
    ctx.scalar_cache[CONTEXT_KEY] = cached
    lookup_stats = getattr(store, "last_get_many_stats", {}) or {}
    stats = {
        "store_version": STORE_VERSION,
        "feature_spec_version": FEATURE_SPEC_VERSION,
        "mode": mode,
        "identity": identity,
        "legacy_store": {"store_version": LEGACY_STORE_VERSION, "read_policy": "IGNORED_READ_ONLY"},
        "target_sessions": [_date_text(session) for session in sessions],
        "eligible_count": len(instrument_ids),
        "requested_feature_rows": len(wanted),
        "cache_hits": lookup_stats.get("row_lifecycle", {}).get("full_row_reused", len(wanted) - len(missing)),
        "cache_misses": len(missing),
        "built_rows": built_count,
        "reused": lookup_stats.get("row_lifecycle", {}).get("full_row_reused", len(wanted) - len(missing)),
        "full_row_reused": lookup_stats.get("row_lifecycle", {}).get("full_row_reused", len(wanted) - len(missing)),
        "partial_family_reused_rows": lookup_stats.get("row_lifecycle", {}).get("partial_family_reused", 0),
        "new_rows": lookup_stats.get("row_lifecycle", {}).get("new_rows", 0),
        "newly_computed": built_count,
        "newly_built": built_count,
        "invalidated": lookup_stats.get("invalidated_rows", 0),
        "invalidated_rows": lookup_stats.get("invalidated_rows", 0),
        "missing_cache_rows": lookup_stats.get("missing_cache_rows", 0),
        "integrity_conflicts": put_stats.get("integrity_conflicts", 0),
        "family_cache": {
            "requested": lookup_stats.get("family_requested", len(wanted) * len(MATERIALIZED_FAMILIES)),
            "hits": lookup_stats.get("family_hits", 0),
            "misses": lookup_stats.get("family_misses", len(missing) * len(MATERIALIZED_FAMILIES)),
            "inserted": put_stats.get("inserted_family_rows", 0),
            "reused_existing_on_put": put_stats.get("reused_existing_family_rows", 0),
            "invalidated": lookup_stats.get("invalidated_family_rows", 0),
            "by_family": lookup_stats.get("family_by_family", {}),
            "lifecycle": lookup_stats.get("family_lifecycle", {}),
        },
        "row_lifecycle": lookup_stats.get("row_lifecycle", {}),
        "cache_lifecycle_policy": "R16_CACHE_ROW_FAMILY_LIFECYCLE_V1",
        "cache_hit_rate": (len(wanted) - len(missing)) / len(wanted) if wanted else None,
        "reuse_ratio": lookup_stats.get("row_lifecycle", {}).get("full_row_reused", len(wanted) - len(missing)) / len(wanted) if wanted else None,
        "bytes_read": None,
        "bytes_written": None,
        "timing_seconds": {
            "pre_cache_hit": after_lookup - started,
            "source_resolution": after_source_view - source_view_started,
            "dependency_plan": after_dependency_plan - dependency_plan_started,
            "lookup": after_lookup - after_dependency_plan,
            "build_missing_setup": after_build - after_lookup,
            "build_and_write_missing_chunks": after_write - after_build,
            "build_missing": computation_seconds,
            "write_missing": write_seconds,
            "total": after_write - started,
        },
        "incremental_semantics": "FEATURE_DEPENDENCY_FINGERPRINT_REUSE_ACROSS_REQUEST_SESSIONS",
        "authority": "CACHE_MISS_REBUILDS_FROM_MARKET_INPUT_HISTORY_ARTIFACT_AND_SOURCE_EVIDENCE",
        "source_work_counters": source_view.get("work_counters", {}),
        "dependency_work_counters": source_view.get("dependency_work_counters", {}),
    }
    if not missing:
        from .e2e_tracing import current_trace
        trace = current_trace()
        if trace is not None:
            trace.mark("FEATURE_COMPUTATION", "SKIPPED", "ALL_REQUIRED_FEATURE_FAMILIES_REUSED")
    # Drop only this context's ownership. External references and dependency
    # first/last tokens remain valid; never clear the shared view contents.
    for key, value in list(ctx.scalar_cache.items()):
        if isinstance(key, tuple) and key and key[0] == "R16_CANONICAL_PRICE_SOURCE_VIEW" and value is source_view:
            del ctx.scalar_cache[key]
    ctx.scalar_cache[CONTEXT_STATS_KEY] = stats
    ctx.scalar_cache[LEGACY_CONTEXT_STATS_KEY] = stats
    return stats


def _sum_counters(left, right):
    """Merge disjoint instrument counts, retaining nested lifecycle categories."""
    for key, value in right.items():
        if isinstance(value, dict):
            _sum_counters(left.setdefault(key, {}), value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            left[key] = left.get(key, 0) + value
        else:
            raise TypeError("NON_NUMERIC_BATCH_COUNTER: " + key)


def ensure_stage_aware_derived_features(ctx, store: DerivedFeatureStore, *, input_hash, mode="DAILY_INCREMENTAL"):
    """Bound source/dependency retention to disjoint instrument batches.

    Each batch runs the same resolver, fingerprint, lookup and computation. No
    source window is truncated. Only compact runtime feature rows survive a
    batch; cache identities and payloads do not contain the batch boundary.
    """
    started = time.perf_counter()
    ids = list(ctx.eligible_ids)
    batches = [ids[left:left + SOURCE_BATCH_INSTRUMENTS]
               for left in range(0, len(ids), SOURCE_BATCH_INSTRUMENTS)] or [[]]
    cached, combined, lookups = {}, None, {}
    timings = []
    count_fields = (
        "eligible_count", "requested_feature_rows", "cache_hits", "cache_misses", "built_rows",
        "reused", "full_row_reused", "partial_family_reused_rows", "new_rows", "newly_computed",
        "newly_built", "invalidated", "invalidated_rows", "missing_cache_rows", "integrity_conflicts",
    )
    nested_fields = ("family_cache", "row_lifecycle", "timing_seconds", "source_work_counters", "dependency_work_counters")
    samples = []
    for index, batch in enumerate(batches):
        stats = _ensure_stage_aware_derived_features_batch(
            ctx, store, input_hash=input_hash, mode=mode, instrument_ids=batch)
        cached.update(ctx.scalar_cache.pop(CONTEXT_KEY))
        lookup = dict(getattr(store, "last_get_many_stats", {}) or {})
        samples.extend(lookup.pop("lifecycle_samples", []))
        _sum_counters(lookups, lookup)
        timings.append({"batch": index + 1, "instruments": len(batch),
                        "first_instrument": batch[0] if batch else None,
                        "last_instrument": batch[-1] if batch else None,
                        "timing_seconds": dict(stats["timing_seconds"])})
        if combined is None:
            # Subsequent summation must not mutate an earlier span's metadata.
            from copy import deepcopy
            combined = deepcopy(stats)
        else:
            for key in count_fields:
                combined[key] += stats[key]
            for key in nested_fields:
                _sum_counters(combined[key], stats[key])
        logging.getLogger(__name__).info(
            "R16_R1_FEATURE_BATCH processed=%s/%s batches=%s/%s elapsed_seconds=%.3f",
            min((index + 1) * SOURCE_BATCH_INSTRUMENTS, len(ids)), len(ids), index + 1,
            len(batches), time.perf_counter() - started)
    lookups["lifecycle_samples"] = sorted(samples, key=lambda row: row["cache_key"])[:50]
    store.last_get_many_stats = lookups
    requested = combined["requested_feature_rows"]
    combined["cache_hit_rate"] = (requested - combined["cache_misses"]) / requested if requested else None
    combined["reuse_ratio"] = combined["full_row_reused"] / requested if requested else None
    combined["bounded_source_batches"] = {"instrument_limit": SOURCE_BATCH_INSTRUMENTS,
        "batch_count": len(batches), "timings": timings,
        "policy": "DISJOINT_INSTRUMENTS_COMPLETE_SOURCE_WINDOWS_SAME_IDENTITIES",
        "timing_policy": "PRE_CACHE_HIT_AND_PHASES_SUM_DISJOINT_BATCH_WORK; TOTAL_IS_OUTER_WALL_CLOCK"}
    combined["timing_seconds"]["total"] = time.perf_counter() - started
    ctx.scalar_cache[CONTEXT_KEY] = cached
    ctx.scalar_cache[CONTEXT_STATS_KEY] = combined
    ctx.scalar_cache[LEGACY_CONTEXT_STATS_KEY] = combined
    return combined


def _context_feature(ctx, iid, end_index):
    cache = getattr(ctx, "scalar_cache", None)
    if cache is None or CONTEXT_KEY not in cache:
        return MISSING
    if end_index is None or end_index < 0 or end_index >= len(ctx.sessions):
        return None
    return cache[CONTEXT_KEY].get((iid, _date_text(ctx.sessions[end_index])))


def _features(row):
    return row.get("features") if isinstance(row, dict) else row


def ma_value_from_context(ctx, iid, end_index, window):
    row = _context_feature(ctx, iid, end_index)
    if row is MISSING:
        return MISSING
    if row is None:
        return None, f"TCT_MA{window}_WINDOW_UNAVAILABLE"
    features = _features(row) or {}
    if isinstance(features, tuple):
        index = MA_INDEX.get(window)
        value = _dec(features[2][index]) if index is not None else None
        reason = features[3][index] if index is not None else None
        return value, reason
    values = features.get("ma") or {}
    reasons = features.get("ma_reasons") or {}
    key = str(window)
    return _dec(values.get(key)), reasons.get(key)


def window_return_from_context(ctx, iid, end_index, lookback):
    row = _context_feature(ctx, iid, end_index)
    if row is MISSING:
        return MISSING
    if row is None:
        return None
    features = _features(row) or {}
    if isinstance(features, tuple):
        index = RET_INDEX.get(lookback)
        return _dec(features[0][index]) if index is not None else None
    return _dec(((features or {}).get("returns") or {}).get(str(lookback)))


def amount_change_from_context(ctx, iid, end_index, lookback):
    row = _context_feature(ctx, iid, end_index)
    if row is MISSING:
        return MISSING
    if row is None:
        return None
    features = _features(row) or {}
    if isinstance(features, tuple):
        index = AMOUNT_CHANGE_INDEX.get(lookback)
        return _dec(features[1][index]) if index is not None else None
    return _dec(((features or {}).get("amount_change") or {}).get(str(lookback)))


def cache_identity_with_overrides(identity, **overrides):
    """Small helper for invalidation tests and audit reports."""
    value = dict(identity)
    value.update(overrides)
    return value
