"""Audit and rectify full-market comparable coverage without changing signals."""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
from datetime import date, datetime, time as wall_time
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import tracemalloc
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SH = ZoneInfo("Asia/Shanghai")
COMPARABLE_SOURCE_DATASETS = {"adjustment_factors", "history_adjusted", "tushare_adj_factor"}
RECTIFICATION_POLICY = "FULL_MARKET_COMPARABLE_INCREMENTAL_HISTORY_ARTIFACT_V2"

sys.path.insert(0, str(ROOT))

from stockfish_core.common import utc_now
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.candidate_service import CandidateService
from stockfish_core.yaogu.contracts import DailyBar, MarketInput, SourceReceipt, digest
from stockfish_core.yaogu.coverage_diagnostics import (
    EXCHANGES,
    _raw_ok,
    _selected_bar_map,
    audit_factor_queue,
    build_coverage_diagnostics,
    comparable_chain_sample,
    funnel_markdown,
    history_session_gap_matrix,
    json_safe,
    reason_aggregates_markdown,
    write_comparable_chain_csv,
)
from stockfish_core.yaogu.data_pipeline import stage_aware_history_sessions_required
from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore, bind_history, digest_market_input, history_receipts
from stockfish_core.yaogu.provider_comparability import DEFINITION as BAOSTOCK_FACTOR_BASIS, factor_series, positive
from stockfish_core.yaogu.provider_normalization import _clock as provider_clock
from stockfish_core.yaogu.provider_normalization import _iid as provider_iid
from stockfish_core.yaogu.provider_normalization import _number as provider_number
from stockfish_core.yaogu.provider_requests import FIELD_VERSION, execute_request
from stockfish_core.yaogu.request_queue import RequestBudget, RequestQueue
from stockfish_core.yaogu.strategy_experiments import (
    _prepare_context,
    run_pv_focus_preview,
    run_pv_observation,
)
from stockfish_core.yaogu.stage_aware_momentum import run_stage_aware_momentum


class LocalJobRegistry:
    """Enough of JobService for direct maintenance without taking worker lock."""

    def __init__(self, store):
        self.store = store

    def register(self, *_args, **_kwargs):
        return None

    def submit(self, *_args, **_kwargs):
        raise RuntimeError("DIRECT_RECTIFICATION_SCRIPT_DOES_NOT_SUBMIT_ASYNC_JOBS")

    def shutdown(self, *_args, **_kwargs):
        return None


def _load_service_paths():
    path = REPO / "work" / "stockfish-service.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _date(value):
    return date.fromisoformat(str(value)[:10]) if value else None


def _dump_json(path, value):
    path.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path, text):
    path.write_text(text, encoding="utf-8")


def _log(stage, **extra):
    print(json.dumps({"stage": stage, **extra}, ensure_ascii=False, default=str), flush=True)


def _normalize_code(code):
    text = str(code)
    if len(text) == 9 and text[6] == ".":
        return text[:6] + "." + text[7:].upper()
    if len(text) == 9 and text[2] == ".":
        return text[3:] + "." + text[:2].upper()
    return text.upper()


def _queue_rows(queue_root, endpoint=None):
    db = Path(queue_root) / "request-queue.sqlite3"
    if not db.exists():
        return []
    rows = []
    with sqlite3.connect(str(db)) as con:
        con.row_factory = sqlite3.Row
        for row in con.execute("SELECT request_id,payload,state,attempts,failure_category,result_path FROM requests"):
            payload = json.loads(row["payload"])
            if endpoint is None or payload.get("endpoint") == endpoint:
                rows.append({**dict(row), "payload_obj": payload})
    return rows


def _factor_request_ids(queue_root, expected_ids):
    expected = set(expected_ids)
    requested = set()
    ids = []
    for row in _queue_rows(queue_root, "adjustment_factors"):
        codes = {_normalize_code(code) for code in row["payload_obj"].get("parameters", {}).get("codes") or []}
        requested.update(codes)
        if not expected or codes & expected:
            ids.append(row["request_id"])
    return sorted(set(ids)), requested


def _market_daily_request_ids(queue_root, sessions):
    wanted = {str(session)[:10] for session in sessions}
    requested = set()
    ids = []
    for row in _queue_rows(queue_root, "market_daily"):
        session = str(row["payload_obj"].get("parameters", {}).get("date") or "")[:10]
        if session:
            requested.add(session)
        if session in wanted:
            ids.append(row["request_id"])
    return sorted(set(ids)), requested


def _summarize_rows(queue, ids):
    rows = queue.rows(ids)
    states = Counter(row["state"] for row in rows)
    failures = Counter(row.get("failure_category") for row in rows if row.get("failure_category"))
    return {
        "requested": len(rows),
        "states": dict(states),
        "failure_categories": dict(failures),
        "attempts": sum(row["attempts"] for row in rows),
        "complete": states.get("COMPLETE", 0),
        "pending_request_ids": [row["request_id"] for row in rows if row["state"] != "COMPLETE"],
    }


def _drain_ids(queue_root, ids, *, label, max_requests, max_seconds, timeout_seconds, retry_blocked=False):
    ids = sorted(set(ids))
    if not ids:
        return {"label": label, "requested": 0, "status": "NO_REQUESTS"}
    with RequestQueue(
        queue_root,
        budget=RequestBudget(
            max_requests=max_requests,
            max_seconds=max_seconds,
            timeout_seconds=timeout_seconds,
        ),
    ) as queue:
        queue.requested_ids.update(ids)
        retried_blocked = queue.retry_blocked(ids, reason=label + "_EXPLICIT_RETRY") if retry_blocked and max_requests > 0 and max_seconds > 0 else 0
        before = _summarize_rows(queue, ids)

        def progress(value):
            view = {
                "label": label,
                "states": value.get("states"),
                "network_requests_this_run": value.get("network_requests_this_run"),
                "elapsed_seconds": round(value.get("elapsed_seconds", 0), 2),
            }
            print(json.dumps(view, ensure_ascii=False), flush=True)

        queue.drain(execute_request, ids=ids, progress=progress)
        after = _summarize_rows(queue, ids)
        return {
            "label": label,
            "before": before,
            "after": after,
            "retried_blocked": retried_blocked,
            "queue_summary": queue.summary(),
            "status": "COMPLETE" if after["complete"] == after["requested"] else "PARTIAL",
        }


