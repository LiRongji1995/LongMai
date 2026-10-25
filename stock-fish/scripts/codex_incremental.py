"""Small, explicit RAW increments through the unchanged provider capture cache.

This is a bounded source-acquisition component, not a reduced-market selector.
The chat coordinator owns shared runtime locking and capacity admission. No
business database, source copy, strategy execution, or hidden retry is created.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time as day_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERSION = "CODEX_BOUNDED_RAW_INCREMENT_V1"
MIB = 1024 ** 2
SH = timezone(timedelta(hours=8))
CODE = re.compile(r"(?:sh\.(?:600|601|603|605)|sz\.(?:000|001|002|003))[0-9]{3}", re.ASCII)
CAPS = dict(max_requests=6, max_sdk_calls=18, max_http_attempts=1,
            request_http_attempts=1, request_timeout_seconds=30, max_seconds=180,
            capture_read_bytes=128*MIB, core_read_bytes=MIB, theme_read_bytes=MIB,
            presentation_read_bytes=MIB, supervisor_read_bytes=MIB,
            self_rss_bytes=512*MIB, tree_rss_bytes=768*MIB,
            output_bytes=32*MIB, cache_bytes=32*MIB)


class IncrementalError(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise IncrementalError(reason)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _limits(value):
    value = {} if value is None else value
    _need(isinstance(value, dict) and not set(value)-set(CAPS), "UNKNOWN_INCREMENT_LIMIT")
    result = dict(CAPS, **value)
    for key, maximum in CAPS.items():
        _need(type(result[key]) is int and 0 < result[key] <= maximum,
              "INCREMENT_LIMIT_MUST_NOT_EXCEED_CAP:" + key)
    return result


def _directory(value):
    _need(isinstance(value, (str, Path)), "EXPLICIT_CACHE_DIRECTORY_REQUIRED")
    path = Path(value).expanduser().absolute()
    _need(".." not in path.parts and path.resolve(strict=True) == path,
          "CANONICAL_CACHE_DIRECTORY_REQUIRED")
    _need(stat.S_ISDIR(path.lstat().st_mode), "EXISTING_CACHE_DIRECTORY_REQUIRED")
    return path


def _scope(codes, sessions):
    _need(isinstance(codes, (list, tuple)) and 1 <= len(codes) <= 2
          and all(isinstance(code, str) and CODE.fullmatch(code) for code in codes)
          and len(set(codes)) == len(codes), "ONE_OR_TWO_EXPLICIT_UNIQUE_MAINBOARD_CODES")
    _need(isinstance(sessions, (list, tuple)) and 1 <= len(sessions) <= 3
          and all(isinstance(day, str) for day in sessions)
          and len(set(sessions)) == len(sessions), "ONE_TO_THREE_EXPLICIT_UNIQUE_SESSIONS")
    from stockfish_core.observed_data import exchange_calendar_2026
    registered = set(exchange_calendar_2026().sessions)
    now = datetime.now(timezone.utc)
    for value in sessions:
        try:
            day = date.fromisoformat(value)
        except ValueError:
            raise IncrementalError("EXACT_ISO_SESSION_REQUIRED") from None
        _need(day.isoformat() == value, "EXACT_ISO_SESSION_REQUIRED")
        _need(day in registered, "SESSION_OUTSIDE_REGISTERED_EXCHANGE_CALENDAR")
        _need(datetime.combine(day, day_time(18), SH) <= now, "SESSION_NOT_YET_CLOSED")
    from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec
    codes, sessions = sorted(codes), sorted(sessions)
    specs = [RequestSpec("history", dict(codes=[code], start_date=day, end_date=day),
                         day, role="EXPLICIT_BOUNDED_RAW_INCREMENT", current=False)
             for day in sessions for code in codes]
    return codes, sessions, specs


def _inventory(root):
    from scripts.codex_capacity import storage_report
    value = storage_report([dict(name="shared_capture_cache", path=str(root), role="cache")],
                           max_entries=100000, max_depth=32)
    _need(value["status"] == "COMPLETE", "INCREMENT_CACHE_INVENTORY_PARTIAL")
    return value["totals"]


def _metadata_paths(root, spec):
    folder, attempted = root / "index" / spec.key, root / "raw" / spec.key
    for path in (root / "index", root / "raw", folder, attempted):
        _need(not path.is_symlink(), "INCREMENT_METADATA_SYMLINK_FORBIDDEN")
    return folder, attempted


def plan(*, cache_root, codes, sessions, limits=None):
    """Read metadata only; an index's presence is never asserted as coverage."""
    root, policy = _directory(cache_root), _limits(limits)
    codes, sessions, specs = _scope(codes, sessions)
    inventory = _inventory(root)
    shards = []
    for spec in specs:
        # The safe inventory above rejects cloud placeholders and never follows
        # child links. Explicit metadata paths must not redirect this lookup.
        folder, attempted = _metadata_paths(root, spec)
        present = folder.is_dir() and any(folder.glob("*.json"))
        unfinished = not present and attempted.is_dir() and any(attempted.iterdir())
        shards.append(dict(request_key=spec.key, request_spec=spec.to_dict(),
                           status="UNVERIFIED_INDEX_PRESENT" if present else
                           "UNFINISHED_ATTEMPT_REQUIRES_RECONCILIATION" if unfinished else "NO_INDEX",
                           actual_row_coverage="NOT_CHECKED"))
    return dict(schema_version=VERSION, operation="PLAN_METADATA_ONLY", codes=codes, sessions=sessions,
                shards=shards, limits=policy, cache_root=str(root),
                cache_logical_bytes=inventory["logical_bytes"],
                worst_case_new_requests=len(sessions),
                worst_case_sdk_reservations=(len(codes)+2)*len(sessions),
                transport_policy="ONE_ORIGINAL_HISTORY_BATCH_PER_MISSING_SESSION",
                network_calls=0, coverage="NOT_CHECKED", strategy_executed=False,
                full_premarket_ready=False, calendar_source="REGISTERED_2026_EXCHANGE_SCHEDULE")


