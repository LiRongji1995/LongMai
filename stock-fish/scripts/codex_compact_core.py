"""Explicit native R2 execution with compact, reusable new artifacts.

The original selector, normalizer and NativeCore are called unchanged. Only
unpublished output owned by this invocation can be replaced by compact storage.
An archived reply never restores a NativeCore or a source capability.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import gc
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_core as original
from scripts import codex_workspace as workspace
from scripts import codex_compact_artifacts as compact

VERSION = "CODEX_COMPACT_NATIVE_CORE_REUSE_V1"
PROFILE = "FULL_CORE_COMPACT_V1"
MIB, GIB = 1024 ** 2, 1024 ** 3
LIMITS = dict(core_read_bytes=4 * GIB, output_bytes=258 * MIB,
              max_seconds=900, self_rss_bytes=5 * GIB // 2, tree_rss_bytes=3 * GIB)
need = original._need


def _request(path, fixture):
    value, reference = original._control(Path(path).expanduser().absolute())
    need(set(value) == {"schema_version", "captures", "observation_date", "target_session",
                       "cutoff", "coverage_type"} and value["schema_version"] == original.INPUT_VERSION,
         "COMPACT_CORE_EXACT_REQUEST_REQUIRED")
    need(value["coverage_type"] in {"TEST_FIXTURE", "RECONSTRUCTED_HISTORICAL", "PROSPECTIVE_CAPTURED"}
         and (value["coverage_type"] == "TEST_FIXTURE") == fixture,
         "COMPACT_CORE_FIXTURE_SEPARATION_REQUIRED")
    from datetime import date
    for name in ("observation_date", "target_session"):
        need(date.fromisoformat(value[name]).isoformat() == value[name], "COMPACT_CORE_DATE_REQUIRED")
    need(value["target_session"] > value["observation_date"], "COMPACT_CORE_TARGET_AFTER_OBSERVATION")
    need(original._clock(value["cutoff"]) == value["cutoff"], "COMPACT_CORE_CANONICAL_CUTOFF_REQUIRED")
    captures = value["captures"]
    need(type(captures) is list and 1 <= len(captures) <= 256, "COMPACT_CORE_CAPTURE_LIMIT")
    refs, receipts = {}, set()
    for capture in captures:
        need(all(key in capture for key in ("result", "raw", "receipt", "archive_root")),
             "COMPACT_CORE_ORIGINAL_TRIO_REQUIRED")
        need(capture["receipt"]["path"] not in receipts, "COMPACT_CORE_DUPLICATE_RECEIPT")
        receipts.add(capture["receipt"]["path"])
        need(fixture or len(capture.get("producer_files", [])) == 3,
             "COMPACT_CORE_ORIGINAL_PRODUCERS_REQUIRED")
        for ref in [capture[key] for key in ("result", "raw", "receipt")] + capture.get("producer_files", []):
            compact._reference_shape(ref)
            need(ref["bytes"] <= 16 * MIB, "COMPACT_CORE_SOURCE_FILE_LIMIT")
            need(refs.setdefault(ref["path"], ref) == ref, "COMPACT_CORE_REFERENCE_CONFLICT")
        result, actual = original._control(Path(capture["result"]["path"]), 16 * MIB)
        need(actual == capture["result"], "COMPACT_CORE_RESULT_CHANGED")
        is_fixture = (str(result.get("provider", "")).startswith("TEST_FIXTURE") or
                      str(result.get("field_version", "")).startswith("TEST_FIXTURE") or
                      any("TEST_FIXTURE.invalid" in str(row.get("source_url", ""))
                          for row in result.get("receipts", [])))
        need(is_fixture == fixture, "COMPACT_CORE_MIXED_FIXTURE_FORBIDDEN")
    need(sum(ref["bytes"] for ref in refs.values()) <= 256 * MIB, "COMPACT_CORE_DISTINCT_SOURCE_LIMIT")
    return value, reference


def _snapshot(profile):
    for name, module in tuple(sys.modules.items()):
        if name == "stockfish_core" or name.startswith("stockfish_core."):
            filename = getattr(module, "__file__", None)
            need(filename is not None and Path(filename).resolve(strict=True).is_relative_to(ROOT / "stockfish_core"),
                 "COMPACT_CORE_FOREIGN_LOADED_SOURCE:" + name)
    value = original._snapshot(profile)
    value["compact_coordinator_sources"] = [original._reference(Path(module.__file__).resolve())
                                             for module in (sys.modules[__name__], compact)]
    return value


def _compact_limits():
    return compact.CompactLimits(logical_bytes=128 * MIB, stored_bytes=128 * MIB,
                                 peak_bytes=258 * MIB, returned_read_bytes=GIB, max_seconds=300)


def _unique(references):
    result = {}
    for reference in references:
        need(result.setdefault(reference["path"], reference) == reference, "COMPACT_CORE_REFERENCE_CONFLICT")
    return list(result.values())


def _locators(request):
    return [deepcopy(capture["verification_locator"]) for capture in request["captures"]
            if capture.get("verification_locator") is not None]


def _validate_locators(declarations):
    from stockfish_core.yaogu.premarket_native_core_v1 import _LocatorBinding
    for declaration in declarations:
        _LocatorBinding(declaration).validate()


def _verify_logical_core(archive, logical, manifest, summary, request, meter):
    """Run the original archive semantic checks over exact logical bytes only.

    This adapter is deliberately confined to the eight historical output names.
    It cannot read market files or mint any source or native-owner capability.
    """
    from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
    from stockfish_core.yaogu import strategy_experiments
    from stockfish_core.yaogu.contracts import MarketInput, SourceReceipt, digest
    from stockfish_core.yaogu.short_horizon_participation import short_horizon_source_identity
    role_by_path = {ref["path"]: role for role, ref in logical.items()}
    need(len(role_by_path) == 8, "COMPACT_CORE_UNIQUE_LOGICAL_PATHS_REQUIRED")

    class LogicalReader:
        def check(self):
            meter.check()
            need(bridge._rss() <= LIMITS["self_rss_bytes"], "COMPACT_CORE_VERIFICATION_RSS_LIMIT")

        def read(self, path, expected):
            role = role_by_path.get(str(path))
            need(role is not None and expected == logical[role], "COMPACT_CORE_LOGICAL_READER_SCOPE")
            self.check()
            return archive.read_artifact(role, max_bytes=128 * MIB)

    reader = LogicalReader()

    def document(role):
        raw = reader.read(logical[role]["path"], logical[role])
        parts, size = [], 0
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
            while True:
                reader.check()
                chunk = stream.read(min(MIB, 256 * MIB - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                need(size <= 256 * MIB, "COMPACT_CORE_DECOMPRESSED_DOCUMENT_LIMIT")
                meter.charge(len(chunk))
                parts.append(chunk)
        value = bridge._json(b"".join(parts))
        reader.check()
        return value

    need(manifest.get("schema_version") == "PREMARKET_NATIVE_CORE_V1" and manifest.get("status") == "PASS"
         and manifest.get("source_capability_serialized") is False
         and manifest.get("source_database_written") is False, "COMPACT_CORE_NATIVE_MANIFEST_ROLE")
    source = document("input.json.gz")
    need(source.get("history_artifact_ref") is None and source.get("history_artifact_hash") is None
         and not any(row.get("historical_time_proof") is not None for row in source["receipts"]),
         "COMPACT_CORE_INLINE_INPUT_REQUIRED")
    need(source["coverage_type"] == request["coverage_type"]
         and source["observation_date"] == request["observation_date"], "COMPACT_CORE_INPUT_REQUEST_CONTEXT")
    input_hash = bridge.digest_json_stream(source, checkpoint=reader.check)
    source_receipts = {row["receipt_id"]: row for row in source["receipts"]}
    native_receipts = {row["receipt_id"]: row for row in manifest["source_receipts"]}
    need(len(source_receipts) == len(source["receipts"]) == len(native_receipts)
         == len(manifest["source_receipts"]) == len(request["captures"])
         and source_receipts == native_receipts, "COMPACT_CORE_EXACT_INPUT_RECEIPTS_REQUIRED")

    # Reuse the original read-only trio validator, never a source-capability
    # factory. Matching the two output receipt lists alone would allow both to
    # be replaced by the same unrelated list while the request stayed intact.
    source_refs = {ref["path"]: ref for capture in request["captures"]
        for ref in [capture[key] for key in ("result", "raw", "receipt")] + capture.get("producer_files", [])}
    typed_receipts = {rid: SourceReceipt.model_validate(row) for rid, row in source_receipts.items()}

    class SourceReader:
        def __init__(self):
            self.locators = []

        def read(self, path, expected, *, limit=16 * MIB, retain=True):
            reader.check()
            need(source_refs.get(str(path)) == expected, "COMPACT_CORE_SOURCE_READER_SCOPE")
            reference, raw = original._read_file(path, min(limit, 16 * MIB), retain=True)
            if original._ACTIVE_BUDGET.get() is not meter:
                meter.charge(len(raw))
            need(reference == expected, "COMPACT_CORE_ORIGINAL_TRIO_CHANGED")
            reader.check()
            return raw

    checked_receipts, source_reader = {}, SourceReader()
    for capture in request["captures"]:
        _, typed, _ = bridge._trio(source_reader, capture, source_refs, typed_receipts, {},
            request["coverage_type"], datetime.fromisoformat(request["cutoff"]))
        need(typed.receipt_id not in checked_receipts, "COMPACT_CORE_DUPLICATE_ORIGINAL_RECEIPT")
        checked_receipts[typed.receipt_id] = typed.model_dump(mode="json")
    need(checked_receipts == source_receipts, "COMPACT_CORE_EXACT_ORIGINAL_RECEIPTS_REQUIRED")

    # Qualification depends only on the saved security, receipt and calendar
    # evidence. Run that original rule on a small typed projection; do not
    # normalize, evaluate prices or select the strategy again during reuse.
    projection = MarketInput.model_validate({key: source[key] for key in
        ("coverage_type", "observation_date", "sessions", "universe", "receipts")} | {"bars": []})
    reader.check()
    qualification = strategy_experiments._prepare_context(projection,
        cutoff=datetime.fromisoformat(request["cutoff"])).qualification
    reader.check()
    del source, source_receipts, native_receipts
    del source_reader, typed_receipts, checked_receipts, source_refs
    gc.collect()
    run = document("canonical-run.json.gz")
    need(input_hash == run.get("input_hash") == manifest.get("input_hash") == summary["input_hash"],
         "COMPACT_CORE_INPUT_HASH_MISMATCH")
    need(run.get("content_hash") == manifest.get("canonical_content_hash") == summary["canonical_content_hash"],
         "COMPACT_CORE_CANONICAL_HASH_MISMATCH")
    need(run.get("decision_cutoff") == request["cutoff"]
         and run.get("target_session") == request["target_session"], "COMPACT_CORE_RUN_REQUEST_CONTEXT")
    need(original._clock(run["decision_cutoff"]) <= original._clock(run["generated_at"]),
         "COMPACT_CORE_RUN_CLOCK_ORDER")
    for key, expected in short_horizon_source_identity().items():
        if key in run:
            need(run[key] == expected, "COMPACT_CORE_FROZEN_STRATEGY_CHANGED:" + key)
    result = run["results"][original.STRATEGY]
    need(run.get("qualification") == result.get("qualification") == qualification
         and run.get("universe_hash") == digest(qualification), "COMPACT_CORE_ORIGINAL_QUALIFICATION_MISMATCH")
    eligible = qualification["eligible_ids"]
    need(len(eligible) == len(set(eligible)) == qualification["eligible"]
         and result.get("eligible_pool_ids") == eligible
         and len(result.get("items", [])) == len(eligible)
         and {row.get("instrument_id") for row in result["items"]} == set(eligible),
         "COMPACT_CORE_FULL_QUALIFIED_POOL_REQUIRED")
    bridge._validate_run(run, projection, manifest, logical, reader, input_hash)
    need(original._summary(run, manifest, request) == summary, "COMPACT_CORE_COMPLETE_SUMMARY_MISMATCH")
    del run, projection
    gc.collect()
    reader.check()


def _owned_job_state(path):
    with workspace._directory_fd(path) as fd:
        return compact._dir_identity(os.fstat(fd))


def _monitor_owned_job(control, report_root, prior=None):
    """Observe only this worker's explicitly declared newly owned directory."""
    from stockfish_core.yaogu.premarket_runtime_v1 import directory_bytes
    path = control / "owned-job.json"
    if not path.exists() and not path.is_symlink():
        need(prior is None, "COMPACT_CORE_OWNED_JOB_DECLARATION_DISAPPEARED")
        return None, 0
    value = workspace._read(path, limit=8192)
    need(set(value) == {"schema_version", "job", "directory_identity"} and value["schema_version"] == VERSION,
         "COMPACT_CORE_OWNED_JOB_DECLARATION_SCHEMA")
    job = Path(value["job"])
    need(job.is_absolute() and job.parent == report_root and job.name.startswith("compact-core-")
         and _owned_job_state(job) == value["directory_identity"], "COMPACT_CORE_OWNED_JOB_IDENTITY")
    need(prior is None or value == prior, "COMPACT_CORE_OWNED_JOB_DECLARATION_CHANGED")
    size = directory_bytes(job)
    need(size <= LIMITS["output_bytes"], "COMPACT_CORE_EXTERNAL_JOB_OUTPUT_LIMIT")
    need(_owned_job_state(job) == value["directory_identity"], "COMPACT_CORE_OWNED_JOB_IDENTITY")
    return value, size


