"""Reuse one original, bounded R2 core computation across Codex chats.

The saved reply is an archived research result, never a restored live source
capability or a complete premarket run. Original strategy and capture modules
remain untouched. No database, downloader, provider, or service is created.
"""
from __future__ import annotations

import argparse
from contextvars import ContextVar
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_workspace as workspace
from scripts import codex_premarket as premarket

VERSION = "CODEX_FROZEN_CORE_REUSE_V1"
INPUT_VERSION = "CODEX_FROZEN_CORE_REQUEST_V1"
STRATEGY = "SHORT_HORIZON_STRENGTH_R2"
MIB = 1024 ** 2
MAX_MANIFEST = 2 * MIB
MAX_SOURCE_BYTES = 64 * MIB
MAX_CAPTURES = 128
FP_KEYS = premarket.FP_KEYS
FILES = premarket.CORE_FILES
LIMITS = {"core_read_bytes": 512 * MIB, "output_bytes": 64 * MIB,
          "max_seconds": 180, "self_rss_bytes": 1536 * MIB, "tree_rss_bytes": 2 * 1024 * MIB}
_ACTIVE_BUDGET = ContextVar("codex_core_invocation_budget", default=None)


class CoreError(ValueError):
    pass


def _need(value, reason):
    if not value:
        raise CoreError(reason)


def _digest(value):
    return hashlib.sha256(workspace._canonical(value)).hexdigest()


class _InvocationBudget:
    """One non-borrowable read/time envelope, including archived-result reuse."""
    def __init__(self):
        self.started = time.monotonic()
        self.maximum = LIMITS["core_read_bytes"]
        self.seconds = LIMITS["max_seconds"]
        self.read_bytes = 0
        self.native_read_bytes = 0

    def check(self):
        _need(self.read_bytes <= self.maximum, "CORE_TOTAL_READ_BUDGET_EXCEEDED")
        _need(time.monotonic() - self.started < self.seconds, "CORE_TOTAL_TIME_BUDGET_EXCEEDED")

    def charge(self, size, *, native=False):
        self.read_bytes += size
        if native:
            self.native_read_bytes += size
        self.check()

    def tighten(self, limits):
        self.maximum = min(self.maximum, limits["core_read_bytes"])
        self.seconds = min(self.seconds, limits["max_seconds"])
        self.check()

    def remaining(self, limits):
        self.check()
        result = dict(limits)
        result["core_read_bytes"] = self.maximum - self.read_bytes
        _need(result["core_read_bytes"] > 0, "CORE_TOTAL_READ_BUDGET_EXCEEDED")
        result["max_seconds"] = self.seconds - (time.monotonic() - self.started)
        _need(result["max_seconds"] > 0, "CORE_TOTAL_TIME_BUDGET_EXCEEDED")
        return result

    def metrics(self):
        self.check()
        return {"returned_read_bytes": self.read_bytes, "native_read_bytes": self.native_read_bytes,
                "outer_read_bytes": self.read_bytes - self.native_read_bytes,
                "elapsed_seconds": time.monotonic() - self.started,
                "max_read_bytes": self.maximum, "max_seconds": self.seconds,
                "scope": "Explicit wrapper file reads plus original native Budget returned bytes; not kernel I/O or imports outside native meter",
                "configuration_bootstrap_max_bytes": 65536}


def _fp(value):
    _need(stat.S_ISREG(value.st_mode), "CORE_REGULAR_FILE_REQUIRED")
    _need(not getattr(value, "st_flags", 0) & 0x40000000 and
          (not value.st_size or getattr(value, "st_blocks", 1) > 0), "CORE_RESIDENT_FILE_REQUIRED")
    return {key: int(getattr(value, key, 0)) for key in FP_KEYS}