def _row_status(result, code, day):
    """Validate exact source rows without inventing absent/invalid prices."""
    rows = result["rows"]
    _need(isinstance(rows, list), "RAW_ROWS_REQUIRED")
    for row in rows:
        _need(isinstance(row, dict) and row.get("code") == code and row.get("date") == day,
              "RAW_ROW_OUTSIDE_EXACT_SECURITY_SESSION")
        _need(str(row.get("adjustflag")) == "3", "RAW_ADJUSTFLAG_REQUIRED")
    if result.get("status") != "COMPLETE" or result.get("pagination_complete") is not True:
        return "UNKNOWN_PARENT_INCOMPLETE"
    if not rows:
        return "UNKNOWN_NO_ACTUAL_ROW"
    if len(rows) != 1:
        return "UNKNOWN_DUPLICATE_SECURITY_SESSION"
    row = rows[0]
    if str(row.get("tradestatus")) == "0":
        return "SUSPENDED_SOURCE_ROW"
    if str(row.get("tradestatus")) != "1":
        return "UNKNOWN_TRADING_STATUS"
    try:
        values = [Decimal(str(row.get(field))) for field in ("open", "high", "low", "close", "preclose")]
        valid = all(value.is_finite() and value > 0 for value in values)
        valid = valid and values[2] <= min(values[0], values[3]) <= max(values[0], values[3]) <= values[1]
    except (ValueError, InvalidOperation):
        valid = False
    return "ACTUAL_RAW_ROW" if valid else "UNKNOWN_INVALID_OHLC"


def _history_clock(metadata):
    """Original ordering alone cannot authorize a capture dated in the future."""
    now = datetime.now(timezone.utc)
    for name in ("started_at", "first_seen_at", "ingested_at", "completed_at"):
        stamp = datetime.fromisoformat(str(metadata[name]).replace("Z", "+00:00"))
        _need(stamp.tzinfo is not None and stamp.utcoffset() is not None,
              "AWARE_HISTORY_SOURCE_CLOCK_REQUIRED")
        _need(stamp <= now, "ORIGINAL_HISTORY_FROM_FUTURE:" + name)