def _execute(runtime, manifest_path, profile, fixture, supervisor_control=None):
    need(profile == PROFILE, "COMPACT_CORE_EXPLICIT_PROFILE_REQUIRED")
    need(runtime is not None and runtime.profile.get("backend") == "premarket", "COMPACT_CORE_RUNTIME_REQUIRED")
    request, request_ref = _request(manifest_path, fixture)
    snapshot = _snapshot(runtime.profile)
    identity = dict(schema_version=VERSION, profile=profile,
                    request_digest=original._digest(request), implementation_digest=original._digest(snapshot),
                    observation_date=request["observation_date"], target_session=request["target_session"],
                    cutoff=request["cutoff"], coverage_type=request["coverage_type"])
    identity_hash = original._digest(identity)
    report_root = Path(runtime.profile["report_root"])
    meter = original._ACTIVE_BUDGET.get()

    def action():
        admission = runtime.preflight(expected_growth_bytes=LIMITS["output_bytes"] + 2 * MIB,
                                      destination=report_root)
        need(admission["allowed"], "COMPACT_CORE_CAPACITY_BLOCKED")
        job = report_root / ("compact-core-" + uuid.uuid4().hex)
        workspace._directory(job, create=True)
        if supervisor_control is not None:
            workspace._write(supervisor_control / "owned-job.json", dict(schema_version=VERSION,
                job=str(job), directory_identity=_owned_job_state(job)))
        stage = compact.create_staging(job)
        from stockfish_core.yaogu.premarket_native_core_v1 import run_core
        from stockfish_core.yaogu.premarket_runtime_v1 import Budget
        native = Budget(meter.remaining(LIMITS), job_dir=job)
        owner = None
        try:
            with native.phase("core"), native.meter_files():
                owner = run_core(captures=request["captures"], observation_date=request["observation_date"],
                                 target_session=request["target_session"], cutoff=request["cutoff"],
                                 output_dir=stage.core_dir, budget=native, coverage_type=request["coverage_type"])
                owner.validate()
                run, artifacts = owner.run, owner.artifacts
                need(set(artifacts) == original.FILES, "COMPACT_CORE_EIGHT_ARTIFACTS_REQUIRED")
                # The live owner has authenticated source files and whole input/run;
                # no normalizer or selector is called again for archival storage.
                manifest = json.loads(Path(artifacts["manifest.json"]["path"]).read_bytes())
                summary = original._summary(run, manifest, request)
                references = _unique([*owner.validation_references, request_ref])
                for capture in request["captures"]:
                    for ref in [capture[key] for key in ("raw", "receipt", "result")] + capture.get("producer_files", []):
                        need(ref in references, "COMPACT_CORE_SOURCE_NOT_IN_LIVE_OWNER")
                del run

                def terminal():
                    owner.validate()
                    return True

                packed = compact.compact_owned(stage, artifacts, destination=job / "artifacts",
                                               terminal_validator=terminal, limits=_compact_limits())
                # The old output paths are now historical staging descriptions.
                # Do not call owner.validate or serialize it after the transition.
                owner = None
        finally:
            meter.charge(sum(native.metrics["returned_read_bytes"].values()), native=True)
        need(packed["staging_removed"] is True, "COMPACT_CORE_OWNED_STAGING_NOT_REMOVED")
        evidence = dict(schema_version=VERSION, identity_digest=identity_hash, request=request_ref,
                        implementation=snapshot, summary=summary, validation_references=references,
                        locators=_locators(request),
                        original_logical_artifacts=artifacts, compact_manifest=packed["manifest"],
                        original_output_paths_role="RETIRED_UNPUBLISHED_OWNED_STAGING_ONLY",
                        native_metrics=native.metrics, compact_metrics=packed,
                        source_authority_restored=False, native_owner_retained=False)
        workspace._write(job / "compact-reuse-evidence.json", evidence)
        return dict(schema_version=VERSION, identity_digest=identity_hash, summary=summary,
                    evidence=original._reference(job / "compact-reuse-evidence.json", 2 * MIB))

    def validate(receipt):
        need(receipt.get("schema_version") == VERSION and receipt.get("identity_digest") == identity_hash,
             "COMPACT_CORE_REUSE_IDENTITY_CHANGED")
        path = Path(receipt["evidence"]["path"])
        need(path.name == "compact-reuse-evidence.json" and path.parent.parent == report_root
             and path.parent.name.startswith("compact-core-"), "COMPACT_CORE_EVIDENCE_OUTSIDE_RUNTIME")
        evidence, reference = original._control(path)
        need(reference == receipt["evidence"] and evidence["schema_version"] == VERSION
             and evidence["identity_digest"] == identity_hash and evidence["implementation"] == snapshot
             and evidence["summary"] == receipt["summary"]
             and evidence["source_authority_restored"] is False
             and evidence["native_owner_retained"] is False, "COMPACT_CORE_EVIDENCE_CHANGED")
        need(_snapshot(runtime.profile) == snapshot, "COMPACT_CORE_IMPLEMENTATION_CHANGED")
        original._verify(request_ref)
        if evidence["request"] != request_ref:
            saved_request, saved_reference = _request(evidence["request"]["path"], fixture)
            need(saved_request == request and saved_reference == evidence["request"], "COMPACT_CORE_REQUEST_CHANGED")
        refs = _unique(evidence["validation_references"])
        need(len(refs) <= 2048 and sum(ref["bytes"] for ref in refs) <= 300 * MIB,
             "COMPACT_CORE_REFERENCE_SET_LIMIT")
        for capture in request["captures"]:
            for ref in [capture[key] for key in ("raw", "receipt", "result")] + capture.get("producer_files", []):
                need(ref in refs, "COMPACT_CORE_SOURCE_REFERENCE_MISSING")
        for ref in refs:
            original._verify(ref)
        need(evidence["locators"] == _locators(request), "COMPACT_CORE_COMPLETE_LOCATOR_SET_REQUIRED")
        _validate_locators(evidence["locators"])
        packed_ref = evidence["compact_manifest"]
        need(Path(packed_ref["path"]) == path.parent / "artifacts" / compact.MANIFEST_NAME,
             "COMPACT_CORE_PACKED_PATH_CHANGED")
        archive = compact.CompactArchive(packed_ref, limits=_compact_limits())
        try:
            checked = archive.verify_all()
            logical = evidence["original_logical_artifacts"]
            need(set(logical) == original.FILES, "COMPACT_CORE_EIGHT_LOGICAL_ARTIFACTS_REQUIRED")
            for name, item in archive.manifest["artifacts"].items():
                need(item["source_reference"] == logical[name], "COMPACT_CORE_LOGICAL_ARTIFACT_CHANGED")
            manifest = original.premarket._json(archive.read_artifact("manifest.json", max_bytes=16 * MIB))
            for name, ref in manifest["artifacts"].items():
                need(name in logical and ref == logical[name], "COMPACT_CORE_ORIGINAL_MANIFEST_CHANGED")
            need(set(manifest["artifacts"]) == original.FILES - {"manifest.json"},
                 "COMPACT_CORE_ORIGINAL_MANIFEST_ARTIFACTS_MISSING")
            for key in ("run_id", "input_hash", "canonical_content_hash", "counts",
                        "observation_date", "target_session", "decision_cutoff"):
                need(manifest[key] == receipt["summary"][key], "COMPACT_CORE_SUMMARY_MISMATCH")
            _verify_logical_core(archive, logical, manifest, receipt["summary"], request, meter)
        finally:
            meter.charge(archive.meter.read_bytes)
        need(_snapshot(runtime.profile) == snapshot, "COMPACT_CORE_IMPLEMENTATION_CHANGED_AT_TERMINAL")
        # Physical source identity is pinned again after the complete output pass.
        for ref in refs:
            need(original._fp(Path(ref["path"]).lstat()) == ref["fp"], "COMPACT_CORE_SOURCE_CHANGED_AT_TERMINAL")
        _validate_locators(evidence["locators"])
        return dict(verified=True, archived_bytes_verified=True, source_authority_restored=False,
                    original_logical_bytes=checked["logical_bytes"], stored_payload_bytes=checked["stored_bytes"],
                    saved_payload_bytes=checked["logical_bytes"] - checked["stored_bytes"],
                    artifact_count=8, full_premarket_ready=False)

    result = runtime.run_once(identity, action, validate)
    result.update(summary=deepcopy(result["receipt"]["summary"]), normalizer_calls=0 if result["reused"] else 1,
                  selector_calls=0 if result["reused"] else 1, provider_calls=0,
                  new_large_artifact_bytes=0 if result["reused"] else result["validation"]["stored_payload_bytes"],
                  source_authority_restored=False, full_premarket_ready=False)
    return result


