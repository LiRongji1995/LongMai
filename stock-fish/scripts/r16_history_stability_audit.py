"""R16 deterministic historical source stability audit.

The full-universe audit is intentionally set-based: it streams normalized
source rows from immutable history partitions, aggregates the selected revision
per (instrument, session, price-source-family), and only then compares Day T
against Day T+1.  The canonical resolver is still used for targeted correctness
checks over every discovered discrepancy plus representative PIT cases.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time as perf_time
from typing import NamedTuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from stockfish_core.storage import SQLiteStore  # noqa: E402
from stockfish_core.yaogu.contracts import DailyBar, MarketInput  # noqa: E402
from stockfish_core.yaogu.history_artifacts import (  # noqa: E402
    bind_history,
    daily_bar_payload,
    digest_market_input,
)
from stockfish_core.yaogu.derived_feature_store import MATERIALIZED_FAMILIES, _source_session_range  # noqa: E402
from stockfish_core.yaogu.source_resolution import (  # noqa: E402
    CANONICAL_SOURCE_RESOLVER_VERSION,
    CONFLICTING_PRICE_REVISION,
    MISSING_VISIBLE_BAR,
    PRICE_SOURCE_REQUIREMENT,
    _candidate,
    _receipt_index,
    _row_hash,
    canonical_price_source_view,
    source_token,
)
from stockfish_core.yaogu.strategy_experiments import _prepare_context  # noqa: E402


SH = ZoneInfo("Asia/Shanghai")
DEFAULT_T = "market_63f83798f5098898debce30e0810a0c54f6a0daffdc75ac6404d47d5f8488a23"
DEFAULT_TP1 = "market_04d95df6f6f699c7f88353d181308159526865877a666a7c3efb84be01b9c08d"
AUDIT_VERSION = "R16_FAST_SET_BASED_HISTORY_STABILITY_AUDIT_V2"
SOURCE_FAMILY = "PRICE_DAILY_BAR"
REVISION_FIELDS = ("source_published_at", "ingested_at", "first_seen_at")

COMPARE_VALUE_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "reference_prev_close",
    "volume_shares",
    "amount_cny",
    "turnover_fraction",
    "comparable_close",
    "comparable_basis",
)
VALUE_INDEX = {field: index for index, field in enumerate(COMPARE_VALUE_FIELDS)}
VIOLATION_CATEGORIES = {
    "SOURCE_REPLACED",
    "SOURCE_DISAPPEARED",
    "BUG_IN_ARTIFACT_BUILD",
    "BUG_IN_MERGE/RESOLUTION",
    "SOURCE_BECAME_UNAVAILABLE",
    "UNKNOWN",
}


class AuditToken(NamedTuple):
    status: str
    row_hash: str | None
    source_kind: str | None
    revision_key: tuple | None
    source_revision: tuple | None
    value_hash: str | None
    values: tuple


def _load_bundle(store, input_id):
    raw = store.get_document_bytes("candidate_inputs", input_id, expected_hash=input_id[7:])
    if raw is None:
        raise RuntimeError(f"candidate input missing or corrupt: {input_id}")
    return bind_history(MarketInput.model_validate_json(raw), store)


def _cutoff(bundle, value):
    if value:
        return datetime.fromisoformat(value)
    receipt_times = [receipt.ingested_at for receipt in bundle.receipts if receipt.ingested_at is not None]
    if receipt_times:
        return max(receipt_times)
    return datetime.combine(bundle.observation_date, time(20, 30), SH)


def _date_text(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)[:10]


def _json_line(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class ProgressLogger:
    def __init__(self, checkpoint_json=None, *, interval_seconds=10.0):
        self.started = perf_time.perf_counter()
        self.last_emit = 0.0
        self.checkpoint_json = Path(checkpoint_json) if checkpoint_json else None
        self.interval_seconds = max(float(interval_seconds), 0.0)
        self.latest = {}
        if self.checkpoint_json:
            self.checkpoint_json.parent.mkdir(parents=True, exist_ok=True)

    def event(self, stage, *, processed=None, total=None, counts=None, force=False, **metadata):
        now = perf_time.perf_counter()
        if not force and self.interval_seconds and now - self.last_emit < self.interval_seconds:
            return
        elapsed = now - self.started
        percent = None
        eta_seconds = None
        if processed is not None and total:
            percent = min(100.0, max(0.0, processed * 100.0 / total))
            if processed > 0:
                eta_seconds = max(0.0, elapsed * (total - processed) / processed)
        payload = {
            "event": "R16_HISTORY_AUDIT_PROGRESS",
            "stage": stage,
            "processed": processed,
            "total": total,
            "percent": round(percent, 4) if percent is not None else None,
            "elapsed_seconds": round(elapsed, 3),
            "eta_seconds": round(eta_seconds, 3) if eta_seconds is not None else None,
            "counts": dict(counts or {}),
            **metadata,
        }
        self.latest = payload
        print(_json_line(payload), flush=True)
        if self.checkpoint_json:
            checkpoint_text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            tmp = self.checkpoint_json.with_suffix(self.checkpoint_json.suffix + ".tmp")
            try:
                tmp.write_text(checkpoint_text, encoding="utf-8")
                tmp.replace(self.checkpoint_json)
            except PermissionError:
                self.checkpoint_json.write_text(checkpoint_text, encoding="utf-8")
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        self.last_emit = now

    def finish(self, **metadata):
        self.event("DONE", force=True, **metadata)


def _target_source_sessions(ctx):
    sessions = tuple(ctx.sessions)
    target_sessions = sessions[-min(26, len(sessions)) :]
    positions = {session: index for index, session in enumerate(sessions)}
    source = sorted({
        source_session
        for session in target_sessions
        for family in MATERIALIZED_FAMILIES
        for source_session in _source_session_range(ctx, session, family)
    }, key=positions.__getitem__)
    return target_sessions, source


def _after_cutoff(value, cutoff):
    if not value:
        return False
    try:
        return datetime.fromisoformat(str(value)) > cutoff
    except ValueError:
        return False


def _has_newer_visible_revision(token, cutoff):
    revision = token.source_revision or ()
    return any(_after_cutoff(value, cutoff) for value in revision)


def _lost_comparable_payload(t_token, tp1_token):
    comparable_was_known = _value(t_token, "comparable_close") is not None and _value(t_token, "comparable_basis") is not None
    comparable_now_missing = _value(tp1_token, "comparable_close") is None or _value(tp1_token, "comparable_basis") is None
    raw_fields = ("open", "high", "low", "close", "reference_prev_close", "volume_shares", "amount_cny", "turnover_fraction")
    raw_same = all(_value(t_token, field) == _value(tp1_token, field) for field in raw_fields)
    return comparable_was_known and comparable_now_missing and raw_same


def _classify(t_token, tp1_token, session, day_t, day_tp1, cutoff_t):
    session = _date_text(session)
    day_t = _date_text(day_t)
    day_tp1 = _date_text(day_tp1)
    if session > day_t and session <= day_tp1:
        return "APPENDED_NEW_SESSION"
    if t_token.status == "MISSING" and tp1_token.status == "MISSING":
        return "UNCHANGED"
    if t_token.status == "SELECTED" and tp1_token.status == "SELECTED":
        if t_token.row_hash == tp1_token.row_hash:
            return "UNCHANGED"
        if t_token.value_hash == tp1_token.value_hash:
            return "LEGITIMATE_SOURCE_REVISION"
        if _lost_comparable_payload(t_token, tp1_token):
            return "BUG_IN_ARTIFACT_BUILD"
        if _has_newer_visible_revision(tp1_token, cutoff_t):
            return "LEGITIMATE_SOURCE_REVISION"
        if t_token.source_kind != tp1_token.source_kind:
            return "SOURCE_REPLACED"
        return "SOURCE_REPLACED"
    if t_token.status == "SELECTED" and tp1_token.status == "MISSING":
        return "SOURCE_DISAPPEARED"
    if t_token.status == "MISSING" and tp1_token.status == "SELECTED":
        return "LEGITIMATE_SOURCE_REVISION" if _has_newer_visible_revision(tp1_token, cutoff_t) else "BUG_IN_ARTIFACT_BUILD"
    if "CONFLICT" in {t_token.status, tp1_token.status}:
        return "BUG_IN_MERGE/RESOLUTION"
    return "UNKNOWN"


def _missing_token(iid, session):
    return AuditToken("MISSING", MISSING_VISIBLE_BAR, None, None, None, MISSING_VISIBLE_BAR, tuple(None for _ in COMPARE_VALUE_FIELDS))


def _compact_token(token):
    values = token.get("values") or {}
    revision = token.get("source_revision") or {}
    revision_tuple = tuple(revision.get(field) for field in REVISION_FIELDS) if isinstance(revision, dict) else tuple(revision or ())
    return AuditToken(
        token.get("status"),
        token.get("row_hash"),
        token.get("source_kind"),
        tuple(token.get("revision_key") or ()) or None,
        revision_tuple if any(value is not None for value in revision_tuple) else None,
        token.get("value_hash"),
        tuple(values.get(field) for field in COMPARE_VALUE_FIELDS),
    )


def _value(token, field):
    return token.values[VALUE_INDEX[field]]


def _token_for(selected, iid, session):
    return selected.get((iid, _date_text(session))) or _missing_token(iid, session)


def _token_identity(token):
    return token


def _token_to_dict(token, iid=None, session=None):
    source_revision = None
    if token.source_revision is not None:
        source_revision = {field: token.source_revision[index] for index, field in enumerate(REVISION_FIELDS)}
    values = {
        field: token.values[index]
        for index, field in enumerate(COMPARE_VALUE_FIELDS)
        if token.values[index] is not None
    }
    return {
        "resolver_version": CANONICAL_SOURCE_RESOLVER_VERSION,
        "requirement": PRICE_SOURCE_REQUIREMENT,
        "source_family": SOURCE_FAMILY,
        "instrument_id": iid,
        "session": _date_text(session) if session is not None else None,
        "status": token.status,
        "row_hash": token.row_hash,
        "source_kind": token.source_kind,
        "revision_key": token.revision_key,
        "source_revision": source_revision,
        "value_hash": token.value_hash,
        "values": values,
    }


class SelectedSourceAccumulator:
    def __init__(self, ctx, wanted_sessions, wanted_ids):
        self.ctx = ctx
        self.wanted_sessions = {_date_text(session) for session in wanted_sessions}
        self.wanted_ids = set(wanted_ids)
        self.receipts = _receipt_index(ctx.bundle)
        self.latest = {}
        self.stats = Counter()
        self.rejections = []

    def consider(self, value, source_kind, provided_hash=None):
        self.stats["candidate_rows_seen"] += 1
        row = daily_bar_payload(value)
        session = _date_text(row.get("session"))
        iid = row.get("instrument_id")
        if session not in self.wanted_sessions or iid not in self.wanted_ids:
            self.stats["candidate_rows_out_of_scope"] += 1
            return
        row_hash = _row_hash(value, provided_hash)
        if row.get("quality") == "INVALID":
            self._reject(iid, session, source_kind, row_hash, ["INVALID_PRICE_QUALITY"])
            return
        proof = self.ctx.resolver.resolve(row, instrument_id=iid, session=session, require_session=True, record_hash=row_hash)
        if not proof.allowed:
            self._reject(iid, session, source_kind, row_hash, list(proof.reason_codes))
            return
        candidate = self._compact_candidate(_candidate(source_kind, row, row_hash, self.receipts))
        key = (iid, session)
        current = self.latest.get(key)
        if current is None or candidate["revision_key"] > current["revision_key"]:
            if current is not None:
                self.stats["lower_revision_rows_ignored"] += len(current["candidates"])
            self.latest[key] = {"revision_key": candidate["revision_key"], "candidates": [candidate]}
        elif candidate["revision_key"] == current["revision_key"]:
            current["candidates"].append(candidate)
            self.stats["same_revision_candidate_rows"] += 1
        else:
            self.stats["lower_revision_rows_ignored"] += 1
        self.stats["candidate_rows_allowed"] += 1
        self.stats[f"source_kind_{source_kind}"] += 1

    @staticmethod
    def _compact_candidate(candidate):
        values = candidate.get("values") or {}
        revision = candidate.get("source_revision") or {}
        return {
            "source_kind": candidate.get("source_kind"),
            "row_hash": candidate.get("row_hash"),
            "revision_key": tuple(candidate.get("revision_key") or ()),
            "source_revision": tuple(revision.get(field) for field in REVISION_FIELDS),
            "value_hash": candidate.get("value_hash"),
            "values": tuple(values.get(field) for field in COMPARE_VALUE_FIELDS),
        }

    def _reject(self, iid, session, source_kind, row_hash, reason_codes):
        self.stats["candidate_rows_rejected"] += 1
        if len(self.rejections) < 50:
            self.rejections.append({
                "instrument": iid,
                "session": session,
                "source_kind": source_kind,
                "row_hash": row_hash,
                "reason_codes": reason_codes,
            })

    def finalize(self):
        selected = {}
        conflict_count = 0
        for (iid, session), state in self.latest.items():
            candidates = state["candidates"]
            row_hashes = {candidate["row_hash"] for candidate in candidates}
            if len(row_hashes) != 1:
                conflict_count += 1
                selected[(iid, session)] = AuditToken(
                    "CONFLICT",
                    CONFLICTING_PRICE_REVISION,
                    "CONFLICT",
                    tuple(state["revision_key"]),
                    None,
                    CONFLICTING_PRICE_REVISION,
                    tuple(None for _ in COMPARE_VALUE_FIELDS),
                )
                continue
            chosen = candidates[0]
            selected[(iid, session)] = AuditToken(
                "SELECTED",
                chosen["row_hash"],
                chosen["source_kind"],
                tuple(chosen["revision_key"]),
                tuple(chosen["source_revision"]),
                chosen["value_hash"],
                tuple(chosen["values"]),
            )
        self.stats["selected_pairs"] = len(selected)
        self.stats["conflict_pairs"] = conflict_count
        return selected, dict(self.stats)


def _history_partitions(ctx, wanted_sessions):
    if not ctx.bundle.history_artifact_ref:
        return []
    owner = ctx.bundle._history_store
    manifest = owner.manifest(ctx.bundle.history_artifact_ref, ctx.bundle.history_artifact_hash)
    wanted = {_date_text(session) for session in wanted_sessions}
    return [part["partition_hash"] for part in manifest["partitions"] if part["session"] in wanted]


def _read_history_rows(ctx, wanted_sessions, wanted_ids, accumulator, logger, side, *, partition_batch_size=64):
    partitions = _history_partitions(ctx, wanted_sessions)
    wanted_ids = sorted(set(wanted_ids))
    use_id_filter = len(wanted_ids) <= 2000
    rows_read = 0
    logger.event(
        f"{side}_HISTORY_SET_READ_START",
        processed=0,
        total=len(partitions),
        force=True,
        partition_count=len(partitions),
        instrument_filter_count=len(wanted_ids),
        sql_instrument_filter=use_id_filter,
    )
    if not partitions:
        return
    with ctx.bundle._history_store.store.transaction(write=False) as con:
        for left in range(0, len(partitions), partition_batch_size):
            batch = partitions[left : left + partition_batch_size]
            placeholders_p = ",".join("?" for _ in batch)
            if use_id_filter:
                for id_left in range(0, len(wanted_ids), 500):
                    id_batch = wanted_ids[id_left : id_left + 500]
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
                            raise RuntimeError("CORRUPT_HISTORY_ROW")
                        accumulator.consider(json.loads(payload), "HISTORY_ARTIFACT", row["row_hash"])
                        rows_read += 1
                        logger.event(
                            f"{side}_HISTORY_SET_READ_PROGRESS",
                            processed=left + len(batch),
                            total=len(partitions),
                            counts=accumulator.stats,
                            rows_read=rows_read,
                        )
            else:
                cursor = con.execute(
                    "SELECT m.row_hash,m.instrument_id,m.session,r.payload "
                    "FROM history_partition_members m "
                    "JOIN history_evidence_rows r ON r.row_hash=m.row_hash "
                    f"WHERE m.partition_hash IN ({placeholders_p}) AND m.kind='bar' "
                    "ORDER BY m.instrument_id,m.session,m.row_hash",
                    batch,
                )
                for row in cursor:
                    if row["instrument_id"] not in accumulator.wanted_ids:
                        accumulator.stats["candidate_rows_out_of_scope"] += 1
                        continue
                    payload = row["payload"]
                    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != row["row_hash"]:
                        raise RuntimeError("CORRUPT_HISTORY_ROW")
                    accumulator.consider(json.loads(payload), "HISTORY_ARTIFACT", row["row_hash"])
                    rows_read += 1
                    logger.event(
                        f"{side}_HISTORY_SET_READ_PROGRESS",
                        processed=left + len(batch),
                        total=len(partitions),
                        counts=accumulator.stats,
                        rows_read=rows_read,
                    )
            logger.event(
                f"{side}_HISTORY_SET_READ_BATCH_DONE",
                processed=min(left + len(batch), len(partitions)),
                total=len(partitions),
                counts=accumulator.stats,
                rows_read=rows_read,
                force=True,
            )
    logger.event(
        f"{side}_HISTORY_SET_READ_DONE",
        processed=len(partitions),
        total=len(partitions),
        counts=accumulator.stats,
        rows_read=rows_read,
        force=True,
    )


def _read_inline_rows(ctx, accumulator, logger, side):
    total = len(ctx.bundle.bars)
    logger.event(f"{side}_INLINE_READ_START", processed=0, total=total, force=True)
    for index, bar in enumerate(ctx.bundle.bars, start=1):
        accumulator.consider(bar, "INLINE_MARKET_INPUT")
        logger.event(f"{side}_INLINE_READ_PROGRESS", processed=index, total=total, counts=accumulator.stats)
    logger.event(f"{side}_INLINE_READ_DONE", processed=total, total=total, counts=accumulator.stats, force=True)


def _selected_source_view_fast(ctx, source_sessions, instrument_ids, logger, side):
    accumulator = SelectedSourceAccumulator(ctx, source_sessions, instrument_ids)
    _read_history_rows(ctx, source_sessions, instrument_ids, accumulator, logger, side)
    _read_inline_rows(ctx, accumulator, logger, side)
    selected, stats = accumulator.finalize()
    logger.event(
        f"{side}_SET_AGGREGATE_DONE",
        processed=len(selected),
        total=len(source_sessions) * len(instrument_ids),
        counts=stats,
        rejected_samples=accumulator.rejections,
        force=True,
    )
    return selected, stats, accumulator.rejections


def _compare_selected(selected_t, selected_tp1, common_instruments, common_sessions, ctx_t, ctx_tp1, logger):
    counts = Counter()
    changed_by_session = Counter()
    changed_by_instrument = Counter()
    samples = []
    category_pairs = defaultdict(list)
    unchanged_pairs = []
    missing_pairs = []
    inline_pairs = []
    total = len(common_instruments) * len(common_sessions)
    processed = 0
    for iid in common_instruments:
        for session in common_sessions:
            token_t = _token_for(selected_t, iid, session)
            token_tp1 = _token_for(selected_tp1, iid, session)
            category = _classify(token_t, token_tp1, session, ctx_t.observation_date, ctx_tp1.observation_date, ctx_t.cutoff)
            counts[category] += 1
            if category == "UNCHANGED" and len(unchanged_pairs) < 100:
                unchanged_pairs.append((iid, session))
            if token_t.status == "MISSING" or token_tp1.status == "MISSING":
                if len(missing_pairs) < 100:
                    missing_pairs.append((iid, session))
            if token_t.source_kind == "INLINE_MARKET_INPUT" or token_tp1.source_kind == "INLINE_MARKET_INPUT":
                if len(inline_pairs) < 100:
                    inline_pairs.append((iid, session))
            if category != "UNCHANGED":
                changed_by_session[session] += 1
                changed_by_instrument[iid] += 1
                category_pairs[category].append((iid, session))
                if len(samples) < 100:
                    samples.append({
                        "instrument": iid,
                        "session": session,
                        "category": category,
                        "day_t": _token_to_dict(token_t, iid, session),
                        "day_t_plus_1": _token_to_dict(token_tp1, iid, session),
                    })
            processed += 1
            logger.event("PAIR_COMPARE_PROGRESS", processed=processed, total=total, counts=counts)
    logger.event("PAIR_COMPARE_DONE", processed=processed, total=total, counts=counts, force=True)
    return {
        "classification_counts": dict(counts),
        "changed_by_session_top": changed_by_session.most_common(20),
        "changed_by_instrument_top": changed_by_instrument.most_common(20),
        "samples": samples,
        "category_pairs": category_pairs,
        "unchanged_pairs": unchanged_pairs,
        "missing_pairs": missing_pairs,
        "inline_pairs": inline_pairs,
    }


def _clear_context_cache(ctx):
    cache = getattr(ctx, "scalar_cache", None)
    if isinstance(cache, dict):
        cache.clear()


def _verify_pairs_with_canonical(ctx_t, ctx_tp1, pairs, selected_t, selected_tp1, logger, case_id, *, batch_size=300):
    pairs = sorted(set((iid, _date_text(session)) for iid, session in pairs))
    by_session = defaultdict(list)
    for iid, session in pairs:
        by_session[session].append(iid)
    total = len(pairs)
    processed = 0
    mismatches = []
    logger.event(f"CANONICAL_VERIFY_{case_id}_START", processed=0, total=total, force=True)
    for session in sorted(by_session):
        ids = sorted(set(by_session[session]))
        for left in range(0, len(ids), batch_size):
            batch = ids[left : left + batch_size]
            _clear_context_cache(ctx_t)
            _clear_context_cache(ctx_tp1)
            view_t = canonical_price_source_view(ctx_t, [session], batch)
            view_tp1 = canonical_price_source_view(ctx_tp1, [session], batch)
            for iid in batch:
                expected_t = _token_for(selected_t, iid, session)
                expected_tp1 = _token_for(selected_tp1, iid, session)
                actual_t = _compact_token(source_token(view_t, iid, session))
                actual_tp1 = _compact_token(source_token(view_tp1, iid, session))
                if _token_identity(expected_t) != _token_identity(actual_t) and len(mismatches) < 100:
                    mismatches.append({
                        "side": "T",
                        "case_id": case_id,
                        "instrument": iid,
                        "session": session,
                        "expected": _token_to_dict(expected_t, iid, session),
                        "actual": _token_to_dict(actual_t, iid, session),
                    })
                if _token_identity(expected_tp1) != _token_identity(actual_tp1) and len(mismatches) < 100:
                    mismatches.append({
                        "side": "T_PLUS_1",
                        "case_id": case_id,
                        "instrument": iid,
                        "session": session,
                        "expected": _token_to_dict(expected_tp1, iid, session),
                        "actual": _token_to_dict(actual_tp1, iid, session),
                    })
            processed += len(batch)
            logger.event(
                f"CANONICAL_VERIFY_{case_id}_PROGRESS",
                processed=processed,
                total=total,
                mismatch_count=len(mismatches),
            )
    logger.event(
        f"CANONICAL_VERIFY_{case_id}_DONE",
        processed=processed,
        total=total,
        mismatch_count=len(mismatches),
        force=True,
    )
    return {
        "case_id": case_id,
        "requested_pair_count": total,
        "checked_pair_count": processed,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "status": "PASS" if not mismatches else "FAIL",
    }


def _single_context_canonical_check(ctx, pairs, expected_status=None, logger=None, case_id="CASE", *, batch_size=300):
    pairs = sorted(set((iid, _date_text(session)) for iid, session in pairs))
    by_session = defaultdict(list)
    for iid, session in pairs:
        by_session[session].append(iid)
    checked = 0
    failures = []
    if logger:
        logger.event(f"CANONICAL_SINGLE_{case_id}_START", processed=0, total=len(pairs), force=True)
    for session in sorted(by_session):
        ids = sorted(set(by_session[session]))
        for left in range(0, len(ids), batch_size):
            batch = ids[left : left + batch_size]
            _clear_context_cache(ctx)
            view = canonical_price_source_view(ctx, [session], batch)
            for iid in batch:
                token = source_token(view, iid, session)
                if expected_status is not None and token.get("status") != expected_status and len(failures) < 100:
                    failures.append({
                        "instrument": iid,
                        "session": session,
                        "expected_status": expected_status,
                        "actual_status": token.get("status"),
                        "token": _token_to_dict(_compact_token(token), iid, session),
                    })
            checked += len(batch)
            if logger:
                logger.event(f"CANONICAL_SINGLE_{case_id}_PROGRESS", processed=checked, total=len(pairs), failure_count=len(failures))
    if logger:
        logger.event(f"CANONICAL_SINGLE_{case_id}_DONE", processed=checked, total=len(pairs), failure_count=len(failures), force=True)
    return {
        "case_id": case_id,
        "checked_pair_count": checked,
        "expected_status": expected_status,
        "failure_count": len(failures),
        "failures": failures,
        "status": "PASS" if not failures else "FAIL",
    }


def _synthetic_inline_revision_checks(bundle, ctx, session, iid):
    base_bar = next((bar for bar in bundle.bars if bar.instrument_id == iid and _date_text(bar.session) == _date_text(session)), None)
    if base_bar is None:
        return {
            "case_id": "INLINE_SOURCE_HIGHER_REVISION_CASE",
            "status": "NOT_COLLECTED",
            "reason": "no inline base bar for selected sample",
        }
    visible_stamp = min(ctx.cutoff - timedelta(seconds=1), base_bar.ingested_at + timedelta(seconds=1))
    future_stamp = ctx.cutoff + timedelta(seconds=1)

    def revised(stamp, close_text, source_ref):
        value = base_bar.model_dump(mode="json")
        close = Decimal(close_text)
        value.update({
            "close": close,
            "high": max(Decimal(str(value["high"])), close),
            "comparable_close": close,
            "comparable_high": max(Decimal(str(value["comparable_high"])), close),
            "source_published_at": stamp,
            "first_seen_at": stamp,
            "ingested_at": stamp,
            "source_refs": tuple(value.get("source_refs") or ("fixture-bars",)),
        })
        return DailyBar.model_validate(value)

    visible = revised(visible_stamp, "99.1234", "R16_SYNTHETIC_VISIBLE_INLINE_REVISION")
    future = revised(future_stamp, "88.1234", "R16_SYNTHETIC_FUTURE_INLINE_REVISION")

    visible_bundle = bundle.model_copy(update={"bars": tuple(bundle.bars) + (visible,)})
    visible_bundle._history_store = bundle._history_store
    visible_ctx = _prepare_context(visible_bundle, ctx.cutoff)
    visible_view = canonical_price_source_view(visible_ctx, [session], [iid])
    visible_token = source_token(visible_view, iid, session)

    future_bundle = bundle.model_copy(update={"bars": tuple(bundle.bars) + (future,)})
    future_bundle._history_store = bundle._history_store
    future_ctx = _prepare_context(future_bundle, ctx.cutoff)
    future_view = canonical_price_source_view(future_ctx, [session], [iid])
    future_token = source_token(future_view, iid, session)

    visible_compact = _compact_token(visible_token)
    future_compact = _compact_token(future_token)
    visible_ok = visible_compact.source_kind == "INLINE_MARKET_INPUT" and _value(visible_compact, "comparable_close") == "99.1234"
    future_ok = _value(future_compact, "comparable_close") != "88.1234"
    return {
        "case_id": "INLINE_HIGHER_REVISION_AND_FUTURE_LEAK_SYNTHETIC_CASES",
        "instrument": iid,
        "session": _date_text(session),
        "visible_higher_revision_status": "PASS" if visible_ok else "FAIL",
        "future_revision_pit_status": "PASS" if future_ok else "FAIL",
        "visible_token": _token_to_dict(visible_compact, iid, session),
        "future_token": _token_to_dict(future_compact, iid, session),
        "status": "PASS" if visible_ok and future_ok else "FAIL",
    }


def _targeted_canonical_checks(
    bundle_t,
    bundle_tp1,
    ctx_t,
    ctx_tp1,
    selected_t,
    selected_tp1,
    compare_result,
    common_instruments,
    common_sessions,
    logger,
    *,
    batch_size=300,
):
    category_pairs = compare_result["category_pairs"]
    discrepancy_pairs = []
    for category in VIOLATION_CATEGORIES:
        discrepancy_pairs.extend(category_pairs.get(category, ()))
    legitimate_pairs = category_pairs.get("LEGITIMATE_SOURCE_REVISION", [])
    checks = []
    checks.append(_verify_pairs_with_canonical(
        ctx_t,
        ctx_tp1,
        discrepancy_pairs,
        selected_t,
        selected_tp1,
        logger,
        "ALL_DISCOVERED_DISCREPANCIES",
        batch_size=batch_size,
    ))
    checks.append(_verify_pairs_with_canonical(
        ctx_t,
        ctx_tp1,
        legitimate_pairs,
        selected_t,
        selected_tp1,
        logger,
        "LEGITIMATE_REVISION_CASES",
        batch_size=batch_size,
    ))
    checks.append(_verify_pairs_with_canonical(
        ctx_t,
        ctx_tp1,
        compare_result["unchanged_pairs"][:50],
        selected_t,
        selected_tp1,
        logger,
        "UNCHANGED_CASES",
        batch_size=batch_size,
    ))
    checks.append(_verify_pairs_with_canonical(
        ctx_t,
        ctx_tp1,
        compare_result["missing_pairs"][:50],
        selected_t,
        selected_tp1,
        logger,
        "MISSING_DATA_CASES",
        batch_size=batch_size,
    ))
    checks.append(_verify_pairs_with_canonical(
        ctx_t,
        ctx_tp1,
        compare_result["inline_pairs"][:50],
        selected_t,
        selected_tp1,
        logger,
        "REAL_INLINE_SELECTED_CASES",
        batch_size=batch_size,
    ))
    fake_iid = "__R16_MISSING__.NA"
    checks.append(_single_context_canonical_check(
        ctx_t,
        [(fake_iid, common_sessions[0])],
        expected_status="MISSING",
        logger=logger,
        case_id="SYNTHETIC_MISSING_SOURCE_CASE",
        batch_size=batch_size,
    ))
    appended_sessions = [
        session for session in ctx_tp1.sessions
        if _date_text(session) > _date_text(ctx_t.observation_date) and _date_text(session) <= _date_text(ctx_tp1.observation_date)
    ]
    if appended_sessions and common_instruments:
        pit_ctx = _prepare_context(bundle_tp1, ctx_t.cutoff)
        checks.append(_single_context_canonical_check(
            pit_ctx,
            [(iid, appended_sessions[-1]) for iid in common_instruments[:50]],
            expected_status="MISSING",
            logger=logger,
            case_id="FUTURE_LEAK_PIT_APPENDED_SESSION_CASES",
            batch_size=batch_size,
        ))
    else:
        checks.append({
            "case_id": "FUTURE_LEAK_PIT_APPENDED_SESSION_CASES",
            "status": "NOT_COLLECTED",
            "reason": "no appended source session or no common instruments",
        })
    synthetic_session = common_sessions[-1]
    synthetic_iid = common_instruments[0] if common_instruments else None
    if synthetic_iid:
        checks.append(_synthetic_inline_revision_checks(bundle_t, ctx_t, synthetic_session, synthetic_iid))
    else:
        checks.append({
            "case_id": "INLINE_HIGHER_REVISION_AND_FUTURE_LEAK_SYNTHETIC_CASES",
            "status": "NOT_COLLECTED",
            "reason": "no common instrument",
        })
    failures = [check for check in checks if check.get("status") not in {"PASS", "NOT_COLLECTED"}]
    return {
        "check_version": "R16_CANONICAL_RESOLVER_TARGETED_CHECKS_V1",
        "status": "PASS" if not failures else "FAIL",
        "all_discovered_discrepancies_checked": checks[0].get("checked_pair_count") == len(set(discrepancy_pairs)),
        "discovered_discrepancy_pair_count": len(set(discrepancy_pairs)),
        "legitimate_revision_pair_count": len(set(legitimate_pairs)),
        "checks": checks,
    }


def _root_cause_and_fix_status(counts):
    source_disappeared = counts.get("SOURCE_DISAPPEARED", 0)
    artifact_build_bug = counts.get("BUG_IN_ARTIFACT_BUILD", 0)
    if artifact_build_bug:
        root_cause = "HISTORY_ARTIFACT_REBUILT_WITHOUT_PRIOR_COMPARABLE_FACTOR_COMPOSITE_ROWS"
    elif source_disappeared:
        root_cause = "CANONICAL_RESOLVER_CONFIRMED_SOURCE_DISAPPEARANCE_IN_T_PLUS_1"
    else:
        root_cause = "CANONICAL_RESOLVER_NO_HISTORICAL_SOURCE_DISAPPEARANCE_FOR_COMPARED_RANGE"
    if source_disappeared or artifact_build_bug:
        fix_status = (
            "R16 fixes future artifact builds by carrying the prior registered history artifact as the normalization "
            "base_ref, and fixes cache dependency completeness by sharing the canonical resolved source view."
        )
    else:
        fix_status = "Append-only invariant holds for the compared historical range under the canonical resolved source view."
    return root_cause, fix_status


def audit(
    db_path,
    input_id_t,
    input_id_tp1,
    cutoff_t=None,
    cutoff_tp1=None,
    instrument_limit=None,
    *,
    checkpoint_json=None,
    progress_interval_seconds=10.0,
    canonical_batch_size=300,
    run_canonical_checks=True,
):
    logger = ProgressLogger(checkpoint_json, interval_seconds=progress_interval_seconds)
    logger.event("LOAD_INPUTS_START", force=True, db_path=str(Path(db_path).resolve()))
    store = SQLiteStore(db_path)
    bundle_t = _load_bundle(store, input_id_t)
    bundle_tp1 = _load_bundle(store, input_id_tp1)
    ctx_t = _prepare_context(bundle_t, _cutoff(bundle_t, cutoff_t))
    ctx_tp1 = _prepare_context(bundle_tp1, _cutoff(bundle_tp1, cutoff_tp1))
    logger.event("LOAD_INPUTS_DONE", force=True, input_t=input_id_t, input_t_plus_1=input_id_tp1)

    target_sessions_t, source_sessions_t = _target_source_sessions(ctx_t)
    _target_sessions_tp1, source_sessions_tp1 = _target_source_sessions(ctx_tp1)
    common_sessions = tuple(
        _date_text(session)
        for session in source_sessions_t
        if session in set(source_sessions_tp1) and session <= ctx_t.observation_date
    )
    common_instruments = sorted(set(ctx_t.eligible_ids) & set(ctx_tp1.eligible_ids))
    if instrument_limit:
        common_instruments = common_instruments[:instrument_limit]
    total_pairs = len(common_sessions) * len(common_instruments)
    logger.event(
        "COMPARISON_SCOPE_READY",
        processed=0,
        total=total_pairs,
        force=True,
        sessions_compared=len(common_sessions),
        instruments_compared=len(common_instruments),
        instrument_limit=instrument_limit,
    )

    selected_t, selected_t_stats, rejected_t = _selected_source_view_fast(ctx_t, common_sessions, common_instruments, logger, "DAY_T")
    selected_tp1, selected_tp1_stats, rejected_tp1 = _selected_source_view_fast(ctx_tp1, common_sessions, common_instruments, logger, "DAY_T_PLUS_1")
    compare_result = _compare_selected(selected_t, selected_tp1, common_instruments, common_sessions, ctx_t, ctx_tp1, logger)
    counts = Counter(compare_result["classification_counts"])
    violation_count = sum(counts.get(category, 0) for category in VIOLATION_CATEGORIES)
    root_cause, fix_status = _root_cause_and_fix_status(counts)
    canonical_checks = (
        _targeted_canonical_checks(
            bundle_t,
            bundle_tp1,
            ctx_t,
            ctx_tp1,
            selected_t,
            selected_tp1,
            compare_result,
            common_instruments,
            common_sessions,
            logger,
            batch_size=canonical_batch_size,
        )
        if run_canonical_checks
        else {"status": "SKIPPED", "reason": "explicit --skip-canonical-target-checks"}
    )
    elapsed = perf_time.perf_counter() - logger.started
    result = {
        "audit_version": AUDIT_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "elapsed_seconds": elapsed,
        "db_path": str(Path(db_path).resolve()),
        "input_t": {
            "input_id": input_id_t,
            "observation_date": str(bundle_t.observation_date),
            "input_hash": digest_market_input(bundle_t),
            "history_artifact_ref": bundle_t.history_artifact_ref,
            "history_artifact_hash": bundle_t.history_artifact_hash,
            "eligible": len(ctx_t.eligible_ids),
        },
        "input_t_plus_1": {
            "input_id": input_id_tp1,
            "observation_date": str(bundle_tp1.observation_date),
            "input_hash": digest_market_input(bundle_tp1),
            "history_artifact_ref": bundle_tp1.history_artifact_ref,
            "history_artifact_hash": bundle_tp1.history_artifact_hash,
            "eligible": len(ctx_tp1.eligible_ids),
        },
        "comparison_scope": {
            "comparison_method": "SET_BASED_SELECTED_SOURCE_RECORDS_WITH_TARGETED_CANONICAL_RESOLVER_VERIFICATION",
            "source_family": SOURCE_FAMILY,
            "sessions_compared": list(common_sessions),
            "instrument_count": len(common_instruments),
            "total_pairs": total_pairs,
            "target_sessions_t": [_date_text(session) for session in target_sessions_t],
            "instrument_limit": instrument_limit,
        },
        "source_record_aggregation": {
            "day_t": selected_t_stats,
            "day_t_plus_1": selected_tp1_stats,
            "rejected_samples_day_t": rejected_t,
            "rejected_samples_day_t_plus_1": rejected_tp1,
        },
        "classification_counts": dict(counts),
        "changed_by_session_top": compare_result["changed_by_session_top"],
        "changed_by_instrument_top": compare_result["changed_by_instrument_top"],
        "samples": compare_result["samples"],
        "targeted_canonical_resolver_checks": canonical_checks,
        "append_only_invariant": {
            "policy": "for session <= Day T, resolved historical source(T+1) must equal resolved historical source(T) unless a legitimate revision is proven",
            "violations": violation_count,
            "status": "PASS" if violation_count == 0 else "FAIL",
        },
        "root_cause": root_cause,
        "fix_status": fix_status,
        "progress": {
            "checkpoint_json": str(Path(checkpoint_json).resolve()) if checkpoint_json else None,
            "total_pairs": total_pairs,
            "elapsed_seconds": elapsed,
            "last_progress_event": logger.latest,
        },
        "market_edge": "NOT_ESTABLISHED",
    }
    logger.finish(
        status=result["append_only_invariant"]["status"],
        root_cause=root_cause,
        canonical_check_status=canonical_checks.get("status"),
        elapsed_seconds=elapsed,
    )
    return result


def write_md(path, result):
    counts = result["classification_counts"]
    checks = result.get("targeted_canonical_resolver_checks") or {}
    lines = [
        "# R16 History Stability Audit",
        "",
        f"- audit_version: `{result['audit_version']}`",
        f"- generated_at: `{result['generated_at']}`",
        f"- elapsed_seconds: `{result['elapsed_seconds']}`",
        f"- input_t: `{result['input_t']['input_id']}` / `{result['input_t']['observation_date']}`",
        f"- input_t_plus_1: `{result['input_t_plus_1']['input_id']}` / `{result['input_t_plus_1']['observation_date']}`",
        f"- sessions_compared: `{len(result['comparison_scope']['sessions_compared'])}`",
        f"- instruments_compared: `{result['comparison_scope']['instrument_count']}`",
        f"- total_pairs: `{result['comparison_scope']['total_pairs']}`",
        f"- append_only_invariant: `{result['append_only_invariant']['status']}`",
        f"- canonical_target_checks: `{checks.get('status')}`",
        f"- all_discovered_discrepancies_checked: `{checks.get('all_discovered_discrepancies_checked')}`",
        f"- root_cause: `{result['root_cause']}`",
        f"- fix_status: {result['fix_status']}",
        "",
        "## Classification Counts",
        "",
    ]
    for key in (
        "UNCHANGED",
        "APPENDED_NEW_SESSION",
        "LEGITIMATE_SOURCE_REVISION",
        "SOURCE_REPLACED",
        "SOURCE_DISAPPEARED",
        "SOURCE_BECAME_UNAVAILABLE",
        "BUG_IN_ARTIFACT_BUILD",
        "BUG_IN_MERGE/RESOLUTION",
        "UNKNOWN",
    ):
        lines.append(f"- {key}: `{counts.get(key, 0)}`")
    lines.extend(["", "## Source Aggregation", ""])
    for side in ("day_t", "day_t_plus_1"):
        stats = result["source_record_aggregation"].get(side) or {}
        lines.append(f"- {side}: selected `{stats.get('selected_pairs')}`, allowed rows `{stats.get('candidate_rows_allowed')}`, rejected rows `{stats.get('candidate_rows_rejected')}`")
    lines.extend(["", "## Canonical Resolver Targeted Checks", ""])
    for check in checks.get("checks") or []:
        lines.append(
            f"- `{check.get('case_id')}`: status `{check.get('status')}`, checked `{check.get('checked_pair_count')}`"
        )
    lines.extend(["", "## Sample Differences", ""])
    for sample in result["samples"][:20]:
        lines.append(
            f"- `{sample['instrument']}` `{sample['session']}` `{sample['category']}`: "
            f"T `{sample['day_t'].get('row_hash')}` -> T+1 `{sample['day_t_plus_1'].get('row_hash')}`"
        )
    lines.extend([
        "",
        "The audit reads existing immutable inputs only. It performs no provider refresh and no historical backfill.",
    ])
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "stockfish.sqlite3"))
    parser.add_argument("--input-id-t", default=DEFAULT_T)
    parser.add_argument("--input-id-t-plus-1", default=DEFAULT_TP1)
    parser.add_argument("--cutoff-t", default=None)
    parser.add_argument("--cutoff-t-plus-1", default=None)
    parser.add_argument("--instrument-limit", type=int, default=None)
    parser.add_argument("--canonical-batch-size", type=int, default=300)
    parser.add_argument("--progress-interval-seconds", type=float, default=10.0)
    parser.add_argument("--checkpoint-json", default=None)
    parser.add_argument("--skip-canonical-target-checks", action="store_true")
    parser.add_argument("--output-json", default=str(REPO_ROOT / "r16_history_stability_audit.json"))
    parser.add_argument("--output-md", default=str(REPO_ROOT / "R16_HISTORY_STABILITY_AUDIT.md"))
    args = parser.parse_args(argv)
    checkpoint_json = args.checkpoint_json
    if checkpoint_json is None and args.output_json:
        checkpoint_json = str(Path(args.output_json).with_suffix(".checkpoint.json"))
    result = audit(
        args.db,
        args.input_id_t,
        args.input_id_t_plus_1,
        args.cutoff_t,
        args.cutoff_t_plus_1,
        args.instrument_limit,
        checkpoint_json=checkpoint_json,
        progress_interval_seconds=args.progress_interval_seconds,
        canonical_batch_size=args.canonical_batch_size,
        run_canonical_checks=not args.skip_canonical_target_checks,
    )
    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    output_md = Path(args.output_md)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    write_md(output_md, result)
    print(json.dumps({
        "status": result["append_only_invariant"]["status"],
        "root_cause": result["root_cause"],
        "classification_counts": result["classification_counts"],
        "canonical_target_checks": result["targeted_canonical_resolver_checks"]["status"],
        "elapsed_seconds": result["elapsed_seconds"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