def _covered(original, specs):
    if original.endpoint != "history":
        return []
    params = original.parameters
    return [spec for spec in specs if spec.parameters["codes"][0] in params["codes"]
            and params["start_date"] <= spec.business_session <= params["end_date"]]


def _unfinished_claims(cache, specs, verified):
    """Claims only prevent implicit retries; they never supply source authority."""
    from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec, VERSION as CACHE_VERSION, _json

    def children(path):
        _need(not path.is_symlink() and path.is_dir(), "INCREMENT_ATTEMPT_DIRECTORY_REQUIRED")
        values = []
        for child in path.iterdir():
            values.append(child)
            _need(len(values) <= cache.max_entries, "INCREMENT_ATTEMPT_SCAN_LIMIT")
        return sorted(values)

    def read(path):
        _need(not path.is_symlink() and path.resolve(strict=True) == path,
              "INCREMENT_ATTEMPT_SYMLINK_FORBIDDEN")
        raw, reference = cache._read(path, limit=MIB)
        return _json(raw), reference

    attempts = []
    for folder in children(cache.root / "raw"):
        _need(not folder.is_symlink() and folder.is_dir(), "INCREMENT_ATTEMPT_SYMLINK_FORBIDDEN")
        _need(re.fullmatch("[0-9a-f]{64}", folder.name) is not None,
              "INCREMENT_ATTEMPT_REQUEST_KEY_REQUIRED")
        raw = folder / "raw"
        if raw.is_symlink():
            raise IncrementalError("INCREMENT_ATTEMPT_SYMLINK_FORBIDDEN")
        if not raw.exists():
            _need(not any(folder.iterdir()), "INCREMENT_UNRECOGNIZED_ATTEMPT_LAYOUT")
            continue
        for attempt in children(raw):
            _need(not attempt.is_symlink() and attempt.is_dir(), "INCREMENT_ATTEMPT_SYMLINK_FORBIDDEN")
            attempts.append(attempt)
            _need(len(attempts) <= cache.max_entries, "INCREMENT_ATTEMPT_SCAN_LIMIT")
    blocked = []
    for attempt in attempts:
        path = attempt / "request-claim.json"
        _need(not path.is_symlink(), "INCREMENT_ATTEMPT_SYMLINK_FORBIDDEN")
        if not path.exists():
            blocked.append(dict(request_keys=[spec.key for spec in specs], attempt_dir=str(attempt),
                                reason="UNKNOWN_RAW_ATTEMPT_WITHOUT_CLAIM"))
            continue
        claim, reference = read(path)
        _need(isinstance(claim, dict) and claim.get("schema_version") == CACHE_VERSION,
              "ORIGINAL_ATTEMPT_CLAIM_REQUIRED")
        original = RequestSpec(**claim["request_spec"])
        _need(original.key == attempt.parents[1].name, "ORIGINAL_ATTEMPT_REQUEST_KEY_CHANGED")
        covered = _covered(original, specs)
        if not covered:
            continue
        commit, failed = attempt / "committed.json", attempt / "attempt-failed.json"
        _need(not commit.is_symlink() and not failed.is_symlink(), "INCREMENT_ATTEMPT_SYMLINK_FORBIDDEN")
        matches = [value for value in verified if value.get("capture_attempt_dir") == str(attempt)
                   and value["request_key"] == original.key]
        finished = False
        if commit.exists():
            value, _ = read(commit)
            finished = any(value == dict(request_key=original.key, receipt_id=item["receipt_id"])
                           for item in matches)
        if not finished or failed.exists():
            blocked.append(dict(request_keys=[spec.key for spec in covered], attempt_dir=str(attempt),
                                original_request_spec=original.to_dict(), claim=reference,
                                reason="UNFINISHED_OR_FAILED_ORIGINAL_ATTEMPT_NO_RETRY"))
    return blocked


