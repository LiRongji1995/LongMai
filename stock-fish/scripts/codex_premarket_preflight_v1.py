"""Read-only whole-premarket admission, including costs behind early gaps.

The independent source-inspection budget grants no writer authority. Index
facts describe cached claims until their originals have been authenticated.
Missing current parents never turn an older universe into today's exact scope.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_core_sources as sources
from scripts import codex_core as original

VERSION = "CODEX_PREMARKET_PREFLIGHT_V1"


def _need(value, reason):
    if not value:
        raise ValueError(reason)


def request_cost(spec, http_attempts):
    """Original adapter reservations, not measured transport or elapsed time."""
    from stockfish_core.yaogu.provider_requests import ENDPOINTS, validate_request
    _need(type(http_attempts) is int and http_attempts > 0, "PREFLIGHT_HTTP_POLICY_REQUIRED")
    endpoint, parameters = spec["endpoint"], spec["parameters"]
    _need(endpoint in ENDPOINTS, "PREFLIGHT_UNSUPPORTED_ENDPOINT")
    parameters = validate_request(endpoint, parameters)
    sdk = 0
    if ENDPOINTS[endpoint]["provider"] == "baostock":
        sdk = len(parameters["codes"])+2 if endpoint in (
            "adjustment_factors", "stock_basic", "history", "history_adjusted") else 3
    return dict(requests=1, sdk_reserved=sdk, http_reserved=0 if sdk else http_attempts)


def _cost(rows, http_attempts):
    value = dict(requests=0, sdk_reserved=0, http_reserved=0)
    for row in rows:
        if row["status"] != "MISSING":
            continue
        for name, number in request_cost(row["request_spec"], http_attempts).items():
            value[name] += number
    return value


def _metadata(index, spec, raw_entries=(), allow_raw=False):
    from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec, _stamp
    candidates = index.by_key.get(spec.key, ())
    visible = []
    for value in candidates:
        _need(RequestSpec(**value["request_spec"]).key == spec.key,
              "PREFLIGHT_INDEX_REQUEST_IDENTITY_CHANGED")
        if max(_stamp(value["first_seen_at"]), _stamp(value["completed_at"])) <= index.instant:
            visible.append(value)
    row = dict(request_key=spec.key, request_spec=spec.to_dict(),
               authentication="INDEX_ONLY_NOT_SOURCE_AUTHORITY", receipt_id=None)
    if visible:
        latest = max(_stamp(value["completed_at"]) for value in visible)
        tied = [value for value in visible if _stamp(value["completed_at"]) == latest]
        _need(len({value["receipt_id"] for value in tied}) == 1, "PREFLIGHT_SAME_CLOCK_CONFLICT")
        value = tied[0]
        row.update(status="CACHED_COMPLETE_UNAUTHENTICATED" if value["status"] == "COMPLETE"
                   and value["pagination_complete"] else "INCOMPLETE_NO_IMPLICIT_RETRY",
                   receipt_id=value["receipt_id"])
        return row
    recipes = [value for value in raw_entries if value["request_key"] == spec.key]
    if allow_raw and recipes:
        row.update(status="RAW_ONLY_INDEX_CANDIDATE_NOT_AUTHENTICATED",
                   raw_recipe_count=len(recipes))
        return row
    attempt = index.cache.root / "raw" / spec.key
    _need(not attempt.is_symlink(), "PREFLIGHT_ATTEMPT_LINK_FORBIDDEN")
    row["status"] = "NOT_VISIBLE_AT_CUTOFF" if candidates else (
        "UNFINISHED_NO_IMPLICIT_RETRY" if attempt.exists() and any(attempt.iterdir()) else "MISSING")
    return row


def _factor_inventory(index, observed):
    """Conditional old-universe cost is explicitly separate from new scope."""
    from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec, _stamp
    groups = {}
    for value in index.entries:
        if value["endpoint"] != "adjustment_factors":
            continue
        spec = RequestSpec(**value["request_spec"])
        _need(spec.key == value["request_key"], "PREFLIGHT_FACTOR_REQUEST_IDENTITY_CHANGED")
        if max(_stamp(value["first_seen_at"]), _stamp(value["completed_at"])) > index.instant:
            continue
        params = spec.parameters
        key = (params["start_date"], params["end_date"])
        group = groups.setdefault(key, dict(codes=set(), batches=set(), statuses=Counter()))
        group["statuses"][value["status"]] += 1
        if value["status"] == "COMPLETE" and value["pagination_complete"]:
            group["codes"].update(params["codes"])
            group["batches"].add(spec.key)
    rows = [dict(start_date=start, end_date=end, complete_index_security_count=len(group["codes"]),
                 complete_index_batch_count=len(group["batches"]), statuses=dict(group["statuses"]))
            for (start, end), group in sorted(groups.items())]
    return dict(authentication="INDEX_ONLY_NOT_SOURCE_AUTHORITY", windows=rows,
                required_start_date="1990-01-01", required_end_date=observed,
                historical_revision_outside_tail="UNKNOWN_NO_UPSTREAM_REVISION_PROOF",
                tail_composition_allowed=False,
                shortening_date_range_reduces_sdk_calls=False)


def _scope(index, config, writer_limits, raw_entries):
    from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec, lhb_request_spec
    calendar, result = index.calendar()
    if calendar is None:
        local = index.instant.astimezone(sources.SH).date()
        back, ahead = int(config.get("calendar_lookback_days", 800)), int(config.get("calendar_lookahead_days", 32))
        _need(90 <= back <= 1500 and 1 <= ahead <= 366, "PREFLIGHT_FINITE_CALENDAR_WINDOW_REQUIRED")
        spec = RequestSpec("calendar", dict(start_date=str(local-timedelta(days=back)),
            end_date=str(local+timedelta(days=ahead))), str(local), role="CALENDAR")
        rows = [_metadata(index, spec)]
        stage = dict(name="CALENDAR", requests=rows,
            unknown_dependencies=["AUTHENTICATED_CALENDAR_REQUIRED_FOR_ALL_DOWNSTREAM_SCOPE"],
            cost=_cost(rows, writer_limits["request_http_attempts"]),
            cost_scope="KNOWN_MISSING_REQUESTS_ONLY", authentication="INDEX_ONLY_NOT_SOURCE_AUTHORITY")
        return dict(observation_date=None, target_session=None, stages=[stage],
                    reasons=["AUTHENTICATED_CURRENT_CALENDAR_REQUIRED"],
                    source_plan=None, factor_inventory=_factor_inventory(index, None),
                    core_sessions=[], theme_sessions=[], calendar=None)
    observed, target, price, theme, gate = sources._calendar(result, index.instant)
    local = str(index.instant.astimezone(sources.SH).date())
    stages = []

    def stage(name, specs, *, allow_raw=False, unknown=()):
        rows = [_metadata(index, spec, raw_entries, allow_raw) for spec in specs]
        value = dict(name=name, requests=rows, unknown_dependencies=list(unknown),
                     cost=_cost(rows, writer_limits["request_http_attempts"]),
                     cost_scope="KNOWN_MISSING_REQUESTS_ONLY",
                     authentication="INDEX_ONLY_UNLESS_ORIGINAL_SOURCE_PLAN_CONFIRMS")
        stages.append(value)
        return value

    stage("CURRENT_OFFICIAL", [RequestSpec(ep, {}, local, role="CURRENT_OFFICIAL", current=True)
          for ep in ("mainboard_sse", "master_szse", "delisting_sse")])
    stage("CORE_MARKET_25", [RequestSpec(ep, dict(date=day), day, role="FULL_MARKET_CONTEXT")
          for day in price for ep in ("market_daily", "master_all")])
    stage("THEME_MARKET_ADDITIONAL_42", [RequestSpec(ep, dict(date=day), day, role="FULL_MARKET_CONTEXT")
          for day in theme[:-25] for ep in ("market_daily", "master_all")], allow_raw=True)
    lhb_spec = lhb_request_spec(price)
    lhb_stage = stage("PRESCREEN_LHB", [lhb_spec])
    lhb_blockers = index.cache.lhb_overlap_blockers(lhb_spec, entries=index.entries)
    if lhb_blockers:
        lhb_stage['requests'][0].update(status='OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY',
                                       overlapping_prior_requests=lhb_blockers)
        lhb_stage['cost'] = _cost(lhb_stage['requests'], writer_limits['request_http_attempts'])
    # The original planner authenticates only what it actually reaches. Its
    # early return is retained, but does not hide the index gaps above/below.
    core = sources._collect(index, config, sources.LEGACY_IDENTITY)
    captures = core.pop("captures")
    if core.get("remaining_core_dependencies") != "NOT_EVALUATED":
        constraints = sources._request_constraints(dict(schema_version=original.INPUT_VERSION,
            captures=captures, observation_date=observed, target_session=target,
            cutoff=index.instant.isoformat(), coverage_type="TEST_FIXTURE" if index.fixture else "PROSPECTIVE_CAPTURED"))
        core["native_request_constraints"] = constraints
        if not constraints["compatible"]:
            core["core_ready_to_run"] = False
    known = {row["request_key"]: row for row in core["requests"]}
    for value in stages:
        for row in value["requests"]:
            original_row = known.get(row["request_key"])
            if original_row and original_row["status"] == "AVAILABLE":
                row.update(status="AVAILABLE", authentication="ORIGINAL_SOURCE_AUTHENTICATED")
    factor_specs = [RequestSpec(**row["request_spec"]) for row in core["requests"]
                    if row["request_spec"]["endpoint"] == "adjustment_factors"]
    factors = stage("FULL_ANCHORED_FACTORS", factor_specs,
                    unknown=() if factor_specs else ("CURRENT_MASTER_AND_OFFICIAL_REQUIRED_FOR_EXACT_SECURITY_SCOPE",))
    basics = [RequestSpec(**row["request_spec"]) for row in core["requests"]
              if row["request_spec"]["endpoint"] in ("stock_basic", "stock_basic_all")]
    stage("QUALIFICATION", basics, unknown=() if core.get("remaining_core_dependencies") == "EVALUATED"
          else ("CURRENT_RAW_UNION_AND_OFFICIAL_REQUIRED_FOR_EXACT_LISTING_GAPS",))
    inventory = _factor_inventory(index, observed)
    previous = [row for row in inventory["windows"] if row["start_date"] == "1990-01-01"
                and row["end_date"] < observed and row["complete_index_security_count"]]
    if not factor_specs and previous:
        prior = max(previous, key=lambda row: row["end_date"])
        batch_size = int(config.get("batch_size", 50))
        _need(1 <= batch_size <= 100, "PREFLIGHT_FACTOR_BATCH_SIZE_BOUND")
        count = prior["complete_index_security_count"]
        batches = (count+batch_size-1)//batch_size
        factors["conditional_prior_universe_estimate"] = dict(
            condition="ONLY_IF_CURRENT_AUTHENTICATED_SCOPE_EQUALS_PRIOR_INDEX_SCOPE",
            authentication="INDEX_ONLY_NOT_CURRENT_SECURITY_SCOPE", prior_end_date=prior["end_date"],
            security_count=count, requests=batches, sdk_reserved=count+2*batches,
            included_in_known_totals=False)
    families = []
    if config.get("include_industries", True):
        families.append(("industry_list_sina", "industry_members_sina", r"hangye_[A-Za-z0-9_]+"))
    if config.get("include_concepts", True):
        families.append(("concept_list_sina", "concept_members_sina", r"gn_[A-Za-z0-9_]+"))
    _need(families, "PREFLIGHT_EXPLICIT_THEME_FAMILY_REQUIRED")
    dirs = stage("CURRENT_THEME_DIRECTORIES", [RequestSpec(ep, {}, local,
        role="CURRENT_THEME_DIRECTORY", current=True) for ep, _, _ in families])
    member_specs, member_unknown = [], []
    for directory, (endpoint, member, pattern) in zip(dirs["requests"], families):
        if directory["status"] != "CACHED_COMPLETE_UNAUTHENTICATED":
            member_unknown.append("COMPLETE_"+endpoint.upper()+"_REQUIRED_FOR_MEMBER_SCOPE")
            continue
        metadata, decoded, status = index.lookup(RequestSpec(**directory["request_spec"]))
        if status != "AVAILABLE":
            member_unknown.append("COMPLETE_"+endpoint.upper()+"_REQUIRED_FOR_MEMBER_SCOPE")
            continue
        directory.update(status="AVAILABLE", authentication="ORIGINAL_SOURCE_AUTHENTICATED")
        nodes = []
        for row in decoded["rows"]:
            node = row.get("label") or row.get("板块代码")
            _need(type(node) is str and re.fullmatch(pattern, node, re.ASCII) and node not in nodes,
                  "PREFLIGHT_THEME_DIRECTORY_NODE_INVALID_OR_DUPLICATE")
            nodes.append(node)
        _need(nodes, "PREFLIGHT_COMPLETE_THEME_DIRECTORY_EMPTY")
        member_specs.extend(RequestSpec(member, dict(symbol=node), local,
                            role="WHOLE_CURRENT_THEME_MEMBERS", current=True) for node in nodes)
    _need(len(member_specs) <= config.get("max_theme_nodes", 2048), "PREFLIGHT_THEME_NODE_BOUND_NO_TRUNCATION")
    stage("WHOLE_CURRENT_THEME_MEMBERS", member_specs, unknown=member_unknown)
    # News is optional in the original workflow. Its presence is reported by
    # the caller; there is no attempt to claim its cost from a missing result.
    reasons = []
    if not core.get("core_ready_to_run"):
        reasons.append("ORIGINAL_CORE_SOURCES_NOT_READY")
    for value in stages:
        for row in value["requests"]:
            if row["request_key"] in known and known[row["request_key"]]["status"] == "AVAILABLE":
                row.update(status="AVAILABLE", authentication="ORIGINAL_SOURCE_AUTHENTICATED")
        if value["unknown_dependencies"]:
            reasons.append("UNKNOWN_SCOPE:"+value["name"])
        if any(row["status"] not in ("AVAILABLE", "CACHED_COMPLETE_UNAUTHENTICATED",
                                     "RAW_ONLY_INDEX_CANDIDATE_NOT_AUTHENTICATED") for row in value["requests"]):
            reasons.append("SOURCE_GAPS:"+value["name"])
    return dict(observation_date=observed, target_session=target, calendar_currentness=gate,
                core_sessions=price, theme_sessions=theme, calendar=calendar,
                stages=stages, reasons=reasons, source_plan=core, factor_inventory=inventory)


def _collection_reasons(result, configuration, reasons, *, calendar_available, authenticated=False):
    """Separate bounded missing-source permission from complete-source readiness.

    Unknown scope is admitted only when an explicitly missing parent explains
    it. Every source already present must be authenticated before admission.
    The original collector still reserves each newly discovered exact stage.
    """
    stages = {stage["name"]: stage for stage in result["stages"]}
    permitted_stages = {"CURRENT_OFFICIAL", "CORE_MARKET_25", "THEME_MARKET_ADDITIONAL_42",
        "FULL_ANCHORED_FACTORS", "QUALIFICATION", "CURRENT_THEME_DIRECTORIES",
        "WHOLE_CURRENT_THEME_MEMBERS", "PRESCREEN_LHB"}
    permitted_reasons = {"ORIGINAL_CORE_SOURCES_NOT_READY", "WHOLE_SOURCE_GRAPH_NOT_AUTHENTICATED"}
    permitted_reasons.update(prefix+stage for prefix in ("SOURCE_GAPS:", "UNKNOWN_SCOPE:")
                             for stage in permitted_stages)
    blocked = [reason for reason in reasons if reason not in permitted_reasons]
    capture = configuration.get("capture", {})
    if not calendar_available:
        blocked.append("COLLECTION_AUTHENTICATED_CALENDAR_REQUIRED")
    if capture.get("offline", False):
        blocked.append("COLLECTION_ONLINE_POLICY_REQUIRED")
    if capture.get("retry_failed", False):
        blocked.append("COLLECTION_RETRY_POLICY_FORBIDDEN")
    if configuration["seed_sources"]:
        blocked.append("COLLECTION_SEED_IMPORT_NOT_ADMITTED")

    def missing(stage, endpoint=None, session=None):
        return any(row["status"] == "MISSING"
            and (endpoint is None or row["request_spec"]["endpoint"] == endpoint)
            and (session is None or row["request_spec"]["business_session"] == session)
            for row in stages.get(stage, {}).get("requests", []))

    current_missing = missing("CURRENT_OFFICIAL")
    market_missing = missing("CORE_MARKET_25")
    latest_missing = missing("CORE_MARKET_25", session=result.get("observation_date"))
    factor_parent_missing = current_missing or latest_missing
    allowed_unknown = {
        ("FULL_ANCHORED_FACTORS", "CURRENT_MASTER_AND_OFFICIAL_REQUIRED_FOR_EXACT_SECURITY_SCOPE"): factor_parent_missing,
        ("QUALIFICATION", "CURRENT_RAW_UNION_AND_OFFICIAL_REQUIRED_FOR_EXACT_LISTING_GAPS"): current_missing or market_missing,
    }
    for endpoint in ("industry_list_sina", "concept_list_sina"):
        allowed_unknown[("WHOLE_CURRENT_THEME_MEMBERS", "COMPLETE_"+endpoint.upper()+"_REQUIRED_FOR_MEMBER_SCOPE")] = missing("CURRENT_THEME_DIRECTORIES", endpoint)
    allowed_statuses = {"AVAILABLE", "MISSING"}
    if not authenticated:
        allowed_statuses.update({"CACHED_COMPLETE_UNAUTHENTICATED", "RAW_ONLY_INDEX_CANDIDATE_NOT_AUTHENTICATED"})
    for stage in result["stages"]:
        if stage["name"] not in permitted_stages | {"CALENDAR", "OPTIONAL_NEWS"}:
            blocked.append("COLLECTION_UNSUPPORTED_STAGE:"+stage["name"])
        for row in stage["requests"]:
            if row["status"] not in allowed_statuses:
                reason = ("COLLECTION_SOURCE_NOT_RETRYABLE:" if row["status"] in {
                    "INCOMPLETE_NO_IMPLICIT_RETRY", "UNFINISHED_NO_IMPLICIT_RETRY", "NOT_VISIBLE_AT_CUTOFF",
                    "OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY"}
                    else "COLLECTION_EXISTING_SOURCE_NOT_AUTHENTICATED:")
                blocked.append(reason+row["request_key"])
        for dependency in stage["unknown_dependencies"]:
            if not allowed_unknown.get((stage["name"], dependency), False):
                blocked.append("COLLECTION_UNEXPLAINED_SCOPE:"+stage["name"])
    source = result.get("source_plan") or {}
    permitted_dependencies = {
        "OLDER_24_SESSION_ORIGINALS_NOT_EVALUATED": latest_missing or current_missing,
        "LISTING_AND_FULL_FACTOR_SCOPE_NOT_EVALUATED": latest_missing or current_missing,
        "COMPLETE_MARKET_AND_OFFICIAL_ORIGINALS_REQUIRED_FOR_EXACT_LISTING_GAPS": market_missing or current_missing,
        "CURRENT_MASTER_AND_OFFICIAL_ORIGINALS_REQUIRED_FOR_EXACT_FACTOR_SCOPE": factor_parent_missing,
        "BASIC_FALLBACK_SCOPE_AWAITS_CURRENT_BASIC_ALL_RESULT": missing("QUALIFICATION", "stock_basic_all"),
    }
    for dependency in source.get("dependencies", []):
        if not permitted_dependencies.get(dependency, False):
            blocked.append("COLLECTION_UNEXPLAINED_CORE_DEPENDENCY:"+dependency)
    qualification = source.get("qualification_diagnostics") or {}
    if qualification.get("future_listing_codes") or qualification.get("current_roster_positive_outDate_conflicts"):
        blocked.append("COLLECTION_EXISTING_QUALIFICATION_CONFLICT")
    if qualification.get("missing_positive_listing_codes") and not (
            current_missing or market_missing or missing("QUALIFICATION")):
        blocked.append("COLLECTION_UNRESOLVED_POSITIVE_LISTING")
    constraints = source.get("native_request_constraints")
    if constraints is not None and not constraints.get("compatible", False):
        blocked.append("COLLECTION_EXISTING_SOURCE_CONSTRAINTS_INCOMPATIBLE")
    return sorted(set(blocked))


def plan(runtime, *, allow_test_fixture=False, _now=None, collect_missing=False):
    """Never collect, create files, retry, run a strategy, or expand its limits."""
    from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache, RequestSpec, _config, _stamp
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget
    from stockfish_core.yaogu.premarket_workflow_v1 import _latest_gate, load_config
    _need(runtime.profile.get("backend") == "premarket", "PREFLIGHT_REGISTERED_PREMARKET_REQUIRED")
    _need(_now is None or allow_test_fixture, "PREFLIGHT_REAL_CLOCK_OVERRIDE_FORBIDDEN")
    _need(type(collect_missing) is bool, "PREFLIGHT_EXPLICIT_COLLECTION_MODE_REQUIRED")
    runtime.check_binding()
    prepared = original.premarket._prepared(runtime.profile)
    _need(prepared["source"] == ROOT and Path(sys.executable).absolute() == prepared["python"],
          "PREFLIGHT_REGISTERED_SOURCE_AND_PYTHON_REQUIRED")
    before = original._snapshot(runtime.profile)
    implementations = [original._reference(Path(path).resolve())
                       for path in (__file__, sources.__file__)]
    configuration = load_config(prepared["config"])
    _config(configuration.get("capture", {}))
    writer = dict(configuration["limits"])
    root, inventory = sources._existing_cache(prepared["state"] / "cache")
    diagnostic = dict(sources.LIMITS)
    diagnostic["cache_bytes"] = max(diagnostic["cache_bytes"], writer["cache_bytes"])
    _need(inventory["logical_bytes"] <= diagnostic["cache_bytes"], "PREFLIGHT_READONLY_CACHE_LIMIT_EXCEEDED")
    # This planner has no writer or network executor. Scan the complete cache
    # at entry and again before returning admission, instead of recursively
    # scanning it during each interval of source reads. Per-read content,
    # time, RSS and byte limits remain active; writer Budgets are unchanged.
    budget = Budget(diagnostic)
    now = _now or datetime.now(timezone.utc)
    _need(now.tzinfo is not None and now.utcoffset() is not None, "PREFLIGHT_AWARE_CLOCK_REQUIRED")
    options = dict(configuration.get("capture", {}), offline=True, retry_failed=False,
        allow_test_fixture=allow_test_fixture, request_timeout_seconds=diagnostic["request_timeout_seconds"],
        request_http_attempts=diagnostic["request_http_attempts"],
        max_http_requests_per_request=diagnostic["request_http_attempts"])
    # All directories were required to exist above, so the original constructor
    # cannot create any cache directory. No writer or runtime journal is opened.
    with budget.phase("capture"), budget.meter_files():
        cache = CaptureCache(root, budget, config=options, executor=sources._no_network)
        index = sources._Index(cache, now, allow_test_fixture)
        raw_entries = list(cache._entries(raw_only=True))
        result = _scope(index, options, writer, raw_entries)
        reasons = result.pop("reasons")
        calendar = result.pop("calendar")
        if inventory["logical_bytes"] > writer["cache_bytes"]:
            reasons.append("ORIGINAL_WRITER_CACHE_LIMIT_EXCEEDED")
        unknown = [dict(stage=value["name"], reason=reason) for value in result["stages"]
                   for reason in value["unknown_dependencies"]]
        rows = {row["request_key"]: row for value in result["stages"] for row in value["requests"]}
        totals = _cost(rows.values(), writer["request_http_attempts"])
        for key, limit in (("requests", "max_requests"), ("sdk_reserved", "max_sdk_calls"),
                           ("http_reserved", "max_http_attempts")):
            if totals[key] > writer[limit]:
                reasons.append("KNOWN_REQUEST_COST_EXCEEDS_ORIGINAL_LIMIT:"+limit)
        from stockfish_core.yaogu.premarket_news_v1 import DEFAULTS as NEWS_DEFAULTS
        news = dict(NEWS_DEFAULTS, **configuration.get("news", {}))
        _need(set(news) == set(NEWS_DEFAULTS), "PREFLIGHT_UNKNOWN_NEWS_CONFIGURATION")
        for name, low, high in (("max_gets", 1, 10), ("max_articles", 0, 8),
                                ("max_redirects", 0, 2), ("max_body_bytes", 1, 2*1024**2)):
            _need(type(news[name]) is int and low <= news[name] <= high,
                  "PREFLIGHT_BOUNDED_NEWS_CONFIGURATION_REQUIRED:"+name)
        configured_timeout = configuration.get("news", {}).get("request_timeout_seconds",
            min(90, writer["request_timeout_seconds"]))
        _need(type(configured_timeout) in (int, float) and 0 < configured_timeout <= min(90, writer["request_timeout_seconds"]),
              "PREFLIGHT_NEWS_TIMEOUT_OUTSIDE_ORIGINAL_LIMIT")
        news_calls = 0 if configuration.get("capture", {}).get("offline", False) else news["max_gets"]
        result["stages"].append(dict(name="OPTIONAL_NEWS", requests=[], unknown_dependencies=[],
            cost=dict(requests=news_calls, sdk_reserved=0, http_reserved=news_calls),
            cost_scope="BOUNDED_UPPER_RESERVATION_INCLUDING_REDIRECTS", authentication="NOT_SOURCE_CAPABILITY",
            result_if_offline="UNKNOWN_NOT_COLLECTED", offline=not bool(news_calls)))
        totals["requests"] += news_calls
        totals["http_reserved"] += news_calls
        for key, limit in (("requests", "max_requests"), ("http_reserved", "max_http_attempts")):
            if totals[key] > writer[limit]:
                reasons.append("KNOWN_REQUEST_COST_EXCEEDS_ORIGINAL_LIMIT:"+limit)
        if configuration["seed_sources"]:
            unknown.append(dict(stage="SEED_IMPORT", reason="SEED_IMPORT_WRITE_SCOPE_NOT_INSPECTED"))
            reasons.append("SEED_IMPORT_WRITE_SCOPE_UNKNOWN")
        growth = max(runtime.profile["storage"]["expected_growth_bytes"], writer["output_bytes"])
        capacity = runtime.preflight(expected_growth_bytes=growth)
        if not capacity["allowed"]:
            reasons.append("REGISTERED_STORAGE_CAPACITY_BLOCKED")
        # Ordinary readiness remains cheap when blocked. Explicit collection
        # planning authenticates every present dependency before allowing only
        # genuine missing requests; it still performs no network or writes.
        collection_reasons = (_collection_reasons(result, configuration, reasons,
            calendar_available=calendar is not None) if collect_missing else
            ["EXPLICIT_MISSING_COLLECTION_PLAN_REQUIRED"])
        if not reasons or not collection_reasons:
            for row in rows.values():
                if row["status"] == "MISSING":
                    continue
                if row["status"] == "RAW_ONLY_INDEX_CANDIDATE_NOT_AUTHENTICATED":
                    recipe = cache.context_recipe(row["request_spec"]["endpoint"], row["request_spec"]["business_session"])
                    _need(recipe is not None, "PREFLIGHT_RAW_RECIPE_DISAPPEARED")
                    row.update(status="AVAILABLE", authentication="ORIGINAL_RAW_RECEIPT_AUTHENTICATED")
                elif row["status"] != "AVAILABLE":
                    _, _, status = index.lookup(RequestSpec(**row["request_spec"]))
                    _need(status == "AVAILABLE", "PREFLIGHT_ORIGINAL_SOURCE_NOT_COMPLETE")
                    row.update(status="AVAILABLE", authentication="ORIGINAL_SOURCE_AUTHENTICATED")
            if collect_missing:
                # The collector consults all visible complete prior listing
                # references even when current market/official parents are new.
                # Authenticate these before allowing any missing request.
                for value in index.entries:
                    if (value["endpoint"] in ("stock_basic", "stock_basic_all")
                            and value["status"] == "COMPLETE" and value["pagination_complete"]
                            and _stamp(value["completed_at"]) <= index.instant):
                        index.verify(value)
        unresolved = sum(row["status"] != "AVAILABLE" for row in rows.values())
        if unresolved:
            reasons.append("WHOLE_SOURCE_GRAPH_NOT_AUTHENTICATED")
        cache.terminal()
        _need(before == original._snapshot(runtime.profile)
              and implementations == [original._reference(Path(row["path"])) for row in implementations],
              "PREFLIGHT_CONTROL_OR_IMPLEMENTATION_CHANGED")
        runtime.check_binding()
        _, terminal_inventory = sources._existing_cache(root)
        _need(terminal_inventory["logical_bytes"] <= diagnostic["cache_bytes"],
              "PREFLIGHT_READONLY_CACHE_LIMIT_EXCEEDED")
        if terminal_inventory["logical_bytes"] > writer["cache_bytes"]:
            reasons.append("ORIGINAL_WRITER_CACHE_LIMIT_EXCEEDED")
        capacity = runtime.preflight(expected_growth_bytes=growth)
        if not capacity["allowed"]:
            reasons.append("REGISTERED_STORAGE_CAPACITY_BLOCKED")
        runtime.check_binding()
        if collect_missing:
            collection_reasons = _collection_reasons(result, configuration, reasons,
                calendar_available=calendar is not None, authenticated=True)
        terminal_now = _now or datetime.now(timezone.utc)
        _need(terminal_now.astimezone(sources.SH).date() == now.astimezone(sources.SH).date(),
              "PREFLIGHT_CURRENT_SNAPSHOT_DAY_CHANGED")
        if calendar is not None:
            _latest_gate(index.verify(calendar)["rows"], result["observation_date"], result["target_session"],
                         terminal_now)
        budget.check("whole_preflight_return", force=True)
        lhb_rows = [row for stage in result["stages"] if stage["name"] == "PRESCREEN_LHB"
                    for row in stage["requests"]]
        result["prescreen_plan"] = dict(status="NOT_EVALUATED_READONLY_SOURCE_PLAN",
            candidate_count=None, candidate_ids=None, decisions_computed=False,
            window_sessions=result["core_sessions"][-5:] if calendar is not None else None,
            source_status=lhb_rows[0]["status"] if lhb_rows else "UNKNOWN_CALENDAR_REQUIRED",
            request_key=lhb_rows[0]["request_key"] if lhb_rows else None,
            scope="SUPPLEMENTAL_DEEP_RESEARCH_ROUTING_ONLY",
            full_pool_factor_scope_preserved=True, missing_source_is_not_empty=True)
        result.update(schema_version=VERSION, planned_at=now.isoformat(),
            status="ADMITTED_FOR_BOUNDED_EXECUTION" if not reasons else "BLOCKED",
            execution_admitted=not reasons, reasons=sorted(set(reasons)),
            collection_admitted=collect_missing and not collection_reasons,
            collection_reasons=collection_reasons, explicit_missing_collection_plan=collect_missing,
            collection_policy="ORIGINAL_COLLECTOR_PER_STAGE_ADMISSION_NO_RETRY",
            original_writer_limits=writer, cache_inventory=terminal_inventory,
            cache_inventory_at_entry=inventory, capacity=capacity,
            diagnostic_cache_observation="ENTRY_AND_TERMINAL_FULL_INVENTORY_READONLY",
            diagnostic_cache_ceiling_source="MAX_FIXED_DIAGNOSTIC_AND_REGISTERED_WRITER_LIMIT",
            read_only_diagnostic_limits=diagnostic, diagnostic_limits_authorize_writer=False,
            totals={**totals, "scope":"KNOWN_MISSING_PLUS_BOUNDED_OPTIONAL_NEWS", "unknown_components":unknown,
                    "total_exact":not unknown and not news_calls and all(row["status"] in ("AVAILABLE", "MISSING") for row in rows.values()),
                    "network_transfer_bytes":None, "elapsed_seconds_estimate":None},
            source_authentication=dict(authenticated_original_count=len(index.checked),
                unresolved_graph_requests=unresolved, index_count=len(index.entries), raw_index_count=len(raw_entries)),
            source_identity_policy=sources.LEGACY_IDENTITY,
            full_premarket_ready=False, source_capability_issued=False, network_calls=0,
            strategy_calls=0, database_created=False, added_cache_logical_bytes=0,
            metrics=budget.metrics)
        return result
