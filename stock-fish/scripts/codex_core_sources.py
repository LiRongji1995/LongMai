"""Offline, bounded planning of the frozen 25-session native-core sources.

The original strategy remains unchanged. The 67-session calendar requirement
is retained; the additional 42 market sessions are optional cached context.
Authenticated sources also feed the original qualification rules before factor
requests. This planner never downloads or executes a strategy selection.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time as wall_time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stockfish_core.file_integrity import (
    content_equal, content_reference_equal, content_fingerprint, verify_content_reference,
)

from scripts import codex_core as original
from scripts import codex_workspace as workspace

VERSION = "CODEX_CORE_SOURCES_FIRST_V1"
PROFILE = "CORE_SOURCES_FIRST_V1"
NATIVE_IDENTITY = "NATIVE_CORE_UNKNOWN_PRESERVED_V1"
LEGACY_IDENTITY = "LEGACY_COLLECTOR_STRICT_V1"
MIB, GIB = 1024 ** 2, 1024 ** 3
SH = timezone(timedelta(hours=8))
LIMITS = dict(capture_read_bytes=4*GIB, core_read_bytes=MIB, theme_read_bytes=MIB,
              presentation_read_bytes=MIB, supervisor_read_bytes=MIB,
              max_requests=1, max_sdk_calls=1, max_http_attempts=1,
              request_http_attempts=1, request_timeout_seconds=30,
              max_seconds=900, self_rss_bytes=768*MIB, tree_rss_bytes=GIB,
              output_bytes=4*MIB, cache_bytes=256*MIB)


class SourcesError(ValueError):
    pass


def _need(value, reason):
    if not value:
        raise SourcesError(reason)


def _no_network(*args, **kwargs):
    raise SourcesError("CORE_SOURCE_PLANNER_NETWORK_FORBIDDEN")


def _limits(value):
    value = {} if value is None else value
    _need(type(value) is dict and not set(value)-set(LIMITS), "UNKNOWN_CORE_SOURCE_LIMIT")
    result = dict(LIMITS, **value)
    for key, cap in LIMITS.items():
        _need(type(result[key]) is int and 0 < result[key] <= cap, "CORE_SOURCE_LIMIT_EXCEEDS_CAP:"+key)
    return result


def _existing_cache(path):
    from scripts.codex_capacity import storage_report
    path = Path(path).absolute()
    _need(path.resolve(strict=True) == path and stat.S_ISDIR(path.lstat().st_mode),
          "CORE_SOURCE_EXISTING_CANONICAL_CACHE_REQUIRED")
    for name in ("index", "raw", "raw-index", "locators"):
        child = path / name
        _need(stat.S_ISDIR(child.lstat().st_mode), "CORE_SOURCE_EXISTING_CACHE_LAYOUT_REQUIRED")
    inventory = storage_report([dict(name="cache", path=str(path), role="cache")],
                               max_entries=100000, max_depth=32)
    _need(inventory["status"] == "COMPLETE", "CORE_SOURCE_CACHE_INVENTORY_INCOMPLETE")
    return path, inventory["totals"]


class _Index:
    """One bounded index scan; each selected original is authenticated once.

    The memo is invocation local, never a source capability. The original
    cache's complete terminal SHA/physical-identity check still runs at the end.
    """
    def __init__(self, cache, instant, fixture):
        self.cache, self.instant, self.fixture = cache, instant, fixture
        self.entries = list(cache._entries())
        self.by_key, self.checked = {}, {}
        for value in self.entries:
            self.by_key.setdefault(value["request_key"], []).append(value)
        self.query_count = 0
        self.later_originals = 0
        self.lookups = {}

    def verify(self, value):
        from stockfish_core.yaogu.premarket_capture_cache_v1 import _canonical, _stamp
        key = hashlib.sha256(_canonical(value)).hexdigest()
        if key not in self.checked:
            try:
                result = self.cache.verify(value)
            except ValueError as error:
                if str(error) == "EXPLICIT_SUPPORTED_PROVIDER_OR_TEST_FIXTURE_REQUIRED":
                    raise SourcesError("CORE_SOURCE_REAL_AND_FIXTURE_SEPARATION") from error
                raise
            _need((result["provider"] == "TEST_FIXTURE") == self.fixture,
                  "CORE_SOURCE_REAL_AND_FIXTURE_SEPARATION")
            _need(_stamp(value["completed_at"]) <= self.instant
                  and _stamp(value["first_seen_at"]) <= self.instant,
                  "CORE_SOURCE_NOT_VISIBLE_AT_PLANNING_TIME")
            self.checked[key] = result
        return self.checked[key]

    def lookup(self, spec):
        from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec, _stamp
        if spec.key in self.lookups:
            return self.lookups[spec.key]
        self.query_count += 1
        all_values = self.by_key.get(spec.key, [])
        values = [value for value in all_values if _stamp(value["completed_at"]) <= self.instant
                  and _stamp(value["first_seen_at"]) <= self.instant]
        self.later_originals += len(all_values)-len(values)
        for value in values:
            _need(RequestSpec(**value["request_spec"]).key == spec.key, "CORE_SOURCE_REQUEST_SPEC_CHANGED")
            self.verify(value)
        if not values:
            attempt = self.cache.root / "raw" / spec.key
            _need(not attempt.is_symlink(), "CORE_SOURCE_ATTEMPT_LINK_FORBIDDEN")
            status = "NOT_VISIBLE_AT_CUTOFF" if all_values else (
                "UNFINISHED_NO_IMPLICIT_RETRY" if attempt.exists() and any(attempt.iterdir()) else "MISSING")
            answer = (None, None, status)
        else:
            latest = max(_stamp(value["completed_at"]) for value in values)
            tied = [value for value in values if _stamp(value["completed_at"]) == latest]
            _need(len({value["receipt_id"] for value in tied}) == 1, "CORE_SOURCE_SAME_CLOCK_CONFLICT")
            value = tied[0]
            result = self.verify(value)
            status = "AVAILABLE" if result["status"] == "COMPLETE" and result["pagination_complete"] else "INCOMPLETE_NO_IMPLICIT_RETRY"
            answer = (value, result, status)
        self.lookups[spec.key] = answer
        return answer

    def calendar(self):
        from stockfish_core.yaogu.premarket_capture_cache_v1 import _stamp
        day = str(self.instant.astimezone(SH).date())
        values = []
        for value in self.entries:
            if value["endpoint"] != "calendar" or value["status"] != "COMPLETE" or not value["pagination_complete"]:
                continue
            params = value["parameters"]
            if params["start_date"] <= day < params["end_date"] and _stamp(value["completed_at"]) <= self.instant:
                result = self.verify(value)
                sessions = [date.fromisoformat(row["calendar_date"]) for row in result["rows"]
                            if row.get("is_trading_day") == "1"]
                completed = [d for d in sessions if datetime.combine(d, wall_time(18), SH) <= self.instant]
                if completed and len([d for d in sessions if d <= max(completed)]) >= 67 and any(d > max(completed) for d in sessions):
                    values.append(value)
        if not values:
            return None, None
        value = max(values, key=lambda v: _stamp(v["completed_at"]))
        return value, self.verify(value)


def _calendar(result, instant):
    from stockfish_core.yaogu.premarket_workflow_v1 import _latest_gate
    start, end = (date.fromisoformat(result["parameters"][key]) for key in ("start_date", "end_date"))
    flags = {}
    for row in result["rows"]:
        day = date.fromisoformat(row["calendar_date"])
        _need(day not in flags and row["is_trading_day"] in ("0", "1"), "CORE_SOURCE_CALENDAR_DUPLICATE_OR_UNKNOWN")
        flags[day] = row["is_trading_day"]
    _need(set(flags) == {start+timedelta(days=n) for n in range((end-start).days+1)},
          "CORE_SOURCE_CALENDAR_NATURAL_DAY_GAP")
    sessions = sorted(day for day, flag in flags.items() if flag == "1")
    closed = [day for day in sessions if datetime.combine(day, wall_time(18), SH) <= instant]
    _need(closed, "CORE_SOURCE_NO_COMPLETED_SESSION")
    observed = closed[-1]
    following = [day for day in sessions if day > observed]
    _need(following and len(closed) >= 67, "CORE_SOURCE_NEXT_OR_LAST67_UNAVAILABLE")
    gate = _latest_gate(result["rows"], observed, following[0], instant)
    return str(observed), str(following[0]), [str(day) for day in closed[-25:]], [str(day) for day in closed[-67:]], gate


def _collect(index, config, identity_policy):
    from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec, _listing, _stamp, ORDINARY
    local = index.instant.astimezone(SH).date()
    plans, sources, dependencies = [], {}, []

    def take(spec):
        record, result, status = index.lookup(spec)
        plans.append(dict(request_key=spec.key, request_spec=spec.to_dict(), status=status,
                          receipt_id=None if record is None else record["receipt_id"]))
        if record is not None:
            sources[record["receipt_id"]] = record
        return record, result, status

    calendar, calendar_result = index.calendar()
    if calendar is None:
        lookback, lookahead = int(config.get("calendar_lookback_days", 800)), int(config.get("calendar_lookahead_days", 32))
        _need(90 <= lookback <= 1500 and 1 <= lookahead <= 366, "CORE_SOURCE_FINITE_CALENDAR_WINDOW")
        spec = RequestSpec("calendar", dict(start_date=str(local-timedelta(days=lookback)),
            end_date=str(local+timedelta(days=lookahead))), str(local), role="CALENDAR")
        take(spec)
        return dict(status="BLOCKED_MISSING_CURRENT_CALENDAR", requests=plans,
                    dependencies=["AUTHENTICATED_CALENDAR_REQUIRED_FOR_EXACT_25_SESSION_SCOPE"],
                    captures=[], core_ready_to_run=False, collector_qualification_ready=False)
    sources[calendar["receipt_id"]] = calendar
    observed, target, price, theme, gate = _calendar(calendar_result, index.instant)
    plans.append(dict(request_key=calendar["request_key"], request_spec=calendar["request_spec"],
                      status="AVAILABLE", receipt_id=calendar["receipt_id"]))
    official_specs = [RequestSpec(ep, {}, str(local), role="CURRENT_OFFICIAL", current=True)
                      for ep in ("mainboard_sse", "master_szse", "delisting_sse")]
    market_specs = [RequestSpec(ep, dict(date=observed), observed, role="FULL_MARKET_CONTEXT")
                    for ep in ("market_daily", "master_all")]
    # A new close cannot be processed without its own full market and master.
    # Authenticate these first, then current official snapshots. A gap returns
    # only the scope actually checked: it does not declare the old 24 sessions,
    # factor scope, or qualification gaps complete. Successful probes are kept
    # in the same invocation memo; the full path below keeps original ordering.
    probed = []
    for stage, specs in (("LATEST_MARKET", market_specs), ("CURRENT_OFFICIAL", official_specs)):
        for spec in specs:
            record, result, status = index.lookup(spec)
            probed.append((spec, record, result, status))
        if any(status != "AVAILABLE" for _, _, _, status in probed):
            for spec, record, _, status in probed:
                plans.append(dict(request_key=spec.key, request_spec=spec.to_dict(), status=status,
                                  receipt_id=None if record is None else record["receipt_id"]))
                if record is not None:
                    sources[record["receipt_id"]] = record
            return dict(status="BLOCKED_CURRENT_SESSION_GAPS", observation_date=observed,
                target_session=target, calendar_currentness=gate, core_sessions=price,
                theme_calendar_sessions=theme, requests=plans, captures=list(sources.values()),
                core_ready_to_run=False, collector_qualification_ready=False, legacy_collector_ready=False,
                gap_scope="CURRENT_SESSION_PREREQUISITES_ONLY", blocked_prerequisite_stage=stage,
                remaining_core_dependencies="NOT_EVALUATED", historical_core_market_status="NOT_EVALUATED",
                current_official_status="NOT_EVALUATED" if stage == "LATEST_MARKET" else "EVALUATED_WITH_GAPS",
                dependencies=["OLDER_24_SESSION_ORIGINALS_NOT_EVALUATED", "LISTING_AND_FULL_FACTOR_SCOPE_NOT_EVALUATED"],
                qualification_diagnostics=dict(status="NOT_EVALUATED", RAW_union_count=None,
                    missing_positive_listing_codes=None, future_listing_codes=None,
                    current_roster_positive_outDate_conflicts=None, exact_missing_scope=False,
                    factor_query_codes=None, query_scope_not_eligibility=True),
                theme_context=dict(status="UNKNOWN_NOT_EXECUTED", required_sessions=67,
                    additional_market_sessions_deferred=theme[:-25], additional_market_requests_not_issued=84,
                    membership_and_theme_computation="NOT_EXECUTED", core_scope_shortened=False))
    official = [take(spec) for spec in official_specs]
    raw_union, latest_master = set(), None
    market_complete = True
    for day in price:
        for endpoint in ("market_daily", "master_all"):
            _, result, status = take(RequestSpec(endpoint, dict(date=day), day, role="FULL_MARKET_CONTEXT"))
            market_complete = market_complete and status == "AVAILABLE"
            if result is not None and status == "AVAILABLE":
                if endpoint == "market_daily":
                    raw_union.update(row["code"] for row in result["rows"])
                elif day == observed:
                    latest_master = result
    official_complete = all(status == "AVAILABLE" for _, _, status in official)
    roster, mainboard = {}, set()
    for (_, result, status), is_sse in zip(official[:2], (True, False)):
        if status != "AVAILABLE":
            continue
        for row in result["rows"]:
            code = ("sh."+row["证券代码"]) if is_sse else ("sz."+row["A股代码"])
            _need(code not in roster, "CORE_SOURCE_CONFLICTING_CURRENT_OFFICIAL_IDENTITY")
            roster[code] = row
            if is_sse or row.get("板块") == "主板":
                mainboard.add(code)
    basic = {}
    prior = sorted((value for value in index.entries if value["endpoint"] in ("stock_basic", "stock_basic_all")
                    and value["status"] == "COMPLETE" and value["pagination_complete"]
                    and _stamp(value["completed_at"]) <= index.instant), key=lambda v: _stamp(v["completed_at"]))
    for value in prior:
        result = index.verify(value)
        basic.update((row["code"], row) for row in result["rows"])
        sources[value["receipt_id"]] = value

    def listing(code):
        row = roster.get(code, {})
        return _listing(row.get("上市日期") if code.startswith("sh.") else row.get("A股上市日期")) or _listing(basic.get(code, {}).get("ipoDate"))

    missing = sorted(code for code in raw_union if listing(code) is None)
    batch_size = int(config.get("batch_size", 50))
    _need(1 <= batch_size <= 100, "CORE_SOURCE_PROVIDER_BATCH_SIZE_BOUND")
    all_pending = False
    native_identity = identity_policy == NATIVE_IDENTITY
    if market_complete and official_complete and missing and config.get("basic_all_first", True) and (not native_identity or not prior):
        _, result, status = take(RequestSpec("stock_basic_all", {}, str(local), role="CURRENT_BASIC_ATTEMPT", current=True))
        all_pending = status == "MISSING"
        if status == "AVAILABLE":
            basic.update((row["code"], row) for row in result["rows"])
            missing = sorted(code for code in raw_union if listing(code) is None)
    fallback = config.get("basic_batch_fallback", True)
    _need(type(fallback) is bool, "CORE_SOURCE_EXPLICIT_BASIC_FALLBACK_POLICY")
    if missing and all_pending:
        dependencies.append("BASIC_FALLBACK_SCOPE_AWAITS_CURRENT_BASIC_ALL_RESULT")
    elif market_complete and official_complete and missing and fallback and not native_identity:
        for offset in range(0, len(missing), batch_size):
            _, result, status = take(RequestSpec("stock_basic", dict(codes=missing[offset:offset+batch_size]),
                str(local), role="REGISTERED_BASIC_FALLBACK", current=True))
            if status == "AVAILABLE":
                basic.update((row["code"], row) for row in result["rows"])
        missing = sorted(code for code in raw_union if listing(code) is None)
    if not market_complete or not official_complete:
        dependencies.append("COMPLETE_MARKET_AND_OFFICIAL_ORIGINALS_REQUIRED_FOR_EXACT_LISTING_GAPS")
    future = sorted(code for code in raw_union if listing(code) is not None and listing(code) > date.fromisoformat(observed))
    conflicts = sorted(code for code in raw_union & set(roster) if _listing(basic.get(code, {}).get("outDate")) is not None
                       and _listing(basic[code]["outDate"]) <= date.fromisoformat(observed))
    factor_codes, factor_scope = [], None
    if latest_master is not None and official_complete:
        from stockfish_core.yaogu.collection_qualification import factor_query_scope
        from stockfish_core.yaogu.collection_factor_scope import plan_factor_requests
        original_factor_codes = sorted({row["code"] for row in latest_master["rows"]
                              if row["code"] in mainboard and ORDINARY.fullmatch(row["code"])})
        qualification_sources = [value for value in sources.values()
            if value["endpoint"] not in ("market_daily", "master_all")
            or value["parameters"].get("date") == observed]
        factor_scope = factor_query_scope([index.verify(value) for value in qualification_sources],
            requested_codes=original_factor_codes, observation_date=observed,
            cutoff=index.instant, sessions=[row["calendar_date"] for row in calendar_result["rows"]
                if row["is_trading_day"] == "1" and row["calendar_date"] <= observed],
            coverage_type="TEST_FIXTURE" if index.fixture else "RECONSTRUCTED_HISTORICAL")
        factor_scope = {key: value for key, value in factor_scope.items()
                        if key not in ("qualification", "known_excluded_records")}
        factor_codes = factor_scope["factor_query_codes"]
        for spec in plan_factor_requests(index.cache, codes=factor_codes, observed=observed,
                batch_size=batch_size, entries=index.entries):
            _, _, status = take(spec)
            if status == "MISSING":
                blockers = index.cache.factor_overlap_blockers(spec, entries=index.entries)
                if blockers:
                    plans[-1].update(status="OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY",
                                     overlapping_prior_requests=blockers)
    else:
        dependencies.append("CURRENT_MASTER_AND_OFFICIAL_ORIGINALS_REQUIRED_FOR_EXACT_FACTOR_SCOPE")
    qualification_ready = market_complete and official_complete and not (missing or future or conflicts)
    ready = not dependencies and (native_identity or qualification_ready) and all(p["status"] == "AVAILABLE" for p in plans)
    return dict(status="READY_ORIGINAL_CORE_SOURCES" if ready else "BLOCKED_CORE_SOURCE_GAPS",
        observation_date=observed, target_session=target, calendar_currentness=gate,
        core_sessions=price, theme_calendar_sessions=theme, requests=plans, dependencies=dependencies,
        captures=list(sources.values()), core_ready_to_run=ready,
        gap_scope="FULL_25_SESSION_CORE_DEPENDENCIES", remaining_core_dependencies="EVALUATED",
        historical_core_market_status="EVALUATED", current_official_status="EVALUATED",
        collector_qualification_ready=qualification_ready, legacy_collector_ready=qualification_ready and all(p["status"] in ("AVAILABLE", "INCOMPLETE_NO_IMPLICIT_RETRY") for p in plans),
        unknown_qualification_policy="PRESERVED_BY_FROZEN_NATIVE_NORMALIZER_NOT_REMOVED_OR_PROMOTED",
        qualification_diagnostics=dict(RAW_union_count=len(raw_union), missing_positive_listing_codes=missing,
            future_listing_codes=future, current_roster_positive_outDate_conflicts=conflicts,
            exact_missing_scope=market_complete and official_complete,
            factor_query_codes=factor_codes, factor_scope=factor_scope,
            query_scope_not_eligibility=True),
        qualification_preview=dict(normalizer_calls=0 if factor_scope is None else factor_scope["normalizer_calls"],
            strategy_execution_calls=0),
        theme_context=dict(status="UNKNOWN_NOT_EXECUTED", required_sessions=67,
            additional_market_sessions_deferred=theme[:-25], additional_market_requests_not_issued=84,
            membership_and_theme_computation="NOT_EXECUTED", core_scope_shortened=False))



def _request_constraints(request):
    """Metadata-only bounds of the explicit compact/first-core consumers."""
    captures = request["captures"]
    references, conflicts = {}, []
    for capture in captures:
        for reference in [capture[key] for key in ("result", "raw", "receipt")] + capture.get("producer_files", []):
            previous = references.setdefault(reference["path"], reference)
            if not content_equal(previous, reference):
                conflicts.append(reference["path"])
    size = len(workspace._canonical(request))
    total = sum(reference["bytes"] for reference in references.values())
    violations = []
    if not 1 <= len(captures) <= 256:
        violations.append("CAPTURE_COUNT_MUST_BE_1_TO_256")
    if size > original.MAX_MANIFEST:
        violations.append("MANIFEST_EXCEEDS_2_MIB")
    if any(type(reference["bytes"]) is not int or not 0 <= reference["bytes"] <= 16*MIB
           for reference in references.values()):
        violations.append("INDIVIDUAL_ORIGINAL_EXCEEDS_16_MIB")
    if total > 256*MIB:
        violations.append("UNIQUE_ORIGINALS_EXCEED_256_MIB")
    if conflicts:
        violations.append("CONFLICTING_CONTENT_SOURCE_REFERENCES")
    return dict(compatible=not violations, violations=violations, capture_count=len(captures),
                manifest_bytes=size, unique_original_bytes=total,
                target_consumers="EXPLICIT_COMPACT_OR_FIRST_CORE_NOT_LEGACY_SMALL_PROFILE",
                maximum_captures=256, maximum_manifest_bytes=original.MAX_MANIFEST,
                maximum_individual_original_bytes=16*MIB, maximum_unique_original_bytes=256*MIB)
