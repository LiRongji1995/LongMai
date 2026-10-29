"""Read-only ABBA comparison of every core capture in one original summary.

This is source-authentication performance, not a daily request, recovery,
network experiment, new core result or complete premarket delivery.  All arms
use independent fresh Python processes and the original complete verifier.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import codex_capture_decoded_v1 as decoded
from stockfish_core.yaogu import premarket_capture_cache_v1 as capture
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, json_bytes, strict_json


VERSION = "CAPTURE_DECODED_READONLY_BENCHMARK_V1"
ORDER = ("reference", "candidate", "candidate", "reference")
SCOPE = "ALL_ORIGINAL_SUMMARY_CORE_RECORDS_AUTHENTICATION_ONLY"
CONTROL_BYTES = 32768
MAX_CONTROL_BYTES = 16 * 1024**2
_BasePath = type(Path())


def need(value, reason):
    if not value:
        raise ValueError(VERSION + ":" + reason)


def digest(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def _load(path, budget=None, expected=None):
    path = Path(path).absolute()
    need(not path.is_symlink() and path == path.resolve(strict=True), "UNALIASED_CONTROL_REQUIRED")
    if budget is not None:
        raw, reference = budget.read(path, expected=expected, limit=MAX_CONTROL_BYTES)
    else:
        before = workflow.fingerprint(path)
        need(before["st_size"] <= MAX_CONTROL_BYTES, "CONTROL_SIZE_LIMIT")
        raw = path.read_bytes()
        need(workflow.fingerprint(path) == before and len(raw) == before["st_size"], "CONTROL_CHANGED")
        reference = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), fp=before)
        need(expected is None or reference == expected, "CONTROL_REFERENCE_CHANGED")
    return strict_json(raw), reference


def _save(path, value):
    raw = json_bytes(value)
    need(len(raw) <= MAX_CONTROL_BYTES, "OUTPUT_CONTROL_SIZE_LIMIT")
    with Path(path).open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _reserve(path):
    raw = json_bytes(dict(schema_version=VERSION, status="UNSEALED", comparison_proof=None))
    with Path(path).open("xb") as handle:
        handle.write(raw.ljust(CONTROL_BYTES, b" "))
        handle.flush()
        os.fsync(handle.fileno())
        st = os.fstat(handle.fileno())
    return (st.st_dev, st.st_ino)


def _publish(path, identity, value):
    raw = json_bytes(value)
    need(len(raw) <= CONTROL_BYTES, "RESERVED_CONTROL_SIZE_LIMIT")
    path = Path(path)
    need(not path.is_symlink(), "OWNED_CONTROL_SYMLINK")
    with path.open("r+b") as handle:
        st = os.fstat(handle.fileno())
        need((st.st_dev, st.st_ino) == identity and st.st_size == CONTROL_BYTES,
             "OWNED_CONTROL_IDENTITY_CHANGED")
        handle.write(raw.ljust(CONTROL_BYTES, b" "))
        handle.flush()
        os.fsync(handle.fileno())


class _ExistingPath(_BasePath):
    def mkdir(self, *args, **kwargs):
        need(self.is_dir() and not self.is_symlink(), "READONLY_CACHE_DIRECTORY_MISSING")


def readonly_cache(root, budget, config, variant):
    need(variant in set(ORDER), "UNKNOWN_VARIANT")
    root = Path(root).absolute()
    need(root == root.resolve(strict=True) and not root.is_symlink(), "UNALIASED_CACHE_REQUIRED")
    for name in (".", "index", "raw", "raw-index", "locators"):
        need((root / name).is_dir() and not (root / name).is_symlink(), "EXISTING_CACHE_REQUIRED")
    base = capture.CaptureCache if variant == "reference" else decoded.CaptureCache

    class ReadOnly(base):
        def __init__(self):
            constructor = decoded._bind(capture.CaptureCache.__init__, {"Path": _ExistingPath})
            constructor(self, root, budget, config={**config, "offline": True, "retry_failed": False},
                        executor=self.forbidden)
            if variant == "candidate":
                self.decoded_metrics = dict(authentication_calls=0, strict_decodes=0,
                    fresh_native_reads=0, native_json_reuses=0, native_fallback_decodes=0,
                    max_retained_source_bytes=0)

        @staticmethod
        def forbidden(*args, **kwargs):
            raise ValueError(VERSION + ":READONLY_CACHE_WRITE_OR_NETWORK_FORBIDDEN")

        _mkdir_owned = _save = _commit = _writer = capture_one = import_seed = forbidden

    return ReadOnly()


def _configuration(value, fixture):
    need(type(value) is dict and not set(value)-{'schema_version', 'state_root',
         'minimum_amount_cny', 'limits', 'capture', 'news', 'seed_sources'}
         and value.get('schema_version', workflow.VERSION) == workflow.VERSION,
         "ORIGINAL_CONFIGURATION_SCHEMA")
    config = workflow.load_config(overrides=deepcopy(value))
    need(config["capture"].get("allow_test_fixture", False) is fixture,
         "EXPLICIT_TEST_FIXTURE_OR_REAL_CONFIGURATION_REQUIRED")
    need(not config["seed_sources"], "SEED_IMPORT_FORBIDDEN")
    seconds = config["limits"]["max_seconds"]
    need(type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0,
         "FINITE_ORIGINAL_TIME_LIMIT_REQUIRED")
    config["capture"].update(offline=True, retry_failed=False)
    return config


def _arm_limits(config, arm_seconds):
    need(type(arm_seconds) in (int, float) and math.isfinite(arm_seconds) and arm_seconds > 0,
         "FINITE_ARM_TIME_REQUIRED")
    limits = dict(config["limits"])
    limits["max_seconds"] = min(float(arm_seconds), limits["max_seconds"] / len(ORDER))
    limits["capture_read_bytes"] //= len(ORDER)
    limits["output_bytes"] = min(limits["output_bytes"] // len(ORDER), 64 * 1024**2)
    need(limits["capture_read_bytes"] > 0 and limits["output_bytes"] >= 4 * CONTROL_BYTES,
         "ORIGINAL_BUDGET_TOO_SMALL_FOR_FOUR_ARMS")
    return limits


def _records(summary, expected_count, fixture):
    need(type(expected_count) is int and expected_count > 0, "EXPLICIT_POSITIVE_RECORD_COUNT")
    need(summary.get("schema_version") == capture.COLLECTION_VERSION, "ORIGINAL_SUMMARY_SCHEMA")
    records = summary.get("core_records")
    need(type(records) is list and len(records) == expected_count, "FULL_SUMMARY_RECORD_COUNT")
    need(all(type(row) is dict for row in records), "COMPLETE_RECORD_METADATA_REQUIRED")
    ids = [row.get("receipt_id") for row in records]
    need(all(type(value) is str and value for value in ids) and len(set(ids)) == len(ids),
         "UNIQUE_FULL_RECORD_IDENTITIES")
    need(summary.get("coverage_type") == "TEST_FIXTURE" if fixture else
         summary.get("coverage_type") in ("RECONSTRUCTED_HISTORICAL", "PROSPECTIVE_CAPTURED"),
         "SUMMARY_COVERAGE_MODE")
    return records


def _verify_refs(references, budget):
    for reference in references:
        budget.read(reference["path"], expected=reference, limit=max(reference["bytes"], 1))


def _fingerprints(references):
    """Guard the publication window after complete SHA verification."""
    for reference in references:
        path = Path(reference["path"])
        need(str(path.resolve(strict=True)) == reference["path"]
             and workflow.fingerprint(path) == reference["fp"], "PUBLICATION_SOURCE_CHANGED")


def _source_refs(budget):
    references = workflow._code_refs(budget.charge_read)
    for path in (__file__, decoded.__file__, sys.executable):
        references.append(workflow._ref(path, budget.charge_read))
    by_path = {}
    for ref in references:
        need(by_path.setdefault(ref["path"], ref) == ref, "SOURCE_REFERENCE_CONFLICT")
    return [by_path[path] for path in sorted(by_path)]


@contextmanager
def _existing_locks(state, cache):
    need(os.name == "posix", "READONLY_LOCKS_REQUIRE_POSIX")
    import fcntl
    handles = []
    try:
        for path in (Path(state) / "premarket.lock", Path(cache) / "writer.lock"):
            need(path.is_file() and not path.is_symlink(), "EXISTING_ORIGINAL_LOCK_REQUIRED")
            handle = path.open("rb")
            handles.append(handle)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        for handle in reversed(handles):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def worker(request_path, request_sha, directory):
    started, cpu = time.perf_counter(), time.process_time()
    request, request_ref = _load(request_path)
    need(request_ref["sha256"] == request_sha and request.get("schema_version") == VERSION,
         "WORKER_REQUEST_IDENTITY")
    directory = Path(directory).resolve(strict=True)
    need(request["directory"] == str(directory) and request_ref["path"] == str(directory / "request.json"),
         "WORKER_OWNED_DIRECTORY")
    budget = Budget(request["limits"], job_dir=directory)
    budget.started = time.monotonic() - (time.perf_counter() - started)
    budget.charge_read(request_ref["bytes"])
    config, config_ref = _load(request["configuration"]["path"], budget, request["configuration"])
    config = _configuration(config, request["test_fixture"])
    need(request["limits"] == _arm_limits(config, request["arm_seconds"]), "WORKER_LIMITS_CHANGED")
    summary, summary_ref = _load(request["summary"]["path"], budget, request["summary"])
    records = _records(summary, request["expected_record_count"], request["test_fixture"])
    cache_root = Path(config["state_root"]) / "cache"
    need(str(cache_root) == request["cache_root"], "CACHE_CONFIGURATION_BINDING")
    references = [*request["source_code"], config_ref, summary_ref, request_ref]
    _verify_refs(references, budget)
    cache = readonly_cache(cache_root, budget, config["capture"], request["variant"])
    verify_wall = verify_cpu = hash_wall = hash_cpu = 0.0
    hashes = []
    read_before = budget.metrics["returned_read_bytes"]["capture"]
    work_started, work_cpu = time.perf_counter(), time.process_time()
    with budget.phase("capture"), budget.meter_files():
        for ordinal, record in enumerate(records):
            mark, consumed = time.perf_counter(), time.process_time()
            value = cache.verify(record)
            verify_wall += time.perf_counter() - mark
            verify_cpu += time.process_time() - consumed
            mark, consumed = time.perf_counter(), time.process_time()
            encoded = json_bytes(value)
            hashes.append(dict(ordinal=ordinal, receipt_id=record["receipt_id"],
                result_sha256=hashlib.sha256(encoded).hexdigest(), canonical_bytes=len(encoded)))
            del value, encoded
            hash_wall += time.perf_counter() - mark
            hash_cpu += time.process_time() - consumed
            budget.check("complete_record_hash")
        mark, consumed = time.perf_counter(), time.process_time()
        source_references = cache.terminal()
        terminal_wall, terminal_cpu = time.perf_counter() - mark, time.process_time() - consumed
    work_wall, work_consumed = time.perf_counter() - work_started, time.process_time() - work_cpu
    read_bytes = budget.metrics["returned_read_bytes"]["capture"] - read_before
    _verify_refs(references, budget)
    need(all(value == 0 for value in budget.metrics["calls"].values()), "READONLY_CALL_COUNTER_CHANGED")
    source_references.sort(key=lambda row: row["path"])
    proof = dict(record_count=len(records), records_metadata_sha256=digest(records),
        all_results_sha256=digest(hashes), authenticated_sources_sha256=digest(source_references),
        source_returned_read_bytes=read_bytes, summary=summary_ref, configuration=config_ref)
    measurement = dict(schema_version=VERSION, status="COMPLETE", scope=SCOPE,
        variant=request["variant"], test_fixture=request["test_fixture"],
        observation_date=summary.get("observation_date"), target_session=summary.get("target_session"),
        comparison_proof=proof, record_results=hashes, authenticated_sources=source_references,
        verify_wall_seconds=verify_wall, verify_cpu_seconds=verify_cpu,
        canonical_hash_wall_seconds=hash_wall, canonical_hash_cpu_seconds=hash_cpu,
        terminal_wall_seconds=terminal_wall, terminal_cpu_seconds=terminal_cpu,
        authentication_scope_wall_seconds=work_wall, authentication_scope_cpu_seconds=work_consumed,
        worker_wall_seconds=time.perf_counter()-started, worker_cpu_seconds=time.process_time()-cpu,
        metrics=budget.metrics, decode_metrics=getattr(cache, "decoded_metrics", None),
        original_capture_status=summary.get("completeness"),
        real_clock_used=True, source_clocks_rewritten=False, daily_delivery_claimed=False,
        network_collection_measured=False, original_request_retried=False)
    _save(directory / "measurement.json", measurement)
    budget.check("worker_measurement_publication", force=True)
    return 0


def _run_arm(request, directory, parent_budget, final_sources=None):
    started, peak = time.perf_counter(), 0
    request_path = directory / "request.json"
    _save(request_path, request)
    request_ref = workflow._ref(request_path, parent_budget.charge_read)
    parent_path = directory / "parent-result.json"
    owned = _reserve(parent_path)
    result = dict(schema_version=VERSION, variant=request["variant"], status="FAILED",
        comparison_proof=None, scope=SCOPE)
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="capture-benchmark-bytecode-") as temporary:
            command = [sys.executable, "-I", "-B", "-X", "pycache_prefix="+str(Path(temporary)/"absent"),
                str(Path(__file__).resolve()), "_worker", "--request", str(request_path),
                "--request-sha256", request_ref["sha256"], "--directory", str(directory)]
            environment = dict(os.environ)
            for name in ("PYTHONPATH", "PYTHONHOME"):
                environment.pop(name, None)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            with (directory / "console.txt").open("xb") as console:
                process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=console,
                    stderr=subprocess.STDOUT, start_new_session=True)
                while process.poll() is None:
                    parent_budget.check("owned_worker")
                    need(time.perf_counter()-started <= request["limits"]["max_seconds"], "ARM_TIME_LIMIT")
                    try:
                        peak = max(peak, workflow._tree_sample(process.pid, parent_budget.charge_read))
                    except Exception:
                        if process.poll() is None:
                            raise
                    need(peak <= request["limits"]["tree_rss_bytes"], "ARM_TREE_RSS_LIMIT")
                    need(workflow.directory_bytes(directory) <= request["limits"]["output_bytes"], "ARM_OUTPUT_LIMIT")
                    time.sleep(.1)
                code = process.wait()
                need(not workflow._owned_group_members(process.pid, parent_budget.charge_read), "OWNED_DESCENDANTS_REMAIN")
                need(code == 0, "WORKER_EXIT_NONZERO:"+str(code))
            measurement, measurement_ref = _load(directory / "measurement.json", parent_budget)
            need(measurement.get("schema_version") == VERSION and measurement.get("status") == "COMPLETE"
                 and measurement.get("variant") == request["variant"]
                 and measurement.get("test_fixture") is request["test_fixture"], "WORKER_MEASUREMENT_BINDING")
            need(measurement["comparison_proof"]["summary"] == request["summary"]
                 and measurement["comparison_proof"]["configuration"] == request["configuration"]
                 and measurement["comparison_proof"]["record_count"] == request["expected_record_count"],
                 "WORKER_INPUT_BINDING")
            need(digest(measurement["record_results"]) == measurement["comparison_proof"]["all_results_sha256"]
                 and digest(measurement["authenticated_sources"]) == measurement["comparison_proof"]["authenticated_sources_sha256"],
                 "WORKER_COMPLETE_PROOF_BINDING")
            verified = [*request["source_code"], request["summary"], request["configuration"],
                        request_ref, *measurement["authenticated_sources"], measurement_ref]
            _verify_refs(verified, parent_budget)
            result.update(status="COMPLETE", comparison_proof=measurement["comparison_proof"],
                measurement=measurement_ref, authentication_scope_wall_seconds=measurement["authentication_scope_wall_seconds"],
                authentication_scope_cpu_seconds=measurement["authentication_scope_cpu_seconds"],
                verify_wall_seconds=measurement["verify_wall_seconds"], verify_cpu_seconds=measurement["verify_cpu_seconds"],
                worker_returned_read_bytes=measurement["metrics"]["returned_read_bytes"]["capture"],
                worker_peak_self_rss_bytes=measurement["metrics"]["peak_self_rss_bytes"],
                decode_metrics=measurement["decode_metrics"])
        need(time.perf_counter()-started <= request["limits"]["max_seconds"], "ARM_FINAL_TIME_LIMIT")
        need(workflow.directory_bytes(directory) <= request["limits"]["output_bytes"], "ARM_FINAL_OUTPUT_LIMIT")
        result.update(external_exit_code=process.returncode, external_worker_reaped=True,
            sampled_peak_tree_rss_bytes=peak, external_wall_seconds=time.perf_counter()-started)
        _fingerprints(verified)
        _publish(parent_path, owned, result)
        parent_budget.check("parent_arm_publication", force=True)
        need(time.perf_counter()-started <= request["limits"]["max_seconds"], "ARM_PUBLICATION_TIME_LIMIT")
        _fingerprints(verified)
        if final_sources is not None:
            final_sources.extend(verified)
        return result
    except BaseException as error:
        if process is not None:
            workflow._stop_owned(process)
        result.update(status="FAILED", comparison_proof=None,
            error=dict(type=type(error).__name__, reason=str(error)[:2000]),
            external_exit_code=None if process is None else process.returncode,
            external_worker_reaped=process is not None and process.poll() is not None,
            sampled_peak_tree_rss_bytes=peak, external_wall_seconds=time.perf_counter()-started)
        _publish(parent_path, owned, result)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return result


def compare(results):
    if len(results) != len(ORDER) or tuple(row.get("variant") for row in results) != ORDER:
        return dict(comparable=False, reason="COMPLETE_ABBA_REQUIRED")
    if any(row.get("status") != "COMPLETE" or row.get("external_exit_code") != 0
           or row.get("external_worker_reaped") is not True or not row.get("comparison_proof") for row in results):
        return dict(comparable=False, reason="COMPLETE_REAPED_PROVEN_ARMS_REQUIRED")
    if any(row["comparison_proof"] != results[0]["comparison_proof"] for row in results[1:]):
        return dict(comparable=False, reason="FULL_RESULTS_METADATA_SOURCES_OR_READ_BYTES_DIFFER")
    answer = dict(comparable=True, sample_count_per_variant=2, order=list(ORDER),
        scope=SCOPE, full_premarket_speedup_established=False, network_speedup_established=False)
    for metric in ("authentication_scope_wall_seconds", "authentication_scope_cpu_seconds",
                   "verify_wall_seconds", "verify_cpu_seconds", "external_wall_seconds"):
        groups = {variant: [row[metric] for row in results if row["variant"] == variant] for variant in set(ORDER)}
        need(all(type(value) in (int, float) and math.isfinite(value) and value > 0
                 for values in groups.values() for value in values), "FINITE_POSITIVE_MEASUREMENTS")
        medians = {variant: statistics.median(values) for variant, values in groups.items()}
        answer[metric] = dict(samples=groups, medians=medians,
            reduction_percent=(1-medians["candidate"]/medians["reference"])*100)
    return answer


def benchmark(configuration_path, summary_path, output, *, expected_record_count, arm_seconds=900, test_fixture=False):
    started = time.monotonic()
    configuration, config_ref = _load(configuration_path)
    config = _configuration(configuration, test_fixture)
    arm_limits = _arm_limits(config, arm_seconds)
    state = Path(config["state_root"]).absolute()
    need(state == state.resolve(strict=True), "ORIGINAL_STATE_PATH")
    output = Path(output).absolute()
    need(output.parent == output.parent.resolve(strict=True) and not output.exists() and not output.is_symlink(),
         "NEW_PRIVATE_OWNED_OUTPUT_REQUIRED")
    need(not output.is_relative_to(ROOT) and not output.is_relative_to(state)
         and not state.is_relative_to(output), "PRIVATE_OUTPUT_SEPARATE_FROM_SOURCE_STATE")
    output.mkdir()
    parent_limits = {**config["limits"], "capture_read_bytes": config["limits"]["supervisor_read_bytes"]}
    parent = Budget(parent_limits, job_dir=output)
    parent.started = started
    parent.charge_read(config_ref["bytes"])
    summary_path = Path(summary_path).absolute()
    summary, summary_ref = _load(summary_path, parent)
    records = _records(summary, expected_record_count, test_fixture)
    code = _source_refs(parent)
    summary_control = output / "summary.json"
    summary_owned = _reserve(summary_control)
    results = []
    final_sources = []
    final = dict(schema_version=VERSION, status="FAILED", scope=SCOPE,
        order=list(ORDER), results=results, comparison=None, test_fixture=test_fixture,
        original_summary_record_count=len(records), sample_filter_applied=False,
        source_clocks_rewritten=False, original_request_retried=False,
        full_premarket_speedup_established=False, network_speedup_established=False)
    try:
        with _existing_locks(state, state / "cache"):
            _verify_refs([config_ref, summary_ref, *code], parent)
            experiment = dict(schema_version=VERSION, scope=SCOPE, order=list(ORDER),
                configuration=config_ref, summary=summary_ref, source_code=code,
                arm_limits=arm_limits, total_limits=config["limits"],
                records_metadata_sha256=digest(records), expected_record_count=expected_record_count,
                test_fixture=test_fixture, actual_started_at=datetime.now(timezone.utc).isoformat())
            _save(output / "experiment.json", experiment)
            for index, variant in enumerate(ORDER):
                parent.check("before_arm", force=True)
                _verify_refs([config_ref, summary_ref, *code], parent)
                directory = output / (str(index+1).zfill(2) + "-" + variant)
                directory.mkdir()
                request = dict(schema_version=VERSION, variant=variant, directory=str(directory),
                    limits=arm_limits, arm_seconds=arm_seconds, configuration=config_ref,
                    summary=summary_ref, source_code=code, expected_record_count=expected_record_count,
                    cache_root=str(state / "cache"), test_fixture=test_fixture)
                result = _run_arm(request, directory, parent, final_sources)
                results.append(result)
                if result["status"] != "COMPLETE" or result.get("comparison_proof") is None:
                    break
                if result["comparison_proof"] != results[0]["comparison_proof"]:
                    break
            final["comparison"] = compare(results)
            final["status"] = "COMPLETE" if final["comparison"]["comparable"] else "FAILED"
            _verify_refs([config_ref, summary_ref, *code], parent)
            final.update(unrun_arms=list(ORDER[len(results):]), parent_metrics=parent.metrics,
                         total_elapsed_seconds=time.monotonic()-started)
            _fingerprints([config_ref, summary_ref, *code, *final_sources])
            _publish(summary_control, summary_owned, final)
            parent.check("summary_publication", force=True)
            _fingerprints([config_ref, summary_ref, *code, *final_sources])
            return final
    except BaseException as error:
        final.update(status="FAILED", comparison=None, unrun_arms=list(ORDER[len(results):]),
            error=dict(type=type(error).__name__, reason=str(error)[:2000]),
            total_elapsed_seconds=time.monotonic()-started)
        _publish(summary_control, summary_owned, final)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return final


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--configuration", required=True)
    run.add_argument("--capture-summary", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--expected-record-count", type=int, required=True)
    run.add_argument("--arm-seconds", type=float, default=900)
    run.add_argument("--test-fixture", action="store_true")
    child = commands.add_parser("_worker")
    child.add_argument("--request", required=True)
    child.add_argument("--request-sha256", required=True)
    child.add_argument("--directory", required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "_worker":
        return worker(args.request, args.request_sha256, args.directory)
    result = benchmark(args.configuration, args.capture_summary, args.output,
        expected_record_count=args.expected_record_count, arm_seconds=args.arm_seconds,
        test_fixture=args.test_fixture)
    print(json.dumps(dict(status=result["status"], summary=str(Path(args.output).absolute()/"summary.json"),
                          comparison=result["comparison"]), ensure_ascii=False))
    return 0 if result["status"] == "COMPLETE" else 4


if __name__ == "__main__":
    raise SystemExit(main())