def execute_compact_core(runtime, manifest_path, *, profile, allow_test_fixture=False, _supervisor_control=None):
    if _supervisor_control is not None:
        _supervisor_control = Path(_supervisor_control).absolute()
        need(runtime is not None and _supervisor_control.parent == Path(runtime.profile["report_root"])
             and _supervisor_control.name.startswith("compact-supervisor-"), "COMPACT_CORE_SUPERVISOR_CONTROL_SCOPE")
        _owned_job_state(_supervisor_control)
    meter = original._InvocationBudget()
    meter.maximum, meter.seconds = LIMITS["core_read_bytes"], LIMITS["max_seconds"]
    token = original._ACTIVE_BUDGET.set(meter)
    try:
        result = _execute(runtime, manifest_path, profile, allow_test_fixture, _supervisor_control)
        result["invocation_metrics"] = meter.metrics()
        return result
    finally:
        original._ACTIVE_BUDGET.reset(token)


def supervised_command(runtime, manifest_path, *, profile, allow_test_fixture=False):
    """Bound the isolated native worker, including serialization between checks."""
    need(profile == PROFILE and runtime is not None, "COMPACT_CORE_EXPLICIT_RUNTIME_PROFILE_REQUIRED")
    need(os.name == "posix", "COMPACT_CORE_SUPERVISOR_REQUIRES_POSIX")
    from stockfish_core.yaogu.premarket_workflow_v1 import _tree_sample, _stop_owned, _owned_group_members
    from stockfish_core.yaogu.premarket_runtime_v1 import directory_bytes
    prepared = original.premarket._prepared(runtime.profile)
    need(prepared["source"] == ROOT, "COMPACT_CORE_REGISTERED_SOURCE_REQUIRED")
    report_root = Path(runtime.profile["report_root"])
    with runtime.exclusive():
        admission = runtime.preflight(expected_growth_bytes=2 * MIB, destination=report_root)
        need(admission["allowed"], "COMPACT_CORE_SUPERVISOR_CAPACITY_BLOCKED")
        control = report_root / ("compact-supervisor-" + uuid.uuid4().hex)
        workspace._directory(control, create=True)
    # Child owns run_once's resource lock. Holding it here would deadlock.
    output = control / "result.json"
    source_before = _snapshot(runtime.profile)
    command = [str(prepared["python"]), "-I", "-B", str(Path(__file__).resolve()),
               "--runtime", runtime.profile["name"], "--registry-root", str(runtime.registry_root),
               "--manifest", str(Path(manifest_path).expanduser().absolute()), "--profile", profile,
               "--worker-result", str(output)]
    if allow_test_fixture:
        command.append("--allow-test-fixture")
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    process, peak, started, owned_job, peak_job_bytes = None, 0, time.monotonic(), None, 0
    try:
        with (control / "console.txt").open("xb") as console:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=console,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            last = 0
            while process.poll() is None:
                try:
                    peak = max(peak, _tree_sample(process.pid))
                except Exception:
                    if process.poll() is None:
                        raise
                elapsed = time.monotonic() - started
                need(elapsed < LIMITS["max_seconds"], "COMPACT_CORE_TREE_TIME_LIMIT")
                need(peak <= LIMITS["tree_rss_bytes"], "COMPACT_CORE_TREE_RSS_LIMIT")
                need(directory_bytes(control) <= MIB, "COMPACT_CORE_SUPERVISOR_OUTPUT_LIMIT")
                owned_job, current_job_bytes = _monitor_owned_job(control, report_root, owned_job)
                peak_job_bytes = max(peak_job_bytes, current_job_bytes)
                if elapsed - last >= 10:
                    workspace._write(control / "progress.json", dict(status="RUNNING",
                        elapsed_seconds=elapsed, peak_tree_rss_bytes=peak), replace=(control / "progress.json").exists())
                    last = elapsed
                time.sleep(0.25)
            code = process.wait()
        need(not _owned_group_members(process.pid), "COMPACT_CORE_OWNED_DESCENDANTS_REMAIN")
        owned_job, current_job_bytes = _monitor_owned_job(control, report_root, owned_job)
        peak_job_bytes = max(peak_job_bytes, current_job_bytes)
        need(directory_bytes(control) <= MIB, "COMPACT_CORE_SUPERVISOR_OUTPUT_LIMIT")
        if code != 0:
            error_path = control / "worker-error.json"
            if error_path.exists() or error_path.is_symlink():
                failure = workspace._read(error_path, limit=8192)
                need(failure.get("schema_version") == VERSION and failure.get("status") == "BLOCKED"
                     and isinstance(failure.get("reason"), str) and len(failure["reason"]) <= 2048,
                     "COMPACT_CORE_WORKER_FAILURE_SCHEMA")
                raise original.CoreError(failure["reason"])
            raise original.CoreError("COMPACT_CORE_WORKER_FAILED")
        result = workspace._read(output)
        need(result.get("status") in {"RECORDED", "REUSED"}, "COMPACT_CORE_WORKER_FAILED")
        need((result["status"] == "REUSED" and owned_job is None) or
             (result["status"] == "RECORDED" and owned_job is not None and
              Path(result["receipt"]["evidence"]["path"]).parent == Path(owned_job["job"])),
             "COMPACT_CORE_WORKER_OWNED_JOB_BINDING")
        need(_snapshot(runtime.profile) == source_before, "COMPACT_CORE_SOURCE_CHANGED_DURING_WORKER")
        result["external_supervision"] = dict(exit_code=code, reaped=True, peak_tree_rss_bytes=peak,
            elapsed_seconds=time.monotonic() - started, control_directory=str(control),
            peak_owned_job_bytes=peak_job_bytes, owned_job=owned_job,
            scope="PROCESS_TIME_RSS_OWNED_JOB_BYTES_AND_SOURCE_BINDING; ARCHIVE_VERIFIED_BY_WORKER")
        workspace._write(control / "completion.json", result)
        return result
    except BaseException as error:
        cleanup_error = None
        if process is not None:
            try:
                _stop_owned(process)
            except Exception as cleanup:
                cleanup_error = type(cleanup).__name__
        workspace._write(control / "failure.json", dict(status="FAILED_OR_UNKNOWN",
            error_type=type(error).__name__, cleanup_error_type=cleanup_error,
            exit_code=process.returncode if process else None,
            elapsed_seconds=time.monotonic() - started, peak_tree_rss_bytes=peak,
            peak_owned_job_bytes=peak_job_bytes, owned_job=owned_job,
            reason=_failure_reason(error)))
        raise


