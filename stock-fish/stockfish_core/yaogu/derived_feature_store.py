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
import sqlite3
import time

from ..common import utc_now
from ..storage import IntegrityError, SQLiteStore
from .contracts import digest


STORE_VERSION = "DERIVED_FEATURE_STORE_V1"
FEATURE_SPEC_VERSION = "STAGE_AWARE_DERIVED_FEATURE_SPEC_V1"
RET_WINDOWS = (1, 3, 5, 10, 20)
AMOUNT_CHANGE_WINDOWS = (1, 3, 5, 10, 20)
MA_WINDOWS = (5, 10, 20, 50, 60, 120, 150, 200, 250)
ROLLING_WINDOWS = (5, 10, 20)
RET_INDEX = {value: index for index, value in enumerate(RET_WINDOWS)}
AMOUNT_CHANGE_INDEX = {value: index for index, value in enumerate(AMOUNT_CHANGE_WINDOWS)}
MA_INDEX = {value: index for index, value in enumerate(MA_WINDOWS)}
CONTEXT_KEY = "R14_DERIVED_FEATURES"
CONTEXT_STATS_KEY = "R14_DERIVED_FEATURE_STATS"
MISSING = object()


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


def feature_spec_hash():
    from .stage_aware_momentum import CONFIG, IMPLEMENTATION_VERSION, SPEC_REVISION

    return digest({
        "store_version": STORE_VERSION,
        "feature_spec_version": FEATURE_SPEC_VERSION,
        "ret_windows": RET_WINDOWS,
        "amount_change_windows": AMOUNT_CHANGE_WINDOWS,
        "ma_windows": MA_WINDOWS,
        "rolling_windows": ROLLING_WINDOWS,
        "stage_aware_implementation_version": IMPLEMENTATION_VERSION,
        "stage_aware_spec_revision": SPEC_REVISION,
        "stage_aware_config_hash": digest(CONFIG),
        "facts_are_not_final_decisions": True,
    })


def strategy_dependency_version():
    from .stage_aware_momentum import IMPLEMENTATION_VERSION, SPEC_REVISION

    return f"{IMPLEMENTATION_VERSION}:{SPEC_REVISION}:{FEATURE_SPEC_VERSION}"


def identity_for_context(ctx, input_hash):
    history_hash = ctx.bundle.history_artifact_hash or input_hash
    return {
        "store_version": STORE_VERSION,
        "feature_spec_version": FEATURE_SPEC_VERSION,
        "input_hash": input_hash,
        "history_artifact_hash": history_hash,
        "feature_spec_hash": feature_spec_hash(),
        "strategy_dependency_version": strategy_dependency_version(),
        "as_of_session": _date_text(ctx.observation_date),
        "comparable_basis_policy": "PER_ROW_BASIS_IN_CACHE_KEY_AND_WINDOW_BASIS_MATCH_REQUIRED",
        "pit_policy": "SOURCE_VISIBILITY_RESOLVED_BEFORE_FEATURE_MATERIALIZATION",
    }