def _history_rows(result, parameters):
    """Check the entire RAW response before selecting any individual row."""
    _need(isinstance(result.get("rows"), list), "RAW_ROWS_REQUIRED")
    by_identity = {}
    for row in result["rows"]:
        _need(isinstance(row, dict) and row.get("code") in parameters["codes"],
              "ORIGINAL_RANGE_ROW_OUTSIDE_REQUESTED_SECURITY")
        raw_day = row.get("date")
        _need(isinstance(raw_day, str) and date.fromisoformat(raw_day).isoformat() == raw_day
              and parameters["start_date"] <= raw_day <= parameters["end_date"],
              "ORIGINAL_RANGE_ROW_OUTSIDE_REQUESTED_DATE")
        _need(str(row.get("adjustflag")) == "3", "RAW_ADJUSTFLAG_REQUIRED")
        by_identity.setdefault((row["code"], raw_day), []).append(row)
    return by_identity


def _range_descriptor(spec, parents, originals, *, cache_hit=True):
    complete = [parent for parent in parents if parent[3]]
    nonempty = [parent for parent in parents if parent[2]]
    _need(not complete or all(parent[1] == complete[0][1] for parent in complete),
          "CONFLICTING_ORIGINAL_RANGE_ROWS")
    _need(not nonempty or all(parent[1] == nonempty[0][1] for parent in nonempty),
          "CONFLICTING_ORIGINAL_RANGE_ROWS")
    # An incomplete attempt remains unknown beside a complete matching parent.
    parents = sorted(parents, key=lambda parent: (parent[3], parent[0]))
    rows = parents[0][2]
    code, day = spec.parameters["codes"][0], spec.business_session
    status = ("UNKNOWN_PARENT_INCOMPLETE" if not parents[0][3] else
              _row_status(dict(rows=rows, status="COMPLETE", pagination_complete=True), code, day)
              if rows else "UNKNOWN_SOURCE_RANGE_MISSING_ACTUAL_ROW")
    # A view descriptor is not a replacement RAW/result/receipt or narrow index.
    return dict(
        request_key=spec.key, request_spec=spec.to_dict(), cache_hit=cache_hit,
        status=status, parent_status=originals[parents[0][0]]["status"], actual_raw_rows=len(rows),
        receipt=originals[parents[0][0]],
        coverage_from_original_request=dict(
            original_receipt_ids=sorted({parent[0] for parent in parents}),
            incomplete_original_receipt_ids=sorted({parent[0] for parent in parents if not parent[3]}),
            matching_raw_row_count=len(rows), exact_shard_index_created=False,
            original_request_parameters_preserved=True,
            contract="VERIFIED_WHOLE_ORIGINAL_RANGE_READ_ONLY_ROW_VIEW",
            agreement_policy="COMPLETE_ROWS_AGREE_NONEMPTY_ROWS_AGREE_INCOMPLETE_REMAINS_UNKNOWN"))


def _range_views(cache, specs, attempt_specs, authenticated):
    """Verify whole registered parents; expose row views without new sources.

    Exact prior attempts always take precedence outside this function. A
    complete original range with no target row is evidence of an unknown, not
    permission to repeat that request with a smaller transport scope.
    """
    from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec
    paths = sorted((cache.root / "index").glob("*/*.json"))
    _need(len(paths) <= cache.max_entries, "INCREMENT_RANGE_INDEX_LIMIT")
    for path in paths:
        _need(not path.parent.is_symlink() and not path.is_symlink(), "INCREMENT_METADATA_SYMLINK_FORBIDDEN")
        _need(path.lstat().st_size <= MIB, "INCREMENT_RANGE_INDEX_FILE_LIMIT")
    matches, originals, verified = {spec.key: [] for spec in specs}, {}, []
    for metadata in cache._entries():
        original_spec = RequestSpec(**metadata["request_spec"])
        _need(original_spec.key == metadata["request_key"], "INCREMENT_RANGE_REQUEST_KEY_CHANGED")
        if original_spec.endpoint != "history":
            continue
        parameters = original_spec.parameters
        if not _covered(original_spec, attempt_specs):
            continue
        _metadata_paths(cache.root, original_spec)
        previous = authenticated.get(original_spec.key)
        if previous is not None and previous[0] == metadata:
            result = previous[1]
        else:
            result = cache.verify(metadata)
            _history_clock(metadata)
        verified.append(metadata)
        covered = _covered(original_spec, specs)
        if not covered:
            continue
        by_identity = _history_rows(result, parameters)
        rid = metadata["receipt_id"]
        prior = originals.setdefault(rid, metadata)
        _need(prior == metadata, "ORIGINAL_RANGE_RECEIPT_METADATA_CONFLICT")
        for spec in covered:
            selected = by_identity.get((spec.parameters["codes"][0], spec.business_session), [])
            complete = result["status"] == "COMPLETE" and result.get("pagination_complete") is True
            matches[spec.key].append((rid, sorted(_canonical(row) for row in selected), selected, complete))
    blocked = _unfinished_claims(cache, attempt_specs, verified)
    views = {}
    for spec in specs:
        parents = matches[spec.key]
        if not parents:
            continue
        views[spec.key] = _range_descriptor(spec, parents, originals)
    return views, originals, blocked


