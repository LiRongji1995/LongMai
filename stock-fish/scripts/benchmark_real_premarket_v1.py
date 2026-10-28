"""Explicit cache-only, fresh-process comparison of native premarket workers.

This is an engineering experiment, not a daily request or an implicit retry.
No source is copied, clock frozen, network adapter replaced or limit enlarged.
Each arm keeps its actual cutoff/run_id. Equal source/input-data and numerical
projections are required before a timing ratio is published. Gateway admission,
online acquisition and the ordinary supervisor are outside this measurement.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import codex_premarket_shared_v1 as shared
from stockfish_core.yaogu import premarket_workflow_v1 as original
from stockfish_core.yaogu.contracts import MarketInput, UniverseSnapshot
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, JobLock, json_bytes, strict_json

VERSION = "REAL_CACHE_ONLY_PREMARKET_BENCHMARK_V1"
ENGINES = ("original-ref", "original", "shared")
STRATEGY = "SHORT_HORIZON_STRENGTH_R2"
SUCCESS = ("COMPLETE", "COMPLETED_WITH_UNKNOWNS")
CSV_NAMES = ("all.csv", "observation.csv", "focus.csv", "unselected.csv")
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
COMPARISON_KEYS = ("capture_sources", "market_input", "security_selection", "formal_csv_values",
                   "core_numeric_leaves", "theme_values", "display_counts", "phases")
ASSEMBLY_CLOCKS = ("event_time", "source_published_at", "first_seen_at", "ingested_at")
FAILURE_RECEIPT_BYTES = 8192


def _need(value, reason):
    if not value:
        raise ValueError(VERSION + ":" + reason)


def _digest(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def _save(path, value, *, on_create=None):
    with Path(path).open("xb") as handle:
        if on_create is not None:
            on_create(Path(path), os.fstat(handle.fileno()))
        handle.write(json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def _rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _offline_configuration(value):
    config = original.load_config(overrides=deepcopy(value))
    _need(config["capture"].get("allow_test_fixture", False) is False, "REAL_SOURCES_REQUIRED")
    _need(not config["seed_sources"], "SEED_IMPORT_FORBIDDEN")
    maximum = config["limits"]["max_seconds"]
    _need(type(maximum) in (int, float) and math.isfinite(maximum) and maximum > 0,
          "EXPLICIT_FINITE_TIME_LIMIT_REQUIRED")
    # These original policy switches stop before provider reservations/execution.
    # News inherits capture.offline inside the unchanged full workflow.
    config["capture"].update(offline=True, retry_failed=False)
    config["news"]["offline"] = True
    state = Path(config["state_root"]).expanduser().absolute()
    _need(state == state.resolve(strict=True) and state.is_dir(), "EXISTING_UNALIASED_STATE_REQUIRED")
    for name in ("cache", "cache/index", "cache/raw", "cache/raw-index", "cache/locators"):
        folder = state / name
        _need(folder.is_dir() and not folder.is_symlink(), "EXISTING_CACHE_DIRECTORIES_REQUIRED")
    config["state_root"] = str(state)
    return config


def _instrumented(engine, stages):
    """Local function bindings only; every phase uses the exact native Budget."""
    factory = Budget if engine == "original-ref" else shared._workflow_budget

    def make_budget(*args, **kwargs):
        budget = factory(*args, **kwargs)
        _need(type(budget) is Budget, "ORIGINAL_BUDGET_REQUIRED")
        phase = budget.phase

        @contextmanager
        def measured(name):
            started, cpu = time.perf_counter(), time.process_time()
            before = deepcopy(budget.metrics["returned_read_bytes"])
            outcome = "FAILED"
            try:
                with phase(name):
                    yield budget
                outcome = "COMPLETE"
            finally:
                stages.append(dict(phase=name, status=outcome,
                    wall_seconds=time.perf_counter()-started, cpu_seconds=time.process_time()-cpu,
                    process_peak_rss_bytes_at_end=_rss(),
                    returned_read_bytes={key: value-before.get(key, 0)
                        for key, value in budget.metrics["returned_read_bytes"].items()},
                    job_output_bytes_at_end=original.directory_bytes(budget.job_dir)))
        budget.phase = measured
        return budget

    if engine == "original-ref":
        return shared._clone(original.execute_workflow, {"Budget": make_budget})
    return shared._clone(shared.execute_workflow_shared, {"_workflow_budget": make_budget})


def _read_reference(reference, read_meter=None):
    original._verify(reference, read_meter)
    raw = Path(reference["path"]).read_bytes()
    if read_meter is not None:
        read_meter(len(raw))
    _need(hashlib.sha256(raw).hexdigest() == reference["sha256"]
          and original.fingerprint(reference["path"]) == reference["fp"], "ARTIFACT_CHANGED_DURING_READ")
    return raw


def _document(reference, read_meter=None):
    raw = _read_reference(reference, read_meter)
    if reference["path"].endswith(".gz"):
        raw = gzip.decompress(raw)
        if read_meter is not None:
            read_meter(len(raw))
    return strict_json(raw)


def _csv_values(raw, run_id):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    _need(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames))
          and "run_id" in reader.fieldnames, "FORMAL_CSV_HEADER_REQUIRED")
    rows = list(reader)
    _need(all(row.get("run_id") == run_id and None not in row
              and all(value is not None for value in row.values()) for row in rows),
          "FORMAL_CSV_RUN_OR_ROW_CHANGED")
    return dict(columns=[key for key in reader.fieldnames if key != "run_id"],
                rows=[{key: value for key, value in row.items() if key != "run_id"} for row in rows])


def _numeric_leaves(value, path=()):
    """Keep every numeric/null/bool leaf, including exact numeric strings.

    Whole CSV cells separately retain all exported categorical decisions and
    reasons. This projection is explicitly narrower than full document equality.
    """
    if isinstance(value, dict):
        return [leaf for key in sorted(value) for leaf in _numeric_leaves(value[key], (*path, key))]
    if isinstance(value, list):
        return [leaf for index, item in enumerate(value) for leaf in _numeric_leaves(item, (*path, index))]
    if value is None or type(value) in (bool, int, float) or isinstance(value, str) and NUMBER.fullmatch(value):
        return [[list(path), value]]
    return []


def _clock(value):
    _need(type(value) is str, "AWARE_EXECUTION_CLOCK_REQUIRED")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _need(parsed.utcoffset() is not None, "AWARE_EXECUTION_CLOCK_REQUIRED")
    return parsed


def _market_projection(market, run, manifest):
    """Exclude only four proven R03 assembly clocks at /universe's root.

    R03_SOURCE_ASSEMBLY_V1 constructs these four envelope clocks from its
    ``now`` argument, which NativeCore passes as this run's decision cutoff.
    Securities, bars, receipts and theme clocks are genuine source/business
    evidence and remain byte-for-byte values in the comparison projection.
    Original hashes/IDs/clocks are retained separately, never rewritten.
    """
    _need(set(market) == set(MarketInput.model_fields)
          and isinstance(market["universe"], dict)
          and set(market["universe"]) == set(UniverseSnapshot.model_fields),
          "CANONICAL_MARKET_INPUT_SCHEMA_REQUIRED")
    report = manifest.get("normalization_report", {})
    cutoff = _clock(run["decision_cutoff"])
    full_hash = _digest(market)
    _need(manifest.get("schema_version") == "PREMARKET_NATIVE_CORE_V1"
          and manifest.get("status") == "PASS"
          and report.get("normalization_version") == "R03_SOURCE_ASSEMBLY_V1"
          and _clock(report.get("actual_capture_cutoff")) == cutoff
          and _clock(manifest.get("decision_cutoff")) == cutoff
          and manifest.get("run_id") == run["run_id"]
          and manifest.get("input_hash") == run.get("input_hash") == full_hash
          and run.get("input_id") == "market_"+full_hash,
          "ORIGINAL_NORMALIZATION_AND_INPUT_BINDING_REQUIRED")
    assembly = {key: market["universe"][key] for key in ASSEMBLY_CLOCKS}
    _need(all(_clock(value) == cutoff for value in assembly.values()),
          "UNIVERSE_ENVELOPE_CLOCK_NOT_ACTUAL_ASSEMBLY_CUTOFF")
    projection = {**market, "universe": {key: value for key, value in market["universe"].items()
                                         if key not in ASSEMBLY_CLOCKS}}
    metadata = dict(full_market_input_sha256=full_hash, input_id=run["input_id"],
        run_id=run["run_id"], decision_cutoff=run["decision_cutoff"],
        normalization_version=report["normalization_version"],
        excluded_comparison_values={"/universe/"+key: value for key, value in assembly.items()},
        exclusion_reason="FOUR_R03_ASSEMBLY_ENVELOPE_CLOCKS_EACH_VERIFIED_EQUAL_TO_THIS_ACTUAL_RUN_CUTOFF",
        original_documents_modified=False, source_or_security_or_price_or_theme_clocks_removed=False)
    return projection, metadata


def _proof(completion, job, read_meter=None):
    """Hashes compare actual independent outputs; never rewrite their metadata."""
    _need(completion.get("status") in SUCCESS and completion.get("core_verified") is True,
          "SUCCESSFUL_VERIFIED_CORE_REQUIRED")
    _need(all(completion["phases"].get(key) == "COMPLETE"
              for key in ("capture", "core", "theme_sources", "theme", "presentation")),
          "WHOLE_CORE_AND_THEME_REQUIRED_FOR_COMPARISON")
    calls = completion["metrics"]["calls"]
    _need(all(value == 0 for value in calls.values()), "CACHE_ONLY_REQUEST_COUNTER_NOT_ZERO")
    artifacts = completion["core_artifacts"]
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
    projections = dict(capture_sources=source_projection, market_input=market_projection,
        security_selection={key: arm[key] for key in ("eligible_pool_ids", "research_selected_ids", "counts")},
        formal_csv_values=csv_values, core_numeric_leaves=_numeric_leaves(arm["items"]),
        theme_values=theme_projection, display_counts=completion["display"]["counts"],
        phases=completion["phases"])
    return dict(hashes={key: _digest(value) for key, value in projections.items()}, run_id=run_id,
        decision_cutoff=run["decision_cutoff"], context_cutoff=parent["decision_cutoff"],
        observation_date=run["observation_date"], target_session=run["target_session"],
        eligible_count=len(all_ids), selected_count=len(arm["research_selected_ids"]),
        original_execution_identity=assembly_metadata,
        formal_export_byte_equality_claimed=False,
        projection_scope="EXACT_CAPTURES_AND_MARKET_INPUT_EXCEPT_FOUR_PROVEN_ROOT_UNIVERSE_ASSEMBLY_CLOCKS; ALL_CSV_CELLS_EXCEPT_RUN_ID; ALL_CORE_NUMERIC_NULL_BOOL_LEAVES; WHOLE_THEME_ITEMS_AND_DECLARED_GATES")


def compare(reference, candidate):
    failures = []
    for label, item in (("reference", reference), ("candidate", candidate)):
        if item.get("comparison_proof") is None or item.get("status") not in SUCCESS:
            failures.append(label.upper()+"_NOT_COMPLETE_AND_COMPARABLE")
        else:
            hashes = item["comparison_proof"].get("hashes", {})
            if set(hashes) != set(COMPARISON_KEYS) or not all(
                    type(value) is str and re.fullmatch("[0-9a-f]{64}", value) for value in hashes.values()):
                failures.append(label.upper()+"_INCOMPLETE_COMPARISON_PROOF")
    if not failures:
        first, second = reference["comparison_proof"], candidate["comparison_proof"]
        failures.extend("DIFF:"+key for key in COMPARISON_KEYS
                        if first["hashes"].get(key) != second["hashes"].get(key))
        failures.extend("DIFF:"+key for key in ("observation_date", "target_session")
                        if first[key] != second[key])
    comparable = not failures
    result = dict(reference=reference["engine"], candidate=candidate["engine"], comparable=comparable,
        reasons=failures, formal_export_byte_equality_claimed=False,
        scope="CACHE_ONLY_NATIVE_WORKER_WITH_PHASE_INSTRUMENTATION; NOT_GATEWAY_OR_ONLINE_COLLECTION")
    if comparable:
        old, new = reference["workflow_wall_seconds"], candidate["workflow_wall_seconds"]
        _need(old > 0 and new > 0, "POSITIVE_MEASURED_TIME_REQUIRED")
        result.update(wall_seconds_saved=old-new, worker_wall_speedup_percent=(old-new)/old*100,
            worker_speed_ratio=old/new,
            output_bytes_change=candidate["job_output_bytes"]-reference["job_output_bytes"])
    return result


def _worker(config_path, directory, engine, expected):
    config = _offline_configuration(original._read_control(config_path))
    _need(engine in ENGINES, "UNKNOWN_ENGINE")
    shared._expected_gate(expected)
    job = directory / "job"
    job.mkdir(exist_ok=False)
    stages = []
    function = _instrumented(engine, stages)
    kwargs = {} if engine == "original-ref" else dict(engine=engine, expected_sessions=expected)
    started_at = datetime.now(timezone.utc).isoformat()
    started, cpu = time.perf_counter(), time.process_time()
    completion = function(config, job, **kwargs)
    elapsed, consumed = time.perf_counter()-started, time.process_time()-cpu
    shared._expected_gate(expected)
    result = dict(schema_version=VERSION, engine=engine, status=completion["status"],
        started_at=started_at, completed_at=datetime.now(timezone.utc).isoformat(),
        workflow_wall_seconds=elapsed, workflow_cpu_seconds=consumed,
        process_peak_rss_bytes_after_workflow=_rss(), job_output_bytes=original.directory_bytes(job),
        phase_measurements=stages, metrics=completion["metrics"], job=str(job),
        completion=original._ref(job / "worker-completion.json"), comparison_proof=None,
        real_clock_used=True, ordinary_supervisor_included=False, gateway_admission_included=False,
        acquisition_included=False, network_policy="ORIGINAL_OFFLINE_TRUE_RETRY_FALSE_NO_SEEDS")
    proof_started, proof_cpu = time.perf_counter(), time.process_time()
    proof_bytes = 0
    def charge(count):
        nonlocal proof_bytes
        proof_bytes += count
        _need(proof_bytes <= config["limits"]["supervisor_read_bytes"], "COMPARISON_READ_LIMIT")
        _need(time.perf_counter()-started <= config["limits"]["max_seconds"], "COMPARISON_TIME_LIMIT")
    try:
        _need(completion.get("observation_date") == expected[0]
              and completion.get("target_session") == expected[1], "ACTUAL_SESSION_DIFFERS")
        result["comparison_proof"] = _proof(completion, job, charge)
        charge(0)
        _need(_rss() <= config["limits"]["self_rss_bytes"], "COMPARISON_RSS_LIMIT")
    except Exception as error:
        result["comparison_proof"] = None
        result["comparison_refusal"] = dict(type=type(error).__name__, reason=str(error)[:2000])
    result["comparison_verification_seconds"] = time.perf_counter()-proof_started
    result["comparison_verification_cpu_seconds"] = time.process_time()-proof_cpu
    result["process_cpu_seconds_at_end"] = time.process_time()
    result["process_peak_rss_bytes_at_end"] = _rss()
    result["comparison_verification_returned_bytes"] = proof_bytes
    result["comparison_read_scope"] = "FULL_ARTIFACT_SHA_READS_PLUS_DECODE_INPUT_AND_GZIP_EXPANSION; SEPARATE_ORIGINAL_SUPERVISOR_ALLOCATION"
    _save(directory / "measurement.json", result)
    return 0 if completion["status"] in SUCCESS else 4


def _receipt_paths(directory, engine):
    return (directory / "process-result.json", directory.parent / (engine+"-receipt.json"))


def _arm_output_bytes(directory, engine):
    receipt = _receipt_paths(directory, engine)[1]
    siblings = (receipt, *(receipt.with_suffix(suffix) for suffix in
                (".candidate.json", ".rejected.json", ".failure-reserve.json")))
    return original.directory_bytes(directory) + sum(path.stat().st_size for path in siblings if path.exists())


def _check_parent_budget(config, directory, engine, started, additional_bytes=0):
    _need(time.perf_counter()-started <= config["limits"]["max_seconds"], "PARENT_RECEIPT_TIME_LIMIT")
    _need(_arm_output_bytes(directory, engine)+additional_bytes <= config["limits"]["output_bytes"],
          "PARENT_RECEIPT_OUTPUT_LIMIT")
    # Include the directory accounting itself in the same monotonic deadline.
    _need(time.perf_counter()-started <= config["limits"]["max_seconds"], "PARENT_RECEIPT_TIME_LIMIT")


def _reserve_failure_receipts(config, directory, engine, started):
    """Reserve diagnostics inside the original quota before starting a child."""
    _check_parent_budget(config, directory, engine, started, 2*FAILURE_RECEIPT_BYTES)
    pending = json_bytes(dict(schema_version=VERSION, engine=engine, status="UNSEALED",
        comparison_proof=None, role="RESERVED_PARENT_FAILURE_CONTROL_NOT_RESULT"))
    for path in _receipt_paths(directory, engine):
        with path.with_suffix(".failure-reserve.json").open("xb") as handle:
            handle.write(pending.ljust(FAILURE_RECEIPT_BYTES, b" "))
            handle.flush()
            os.fsync(handle.fileno())
    _check_parent_budget(config, directory, engine, started)


def _publish_process_result(config, directory, engine, started, result, source_refs=()):
    """Publish both parent receipts, or retain rejected bytes and seal FAILED.

    The arm quota includes its directory, its root receipt and both preallocated
    failure controls. Experiment/summary metadata are separate aggregate records.
    Failure sealing reuses reserved bytes; it never increases an exhausted quota.
    """
    destinations = _receipt_paths(directory, engine)
    for path in destinations:
        _need(all(not candidate.exists() and not candidate.is_symlink() for candidate in
                  (path, path.with_suffix(".candidate.json"), path.with_suffix(".rejected.json"))),
              "PARENT_RECEIPT_DESTINATION_EXISTS")
    owned = {}
    def remember(path, stat):
        owned[path] = (stat.st_dev, stat.st_ino)
    try:
        for reference in source_refs:
            original._verify(reference)
        payload_bytes = len(json_bytes(result))
        _check_parent_budget(config, directory, engine, started, 2*payload_bytes)
        for index, path in enumerate(destinations):
            _save(path.with_suffix(".candidate.json"), result, on_create=remember)
            _check_parent_budget(config, directory, engine, started, (1-index)*payload_bytes)
        for path in destinations:
            _need(not path.exists(), "PARENT_RECEIPT_DESTINATION_EXISTS")
            candidate = path.with_suffix(".candidate.json")
            owned[path] = owned[candidate]
            candidate.rename(path)
        _check_parent_budget(config, directory, engine, started)
        return result
    except BaseException as error:
        # Preserve every produced byte without leaving a positive canonical
        # parent receipt. Worker measurement remains only worker-stage evidence.
        for path in destinations:
            candidate = path.with_suffix(".candidate.json")
            for published in (path, candidate):
                if published in owned and published.exists() and not published.is_symlink():
                    stat = published.stat()
                    _need((stat.st_dev, stat.st_ino) == owned[published], "OWNED_RECEIPT_IDENTITY_CHANGED")
                    rejected = path.with_suffix(".rejected.json")
                    _need(not rejected.exists(), "REJECTED_RECEIPT_ALREADY_EXISTS")
                    published.rename(rejected)
        reason = str(error).encode("utf-8")[:2048].decode("utf-8", errors="ignore")
        failed = dict(schema_version=VERSION, engine=engine, status="FAILED", comparison_proof=None,
            parent_failure=dict(type=type(error).__name__, reason=reason),
            prior_worker_status=result.get("status"), external_exit_code=result.get("external_exit_code"),
            external_worker_reaped=result.get("external_worker_reaped"),
            external_wall_seconds=time.perf_counter()-started,
            worker_measurement_is_final_result=False, quota_enlarged=False)
        raw = json_bytes(failed)
        _need(len(raw) <= FAILURE_RECEIPT_BYTES, "FAILURE_RECEIPT_RESERVE_EXCEEDED")
        for path in destinations:
            reserve = path.with_suffix(".failure-reserve.json")
            _need(reserve.stat().st_size == FAILURE_RECEIPT_BYTES, "FAILURE_RECEIPT_RESERVE_CHANGED")
            with reserve.open("r+b") as handle:
                handle.write(raw.ljust(FAILURE_RECEIPT_BYTES, b" "))
                handle.flush()
                os.fsync(handle.fileno())
            if not path.exists() and not path.is_symlink():
                reserve.rename(path)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return failed


def _run_process(config, directory, engine, expected, *, source_refs=()):
    started, peak = time.perf_counter(), 0
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
                        peak = max(peak, original._tree_sample(process.pid))
                    except Exception:
                        if process.poll() is None:
                            raise
                    _need(peak <= config["limits"]["tree_rss_bytes"], "PROCESS_RSS_LIMIT")
                    _need(_arm_output_bytes(directory, engine) <= config["limits"]["output_bytes"],
                          "PROCESS_OUTPUT_LIMIT")
                    time.sleep(0.25)
                code = process.wait()
                _need(not original._owned_group_members(process.pid), "OWNED_DESCENDANTS_REMAIN")
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
                measurement = original._read_control(measurement_path)
                _need(measurement["schema_version"] == VERSION and measurement["engine"] == engine,
                      "WORKER_MEASUREMENT_IDENTITY_CHANGED")
            if measurement is None:
                measurement = dict(schema_version=VERSION, engine=engine, status="FAILED",
                    comparison_proof=None, process_failure=failure)
            measurement.update(external_exit_code=code, external_wall_seconds=time.perf_counter()-started,
                sampled_peak_tree_rss_bytes=peak, external_worker_reaped=process.poll() is not None)
    return _publish_process_result(config, directory, engine, started, measurement, source_refs)


def benchmark(config, output, engines, observation, target):
    config = _offline_configuration(config)
    _need(engines and len(set(engines)) == len(engines) and all(value in ENGINES for value in engines),
          "UNIQUE_EXPLICIT_ENGINES_REQUIRED")
    expected = (observation, target, str(datetime.now(timezone.utc).astimezone(original.SH).date()))
    shared._expected_gate(expected)
    output = Path(output).expanduser().absolute()
    _need(output.parent == output.parent.resolve(strict=True) and not output.exists() and not output.is_symlink(),
          "NEW_UNALIASED_OWNED_OUTPUT_REQUIRED")
    state = Path(config["state_root"])
    _need(not output.is_relative_to(state) and not state.is_relative_to(output),
          "BENCHMARK_OUTPUT_MUST_BE_SEPARATE_FROM_SOURCE_STATE")
    _need(not output.is_relative_to(ROOT), "PRIVATE_OUTPUT_OUTSIDE_SOURCE_TREE_REQUIRED")
    # Same native lock excludes a live premarket writer throughout this explicit
    # experiment. capture_one additionally keeps its original cache writer lock.
    with JobLock(state / "premarket.lock"):
        refs = [*shared.code_refs(), original._ref(Path(__file__).resolve())]
        output.mkdir(exist_ok=False)
        _save(output / "experiment.json", dict(schema_version=VERSION, engines=engines,
            configuration=config, expected_sessions=expected, source_code=refs,
            scope="EXPLICIT_OFFLINE_REAL_DATA_EXPERIMENT_NOT_DAILY_REQUEST_OR_RETRY"))
        results = []
        for engine in engines:
            shared._expected_gate(expected)
            for ref in refs:
                original._verify(ref)
            directory = output / engine
            directory.mkdir()
            result = _run_process(config, directory, engine, expected, source_refs=refs)
            results.append(result)
            # A failed/incomplete arm is evidence, never an invitation to switch
            # engines as a retry. The explicitly selected later arms stay unrun.
            if result["status"] not in SUCCESS or result.get("comparison_proof") is None:
                break
        comparisons = [compare(results[0], row) for row in results[1:]]
        summary = dict(schema_version=VERSION, results=results, comparisons=comparisons,
            requested_engines=engines, unrun_engines=engines[len(results):],
            real_end_to_end_speedup_established=False, online_collection_measured=False,
            gateway_admission_measured=False, repeated_order_bias_controlled=False,
            arm_output_scope="ARM_DIRECTORY_PLUS_OWN_ROOT_RECEIPT_AND_FAILURE_RESERVES; EXPERIMENT_AND_SUMMARY_ARE_AGGREGATE_METADATA",
            notes="Independent fresh processes; one explicit order; warm OS caches uncontrolled; actual cutoffs/run_ids retained.")
        _save(output / "summary.candidate.json", summary)
        (output / "summary.candidate.json").rename(output / "summary.json")
        return summary


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--observation-date", required=True)
    run.add_argument("--target-session", required=True)
    run.add_argument("--engines", nargs="+", choices=ENGINES, default=list(ENGINES))
    worker = commands.add_parser("_worker")
    worker.add_argument("--config", required=True)
    worker.add_argument("--directory", required=True)
    worker.add_argument("--engine", choices=ENGINES, required=True)
    worker.add_argument("--expected", nargs=3, required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "_worker":
        return _worker(Path(args.config), Path(args.directory), args.engine, args.expected)
    result = benchmark(original._read_control(args.config), args.output, args.engines,
                       args.observation_date, args.target_session)
    print(json.dumps(dict(summary=str(Path(args.output).absolute() / "summary.json"),
        comparisons=result["comparisons"], unrun_engines=result["unrun_engines"],
        real_end_to_end_speedup_established=False), ensure_ascii=False))
    return 0 if not result["unrun_engines"] and all(row.get("comparison_proof") is not None
        for row in result["results"]) and all(row["comparable"] for row in result["comparisons"]) else 4


if __name__ == "__main__":
    raise SystemExit(main())
