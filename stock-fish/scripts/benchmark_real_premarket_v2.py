"""Explicit V2 experiment: whole offline workers retaining identical source unknowns.

A successfully sealed, current, full original daily job is mandatory before any
arm starts. It is admission evidence, not an online timing reference. Every arm
retains native mathematics, sources, clocks and resource limits. Only explicit
source pagination gaps and native offline news UNKNOWN are admitted. V1 remains
unchanged. No result establishes end-to-end or full-market-source speedup.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from types import FunctionType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import benchmark_real_premarket_v1 as v1
from scripts import codex_premarket_shared_v1 as shared
from stockfish_core.yaogu import premarket_workflow_v1 as original
from stockfish_core.yaogu.premarket_runtime_v1 import JobLock, json_bytes, strict_json

VERSION = "REAL_CACHE_ONLY_PREMARKET_BENCHMARK_V2"
ENGINES, SUCCESS, STRATEGY = v1.ENGINES, v1.SUCCESS, v1.STRATEGY
CSV_NAMES, FAILURE_RECEIPT_BYTES = v1.CSV_NAMES, v1.FAILURE_RECEIPT_BYTES
COMPARISON_KEYS = (*v1.COMPARISON_KEYS, "theme_acquisition", "offline_news")
BASELINE_KEYS = tuple(key for key in COMPARISON_KEYS if key not in ("phases", "offline_news"))
MAX_EXPERIMENT_BYTES = 2 * 1024**3
SCOPE = "WHOLE_OFFLINE_NATIVE_WORKER_WITH_IDENTICAL_RETAINED_SOURCE_UNKNOWNS; NOT_ONLINE_OR_GATEWAY_OR_FULL_MARKET_SOURCE_COMPLETENESS"


def _need(value, reason):
    if not value:
        raise ValueError(VERSION + ":" + reason)


def _bind(function, bindings=None):
    # Independent function globals bind actual child entry and all failure
    # receipts to V2. No mutation of V1's module or its strict proof assertions.
    scope = globals() if bindings is None else {**globals(), **bindings}
    result = FunctionType(function.__code__, scope, function.__name__,
                          function.__defaults__, function.__closure__)
    result.__kwdefaults__ = function.__kwdefaults__
    return result


# Resource/process control is the reviewed V1 implementation, including the
# reserved failure receipts, inode ownership, last-rename check and owned reap.
for _name in ("_save", "_receipt_paths", "_arm_output_bytes", "_check_parent_budget",
              "_reserve_failure_receipts"):
    globals()[_name] = _bind(getattr(v1, _name))
_digest, _rss, _clock = v1._digest, v1._rss, v1._clock
_document, _read_reference = v1._document, v1._read_reference
_market_projection, _csv_values, _numeric_leaves = v1._market_projection, v1._csv_values, v1._numeric_leaves
_offline_configuration, _instrumented = v1._offline_configuration, v1._instrumented


def _references(*values):
    """Collect every physical reference, including non-potential theme nodes."""
    found = {}
    def visit(value):
        if isinstance(value, dict):
            if {"path", "sha256", "bytes", "fp"} <= set(value):
                _need(set(value) == {"path", "sha256", "bytes", "fp"}, "PHYSICAL_REFERENCE_SCHEMA")
                path = Path(value["path"])
                _need(path.is_absolute() and path.resolve(strict=True) == path,
                      "UNALIASED_PHYSICAL_REFERENCE_REQUIRED")
                prior = found.setdefault(str(path), value)
                _need(prior == value, "PHYSICAL_REFERENCE_CONFLICT")
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
    for value in values:
        visit(value)
    return [found[path] for path in sorted(found)]


def _terminal(references, charge):
    for reference in references:
        original._verify(reference, charge)
    for reference in references:
        _need(original.fingerprint(reference["path"]) == reference["fp"], "TERMINAL_PHYSICAL_CHANGE")
    charge(0)


def _meter(config, started, initial=0):
    counter = [initial]
    def charge(count):
        counter[0] += count
        _need(counter[0] <= config["limits"]["supervisor_read_bytes"], "COMPARISON_READ_LIMIT")
        _need(time.perf_counter()-started <= config["limits"]["max_seconds"], "COMPARISON_TIME_LIMIT")
        _need(_rss() <= config["limits"]["self_rss_bytes"], "COMPARISON_RSS_LIMIT")
    charge(0)
    return charge, counter


def _complete_phases(completion, *, offline):
    _need(completion.get("status") in SUCCESS and completion.get("core_verified") is True,
          "SUCCESSFUL_VERIFIED_CORE_REQUIRED")
    phases = completion.get("phases", {})
    _need(set(phases) == {"capture", "core", "news", "theme_sources", "theme", "presentation"}
          and all(phases[key] == "COMPLETE" for key in ("capture", "core", "theme", "presentation"))
          and phases["theme_sources"] in ("COMPLETE", "PARTIAL"), "WHOLE_WORKFLOW_WITHOUT_FAILED_PHASE_REQUIRED")
    _need(phases["news"] == "UNKNOWN" if offline else phases["news"] in ("COMPLETE", "PARTIAL", "UNKNOWN"),
          "EXPLICIT_NEWS_UNKNOWN_OR_COMPLETE_REQUIRED")
    _need(completion.get("phase_failures") == {} and not completion.get("error"), "EXECUTION_FAILURE_FORBIDDEN")
    expected = "COMPLETE" if all(value == "COMPLETE" for value in phases.values()) else "COMPLETED_WITH_UNKNOWNS"
    _need(completion["status"] == expected, "UNKNOWN_STATUS_MUST_REMAIN_EXPLICIT")


def _theme_acquisition(themes, arm, phase, charge):
    _need(themes.get("schema_version") == "PREMARKET_THEME_SOURCE_COLLECTION_V1"
          and themes.get("source_capabilities_issued") == 0, "ORIGINAL_THEME_COLLECTION_REQUIRED")
    facts = themes["parent_acquisition"]
    selected = arm["research_selected_ids"]
    _need(facts.get("schema_version") == "PREMARKET_THEME_ACQUISITION_FACTS_V1"
          and facts.get("original_selected_count") == len(selected)
          and facts.get("display_amount_filter_applied") is False, "WHOLE_ORIGINAL_SELECTION_REQUIRED")
    # This measurement is explicitly the full, nonempty real selection. Empty
    # scopes require another contract, never a fabricated all-market speedup.
    _need(bool(selected) and facts.get("scope") ==
          "ALL_DECLARED_SOURCE_NODES_THEN_GENUINE_READER_INTERSECTION_WITH_FULL_ORIGINAL_SELECTION",
          "FULL_DECLARED_THEME_SCOPE_REQUIRED")
    records = facts["records"]
    directories = facts["directory_captures"]
    _need(len(directories) == 2 and {row["endpoint"] for row in directories} ==
          {"industry_list_sina", "concept_list_sina"}
          and all(row["status"] == "COMPLETE" and row["pagination_complete"] is True for row in directories),
          "BOTH_FULL_THEME_DIRECTORIES_REQUIRED")
    by_family = {"industry_members_sina": facts["all_requested_industry_nodes"],
                 "concept_members_sina": facts["all_requested_concept_symbols"]}
    _need(all(nodes and len(nodes) == len(set(nodes)) for nodes in by_family.values()), "COMPLETE_NONEMPTY_NODE_SCOPE_REQUIRED")
    for directory in directories:
        result = _document(directory["result"], charge)
        member_endpoint = directory["endpoint"].replace("_list_", "_members_")
        nodes = [row.get("label") or row.get("板块代码") for row in result["rows"]]
        _need(result["status"] == "COMPLETE" and result["pagination_complete"] is True
              and nodes == by_family[member_endpoint], "DIRECTORY_ACTUAL_FULL_NODE_SCOPE_REQUIRED")
    expected = {(endpoint, node) for endpoint, nodes in by_family.items() for node in nodes}
    actual = [(row.get("endpoint"), row.get("parameters", {}).get("symbol")) for row in records]
    _need(len(actual) == len(set(actual)) == facts["requested_members"] == len(expected)
          and set(actual) == expected, "EVERY_REQUESTED_NODE_MUST_HAVE_ONE_FACT")
    gaps, potential = [], {key: [] for key in by_family}
    captures = {row["receipt_id"]: row for row in themes["context_captures"]}
    _need(len(captures) == len(themes["context_captures"]) == themes["context_capture_count"]
          and len(themes["raw_receipt_sources"]) == themes["raw_receipt_source_count"],
          "COMPLETE_THEME_CAPTURE_LEDGER_REQUIRED")
    for row in records:
        source = row["actual_capture"]
        _need(row["status"] in ("COMPLETE", "PARTIAL")
              and row["status"] == source["status"]
              and row["pagination_complete"] is source["pagination_complete"]
              and row["rows"] == source["rows"]
              and row["endpoint"] == source["endpoint"] and row["parameters"] == source["parameters"]
              and captures.get(source["receipt_id"]) == source
              and row["literal_intersection_is_not_authenticated_membership"] is True,
              "SOURCE_FACT_BINDING_OR_FAILED_SOURCE")
        intersection = row["literal_intersection_with_full_original_selected"]
        _need(intersection == [iid for iid in selected if iid in intersection]
              and row["potential_reader_request"] is bool(intersection), "WHOLE_SELECTION_INTERSECTION_REQUIRED")
        if intersection:
            potential[row["endpoint"]].append(row["parameters"]["symbol"])
        if row["status"] == "PARTIAL":
            _need(row["pagination_complete"] is False, "ONLY_EXPLICIT_PAGINATION_GAPS_ALLOWED")
            _need(bool(intersection) or row["omitted_from_potential_scope_reason"] ==
                  "INCOMPLETE_SOURCE_CANNOT_PROVE_ABSENCE_OF_MEMBERSHIP", "UNKNOWN_MEMBERSHIP_MUST_NOT_BECOME_ABSENCE")
            actual_result = _document(source["result"], charge)
            _need(actual_result["status"] == "PARTIAL" and actual_result["pagination_complete"] is False
                  and actual_result["endpoint"] == row["endpoint"]
                  and actual_result["parameters"] == row["parameters"]
                  and len(actual_result["rows"]) == row["rows"], "REAL_PAGINATION_GAP_REQUIRED")
            _need(actual_result.get("failure_category") is None and actual_result.get("error_code") is None
                  and actual_result.get("gaps") == ["PAGINATION_OR_EMPTY_RESULT_NOT_VERIFIED"],
                  "ONLY_NATIVE_PAGINATION_UNKNOWN_NOT_PROVIDER_EXECUTION_FAILURE")
            gaps.append(dict(fact=deepcopy(row), original_result_metadata={key: value
                for key, value in actual_result.items() if key != "rows"},
                full_original_rows_sha256=_digest(actual_result["rows"])))
        else:
            _need(row["pagination_complete"] is True, "COMPLETE_PAGINATION_REQUIRED")
        expected_reason = None if intersection else ("NO_LITERAL_INTERSECTION_OBSERVED"
            if row["status"] == "COMPLETE" else "INCOMPLETE_SOURCE_CANNOT_PROVE_ABSENCE_OF_MEMBERSHIP")
        _need(row["omitted_from_potential_scope_reason"] == expected_reason, "SOURCE_UNKNOWN_REASON_CHANGED")
    for endpoint, parent_key, source_key in (
        ("industry_members_sina", "potential_industry_nodes", "requested_industry_nodes"),
        ("concept_members_sina", "potential_concept_symbols", "requested_concept_symbols")):
        _need(facts[parent_key] == themes[source_key] == potential[endpoint], "POTENTIAL_READER_SCOPE_CHANGED")
    _need(facts["excluded_from_potential_scope_count"] == len(records)-sum(map(len, potential.values()))
          and facts["potential_scope_is_not_ready_or_source_authority"] is True
          and facts["native_authority"] == "NOT_ISSUED_BY_CAPTURE_CACHE", "THEME_AUTHORITY_OR_EXCLUDED_SCOPE_CHANGED")
    _need(facts["status"] == ("PARTIAL_OR_FAILED_RETAINED" if gaps else "COMPLETE_CAPTURE_NOT_THEME_READY")
          and phase == ("PARTIAL" if gaps else "COMPLETE"), "SOURCE_UNKNOWN_PHASE_MUST_NOT_BE_REWRITTEN")
    return dict(full_acquisition=deepcopy(facts), full_collection=deepcopy(themes),
                retained_pagination_gaps=gaps, all_requested_nodes_compared=True,
                partial_nodes_are_not_known_empty=True, full_market_source_completeness_claimed=False)


def _news_projection(completion, run, charge, started_at=None, completed_at=None):
    news = completion.get("news")
    _need(isinstance(news, dict), "OFFLINE_NEWS_METADATA_REQUIRED")
    metadata = _document(news["metadata"], charge)
    summary = metadata["summary"]
    _need(metadata["schema_version"] == "PREMARKET_REPORTED_NEWS_V1"
          and metadata["run_id"] == run["run_id"]
          and metadata["observation_date"] == run["observation_date"]
          and metadata["target_session"] == run["target_session"]
          and metadata["config"]["offline"] is True and news["summary"] == summary,
          "ORIGINAL_OFFLINE_NEWS_BINDING_REQUIRED")
    _need(summary["status"] == "OFFLINE_NOT_COLLECTED_UNKNOWN"
          and summary["acquisition_complete"] is None
          and summary["article_requests_planned"] is None
          and summary["business_date_status_counts"] == {}
          and summary["source_coverage"] == summary["economic_attribution"] == "UNKNOWN"
          and summary["EVENT_confirmation"] == "UNKNOWN_NOT_ISSUED", "OFFLINE_NEWS_MUST_REMAIN_UNKNOWN")
    _need(all(summary[key] == 0 for key in ("HTTP_GET_attempts", "articles_saved", "article_records",
          "parsed_articles", "homepages_completed", "automatic_retries"))
          and summary["homepage_requests_declared"] == 2
          and all(metadata[key] == [] for key in ("original_HTTP_captures", "discovered_candidates",
                                                  "articles", "homepage_parse_errors")),
          "OFFLINE_NEWS_ZERO_NETWORK_ZERO_ARTICLES_REQUIRED")
    _need(all(metadata[key] is True for key in ("raw_and_receipt_initial_terminal_SHA7FP_verified",
        "no_SOURCE_or_EVENT_capability_issued", "no_direction_mapping_or_company_business_guess")),
        "OFFLINE_NEWS_AUTHORITY_CHANGED")
    generated = _clock(metadata["actual_generated_at"])
    _need(_clock(run["decision_cutoff"]) <= generated <= _clock(completion["completed_at"]),
          "ACTUAL_NEWS_CLOCK_REQUIRED")
    if started_at is not None:
        _need(_clock(started_at) <= generated <= _clock(completed_at), "NEWS_OUTSIDE_MEASURED_WORKFLOW")
    return {key: value for key, value in metadata.items() if key not in ("run_id", "actual_generated_at")}


def _proof(completion, job, read_meter, *, offline=True, started_at=None, completed_at=None):
    """Hashes compare actual independent outputs; never rewrite their metadata."""
    _complete_phases(completion, offline=offline)
    if offline:
        calls = completion["metrics"]["calls"]
        _need(bool(calls) and all(type(value) is int and value == 0 for value in calls.values()),
              "CACHE_ONLY_REQUEST_COUNTER_NOT_ZERO")
    artifacts = completion["core_artifacts"]
    _need(set(artifacts) == {"canonical-run.json.gz", "input.json.gz", "manifest.json",
          "observation.json", *CSV_NAMES} and len(completion["display"]["artifacts"]) == 3,
          "ALL_CORE_AND_DISPLAY_ARTIFACTS_REQUIRED")
    run = _document(artifacts["canonical-run.json.gz"], read_meter)
    market = _document(artifacts["input.json.gz"], read_meter)
    manifest = _document(artifacts["manifest.json"], read_meter)
    _need(run["coverage_type"] in ("PROSPECTIVE_CAPTURED", "RECONSTRUCTED_HISTORICAL")
          and run["coverage_type"] == market["coverage_type"],
          "REAL_COVERAGE_REQUIRED")
    run_id = run["run_id"]
    _need(run_id == completion["run_id"], "COMPLETION_RUN_DIFFERS")
    market_projection, assembly_metadata = _market_projection(market, run, manifest)
    arm = run["results"][STRATEGY]
    csv_values = {name: _csv_values(_read_reference(artifacts[name], read_meter), run_id) for name in CSV_NAMES}
    all_ids = [row["instrument_id"] for row in csv_values["all.csv"]["rows"]]
    _need(len(all_ids) == len(set(all_ids)) == len(arm["eligible_pool_ids"])
          and set(all_ids) == set(arm["eligible_pool_ids"]), "FULL_ELIGIBLE_CSV_REQUIRED")
    capture = _document(original._ref(job / "capture-summary.json", read_meter), read_meter)
    themes = _document(original._ref(job / "theme-source-summary.json", read_meter), read_meter)
    # Includes full original physical references and receipts, not just endpoints.
    source_projection = dict(core=capture["core_records"], context=capture["context_records"],
        raw=capture["raw_context_sources"], theme=themes)
    parent_ref = completion["theme_artifacts"].get("native-theme-parent.json.gz")
    _need(parent_ref is not None, "COMPLETE_NUMERIC_THEME_PARENT_REQUIRED")
    parent = _document(parent_ref, read_meter)
    _need(parent.get("source_input_hash") == run["input_hash"]
          and parent.get("observation_date") == run["observation_date"]
          and _clock(parent["decision_cutoff"]) >= _clock(run["decision_cutoff"]),
          "THEME_PARENT_ORIGINAL_INPUT_BINDING_REQUIRED")
    assembly_metadata.update(theme_source_input_hash=parent["source_input_hash"],
        theme_effective_input_hash=parent["effective_input_hash"],
        theme_context_content_hash=parent["context_content_hash"],
        context_cutoff=parent["decision_cutoff"])
    theme_projection = {key: parent[key] for key in (
        "items", "source_refs", "computed_exchange_sessions", "membership_mode", "status",
        "market_edge", "exact_numeric_encoding", "historical_membership_verified",
        "official_pit_validation_countable", "formal_focus", "core_pass", "parameter_status")}
    _need(len(capture["sessions"]) == 67 and len(set(capture["sessions"])) == 67
          and parent["computed_exchange_sessions"] == capture["sessions"], "WHOLE_ORIGINAL_67_SESSION_SCOPE_REQUIRED")
    acquisition = _theme_acquisition(themes, arm, completion["phases"]["theme_sources"], read_meter)
    news = _news_projection(completion, run, read_meter, started_at, completed_at) if offline else None
    projections = dict(capture_sources=source_projection, market_input=market_projection,
        security_selection={key: arm[key] for key in ("eligible_pool_ids", "research_selected_ids", "counts")},
        formal_csv_values=csv_values, core_numeric_leaves=_numeric_leaves(arm["items"]),
        theme_values=theme_projection, display_counts=completion["display"]["counts"],
        phases=completion["phases"], theme_acquisition=acquisition)
    if offline:
        projections["offline_news"] = news
    controls = [original._ref(job / name, read_meter) for name in
                ("worker-completion.json", "capture-summary.json", "theme-source-summary.json")]
    references = _references(completion, capture, themes, controls)
    _need(bool(completion.get("validation_references")), "FULL_NATIVE_VALIDATION_REFERENCES_REQUIRED")
    _terminal(references, read_meter)
    return dict(hashes={key: _digest(value) for key, value in projections.items()}, run_id=run_id,
        decision_cutoff=run["decision_cutoff"], context_cutoff=parent["decision_cutoff"],
        observation_date=run["observation_date"], target_session=run["target_session"],
        eligible_count=len(all_ids), selected_count=len(arm["research_selected_ids"]),
        original_execution_identity=assembly_metadata,
        terminal_references=references, references_fully_verified_after_projection=True,
        retained_pagination_gaps=acquisition["retained_pagination_gaps"],
        full_market_source_completeness_claimed=False,
        formal_export_byte_equality_claimed=False,
        projection_scope="EXACT_CAPTURES_AND_MARKET_INPUT_EXCEPT_FOUR_PROVEN_ROOT_UNIVERSE_ASSEMBLY_CLOCKS; ALL_CSV_CELLS_EXCEPT_RUN_ID; ALL_CORE_NUMERIC_NULL_BOOL_LEAVES; WHOLE_THEME_ITEMS_AND_DECLARED_GATES")


def _code_refs(charge=None):
    return _references(shared.code_refs(charge), original._ref(Path(v1.__file__).resolve(), charge),
                       original._ref(Path(__file__).resolve(), charge))


def _prerequisite(job, config, expected, charge):
    """Read-only qualification of the current genuine original daily completion."""
    job = Path(job).expanduser().absolute()
    _need(job == job.resolve(strict=True) and job.is_dir(), "UNALIASED_COMPLETED_ORIGINAL_JOB_REQUIRED")
    _need(job.is_relative_to(Path(config["state_root"])), "ORIGINAL_JOB_MUST_SHARE_SOURCE_STATE")
    final_ref = original._ref(job / "final-control.json", charge)
    final = _document(final_ref, charge)
    _need(final.get("schema_version") == shared.VERSION and final.get("status") in SUCCESS
          and final.get("execution_engine") == "original" and final.get("job") == str(job)
          and final.get("external_worker_exit_code") == shared.EXIT_CODES.get(final.get("status"))
          and all(final.get(key) is True for key in ("external_worker_reaped", "core_verified",
            "whole_workflow_wall_enforced", "implementation_epoch_verified", "source_terminal_binding_verified")),
          "CURRENT_SUCCESSFUL_SEALED_ORIGINAL_REQUIRED")
    _need(final.get("expected_sessions") == list(expected)
          and str(_clock(final["final_sealed_at"]).astimezone(original.SH).date()) == expected[2],
          "ORIGINAL_MUST_COMPLETE_ON_CURRENT_PLANNING_DAY")
    for key in ("completion", "configuration", "code_registry"):
        _need(Path(final[key]["path"]).is_relative_to(job), "ORIGINAL_OWNED_CONTROL_REQUIRED")
    completion = _document(final["completion"], charge)
    _need(completion.get("schema_version") == shared.VERSION
          and completion.get("execution_engine") == "original"
          and completion.get("expected_sessions") == list(expected)
          and completion.get("status") == final["status"]
          and completion.get("observation_date") == expected[0]
          and completion.get("target_session") == expected[1]
          and completion.get("display") == final.get("display"), "ORIGINAL_COMPLETION_BINDING_REQUIRED")
    actual = _document(final["configuration"], charge)
    _need(_offline_configuration(actual) == config, "ORIGINAL_CONFIG_AND_LIMITS_MUST_MATCH")
    code = _document(final["code_registry"], charge)
    _need(code == shared.code_refs(charge), "ORIGINAL_EXECUTED_SOURCE_CLOSURE_CHANGED")
    shared._expected_gate(expected)
    original._latest_gate(completion["calendar_rows"], expected[0], expected[1])
    proof = _proof(completion, job, charge, offline=False)
    news = None if completion.get("news") is None else _document(completion["news"]["metadata"], charge)
    _need(news is not None and news.get("run_id") == completion["run_id"]
          and news.get("observation_date") == expected[0] and news.get("target_session") == expected[1]
          and news.get("summary") == completion["news"]["summary"], "ORIGINAL_NEWS_UNKNOWN_LEDGER_REQUIRED")
    acquired = news["summary"]["acquisition_complete"]
    _need(acquired is None or type(acquired) is bool, "ORIGINAL_NEWS_COMPLETENESS_REQUIRED")
    _need(completion["phases"]["news"] == ("COMPLETE" if acquired is True else
          "PARTIAL" if acquired is False else "UNKNOWN"), "ORIGINAL_NEWS_PHASE_MUST_RETAIN_UNKNOWN")
    references = _references(final_ref, final, completion, code, proof["terminal_references"])
    _terminal(references, charge)
    return dict(job=str(job), final=final_ref, completion=final["completion"], run_id=completion["run_id"],
        expected_sessions=list(expected), baseline_hashes={key: proof["hashes"][key] for key in BASELINE_KEYS},
        retained_pagination_gaps=proof["retained_pagination_gaps"],
        original_online_news=news, original_news_not_claimed_equivalent_to_offline=True,
        terminal_references=references, admission_only_not_timing_reference=True,
        admission_scope="SUCCESSFULLY_SEALED_CURRENT_ORIGINAL_WORKER",
        gateway_registry_delivery_verified=False, caller_must_verify_current_daily_gateway_delivery=True)


def _match_prerequisite(proof, prerequisite):
    _need(all(proof["hashes"].get(key) == prerequisite["baseline_hashes"][key] for key in BASELINE_KEYS),
          "CURRENT_ORIGINAL_SOURCE_INPUT_SELECTION_OR_VALUES_DIFFER")


def _publish_process_result(config, directory, engine, started, result, source_refs=()):
    """Recheck all originals after proof, inside the same read/time/receipt caps."""
    reference_error = None
    try:
        references = _references(source_refs, (result.get("comparison_proof") or {}).get("terminal_references", []),
                                 (result.get("prerequisite") or {}).get("terminal_references", []))
    except BaseException as error:
        reference_error, references = error, []
    # Initial meter failures must be sealed by the V1 failure-publication path.
    count = [result.get("comparison_verification_returned_bytes", 0)+result.get("parent_control_returned_bytes", 0)]
    def verify(reference):
        if reference_error is not None:
            raise reference_error
        def charge(size):
            count[0] += size
            result["parent_terminal_total_returned_bytes"] = count[0]
            _need(count[0] <= config["limits"]["supervisor_read_bytes"], "COMPARISON_READ_LIMIT")
            _need(time.perf_counter()-started <= config["limits"]["max_seconds"], "COMPARISON_TIME_LIMIT")
            _need(_rss() <= config["limits"]["self_rss_bytes"], "COMPARISON_RSS_LIMIT")
        charge(0)
        original._verify(reference, charge)
    def check(*args):
        _check_parent_budget(*args)
        if result.get("comparison_proof") is not None:
            shared._expected_gate(result["prerequisite"]["expected_sessions"])
        for reference in references:
            _need(original.fingerprint(reference["path"]) == reference["fp"], "PARENT_FINAL_REFERENCE_CHANGED")
        _check_parent_budget(*args)
        if result.get("comparison_proof") is not None:
            shared._expected_gate(result["prerequisite"]["expected_sessions"])
    proxy = SimpleNamespace(**vars(original))
    proxy._verify = verify
    publish = _bind(v1._publish_process_result, {"original": proxy, "_check_parent_budget": check})
    return publish(config, directory, engine, started, result, [None] if reference_error is not None else references)


def _worker_identity(completion, engine, expected):
    _need(completion.get("schema_version") == (original.VERSION if engine == "original-ref" else shared.VERSION)
          and (engine == "original-ref" or (completion.get("execution_engine") == engine
               and completion.get("expected_sessions") == list(expected)))
          and completion.get("observation_date") == expected[0]
          and completion.get("target_session") == expected[1], "ACTUAL_WORKER_ENGINE_BINDING_REQUIRED")


def _worker(config_path, directory, engine, expected):
    started = time.perf_counter()
    initial_bytes = []
    config = _offline_configuration(original._read_control(config_path, initial_bytes.append))
    _need(engine in ENGINES, "UNKNOWN_ENGINE")
    expected = tuple(expected)
    shared._expected_gate(expected)
    charge, read_count = _meter(config, started, sum(initial_bytes))
    experiment_ref = original._ref(directory.parent / "experiment.json", charge)
    experiment = _document(experiment_ref, charge)
    _need(experiment.get("schema_version") == VERSION and experiment["configuration"] == config
          and experiment["expected_sessions"] == list(expected) and engine in experiment["engines"],
          "V2_WORKER_EXPERIMENT_BINDING_REQUIRED")
    _need(experiment["source_code"] == _code_refs(charge), "V2_WORKER_SOURCE_CLOSURE_CHANGED")
    prerequisite = _prerequisite(experiment["prerequisite"]["job"], config, expected, charge)
    _need(prerequisite == experiment["prerequisite"], "COMPLETED_ORIGINAL_CHANGED_BEFORE_ARM")
    job = directory / "job"
    job.mkdir(exist_ok=False)
    stages = []
    function = _instrumented(engine, stages)
    kwargs = {} if engine == "original-ref" else dict(engine=engine, expected_sessions=expected)
    started_at = datetime.now(timezone.utc).isoformat()
    workflow_started, cpu = time.perf_counter(), time.process_time()
    completion = function(config, job, **kwargs)
    elapsed, consumed = time.perf_counter()-workflow_started, time.process_time()-cpu
    completed_at = datetime.now(timezone.utc).isoformat()
    shared._expected_gate(expected)
    result = dict(schema_version=VERSION, engine=engine, status=completion["status"],
        started_at=started_at, completed_at=completed_at, workflow_wall_seconds=elapsed,
        workflow_cpu_seconds=consumed, process_peak_rss_bytes_after_workflow=_rss(),
        job_output_bytes=original.directory_bytes(job), phase_measurements=stages,
        metrics=completion["metrics"], job=str(job), completion=original._ref(job / "worker-completion.json", charge),
        comparison_proof=None, prerequisite=prerequisite, real_clock_used=True,
        ordinary_supervisor_included=False, gateway_admission_included=False, acquisition_included=False,
        network_policy="ORIGINAL_OFFLINE_TRUE_RETRY_FALSE_NO_SEEDS", scope=SCOPE)
    proof_started, proof_cpu = time.perf_counter(), time.process_time()
    try:
        _need(completion.get("observation_date") == expected[0] and completion.get("target_session") == expected[1],
              "ACTUAL_SESSION_DIFFERS")
        _worker_identity(completion, engine, expected)
        proof = _proof(completion, job, charge, started_at=started_at, completed_at=completed_at)
        _match_prerequisite(proof, prerequisite)
        _terminal(_references(experiment_ref, experiment["source_code"], prerequisite["terminal_references"]), charge)
        shared._expected_gate(expected)
        result["comparison_proof"] = proof
    except Exception as error:
        result["comparison_refusal"] = dict(type=type(error).__name__, reason=str(error)[:2000])
    result.update(comparison_verification_seconds=time.perf_counter()-proof_started,
        comparison_verification_cpu_seconds=time.process_time()-proof_cpu,
        process_cpu_seconds_at_end=time.process_time(), process_peak_rss_bytes_at_end=_rss(),
        comparison_verification_returned_bytes=read_count[0],
        comparison_read_scope="WORKER_CONFIG_PREREQUISITE_PROOF_TERMINAL_FULL_SHA_AND_DECOMPRESSION; PARENT_CONTROL_PS_AND_TERMINAL_ADDED_TO_SAME_SUPERVISOR_ALLOCATION")
    _save(directory / "measurement.json", result)
    return 0 if completion["status"] in SUCCESS and result["comparison_proof"] is not None else 4


def _run_process(config, directory, engine, expected, *, source_refs=()):
    # V1 owned process/supervision path, with its control and ps reads also
    # charged to V2's cumulative verification allocation.
    started, peak = time.perf_counter(), 0
    charge, control_bytes = _meter(config, started)
    config_path = directory / "configuration.json"
    _save(config_path, config)
    _reserve_failure_receipts(config, directory, engine, started)
    measurement = None
    with tempfile.TemporaryDirectory(prefix="premarket-real-benchmark-bytecode-") as temporary:
        absent = Path(temporary) / "absent"
        command = [sys.executable, "-I", "-B", "-X", "pycache_prefix="+str(absent),
            str(Path(__file__).resolve()), "_worker", "--config", str(config_path),
            "--directory", str(directory), "--engine", engine, "--expected", *expected]
        environment = dict(os.environ)
        for name in ("PYTHONPATH", "PYTHONHOME"):
            environment.pop(name, None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        with (directory / "console.txt").open("xb") as console:
            process = subprocess.Popen(command, cwd=ROOT, stdout=console, stderr=subprocess.STDOUT,
                env=environment, start_new_session=os.name == "posix")
            failure = None
            try:
                while process.poll() is None:
                    if time.perf_counter()-started > config["limits"]["max_seconds"]:
                        raise ValueError("BENCHMARK_PROCESS_TIME_LIMIT")
                    try:
                        peak = max(peak, original._tree_sample(process.pid, charge))
                    except Exception:
                        if process.poll() is None:
                            raise
                    _need(peak <= config["limits"]["tree_rss_bytes"], "PROCESS_RSS_LIMIT")
                    _need(_arm_output_bytes(directory, engine) <= config["limits"]["output_bytes"],
                          "PROCESS_OUTPUT_LIMIT")
                    time.sleep(0.25)
                code = process.wait()
                _need(not original._owned_group_members(process.pid, charge), "OWNED_DESCENDANTS_REMAIN")
                _need(time.perf_counter()-started <= config["limits"]["max_seconds"], "PROCESS_FINAL_TIME_LIMIT")
                _need(_arm_output_bytes(directory, engine) <= config["limits"]["output_bytes"],
                      "PROCESS_FINAL_OUTPUT_LIMIT")
            except BaseException as error:
                original._stop_owned(process)
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                failure = dict(type=type(error).__name__, reason=str(error)[:2000])
                code = process.returncode
            measurement_path = directory / "measurement.json"
            if failure is None and code in (0, 4) and measurement_path.exists():
                try:
                    measurement = original._read_control(measurement_path, charge)
                    _need(measurement["schema_version"] == VERSION and measurement["engine"] == engine,
                          "WORKER_MEASUREMENT_IDENTITY_CHANGED")
                except Exception as error:
                    measurement = None
                    failure = dict(type=type(error).__name__, reason=str(error)[:2000])
            if measurement is None:
                measurement = dict(schema_version=VERSION, engine=engine, status="FAILED",
                    comparison_proof=None, process_failure=failure)
            measurement.update(parent_control_returned_bytes=control_bytes[0], external_exit_code=code, external_wall_seconds=time.perf_counter()-started,
                sampled_peak_tree_rss_bytes=peak, external_worker_reaped=process.poll() is not None)
    return _publish_process_result(config, directory, engine, started, measurement, source_refs)


def compare(reference, candidate):
    for item in (reference, candidate):
        seconds = item.get("workflow_wall_seconds")
        if item.get("status") in SUCCESS and item.get("comparison_proof") is not None:
            _need(type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0,
                  "FINITE_POSITIVE_MEASURED_TIME_REQUIRED")
    result = _bind(v1.compare)(reference, candidate)
    for label, row in (("reference", reference), ("candidate", candidate)):
        if row.get("schema_version") != VERSION or not row.get("prerequisite"):
            result["reasons"].append(label.upper()+"_V2_PREREQUISITE_REQUIRED")
        if row.get("external_exit_code") != 0 or row.get("external_worker_reaped") is not True:
            result["reasons"].append(label.upper()+"_OWNED_PROCESS_SUCCESS_REQUIRED")
    if reference.get("prerequisite") != candidate.get("prerequisite"):
        result["reasons"].append("DIFF:completed_original_prerequisite")
    result["comparable"] = not result["reasons"]
    if not result["comparable"]:
        for key in ("wall_seconds_saved", "worker_wall_speedup_percent", "worker_speed_ratio", "output_bytes_change"):
            result.pop(key, None)
    result.update(schema_version=VERSION, scope=SCOPE, real_end_to_end_speedup_established=False,
        full_market_source_completeness_claimed=False, original_online_news_equivalence_claimed=False,
        repeated_order_bias_controlled=False)
    return result


def _new_output(path, state):
    path = Path(path).expanduser().absolute()
    _need(path.parent == path.parent.resolve(strict=True) and not path.exists() and not path.is_symlink(),
          "NEW_UNALIASED_OWNED_OUTPUT_REQUIRED")
    _need(not path.is_relative_to(state) and not Path(state).is_relative_to(path)
          and not path.is_relative_to(ROOT), "PRIVATE_OUTPUT_SEPARATE_FROM_SOURCE_AND_STATE_REQUIRED")
    return path


def benchmark(config, output, engines, observation, target, completed_original_job):
    config = _offline_configuration(config)
    _need(engines and len(set(engines)) == len(engines) and all(engine in ENGINES for engine in engines),
          "UNIQUE_EXPLICIT_ENGINES_REQUIRED")
    expected = (observation, target, str(datetime.now(timezone.utc).astimezone(original.SH).date()))
    shared._expected_gate(expected)
    output = _new_output(output, config["state_root"])
    with JobLock(Path(config["state_root"]) / "premarket.lock"):
        admission_started = time.perf_counter()
        charge, read_count = _meter(config, admission_started)
        prerequisite = _prerequisite(completed_original_job, config, expected, charge)
        refs = _code_refs(charge)
        experiment = dict(schema_version=VERSION, engines=list(engines), configuration=config,
            expected_sessions=list(expected), source_code=refs, prerequisite=prerequisite,
            scope=SCOPE, admission_only_not_online_reference=True,
            maximum_experiment_output_bytes=MAX_EXPERIMENT_BYTES,
            admission_returned_read_bytes=read_count[0])
        # Reserve aggregate metadata before execution. Each arm still receives
        # exactly the original output cap; no phase cap is enlarged or borrowed.
        aggregate_reserve = max(1024**2, len(json_bytes(experiment))*4)
        _need(len(engines)*config["limits"]["output_bytes"]+aggregate_reserve <= MAX_EXPERIMENT_BYTES,
              "EXPLICIT_EXPERIMENT_OUTPUT_CAP_2GIB")
        _terminal(_references(refs, prerequisite["terminal_references"]), charge)
        output.mkdir(exist_ok=False)
        _save(output / "experiment.json", experiment)
        experiment_ref = original._ref(output / "experiment.json")
        results = []
        for engine in engines:
            shared._expected_gate(expected)
            directory = output / engine
            directory.mkdir()
            result = _run_process(config, directory, engine, expected,
                source_refs=_references(refs, experiment_ref, prerequisite["terminal_references"]))
            results.append(result)
            if result["status"] not in SUCCESS or result.get("comparison_proof") is None:
                break
        comparisons = [compare(results[0], row) for row in results[1:]]
        summary = dict(schema_version=VERSION, results=results, comparisons=comparisons,
            requested_engines=list(engines), unrun_engines=list(engines[len(results):]),
            real_end_to_end_speedup_established=False, online_collection_measured=False,
            gateway_admission_measured=False, full_market_source_completeness_claimed=False,
            original_online_news_equivalence_claimed=False, repeated_order_bias_controlled=False,
            scope=SCOPE, notes="Single explicit order, warm OS caches uncontrolled; all actual cutoffs and run IDs retained.")
        _need(original.directory_bytes(output)+len(json_bytes(summary)) <= MAX_EXPERIMENT_BYTES,
              "EXPERIMENT_FINAL_OUTPUT_CAP_2GIB")
        _save(output / "summary.candidate.json", summary)
        (output / "summary.candidate.json").rename(output / "summary.json")
        _need(original.directory_bytes(output) <= MAX_EXPERIMENT_BYTES, "EXPERIMENT_FINAL_OUTPUT_CAP_2GIB")
        return summary


def _revalidate_arm(directory, expected_experiment=None, charge=None):
    """Recompute proof from original artifacts, never trust an old hash summary."""
    directory = Path(directory).expanduser().absolute()
    _need(directory == directory.resolve(strict=True), "UNALIASED_ARM_REQUIRED")
    _need(callable(charge), "EXPLICIT_SHARED_COMPARE_METER_REQUIRED")
    experiment_ref = original._ref(directory.parent / "experiment.json", charge)
    experiment = _document(experiment_ref, charge)
    _need(experiment.get("schema_version") == VERSION and directory.name in experiment["engines"],
          "V2_EXPERIMENT_REQUIRED")
    config = _offline_configuration(experiment["configuration"])
    charge(0)
    if expected_experiment is not None:
        _need(all(experiment[key] == expected_experiment[key] for key in
              ("configuration", "expected_sessions", "source_code", "prerequisite")),
              "CROSS_EXPERIMENT_BINDINGS_DIFFER")
    expected = experiment["expected_sessions"]
    shared._expected_gate(expected)
    _need(experiment["source_code"] == _code_refs(charge), "CROSS_EXPERIMENT_SOURCE_CLOSURE_CHANGED")
    prerequisite = _prerequisite(experiment["prerequisite"]["job"], config, expected, charge)
    _need(prerequisite == experiment["prerequisite"], "CROSS_EXPERIMENT_ORIGINAL_CHANGED")
    receipt_refs = [original._ref(path, charge) for path in _receipt_paths(directory, directory.name)]
    first, second = [_document(ref, charge) for ref in receipt_refs]
    _need(first == second and first.get("schema_version") == VERSION
          and first.get("engine") == directory.name and first.get("status") in SUCCESS
          and first.get("external_exit_code") == 0 and first.get("external_worker_reaped") is True
          and first.get("prerequisite") == prerequisite, "SUCCESSFUL_MATCHING_PARENT_RECEIPTS_REQUIRED")
    _need(first.get("external_wall_seconds", math.inf) <= config["limits"]["max_seconds"]
          and first.get("sampled_peak_tree_rss_bytes", math.inf) <= config["limits"]["tree_rss_bytes"]
          and first.get("parent_terminal_total_returned_bytes", math.inf) <= config["limits"]["supervisor_read_bytes"]
          and _arm_output_bytes(directory, directory.name) <= config["limits"]["output_bytes"],
          "ORIGINAL_RECEIPT_BUDGETS_REQUIRED")
    measurement_ref = original._ref(directory / "measurement.json", charge)
    measurement = _document(measurement_ref, charge)
    _need(all(first.get(key) == value for key, value in measurement.items()), "PARENT_WORKER_MEASUREMENT_DIFFERS")
    _need(first["job"] == str(directory / "job")
          and first["completion"]["path"] == str(directory / "job" / "worker-completion.json"),
          "OWNED_ACTUAL_ARM_BINDING_REQUIRED")
    completion = _document(first["completion"], charge)
    _worker_identity(completion, directory.name, expected)
    proof = _proof(completion, directory / "job", charge,
                   started_at=first["started_at"], completed_at=first["completed_at"])
    _need(proof == first["comparison_proof"], "RECOMPUTED_PROOF_DIFFERS")
    _match_prerequisite(proof, prerequisite)
    references = _references(experiment_ref, experiment["source_code"], receipt_refs, measurement_ref,
                              prerequisite["terminal_references"], proof["terminal_references"])
    _terminal(references, charge)
    return first, experiment, references


def _bootstrap_experiment(path):
    """Bounded bootstrap; its bytes/time enter the selected compare budget."""
    path = Path(path)
    maximum = 16*1024**2
    initial = original.fingerprint(path)
    _need(initial["st_size"] <= maximum, "COMPARE_BOOTSTRAP_CONTROL_SIZE_LIMIT")
    with path.open("rb") as handle:
        raw = handle.read(maximum+1)
    _need(len(raw) <= maximum and original.fingerprint(path) == initial,
          "COMPARE_BOOTSTRAP_CONTROL_CHANGED_OR_OVERSIZE")
    return strict_json(raw), len(raw)


def compare_saved(reference, candidate, output):
    # The bounded seed has no authority: its bytes/time are subsequently charged
    # and its exact metadata must equal the fully hashed, metered experiment.
    started = time.perf_counter()
    reference = Path(reference).expanduser().absolute()
    seed, bootstrap_bytes = _bootstrap_experiment(reference.parent / "experiment.json")
    config = _offline_configuration(seed["configuration"])
    charge, counter = _meter(config, started, bootstrap_bytes)
    output = _new_output(output, config["state_root"])
    with JobLock(Path(config["state_root"]) / "premarket.lock"):
        first, experiment, refs1 = _revalidate_arm(reference, seed, charge=charge)
        second, _, refs2 = _revalidate_arm(candidate, experiment, charge=charge)
        result = compare(first, second)
        references = _references(refs1, refs2)
        _terminal(references, charge)
        result.update(reference_arm=str(reference), candidate_arm=str(Path(candidate).absolute()),
            comparison_revalidation_returned_bytes=counter[0],
            saved_parent_receipts_and_full_proofs_revalidated=True)
        _need(FAILURE_RECEIPT_BYTES+len(json_bytes(result)) <=
              min(config["limits"]["output_bytes"], MAX_EXPERIMENT_BYTES), "COMPARE_OUTPUT_RESERVE_LIMIT")
        charge(0)
        output.mkdir()
        pending, final = output / "comparison.candidate.json", output / "comparison.json"
        reserve = output / "comparison.failure-reserve.json"
        raw = json_bytes(dict(schema_version=VERSION, comparable=False, status="UNSEALED"))
        with reserve.open("xb") as handle:
            handle.write(raw.ljust(FAILURE_RECEIPT_BYTES, b" "))
            handle.flush()
            os.fsync(handle.fileno())
        owned = {}
        def remember(path, stat):
            owned[path] = (stat.st_dev, stat.st_ino)
        def tail():
            charge(0)
            shared._expected_gate(experiment["expected_sessions"])
            _need(original.directory_bytes(output) <= min(config["limits"]["output_bytes"], MAX_EXPERIMENT_BYTES),
                  "COMPARE_OUTPUT_LIMIT")
            for ref in references:
                _need(original.fingerprint(ref["path"]) == ref["fp"], "COMPARE_FINAL_REFERENCE_CHANGED")
            charge(0)
            shared._expected_gate(experiment["expected_sessions"])
        try:
            tail()
            _save(pending, result, on_create=remember)
            tail()
            owned[final] = owned[pending]
            pending.rename(final)
            tail()
            return result
        except BaseException as error:
            for path in (pending, final):
                if path in owned and path.exists() and not path.is_symlink():
                    stat = path.stat()
                    _need((stat.st_dev, stat.st_ino) == owned[path], "COMPARE_OWNED_RECEIPT_CHANGED")
                    path.rename(output / "comparison.rejected.json")
            failed = dict(schema_version=VERSION, status="FAILED", comparable=False,
                reason=str(error).encode("utf-8")[:2048].decode("utf-8", errors="ignore"),
                real_end_to_end_speedup_established=False, quota_enlarged=False)
            raw = json_bytes(failed)
            _need(len(raw) <= FAILURE_RECEIPT_BYTES, "COMPARE_FAILURE_RESERVE_EXCEEDED")
            with reserve.open("r+b") as handle:
                handle.write(raw.ljust(FAILURE_RECEIPT_BYTES, b" "))
                handle.flush()
                os.fsync(handle.fileno())
            _need(not final.exists() and not final.is_symlink(), "COMPARE_RECEIPT_DESTINATION_EXISTS")
            reserve.rename(final)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            return failed


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    for key in ("config", "output", "observation-date", "target-session", "completed-original-job"):
        run.add_argument("--"+key, required=True)
    run.add_argument("--engines", nargs="+", choices=ENGINES, required=True)
    worker = commands.add_parser("_worker")
    for key in ("config", "directory", "engine"):
        worker.add_argument("--"+key, required=True, **({"choices": ENGINES} if key == "engine" else {}))
    worker.add_argument("--expected", nargs=3, required=True)
    comparison = commands.add_parser("compare")
    for key in ("reference", "candidate", "output"):
        comparison.add_argument("--"+key, required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "_worker":
        return _worker(Path(args.config), Path(args.directory), args.engine, args.expected)
    if args.command == "compare":
        result = compare_saved(args.reference, args.candidate, args.output)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["comparable"] else 4
    result = benchmark(original._read_control(args.config), args.output, args.engines,
                       args.observation_date, args.target_session, args.completed_original_job)
    print(json.dumps(dict(summary=str(Path(args.output).absolute() / "summary.json"),
        comparisons=result["comparisons"], unrun_engines=result["unrun_engines"],
        real_end_to_end_speedup_established=False), ensure_ascii=False))
    return 0 if not result["unrun_engines"] and all(row.get("comparison_proof") is not None
        for row in result["results"]) and all(row["comparable"] for row in result["comparisons"]) else 4


if __name__ == "__main__":
    raise SystemExit(main())