class DerivedFeatureStore:
    def __init__(self, store: SQLiteStore):
        self.store = store
        with self.store.transaction() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS yaogu_derived_features_v1 (
                    cache_key TEXT PRIMARY KEY,
                    input_hash TEXT NOT NULL,
                    history_artifact_hash TEXT NOT NULL,
                    feature_spec_hash TEXT NOT NULL,
                    strategy_dependency_version TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    session TEXT NOT NULL,
                    comparable_basis TEXT NOT NULL,
                    as_of_session TEXT NOT NULL,
                    payload TEXT NOT NULL CHECK(json_valid(payload)),
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS yaogu_derived_features_lookup "
                "ON yaogu_derived_features_v1(input_hash,history_artifact_hash,feature_spec_hash,"
                "strategy_dependency_version,as_of_session,instrument_id,session)"
            )

    @staticmethod
    def cache_key(identity, instrument_id, session, comparable_basis):
        return "derived_feature_" + digest({
            "store_version": STORE_VERSION,
            "input_hash": identity["input_hash"],
            "history_artifact_hash": identity["history_artifact_hash"],
            "feature_spec_hash": identity["feature_spec_hash"],
            "strategy_dependency_version": identity["strategy_dependency_version"],
            "as_of_session": identity["as_of_session"],
            "instrument_id": instrument_id,
            "session": _date_text(session),
            "comparable_basis": comparable_basis or "UNAVAILABLE",
        })

    def get_many(self, identity, instrument_ids, sessions):
        wanted = set((iid, _date_text(session)) for session in sessions for iid in instrument_ids)
        if not wanted:
            return {}
        result = {}
        ids = list(instrument_ids)
        session_texts = [_date_text(session) for session in sessions]
        with self.store.transaction(write=False) as con:
            for left in range(0, len(ids), 500):
                chunk = ids[left:left + 500]
                placeholders_i = ",".join("?" for _ in chunk)
                placeholders_s = ",".join("?" for _ in session_texts)
                rows = con.execute(
                    "SELECT instrument_id,session,payload,payload_hash FROM yaogu_derived_features_v1 "
                    "WHERE input_hash=? AND history_artifact_hash=? AND feature_spec_hash=? "
                    "AND strategy_dependency_version=? AND as_of_session=? "
                    f"AND instrument_id IN ({placeholders_i}) AND session IN ({placeholders_s})",
                    [
                        identity["input_hash"],
                        identity["history_artifact_hash"],
                        identity["feature_spec_hash"],
                        identity["strategy_dependency_version"],
                        identity["as_of_session"],
                        *chunk,
                        *session_texts,
                    ],
                ).fetchall()
                for row in rows:
                    value = json.loads(row["payload"])
                    if _payload_hash(value) != row["payload_hash"]:
                        raise IntegrityError("DERIVED_FEATURE_CACHE_HASH_MISMATCH")
                    key = (row["instrument_id"], row["session"])
                    if key in wanted:
                        result[key] = _runtime_row(value)
        return result

    def put_many(self, identity, rows):
        now = utc_now().isoformat()
        with self.store.transaction() as con:
            for row in rows:
                payload_hash = _payload_hash(row)
                cache_key = self.cache_key(identity, row["instrument_id"], row["session"], row.get("comparable_basis"))
                con.execute(
                    """
                    INSERT INTO yaogu_derived_features_v1(
                        cache_key,input_hash,history_artifact_hash,feature_spec_hash,
                        strategy_dependency_version,instrument_id,session,comparable_basis,
                        as_of_session,payload,payload_hash,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        payload=excluded.payload,
                        payload_hash=excluded.payload_hash,
                        updated_at=excluded.updated_at
                    """,
                    (
                        cache_key,
                        identity["input_hash"],
                        identity["history_artifact_hash"],
                        identity["feature_spec_hash"],
                        identity["strategy_dependency_version"],
                        row["instrument_id"],
                        row["session"],
                        row.get("comparable_basis") or "UNAVAILABLE",
                        identity["as_of_session"],
                        _json(row),
                        payload_hash,
                        now,
                        now,
                    ),
                )

    def clear_identity(self, identity):
        with self.store.transaction() as con:
            con.execute(
                "DELETE FROM yaogu_derived_features_v1 WHERE input_hash=? AND history_artifact_hash=? "
                "AND feature_spec_hash=? AND strategy_dependency_version=? AND as_of_session=?",
                (
                    identity["input_hash"],
                    identity["history_artifact_hash"],
                    identity["feature_spec_hash"],
                    identity["strategy_dependency_version"],
                    identity["as_of_session"],
                ),
            )

    def count_identity(self, identity):
        with self.store.transaction(write=False) as con:
            row = con.execute(
                "SELECT count(*) AS n FROM yaogu_derived_features_v1 WHERE input_hash=? "
                "AND history_artifact_hash=? AND feature_spec_hash=? AND strategy_dependency_version=? "
                "AND as_of_session=?",
                (
                    identity["input_hash"],
                    identity["history_artifact_hash"],
                    identity["feature_spec_hash"],
                    identity["strategy_dependency_version"],
                    identity["as_of_session"],
                ),
            ).fetchone()
        return int(row["n"])


def _revision_key(row):
    return (str(row.get("source_published_at")), str(row.get("ingested_at")), str(row.get("first_seen_at")))