def _missing_batches(specs):
    """Merge only missing securities sharing one exact session."""
    from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec
    grouped = {}
    for spec in specs:
        grouped.setdefault(spec.business_session, []).append(spec)
    return [(RequestSpec("history", dict(codes=[spec.parameters["codes"][0] for spec in members],
                                        start_date=day, end_date=day), day,
                         role="EXPLICIT_BOUNDED_RAW_INCREMENT_SESSION_BATCH"), members)
            for day, members in sorted(grouped.items())]


def _calendar(cache, record, sessions):
    _need(isinstance(record, dict) and record.get("endpoint") == "calendar",
          "ORIGINAL_CALENDAR_CAPTURE_METADATA_REQUIRED")
    from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec
    _need(isinstance(record.get("request_spec"), dict), "ORIGINAL_CALENDAR_REQUEST_SPEC_REQUIRED")
    spec = RequestSpec(**record["request_spec"])
    _metadata_paths(cache.root, spec)
    registered = cache.lookup(spec)
    _need(registered is not None and registered.metadata == record,
          "CALENDAR_MUST_BE_REGISTERED_IN_SAME_SHARED_CACHE")
    result = cache.verify(record)
    _need(result["status"] == "COMPLETE" and result.get("pagination_complete") is True,
          "COMPLETE_ORIGINAL_CALENDAR_REQUIRED")
    start, end = [date.fromisoformat(result["parameters"][key]) for key in ("start_date", "end_date")]
    _need(0 <= (end-start).days <= 1500, "BOUNDED_ORIGINAL_CALENDAR_REQUIRED")
    days = {}
    for row in result["rows"]:
        day = row.get("calendar_date")
        _need(isinstance(day, str) and day not in days and row.get("is_trading_day") in ("0", "1"),
              "ORIGINAL_CALENDAR_DUPLICATE_OR_UNKNOWN_DAY")
        days[day] = row["is_trading_day"]
    expected = {str(start+timedelta(days=n)) for n in range((end-start).days+1)}
    _need(set(days) == expected, "ORIGINAL_CALENDAR_NATURAL_DAY_GAP")
    _need(all(days.get(day) == "1" for day in sessions), "REQUESTED_SESSION_NOT_ORIGINAL_TRADING_DAY")
    _need(datetime.fromisoformat(record["completed_at"]) <= datetime.now(timezone.utc),
          "ORIGINAL_CALENDAR_FROM_FUTURE")
    return {key: record[key] for key in ("receipt_id", "raw", "result", "receipt", "first_seen_at", "completed_at")}