def _ensure_factor_requests(queue_root, expected_ids, observation_date):
    ids, requested = _factor_request_ids(queue_root, expected_ids)
    missing = sorted(set(expected_ids) - requested)
    if missing:
        with RequestQueue(queue_root, budget=RequestBudget(max_requests=0, max_seconds=1)) as queue:
            for offset in range(0, len(missing), 100):
                ids.append(
                    queue.enqueue(
                        "adjustment_factors",
                        {"codes": missing[offset : offset + 100], "start_date": "1990-01-01", "end_date": str(observation_date)},
                        stage="FullMarketComparableRectification",
                        definition_version=FIELD_VERSION,
                        priority=1,
                    )
                )
    ids, requested = _factor_request_ids(queue_root, expected_ids)
    return {"request_ids": ids, "demanded_ids": sorted(requested), "missing_from_demand_before_enqueue": missing}


def _ensure_market_daily_requests(queue_root, sessions, observation_date):
    ids, requested = _market_daily_request_ids(queue_root, sessions)
    missing = sorted({str(session)[:10] for session in sessions} - requested)
    if missing:
        with RequestQueue(queue_root, budget=RequestBudget(max_requests=0, max_seconds=1)) as queue:
            for session in missing:
                ids.append(
                    queue.enqueue(
                        "market_daily",
                        {"date": session},
                        stage="FullMarketMARectification",
                        definition_version=FIELD_VERSION,
                        cache_epoch=str(observation_date) if session == str(observation_date) else None,
                        priority=0 if session == str(observation_date) else 2,
                    )
                )
    ids, requested = _market_daily_request_ids(queue_root, sessions)
    return {"request_ids": ids, "demanded_sessions": sorted(requested), "missing_from_demand_before_enqueue": missing}


def _logical_request_ids(queue_root):
    db = Path(queue_root) / "request-queue.sqlite3"
    if not db.exists():
        return []
    with sqlite3.connect(str(db)) as con:
        return [row[0] for row in con.execute("SELECT request_id FROM logical_requests ORDER BY request_id")]


def _queue_results(queue_root):
    ids = _logical_request_ids(queue_root)
    with RequestQueue(queue_root, budget=RequestBudget(max_requests=0, max_seconds=1)) as queue:
        return queue.results(ids), ids


def _read_queue_result(queue_root, row):
    if not row.get("result_path"):
        return None
    root = Path(queue_root).resolve()
    path = (root / row["result_path"]).resolve()
    if not path.is_relative_to(root) or path.is_symlink():
        raise ValueError("CACHE_RESULT_PATH_INVALID")
    value = json.loads(path.read_text(encoding="utf-8"))
    if row.get("result_hash") and digest(value) != row["result_hash"]:
        raise ValueError("CACHE_RESULT_HASH_MISMATCH")
    return value


def _endpoint_results(queue_root, endpoint):
    for row in _queue_rows(queue_root, endpoint):
        result = _read_queue_result(queue_root, row)
        if result is not None:
            yield row, result


def _result_source(result):
    rid = result.get("request_receipt_id")
    return next((row for row in result.get("receipts") or [] if row.get("receipt_id") == rid), None)


def _json_parameter(value):
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


def _source_receipt(result):
    source = _result_source(result)
    if not source:
        return None
    params = result.get("parameters") or {}
    end = _date(params.get("end_date") or params.get("date") or params.get("trade_date"))
    coverage = result.get("coverage") or {}
    instrument_ids = tuple(sorted(str(value) for value in coverage.get("returned_instrument_ids", ()) if value))
    business_sessions = tuple(sorted({_date(value) for value in coverage.get("business_sessions", ()) if _date(value) is not None}))
    return SourceReceipt(
        receipt_id=source["receipt_id"],
        provider=result["provider"],
        dataset=result["endpoint"],
        content_hash=source["content_hash"],
        field_version=result.get("field_version") or result.get("request_definition_version") or FIELD_VERSION,
        row_count=len(result.get("rows") or []),
        pagination_complete=bool(result.get("pagination_complete", coverage.get("pagination_complete", False))),
        status=result["status"],
        parameters={key: _json_parameter(value) for key, value in params.items()},
        source_url=source.get("source_url"),
        source_type=source.get("source_type"),
        instrument_ids=instrument_ids,
        business_sessions=business_sessions,
        **provider_clock(source, end),
    )


def _receipt_lookup(bundle):
    return {receipt.receipt_id: receipt for receipt in history_receipts(bundle)}


def _factor_materialization(queue_root, expected_ids, observation_date):
    expected = set(expected_ids)
    captures = {}
    receipts = {}
    stats = Counter()
    for _row, result in _endpoint_results(queue_root, "adjustment_factors"):
        if result.get("status") not in {"COMPLETE", "PARTIAL"}:
            stats["ignored_non_usable_factor_results"] += 1
            continue
        receipt = _source_receipt(result)
        if receipt is None:
            stats["ignored_factor_results_without_receipt"] += 1
            continue
        receipts[receipt.receipt_id] = receipt
        groups = {}
        for provider_row in result.get("rows") or []:
            factor_day = _date(provider_row.get("dividOperateDate"))
            iid = provider_iid(provider_row.get("code"))
            if iid and iid in expected and (factor_day is None or factor_day <= observation_date):
                groups.setdefault(iid, []).append(provider_row)
        complete_codes = set()
        if result.get("status") == "COMPLETE" and result.get("pagination_complete"):
            complete_codes = {provider_iid(code) for code in (result.get("parameters") or {}).get("codes", ())}
        elif result.get("status") == "PARTIAL":
            for status in (result.get("coverage") or {}).get("security_statuses", ()):
                iid = provider_iid(status.get("code"))
                if (
                    iid
                    and status.get("status") == "COMPLETE"
                    and status.get("cursor_exhausted") is True
                    and status.get("row_count") == len(groups.get(iid, ()))
                ):
                    complete_codes.add(iid)
        for iid in complete_codes:
            if iid in expected:
                captures.setdefault(iid, []).append(
                    (
                        groups.get(iid, []),
                        receipt.receipt_id,
                        (result.get("parameters") or {}).get("end_date"),
                        (result.get("parameters") or {}).get("start_date"),
                    )
                )
    factor_by_instrument = {}
    conflicts = []
    empty = []
    for iid in sorted(expected):
        series, refs, conflict = factor_series(captures.get(iid, ()))
        if conflict:
            conflicts.append(iid)
            continue
        if not series:
            empty.append(iid)
            continue
        days = [day for day, _factor in series]
        factor_by_instrument[iid] = {
            "days": days,
            "series": series,
            "refs": tuple(refs),
            "basis": BAOSTOCK_FACTOR_BASIS + ":" + iid + ":" + digest([series[0][0], str(series[0][1])])[:20],
        }
    stats.update(
        {
            "factor_receipts": len(receipts),
            "accepted_factor_series": len(factor_by_instrument),
            "factor_conflicts": len(conflicts),
            "empty_factor_series": len(empty),
        }
    )
    return factor_by_instrument, receipts, {"counts": dict(stats), "conflict_ids": conflicts[:100], "empty_factor_ids": empty[:100]}