def _selected_compact_bars(ctx, source_sessions, instrument_ids):
    from .history_artifacts import daily_bar_payload

    wanted_sessions = set(_date_text(session) for session in source_sessions)
    wanted_ids = set(instrument_ids)
    grouped = defaultdict(list)

    def consider(value, row_hash=None):
        row = daily_bar_payload(value)
        session = _date_text(row.get("session"))
        iid = row.get("instrument_id")
        if session not in wanted_sessions or iid not in wanted_ids:
            return
        if row.get("quality") == "INVALID":
            return
        proof = ctx.resolver.resolve(row, instrument_id=iid, session=session, require_session=True, record_hash=row_hash)
        if not proof.allowed:
            ctx.rejected_evidence.append({"kind": "BAR", "instrument_id": iid, "session": session, "reason_codes": list(proof.reason_codes)})
            return
        grouped[(iid, session)].append((_revision_key(row), digest(row), {
            "instrument_id": iid,
            "session": session,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "reference_prev_close": row.get("reference_prev_close"),
            "comparable_close": row.get("comparable_close"),
            "comparable_basis": row.get("comparable_basis"),
            "amount_cny": row.get("amount_cny"),
            "turnover_fraction": row.get("turnover_fraction"),
        }))

    if ctx.bundle.history_artifact_ref:
        owner = ctx.bundle._history_store
        manifest = owner.manifest(ctx.bundle.history_artifact_ref, ctx.bundle.history_artifact_hash)
        partitions = [part["partition_hash"] for part in manifest["partitions"] if part["session"] in wanted_sessions]
        with owner.store.transaction(write=False) as con:
            for left in range(0, len(partitions), 250):
                batch = partitions[left:left + 250]
                if not batch:
                    continue
                placeholders = ",".join("?" for _ in batch)
                cursor = con.execute(
                    "SELECT m.row_hash,m.instrument_id,m.session,r.payload "
                    "FROM history_partition_members m "
                    "JOIN history_evidence_rows r ON r.row_hash=m.row_hash "
                    f"WHERE m.partition_hash IN ({placeholders}) AND m.kind='bar' "
                    "ORDER BY m.instrument_id,m.session,m.row_hash",
                    batch,
                )
                for row in cursor:
                    payload = row["payload"]
                    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != row["row_hash"]:
                        raise IntegrityError("CORRUPT_HISTORY_ROW")
                    consider(json.loads(payload), row_hash=row["row_hash"])
    for bar in ctx.bundle.bars:
        consider(bar)

    selected = {}
    for key, rows in grouped.items():
        latest = max(row[0] for row in rows)
        choices = [row for row in rows if row[0] == latest]
        if len({row[1] for row in choices}) == 1:
            selected[key] = choices[0][2]
        else:
            iid, session = key
            ctx.rejected_evidence.append({"kind": "BAR", "instrument_id": iid, "session": session, "reason_codes": ["CONFLICTING_PRICE_REVISION"]})
    return selected


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


def _feature_row(identity, iid, session, row, sessions, bars_by_iid, position):
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
        "input_hash": identity["input_hash"],
        "history_artifact_hash": identity["history_artifact_hash"],
        "feature_spec_hash": identity["feature_spec_hash"],
        "strategy_dependency_version": identity["strategy_dependency_version"],
        "as_of_session": identity["as_of_session"],
        "instrument_id": iid,
        "session": session,
        "comparable_basis": basis or "UNAVAILABLE",
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


def build_missing_features(ctx, identity, missing_keys):
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
    selected = _selected_compact_bars(ctx, source_sessions, instrument_ids)
    bars_by_iid = defaultdict(dict)
    for (iid, session), row in selected.items():
        bars_by_iid[iid][session] = row
    rows = []
    for iid, session in sorted(missing_keys):
        position = ctx.sessions.index(session)
        row = bars_by_iid[iid].get(session)
        rows.append(_feature_row(identity, iid, session, row, ctx.sessions, bars_by_iid, position))
    return rows


def ensure_stage_aware_derived_features(ctx, store: DerivedFeatureStore, *, input_hash, mode="DAILY_INCREMENTAL"):
    started = time.perf_counter()
    identity = identity_for_context(ctx, input_hash)
    sessions = [ctx.sessions[index] for index in _target_indices(ctx)]
    session_by_text = {_date_text(session): session for session in sessions}
    instrument_ids = list(ctx.eligible_ids)
    cached = store.get_many(identity, instrument_ids, sessions)
    after_lookup = time.perf_counter()
    wanted = {(iid, _date_text(session)) for iid in instrument_ids for session in sessions}
    missing = [(iid, session_by_text[session]) for iid, session in sorted(wanted - set(cached))]
    built = build_missing_features(ctx, identity, missing)
    after_build = time.perf_counter()
    if built:
        store.put_many(identity, built)
        cached.update({(row["instrument_id"], _date_text(row["session"])): _runtime_row(row) for row in built})
    after_write = time.perf_counter()
    ctx.scalar_cache[CONTEXT_KEY] = cached
    stats = {
        "store_version": STORE_VERSION,
        "feature_spec_version": FEATURE_SPEC_VERSION,
        "mode": mode,
        "identity": identity,
        "target_sessions": sessions,
        "eligible_count": len(instrument_ids),
        "requested_feature_rows": len(wanted),
        "cache_hits": len(wanted) - len(missing),
        "cache_misses": len(missing),
        "built_rows": len(built),
        "cache_hit_rate": (len(wanted) - len(missing)) / len(wanted) if wanted else None,
        "timing_seconds": {
            "lookup": after_lookup - started,
            "build_missing": after_build - after_lookup,
            "write_missing": after_write - after_build,
            "total": after_write - started,
        },
        "incremental_semantics": "BUILD_ONLY_MISSING_INSTRUMENT_SESSION_ROWS_FOR_THIS_AS_OF_AND_FEATURE_SPEC",
        "authority": "CACHE_MISS_REBUILDS_FROM_MARKET_INPUT_HISTORY_ARTIFACT_AND_SOURCE_EVIDENCE",
    }
    ctx.scalar_cache[CONTEXT_STATS_KEY] = stats
    return stats


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