def sync(*, cache_root, codes, sessions, calendar_record, limits=None, offline=False,
         executor=None, allow_test_fixture=False):
    """Acquire absent row scopes in same-session batches; retain every original.

    ``executor`` injection requires explicit TEST_FIXTURE. The CLI exposes no
    injection flag. Caller must hold its registered runtime's exclusive lock
    and apply storage preflight before this component is invoked.
    """
    _need(type(offline) is bool and type(allow_test_fixture) is bool, "BOOLEAN_INCREMENT_POLICY_REQUIRED")
    _need(executor is None or allow_test_fixture, "EXECUTOR_REQUIRES_EXPLICIT_TEST_FIXTURE")
    started = time.monotonic()
    root, policy = _directory(cache_root), _limits(limits)
    codes, sessions, specs = _scope(codes, sessions)
    before = _inventory(root)
    _need(before["logical_bytes"] <= policy["cache_bytes"], "INCREMENT_CACHE_CAPACITY_EXCEEDED")
    for spec in specs:
        _metadata_paths(root, spec)
    from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget
    budget = Budget(policy, cache_dir=root)
    options = dict(offline=offline, retry_failed=False, allow_test_fixture=allow_test_fixture,
                   request_timeout_seconds=policy["request_timeout_seconds"],
                   max_http_requests_per_request=1, source_file_bytes=MIB,
                   index_file_bytes=MIB, max_cache_entries=256)
    kwargs = {} if executor is None else dict(executor=executor)
    cache = CaptureCache(root, budget, config=options, **kwargs)
    shards, known, known_results, blocked = [], {}, {}, []
    with budget.phase("capture"), budget.meter_files():
        calendar = _calendar(cache, calendar_record, sessions)
        # Authenticate all existing identities before admitting any network.
        # Corruption remains an error; it is never converted to a cache miss.
        for spec in specs:
            try:
                known[spec.key] = cache.lookup(spec)
                if known[spec.key] is not None:
                    result = cache.verify(known[spec.key])
                    _history_clock(cache.metadata(known[spec.key]))
                    _row_status(result, spec.parameters["codes"][0], spec.business_session)
                    known_results[spec.key] = result
            except ValueError as error:
                if str(error) != "UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY":
                    raise
                blocked.append(spec.key)
        if blocked:
            cache.terminal()
            return dict(schema_version=VERSION, status="BLOCKED_UNFINISHED_CAPTURE_NO_RETRY",
                        codes=codes, sessions=sessions, blocked_request_keys=blocked,
                        new_requests=0, strategy_executed=False, full_premarket_ready=False,
                        network_transfer_bytes=None, metrics=budget.metrics)
        range_views, range_originals, attempts = _range_views(
            cache, [spec for spec in specs if known[spec.key] is None], specs,
            {key: (cache.metadata(record), known_results[key]) for key, record in known.items()
             if record is not None})
        if attempts:
            cache.terminal()
            return dict(schema_version=VERSION, status="BLOCKED_UNFINISHED_CAPTURE_NO_RETRY",
                        codes=codes, sessions=sessions,
                        blocked_request_keys=sorted({key for attempt in attempts for key in attempt["request_keys"]}),
                        blocking_original_attempts=attempts, new_requests=0,
                        strategy_executed=False, full_premarket_ready=False,
                        network_transfer_bytes=None, metrics=budget.metrics)
        batches = _missing_batches([spec for spec in specs
                                    if known[spec.key] is None and spec.key not in range_views])
        _need(len(batches) <= policy["max_requests"]
              and sum(len(batch.parameters["codes"])+2 for batch, _ in batches) <= policy["max_sdk_calls"],
              "COMPLETE_INCREMENT_REQUEST_BUDGET_REQUIRED")
        _need(not offline or not batches, "OFFLINE_INCREMENT_HAS_MISSING_SHARDS")
        fresh, batch_receipts = {}, []
        for batch, members in batches:
            _metadata_paths(root, batch)
        for batch, members in batches:
            record = cache.capture_one(batch)
            result, metadata = cache.verify(record), cache.metadata(record)
            _history_clock(metadata)
            grouped = _history_rows(result, batch.parameters)
            batch_receipts.append(metadata)
            for spec in members:
                code, day = spec.parameters["codes"][0], spec.business_session
                if len(members) == 1:
                    fresh[spec.key] = dict(request_key=spec.key, request_spec=spec.to_dict(), cache_hit=False,
                        status=_row_status(result, code, day), parent_status=result["status"],
                        actual_raw_rows=len(result["rows"]), receipt=metadata)
                else:
                    rows = grouped.get((code, day), [])
                    complete = result["status"] == "COMPLETE" and result.get("pagination_complete") is True
                    fresh[spec.key] = _range_descriptor(spec,
                        [(metadata["receipt_id"], sorted(_canonical(row) for row in rows), rows, complete)],
                        {metadata["receipt_id"]: metadata}, cache_hit=False)
        for spec in specs:
            if spec.key in range_views:
                shards.append(range_views[spec.key])
                continue
            if spec.key in fresh:
                shards.append(fresh[spec.key])
                continue
            record, result = known[spec.key], known_results[spec.key]
            code, day = spec.parameters["codes"][0], spec.business_session
            status = _row_status(result, code, day)
            shards.append(dict(request_key=spec.key, request_spec=spec.to_dict(),
                               cache_hit=known[spec.key] is not None, status=status,
                               parent_status=result["status"], actual_raw_rows=len(result["rows"]),
                               receipt=cache.metadata(record)))
        cache.terminal()
    after = _inventory(root)
    _need(after["logical_bytes"] <= policy["cache_bytes"], "INCREMENT_CACHE_CAPACITY_EXCEEDED")
    unknowns = [{key: row[key] for key in ("request_key", "request_spec", "status")}
                for row in shards if row["status"].startswith("UNKNOWN")]
    receipt = dict(schema_version=VERSION, status="COMPLETE_EXPLICIT_RAW_SCOPE" if not unknowns else "PARTIAL_EXPLICIT_RAW_SCOPE",
                coverage_type="TEST_FIXTURE" if allow_test_fixture else "RECONSTRUCTED_HISTORICAL",
                codes=codes, sessions=sessions, shards=shards, unknowns=unknowns,
                actual_raw_rows=sum(row["actual_raw_rows"] for row in shards),
                cache_hits=sum(row["cache_hit"] for row in shards),
                range_reuse_views=len(range_views),
                range_originals=[range_originals[key] for key in sorted(range_originals)],
                new_batch_receipts=batch_receipts,
                new_batch_row_views=sum("coverage_from_original_request" in value for value in fresh.values()),
                transport_policy="ONE_ORIGINAL_HISTORY_BATCH_PER_MISSING_SESSION",
                new_requests=budget.metrics["calls"]["requests"], metrics=budget.metrics,
                cache_root=str(root), cache_logical_bytes_before=before["logical_bytes"],
                cache_logical_bytes_after=after["logical_bytes"],
                added_cache_logical_bytes=after["logical_bytes"]-before["logical_bytes"],
                elapsed_seconds=time.monotonic()-started, network_transfer_bytes=None,
                verified_calendar=calendar,
                network_transfer_measurement="NOT_MEASURED_BY_ORIGINAL_SDK_ADAPTER",
                actual_source_clocks_rewritten=False, database_created=False,
                strategy_executed=False, full_premarket_ready=False,
                scope="RAW_ACQUISITION_ONLY_NOT_UNIVERSE_ELIGIBILITY_FACTOR_OR_PIT_CERTIFICATION")
    _need(len(_canonical(receipt)) <= MIB, "INCREMENT_RECEIPT_SIZE_LIMIT")
    return receipt