def _factor_at(factor_info, session):
    if not factor_info:
        return None
    index = bisect_right(factor_info["days"], session.isoformat()) - 1
    return factor_info["series"][index][1] if index >= 0 else None


def _market_daily_queue_index(queue_root, target_sessions):
    wanted = {str(session) for session in target_sessions}
    indexed = {}
    for row in _queue_rows(queue_root, "market_daily"):
        session = _date((row.get("payload_obj") or {}).get("parameters", {}).get("date"))
        if session is not None and str(session) in wanted and row.get("result_path"):
            indexed.setdefault(str(session), []).append(row)
    return indexed


def _instant(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _clock_for_revised_bar(raw, factor_refs, receipt_by_id, session):
    result = {"event_time": datetime.combine(session, wall_time(15), SH)}
    for field in ("source_published_at", "first_seen_at", "ingested_at"):
        values = []
        if raw.get(field):
            values.append(_instant(raw[field]))
        for ref in factor_refs:
            receipt = receipt_by_id.get(ref)
            if receipt is not None:
                values.append(getattr(receipt, field))
        result[field] = max(values) if values else result["event_time"]
    if result["ingested_at"] < result["first_seen_at"]:
        result["ingested_at"] = result["first_seen_at"]
    return result


def _raw_source_refs(raw, receipt_by_id):
    refs = []
    for ref in raw.get("source_refs") or ():
        receipt = receipt_by_id.get(ref)
        if receipt is not None and receipt.dataset in COMPARABLE_SOURCE_DATASETS:
            continue
        refs.append(ref)
    return tuple(dict.fromkeys(refs))


def _market_raw_from_provider_row(provider_row, source):
    iid = provider_iid(provider_row.get("code"))
    session = _date(provider_row.get("date"))
    if not iid or session is None or str(provider_row.get("adjustflag")) != "3":
        return None
    values = {
        "open": provider_number(provider_row.get("open"), positive=True),
        "high": provider_number(provider_row.get("high"), positive=True),
        "low": provider_number(provider_row.get("low"), positive=True),
        "close": provider_number(provider_row.get("close"), positive=True),
        "reference_prev_close": provider_number(provider_row.get("preclose"), positive=True),
        "volume_shares": provider_number(provider_row.get("volume")),
        "amount_cny": provider_number(provider_row.get("amount")),
    }
    turn = provider_number(provider_row.get("turn"))
    turn = float(turn / 100) if turn is not None and turn <= 1000 else None
    raw = {
        "instrument_id": iid,
        "session": session,
        **values,
        "turnover_fraction": turn,
        "turnover_basis": "BAOSTOCK_TURN_PERCENT_FLOAT_SHARES" if turn is not None else None,
        "limit_applicable": None,
        "limit_verified": False,
        "source_refs": (source["receipt_id"],),
        "quality": "VALID",
        **provider_clock(source, session),
    }
    raw["quality"] = "VALID" if _raw_ok(raw) else "PARTIAL"
    return raw


def _market_raw_map(queue_root, indexed_market_rows, sessions, receipt_by_id):
    wanted = {str(session) for session in sessions}
    selected = {}
    receipts = {}
    stats = Counter()
    for session in wanted:
        for queue_row in indexed_market_rows.get(session, ()):
            result = _read_queue_result(queue_root, queue_row)
            if result is None or result.get("status") not in {"COMPLETE", "PARTIAL"}:
                stats["ignored_non_usable_market_results"] += 1
                continue
            source = _result_source(result)
            receipt = _source_receipt(result)
            if source is None or receipt is None:
                stats["ignored_market_results_without_receipt"] += 1
                continue
            receipts[receipt.receipt_id] = receipt
            receipt_by_id[receipt.receipt_id] = receipt
            stats["market_results_read"] += 1
            for provider_row in result.get("rows") or []:
                raw = _market_raw_from_provider_row(provider_row, source)
                if raw is None or str(raw["session"]) not in wanted:
                    continue
                key = (str(raw["session"]), raw["instrument_id"])
                previous = selected.get(key)
                if previous is None or (
                    _instant(raw["source_published_at"]),
                    _instant(raw["ingested_at"]),
                    _instant(raw["first_seen_at"]),
                ) >= (
                    _instant(previous["source_published_at"]),
                    _instant(previous["ingested_at"]),
                    _instant(previous["first_seen_at"]),
                ):
                    selected[key] = raw
                    stats["market_rows_selected"] += 1
    return selected, receipts, dict(stats)


def _daily_bar_from_raw(raw, factor_info, receipt_by_id):
    session = _date(raw.get("session"))
    if session is None:
        return None
    factor = _factor_at(factor_info, session)
    factor_refs = factor_info["refs"] if factor is not None else ()
    data = {field: raw.get(field) for field in DailyBar.model_fields if field in raw}
    data["session"] = session
    data["source_refs"] = tuple(dict.fromkeys((*_raw_source_refs(raw, receipt_by_id), *factor_refs)))
    for field in ("comparable_open", "comparable_high", "comparable_low", "comparable_close", "comparable_basis"):
        data[field] = None
    prices = {field: positive(raw.get(field)) for field in ("open", "high", "low", "close")}
    if factor is not None and all(value is not None for value in prices.values()):
        for field, value in prices.items():
            data["comparable_" + field] = value * factor
        data["comparable_basis"] = factor_info["basis"]
    data["quality"] = "VALID" if _raw_ok(raw) else "PARTIAL"
    data.update(_clock_for_revised_bar(raw, factor_refs, receipt_by_id, session))
    return DailyBar.model_validate(data)


def _register_materialized_input(service, bundle, *, parent_input_id, parent_history_artifact_ref):
    bundle = MarketInput.model_validate(bundle.model_dump(mode="json"))
    bundle = bind_history(bundle, service.store)
    input_id = "market_" + digest_market_input(bundle)
    with service.store.transaction() as connection:
        service.store._put_document(connection, "candidate_inputs", input_id, bundle.model_dump(mode="json"))
        service.store._put_document(
            connection,
            "candidate_input_indexes",
            input_id,
            {
                "input_id": input_id,
                "observation_date": str(bundle.observation_date),
                "coverage_type": bundle.coverage_type,
                "max_session": str(max(bundle.sessions)) if bundle.sessions else None,
                "parent_input_id": parent_input_id,
                "parent_history_artifact_ref": parent_history_artifact_ref,
                "rectification_policy": RECTIFICATION_POLICY,
            },
        )
    return service.input(input_id)


def _existing_materialized_input(service, parent_input_id):
    offset = 0
    matches = []
    while True:
        rows = service.store.list_document_metadata("candidate_input_indexes", limit=1000, offset=offset, descending=True)
        for row in rows:
            value = service.store.get_document("candidate_input_indexes", row["document_id"])
            if (
                value
                and value.get("parent_input_id") == parent_input_id
                and value.get("rectification_policy") == RECTIFICATION_POLICY
            ):
                matches.append((row["created_at"], row["document_id"]))
        if len(rows) < 1000:
            break
        offset += len(rows)
    return max(matches)[1] if matches else None


def _latest_run_for_input(service, input_id, observation_date):
    offset = 0
    while True:
        rows = service.store.list_document_metadata("strategy_experiment_summaries", limit=100, offset=offset, descending=True)
        for row in rows:
            summary = service.store.get_document("strategy_experiment_summaries", row["document_id"])
            if summary and summary.get("input_id") == input_id and str(summary.get("observation_date")) == str(observation_date):
                return service.strategy_experiment(summary["run_id"])
        if len(rows) < 100:
            break
        offset += len(rows)
    offset = 0
    while True:
        rows = service.store.list_document_metadata("strategy_experiment_runs", limit=100, offset=offset, descending=True)
        for row in rows:
            run = service.strategy_experiment(row["document_id"])
            if run.get("input_id") == input_id and str(run.get("observation_date")) == str(observation_date):
                return run
        if len(rows) < 100:
            return None
        offset += len(rows)


def _coverage_snapshot(bundle, cutoff, *, saved_run=None, label="snapshot"):
    _log(label + ":prepare_context:start")
    ctx = _prepare_context(bundle, cutoff)
    _log(label + ":prepare_context:done", eligible=len(ctx.eligible_ids), sessions=len(ctx.sessions))
    if saved_run:
        _log(label + ":diagnostics_from_saved_run:start", run_id=saved_run.get("run_id"))
        pv = (saved_run.get("results") or {}).get("PV_OBSERVATION_V1") or {}
        focus = (saved_run.get("selection_results") or {}).get("PV_FOCUS_PREVIEW_V0.1") or {}
        pv = dict(pv)
        pv.update(selection_policy_id=focus.get("selection_policy_id"), focus_count=focus.get("count"), focus_ids=focus.get("selected_ids") or [])
        stage_aware = (saved_run.get("results") or {}).get("STAGE_AWARE_MOMENTUM_V2") or {}
        diagnostics = saved_run.get("data_coverage_diagnostics") or json_safe(
            build_coverage_diagnostics(ctx, pv_result=pv, stage_aware_result=stage_aware)
        )
    else:
        _log(label + ":pv_stage_recompute:start")
        pv = run_pv_observation(bundle, cutoff=cutoff, _context=ctx)
        focus = run_pv_focus_preview(pv)
        pv.update(selection_policy_id=focus["selection_policy_id"], focus_count=focus["count"], focus_ids=focus["selected_ids"])
        stage_aware = run_stage_aware_momentum(bundle, cutoff=cutoff, _context=ctx)
        diagnostics = json_safe(build_coverage_diagnostics(ctx, pv_result=pv, stage_aware_result=stage_aware))
    _log(label + ":diagnostics:done", pv_status=diagnostics["coverage_scope"]["PV_READINESS"]["status"], trend_status=diagnostics["coverage_scope"]["TREND_READINESS"]["status"])
    _log(label + ":gap_matrix:start")
    gap = json_safe(history_session_gap_matrix(ctx))
    _log(label + ":gap_matrix:done")
    _log(label + ":chain:start")
    chain = comparable_chain_sample(ctx)
    _log(label + ":chain:done", rows=len(chain))
    return {"diagnostics": diagnostics, "gap_matrix": gap, "chain": chain}


def _selected_ma_sessions(gap_matrix, windows):
    selected = {}
    for window in windows:
        req = gap_matrix["requirements"].get(f"MA{window}", {})
        sessions = sorted(set((req.get("missing_sessions") or []) + (req.get("partial_sessions") or [])))
        selected[window] = sessions
    return selected


def _normalize_register_run(service, queue_root, source_bundle):
    cutoff = utc_now()
    _log("incremental_materialize:prepare_context:start")
    ctx = _prepare_context(source_bundle, cutoff)
    eligible_ids = list(ctx.eligible_ids)
    session_dates = [date.fromisoformat(str(session)[:10]) for session in ctx.sessions]
    history_window = min(len(session_dates), stage_aware_history_sessions_required())
    target_sessions = session_dates[-history_window:]
    recent_sessions = set(session_dates[-min(6, len(session_dates)):])
    _log(
        "incremental_materialize:scope",
        eligible=len(eligible_ids),
        target_sessions=len(target_sessions),
        first_session=target_sessions[0] if target_sessions else None,
        last_session=target_sessions[-1] if target_sessions else None,
    )
    parent_input_id = "market_" + digest_market_input(source_bundle)
    existing_input_id = _existing_materialized_input(service, parent_input_id)
    if existing_input_id:
        _log("incremental_materialize:reuse_existing", input_id=existing_input_id, parent_input_id=parent_input_id)
        _, registered = service.input(existing_input_id)
        input_bytes = service.store.get_document_bytes("candidate_inputs", existing_input_id, expected_hash=existing_input_id[7:])
        materialization_stats = {
            "policy": RECTIFICATION_POLICY,
            "reused_existing_input": True,
            "eligible": len(eligible_ids),
            "target_sessions": len(target_sessions),
            "first_target_session": str(target_sessions[0]) if target_sessions else None,
            "last_target_session": str(target_sessions[-1]) if target_sessions else None,
            "candidate_input_bytes": len(input_bytes or b""),
        }
        cutoff = utc_now()
        result = _latest_run_for_input(service, existing_input_id, registered.observation_date)
        if result:
            _log("strategy_run:reuse_existing", input_id=existing_input_id, run_id=result.get("run_id"))
        else:
            result = service._build_strategy_experiment_result(existing_input_id, registered, cutoff, service.current_config())
            with service.store.transaction() as connection:
                service._write_strategy_result(connection, result, created_at=cutoff.isoformat())
        return {
            "input_id": existing_input_id,
            "bundle": registered,
            "strategy_run": result,
            "normalizer_gaps": [],
            "logical_request_count": len(_logical_request_ids(queue_root)),
            "materialization_stats": materialization_stats,
        }
    factor_by_instrument, factor_receipts, factor_stats = _factor_materialization(
        queue_root,
        eligible_ids,
        source_bundle.observation_date,
    )
    _log("incremental_materialize:factors", **factor_stats["counts"])
    receipt_by_id = _receipt_lookup(source_bundle)
    receipt_by_id.update(factor_receipts)
    written_receipts = dict(factor_receipts)
    indexed_market_rows = _market_daily_queue_index(queue_root, target_sessions)
    history_store = HistoryArtifactStore(service.store)
    writer = history_store.writer(tuple(written_receipts.values()), base_ref=source_bundle.history_artifact_ref)
    recent_bars = []
    materialized = Counter()
    market_stats = Counter()
    try:
        for offset in range(0, len(target_sessions), 20):
            chunk = target_sessions[offset : offset + 20]
            _log("incremental_materialize:chunk:start", first=chunk[0], last=chunk[-1], count=len(chunk))
            base_map = _selected_bar_map(ctx, chunk, eligible_ids)
            market_map, market_receipts, chunk_market_stats = _market_raw_map(
                queue_root,
                indexed_market_rows,
                chunk,
                receipt_by_id,
            )
            if market_receipts:
                for receipt_id, receipt in market_receipts.items():
                    written_receipts.setdefault(receipt_id, receipt)
                    writer.receipts.setdefault(receipt_id, receipt.model_dump(mode="json"))
            market_stats.update(chunk_market_stats)
            for session in chunk:
                session_text = str(session)
                for iid in eligible_ids:
                    base_raw = base_map.get((session_text, iid))
                    market_raw = market_map.get((session_text, iid))
                    raw = base_raw or market_raw
                    if market_raw is not None and (base_raw is None or not _raw_ok(base_raw)) and _raw_ok(market_raw):
                        raw = market_raw
                        materialized["new_raw_rows_used"] += 1
                    if raw is None:
                        continue
                    factor_info = factor_by_instrument.get(iid)
                    bar = _daily_bar_from_raw(raw, factor_info, receipt_by_id)
                    if bar is None:
                        continue
                    writer.append(bar)
                    materialized["bar_revisions_considered"] += 1
                    if bar.comparable_basis:
                        materialized["comparable_revisions_considered"] += 1
                    if session in recent_sessions:
                        recent_bars.append(bar)
            _log(
                "incremental_materialize:chunk:done",
                first=chunk[0],
                last=chunk[-1],
                considered=materialized["bar_revisions_considered"],
                comparable=materialized["comparable_revisions_considered"],
                new_raw_used=materialized["new_raw_rows_used"],
            )
        identity = writer.finish()
    except BaseException:
        writer.close()
        raise
    history_store.verify(identity["history_artifact_ref"], identity["history_artifact_hash"])
    receipts = {receipt.receipt_id: receipt for receipt in source_bundle.receipts}
    receipts.update(written_receipts)
    bundle = source_bundle.model_copy(
        update={
            **identity,
            "bars": tuple(sorted(recent_bars, key=lambda row: (row.session, row.instrument_id, row.ingested_at.isoformat()))),
            "receipts": tuple(receipts[key] for key in sorted(receipts)),
        }
    )
    bundle._history_store = history_store
    input_id, registered = _register_materialized_input(
        service,
        bundle,
        parent_input_id=parent_input_id,
        parent_history_artifact_ref=source_bundle.history_artifact_ref,
    )
    input_bytes = service.store.get_document_bytes("candidate_inputs", input_id, expected_hash=input_id[7:])
    materialization_stats = {
        "policy": RECTIFICATION_POLICY,
        "eligible": len(eligible_ids),
        "target_sessions": len(target_sessions),
        "first_target_session": str(target_sessions[0]) if target_sessions else None,
        "last_target_session": str(target_sessions[-1]) if target_sessions else None,
        "factor_stats": factor_stats,
        "market_daily_result_stats": dict(market_stats),
        "history_writer_added_rows": writer.added_rows,
        "history_writer_added_bytes": writer.added_bytes,
        "recent_market_input_bars": len(recent_bars),
        "candidate_input_bytes": len(input_bytes or b""),
        **dict(materialized),
    }
    _log("incremental_materialize:registered", input_id=input_id, **materialization_stats)
    cutoff = utc_now()
    result = service._build_strategy_experiment_result(input_id, registered, cutoff, service.current_config())
    with service.store.transaction() as connection:
        service._write_strategy_result(connection, result, created_at=cutoff.isoformat())
    return {
        "input_id": input_id,
        "bundle": registered,
        "strategy_run": result,
        "normalizer_gaps": [],
        "logical_request_count": len(_logical_request_ids(queue_root)),
        "materialization_stats": materialization_stats,
    }


def _format_stat(stats, layer):
    row = stats["coverage_funnel"][layer]["ALL"]
    pct = "n/a" if row["coverage_pct"] is None else f"{row['coverage_pct']:.2f}%"
    return f"{row['numerator']}/{row['denominator']} ({pct})"


def _exchange_audit_markdown(title, diagnostics):
    lines = [f"# {title}", "", funnel_markdown(diagnostics)]
    scope = diagnostics["coverage_scope"]
    lines.extend(
        [
            "",
            f"- PV_READINESS: {scope['PV_READINESS']['status']} / {_format_stat(diagnostics, 'pv_feature_computable')}",
            f"- TREND_READINESS: {scope['TREND_READINESS']['status']} / {_format_stat(diagnostics, 'trend_computable')}",
            f"- CONTEXT_READINESS: {scope['CONTEXT_READINESS']['status']}",
            "- MARKET_EDGE: NOT_ESTABLISHED",
            "",
        ]
    )
    return "\n".join(lines)


def _gap_markdown(matrix):
    lines = ["# History Session Gap Matrix", "", "| Requirement | Required | Missing RAW | Partial RAW | Missing Comparable | Partial Comparable |", "|---|---:|---:|---:|---:|---:|"]
    for name, row in matrix["requirements"].items():
        lines.append(
            f"| {name} | {len(row.get('required_sessions') or [])} | {len(row.get('missing_sessions') or [])} | "
            f"{len(row.get('partial_sessions') or [])} | {len(row.get('missing_comparable_sessions') or [])} | "
            f"{len(row.get('partial_comparable_sessions') or [])} |"
        )
    lines.extend(["", "MARKET_EDGE: NOT_ESTABLISHED", ""])
    return "\n".join(lines)


def _write_combined_ledger(path, snapshots):
    fields = [
        "phase",
        "input_id",
        "instrument_id",
        "exchange",
        "pv_complete",
        "pv_base_pass",
        "pv_focus_pass",
        "trend_computable",
        "primary_reason",
        "missing_reasons",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for phase, input_id, snapshot in snapshots:
            for row in snapshot["diagnostics"].get("missing_reason_ledger") or []:
                writer.writerow(
                    {
                        **{key: row.get(key) for key in fields if key not in {"phase", "input_id", "missing_reasons"}},
                        "phase": phase,
                        "input_id": input_id,
                        "missing_reasons": "|".join(row.get("missing_reasons") or []),
                    }
                )


def _root_cause_markdown(audit):
    before = audit["factor_queue_before"]
    after = audit.get("factor_queue_after") or {}
    after_summary = audit.get("after") or {}
    lines = [
        "# Data Coverage Root Cause Audit",
        "",
        f"- ROOT_CAUSE_ADJUSTMENT_FACTOR_COVERAGE: {before.get('ROOT_CAUSE_ADJUSTMENT_FACTOR_COVERAGE')}",
        f"- Factor demand before: {before.get('demanded', {})}",
        f"- Factor observed before: {before.get('observed', {})}",
        f"- Factor observed after: {after.get('observed', {}) if after else 'n/a'}",
        f"- PV before: {audit['before']['pv_readiness_status']} / {audit['before']['pv_feature_computable']}",
        f"- PV after: {after_summary.get('pv_readiness_status', 'n/a')} / {after_summary.get('pv_feature_computable', 'n/a')}",
        f"- Trend before: {audit['before']['trend_readiness_status']} / {audit['before']['trend_computable']}",
        f"- Trend after: {after_summary.get('trend_readiness_status', 'n/a')} / {after_summary.get('trend_computable', 'n/a')}",
        "- Historical input immutability: old MarketInput was not rewritten; any rectified input is registered under a new content hash.",
        "- Trading semantics: STAGE_AWARE_MOMENTUM_V2 and PV/Focus thresholds are unchanged.",
        "- MARKET_EDGE: NOT_ESTABLISHED",
        "",
    ]
    return "\n".join(lines)


def _snapshot_summary(snapshot):
    diagnostics = snapshot["diagnostics"]
    return {
        "pv_readiness_status": diagnostics["coverage_scope"]["PV_READINESS"]["status"],
        "trend_readiness_status": diagnostics["coverage_scope"]["TREND_READINESS"]["status"],
        "context_readiness_status": diagnostics["coverage_scope"]["CONTEXT_READINESS"]["status"],
        "pv_feature_computable": _format_stat(diagnostics, "pv_feature_computable"),
        "pv_base_pass": _format_stat(diagnostics, "pv_base_pass"),
        "pv_focus_pass": _format_stat(diagnostics, "pv_focus_pass"),
        "ma20": _format_stat(diagnostics, "has_ma20"),
        "ma60": _format_stat(diagnostics, "has_ma60"),
        "ma120": _format_stat(diagnostics, "has_ma120"),
        "ma250": _format_stat(diagnostics, "has_ma250"),
        "trend_computable": _format_stat(diagnostics, "trend_computable"),
        "sector_available": _format_stat(diagnostics, "sector_available"),
        "capital_available": _format_stat(diagnostics, "capital_available"),
    }


def _rectification_report_markdown(report):
    before = report["before"]
    after = report.get("after") or {}
    materialized = report.get("materialization_stats") or {}
    lines = [
        "# Full-Market Data Rectification Report",
        "",
        f"- Mode: {report['mode']}",
        f"- Before input: {report['before_input_id']}",
        f"- After input: {report.get('after_input_id') or 'n/a'}",
        f"- Strategy run: {report.get('strategy_run_id') or 'n/a'}",
        f"- Required replay history sessions: {stage_aware_history_sessions_required()}",
        f"- Runtime seconds: {report['runtime']['seconds']:.2f}",
        f"- Peak traced memory MB: {report['runtime']['peak_traced_mb']:.2f}",
        f"- Current MarketInput bytes: {materialized.get('candidate_input_bytes', 'n/a')}",
        f"- History artifact added bytes: {materialized.get('history_writer_added_bytes', 'n/a')}",
        "- MARKET_EDGE: NOT_ESTABLISHED",
        "",
        "| Metric | Before | After |",
        "|---|---:|---:|",
    ]
    for key in (
        "pv_feature_computable",
        "pv_base_pass",
        "pv_focus_pass",
        "ma20",
        "ma60",
        "ma120",
        "ma250",
        "trend_computable",
        "sector_available",
        "capital_available",
    ):
        lines.append(f"| {key} | {before.get(key)} | {after.get(key, 'n/a')} |")
    lines.extend(["", "## Queue Actions", ""])
    for action in report.get("queue_actions") or []:
        lines.append(f"- {action['label']}: {action.get('status')} / {action.get('after', {}).get('states', {})}")
    if materialized:
        lines.extend(
            [
                "",
                "## Incremental Materialization",
                "",
                f"- Policy: {materialized.get('policy')}",
                f"- Target sessions: {materialized.get('target_sessions')} ({materialized.get('first_target_session')} to {materialized.get('last_target_session')})",
                f"- Bar revisions considered: {materialized.get('bar_revisions_considered')}",
                f"- Comparable revisions considered: {materialized.get('comparable_revisions_considered')}",
                f"- New RAW rows used: {materialized.get('new_raw_rows_used')}",
                f"- Recent bars retained in MarketInput JSON: {materialized.get('recent_market_input_bars')}",
            ]
        )
    lines.extend(["", "## Provider Blockers", ""])
    blockers = report.get("provider_blockers") or {}
    lines.append(f"- Factor batch states before: {blockers.get('factor_batch_states_before')}")
    lines.append(f"- Factor batch states after: {blockers.get('factor_batch_states_after')}")
    lines.append(f"- Failed factor security statuses after: {blockers.get('failed_factor_security_statuses_after')}")
    lines.append("")
    return "\n".join(lines)


def _write_sha256s(out_dir):
    rows = []
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _emit_artifacts(out_dir, report, before_snapshot, before_input_id, after_snapshot=None, after_input_id=None):
    out_dir.mkdir(parents=True, exist_ok=False)
    factor_before = report["factor_queue_before"]
    factor_after = report.get("factor_queue_after")
    root_audit = {
        "audit_version": "FULL_MARKET_DATA_COVERAGE_ROOT_CAUSE_AUDIT_V1",
        "mode": report["mode"],
        "before_input_id": before_input_id,
        "after_input_id": after_input_id,
        "before": report["before"],
        "after": report.get("after"),
        "factor_queue_before": factor_before,
        "factor_queue_after": factor_after,
        "queue_actions": report.get("queue_actions") or [],
        "old_market_input_immutable": True,
        "strategy_semantics_changed": False,
        "market_edge": "NOT_ESTABLISHED",
    }
    snapshots = [("before", before_input_id, before_snapshot)]
    if after_snapshot is not None:
        snapshots.append(("after", after_input_id, after_snapshot))
    _dump_json(out_dir / "DATA_COVERAGE_ROOT_CAUSE_AUDIT.json", root_audit)
    _write_text(out_dir / "DATA_COVERAGE_ROOT_CAUSE_AUDIT.md", _root_cause_markdown(root_audit))
    _write_combined_ledger(out_dir / "FULL_MARKET_MISSING_REASON_LEDGER.csv", snapshots)
    aggregates = {
        "before": before_snapshot["diagnostics"]["reason_aggregates"],
        "after": after_snapshot["diagnostics"]["reason_aggregates"] if after_snapshot else None,
        "market_edge": "NOT_ESTABLISHED",
    }
    _dump_json(out_dir / "MISSING_REASON_AGGREGATES.json", aggregates)
    _write_text(
        out_dir / "MISSING_REASON_AGGREGATES.md",
        "# Missing Reason Aggregates\n\n## Before\n\n"
        + reason_aggregates_markdown(aggregates["before"])
        + ("\n## After\n\n" + reason_aggregates_markdown(aggregates["after"]) if aggregates["after"] else "")
        + "\nMARKET_EDGE: NOT_ESTABLISHED\n",
    )
    gap_payload = {
        "before": before_snapshot["gap_matrix"],
        "after": after_snapshot["gap_matrix"] if after_snapshot else None,
        "market_edge": "NOT_ESTABLISHED",
    }
    _dump_json(out_dir / "HISTORY_SESSION_GAP_MATRIX.json", gap_payload)
    _write_text(
        out_dir / "HISTORY_SESSION_GAP_MATRIX.md",
        "## Before\n\n" + _gap_markdown(before_snapshot["gap_matrix"])
        + ("\n## After\n\n" + _gap_markdown(after_snapshot["gap_matrix"]) if after_snapshot else ""),
    )
    write_comparable_chain_csv(out_dir / "COMPARABLE_CHAIN_AUDIT.csv", (after_snapshot or before_snapshot)["chain"])
    exchange_payload = {
        "before": before_snapshot["diagnostics"]["coverage_funnel"],
        "after": after_snapshot["diagnostics"]["coverage_funnel"] if after_snapshot else None,
        "scope_before": before_snapshot["diagnostics"]["coverage_scope"],
        "scope_after": after_snapshot["diagnostics"]["coverage_scope"] if after_snapshot else None,
        "market_edge": "NOT_ESTABLISHED",
    }
    _dump_json(out_dir / "EXCHANGE_COVERAGE_AUDIT.json", exchange_payload)
    _write_text(
        out_dir / "EXCHANGE_COVERAGE_AUDIT.md",
        "## Before\n\n" + _exchange_audit_markdown("Exchange Coverage Audit Before", before_snapshot["diagnostics"])
        + ("\n## After\n\n" + _exchange_audit_markdown("Exchange Coverage Audit After", after_snapshot["diagnostics"]) if after_snapshot else ""),
    )
    _write_text(out_dir / "FULL_MARKET_DATA_RECTIFICATION_REPORT.md", _rectification_report_markdown(report))
    _write_sha256s(out_dir)


def main(argv=None):
    defaults = _load_service_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["audit", "rectify"], default="audit")
    parser.add_argument("--db", type=Path, default=Path(defaults.get("DatabasePath", ROOT / "data" / "stockfish.sqlite3")))
    parser.add_argument("--state", type=Path, default=Path(defaults.get("StateRoot", ROOT / "data" / "core")))
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "codex" / "2026-09-09")
    parser.add_argument("--input-id")
    parser.add_argument("--observation-date")
    parser.add_argument("--factor-max-requests", type=int, default=64)
    parser.add_argument("--factor-max-seconds", type=float, default=2400)
    parser.add_argument("--ma-max-requests", type=int, default=256)
    parser.add_argument("--ma-max-seconds", type=float, default=2400)
    parser.add_argument("--timeout-seconds", type=float, default=40)
    parser.add_argument("--ma-windows", default="20,60,120,250")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    tracemalloc.start()
    output = args.output_dir / ("full_market_data_rectification_" + datetime.now(SH).strftime("%Y%m%d-%H%M%S"))
    cache = args.cache or (args.state / "yaogu" / "provider_cache")
    jobs = LocalJobRegistry(SQLiteStore(args.db))
    after = None
    after_input_id = None
    saved_run = None
    queue_actions = []
    normalizer_gaps = []
    materialization_stats = {}
    try:
        _log("service:init:start", db=str(args.db), state=str(args.state), cache=str(cache))
        service = CandidateService(jobs.store, jobs, args.state / "yaogu")
        obs = _date(args.observation_date)
        _log("market_input:load:start", input_id=args.input_id, observation_date=obs)
        before_input_id, bundle = service.strategy_input(args.input_id, obs)
        _log("market_input:load:done", input_id=before_input_id, observation_date=bundle.observation_date)
        cutoff = utc_now()
        _log("saved_run_lookup:start")
        before_run = _latest_run_for_input(service, before_input_id, bundle.observation_date)
        _log("saved_run_lookup:done", run_id=(before_run or {}).get("run_id"))
        before_snapshot = _coverage_snapshot(bundle, cutoff, saved_run=before_run, label="before")
        expected_ids = [row["instrument_id"] for row in before_snapshot["diagnostics"]["missing_reason_ledger"]]
        _log("factor_queue_audit_before:start", expected=len(expected_ids))
        factor_before = audit_factor_queue(cache, expected_ids=expected_ids)
        _log("factor_queue_audit_before:done", root_cause=factor_before.get("ROOT_CAUSE_ADJUSTMENT_FACTOR_COVERAGE"))
        if args.mode == "rectify":
            factor_plan = _ensure_factor_requests(cache, expected_ids, bundle.observation_date)
            factor_action = _drain_ids(
                cache,
                factor_plan["request_ids"],
                label="FullMarketComparableFactors",
                max_requests=args.factor_max_requests,
                max_seconds=args.factor_max_seconds,
                timeout_seconds=args.timeout_seconds,
            )
            factor_action["missing_from_demand_before_enqueue"] = factor_plan["missing_from_demand_before_enqueue"]
            queue_actions.append(factor_action)
            windows = [int(value) for value in args.ma_windows.split(",") if value.strip()]
            requested_sessions = set()
            for window, sessions in _selected_ma_sessions(before_snapshot["gap_matrix"], windows).items():
                new_sessions = [session for session in sessions if session not in requested_sessions]
                requested_sessions.update(new_sessions)
                plan = _ensure_market_daily_requests(cache, new_sessions, bundle.observation_date)
                action = _drain_ids(
                    cache,
                    plan["request_ids"],
                    label=f"FullMarketMA{window}RawSessions",
                    max_requests=args.ma_max_requests,
                    max_seconds=args.ma_max_seconds,
                    timeout_seconds=args.timeout_seconds,
                    retry_blocked=True,
                )
                action["sessions"] = new_sessions
                action["missing_from_demand_before_enqueue"] = plan["missing_from_demand_before_enqueue"]
                queue_actions.append(action)
            _log("normalize_register_run:start")
            registered = _normalize_register_run(service, cache, bundle)
            _log("normalize_register_run:done", input_id=registered["input_id"], run_id=registered["strategy_run"]["run_id"])
            after_input_id = registered["input_id"]
            saved_run = registered["strategy_run"]
            normalizer_gaps = registered["normalizer_gaps"]
            materialization_stats = registered.get("materialization_stats") or {}
            after = _coverage_snapshot(registered["bundle"], utc_now(), saved_run=saved_run, label="after")
        _log("factor_queue_audit_after:start" if args.mode == "rectify" else "factor_queue_audit_after:skip")
        factor_after = audit_factor_queue(cache, expected_ids=expected_ids) if args.mode == "rectify" else None
        if factor_after:
            _log("factor_queue_audit_after:done", root_cause=factor_after.get("ROOT_CAUSE_ADJUSTMENT_FACTOR_COVERAGE"))
        current, peak = tracemalloc.get_traced_memory()
        report = {
            "mode": args.mode,
            "before_input_id": before_input_id,
            "after_input_id": after_input_id,
            "strategy_run_id": saved_run.get("run_id") if saved_run else None,
            "factor_queue_before": factor_before,
            "factor_queue_after": factor_after,
            "before": _snapshot_summary(before_snapshot),
            "after": _snapshot_summary(after) if after else None,
            "queue_actions": queue_actions,
            "normalizer_gap_count": len(normalizer_gaps),
            "normalizer_gaps_preview": normalizer_gaps[:100],
            "materialization_stats": materialization_stats,
            "provider_blockers": {
                "factor_batch_states_before": factor_before.get("batch_state_counts"),
                "factor_batch_states_after": (factor_after or {}).get("batch_state_counts"),
                "failed_factor_security_statuses_after": len((factor_after or {}).get("failed_security_statuses") or []),
            },
            "runtime": {
                "seconds": time.perf_counter() - started,
                "current_traced_mb": current / 1024 / 1024,
                "peak_traced_mb": peak / 1024 / 1024,
            },
            "market_edge": "NOT_ESTABLISHED",
        }
        _log("artifacts:write:start", output=str(output))
        _emit_artifacts(output, report, before_snapshot, before_input_id, after, after_input_id)
        _log("artifacts:write:done", output=str(output))
        print(json.dumps({
            "output_dir": str(output),
            "mode": report["mode"],
            "before_input_id": before_input_id,
            "after_input_id": after_input_id,
            "strategy_run_id": report.get("strategy_run_id"),
            "before": report["before"],
            "after": report.get("after"),
            "factor_root_cause_before": factor_before.get("ROOT_CAUSE_ADJUSTMENT_FACTOR_COVERAGE"),
            "factor_root_cause_after": (factor_after or {}).get("ROOT_CAUSE_ADJUSTMENT_FACTOR_COVERAGE"),
            "materialization_stats": materialization_stats,
            "runtime": report["runtime"],
            "market_edge": "NOT_ESTABLISHED",
        }, ensure_ascii=False, default=str))
    finally:
        jobs.shutdown()


if __name__ == "__main__":
    main()
