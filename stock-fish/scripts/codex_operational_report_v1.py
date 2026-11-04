"""Operational reports and explicit routing to independently versioned contracts.

Report commands create no worker, network client, database or source capability.
Explicit routed commands retain their own original admission and write boundary.
A complete local report does not promote the workflow, PIT or market evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_delivery_audit_v1 as delivery
from scripts import codex_p0 as io
from scripts import codex_workspace as workspace

VERSION = "CODEX_OPERATIONAL_REPORT_V1"
MAX_BYTES = 512 * 1024**2
MAX_SECONDS = 180
ROUTES = {
    "research": "scripts.codex_research_protocol_v1",
    "release-check": "scripts.release_profile_manifest_v1",
    "recovery-drill": "scripts.recovery_drill_v1",
    "layout-benchmark": "scripts.benchmark_artifact_layout_v1",
    "supervised": "scripts.codex_supervised_premarket_v3",
}


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def base():
    return dict(schema_version=VERSION, inspected_at=datetime.now(timezone.utc).isoformat(),
        evidence_role="LOCAL_CONTROLS_AND_SAVED_CONTENT_ONLY",
        source_reauthenticated_now=False, historical_pit_verified=False,
        market_edge="NOT_ESTABLISHED", retry_authorized=False, worker_started=False,
        network_calls=0, business_database_opened=False, production_switched=False)


class Inspection:
    """Cumulative bounds and physical terminal checks across legacy readers."""
    def __init__(self, max_bytes=MAX_BYTES, max_seconds=MAX_SECONDS):
        need(type(max_bytes) is int and 0 < max_bytes <= MAX_BYTES,
             "OPERATIONS_READ_BUDGET_INVALID")
        need(type(max_seconds) in (int, float) and 0 < max_seconds <= MAX_SECONDS,
             "OPERATIONS_TIME_BUDGET_INVALID")
        self.limit, self.seconds = max_bytes, max_seconds
        self.started, self.used, self.refs, self.absent = time.monotonic(), 0, {}, set()

    def charge(self, count):
        need(type(count) is int and count >= 0, "OPERATIONS_READ_COUNT_INVALID")
        self.used += count
        need(self.used <= self.limit, "OPERATIONS_READ_BUDGET_EXCEEDED")
        need(time.monotonic() - self.started <= self.seconds, "OPERATIONS_TIME_BUDGET_EXCEEDED")

    def remember(self, reference):
        need(self.refs.get(reference["path"], reference) == reference,
             "OPERATIONS_INPUT_CHANGED")
        self.refs[reference["path"]] = reference

    def snapshot(self, path, sha):
        value, ref = io.read_snapshot(path, sha, max_stored=min(io.MAX_STORED_BYTES, self.limit-self.used))
        self.charge(ref["bytes"])
        self.remember(ref)
        return value, ref

    def inspect_job(self, path, deadline=None):
        report = delivery.audit_job(path, deadline=deadline,
            max_bytes=min(256*1024**2, self.limit-self.used),
            max_seconds=min(120, self.seconds-(time.monotonic()-self.started)))
        self.charge(report["audit_returned_read_bytes"])
        for ref in report["evidence"]:
            self.remember(ref)
        self.absent.update(report["absent_controls"])
        return report

    def terminal(self):
        self.charge(0)
        remaining = self.limit - self.used
        need(sum(reference["bytes"] for reference in self.refs.values()) <= remaining,
             "OPERATIONS_READ_BUDGET_EXCEEDED")
        if self.refs:
            # The original audit reader reopens the canonical ancestry after
            # each read, detecting a moved parent even if the held fd still
            # exposes unchanged bytes. Do not call reader.terminal(): these
            # reads already are this pass's terminal reads, each charged once.
            reader = delivery._Reader(max(1, remaining),
                self.seconds - (time.monotonic() - self.started))
            for reference in self.refs.values():
                before = reader.returned_bytes
                try:
                    reader.read(reference["path"], expected=reference)
                finally:
                    self.charge(reader.returned_bytes - before)
        for path in self.absent:
            need(not Path(path).exists() and not Path(path).is_symlink(),
                 "OPERATIONS_MISSING_CONTROL_APPEARED")
        self.charge(0)


def combined_delivery(job, run_path, run_sha, *, outcomes_path=None, outcomes_sha=None,
                      deadline=None, inspection=None):
    """Bind coverage to the exact canonical carrier already inspected in the job."""
    need(bool(outcomes_path) == bool(outcomes_sha), "OPERATIONS_OUTCOME_PAIR_REQUIRED")
    session = inspection or Inspection()
    audit = session.inspect_job(job, deadline)
    # The evidence inventory contains theme/display/validation attachments too.
    # Only the canonical role in the final-bound completion names the original
    # core. Matching a basename and run ID in some other attachment is not enough.
    final_path = str(Path(audit["job"]) / "final-control.json")
    final_refs = [ref for ref in audit["evidence"] if ref["path"] == final_path]
    need(len(final_refs) == 1, "OPERATIONS_JOB_RUN_NOT_BOUND")
    final, final_ref = session.snapshot(final_path, final_refs[0]["sha256"])
    need(final_ref == final_refs[0], "OPERATIONS_JOB_RUN_NOT_BOUND")
    completion_ref = final.get("completion")
    need(type(completion_ref) is dict and completion_ref in audit["evidence"],
         "OPERATIONS_JOB_RUN_NOT_BOUND")
    completion, observed_completion_ref = session.snapshot(completion_ref["path"], completion_ref["sha256"])
    need(observed_completion_ref == completion_ref, "OPERATIONS_JOB_RUN_NOT_BOUND")
    core = completion.get("core_artifacts")
    need(type(core) is dict and type(core.get("canonical-run.json.gz")) is dict,
         "OPERATIONS_JOB_RUN_NOT_BOUND")
    canonical_ref = core["canonical-run.json.gz"]
    run, reference = session.snapshot(run_path, run_sha)
    need(Path(reference["path"]).name == "canonical-run.json.gz",
         "OPERATIONS_CANONICAL_JOB_RUN_REQUIRED")
    need(reference == canonical_ref and canonical_ref in audit["evidence"]
         and audit.get("run_id") == run.get("run_id"),
         "OPERATIONS_JOB_RUN_NOT_BOUND")
    for key in ("observation_date", "target_session", "strategy_id"):
        need(audit.get(key) == run.get(key), "OPERATIONS_JOB_RUN_IDENTITY_CONFLICT")
    outcomes = session.snapshot(outcomes_path, outcomes_sha)[0] if outcomes_path else None
    from scripts.codex_research_coverage_v1 import build_coverage_report
    coverage = build_coverage_report(run, outcomes)
    result = {**base(), "command": "delivery-report", "run_id": run["run_id"],
        "observation_date": run["observation_date"], "target_session": run.get("target_session"),
        "decision_cutoff": run["decision_cutoff"], "workflow": audit, "coverage": coverage,
        "canonical_run_reference": canonical_ref,
        "canonical_binding": "FINAL_COMPLETION_CORE_ARTIFACT_ROLE",
        "status": audit["workflow_status"],
        "last_source_authentication_at": None,
        "source_authentication_status": "NOT_REAUTHENTICATED_BY_THIS_READER",
        "user_delivery_status": "UNKNOWN_NOT_OBSERVED",
        "daily_v1_readiness": "UNKNOWN_NOT_EVALUATED",
        "stage_metrics_status": "NOT_RECORDED_BY_LEGACY_WORKFLOW",
        "partial_core_is_complete_daily_delivery": False}
    session.terminal()
    result["read_bytes_before_publication"] = session.used
    return result, session


def runtime_manifest(runtime, *, source_root=None):
    """Inspect an explicitly selected runtime; never chooses a recovery fallback."""
    need(runtime is not None, "OPERATIONS_REGISTERED_RUNTIME_REQUIRED")
    runtime.check_binding()
    profile = runtime.profile
    need(profile["backend"] == "premarket", "OPERATIONS_PREMARKET_RUNTIME_REQUIRED")
    need(source_root is None or Path(source_root).absolute() == Path(profile["source_root"]),
         "OPERATIONS_REGISTERED_SOURCE_REQUIRED")
    reader = delivery._Reader(64*1024**2, 60)
    config, config_ref = reader.read(profile["config"], control=True)
    # This is an inventory of entry points, not an inferred transitive closure.
    paths = ("scripts/codex_daily_v2.py", "scripts/codex_supervised_premarket_v2.py",
             "scripts/codex_supervised_premarket_v3.py", "scripts/codex_operational_report_v1.py",
             "scripts/codex_research_protocol_v1.py",
             "runtime-locks/daily-review-py312-macos-x86_64.json")
    entries = []
    for relative in paths:
        path = Path(profile["source_root"]) / relative
        entries.append(dict(relative_path=relative,
            reference=reader.read(path) if reader.exists(path) else None))
    result = {**base(), "command": "runtime-manifest", "status": "REGISTERED_LOCATION",
        "runtime_name": profile["name"], "registry_scope": runtime.scope,
        "runtime_kind": type(runtime).__name__, "physical_binding_verified": True,
        "python": profile["python"], "source_root": profile["source_root"],
        "state_root": profile["state_root"], "report_root": profile["report_root"],
        "registered_configuration": config_ref, "registered_limits": config.get("limits"),
        "default_daily_entry": "scripts/codex_daily_v2.py", "default_engine": "original",
        "entry_inventory": entries, "inventory_role": "ENTRY_FILES_NOT_TRANSITIVE_DEPENDENCY_CLOSURE",
        "last_complete_business_acceptance": None,
        "acceptance_status": "NOT_INFERRED_FROM_SOURCE_OR_RUNTIME_BINDING",
        "source_capability_restored": False, "automatic_recovery_or_retry": False}
    reader.terminal()
    runtime.check_binding()
    result["evidence"] = list(reader.refs.values())
    result["absent_controls"] = [str(p) for p in sorted(reader.absent)]
    result["inspected_read_bytes"] = reader.returned_bytes
    return result


def component_identity(source_root, specification):
    """An explicit new component identity; never substitutes an old verifier.

    A complete declared dependency graph is required, but runtime/dynamic import
    completeness is a separate review. Caller JSON cannot grant source authority.
    """
    need(type(specification) is dict and set(specification) == {"schema_version", "components"}
         and specification["schema_version"] == "EXPLICIT_COMPONENT_INVENTORY_V1",
         "OPERATIONS_COMPONENT_SPEC_REQUIRED")
    components = specification["components"]
    need(type(components) is dict and 0 < len(components) <= 32,
         "OPERATIONS_COMPONENT_COUNT")
    reader = delivery._Reader(64*1024**2, 60)
    root = Path(source_root).absolute()
    hashes, visiting, evidence = {}, set(), {}

    def visit(name):
        need(name in components and name not in visiting, "OPERATIONS_COMPONENT_CYCLE_OR_MISSING")
        if name in hashes:
            return hashes[name]
        visiting.add(name)
        value = components[name]
        need(type(name) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name)
             and type(value) is dict and set(value) == {"version", "files", "depends_on"}
             and type(value["version"]) is str and value["version"]
             and type(value["files"]) is list and 0 < len(value["files"]) <= 512
             and all(type(p) is str for p in value["files"])
             and len(set(value["files"])) == len(value["files"])
             and type(value["depends_on"]) is list
             and all(type(p) is str for p in value["depends_on"])
             and len(set(value["depends_on"])) == len(value["depends_on"]),
             "OPERATIONS_COMPONENT_FIELDS")
        refs = {}
        for relative in sorted(value["files"]):
            p = Path(relative)
            need(not p.is_absolute() and ".." not in p.parts and p.as_posix() == relative
                 and p.suffix in {".py", ".json", ".yaml", ".yml", ".lock", ".txt"},
                 "OPERATIONS_COMPONENT_RELATIVE_SOURCE_REQUIRED")
            ref = reader.read(root/p)
            refs[relative] = dict(sha256=ref["sha256"], bytes=ref["bytes"])
        deps = {child: visit(child) for child in sorted(value["depends_on"])}
        hashes[name] = digest(dict(version=value["version"], files=refs, dependencies=deps))
        evidence[name] = dict(version=value["version"], files=refs, dependencies=deps)
        visiting.remove(name)
        return hashes[name]

    for name in sorted(components):
        visit(name)
    reader.terminal()
    return {**base(), "command": "component-manifest", "status": "DECLARED_INVENTORY_VERIFIED",
        "component_hashes": hashes, "components": evidence, "evidence": list(reader.refs.values()),
        "inspected_read_bytes": reader.returned_bytes,
        "identity_scope": "EXPLICIT_DECLARED_FILES_AND_GRAPH_ONLY",
        "dynamic_dependency_completeness": "REQUIRES_COMPONENT_REVIEW",
        "legacy_verification_replaced": False}


def performance_summary(reports):
    """Summarize all observed requests, retaining failures and unknown durations."""
    need(type(reports) is list and reports, "OPERATIONS_PERFORMANCE_REPORTS_REQUIRED")
    ids = [r.get("operation_id") for r in reports]
    need(all(type(i) is str and i for i in ids) and len(ids) == len(set(ids)),
         "OPERATIONS_DUPLICATE_OR_MISSING_OPERATION")
    need(all(r.get("schema_version") == delivery.VERSION for r in reports),
         "OPERATIONS_NATIVE_AUDIT_REPORT_REQUIRED")
    groups = {}
    for metric in ("utc_operation_seconds", "operation_action_monotonic_seconds", "supervisor_monotonic_seconds"):
        values = [r.get("timing", {}).get(metric) for r in reports]
        known = [v for v in values if v is not None]
        need(all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in known),
             "OPERATIONS_NONFINITE_DURATION")
        ordered = sorted(known)
        groups[metric] = dict(observed_count=len(known), missing_count=len(values)-len(known),
            median=statistics.median(known) if known else None,
            p95_nearest_rank=ordered[math.ceil(.95*len(ordered))-1] if ordered else None,
            sample_max=max(known) if known else None,
            scope="ALL_OBSERVED_STATUSES_UNKNOWN_DURATIONS_NOT_ZERO_FILLED")
    return {**base(), "command": "performance-report", "operation_count": len(reports),
        "workflow_counts": dict(sorted(Counter(r["workflow_status"] for r in reports).items())),
        "durations": groups, "operation_ids": ids,
        "end_to_end_speedup": None, "sample_selection": "EXPLICIT_OPERATIONS_NOT_ALL_TRADING_DAYS",
        "phase_cpu_io_rss": "UNAVAILABLE_UNLESS_RECORDED_BY_THE_ORIGINAL_PRODUCER",
        "cold_warm_classification": "NOT_INFERRED", "continuous_delivery_accepted": False}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    manifest = sub.add_parser("runtime-manifest")
    manifest.add_argument("--runtime", required=True)
    manifest.add_argument("--registry-root", type=Path)
    manifest.add_argument("--recovered-runtime", action="store_true")
    daily = sub.add_parser("delivery-report")
    daily.add_argument("--job", required=True, type=Path)
    daily.add_argument("--run", required=True, type=Path)
    daily.add_argument("--run-file-sha256", required=True)
    daily.add_argument("--outcomes", type=Path)
    daily.add_argument("--outcomes-file-sha256")
    daily.add_argument("--deadline")
    graph = sub.add_parser("component-manifest")
    graph.add_argument("--source-root", type=Path, required=True)
    graph.add_argument("--spec", type=Path, required=True)
    graph.add_argument("--spec-file-sha256", required=True)
    perf = sub.add_parser("performance-report")
    perf.add_argument("--operation", type=Path, action="append", required=True)
    perf.add_argument("--state-root", type=Path, required=True)
    for child in (manifest, daily, graph, perf):
        child.add_argument("--output", required=True, type=Path)
    for name in ROUTES:
        route = sub.add_parser(name, help="显式版本入口；使用 -- 分隔原入口参数")
        route.add_argument("arguments", nargs=argparse.REMAINDER)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command in ROUTES:
        # Exact argument forwarding. No new defaults, request keys, registry,
        # budgets, shell interpolation, fallback or automatic retry.
        import importlib
        arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
        return importlib.import_module(ROUTES[args.command]).main(arguments)
    session = Inspection()
    extra_check = lambda: None
    try:
        if args.command == "runtime-manifest":
            if args.recovered_runtime:
                from scripts import codex_runtime_recovery_v1 as recovery
                runtime = recovery.load_runtime(args.runtime, args.registry_root)
            else:
                runtime = workspace.load_runtime(args.runtime, args.registry_root)
            result = runtime_manifest(runtime)
            session.charge(result["inspected_read_bytes"])
            extra_check = runtime.check_binding
        elif args.command == "delivery-report":
            result, session = combined_delivery(args.job, args.run, args.run_file_sha256,
                outcomes_path=args.outcomes, outcomes_sha=args.outcomes_file_sha256,
                deadline=args.deadline, inspection=session)
        elif args.command == "component-manifest":
            spec, _ = session.snapshot(args.spec, args.spec_file_sha256)
            result = component_identity(args.source_root, spec)
            session.charge(result["inspected_read_bytes"])
        else:
            need(len(args.operation) <= 64, "OPERATIONS_MAXIMUM_64_REQUESTS")
            reader = delivery._Reader(256*1024**2, 120)
            reports = [delivery._operation(reader, path, state_root=args.state_root)
                       for path in args.operation]
            reader.terminal()
            session.charge(reader.returned_bytes)
            result = performance_summary(reports)
            result["evidence"] = list(reader.refs.values())
            session.absent.update(str(p) for p in reader.absent)
        for ref in result.get("evidence", []):
            session.remember(ref)
        session.absent.update(result.get("absent_controls", []))

        def terminal():
            session.terminal()
            extra_check()

        terminal()
        result["command_exit_code"] = 0
        report = io.publish_new(args.output, result, validate=terminal)
        print(json.dumps(dict(schema_version=VERSION, report=report,
            command_exit_code=0, source_reauthenticated_now=False), ensure_ascii=False))
        return 0
    except Exception as error:
        reason = str(error)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,160}", reason):
            reason = "OPERATIONS_EVIDENCE_REJECTED_OR_UNAVAILABLE"
        print(json.dumps(dict(schema_version=VERSION, status="INSPECTION_FAILED",
            reason=reason, command_exit_code=2)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