def _registered_runtime(name, registry_root, overrides, offline):
    from scripts.codex_workspace import load_runtime
    from scripts.codex_premarket import _prepared, _control
    runtime = load_runtime(name, registry_root)
    _need(runtime is not None and runtime.profile["backend"] == "premarket",
          "REGISTERED_PREMARKET_RUNTIME_REQUIRED")
    profile = runtime.profile
    prepared = _prepared(profile)
    _need(prepared["source"] == ROOT, "INCREMENT_REGISTERED_SOURCE_ROOT_MISMATCH")
    _need(Path(sys.executable).absolute() == prepared["python"], "INCREMENT_REGISTERED_PYTHON_MISMATCH")
    config, _ = _control(prepared["config"])
    inherited = config.get("limits", {})
    capture = config.get("capture", {})
    _need(isinstance(inherited, dict) and isinstance(capture, dict), "INVALID_REGISTERED_INCREMENT_POLICY")
    policy = _limits(overrides)
    for key in CAPS:
        if key in inherited:
            value = inherited[key]
            if key == "max_seconds" and value is None:
                continue
            _need(type(value) in (int, float) and value > 0 and math.isfinite(value),
                  "INVALID_REGISTERED_INCREMENT_LIMIT")
            # Original runtime allows fractional timeouts. Round down; a value
            # below one second cannot be represented by this pilot contract.
            value = int(min(value, policy[key]))
            _need(value > 0, "REGISTERED_LIMIT_TOO_SMALL_FOR_INCREMENT")
            policy[key] = value
    if "request_timeout_seconds" in capture:
        value = capture["request_timeout_seconds"]
        _need(type(value) in (int, float) and value > 0 and math.isfinite(value),
              "INVALID_REGISTERED_INCREMENT_TIMEOUT")
        policy["request_timeout_seconds"] = int(min(policy["request_timeout_seconds"], value))
        _need(policy["request_timeout_seconds"] > 0, "REGISTERED_LIMIT_TOO_SMALL_FOR_INCREMENT")
    _need(type(capture.get("offline", False)) is bool, "INVALID_REGISTERED_OFFLINE_POLICY")
    return runtime, prepared["state"] / "cache", _limits(policy), offline or capture.get("offline", False)


