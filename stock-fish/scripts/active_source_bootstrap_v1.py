"""Bounded current-source acquisition inside one existing PREMARKET worker.

Planning stays read-only. Missing current facts may admit a finite first source
stage, never a complete-data assertion. The original capture collector, calendar
validator and Budget continue to own data, eligibility and cumulative spending.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
import math
from pathlib import Path

VERSION = "ACTIVE_SOURCE_BOOTSTRAP_V1"
_CALENDAR = ContextVar("stockfish_active_calendar_bootstrap", default=None)
_EMPTY_CACHE = ContextVar("stockfish_readonly_empty_cache", default=None)
_CACHE_DIRS = ("index", "raw", "raw-index", "locators")
_GAPS = frozenset({"AUTHENTICATED_CURRENT_CALENDAR_REQUIRED",
    "ORIGINAL_CORE_SOURCES_NOT_READY", "WHOLE_SOURCE_GRAPH_NOT_AUTHENTICATED"})
_STAGES = frozenset({"CALENDAR", "CURRENT_OFFICIAL", "CORE_MARKET_25",
    "THEME_MARKET_ADDITIONAL_42", "FULL_ANCHORED_FACTORS", "QUALIFICATION",
    "CURRENT_THEME_DIRECTORIES", "WHOLE_CURRENT_THEME_MEMBERS", "OPTIONAL_NEWS"})
_ROWS = frozenset({"AVAILABLE", "CACHED_COMPLETE_UNAUTHENTICATED",
    "RAW_ONLY_INDEX_CANDIDATE_NOT_AUTHENTICATED", "MISSING"})


def need(value, code):
    if not value:
        raise ValueError(code)


def _finite_limits(limits):
    return type(limits) is dict and bool(limits) and all(
        type(value) in (int, float) and math.isfinite(value) and value > 0
        for value in limits.values())


def _empty_snapshot(root):
    need(not root.is_symlink(), "ACTIVE_SOURCE_EMPTY_CACHE_ALIAS")
    if not root.exists():
        return None
    need(root.is_dir(), "ACTIVE_SOURCE_EMPTY_CACHE_NOT_DIRECTORY")
    children = list(root.iterdir())
    need(all(p.name in _CACHE_DIRS and not p.is_symlink() and p.is_dir() and not any(p.iterdir())
             for p in children), "ACTIVE_SOURCE_PARTIAL_CACHE_NOT_EMPTY")
    return {str(p): tuple(getattr(p.stat(), key) for key in ("st_dev", "st_ino", "st_mtime_ns", "st_ctime_ns"))
            for p in [root, *children]}


@contextmanager
def readonly_plan(runtime):
    """A provably empty layout can be inspected without creating directories."""
    state = Path(runtime.profile["state_root"]).absolute()
    need(state.resolve(strict=True) == state and state.is_dir(), "ACTIVE_SOURCE_CANONICAL_STATE_REQUIRED")
    root = state / "cache"
    incomplete = not root.exists() or any(not (root/name).exists() for name in _CACHE_DIRS)
    if not incomplete:
        yield
        return
    before = _empty_snapshot(root)
    need(_EMPTY_CACHE.get() is None, "ACTIVE_SOURCE_NESTED_EMPTY_VIEW")
    token = _EMPTY_CACHE.set(root)
    try:
        yield
    finally:
        _EMPTY_CACHE.reset(token)
        need(_empty_snapshot(root) == before, "ACTIVE_SOURCE_EMPTY_CACHE_CHANGED_DURING_PLAN")


def existing_cache(path):
    from scripts import codex_core_sources as sources
    root = Path(path).absolute()
    if _EMPTY_CACHE.get() == root:
        return root, {"logical_bytes": 0, "allocated_bytes": 0, "regular_files": 0,
            "inventory_scope": "OBSERVED_ABSENT_OR_EMPTY_LAYOUT; NO_DIRECTORY_CREATED"}
    return sources._existing_cache(path)


def virtual_mkdir(path, kwargs):
    root = _EMPTY_CACHE.get()
    path = Path(path).absolute()
    return root is not None and kwargs.get("exist_ok") is True and path in {root, *(root/name for name in _CACHE_DIRS)}


def cache_resolve(path):
    path = Path(path).absolute()
    return path if _EMPTY_CACHE.get() == path else path.resolve(strict=True)


def annotate_preflight(plan, config, source_scope_status):
    """Describe the next permitted stage without altering original admission."""
    result = deepcopy(plan)
    full = result.get("execution_admitted") is True
    blockers = []
    if source_scope_status != "FRESH_EMPTY_WORKSPACE_NO_LEGACY_SOURCE_EFFECTS":
        blockers.append("LEGACY_SOURCE_SCOPES_UNRECONCILED")
    if config.get("capture", {}).get("offline", False):
        blockers.append("OFFLINE_SOURCE_ACQUISITION_FORBIDDEN")
    if config.get("capture", {}).get("retry_failed", False):
        blockers.append("FAILED_SOURCE_RETRY_FORBIDDEN")
    if config.get("seed_sources"):
        blockers.append("SEED_IMPORT_NOT_A_BOOTSTRAP_STAGE")
    if config.get("capture", {}).get("allow_test_fixture", False) and not any(
            part.startswith("TEST_FIXTURE") for part in Path(config.get("state_root", "")).parts):
        blockers.append("TEST_FIXTURE_MUST_USE_ISOLATED_STATE")
    if not _finite_limits(config.get("limits")):
        blockers.append("FINITE_ORIGINAL_LIMITS_REQUIRED")
    if result.get("original_writer_limits") != config.get("limits"):
        blockers.append("ORIGINAL_LIMITS_CHANGED")
    if result.get("capacity", {}).get("allowed") is not True:
        blockers.append("REGISTERED_STORAGE_CAPACITY_BLOCKED")
    for reason in result.get("reasons", []):
        allowed = reason in _GAPS or any(reason == prefix+stage for stage in _STAGES
            for prefix in ("SOURCE_GAPS:", "UNKNOWN_SCOPE:"))
        if not allowed:
            blockers.append(reason)
    stages = result.get("stages")
    if type(stages) is not list or not stages:
        blockers.append("NATIVE_SOURCE_STAGE_INVENTORY_REQUIRED")
    else:
        for stage in stages:
            if stage.get("name") not in _STAGES:
                blockers.append("UNKNOWN_NATIVE_SOURCE_STAGE")
            for row in stage.get("requests", []):
                if row.get("status") not in _ROWS:
                    blockers.append("EXISTING_FAILED_UNKNOWN_OR_NOT_VISIBLE_SOURCE")
    execution_blockers = sorted(set(blockers) & {"LEGACY_SOURCE_SCOPES_UNRECONCILED",
        "FINITE_ORIGINAL_LIMITS_REQUIRED", "ORIGINAL_LIMITS_CHANGED",
        "REGISTERED_STORAGE_CAPACITY_BLOCKED", "TEST_FIXTURE_MUST_USE_ISOLATED_STATE",
        "FAILED_SOURCE_RETRY_FORBIDDEN"})
    admitted = not full and bool(result.get("reasons")) and not blockers
    result.update(source_bootstrap_contract=VERSION, full_workflow_ready=full and not execution_blockers,
        active_execution_blockers=execution_blockers,
        full_workflow_ready_scope="ORIGINAL_FULL_SOURCE_GRAPH_ADMISSION_NOT_DELIVERY",
        source_bootstrap_admitted=admitted, source_bootstrap_blockers=sorted(set(blockers)),
        source_bootstrap_scope="ONE_EXISTING_PREMARKET_CLAIM_AND_WORKER; SAME_CUMULATIVE_BUDGET",
        source_bootstrap_next_stage="AUTHENTICATE_CALENDAR_BEFORE_ANY_OTHER_SOURCE" if admitted else None,
        test_fixture=config.get("capture", {}).get("allow_test_fixture", False),
        source_bootstrap_unresolved_dependencies=deepcopy(result.get("totals", {}).get("unknown_components", [])),
        complete_daily_delivery=False)
    if admitted:
        result["status"] = "ADMITTED_FOR_BOUNDED_SOURCE_BOOTSTRAP"
    elif execution_blockers and not result.get("active_authority_blocker"):
        # Preserve the original authority's terminal admission vocabulary. The
        # new source annotations must not overwrite an existing BLOCKED result.
        result["status"] = "BLOCKED_ACTIVE_EXECUTION"
    return result


def admitted(plan):
    if type(plan) is not dict:
        return False
    if plan.get("source_bootstrap_contract") == VERSION:
        return plan.get("active_execution_blockers") == [] and (
            plan.get("execution_admitted") is True or plan.get("source_bootstrap_admitted") is True
            and plan.get("source_bootstrap_blockers") == [])
    return plan.get("execution_admitted") is True


def sessions_match(plan, observation, target):
    """Unknown calendar does not invent dates; the worker must verify the claim."""
    try:
        canonical = date.fromisoformat(observation).isoformat() == observation and \
            date.fromisoformat(target).isoformat() == target and observation < target
    except (ValueError, TypeError):
        return False
    if not canonical:
        return False
    if plan.get("observation_date") is None and plan.get("target_session") is None:
        return admitted(plan) and plan.get("source_bootstrap_admitted") is True
    return plan.get("observation_date") == observation and plan.get("target_session") == target


def admission_allowed(plan, business):
    return admitted(plan) and sessions_match(plan, business["observation_date"], business["target_session"])


def _binding(cache):
    from scripts import active_business_gateway_v1 as gateway
    value = gateway.current("worker")
    claim = value["claim"]
    need(claim["business"]["operation"] == "PREMARKET", "ACTIVE_SOURCE_PREMARKET_CLAIM_REQUIRED")
    need(gateway._JOURNAL.get() is not None, "ACTIVE_SOURCE_DURABLE_WORKER_JOURNAL_REQUIRED")
    return {"business_id": claim["business_id"], "attempt_id": claim["attempt_id"],
        "cache_id": id(cache), "budget_id": id(cache.budget), "limits": deepcopy(cache.budget.limits)}


def guard_capture(cache, spec):
    """Only the fixed calendar request can precede calendar authentication."""
    from scripts import active_business_gateway_v1 as gateway
    if gateway._CONTROL.get():
        return  # Existing private control setup; never a public source capability.
    value = gateway.current("worker")
    if value["claim"]["business"]["operation"] != "PREMARKET":
        return
    binding = _binding(cache)
    pending = _CALENDAR.get()
    if pending is not None:
        need(pending["binding"] == binding and spec.endpoint == "calendar" and spec.key == pending["request_key"],
             "ACTIVE_SOURCE_ONLY_FIXED_CALENDAR_BEFORE_AUTHENTICATION")
    else:
        need(getattr(cache, "_active_calendar_binding", None) == binding,
             "ACTIVE_SOURCE_AUTHENTICATED_CALENDAR_REQUIRED_BEFORE_CAPTURE")


def bootstrap_capture(cache, now, config=None):
    """Run in the original worker, original capture phase, original Budget."""
    from scripts import active_business_gateway_v1 as gateway
    from scripts import codex_current_calendar_v2 as calendar
    from stockfish_core.yaogu import premarket_capture_cache_v1 as native
    binding = _binding(cache)
    need(_finite_limits(cache.budget.limits), "ACTIVE_SOURCE_FINITE_CUMULATIVE_BUDGET_REQUIRED")
    policy = native._collection_policy(cache, config)
    need(not policy.get("allow_test_fixture", False) or any(
        part.startswith("TEST_FIXTURE") for part in cache.root.parts), "ACTIVE_SOURCE_ISOLATED_TEST_FIXTURE_REQUIRED")
    need(not policy.get("retry_failed", False), "ACTIVE_SOURCE_FAILED_REQUEST_NO_RETRY")
    instant = native._stamp(now)
    spec = calendar._spec({"capture": policy}, str(instant.astimezone(calendar.SH).date()))
    need(_CALENDAR.get() is None, "ACTIVE_SOURCE_NESTED_BOOTSTRAP_FORBIDDEN")
    token = _CALENDAR.set({"binding": binding, "request_key": spec.key})
    journal = gateway._JOURNAL.get()
    try:
        with journal.stage("source_calendar_bootstrap", counters=lambda: cache.budget.metrics):
            record = cache.calendar_reference(instant)
            fresh = record is None
            if fresh:
                record = cache.capture_one(spec)
            verified_at = datetime.now(timezone.utc)
            need(verified_at.astimezone(calendar.SH).date() == instant.astimezone(calendar.SH).date(),
                 "ACTIVE_SOURCE_BOOTSTRAP_BUSINESS_DAY_CHANGED")
            gate = calendar.validate_calendar(cache, record, now=verified_at,
                exact_spec=spec if fresh else None)
            gateway.session_result(gate)  # Compare to the unchanged claimed dates.
            # The unchanged collector still receives its original instant.
            # Crossing the 18:00 release boundary must stop before other sources.
            calendar.workflow._latest_gate(cache.verify(record)["rows"],
                gate["observation_date"], gate["target_session"], instant)
            cache.budget.check("active_calendar_authenticated", force=True)
            need(_binding(cache) == binding, "ACTIVE_SOURCE_CUMULATIVE_BUDGET_OR_CLAIM_CHANGED")
            cache._active_calendar_binding = binding
            journal.record("METRICS_SNAPSHOT", counters=lambda: cache.budget.metrics,
                details={"source_stage": "CALENDAR_AUTHENTICATED", "calendar_reused": not fresh,
                    "calendar_request_key": record.to_dict()["request_key"], "source_authority_restored": False,
                    "complete_daily_delivery": False})
    finally:
        _CALENDAR.reset(token)


def collection_result(cache, result):
    """A fixture option taints the complete collection, never real admission."""
    if cache.config.get("allow_test_fixture", False):
        need(any(part.startswith("TEST_FIXTURE") for part in cache.root.parts),
             "ACTIVE_SOURCE_ISOLATED_TEST_FIXTURE_REQUIRED")
        return replace(result, coverage_type="TEST_FIXTURE")
    return result