def _read_file(path, maximum=MAX_SOURCE_BYTES, *, retain=False):
    """Stream the actual file through no-follow directory handles, twice pinning identity."""
    path = Path(path)
    _need(path.is_absolute() and ".." not in path.parts, "CORE_ABSOLUTE_PATH_REQUIRED")
    meter = _ACTIVE_BUDGET.get()
    if meter is not None:
        meter.check()
    with workspace._directory_fd(path.parent) as parent:
        initial = _fp(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
        _need(initial["st_size"] <= maximum, "CORE_SOURCE_FILE_LIMIT")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            _need(_fp(os.fstat(fd)) == initial, "CORE_FILE_CHANGED_DURING_READ")
            digest, size, raw = hashlib.sha256(), 0, bytearray()
            while True:
                if meter is not None:
                    meter.check()
                amount = min(65536, initial["st_size"] - size)
                if not amount:
                    break
                if meter is not None:
                    _need(meter.read_bytes + amount <= meter.maximum, "CORE_TOTAL_READ_BUDGET_EXCEEDED")
                part = os.read(fd, amount)
                if not part:
                    break
                if meter is not None:
                    meter.charge(len(part))
                size += len(part)
                _need(size <= maximum, "CORE_SOURCE_FILE_LIMIT")
                digest.update(part)
                if retain:
                    raw.extend(part)
            _need(_fp(os.fstat(fd)) == initial == _fp(os.stat(path.name, dir_fd=parent,
                  follow_symlinks=False)) and size == initial["st_size"], "CORE_FILE_CHANGED_DURING_READ")
        finally:
            os.close(fd)
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": size, "fp": initial}, bytes(raw)


def _reference(path, maximum=MAX_SOURCE_BYTES):
    return _read_file(path, maximum)[0]


def _verify(reference):
    _need(isinstance(reference, dict) and set(reference) == {"path", "sha256", "bytes", "fp"},
          "CORE_FULL_PHYSICAL_REFERENCE_REQUIRED")
    _need(_reference(reference["path"]) == reference, "CORE_ORIGINAL_BYTES_OR_IDENTITY_CHANGED")
    return reference["bytes"]


def _control(path, maximum=MAX_MANIFEST):
    reference, raw = _read_file(path, maximum, retain=True)
    return premarket._json(raw), reference


def _clock(value):
    _need(isinstance(value, str), "CORE_EXPLICIT_CUTOFF_REQUIRED")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CoreError("CORE_INVALID_CUTOFF") from None
    _need(result.tzinfo is not None and result.utcoffset() is not None,
          "CORE_AWARE_CUTOFF_REQUIRED")
    _need(result <= datetime.now(timezone.utc), "CORE_FUTURE_CUTOFF_FORBIDDEN")
    return result.astimezone(timezone.utc).isoformat()


def _request(path, allow_test_fixture):
    value, reference = _control(Path(path).expanduser().absolute())
    required = {"schema_version", "captures", "observation_date", "target_session", "cutoff", "coverage_type"}
    _need(isinstance(value, dict) and set(value) == required and value["schema_version"] == INPUT_VERSION,
          "CORE_REQUEST_SCHEMA")
    _need(value["coverage_type"] in {"RECONSTRUCTED_HISTORICAL", "PROSPECTIVE_CAPTURED", "TEST_FIXTURE"},
          "CORE_COVERAGE_TYPE_REQUIRED")
    _need((value["coverage_type"] == "TEST_FIXTURE") == bool(allow_test_fixture),
          "CORE_TEST_FIXTURE_EXPLICIT_AND_SEPARATE")
    value["observation_date"] = date.fromisoformat(value["observation_date"]).isoformat()
    value["target_session"] = date.fromisoformat(value["target_session"]).isoformat()
    _need(value["target_session"] > value["observation_date"], "CORE_TARGET_AFTER_OBSERVATION_REQUIRED")
    value["cutoff"] = _clock(value["cutoff"])
    captures = value["captures"]
    _need(isinstance(captures, list) and 1 <= len(captures) <= MAX_CAPTURES, "CORE_BOUNDED_CAPTURES_REQUIRED")
    total, seen = 0, set()
    for item in captures:
        _need(isinstance(item, dict) and all(k in item for k in ("archive_root", "result", "raw", "receipt")),
              "CORE_ORIGINAL_TRIO_REQUIRED")
        _need(Path(item["archive_root"]).is_absolute(), "CORE_ABSOLUTE_ARCHIVE_REQUIRED")
        _need(item["receipt"]["path"] not in seen, "CORE_DUPLICATE_CAPTURE")
        seen.add(item["receipt"]["path"])
        for ref in [item[k] for k in ("result", "raw", "receipt")] + item.get("producer_files", []):
            _need(isinstance(ref, dict) and set(ref) == {"path", "sha256", "bytes", "fp"},
                  "CORE_FULL_PHYSICAL_REFERENCE_REQUIRED")
            _need(type(ref["bytes"]) is int and 0 <= ref["bytes"] <= MAX_SOURCE_BYTES,
                  "CORE_SOURCE_FILE_LIMIT")
            total += ref["bytes"]
        if not allow_test_fixture:
            _need(len(item.get("producer_files", [])) == 3, "CORE_THREE_ORIGINAL_PRODUCERS_REQUIRED")
    _need(total <= MAX_SOURCE_BYTES, "CORE_TOTAL_CAPTURE_LIMIT")
    return value, reference


def _snapshot(profile):
    source, python = Path(profile["source_root"]), Path(profile["python"])
    _need(source == ROOT, "CORE_REGISTERED_SOURCE_MUST_MATCH_EXECUTING_MODULE")
    _need(Path(sys.executable).absolute() == python, "CORE_REGISTERED_INTERPRETER_REQUIRED")
    configuration, config_ref = _control(Path(profile["config"]), 65536)
    _need(configuration.get("state_root") == profile["state_root"], "CORE_EXPLICIT_CONFIG_STATE_ROOT_REQUIRED")
    paths = {*source.joinpath("stockfish_core").rglob("*.py"),
             *(source / name for name in ("scripts/stockfish.py", "premarket", "premarket.ps1"))}
    _need(len(paths) <= premarket.MAX_SOURCE_FILES, "CORE_SOURCE_FILE_COUNT_LIMIT")
    snapshot = {"source_references": [_reference(path) for path in sorted(paths)],
                "strategy_support": [_reference(source / name) for name in premarket.STRATEGY_SUPPORT],
                "configuration": config_ref, "python": _reference(python.resolve(strict=True))}
    snapshot["coordinator_sources"] = [_reference(Path(module.__file__).resolve())
                                       for module in (sys.modules[__name__], workspace, premarket)]
    return snapshot


def _limits(profile):
    configuration, _ = _control(Path(profile["config"]), 65536)
    configured = configuration.get("limits", {})
    _need(isinstance(configured, dict), "CORE_CONFIGURED_LIMITS_MAPPING_REQUIRED")
    limits = dict(LIMITS)
    for key in limits:
        value = configured.get(key)
        if value is None and key == "max_seconds":
            continue
        if key in configured:
            valid_type = type(value) in (int, float) if key == "max_seconds" else type(value) is int
            _need(valid_type and math.isfinite(value) and value > 0, "CORE_FINITE_POSITIVE_LIMIT_REQUIRED")
            limits[key] = min(limits[key], value)
    return limits


def _summary(run, manifest, request):
    _need(run["observation_date"] == request["observation_date"] and
          run["decision_cutoff"] == request["cutoff"] and run["coverage_type"] == request["coverage_type"],
          "CORE_NATIVE_RUN_CONTEXT_CHANGED")
    _need(manifest["run_id"] == run["run_id"] and manifest["canonical_content_hash"] == run["content_hash"]
          and manifest["observation_date"] == request["observation_date"]
          and manifest["target_session"] == request["target_session"]
          and manifest["decision_cutoff"] == request["cutoff"], "CORE_NATIVE_MANIFEST_CONTEXT_CHANGED")
    result = run["results"][STRATEGY]
    _need(result["counts"] == manifest["counts"], "CORE_NATIVE_COUNTS_CHANGED")
    return {"run_id": run["run_id"], "input_hash": manifest["input_hash"],
            "canonical_content_hash": run["content_hash"], "counts": deepcopy(result["counts"]),
            "observation_date": request["observation_date"], "target_session": request["target_session"],
            "decision_cutoff": request["cutoff"], "coverage_type": request["coverage_type"],
            "strategy_id": STRATEGY, "parameter_status": "UNVALIDATED", "market_edge": "NOT_ESTABLISHED",
            "daily_v1_readiness": "UNKNOWN_NOT_EXECUTED", "premarket_completeness": "NOT_EXECUTED_CORE_ONLY",
            "source_authority_restored": False, "result_role": "ARCHIVED_NATIVE_CORE_RESEARCH",
            "normalization_gap_count": len(manifest.get("normalization_report", {}).get("gaps", []))}


def _run_original(request, job, limits):
    from stockfish_core.yaogu import premarket_native_core_v1 as core
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget
    meter = _ACTIVE_BUDGET.get()
    budget = Budget(meter.remaining(limits) if meter else limits, job_dir=job)
    primary = None
    try:
        with budget.phase("core"), budget.meter_files():
            owner = core.run_core(captures=request["captures"], observation_date=request["observation_date"],
                target_session=request["target_session"], cutoff=request["cutoff"], output_dir=job / "core",
                budget=budget, coverage_type=request["coverage_type"])
            owner.validate()
            run, artifacts, references = owner.run, owner.artifacts, owner.validation_references
    except BaseException as error:
        primary = error
        raise
    finally:
        if meter:
            try:
                meter.charge(sum(budget.metrics["returned_read_bytes"].values()), native=True)
            except CoreError as error:
                if primary is None:
                    raise
                if hasattr(primary, "add_note"):
                    primary.add_note(str(error))
    _need(set(artifacts) == FILES, "CORE_ALL_EIGHT_ORIGINAL_ARTIFACTS_REQUIRED")
    manifest, _ = _control(Path(artifacts["manifest.json"]["path"]))
    return _summary(run, manifest, request), artifacts, references, budget.metrics


def _execute_core(runtime, manifest_path, *, allow_test_fixture=False):
    """Run once, or return exact saved core after full original-byte verification."""
    started = time.monotonic()
    _need(runtime is not None and runtime.profile.get("backend") == "premarket", "CORE_REGISTERED_PREMARKET_RUNTIME_REQUIRED")
    limits = _limits(runtime.profile)
    _ACTIVE_BUDGET.get().tighten(limits)
    request, request_ref = _request(manifest_path, allow_test_fixture)
    source_snapshot = _snapshot(runtime.profile)
    identity = {"schema_version": VERSION, "request_digest": _digest(request),
                "captures_digest": _digest(request["captures"]), "implementation_digest": _digest(source_snapshot),
                **{key: request[key] for key in ("observation_date", "target_session", "cutoff", "coverage_type")},
                "limits": limits}
    identity_digest = _digest(identity)
    report_root = Path(runtime.profile["report_root"])

    def action():
        capacity = runtime.preflight(expected_growth_bytes=limits["output_bytes"] + MAX_MANIFEST,
                                     destination=report_root)
        _need(capacity["allowed"], "CORE_CAPACITY_PREFLIGHT_BLOCKED")
        # Mixed fixtures cannot enter the native real-data path. The original
        # authenticator still validates all trios, source clocks and producers.
        for capture in request["captures"]:
            result, actual = _control(Path(capture["result"]["path"]), MAX_SOURCE_BYTES)
            _need(actual == capture["result"], "CORE_CAPTURE_REFERENCE_CHANGED")
            fixture = (str(result.get("provider", "")).startswith("TEST_FIXTURE") or
                       str(result.get("field_version", "")).startswith("TEST_FIXTURE") or
                       any("TEST_FIXTURE.invalid" in str(r.get("source_url", ""))
                           for r in result.get("receipts", [])))
            _need(fixture == allow_test_fixture, "CORE_MIXED_TEST_FIXTURE_AND_REAL_FORBIDDEN")
        job = report_root / ("frozen-core-" + uuid.uuid4().hex)
        workspace._directory(job, create=True)
        summary, artifacts, references, metrics = _run_original(request, job, limits)
        evidence = {"schema_version": VERSION, "identity_digest": identity_digest, "request": request_ref,
                    "implementation": source_snapshot, "summary": summary, "artifacts": artifacts,
                    "validation_references": references, "locators": [c["verification_locator"] for c in
                        request["captures"] if c.get("verification_locator") is not None], "metrics": metrics}
        workspace._write(job / "reuse-evidence.json", evidence)
        return {"schema_version": VERSION, "identity_digest": identity_digest,
                "evidence": _reference(job / "reuse-evidence.json", MAX_MANIFEST), "summary": summary}

    def validate(receipt):
        _need(receipt.get("schema_version") == VERSION and receipt.get("identity_digest") == identity_digest,
              "CORE_REUSE_IDENTITY_CHANGED")
        evidence_path = Path(receipt["evidence"]["path"])
        _need(evidence_path.name == "reuse-evidence.json" and evidence_path.parent.parent == report_root
              and evidence_path.parent.name.startswith("frozen-core-"), "CORE_EVIDENCE_OUTSIDE_REPORT_ROOT")
        evidence, ref = _control(evidence_path)
        _need(ref == receipt["evidence"] and evidence["schema_version"] == VERSION and
              evidence["identity_digest"] == identity_digest and
              evidence["implementation"] == source_snapshot and evidence["summary"] == receipt["summary"],
              "CORE_REUSE_EVIDENCE_CHANGED")
        _need(_snapshot(runtime.profile) == source_snapshot, "CORE_IMPLEMENTATION_CHANGED")
        returned = _verify(request_ref)
        if evidence["request"] != request_ref:
            original_request, original_reference = _request(evidence["request"]["path"], allow_test_fixture)
            _need(original_reference == evidence["request"] and
                  _digest(original_request) == _digest(request),
                  "CORE_ORIGINAL_REQUEST_CHANGED")
            returned += original_reference["bytes"]
        refs = {}
        for reference in evidence["validation_references"]:
            previous = refs.setdefault(reference["path"], reference)
            _need(previous == reference, "CORE_CONFLICTING_VALIDATION_REFERENCES")
        for capture in request["captures"]:
            for reference in [capture[k] for k in ("result", "raw", "receipt")] + capture.get("producer_files", []):
                _need(refs.get(reference["path"]) == reference, "CORE_CAPTURE_NOT_FULLY_REGISTERED")
        _need(set(evidence["artifacts"]) == FILES, "CORE_ALL_EIGHT_ORIGINAL_ARTIFACTS_REQUIRED")
        for name, reference in evidence["artifacts"].items():
            _need(Path(reference["path"]) == evidence_path.parent / "core" / name,
                  "CORE_ARTIFACT_OUTSIDE_ORIGINAL_JOB")
            previous = refs.setdefault(reference["path"], reference)
            _need(previous == reference, "CORE_CONFLICTING_VALIDATION_REFERENCES")
        _need(len(refs) <= 1024 and sum(r["bytes"] for r in refs.values()) <= MAX_SOURCE_BYTES + limits["output_bytes"],
              "CORE_REUSE_ORIGINAL_LIMIT")
        for reference in refs.values():
            returned += _verify(reference)
        if evidence["locators"]:
            from stockfish_core.yaogu.premarket_native_core_v1 import _LocatorBinding
            for locator in evidence["locators"]:
                _LocatorBinding(locator).validate()
        manifest, _ = _control(Path(evidence["artifacts"]["manifest.json"]["path"]))
        summary = receipt["summary"]
        for key in ("run_id", "input_hash", "canonical_content_hash", "counts", "observation_date", "target_session", "decision_cutoff"):
            _need(manifest[key] == summary[key], "CORE_SAVED_SUMMARY_DIFFERS_FROM_ORIGINAL_MANIFEST")
        return {"verified": True, "archived_bytes_verified": True, "source_authority_restored": False,
                "currentness_recomputed": False, "full_premarket_executed": False,
                "original_file_count": len(refs), "original_distinct_path_bytes": sum(r["bytes"] for r in refs.values()),
                "verification_stream_bytes": returned,
                "read_accounting_scope": "Explicit original SHA passes only; metadata rereads and code snapshots excluded; not total kernel I/O",
                "original_core_metrics": evidence["metrics"]}

    result = runtime.run_once(identity, action, validate)
    result["total_elapsed_seconds"] = time.monotonic() - started
    result["summary"] = deepcopy(result["receipt"]["summary"])
    result["normalizer_calls"] = 0 if result["reused"] else 1
    result["native_r2_calls"] = 0 if result["reused"] else 1
    result["note"] = "Exact saved core only; R2 internally retains its R1 comparison. Missing evidence remains UNKNOWN."
    return result


def execute_core(runtime, manifest_path, *, allow_test_fixture=False):
    """One bounded invocation; source authentication and reuse share the same limits."""
    meter = _InvocationBudget()
    token = _ACTIVE_BUDGET.set(meter)
    try:
        result = _execute_core(runtime, manifest_path, allow_test_fixture=allow_test_fixture)
        result["invocation_metrics"] = meter.metrics()
        return result
    finally:
        _ACTIVE_BUDGET.reset(token)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime")
    parser.add_argument("--registry-root")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--allow-test-fixture", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = execute_core(workspace.load_runtime(args.runtime, args.registry_root), args.manifest,
                              allow_test_fixture=args.allow_test_fixture)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (CoreError, workspace.WorkspaceError, premarket.PremarketError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, ensure_ascii=False))
        return 3
    except Exception as error:
        # Original provider payloads and private paths are not console errors.
        # The durable coordinator claim preserves failed/uncertain execution.
        print(json.dumps({"status": "FAILED", "reason": "CORE_ORIGINAL_EXECUTION_OR_VALIDATION_FAILED",
                          "error_type": type(error).__name__}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