def _failure_reason(error):
    from stockfish_core.yaogu.premarket_runtime_v1 import BudgetExceeded
    known = (original.CoreError, workspace.WorkspaceError, compact.CompactError, BudgetExceeded)
    if isinstance(error, known) or isinstance(error, ValueError) and str(error).startswith("R2_ARCHIVE_BRIDGE_"):
        return str(error)[:2048]
    return "COMPACT_CORE_EXECUTION_FAILED"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime")
    parser.add_argument("--registry-root")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--profile", choices=[PROFILE], required=True)
    parser.add_argument("--allow-test-fixture", action="store_true")
    parser.add_argument("--worker-result", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    worker_control = None
    try:
        runtime = workspace.load_runtime(args.runtime, args.registry_root)
        if args.worker_result is None:
            result = supervised_command(runtime, args.manifest, profile=args.profile,
                                        allow_test_fixture=args.allow_test_fixture)
        else:
            output = Path(args.worker_result).absolute()
            need(output.name == "result.json" and output.parent.parent == Path(runtime.profile["report_root"])
                 and output.parent.name.startswith("compact-supervisor-"), "COMPACT_CORE_WORKER_OUTPUT_SCOPE")
            _owned_job_state(output.parent)
            worker_control = output.parent
            result = execute_compact_core(runtime, args.manifest, profile=args.profile,
                                          allow_test_fixture=args.allow_test_fixture,
                                          _supervisor_control=output.parent)
            workspace._write(output, result)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as error:
        failure = dict(schema_version=VERSION, status="BLOCKED", reason=_failure_reason(error),
                       error_type=type(error).__name__)
        if worker_control is not None:
            try:
                workspace._write(worker_control / "worker-error.json", failure)
            except (ValueError, OSError):
                pass  # Console retains the primary failure if control persistence also failed.
        print(json.dumps(failure, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
