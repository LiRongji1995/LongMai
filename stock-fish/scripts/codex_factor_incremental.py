"""Bounded factor-tail facts beside immutable full-window source captures.

A tail query cannot prove that older BaoStock factors were not revised. This
component therefore never manufactures a full result, receipt, capability, or
strategy input. Exact full-window originals remain usable through the original
consumer; a baseline plus tail is explicitly an unauthorised diagnostic view.
The coordinator owns its registered runtime lock and global storage admission.
Standalone commands are offline only; network sync requires that coordinator
and its admission callback. The original writer lock guards a second admission
check immediately before the provider is called.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time as day_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys
import time

VERSION = "CODEX_FACTOR_INCREMENTAL_FACTS_V1"
LEFT_ANCHOR = "1990-01-01"
MIB = 1024 ** 2
FP_KEYS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_flags")
SH = timezone(timedelta(hours=8))
CAPS = dict(max_requests=1, max_sdk_calls=52, max_http_attempts=1,
            request_http_attempts=1, request_timeout_seconds=90, max_seconds=240,
            capture_read_bytes=256*MIB, core_read_bytes=MIB, theme_read_bytes=MIB,
            presentation_read_bytes=MIB, supervisor_read_bytes=MIB,
            self_rss_bytes=512*MIB, tree_rss_bytes=768*MIB,
            output_bytes=16*MIB, cache_bytes=256*MIB)


class FactorIncrementalError(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise FactorIncrementalError(reason)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _need(key not in result, "FACTOR_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(FactorIncrementalError("FACTOR_NONFINITE_JSON")))


def _directory(value):
    _need(isinstance(value, (str, Path)), "EXPLICIT_CANONICAL_DIRECTORY_REQUIRED")
    path = Path(value).expanduser().absolute()
    _need(".." not in path.parts and path.resolve(strict=True) == path
          and stat.S_ISDIR(path.lstat().st_mode), "EXPLICIT_CANONICAL_DIRECTORY_REQUIRED")
    return path


def _bind_source(source_root):
    """Bind the original modules before import; never silently use dev WIP."""
    source = _directory(source_root)
    _need((source / "stockfish_core" / "yaogu" / "premarket_capture_cache_v1.py").is_file(),
          "ORIGINAL_CAPTURE_SOURCE_ROOT_REQUIRED")
    for name, module in tuple(sys.modules.items()):
        if name == "stockfish_core" or name.startswith("stockfish_core."):
            filename = getattr(module, "__file__", None)
            _need(filename is not None and Path(filename).resolve(strict=True).is_relative_to(source / "stockfish_core"),
                  "ALREADY_LOADED_FOREIGN_CORE_SOURCE:" + name)
    if str(source) in sys.path:
        sys.path.remove(str(source))
    sys.path.insert(0, str(source))
    original = importlib.import_module("stockfish_core.yaogu.premarket_capture_cache_v1")
    runtime = importlib.import_module("stockfish_core.yaogu.premarket_runtime_v1")
    _need(Path(original.__file__).resolve(strict=True).is_relative_to(source / "stockfish_core")
          and Path(runtime.__file__).resolve(strict=True).is_relative_to(source / "stockfish_core"),
          "ORIGINAL_FACTOR_MODULE_BINDING_CHANGED")
    return source, original, runtime


def _policy(limits):
    limits = {} if limits is None else limits
    _need(isinstance(limits, dict) and not set(limits)-set(CAPS), "UNKNOWN_FACTOR_LIMIT")
    result = dict(CAPS, **limits)
    for key, cap in CAPS.items():
        _need(type(result[key]) is int and 0 < result[key] <= cap,
              "FACTOR_LIMIT_MUST_NOT_EXCEED_CAP:" + key)
    return result


def _inventory(root, checkpoint=None):
    """Bounded metadata inventory. Native locators are counted, never followed."""
    check = checkpoint or (lambda: None)
    check()
    total, count, pending = 0, 0, [(root, 0)]
    while pending:
        check()
        parent, depth = pending.pop()
        _need(depth <= 32, "FACTOR_CACHE_INVENTORY_DEPTH")
        with os.scandir(parent) as entries:
            for entry in entries:
                check()
                count += 1
                _need(count <= 100000, "FACTOR_CACHE_INVENTORY_ENTRIES")
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append((Path(entry.path), depth+1))
                elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    _need(not getattr(info, "st_flags", 0) & 0x40000000,
                          "FACTOR_NONRESIDENT_CACHE_FILE")
                    _need(not stat.S_ISREG(info.st_mode) or not info.st_size or getattr(info, "st_blocks", 1) > 0,
                          "FACTOR_NONRESIDENT_CACHE_FILE")
                    total += info.st_size
                else:
                    raise FactorIncrementalError("FACTOR_CACHE_SPECIAL_FILE")
    return total


def _day(value):
    _need(isinstance(value, str), "EXACT_FACTOR_ISO_DATE_REQUIRED")
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise FactorIncrementalError("EXACT_FACTOR_ISO_DATE_REQUIRED") from None
    _need(day.isoformat() == value, "EXACT_FACTOR_ISO_DATE_REQUIRED")
    return day


def _clock(metadata):
    now = datetime.now(timezone.utc)
    for key in ("started_at", "first_seen_at", "ingested_at", "completed_at"):
        value = datetime.fromisoformat(str(metadata[key]).replace("Z", "+00:00"))
        _need(value.tzinfo is not None and value.utcoffset() is not None,
              "AWARE_FACTOR_SOURCE_CLOCK_REQUIRED")
        _need(value <= now, "FACTOR_SOURCE_FROM_FUTURE:" + key)


def _paths(root, spec):
    for path in (root / "index", root / "raw", root / "index" / spec.key, root / "raw" / spec.key):
        _need(not path.is_symlink(), "FACTOR_CACHE_METADATA_SYMLINK_FORBIDDEN")


def _registered(cache, original, metadata, endpoint):
    _need(isinstance(metadata, dict) and metadata.get("endpoint") == endpoint,
          "ORIGINAL_" + endpoint.upper() + "_METADATA_REQUIRED")
    spec = original.RequestSpec(**metadata["request_spec"])
    _paths(cache.root, spec)
    record = cache.lookup(spec)
    _need(record is not None and record.metadata == metadata,
          "EXACT_LATEST_FACTOR_PARENT_IN_SHARED_CACHE_REQUIRED")
    result = cache.verify(record)
    _clock(metadata)
    return spec, result


def _calendar(cache, original, metadata, first, last):
    spec, result = _registered(cache, original, metadata, "calendar")
    _need(result["status"] == "COMPLETE" and result.get("pagination_complete") is True,
          "COMPLETE_ORIGINAL_FACTOR_CALENDAR_REQUIRED")
    start, end = (_day(result["parameters"][key]) for key in ("start_date", "end_date"))
    _need(0 <= (end-start).days <= 1500 and start <= first <= last <= end,
          "FACTOR_CALENDAR_DOES_NOT_COVER_BOUNDARIES")
    rows = {}
    for row in result["rows"]:
        day = row.get("calendar_date")
        _day(day)
        _need(day not in rows and row.get("is_trading_day") in ("0", "1"),
              "FACTOR_CALENDAR_DUPLICATE_OR_UNKNOWN_DAY")
        rows[day] = row["is_trading_day"]
    expected = {str(start+timedelta(days=n)) for n in range((end-start).days+1)}
    _need(set(rows) == expected, "FACTOR_CALENDAR_NATURAL_DAY_GAP")
    _need(rows[str(first)] == rows[str(last)] == "1", "FACTOR_BOUNDARY_NOT_TRADING_SESSION")
    return metadata


def _factor_rows(result):
    """Check full responses, not just selected overlap; no numeric substitutes."""
    params, rows = result["parameters"], result["rows"]
    _need(isinstance(rows, list), "ORIGINAL_FACTOR_ROWS_REQUIRED")
    by_identity = {}
    for row in rows:
        _need(isinstance(row, dict) and row.get("code") in params["codes"], "FACTOR_ROW_SECURITY_OUTSIDE_SCOPE")
        day = str(_day(row.get("dividOperateDate")))
        _need(params["start_date"] <= day <= params["end_date"], "FACTOR_ROW_DATE_OUTSIDE_SCOPE")
        identity = (row["code"], day)
        _need(identity not in by_identity, "DUPLICATE_FACTOR_SECURITY_DATE")
        # backAdjustFactor is the original normalizer's comparison value. Other
        # source values, when present, must also be real positive decimals.
        _need("backAdjustFactor" in row, "FACTOR_BACK_VALUE_REQUIRED")
        for field in ("backAdjustFactor", "foreAdjustFactor", "adjustFactor"):
            if field in row:
                try:
                    number = Decimal(str(row[field]))
                    valid = number.is_finite() and number > 0
                except (ValueError, InvalidOperation):
                    valid = False
                _need(valid, "INVALID_ORIGINAL_FACTOR_VALUE:" + field)
        by_identity[identity] = row
    return by_identity


def _complete(result):
    return result["status"] == "COMPLETE" and result.get("pagination_complete") is True


def _compare(baseline, delta, overlap_start, baseline_end):
    left = {key: value for key, value in baseline.items() if overlap_start <= key[1] <= baseline_end}
    right = {key: value for key, value in delta.items() if key[1] <= baseline_end}
    changes = []
    for key in sorted(left.keys() | right.keys()):
        if left.get(key) == right.get(key):
            continue
        changes.append(dict(code=key[0], effective_date=key[1],
            change="ADDED_HISTORICAL_ROW" if key not in left else
                   "REMOVED_HISTORICAL_ROW" if key not in right else "CHANGED_HISTORICAL_ROW",
            baseline_row_sha256=hashlib.sha256(_canonical(left[key])).hexdigest() if key in left else None,
            delta_row_sha256=hashlib.sha256(_canonical(right[key])).hexdigest() if key in right else None))
    return changes


def _operate(operation, *, source_root, cache_root, baseline_record, target_session,
             calendar_record, overlap_days=7, limits=None, offline=True, purpose=None,
             executor=None, allow_test_fixture=False, admit=None):
    _need(operation in ("plan", "reuse", "sync"), "FACTOR_OPERATION_REQUIRED")
    _need(purpose in (None, "DELTA_FACTS_ONLY"), "FACTOR_PURPOSE_MUST_BE_DELTA_FACTS_ONLY")
    _need(operation != "sync" or purpose == "DELTA_FACTS_ONLY", "EXPLICIT_DELTA_FACTS_ONLY_PURPOSE_REQUIRED")
    _need(type(offline) is bool and type(allow_test_fixture) is bool, "BOOLEAN_FACTOR_POLICY_REQUIRED")
    _need(executor is None or allow_test_fixture, "FACTOR_EXECUTOR_REQUIRES_TEST_FIXTURE")
    _need(admit is None or callable(admit), "FACTOR_ADMISSION_CALLBACK_REQUIRED")
    _need(type(overlap_days) is int and 1 <= overlap_days <= 31, "FACTOR_OVERLAP_ONE_TO_31_DAYS")
    target = _day(target_session)
    _need(datetime.combine(target, day_time(18), SH) <= datetime.now(timezone.utc), "FACTOR_TARGET_SESSION_NOT_CLOSED")
    started = time.monotonic()
    policy = _policy(limits)
    def wall_check():
        _need(time.monotonic()-started <= policy["max_seconds"], "FACTOR_TOTAL_TIME_LIMIT_EXCEEDED")
    source, original, runtime = _bind_source(source_root)
    wall_check()
    root = _directory(cache_root)
    for name in ("index", "raw", "raw-index", "locators"):
        _need((root / name).is_dir() and not (root / name).is_symlink(), "EXISTING_ORIGINAL_FACTOR_CACHE_REQUIRED")
    before = _inventory(root, wall_check)
    _need(before <= policy["cache_bytes"], "FACTOR_CACHE_CAPACITY_EXCEEDED")
    class FactorBudget(runtime.Budget):
        def check(self, stage, *, force=False):
            wall_check()
            return super().check(stage, force=force)
    budget = FactorBudget(policy, cache_dir=root)
    options = dict(offline=offline or operation != "sync", retry_failed=False,
        allow_test_fixture=allow_test_fixture, request_timeout_seconds=policy["request_timeout_seconds"],
        max_http_requests_per_request=1, source_file_bytes=16*MIB, index_file_bytes=2*MIB,
        max_cache_entries=8192)
    def admission():
        _need(admit is not None, "FACTOR_NETWORK_REQUIRES_REGISTERED_ADMISSION")
        current = _inventory(root, wall_check)
        _need(current+16*MIB <= policy["cache_bytes"], "FACTOR_NEW_REQUEST_GROWTH_RESERVATION_REQUIRED")
        permitted = admit(16*MIB)
        wall_check()
        _need(isinstance(permitted, dict) and permitted.get("allowed") is True,
              "FACTOR_REGISTERED_CAPACITY_ADMISSION_DENIED")

    def guarded_executor(endpoint, parameters, output, **kwargs):
        # CaptureCache calls its executor while holding its original writer
        # lock. Repeat admission here so another native writer cannot consume
        # the reservation between the initial inventory and source acquisition.
        # A denial retains the original failed attempt and sends no request.
        admission()
        return (executor or original.execute_request)(endpoint, parameters, output, **kwargs)

    cache = original.CaptureCache(root, budget, config=options, executor=guarded_executor)
    result = dict(schema_version=VERSION, operation=operation.upper(), source_root=str(source),
        cache_root=str(root), target_session=target_session, purpose=purpose,
        daily_core_requirement="FULL_CORE_RECHECK_REQUIRED", network_transfer_bytes=None,
        network_transfer_measurement="NOT_MEASURED_BY_ORIGINAL_SDK_ADAPTER",
        source_capability_issued=False, strategy_input_promoted=False, strategy_executed=False,
        full_premarket_ready=False, synthetic_full_result_created=False, actual_source_clocks_rewritten=False,
        database_created=False, historical_point_in_time_certified=False,
        coverage_type="TEST_FIXTURE" if allow_test_fixture else "RECONSTRUCTED_HISTORICAL",
        native_factor_window_available=False, full_window_revalidation_required=True,
        historical_revision_outside_overlap="UNKNOWN_NO_UPSTREAM_REVISION_PROOF",
        baseline=None, delta=None, original_full_record=None)
    with budget.phase("capture"), budget.meter_files():
        baseline_spec, baseline = _registered(cache, original, baseline_record, "adjustment_factors")
        params = baseline_spec.parameters
        end = _day(params["end_date"])
        _need(params["start_date"] == LEFT_ANCHOR and baseline_spec.current is False
              and baseline_spec.business_session == params["end_date"], "FULL_1990_ANCHORED_BASELINE_REQUIRED")
        _need(len(params["codes"]) <= 50 and all(original.ORDINARY.fullmatch(code) for code in params["codes"]),
              "ONE_TO_50_ORIGINAL_MAINBOARD_FACTOR_CODES_REQUIRED")
        _need(end <= target and (target-end).days <= 31, "FACTOR_TARGET_WITHIN_31_DAYS_OF_BASELINE_REQUIRED")
        base_rows = _factor_rows(baseline)
        result.update(baseline=baseline_record, codes=params["codes"], baseline_session=str(end),
                      left_anchor=LEFT_ANCHOR, query_scope_not_eligibility=True)
        result["verified_calendar"] = _calendar(cache, original, calendar_record, end, target)
        full = original.RequestSpec("adjustment_factors", dict(codes=params["codes"],
            start_date=LEFT_ANCHOR, end_date=target_session), target_session, role="FULL_ANCHORED_FACTOR_WINDOW")
        result["required_full_window_request"] = full.to_dict()
        result["required_full_window_request_key"] = full.key
        _paths(root, full)
        try:
            full_record = cache.lookup(full)
        except ValueError as error:
            if str(error) != "UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY":
                raise
            result["status"] = "BLOCKED_UNFINISHED_FULL_WINDOW_NO_RETRY"
            full_record = None
        if "status" not in result and full_record is not None:
            full_result = cache.verify(full_record)
            _clock(full_record.metadata)
            _factor_rows(full_result)
            result["original_full_record"] = full_record.metadata
            if _complete(full_result):
                result.update(status="ORIGINAL_FULL_WINDOW_AVAILABLE", native_factor_window_available=True,
                    daily_core_requirement="ORIGINAL_FULL_WINDOW_PRESENT",
                    full_window_revalidation_required=False,
                    historical_revision_outside_overlap="ORIGINAL_FULL_WINDOW_REQUERIED_AT_ACTUAL_SOURCE_CLOCK")
            else:
                result["status"] = "FULL_WINDOW_PARENT_INCOMPLETE_NO_RETRY"
        if "status" not in result and not _complete(baseline):
            result["status"] = "BASELINE_PARENT_INCOMPLETE_NO_RETRY"
        if "status" not in result:
            start = max(date.fromisoformat(LEFT_ANCHOR), end-timedelta(days=overlap_days-1))
            delta = original.RequestSpec("adjustment_factors", dict(codes=params["codes"],
                start_date=str(start), end_date=target_session), target_session,
                role="FACTOR_TAIL_FACTS_WITH_OVERLAP_NOT_FULL_WINDOW")
            result.update(overlap_start=str(start), overlap_end=str(end), delta_request=delta.to_dict(),
                          delta_request_key=delta.key, worst_case_new_requests=1,
                          worst_case_sdk_reservations=len(params["codes"])+2)
            _paths(root, delta)
            try:
                record = cache.lookup(delta)
            except ValueError as error:
                if str(error) != "UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY":
                    raise
                result["status"] = "BLOCKED_UNFINISHED_DELTA_NO_RETRY"
                record = None
            if record is None and "status" not in result:
                if operation == "plan":
                    result["status"] = "PLANNED_DELTA_FACTS_ONLY"
                elif operation == "reuse" or offline:
                    result["status"] = "MISSING_DELTA_NO_NETWORK"
                else:
                    _need(len(params["codes"])+2 <= policy["max_sdk_calls"], "COMPLETE_FACTOR_DELTA_BUDGET_REQUIRED")
                    _need(before+16*MIB <= policy["cache_bytes"], "FACTOR_NEW_REQUEST_GROWTH_RESERVATION_REQUIRED")
                    admission()
                    record = cache.capture_one(delta)
            if record is not None:
                observed = cache.verify(record)
                _clock(record.metadata)
                delta_rows = _factor_rows(observed)
                result["delta"] = record.metadata
                if not _complete(observed):
                    result["status"] = "DELTA_PARENT_INCOMPLETE_NO_RETRY"
                else:
                    changes = _compare(base_rows, delta_rows, str(start), str(end))
                    result.update(status="OVERLAP_REVISION_DETECTED_REVALIDATION_REQUIRED" if changes else
                        "DELTA_FACTS_OBSERVED_REVALIDATION_REQUIRED", overlap_changes=changes,
                        overlap_comparison="REVISION_DETECTED" if changes else "EXACT_SOURCE_ROWS_AGREE",
                        overlap_row_count=sum(str(start) <= key[1] <= str(end) for key in base_rows),
                        new_factor_row_count=sum(key[1] > str(end) for key in delta_rows),
                        empty_delta_codes=[code for code in params["codes"] if not any(key[0] == code for key in delta_rows)],
                        empty_delta_meaning="NO_EVENT_ROWS_IN_THIS_EXACT_QUERY_NOT_FACTOR_ONE_OR_HISTORY_COMPLETENESS")
        cache.terminal()
    after = _inventory(root, wall_check)
    _need(after <= policy["cache_bytes"], "FACTOR_CACHE_CAPACITY_EXCEEDED")
    result.update(new_requests=budget.metrics["calls"]["requests"], metrics=budget.metrics,
        cache_logical_bytes_before=before, cache_logical_bytes_after=after,
        added_cache_logical_bytes=after-before, elapsed_seconds=time.monotonic()-started,
        verified_at=datetime.now(timezone.utc).isoformat(),
        consumer_policy="ONLY_AUTHENTICATED_ORIGINAL_FULL_RECORD_THROUGH_ORIGINAL_CONSUMER;TAIL_VIEW_NEVER_INPUT")
    _need(len(_canonical(result)) <= 2*MIB, "FACTOR_REPORT_SIZE_LIMIT")
    return result


def plan(**kwargs):
    """Authenticate exact parents and report missing tail; never acquire data."""
    return _operate("plan", **kwargs)


def reuse(**kwargs):
    """Authenticate saved full/tail sources without creating a synthetic source."""
    return _operate("reuse", **kwargs)


def sync(**kwargs):
    """One explicit tail request at most; offline by default, no implicit retry."""
    return _operate("sync", **kwargs)


def _read_control(path):
    """Read a bounded regular control, pinning the original seven-field identity.

    Access time is deliberately absent: reading a real file may update atime.
    The descriptor and path must retain device/inode/mode/size/mtime/ctime/flags
    before and after reading, including when a pathname changes during open.
    """
    path = Path(path).expanduser().absolute()
    info = path.lstat()
    _need(path.resolve(strict=True) == path and stat.S_ISREG(info.st_mode), "CANONICAL_FACTOR_CONTROL_REQUIRED")
    before = {key: getattr(info, key, 0) for key in FP_KEYS}
    _need(not getattr(info, "st_flags", 0) & 0x40000000 and
          (not info.st_size or getattr(info, "st_blocks", 1) > 0), "RESIDENT_FACTOR_CONTROL_REQUIRED")
    _need(before["st_size"] <= 2*MIB, "FACTOR_CONTROL_SIZE_LIMIT")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        _need(stat.S_ISREG(opened.st_mode) and
              {key: getattr(opened, key, 0) for key in FP_KEYS} == before,
              "FACTOR_CONTROL_OPENED_IDENTITY_CHANGED")
        raw = stream.read(2*MIB+1)
        terminal = os.fstat(stream.fileno())
        _need({key: getattr(terminal, key, 0) for key in FP_KEYS} == before,
              "FACTOR_CONTROL_CHANGED_DURING_READ")
    terminal_path = path.lstat()
    _need(path.resolve(strict=True) == path and
          {key: getattr(terminal_path, key, 0) for key in FP_KEYS} == before and
          len(raw) == before["st_size"], "FACTOR_CONTROL_CHANGED_DURING_READ")
    return _json(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "sync", "reuse"))
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--baseline-record", required=True)
    parser.add_argument("--calendar-record", required=True)
    parser.add_argument("--target-session", required=True)
    parser.add_argument("--overlap-days", type=int, default=7)
    parser.add_argument("--limits-json", default="{}")
    parser.add_argument("--purpose", choices=("DELTA_FACTS_ONLY",))
    parser.add_argument("--execute-network", action="store_true",
        help="Rejected here: use the registered chat coordinator for network sync")
    args = parser.parse_args(argv)
    try:
        _need(not args.execute_network, "FACTOR_NETWORK_REQUIRES_REGISTERED_CHAT_COORDINATOR")
        value = _operate(args.operation, source_root=args.source_root, cache_root=args.cache_root,
            baseline_record=_read_control(args.baseline_record), calendar_record=_read_control(args.calendar_record),
            target_session=args.target_session, overlap_days=args.overlap_days,
            limits=_json(args.limits_json), offline=not args.execute_network, purpose=args.purpose)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0 if value["native_factor_window_available"] else 2
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        print(json.dumps(dict(schema_version=VERSION, status="FAILED", error_type=type(error).__name__,
            reason=str(error)[:2048], strategy_executed=False, full_premarket_ready=False), ensure_ascii=False))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
