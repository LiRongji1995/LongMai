"""Explicit RESULT compression with original RAW/receipt source anchors.

Only a new provider attempt owned by this invocation can lose its unpublished
RESULT wrapper. RAW, receipts and producer files remain physical originals.
The opt-in reader returns logical RESULT bytes with the *stored gzip's* real
reference; that mapping is declared by this protocol, never a fabricated inode.
The unchanged native authenticator still opens the original RAW and receipt.
No saved manifest restores a NativeCore owner or historic point-in-time status.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_core as original
from scripts import codex_workspace as workspace

VERSION = "CODEX_CAPTURE_RESULT_COMPACT_V1"
CORE_VERSION = "CODEX_COMPACT_CAPTURE_CORE_REQUEST_V1"
INDEX = "compact-result-v1"
MIB = 1024 ** 2
MAX_SOURCE = 16 * MIB
MAX_CONTROL = 2 * MIB
CHUNK = 65536
need = original._need


def _path(value):
    path = Path(value).absolute()
    need(path.is_absolute() and ".." not in path.parts, "COMPACT_CAPTURE_ABSOLUTE_PATH_REQUIRED")
    return path


def _limits(runtime):
    from stockfish_core.yaogu.premarket_runtime_v1 import DEFAULT_LIMITS
    config, _ = original._control(Path(runtime.profile["config"]), 65536)
    need(config.get("state_root") == runtime.profile["state_root"], "COMPACT_CAPTURE_STATE_BINDING")
    supplied = config.get("limits", {})
    need(type(supplied) is dict and not set(supplied) - set(DEFAULT_LIMITS), "COMPACT_CAPTURE_LIMIT_KEYS")
    limits = dict(DEFAULT_LIMITS)
    limits.update(capture_read_bytes=128 * MIB, core_read_bytes=512 * MIB,
        max_requests=1, max_sdk_calls=52, max_http_attempts=64, request_http_attempts=64,
        max_seconds=180, output_bytes=64 * MIB, cache_bytes=32 * MIB,
        self_rss_bytes=1536 * MIB, tree_rss_bytes=2 * 1024 * MIB)
    for key, value in supplied.items():
        if key == "max_seconds" and value is None:
            continue
        need(type(value) in (int, float) and value > 0, "COMPACT_CAPTURE_POSITIVE_LIMIT_REQUIRED")
        limits[key] = min(limits[key], value)
    return limits, config


def _runtime(runtime):
    need(runtime is not None and runtime.profile.get("backend") == "premarket", "COMPACT_CAPTURE_RUNTIME_REQUIRED")
    need(Path(runtime.profile["source_root"]) == ROOT, "COMPACT_CAPTURE_REGISTERED_SOURCE_REQUIRED")
    need(Path(sys.executable).absolute() == Path(runtime.profile["python"]), "COMPACT_CAPTURE_REGISTERED_PYTHON_REQUIRED")
    runtime.check_binding()


def _physical(budget, path, expected=None, maximum=MAX_SOURCE):
    # The original safe fd reader rejects links/cloud placeholders before open.
    with nullcontext() if budget._meter_active else budget.meter_files():
        ref, raw = original._read_file(_path(path), maximum, retain=True)
    if expected is not None:
        need(ref == expected if isinstance(expected, dict) else ref["sha256"] == expected,
             "COMPACT_CAPTURE_PHYSICAL_REFERENCE_CHANGED")
    return raw, ref


def _document(budget, path, expected=None):
    raw, ref = _physical(budget, path, expected, MAX_CONTROL)
    return original.premarket._json(raw), ref


def _save(budget, path, value):
    workspace._write(_path(path), value)
    return _physical(budget, path, maximum=MAX_CONTROL)[1]


def _decode(budget, raw, logical, limit=None):
    need(set(logical) == {"bytes", "sha256"} and type(logical["bytes"]) is int
         and 0 <= logical["bytes"] <= MAX_SOURCE and
         re.fullmatch(r"[0-9a-f]{64}", str(logical["sha256"])), "COMPACT_CAPTURE_LOGICAL_SCHEMA")
    need(limit is None or logical["bytes"] <= limit, "COMPACT_CAPTURE_LOGICAL_READ_LIMIT")
    decoder, decoded, size, digest = zlib.decompressobj(31), bytearray(), 0, hashlib.sha256()
    try:
        for start in range(0, len(raw), CHUNK):
            pending = raw[start:start + CHUNK]
            while pending:
                budget.check("compact_result_decode")
                part = decoder.decompress(pending, min(CHUNK, logical["bytes"] - size + 1))
                pending = decoder.unconsumed_tail
                budget.charge_read(len(part))
                size += len(part)
                need(size <= logical["bytes"], "COMPACT_CAPTURE_EXPANSION_LIMIT")
                digest.update(part)
                decoded.extend(part)
                need(not decoder.unused_data, "COMPACT_CAPTURE_TRAILING_GZIP_BYTES")
        need(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,
             "COMPACT_CAPTURE_INCOMPLETE_GZIP")
    except zlib.error as error:
        raise original.CoreError("COMPACT_CAPTURE_INVALID_GZIP") from error
    need(size == logical["bytes"] and digest.hexdigest() == logical["sha256"],
         "COMPACT_CAPTURE_LOGICAL_BYTES_CHANGED")
    return decoded


class CompressedResultBudget:
    """Explicit logical reader; every reference still describes real disk bytes.

    NativeCore's registry is injected with this reader. RAW/receipt readers and
    the provider's mature verifier are unchanged. This adapter must never be
    supplied to the legacy CaptureCache, whose read contract is physical-only.
    """
    def __init__(self, budget, entries):
        self.base = budget
        self.entries = {}
        for item in entries:
            path = item["stored"]["path"]
            need(path not in self.entries, "COMPACT_CAPTURE_DUPLICATE_RESULT")
            self.entries[path] = deepcopy(item)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def read(self, path, expected=None, limit=None):
        path = str(_path(path))
        item = self.entries.get(path)
        if item is None:
            return _physical(self.base, path, expected, MAX_SOURCE if limit is None else limit)
        stored = item["stored"]
        need(expected is None or expected == stored or expected == stored["sha256"],
             "COMPACT_CAPTURE_EXPECTED_STORED_REFERENCE")
        raw, reference = _physical(self.base, path, stored, MAX_SOURCE + MIB)
        return _decode(self.base, raw, item["logical"], limit), reference


def _authenticate(budget, capture, *, fixture):
    from stockfish_core.yaogu import premarket_native_core_v1 as native
    from stockfish_core.yaogu import provider_requests
    registry = native._BudgetRegistry(budget)
    result = native._json(registry.read(capture["result"]["path"], capture["result"]))
    physical = native._json(registry.read(capture["receipt"]["path"], capture["receipt"]))
    # Pin RAW before the original verifier's direct reads, including residency.
    registry.read(capture["raw"]["path"], capture["raw"])
    expected_provider = provider_requests.ENDPOINTS[result["endpoint"]]["provider"]
    need(result["provider"] == expected_provider or fixture and result["provider"] == "TEST_FIXTURE",
         "COMPACT_CAPTURE_PROVIDER_OR_EXPLICIT_FIXTURE_REQUIRED")
    locators = []
    native._pin_capture_aux(capture, result, registry,
        "TEST_FIXTURE" if fixture else "PROSPECTIVE_CAPTURED", locators)
    declared = native._typed(result, physical)
    registration = dict(archive_root=capture["archive_root"],
        **{name: native._two(capture[name]) for name in ("result", "raw", "receipt")},
        typed_receipt=declared.model_dump(mode="json"))
    receipt, _, audit = native._authenticate_capture(registry, registration, Path.cwd())
    registry.terminal()
    for locator in locators:
        locator.validate()
    return result, receipt, audit


def _load(runtime, path, budget, *, fixture=False, expected=None):
    from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec
    from scripts.codex_compact_artifacts import _reference_shape
    value, reference = _document(budget, path, expected)
    required = {"schema_version", "request_spec", "request_key", "capture", "result_storage",
                "retired_unpublished_result", "coordinator", "fixture", "source_authority"}
    need(type(value) is dict and set(value) == required and value["schema_version"] == VERSION,
         "COMPACT_CAPTURE_MANIFEST_SCHEMA")
    need(type(value["fixture"]) is bool and value["fixture"] is fixture,
         "COMPACT_CAPTURE_FIXTURE_SEPARATION")
    need(value["source_authority"] == "ORIGINAL_RAW_RECEIPT_AND_PRODUCER_FILES",
         "COMPACT_CAPTURE_AUTHORITY_BOUNDARY")
    spec = RequestSpec(**value["request_spec"])
    need(spec.key == value["request_key"], "COMPACT_CAPTURE_REQUEST_IDENTITY")
    source = Path(runtime.profile["state_root"]) / "cache" / "raw" / spec.key
    manifest_path = _path(path)
    need(manifest_path.name == "compact-result.json" and manifest_path.parent.parent == source / "raw",
         "COMPACT_CAPTURE_MANIFEST_OUTSIDE_REQUEST_ROOT")
    capture, storage = value["capture"], value["result_storage"]
    need(type(capture) is dict and set(capture) == {"archive_root", "raw", "receipt", "result", "producer_files"},
         "COMPACT_CAPTURE_SOURCE_SCHEMA")
    need(capture["archive_root"] == str(source) and set(storage) == {"stored", "logical"}
         and storage["stored"] == capture["result"]
         and capture["result"]["path"] == str(manifest_path.parent / "result.json.gz")
         and Path(capture["raw"]["path"]).parent == manifest_path.parent / "raw"
         and Path(capture["receipt"]["path"]).parent == manifest_path.parent / "raw",
         "COMPACT_CAPTURE_PHYSICAL_SOURCE_SCOPE")
    retired = value["retired_unpublished_result"]
    _reference_shape(retired)
    retired_path = _path(retired["path"])
    need(retired_path.parent == manifest_path.parent and
         re.fullmatch(r"request-.+\.json", retired_path.name) and
         retired_path.name != "request-claim.json" and
         {key: retired[key] for key in ("bytes", "sha256")} == storage["logical"],
         "COMPACT_CAPTURE_RETIRED_LOGICAL_REFERENCE")
    # This is a historical locator only. Its recorded inode is never reopened
    # or accepted as a capability to authenticate the source or delete a file.
    need(value["coordinator"]["path"] == str(Path(__file__).resolve()), "COMPACT_CAPTURE_COORDINATOR_SCOPE")
    _physical(budget, value["coordinator"]["path"], value["coordinator"], MAX_SOURCE)
    adapter = CompressedResultBudget(budget, [storage])
    result, receipt, audit = _authenticate(adapter, capture, fixture=fixture)
    need(result["endpoint"] == spec.endpoint and result["parameters"] == spec.parameters,
         "COMPACT_CAPTURE_REQUEST_SCOPE_CHANGED")
    if spec.current:
        from stockfish_core.yaogu.premarket_capture_cache_v1 import SH
        need(spec.business_session == receipt.first_seen_at.astimezone(SH).date().isoformat(),
             "COMPACT_CAPTURE_CURRENT_SESSION_CHANGED")
    _physical(budget, path, reference, MAX_CONTROL)
    return value, reference, audit


def verify_capture_archive(runtime, archive_path, *, allow_test_fixture=False):
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget
    _runtime(runtime)
    need(type(allow_test_fixture) is bool, "COMPACT_CAPTURE_FIXTURE_FLAG")
    limits, _ = _limits(runtime)
    budget = Budget(limits)
    with runtime.exclusive(), budget.phase("capture"), budget.meter_files():
        value, ref, audit = _load(runtime, archive_path, budget, fixture=allow_test_fixture)
        runtime.check_binding()
    return dict(status="VERIFIED", schema_version=VERSION, archive=ref, audit=audit,
        result_logical_bytes=value["result_storage"]["logical"]["bytes"],
        result_stored_bytes=value["result_storage"]["stored"]["bytes"],
        native_owner_restored=False, historical_pit_restored=False, metrics=budget.metrics)


def execute_capture(runtime, request_path, *, execute_network=False, allow_test_fixture=False,
                    _fixture_executor=None, _supervisor_control=None):
    from stockfish_core.yaogu import premarket_capture_cache_v1 as cache_module
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget
    from stockfish_core.yaogu.provider_requests import execute_request
    _runtime(runtime)
    need(type(execute_network) is bool and type(allow_test_fixture) is bool, "COMPACT_CAPTURE_BOOLEAN_FLAGS")
    need(_fixture_executor is None or allow_test_fixture, "COMPACT_CAPTURE_TEST_EXECUTOR_FORBIDDEN")
    need(not allow_test_fixture or not execute_network, "COMPACT_CAPTURE_FIXTURE_NETWORK_FORBIDDEN")
    limits, configuration = _limits(runtime)
    cache_root = Path(runtime.profile["state_root"]) / "cache"
    budget = Budget(limits, cache_dir=cache_root)
    with runtime.exclusive(), budget.phase("capture"), budget.meter_files():
        document, _ = _document(budget, request_path)
        need(type(document) is dict and set(document) == {"endpoint", "parameters", "business_session", "role", "current"},
             "COMPACT_CAPTURE_EXPLICIT_REQUEST_SPEC")
        spec = cache_module.RequestSpec(**document)
        # Initial creation is subject to shared admission as well as the worker.
        admission = runtime.preflight(expected_growth_bytes=3 * MAX_SOURCE + MIB, destination=cache_root)
        need(admission["allowed"], "COMPACT_CAPTURE_CAPACITY_BLOCKED")
        config = dict(configuration.get("capture", {}))
        config["allow_test_fixture"] = allow_test_fixture
        cache = cache_module.CaptureCache(cache_root, budget, config=config)
        with cache._writer():
            index = cache_root / INDEX / (spec.key + ".json")
            if index.exists() or index.is_symlink():
                saved, _ = _document(budget, index)
                need(set(saved) == {"schema_version", "archive"} and saved["schema_version"] == VERSION,
                     "COMPACT_CAPTURE_INDEX_SCHEMA")
                value, reference, audit = _load(runtime, saved["archive"]["path"], budget,
                    fixture=allow_test_fixture, expected=saved["archive"])
                need(value["request_key"] == spec.key, "COMPACT_CAPTURE_INDEX_REQUEST_CHANGED")
                return dict(status="REUSED", schema_version=VERSION, archive=reference, audit=audit,
                    new_provider_calls=0, new_large_artifact_bytes=0, metrics=budget.metrics)
            legacy = cache.lookup(spec)
            if legacy is not None:
                cache.terminal()
                return dict(status="REUSED_LEGACY_ORIGINAL", capture=legacy.metadata,
                    new_provider_calls=0, new_large_artifact_bytes=0, metrics=budget.metrics)
            need(execute_network or allow_test_fixture and _fixture_executor is not None,
                 "COMPACT_CAPTURE_NETWORK_FLAG_REQUIRED")
            need(not config.get("offline", False), "COMPACT_CAPTURE_CONFIGURED_OFFLINE")
            # No retry option is accepted. The original cache lookup rejects an
            # unfinished attempt, including an interrupted compact publication.
            budget.preclaim_request(spec.endpoint, spec.parameters)
            request_archive = cache_root / "raw" / spec.key
            output = request_archive / "raw" / uuid.uuid4().hex
            cache._mkdir_owned(output)
            output_identity = output.stat()
            if _supervisor_control is not None:
                _publish_owned(runtime, _supervisor_control, output, "capture")
            _save(budget, output / "request-claim.json", dict(schema_version=VERSION,
                request_spec=spec.to_dict(), started_at=datetime.now(timezone.utc).isoformat(), retries=0))
            try:
                executor = _fixture_executor or execute_request
                kwargs = dict(timeout=cache.timeout, max_http_requests=cache.max_http)
                for key in ("budget_path", "budget_id"):
                    if key in config:
                        kwargs[key] = config[key]
                answer = executor(spec.endpoint, spec.parameters, output, **kwargs)
                budget.record_request(answer)
                receipt_path = output / "raw" / (answer["request_receipt_id"] + ".receipt.json")
                receipt, _ = _document(budget, receipt_path)
                raw_path = output / "raw" / (receipt["content_hash"] + ".raw.json")
                candidates = [p for p in output.glob("request-*.json") if p.name != "request-claim.json"]
                need(len(candidates) == 1, "COMPACT_CAPTURE_ONE_PROVIDER_RESULT_REQUIRED")
                result_path = candidates[0]
                payload, old_result = _physical(budget, result_path)
                need(original.premarket._json(payload) == answer and answer["endpoint"] == spec.endpoint
                     and answer["parameters"] == spec.parameters, "COMPACT_CAPTURE_EXECUTOR_SCOPE")
                _, authenticated = cache._authenticate(dict(result=str(result_path), raw=str(raw_path),
                    receipt=str(receipt_path)), str(request_archive))
                cache.terminal()
                need(not authenticated.get("verification_locator"), "COMPACT_CAPTURE_UNEXPECTED_LOCATOR")
                source_refs = {key: deepcopy(authenticated[key]) for key in ("raw", "receipt", "producer_files")}
                compressor = zlib.compressobj(6, zlib.DEFLATED, 31)
                compressed_path = output / "result.json.gz"
                with workspace._directory_fd(output) as directory_fd:
                    fd = os.open(compressed_path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory_fd)
                    with os.fdopen(fd, "wb") as stream:
                        for offset in range(0, len(payload), CHUNK):
                            budget.check("compact_result_encode")
                            stream.write(compressor.compress(payload[offset:offset + CHUNK]))
                        stream.write(compressor.flush())
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.fsync(directory_fd)
                stored_raw, stored = _physical(budget, compressed_path, maximum=MAX_SOURCE + MIB)
                storage = dict(stored=stored, logical={key: old_result[key] for key in ("sha256", "bytes")})
                need(_decode(budget, stored_raw, storage["logical"]) == payload, "COMPACT_CAPTURE_ROUNDTRIP_CHANGED")
                capture = dict(archive_root=str(request_archive), result=stored, **source_refs)
                value = dict(schema_version=VERSION, request_spec=spec.to_dict(), request_key=spec.key,
                    capture=capture, result_storage=storage, retired_unpublished_result=old_result,
                    coordinator=_physical(budget, Path(__file__).resolve())[1], fixture=allow_test_fixture,
                    source_authority="ORIGINAL_RAW_RECEIPT_AND_PRODUCER_FILES")
                manifest = output / "compact-result.json"
                ref = _save(budget, manifest, value)
                # Complete the NEW reader -> original authentication chain before
                # removing only the invocation's just-created RESULT wrapper.
                _load(runtime, manifest, budget, fixture=allow_test_fixture, expected=ref)
                _physical(budget, result_path, old_result)
                with workspace._directory_fd(output) as directory_fd:
                    current = os.fstat(directory_fd)
                    need((current.st_dev, current.st_ino, current.st_mode) ==
                         (output_identity.st_dev, output_identity.st_ino, output_identity.st_mode),
                         "COMPACT_CAPTURE_OWNED_DIRECTORY_CHANGED")
                    current = os.stat(result_path.name, dir_fd=directory_fd, follow_symlinks=False)
                    need(current.st_nlink == 1 and original._fp(current) == old_result["fp"],
                         "COMPACT_CAPTURE_OWNED_RESULT_CHANGED")
                    os.unlink(result_path.name, dir_fd=directory_fd)
                    os.fsync(directory_fd)
                _load(runtime, manifest, budget, fixture=allow_test_fixture, expected=ref)
                cache._mkdir_owned(index.parent)
                _save(budget, index, dict(schema_version=VERSION, archive=ref))
                runtime.check_binding()
                budget.check("compact_capture_complete", force=True)
                return dict(status="RECORDED", schema_version=VERSION, archive=ref,
                    original_result_bytes=old_result["bytes"], stored_result_bytes=stored["bytes"],
                    avoided_result_bytes=old_result["bytes"] - stored["bytes"],
                    old_files_modified=False, raw_receipt_preserved=True, native_owner_restored=False,
                    new_provider_calls=1, metrics=budget.metrics)
            except BaseException as error:
                # Failure is durable; never erase raw attempts to enable retry.
                try:
                    _save(budget, output / "compact-failed.json", dict(schema_version=VERSION,
                        status="FAILED_RETAINED_NO_RETRY", error_type=type(error).__name__))
                except BaseException:
                    pass
                raise


def _core_snapshot(runtime):
    from scripts import codex_compact_core_v2, codex_verification_v2, codex_first_core
    snapshot = codex_compact_core_v2._snapshot(runtime.profile)
    snapshot["capture_coordinators"] = [original._reference(Path(module.__file__).resolve())
        for module in (sys.modules[__name__], codex_first_core, codex_verification_v2)]
    return snapshot


def _packing_limits(limits):
    from scripts.codex_compact_artifacts import CompactLimits
    # One shared output envelope includes native staging plus packed bytes.
    logical = min(128 * MIB, max(1, (limits["output_bytes"] - 2 * MIB) // 2))
    return CompactLimits(logical_bytes=logical, stored_bytes=logical,
        peak_bytes=limits["output_bytes"], returned_read_bytes=limits["core_read_bytes"],
        max_seconds=min(300, limits["max_seconds"]))


def execute_core(runtime, request_path, *, allow_test_fixture=False, _supervisor_control=None):
    """Low-level worker: original C/128 core, owned compression and full semantics.

    CLI callers use supervised_command. Every action and immediate verification
    shares one Budget; no phase receives another complete read/time allowance.
    """
    from contextlib import nullcontext
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget
    from stockfish_core.yaogu.premarket_native_core_v1 import run_core
    from scripts import codex_compact_artifacts as packed
    from scripts import codex_compact_core_v2 as verifier
    from scripts import codex_verification_v2 as verification
    from scripts import codex_first_core as first
    _runtime(runtime)
    limits, _ = _limits(runtime)
    budget = Budget(limits)
    with budget.phase("core"), budget.meter_files():
        request, request_ref = _document(budget, _path(request_path))
        need(type(request) is dict and set(request) == {"schema_version", "compact_archives", "observation_date",
            "target_session", "cutoff", "coverage_type"} and request["schema_version"] == CORE_VERSION,
            "COMPACT_CAPTURE_CORE_REQUEST_SCHEMA")
        need(type(allow_test_fixture) is bool and request["coverage_type"] in
            {"TEST_FIXTURE", "PROSPECTIVE_CAPTURED", "RECONSTRUCTED_HISTORICAL"} and
            (request["coverage_type"] == "TEST_FIXTURE") is allow_test_fixture,
            "COMPACT_CAPTURE_CORE_FIXTURE_SEPARATION")
        need(type(request["compact_archives"]) is list and 1 <= len(request["compact_archives"]) <= 128,
             "COMPACT_CAPTURE_CORE_SOURCE_COUNT")
        need(original._clock(request["cutoff"]) == request["cutoff"], "COMPACT_CAPTURE_CANONICAL_CUTOFF")
        from datetime import date
        need(date.fromisoformat(request["target_session"]) > date.fromisoformat(request["observation_date"]),
             "COMPACT_CAPTURE_NEXT_SESSION_REQUIRED")
        snapshot = _core_snapshot(runtime)
    identity = dict(schema_version=CORE_VERSION, request_digest=original._digest(request),
        request_reference_digest=original._digest(request_ref), source_digest=original._digest(snapshot),
        encoding="TYPED_ROWS_128_V1", storage=packed.VERSION, verification=verifier.VERSION)
    identity_hash = original._digest(identity)
    report_root = Path(runtime.profile["report_root"])

    def sources():
        with nullcontext() if budget._meter_active else budget.meter_files():
            loaded = [_load(runtime, ref["path"], budget, fixture=allow_test_fixture, expected=ref)
                      for ref in request["compact_archives"]]
        values = [row[0] for row in loaded]
        return [value["capture"] for value in values], CompressedResultBudget(budget,
            [value["result_storage"] for value in values])

    def terminal():
        sources()
        with nullcontext() if budget._meter_active else budget.meter_files():
            _physical(budget, request_ref["path"], request_ref, MAX_CONTROL)
            need(_core_snapshot(runtime) == snapshot, "COMPACT_CAPTURE_CORE_SOURCE_CHANGED_AT_TERMINAL")
        runtime.check_binding()

    def action():
        admission = runtime.preflight(expected_growth_bytes=limits["output_bytes"] + MAX_CONTROL,
                                      destination=report_root)
        need(admission["allowed"], "COMPACT_CAPTURE_CORE_CAPACITY_BLOCKED")
        job = report_root / ("compact-capture-core-" + uuid.uuid4().hex)
        workspace._directory(job, create=True)
        if _supervisor_control is not None:
            _publish_owned(runtime, _supervisor_control, job, "core")
        budget.job_dir = job
        with budget.phase("core"), budget.meter_files():
            captures, adapter = sources()
            stage = packed.create_staging(job)
            with first.encoding_scope(first.OPTIMIZED):
                owner = run_core(captures=captures, observation_date=request["observation_date"],
                    target_session=request["target_session"], cutoff=request["cutoff"],
                    output_dir=stage.core_dir, budget=adapter, coverage_type=request["coverage_type"])
                owner.validate()
                artifacts = owner.artifacts
                manifest = json.loads(adapter.read(artifacts["manifest.json"]["path"], artifacts["manifest.json"])[0])
                original_request = {key: request[key] for key in ("observation_date", "target_session", "cutoff", "coverage_type")}
                summary = original._summary(owner.run, manifest, original_request)
                storage = packed.compact_owned(stage, artifacts, destination=job / "artifacts",
                    terminal_validator=owner.validate, limits=_packing_limits(limits))
                owner = None
            evidence = dict(schema_version=CORE_VERSION, identity_digest=identity_hash,
                source_snapshot=snapshot, request=request_ref, summary=summary,
                original_logical_artifacts=artifacts, compact_manifest=storage["manifest"],
                compact_archives=request["compact_archives"],
                source_authority="ORIGINAL_RAW_RECEIPT_AND_PRODUCER_FILES", native_owner_serialized=False)
            terminal()
            evidence_ref = _save(budget, job / "compact-capture-core.json", evidence)
        return dict(evidence=evidence_ref, summary=summary)

    def validate(receipt):
        with budget.phase("core"):
            path = _path(receipt["evidence"]["path"])
            need(path.name == "compact-capture-core.json" and path.parent.parent == report_root
                 and path.parent.name.startswith("compact-capture-core-"), "COMPACT_CAPTURE_CORE_EVIDENCE_SCOPE")
            evidence, _ = _document(budget, path, receipt["evidence"])
            need(set(evidence) == {"schema_version", "identity_digest", "source_snapshot", "request", "summary",
                 "original_logical_artifacts", "compact_manifest", "compact_archives", "source_authority", "native_owner_serialized"}
                 and evidence["schema_version"] == CORE_VERSION and evidence["identity_digest"] == identity_hash
                 and evidence["source_snapshot"] == snapshot and evidence["request"] == request_ref
                 and evidence["compact_archives"] == request["compact_archives"]
                 and evidence["summary"] == receipt["summary"] and evidence["native_owner_serialized"] is False
                 and evidence["source_authority"] == "ORIGINAL_RAW_RECEIPT_AND_PRODUCER_FILES",
                 "COMPACT_CAPTURE_CORE_EVIDENCE_CHANGED")
            captures, adapter = sources()
            packed_ref = evidence["compact_manifest"]
            need(Path(packed_ref["path"]) == path.parent / "artifacts" / packed.MANIFEST_NAME,
                 "COMPACT_CAPTURE_CORE_PACK_SCOPE")
            Archive = verification.single_pass_archive_class(packed)
            class BoundedArchive(Archive):
                def read_artifact(self, role, *, max_bytes):
                    return super().read_artifact(role, max_bytes=min(max_bytes, self.meter.limits.logical_bytes))
            packing = _packing_limits(limits)
            shared_meter = packed._Meter(packing)
            local_charge = shared_meter.read
            def shared_charge(size):
                budget.charge_read(size)
                local_charge(size)
            shared_meter.read = shared_charge
            archive = BoundedArchive(packed_ref, limits=packing, _meter=shared_meter)
            try:
                sizes = archive.verify_all()
                logical = evidence["original_logical_artifacts"]
                need(set(logical) == original.FILES, "COMPACT_CAPTURE_CORE_EIGHT_ARTIFACTS")
                stage_parents = {Path(ref["path"]).parent for ref in logical.values()}
                need(len(stage_parents) == 1, "COMPACT_CAPTURE_CORE_LOGICAL_SCOPE")
                stage_core = next(iter(stage_parents))
                need(stage_core.name == "core" and stage_core.parent.parent == path.parent
                     and re.fullmatch(r"\.codex-compact-stage-[0-9a-f]{32}", stage_core.parent.name),
                     "COMPACT_CAPTURE_CORE_RETIRED_STAGING_SCOPE")
                for role, item in archive.manifest["artifacts"].items():
                    need(item["source_reference"] == logical[role] and
                         Path(logical[role]["path"]).name == role, "COMPACT_CAPTURE_CORE_ROLE_BINDING")
                manifest = original.premarket._json(archive.read_artifact("manifest.json", max_bytes=MAX_SOURCE))
                need(set(manifest["artifacts"]) == original.FILES - {"manifest.json"} and
                     all(ref == logical[name] for name, ref in manifest["artifacts"].items()),
                     "COMPACT_CAPTURE_CORE_NATIVE_MANIFEST_BINDING")
                for key in ("run_id", "input_hash", "canonical_content_hash", "counts", "observation_date",
                            "target_session", "decision_cutoff"):
                    need(manifest[key] == receipt["summary"][key], "COMPACT_CAPTURE_CORE_SUMMARY_CHANGED")
                class SemanticMeter:
                    def check(self):
                        budget.check("compact_capture_core_semantics")
                    def charge(self, count):
                        budget.charge_read(count)
                original_request = {key: request[key] for key in ("observation_date", "target_session", "cutoff", "coverage_type")}
                original_request["captures"] = captures
                verifier._verify_logical_core(archive, logical, manifest, receipt["summary"], original_request,
                    SemanticMeter(), source_read=lambda p, expected, maximum: adapter.read(p, expected, maximum))
            finally:
                budget.check("compact_capture_archive_read_complete")
            terminal()
            return dict(verified=True, source_authority_restored=False, complete_semantics_verified=True,
                original_logical_bytes=sizes["logical_bytes"], stored_payload_bytes=sizes["stored_bytes"])

    result = runtime.run_once(identity, action, validate)
    result.update(summary=result["receipt"]["summary"], native_r2_calls=0 if result["reused"] else 1,
        normalizer_calls=0 if result["reused"] else 1, selector_calls=0 if result["reused"] else 1,
        provider_calls=0, source_authority_restored=False, full_premarket_ready=False,
        invocation_metrics=budget.metrics)
    return result


def _supervisor_path(runtime, control):
    control = _path(control)
    need(control.parent == Path(runtime.profile["report_root"]) and
         re.fullmatch(r"capture-compact-supervisor-[0-9a-f]{32}", control.name),
         "COMPACT_CAPTURE_SUPERVISOR_SCOPE")
    with workspace._directory_fd(control):
        pass
    return control


def _publish_owned(runtime, control, job, kind):
    control = _supervisor_path(runtime, control)
    with workspace._directory_fd(job) as fd:
        value = os.fstat(fd)
    workspace._write(control / "owned-job.json", dict(schema_version=VERSION, kind=kind,
        path=str(job), identity={key: getattr(value, key) for key in ("st_dev", "st_ino", "st_mode")}))


def _monitor_owned(runtime, control, previous, limits):
    from stockfish_core.yaogu.premarket_runtime_v1 import directory_bytes
    declaration = control / "owned-job.json"
    if not declaration.exists() and not declaration.is_symlink():
        need(previous is None, "COMPACT_CAPTURE_OWNERSHIP_RECORD_DISAPPEARED")
        return None, 0
    value = workspace._read(declaration, limit=8192)
    need(type(value) is dict and set(value) == {"schema_version", "kind", "path", "identity"}
         and value["schema_version"] == VERSION and value["kind"] in {"core", "capture"},
         "COMPACT_CAPTURE_OWNERSHIP_SCHEMA")
    need(previous is None or previous == value, "COMPACT_CAPTURE_OWNERSHIP_CHANGED")
    job = _path(value["path"])
    if value["kind"] == "core":
        need(job.parent == Path(runtime.profile["report_root"]) and
             re.fullmatch(r"compact-capture-core-[0-9a-f]{32}", job.name), "COMPACT_CAPTURE_OWNED_CORE_SCOPE")
    else:
        root = Path(runtime.profile["state_root"]) / "cache" / "raw"
        need(job.is_relative_to(root), "COMPACT_CAPTURE_OWNED_SOURCE_SCOPE")
        parts = job.relative_to(root).parts
        need(len(parts) == 3 and re.fullmatch(r"[0-9a-f]{64}", parts[0]) and parts[1] == "raw"
             and re.fullmatch(r"[0-9a-f]{32}", parts[2]), "COMPACT_CAPTURE_OWNED_SOURCE_SCOPE")
    with workspace._directory_fd(job) as fd:
        current = os.fstat(fd)
    need(value["identity"] == {key: getattr(current, key) for key in ("st_dev", "st_ino", "st_mode")},
         "COMPACT_CAPTURE_OWNED_DIRECTORY_CHANGED")
    size = directory_bytes(job)
    need(size <= limits["output_bytes" if value["kind"] == "core" else "cache_bytes"],
         "COMPACT_CAPTURE_OWNED_OUTPUT_LIMIT")
    if value["kind"] == "capture":
        need(directory_bytes(Path(runtime.profile["state_root"]) / "cache") <= limits["cache_bytes"],
             "COMPACT_CAPTURE_TOTAL_CACHE_LIMIT")
    return value, size


def supervised_command(runtime, command, argument_path, *, execute_network=False, allow_test_fixture=False):
    """One isolated process group; wall/RSS/output limits include native gaps."""
    started = time.monotonic()
    from stockfish_core.yaogu.premarket_workflow_v1 import _tree_sample, _stop_owned, _owned_group_members
    from stockfish_core.yaogu.premarket_runtime_v1 import directory_bytes
    _runtime(runtime)
    need(command in {"capture", "verify", "core"} and os.name == "posix", "COMPACT_CAPTURE_SUPERVISOR_POSIX_COMMAND")
    need(command == "capture" or not execute_network, "COMPACT_CAPTURE_NETWORK_COMMAND_SCOPE")
    limits, _ = _limits(runtime)
    snapshot = _core_snapshot(runtime)
    report_root = Path(runtime.profile["report_root"])
    with runtime.exclusive():
        need(runtime.preflight(expected_growth_bytes=2 * MIB, destination=report_root)["allowed"],
             "COMPACT_CAPTURE_SUPERVISOR_CAPACITY_BLOCKED")
        control = report_root / ("capture-compact-supervisor-" + uuid.uuid4().hex)
        workspace._directory(control, create=True)
    output = control / "result.json"
    empty_lookup = control / "empty-pycache-lookup"
    workspace._directory(empty_lookup, create=True)
    arguments = [runtime.profile["python"], "-I", "-B", "-X", "pycache_prefix=" + str(empty_lookup),
        str(Path(__file__).resolve()),
        "--runtime", runtime.profile["name"], "--registry-root", str(runtime.registry_root),
        "--worker-result", str(output)]
    if allow_test_fixture:
        arguments.append("--allow-test-fixture")
    arguments.extend([command, "--archive" if command == "verify" else "--request", str(_path(argument_path))])
    if execute_network:
        arguments.append("--execute-network")
    process, owned, peak, peak_bytes = None, None, 0, 0
    try:
        with (control / "console.txt").open("xb") as console:
            process = subprocess.Popen(arguments, cwd=ROOT,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), stdout=console, stderr=subprocess.STDOUT,
                start_new_session=True)
            while process.poll() is None:
                try:
                    peak = max(peak, _tree_sample(process.pid))
                except Exception:
                    if process.poll() is None:
                        raise
                need(time.monotonic() - started < limits["max_seconds"], "COMPACT_CAPTURE_TREE_TIME_LIMIT")
                need(peak <= limits["tree_rss_bytes"], "COMPACT_CAPTURE_TREE_RSS_LIMIT")
                need(directory_bytes(control) <= MIB, "COMPACT_CAPTURE_CONTROL_OUTPUT_LIMIT")
                owned, size = _monitor_owned(runtime, control, owned, limits)
                peak_bytes = max(peak_bytes, size)
                time.sleep(.25)
            code = process.wait()
        need(not _owned_group_members(process.pid), "COMPACT_CAPTURE_OWNED_DESCENDANTS_REMAIN")
        need(time.monotonic() - started < limits["max_seconds"], "COMPACT_CAPTURE_TREE_TIME_LIMIT")
        need(directory_bytes(control) <= MIB, "COMPACT_CAPTURE_CONTROL_OUTPUT_LIMIT")
        owned, size = _monitor_owned(runtime, control, owned, limits)
        peak_bytes = max(peak_bytes, size)
        if code:
            error = control / "worker-error.json"
            if error.exists():
                failure = workspace._read(error, limit=8192)
                need(failure.get("schema_version") == VERSION and isinstance(failure.get("reason"), str),
                     "COMPACT_CAPTURE_WORKER_ERROR_SCHEMA")
                raise original.CoreError(failure["reason"])
            raise original.CoreError("COMPACT_CAPTURE_WORKER_FAILED")
        result = workspace._read(output)
        need(result.get("status") in {"VERIFIED", "REUSED", "REUSED_LEGACY_ORIGINAL", "RECORDED"},
             "COMPACT_CAPTURE_WORKER_RESULT_SCHEMA")
        need(result["status"] != "RECORDED" or owned is not None, "COMPACT_CAPTURE_MISSING_OWNED_JOB")
        if command == "core" and result["status"] == "RECORDED":
            need(Path(result["receipt"]["evidence"]["path"]).parent == Path(owned["path"]),
                 "COMPACT_CAPTURE_WORKER_CORE_BINDING")
        if command == "capture" and result["status"] == "RECORDED":
            need(Path(result["archive"]["path"]).parent == Path(owned["path"]),
                 "COMPACT_CAPTURE_WORKER_SOURCE_BINDING")
        need(_core_snapshot(runtime) == snapshot, "COMPACT_CAPTURE_SOURCE_CHANGED_DURING_WORKER")
        need(time.monotonic() - started < limits["max_seconds"], "COMPACT_CAPTURE_TREE_TIME_LIMIT")
        result["external_supervision"] = dict(exit_code=code, reaped=True,
            elapsed_seconds=time.monotonic() - started, peak_tree_rss_bytes=peak,
            peak_owned_job_bytes=peak_bytes, control_directory=str(control), owned_job=owned,
            limits=limits, scope="PROCESS_GROUP_TIME_RSS_OWNED_OUTPUT_AND_SOURCE_BINDING")
        workspace._write(control / "completion.json", result)
        return result
    except BaseException as error:
        cleanup_error = None
        if process is not None:
            try:
                _stop_owned(process)
            except BaseException as cleanup:
                cleanup_error = type(cleanup).__name__
        workspace._write(control / "failure.json", dict(schema_version=VERSION, status="FAILED_OR_UNKNOWN",
            error_type=type(error).__name__, reason=str(error)[:2048], cleanup_error_type=cleanup_error,
            exit_code=process.returncode if process is not None else None,
            elapsed_seconds=time.monotonic() - started, peak_tree_rss_bytes=peak,
            peak_owned_job_bytes=peak_bytes, owned_job=owned))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--runtime")
    parser.add_argument("--registry-root")
    parser.add_argument("--allow-test-fixture", action="store_true")
    parser.add_argument("--worker-result", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", allow_abbrev=False)
    capture.add_argument("--request", required=True)
    capture.add_argument("--execute-network", action="store_true")
    verify = commands.add_parser("verify", allow_abbrev=False)
    verify.add_argument("--archive", required=True)
    core = commands.add_parser("core", allow_abbrev=False)
    core.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    try:
        runtime = workspace.load_runtime(args.runtime, args.registry_root)
        if args.worker_result is None:
            result = supervised_command(runtime, args.command,
                args.archive if args.command == "verify" else args.request,
                execute_network=getattr(args, "execute_network", False), allow_test_fixture=args.allow_test_fixture)
        else:
            output = _path(args.worker_result)
            need(output.name == "result.json", "COMPACT_CAPTURE_WORKER_RESULT_NAME")
            control = _supervisor_path(runtime, output.parent)
            if args.command == "capture":
                result = execute_capture(runtime, args.request, execute_network=args.execute_network,
                    allow_test_fixture=args.allow_test_fixture, _supervisor_control=control)
            elif args.command == "verify":
                result = verify_capture_archive(runtime, args.archive, allow_test_fixture=args.allow_test_fixture)
            else:
                result = execute_core(runtime, args.request, allow_test_fixture=args.allow_test_fixture,
                    _supervisor_control=control)
            workspace._write(output, result)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        failure = dict(schema_version=VERSION, status="FAILED", error_type=type(error).__name__, reason=str(error)[:2048])
        if args.worker_result is not None:
            try:
                control = _supervisor_path(runtime, _path(args.worker_result).parent)
                workspace._write(control / "worker-error.json", failure)
            except (ValueError, OSError):
                pass
        print(json.dumps(failure))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
