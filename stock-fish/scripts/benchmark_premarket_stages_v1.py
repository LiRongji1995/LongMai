"""Explicit historical core/theme stage experiments with a common actual cutoff.

This is not a daily request, source recovery, online collection or service
deployment. Each fresh child authenticates every original again and mints its
own NativeCore. A common cutoff is the actual UTC instant when this experiment
was admitted, never a frozen clock or an assertion of historical availability.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import benchmark_real_premarket_v1 as previous
from scripts import benchmark_real_premarket_v2 as guarded
from stockfish_core.yaogu import premarket_workflow_v1 as original
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, json_bytes, strict_json

VERSION = "HISTORICAL_PREMARKET_STAGE_BENCHMARK_V1"
SCOPE = "HISTORICAL_STAGE_BENCHMARK_NOT_DAILY_DELIVERY_OR_RETRY_OR_ONLINE_COLLECTION"
PROFILES = ("reference", "canonical")
SUCCESS = ("COMPLETE",)
MAX_EXPERIMENT_BYTES = 2 * 1024**3
FAILURE_RECEIPT_BYTES = previous.FAILURE_RECEIPT_BYTES
CORE_KEYS = ("sources", "market_input", "whole_run", "whole_strategy_result",
             "formal_csv", "observation_view", "normalization_report")
THEME_KEYS = ("whole_theme_parent", "theme_context", "theme_acquisition")
_digest, _rss, _clock = previous._digest, previous._rss, previous._clock
_document, _read_reference = previous._document, previous._read_reference
_references, _terminal, _meter = guarded._references, guarded._terminal, guarded._meter


def _need(value, reason):
    if not value:
        raise ValueError(VERSION + ":" + reason)


def _save(path, value, *, on_create=None):
    previous._save(path, value, on_create=on_create)


def _configuration(value, arm_output_mib=None):
    config = previous._offline_configuration(value)
    _need(config["limits"]["max_seconds"] <= 10800, "STAGE_MAXIMUM_10800_SECONDS")
    if arm_output_mib is not None:
        _need(type(arm_output_mib) is int and arm_output_mib > 0
              and arm_output_mib * 1024**2 <= config["limits"]["output_bytes"],
              "ARM_OUTPUT_MAY_ONLY_TIGHTEN_ORIGINAL_LIMIT")
        config["limits"]["output_bytes"] = arm_output_mib * 1024**2
    return config


def _order(values):
    values = list(values)
    _need(values in (["reference"], ["canonical"],
                     ["reference", "canonical", "canonical", "reference"]),
          "EXPLICIT_SINGLE_ARM_OR_ABBA_REQUIRED")
    return values


def _collections(capture, themes, scope, cutoff):
    _need(scope in ("core", "core-theme"), "KNOWN_STAGE_SCOPE_REQUIRED")
    _need(capture.get("schema_version") == "PREMARKET_CAPTURE_COLLECTION_V1"
          and capture.get("coverage_type") in ("PROSPECTIVE_CAPTURED", "RECONSTRUCTED_HISTORICAL")
          and capture.get("ready_for_core") is True
          and isinstance(capture.get("core_records"), list) and capture["core_records"],
          "FULL_REAL_SAVED_CAPTURE_COLLECTION_REQUIRED")
    observed, target = (date.fromisoformat(capture[key]) for key in ("observation_date", "target_session"))
    _need(observed < target and target <= _clock(cutoff).astimezone(original.SH).date(),
          "EXPLICIT_HISTORICAL_SESSION_REQUIRED")
    _need(len(capture.get("price_sessions", ())) == 25
          and capture["price_sessions"][-1] == str(observed)
          and len(set(capture["price_sessions"])) == 25, "FULL_25_CORE_SESSIONS_REQUIRED")
    if scope == "core-theme":
        _need(isinstance(themes, dict) and themes.get("schema_version") == "PREMARKET_THEME_SOURCE_COLLECTION_V1"
              and themes.get("source_capabilities_issued") == 0
              and isinstance(themes.get("context_captures"), list)
              and isinstance(themes.get("raw_receipt_sources"), list), "SAVED_THEME_COLLECTION_REQUIRED")
    else:
        _need(themes is None, "CORE_SCOPE_MUST_NOT_HIDE_THEME_WORK")
    return str(observed), str(target)


def _script_refs(charge):
    pending = {Path(__file__).resolve(), ROOT / "scripts/codex_premarket_fast_v1.py"}
    result = {}
    while pending:
        path = min(pending)
        pending.remove(path)
        if str(path) in result:
            continue
        _need(len(result) < 4096 and path.parent == ROOT / "scripts", "SCRIPT_CLOSURE_BOUND")
        reference = original._ref(path, charge)
        raw = _read_reference(reference, charge)
        result[str(path)] = reference
        for node in ast.walk(ast.parse(raw, filename=str(path))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names if alias.name.startswith("scripts.")]
            elif isinstance(node, ast.ImportFrom):
                if node.module == "scripts":
                    names = ["scripts." + alias.name for alias in node.names]
                elif node.module and node.module.startswith("scripts."):
                    names = [node.module]
                elif node.level:
                    raise ValueError("RELATIVE_SCRIPT_IMPORT_UNSUPPORTED")
            for name in names:
                _need(all(part.isidentifier() for part in name.split(".")), "SCRIPT_IMPORT_IDENTITY")
                pending.add(ROOT.joinpath(*name.split(".")).with_suffix(".py"))
    return list(result.values())


@contextmanager
def _readonly_job_lock(state):
    """Coordinate with the existing native writer without rewriting its lock."""
    _need(os.name == "posix", "POSIX_READONLY_LOCK_REQUIRED")
    import fcntl
    path = Path(state) / "premarket.lock"
    before = original.fingerprint(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        actual = os.fstat(fd)
        _need((actual.st_dev, actual.st_ino) == (before["st_dev"], before["st_ino"]), "LOCK_REPLACED")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
        _need(original.fingerprint(path) == before, "LOCK_CHANGED")
    finally:
        os.close(fd)


def _run_projection(run, cutoff, started, finished):
    _need(_clock(run["decision_cutoff"]) == _clock(cutoff), "COMMON_ACTUAL_CUTOFF_REQUIRED")
    _need(_clock(cutoff) <= _clock(started) <= _clock(run["generated_at"]) <= _clock(finished),
          "RUN_GENERATION_OUTSIDE_REAL_STAGE")
    _need(run.get("content_hash") == _digest({key: value for key, value in run.items() if key != "content_hash"}),
          "CANONICAL_RUN_CONTENT_HASH")
    return {key: value for key, value in run.items() if key not in ("generated_at", "content_hash")}


def _view_projection(view, run):
    _need(view.get("run_id") == run["run_id"] and view.get("generated_at") == run["generated_at"]
          and view.get("export_view", {}).get("source_content_hash") == run["content_hash"],
          "FORMAL_VIEW_PARENT_BINDING")
    _need(view.get("view_content_hash") == _digest({key: value for key, value in view.items() if key != "view_content_hash"}),
          "FORMAL_VIEW_CONTENT_HASH")
    return {**{key: value for key, value in view.items() if key not in ("generated_at", "view_content_hash", "export_view")},
            "export_view": {key: value for key, value in view["export_view"].items() if key != "source_content_hash"}}


def _theme_provenance(provenance, theme, charge):
    """Keep the complete audit and counters; omit only fresh journal 7FP."""
    value, excluded = deepcopy(provenance), {}
    audit = value["audit_manifest"]
    _need(audit["complete_allowed_row_audit"] is True, "FULL_THEME_AUDIT_REQUIRED")
    root = Path(theme.artifacts["native-theme-parent.json.gz"]["path"]).parent / "price-journal"
    _need({path.name for path in root.iterdir()} == {*audit["files"], "manifest.json"}, "JOURNAL_FILE_SET")
    manifest_ref = next(row for row in theme.validation_references if row["path"] == str(root / "manifest.json"))
    _need(_document(manifest_ref, charge) == provenance["audit_manifest"], "JOURNAL_MANIFEST_BINDING")
    for name, ref in audit["files"].items():
        _need(Path(name).name == name and set(ref) == {"bytes", "sha256", "fp"}, "JOURNAL_REFERENCE_SCHEMA")
        original._verify({"path": str(root / name), **ref}, charge)
        excluded[name] = ref.pop("fp")
    return value, excluded


def _proof(owner, theme, capture, themes, scope, cutoff, started, finished, charge):
    artifacts = owner.artifacts
    _need(set(artifacts) == {"canonical-run.json.gz", "input.json.gz", "manifest.json", "observation.json", *previous.CSV_NAMES},
          "ALL_EIGHT_CORE_ARTIFACTS_REQUIRED")
    run = _document(artifacts["canonical-run.json.gz"], charge)
    market = _document(artifacts["input.json.gz"], charge)
    manifest = _document(artifacts["manifest.json"], charge)
    # Existing guard verifies the four universe assembly clocks, normalizer and
    # full input hash. Unlike the earlier benchmark, we remove none of them.
    previous._market_projection(market, run, manifest)
    semantic_run = _run_projection(run, cutoff, started, finished)
    arm = run["results"][previous.STRATEGY]
    csv_values = {name: previous._csv_values(_read_reference(artifacts[name], charge), run["run_id"])
                  for name in previous.CSV_NAMES}
    all_ids = [row["instrument_id"] for row in csv_values["all.csv"]["rows"]]
    _need(len(all_ids) == len(set(all_ids)) == len(arm["eligible_pool_ids"])
          and set(all_ids) == set(arm["eligible_pool_ids"]), "FULL_ELIGIBLE_CSV_REQUIRED")
    view = _document(artifacts["observation.json"], charge)
    values = dict(sources=dict(capture=capture, theme=themes), market_input=market,
                  whole_run=semantic_run, whole_strategy_result=run["results"], formal_csv=csv_values,
                  observation_view=_view_projection(view, run), normalization_report=manifest["normalization_report"])
    excluded = {"canonical-run/generated_at": run["generated_at"], "canonical-run/content_hash": run["content_hash"],
                "observation/generated_at": view["generated_at"], "observation/view_content_hash": view["view_content_hash"],
                "observation/export_view/source_content_hash": view["export_view"]["source_content_hash"]}
    if scope == "core-theme":
        _need(theme is not None, "COMPLETE_THEME_STAGE_REQUIRED")
        document = theme.to_dict()
        _need(document["status"] in ("AVAILABLE", "PARTIAL") and document.get("source_verified") is True
              and document.get("failure") is None, "THEME_FAILED_OR_UNAVAILABLE")
        parent_ref = theme.artifacts.get("native-theme-parent.json.gz")
        _need(parent_ref is not None, "COMPLETE_67_THEME_PARENT_REQUIRED")
        parent = _document(parent_ref, charge)
        _need(parent["source_input_hash"] == run["input_hash"] and parent["decision_cutoff"] == cutoff
              and len(parent["computed_exchange_sessions"]) == 67
              and parent["computed_exchange_sessions"][-1] == capture["observation_date"], "THEME_INPUT_AND_67_BINDING")
        _need(parent["context_content_hash"] == _digest({k: v for k, v in parent.items() if k != "context_content_hash"}),
              "THEME_PARENT_CONTENT_HASH")
        provenance = parent["execution_provenance"]
        _need(provenance["complete_allowed_row_audit"] is True
              and provenance["source_terminal_callback_status"] == "LIVE_CALLBACK_COMPLETED", "THEME_COMPLETE_AUDIT_REQUIRED")
        provenance_projection, journal_fingerprints = _theme_provenance(provenance, theme, charge)
        values["whole_theme_parent"] = {**{k: v for k, v in parent.items() if k != "context_content_hash"},
                                        "execution_provenance": provenance_projection}
        for key in ("generated_at", "completed_at"):
            _need(_clock(started) <= _clock(document[key]) <= _clock(finished), "THEME_REAL_GENERATION_CLOCK")
            excluded["theme-context/" + key] = document[key]
        _need(document["run_id"] == run["run_id"] and document["context_cutoff"] == cutoff
              and document["core_decision_cutoff"] == cutoff, "THEME_DOCUMENT_BINDING")
        context = {k: v for k, v in document.items() if k not in ("generated_at", "completed_at")}
        diagnostic = deepcopy(context.get("diagnostic"))
        if diagnostic is not None:
            _need(diagnostic.get("theme_context_hash") == parent["context_content_hash"], "DIAGNOSTIC_PARENT_HASH")
            _need(diagnostic.get("diagnostic_content_hash") == _digest({k: v for k, v in diagnostic.items()
                if k != "diagnostic_content_hash"}), "DIAGNOSTIC_CONTENT_HASH")
            excluded["theme-context/diagnostic/diagnostic_content_hash"] = diagnostic.pop("diagnostic_content_hash")
            excluded["theme-context/diagnostic/theme_context_hash"] = diagnostic.pop("theme_context_hash")
            context["diagnostic"] = diagnostic
        values["theme_context"] = context
        phase = "COMPLETE" if themes["parent_acquisition"]["status"] == "COMPLETE_CAPTURE_NOT_THEME_READY" else "PARTIAL"
        values["theme_acquisition"] = guarded._theme_acquisition(themes, arm, phase, charge)
        excluded["theme-parent/context_content_hash"] = parent["context_content_hash"]
        excluded["theme-parent/execution_provenance/audit_manifest/files/7FP"] = journal_fingerprints
    _terminal(_references(artifacts, theme.artifacts if theme is not None else None,
                          theme.validation_references if theme is not None else None), charge)
    return dict(hashes={key: _digest(value) for key, value in values.items()}, run_id=run["run_id"],
                observation_date=capture["observation_date"], target_session=capture["target_session"],
                common_actual_cutoff=cutoff, scope=scope, excluded_values=excluded,
                whole_strategy_results_compared=True, source_clocks_removed=False, full_input_compared=True,
                formal_export_byte_equality_claimed=False)


def compare(reference, candidate):
    reasons = []
    for name, item in (("reference", reference), ("candidate", candidate)):
        proof = item.get("comparison_proof")
        if item.get("status") != "COMPLETE" or not isinstance(proof, dict):
            reasons.append(name.upper() + "_NOT_COMPLETE")
            continue
        keys = (*CORE_KEYS, *THEME_KEYS) if proof.get("scope") == "core-theme" else CORE_KEYS
        if proof.get("scope") not in ("core", "core-theme") or any(key not in proof for key in
                ("common_actual_cutoff", "observation_date", "target_session", "run_id")):
            reasons.append(name.upper() + "_MISSING_SCOPE_IDENTITY")
        if set(proof.get("hashes", {})) != set(keys) or any(not isinstance(v, str) or not re.fullmatch("[0-9a-f]{64}", v)
                                                                   for v in proof["hashes"].values()):
            reasons.append(name.upper() + "_INCOMPLETE_PROOF")
    if not reasons:
        a, b = reference["comparison_proof"], candidate["comparison_proof"]
        reasons += ["DIFF:" + key for key in ("scope", "common_actual_cutoff", "observation_date", "target_session", "run_id") if a[key] != b[key]]
        reasons += ["DIFF:" + key for key in a["hashes"] if a["hashes"][key] != b["hashes"].get(key)]
    result = dict(comparable=not reasons, reasons=reasons, scope=SCOPE, end_to_end_speedup_established=False)
    if not reasons:
        a, b = reference["stage_wall_seconds"], candidate["stage_wall_seconds"]
        _need(all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in (a, b)),
              "POSITIVE_STAGE_TIMES_REQUIRED")
        result.update(stage_latency_reduction_percent=(a-b)/a*100, stage_speed_ratio=a/b)
    return result


def _worker_guard(directory):
    """Worker writes are confined to its new output; offline means no socket."""
    root = Path(directory).resolve(strict=True)
    def owned(path):
        _need(isinstance(path, (str, bytes, os.PathLike))
              and Path(os.fsdecode(path)).resolve().is_relative_to(root), "WORKER_WRITE_OUTSIDE_OWNED_OUTPUT")
    def guard(event, args):
        if event in ("socket.__new__", "socket.connect", "socket.getaddrinfo", "socket.sendto", "subprocess.Popen",
                     "os.fork", "os.forkpty", "os.posix_spawn", "os.system"):
            raise RuntimeError("STAGE_BENCHMARK_NETWORK_OR_CHILD_FORBIDDEN")
        if event == "open" and not isinstance(args[0], int):
            mode, flags = args[1], args[2]
            if (isinstance(mode, str) and any(mark in mode for mark in "wax+")) or \
                    (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                owned(args[0])
        elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.chown", "os.truncate", "os.utime"):
            owned(args[0])
        elif event in ("os.rename", "os.link", "os.symlink"):
            owned(args[0])
            owned(args[1])
    return guard


def _worker(request_reference, directory, profile):
    _need(sys.flags.isolated and sys.dont_write_bytecode and sys.pycache_prefix
          and not Path(sys.pycache_prefix).exists(), "FRESH_ISOLATED_PROCESS_REQUIRED")
    _need(threading.active_count() == 1 and os.name == "posix" and os.getpgrp() == os.getpid(), "OWNED_SINGLE_THREAD_PROCESS_REQUIRED")
    _need(directory == directory.resolve(strict=True) and directory.parent == Path(request_reference["path"]).parent,
          "WORKER_OWNED_EXPERIMENT_DIRECTORY")
    sys.addaudithook(_worker_guard(directory))
    started = time.perf_counter()
    _need(request_reference["bytes"] <= 16*1024**2, "BOUNDED_EXPERIMENT_CONTROL_REQUIRED")
    initial_reads = [0]
    def initial_charge(count):
        initial_reads[0] += count
        _need(initial_reads[0] <= 64*1024**2, "BOUNDED_EXPERIMENT_CONTROL_READS")
    request = _document(request_reference, initial_charge)
    config = _configuration(request["original_configuration"], request["arm_output_mib"])
    _need(config == request["configuration"] and profile in PROFILES and request["schema_version"] == VERSION,
          "WORKER_IDENTITY_AND_EFFECTIVE_BUDGET")
    charge, read_count = _meter(config, started, initial=initial_reads[0])
    _terminal([request_reference, *request["references"]], charge)
    _need(_document(request["configuration_source"], charge) == request["original_configuration"],
          "ORIGINAL_CONFIGURATION_BINDING")
    capture = _document(request["capture_summary"], charge)
    themes = _document(request["theme_summary"], charge) if request["theme_summary"] is not None else None
    cutoff, scope = request["common_actual_cutoff"], request["stage_scope"]
    observed, target = _collections(capture, themes, scope, cutoff)
    _need(_clock(cutoff) <= datetime.now(timezone.utc), "FUTURE_EXPERIMENT_CUTOFF")
    from scripts import codex_premarket_fast_v1 as fast
    from stockfish_core.yaogu.premarket_native_core_v1 import run_core
    from stockfish_core.yaogu.premarket_theme_context_v1 import attach_theme_context
    job = directory / "job"
    job.mkdir(mode=0o700)
    budget = Budget(config["limits"], job_dir=job, cache_dir=Path(config["state_root"]) / "cache")
    stage_started, cpu = time.perf_counter(), time.process_time()
    actual_started = datetime.now(timezone.utc).isoformat()
    measurements, owner, theme = [], None, None
    result = dict(schema_version=VERSION, engine=profile, status="FAILED", scope=SCOPE,
                  stage_scope=scope, comparison_proof=None, started_at=actual_started,
                  common_actual_cutoff=cutoff, phase_measurements=measurements)
    try:
        with fast.execution_scope(profile, budget) as execution_metrics:
            for phase in (("core", "theme") if scope == "core-theme" else ("core",)):
                phase_started, phase_cpu = time.perf_counter(), time.process_time()
                before = deepcopy(budget.metrics["returned_read_bytes"])
                with budget.phase(phase), budget.meter_files():
                    if phase == "core":
                        owner = run_core(captures=capture["core_records"], observation_date=observed,
                            target_session=target, cutoff=cutoff, output_dir=job/"core", budget=budget,
                            coverage_type=capture["coverage_type"])
                        owner.validate()
                    else:
                        theme = attach_theme_context(owner, context_captures=themes["context_captures"],
                            raw_receipt_sources=themes["raw_receipt_sources"], context_cutoff=cutoff,
                            output_dir=job/"theme", budget=budget,
                            requested_industry_nodes=themes["requested_industry_nodes"],
                            requested_concept_symbols=themes["requested_concept_symbols"],
                            parent_acquisition=themes["parent_acquisition"])
                        _need(theme.to_dict()["status"] in ("AVAILABLE", "PARTIAL"), "THEME_STAGE_NOT_COMPLETE")
                        theme.validate()
                measurements.append(dict(phase=phase, wall_seconds=time.perf_counter()-phase_started,
                    cpu_seconds=time.process_time()-phase_cpu,
                    returned_read_bytes={k: v-before[k] for k, v in budget.metrics["returned_read_bytes"].items()}))
            with budget.phase("theme" if theme is not None else "core"), budget.meter_files():
                (theme or owner).validate()
        result.update(stage_wall_seconds=time.perf_counter()-stage_started,
            stage_cpu_seconds=time.process_time()-cpu, execution_metrics=deepcopy(execution_metrics),
            execution_metrics_are_inclusive_not_additive=True, completed_at=datetime.now(timezone.utc).isoformat(),
            metrics=budget.metrics, job_output_bytes=original.directory_bytes(job))
        _need(all(value == 0 for value in budget.metrics["calls"].values()), "ZERO_NETWORK_COUNTERS_REQUIRED")
        proof_started = time.perf_counter()
        result["comparison_proof"] = _proof(owner, theme, capture, themes, scope, cutoff,
            actual_started, result["completed_at"], charge)
        result["artifact_references"] = _references(owner.artifacts, theme.artifacts if theme else None,
                                                   theme.validation_references if theme else None)
        _terminal([request_reference, *request["references"]], charge)
        result.update(status="COMPLETE", comparison_seconds=time.perf_counter()-proof_started)
    except Exception as error:
        result.update(status="FAILED", comparison_proof=None,
            error=dict(type=type(error).__name__, reason=str(error)[:2000]), metrics=budget.metrics,
            elapsed_until_failure_seconds=time.perf_counter()-stage_started)
    result.update(supervisor_returned_read_bytes=read_count[0], process_peak_rss_bytes=_rss(),
                  process_wall_seconds=time.perf_counter()-started, process_cpu_seconds=time.process_time())
    _save(directory/"measurement.json", result)
    return 0 if result["status"] == "COMPLETE" else 4


def _receipt_paths(directory, engine):
    return (directory / "process-result.json", directory.parent / (directory.name+"-receipt.json"))


# Keep the already reviewed two-receipt publication/rollback implementation,
# with this module's identity and numbered ABBA directories bound privately.
from types import FunctionType, SimpleNamespace
for _name in ("_arm_output_bytes", "_reserve_failure_receipts"):
    _source = getattr(previous, _name)
    _copy = FunctionType(_source.__code__, globals(), _source.__name__, _source.__defaults__, _source.__closure__)
    _copy.__kwdefaults__ = _source.__kwdefaults__
    globals()[_name] = _copy


def _check_parent_budget(config, directory, engine, started, additional_bytes=0):
    previous._need(time.perf_counter()-started <= config["limits"]["max_seconds"], "PARENT_RECEIPT_TIME_LIMIT")
    _need(_arm_output_bytes(directory, engine)+additional_bytes <= config["limits"]["output_bytes"],
          "PARENT_RECEIPT_OUTPUT_LIMIT")
    _need(original.directory_bytes(directory.parent)+additional_bytes <= MAX_EXPERIMENT_BYTES,
          "EXPERIMENT_OUTPUT_2GIB_LIMIT")
    _need(time.perf_counter()-started <= config["limits"]["max_seconds"], "PARENT_RECEIPT_TIME_LIMIT")


def _publish_process_result(config, directory, engine, started, result, source_refs=(), *, charge=None, read_count=None):
    """Seal under the same source identity, read meter and reserved rollback.

    The full SHA pass happens inside V1's guarded publication. Every subsequent
    quota check brackets all references with seven-field physical checks,
    including the final check after both candidate files have been renamed.
    Historical experiments deliberately have no current-session admission gate.
    """
    reference_error = None
    try:
        final_refs = _references(source_refs, result.get("artifact_references"))
    except BaseException as error:
        reference_error, final_refs = error, []
    # Defer even meter creation to the guarded verification/check so failures
    # consume the preallocated FAILED receipts rather than escaping unsealed.
    def charged(size):
        nonlocal charge, read_count
        if charge is None:
            charge, read_count = _meter(config, started, initial=result.get("parent_returned_read_bytes", 0))
        charge(size)
        if read_count is not None:
            result["parent_returned_read_bytes"] = read_count[0]
    def verify(reference):
        if reference_error is not None:
            raise reference_error
        charged(0)
        original._verify(reference, charged)
    def physical():
        charged(0)
        for reference in final_refs:
            _need(original.fingerprint(reference["path"]) == reference["fp"], "PARENT_FINAL_REFERENCE_CHANGED")
        charged(0)
    def check(*args):
        physical()
        _check_parent_budget(*args)
        physical()
    proxy = SimpleNamespace(**vars(original))
    proxy._verify = verify
    source = previous._publish_process_result
    publish = FunctionType(source.__code__, {**globals(), "original": proxy, "_check_parent_budget": check},
                           source.__name__, source.__defaults__, source.__closure__)
    publish.__kwdefaults__ = source.__kwdefaults__
    return publish(config, directory, engine, started, result, [None] if reference_error is not None else final_refs)


def _run_process(config, directory, profile, request_path, references):
    started, peak = time.perf_counter(), 0
    charge, parent_reads = _meter(config, started)
    _reserve_failure_receipts(config, directory, profile, started)
    result, failure = None, None
    with tempfile.TemporaryDirectory(prefix="historical-stage-bytecode-") as temporary:
        command = [sys.executable, "-I", "-B", "-X", "pycache_prefix="+str(Path(temporary)/"absent"),
            str(Path(__file__).resolve()), "_worker", "--request-reference", json_bytes(next(
                ref for ref in references if ref["path"] == str(request_path))).decode(),
            "--directory", str(directory), "--profile", profile]
        environment = dict(os.environ)
        for name in ("PYTHONPATH", "PYTHONHOME"):
            environment.pop(name, None)
        with (directory/"console.txt").open("xb") as console:
            process, code = None, None
            try:
                process = subprocess.Popen(command, cwd=ROOT, stdout=console, stderr=subprocess.STDOUT,
                                           env=environment, start_new_session=True)
                while process.poll() is None:
                    _check_parent_budget(config, directory, profile, started)
                    try:
                        peak = max(peak, original._tree_sample(process.pid, charge))
                    except Exception:
                        if process.poll() is None:
                            raise
                    _need(peak <= config["limits"]["tree_rss_bytes"], "PROCESS_TREE_RSS_LIMIT")
                    time.sleep(.25)
                code = process.wait()
                _need(not original._owned_group_members(process.pid, charge), "OWNED_DESCENDANTS_REMAIN")
                _check_parent_budget(config, directory, profile, started)
                path = directory/"measurement.json"
                _need(code in (0, 4) and path.exists(), "CHILD_DID_NOT_PUBLISH_MEASUREMENT")
                result = original._read_control(path, charge)
                _need(result.get("schema_version") == VERSION and result.get("engine") == profile, "CHILD_RESULT_IDENTITY")
                if code != 0:
                    result.update(status="FAILED", comparison_proof=None)
            except BaseException as error:
                if process is not None:
                    original._stop_owned(process)
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                code = process.returncode if process is not None else None
                failure = dict(type=type(error).__name__, reason=str(error)[:2000])
            if result is None or failure is not None:
                result = dict(schema_version=VERSION, engine=profile, status="FAILED", comparison_proof=None,
                              process_failure=failure)
            result.update(external_exit_code=code, external_wall_seconds=time.perf_counter()-started,
                sampled_peak_tree_rss_bytes=peak, external_worker_reaped=process is None or process.poll() is not None)
    result["parent_returned_read_bytes"] = parent_reads[0]
    return _publish_process_result(config, directory, profile, started, result, references,
                                   charge=charge, read_count=parent_reads)


def benchmark(config, capture_path, theme_path, scope, order, output, *, configuration_source=None, arm_output_mib=None):
    source_config = deepcopy(config)
    config, order = _configuration(config, arm_output_mib), _order(order)
    output = Path(output).expanduser().absolute()
    state = Path(config["state_root"])
    _need(output.parent == output.parent.resolve(strict=True) and not output.exists() and not output.is_symlink(),
          "NEW_UNALIASED_OWNED_OUTPUT_REQUIRED")
    _need(not output.is_relative_to(state) and not state.is_relative_to(output)
          and not output.is_relative_to(ROOT), "OUTPUT_SEPARATE_FROM_SOURCE_AND_STATE")
    with _readonly_job_lock(state):
        started = time.perf_counter()
        charge, _ = _meter(config, started)
        _need(configuration_source is not None and _document(configuration_source, charge) == source_config,
              "ORIGINAL_CONFIGURATION_REFERENCE_REQUIRED")
        capture_ref = original._ref(capture_path, charge)
        theme_ref = original._ref(theme_path, charge) if theme_path is not None else None
        capture = _document(capture_ref, charge)
        themes = _document(theme_ref, charge) if theme_ref is not None else None
        cutoff = datetime.now(timezone.utc).isoformat()
        _collections(capture, themes, scope, cutoff)
        references = _references(capture, themes, capture_ref, theme_ref, configuration_source,
                                 original._code_refs(charge), _script_refs(charge))
        _terminal(references, charge)
        output.mkdir(mode=0o700)
        request = dict(schema_version=VERSION, configuration=config, original_configuration=source_config,
            configuration_source=configuration_source, arm_output_mib=arm_output_mib,
            budget_override_role="ONLY_EXPLICIT_PER_ARM_OUTPUT_TIGHTENING_ALL_OTHER_LIMITS_UNCHANGED",
            capture_summary=capture_ref,
            theme_summary=theme_ref, common_actual_cutoff=cutoff, stage_scope=scope, order=order,
            references=references, scope=SCOPE, failed_daily_job_retried=False,
            system_clock_frozen=False, historical_source_availability_claimed=False)
        request_path = output/"experiment.json"
        _save(request_path, request)
        references = _references(references, original._ref(request_path, charge))
        results = []
        for index, profile in enumerate(order, 1):
            _need(original.directory_bytes(output) < MAX_EXPERIMENT_BYTES, "EXPERIMENT_OUTPUT_2GIB_LIMIT")
            directory = output/(str(index).zfill(2)+"-"+profile)
            directory.mkdir(mode=0o700)
            result = _run_process(config, directory, profile, request_path, references)
            results.append(result)
            if result["status"] != "COMPLETE" or result.get("comparison_proof") is None:
                break
            if index > 1 and not compare(results[0], result)["comparable"]:
                break
        comparisons = [compare(results[0], value) for value in results[1:]]
        summary = dict(schema_version=VERSION, scope=SCOPE, stage_scope=scope,
            common_actual_cutoff=cutoff, results=results, comparisons=comparisons,
            requested_order=order, unrun_profiles=order[len(results):],
            end_to_end_speedup_established=False, online_collection_measured=False,
            gateway_admission_measured=False, operating_system_cache_controlled=False,
            repeated_order_bias_controlled=order == ["reference", "canonical", "canonical", "reference"] and len(results) == 4)
        if len(results) == 4 and all(row["status"] == "COMPLETE" for row in results) and all(row["comparable"] for row in comparisons):
            medians = {profile: statistics.median(row["stage_wall_seconds"] for row in results if row["engine"] == profile)
                       for profile in PROFILES}
            summary.update(stage_wall_medians=medians,
                stage_latency_reduction_percent=(medians["reference"]-medians["canonical"])/medians["reference"]*100,
                stage_speed_ratio=medians["reference"]/medians["canonical"])
        _need(original.directory_bytes(output)+len(json_bytes(summary)) <= MAX_EXPERIMENT_BYTES, "FINAL_EXPERIMENT_OUTPUT_2GIB_LIMIT")
        _save(output/"summary.json", summary)
        return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", allow_abbrev=False)
    run.add_argument("--config", required=True)
    run.add_argument("--capture-summary", required=True)
    run.add_argument("--theme-source-summary")
    run.add_argument("--scope", choices=("core", "core-theme"), default="core")
    run.add_argument("--order", choices=PROFILES, nargs="+", default=["reference", "canonical", "canonical", "reference"])
    run.add_argument("--output", required=True)
    run.add_argument("--arm-output-mib", type=int,
                     help="Explicit per-arm output cap; may only tighten the original config limit")
    worker = commands.add_parser("_worker", allow_abbrev=False)
    worker.add_argument("--request-reference", required=True)
    worker.add_argument("--directory", required=True)
    worker.add_argument("--profile", choices=PROFILES, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "_worker":
            return _worker(strict_json(args.request_reference), Path(args.directory), args.profile)
        source = original._ref(Path(args.config).expanduser().resolve(strict=True))
        result = benchmark(_document(source, lambda _: None), args.capture_summary, args.theme_source_summary,
                           args.scope, args.order, args.output, configuration_source=source,
                           arm_output_mib=args.arm_output_mib)
        print(json_bytes(dict(output=str(Path(args.output).absolute()),
            stage_latency_reduction_percent=result.get("stage_latency_reduction_percent"),
            completed_arms=sum(row["status"] == "COMPLETE" for row in result["results"]),
            unrun_profiles=result["unrun_profiles"], scope=SCOPE)).decode())
        return 0 if not result["unrun_profiles"] and all(row["status"] == "COMPLETE" for row in result["results"]) \
            and all(row["comparable"] for row in result["comparisons"]) else 4
    except Exception as error:
        print(json_bytes(dict(status="FAILED", scope=SCOPE, error=type(error).__name__, reason=str(error)[:2000])).decode())
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