def _calendar_control(path):
    from scripts.codex_premarket import _control, _reference
    calendar_path = Path(path).expanduser().absolute()
    _reference(calendar_path, maximum=MIB)
    value, _ = _control(calendar_path)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=
        "V1 resource scope: at most 2 securities x 3 closed sessions; this does not replace full daily acquisition.")
    parser.add_argument("operation", choices=("plan", "sync"))
    location = parser.add_mutually_exclusive_group(required=True)
    location.add_argument("--cache-root", help="Explicit low-level cache; caller must own storage admission")
    location.add_argument("--runtime", help="Registered shared premarket runtime; use its interpreter")
    parser.add_argument("--registry-root")
    parser.add_argument("--codes", nargs="+", required=True)
    parser.add_argument("--sessions", nargs="+", required=True)
    parser.add_argument("--limits-json", default="{}")
    parser.add_argument("--calendar-record", help="Existing original CaptureCache calendar metadata JSON; required by sync")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    try:
        limits = json.loads(args.limits_json)
        _need(args.runtime is not None or args.registry_root is None, "REGISTRY_REQUIRES_RUNTIME")
        runtime = None
        cache_root, offline = args.cache_root, args.offline
        if args.runtime:
            runtime, cache_root, limits, offline = _registered_runtime(args.runtime, args.registry_root, limits, offline)
        values = dict(cache_root=cache_root, codes=args.codes, sessions=args.sessions, limits=limits)
        def execute():
            if args.operation == "plan":
                return plan(**values)
            _need(args.calendar_record is not None, "ORIGINAL_CALENDAR_CAPTURE_METADATA_REQUIRED")
            calendar_record = _calendar_control(args.calendar_record)
            return sync(**values, calendar_record=calendar_record, offline=offline)
        if runtime is None:
            value = execute()
        elif args.operation == "plan":
            # Location validation is read-only; planning does not create locks.
            runtime.check_binding()
            value = execute()
        else:
            with runtime.exclusive():
                _need(runtime.preflight()["allowed"], "CAPACITY_PREFLIGHT_BLOCKED")
                value = execute()
                runtime.check_binding()
                value["capacity_after_allowed"] = runtime.preflight(expected_growth_bytes=0)["allowed"]
        print(_canonical(value).decode("utf-8"))
        return 0 if value.get("status") in (None, "COMPLETE_EXPLICIT_RAW_SCOPE") else 2
    except Exception as error:
        # Raw exception text may contain provider responses or private paths.
        code = str(error) if isinstance(error, IncrementalError) else "INCREMENT_FAILED:"+type(error).__name__
        print(json.dumps(dict(schema_version=VERSION, status="FAILED", error=code)))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
