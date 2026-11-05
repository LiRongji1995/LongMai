"""Locally sealed views of fully authenticated, already saved research results.

Publishing performs the original full archive authentication once. Reading a
view authenticates its private local HMAC and all five original formal exports;
it does not reopen RAW, input, canonical run, or producer files. This is not a
source capability, current-market assertion, or a replacement for a full audit.
The HMAC protects against edited receipts and recomputed public hashes, assuming
the OS account and its private key remain trusted. It is not a remote signature.
"""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stockfish_core.file_integrity import (
    content_equal, content_reference_equal, content_fingerprint, verify_content_reference,
)
from scripts import codex_workspace as workspace

VERSION = "CODEX_RESULT_VIEW_V1"
PROFILES = {"FULL_CORE_ARCHIVE_READONLY_V1": "CODEX_FULL_CORE_ARCHIVE_V1",
            "FULL_CORE_ARCHIVE_READONLY_V2": "CODEX_FULL_CORE_ARCHIVE_V2"}
EXPORT_ROLES = frozenset(("observation.json", "observation.csv", "all.csv", "focus.csv", "unselected.csv"))
CORE_ROLES = EXPORT_ROLES | {"input.json.gz", "canonical-run.json.gz", "manifest.json"}
FP_KEYS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_flags")
MAX_CONTROL = 1024 * 1024
MAX_EXPORT = 32 * 1024 * 1024
MAX_EXPORT_TOTAL = 64 * 1024 * 1024
MAX_VIEWS = 256
MAX_REFS = 4096
CHUNK = 1024 * 1024
DOMAIN = b"STOCKFISH_LOCALLY_SEALED_RESULT_VIEW_V1\0"
ROLE_DESCRIPTION = "ARCHIVED_RESULT_VIEW_NOT_SOURCE_AUTHORITY"


class ResultViewError(ValueError):
    pass


def _need(value, reason):
    if not value:
        raise ResultViewError(reason)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _json(raw):
    def pairs(items):
        value = dict(items)
        _need(len(value) == len(items), "VIEW_DUPLICATE_JSON_KEY")
        return value
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ResultViewError("VIEW_NONFINITE_JSON")))
    except (UnicodeError, json.JSONDecodeError):
        raise ResultViewError("VIEW_INVALID_JSON") from None


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _hex(value):
    _need(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "VIEW_SHA256_REQUIRED")
    return value


def _fp(value):
    return {key: getattr(value, key, 0) for key in FP_KEYS}


def _path(value):
    _need(isinstance(value, (str, Path)), "VIEW_PATH_REQUIRED")
    path = Path(value)
    _need(path.is_absolute() and ".." not in path.parts, "VIEW_ABSOLUTE_UNALIASED_PATH_REQUIRED")
    return path


def _regular(value, *, private=False):
    _need(stat.S_ISREG(value.st_mode) and value.st_nlink == 1, "VIEW_REGULAR_SINGLE_LINK_FILE_REQUIRED")
    _need(not getattr(value, "st_flags", 0) & 0x40000000, "VIEW_CLOUD_PLACEHOLDER_FORBIDDEN")
    if private:
        _need(stat.S_IMODE(value.st_mode) == 0o600 and value.st_uid == os.getuid(),
              "VIEW_PRIVATE_FILE_PERMISSIONS_REQUIRED")


def _reference(value, *, maximum=None):
    _need(type(value) is dict and set(value) == {"path", "sha256", "bytes", "fp"}, "VIEW_REFERENCE_SCHEMA")
    _path(value["path"])
    _hex(value["sha256"])
    _need(type(value["bytes"]) is int and value["bytes"] >= 0 and type(value["fp"]) is dict
          and set(value["fp"]) == set(FP_KEYS) and all(type(v) is int for v in value["fp"].values())
          and value["bytes"] == value["fp"]["st_size"], "VIEW_REFERENCE_SCHEMA")
    if maximum is not None:
        _need(value["bytes"] <= maximum, "VIEW_EXPORT_SIZE_LIMIT")
    return value


def _metadata(reference):
    """Recheck complete bytes, including old seals containing physical metadata."""
    reference = _reference(reference)
    _read_file(reference["path"], maximum=max(reference["bytes"], 1),
               expected=reference, retain=False)


def _read_file(path, *, maximum, expected=None, retain=True, private=False):
    """No-follow bounded read; persisted references bind bytes, not inodes."""
    path = _path(path)
    if expected is not None:
        _reference(expected, maximum=maximum)
        _need(str(path) == expected["path"], "VIEW_REFERENCE_PATH_MISMATCH")
    with workspace._directory_fd(path.parent) as parent:
        before_path = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _regular(before_path, private=private)
        if expected is not None:
            _need(before_path.st_size == expected["bytes"], "VIEW_ORIGINAL_HASH_CHANGED")
        _need(before_path.st_size <= maximum, "VIEW_FILE_SIZE_LIMIT")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            _regular(before, private=private)
            _need(_fp(before) == _fp(before_path), "VIEW_FILE_CHANGED_DURING_READ")
            digest, size = hashlib.sha256(), 0
            output = bytearray() if retain else None
            while True:
                raw = os.read(fd, min(CHUNK, maximum - size + 1))
                if not raw:
                    break
                size += len(raw)
                _need(size <= maximum, "VIEW_FILE_SIZE_LIMIT")
                digest.update(raw)
                if retain:
                    output.extend(raw)
            after = os.fstat(fd)
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            _regular(after, private=private)
            _regular(current, private=private)
            _need(_fp(before_path) == _fp(before) == _fp(after) == _fp(current)
                  and size == before.st_size, "VIEW_FILE_CHANGED_DURING_READ")
            reference = dict(path=str(path), sha256=digest.hexdigest(), bytes=size, fp=_fp(after))
            if expected is not None:
                _need(content_reference_equal(reference, expected), "VIEW_ORIGINAL_HASH_CHANGED")
            return output, reference
        finally:
            os.close(fd)


def _private_directory(path, *, create=False):
    path = _path(path)
    with workspace._directory_fd(path, create=create) as fd:
        value = os.fstat(fd)
        _need(stat.S_IMODE(value.st_mode) == 0o700 and value.st_uid == os.getuid()
              and not getattr(value, "st_flags", 0) & 0x40000000,
              "VIEW_PRIVATE_DIRECTORY_PERMISSIONS_REQUIRED")
    return path


def _write_new(path, raw):
    """Atomic no-replace publication. Only this invocation's temporary is removed."""
    _need(type(raw) is bytes and len(raw) <= MAX_CONTROL, "VIEW_CONTROL_SIZE_LIMIT")
    path = _path(path)
    with workspace._directory_fd(path.parent) as parent:
        temp = "." + path.name + "." + uuid.uuid4().hex + ".tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            except FileExistsError:
                raise ResultViewError("VIEW_EXISTING_FILE_PRESERVED") from None
            os.unlink(temp, dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temp, dir_fd=parent)
            except FileNotFoundError:
                pass


def _runtime(runtime):
    _need(runtime is not None and runtime.profile.get("backend") == "premarket", "VIEW_REGISTERED_RUNTIME_REQUIRED")
    runtime.check_binding()
    return _sha(dict(scope=runtime.scope, report_root=runtime.profile["report_root"],
                     state_root=runtime.profile["state_root"], source_root=runtime.profile["source_root"],
                     python=runtime.profile["python"]))


def _locations(runtime):
    report = _path(runtime.profile["report_root"])
    return report / "result-views-v1", report / ".result-view-keys-v1"


def _entries(directory):
    with workspace._directory_fd(directory) as fd:
        names = os.listdir(fd)
    _need(len(names) <= MAX_VIEWS + 16, "VIEW_DIRECTORY_ENTRY_LIMIT")
    result = []
    for name in names:
        if name.endswith(".json"):
            _need(re.fullmatch(r"[0-9a-f]{64}\.json", name), "VIEW_UNKNOWN_SNAPSHOT_FILENAME")
            result.append(name)
    _need(len(result) <= MAX_VIEWS, "VIEW_SNAPSHOT_COUNT_LIMIT")
    return sorted(result)


def _key(runtime, *, create=False):
    directory, keys = _locations(runtime)
    if create:
        _private_directory(keys, create=True)
    else:
        _private_directory(keys)
    path = keys / "hmac.key"
    try:
        raw, reference = _read_file(path, maximum=32, private=True)
    except FileNotFoundError:
        _need(create and not _entries(directory), "VIEW_KEY_MISSING_EXISTING_VIEWS_PRESERVED")
        _write_new(path, secrets.token_bytes(32))
        raw, reference = _read_file(path, maximum=32, private=True)
    _need(len(raw) == 32, "VIEW_KEY_LENGTH")
    return bytes(raw), reference


def _identity(runtime_binding, archive_id, profile, fixture):
    return dict(schema_version=VERSION, runtime_binding=runtime_binding,
                archive_id=_hex(archive_id), archive_profile=profile, allow_test_fixture=fixture)


def _context(summary, fixture, *, observation_date=None, target_session=None, cutoff=None):
    _need(type(summary) is dict, "VIEW_SUMMARY_REQUIRED")
    for key in ("run_id", "input_hash", "canonical_content_hash", "counts", "observation_date",
                "target_session", "decision_cutoff", "normalization_gap_count", "strategy_id", "coverage_type"):
        _need(key in summary, "VIEW_COMPLETE_SUMMARY_REQUIRED_" + key)
    try:
        observed = date.fromisoformat(summary["observation_date"])
        target = date.fromisoformat(summary["target_session"])
        clock = datetime.fromisoformat(summary["decision_cutoff"].replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        raise ResultViewError("VIEW_ORIGINAL_CONTEXT_INVALID") from None
    _need(target > observed and clock.tzinfo is not None and clock.utcoffset() is not None,
          "VIEW_ORIGINAL_CONTEXT_INVALID")
    _need((summary["coverage_type"] == "TEST_FIXTURE") is fixture and
          summary["coverage_type"] in {"TEST_FIXTURE", "RECONSTRUCTED_HISTORICAL", "PROSPECTIVE_CAPTURED"},
          "VIEW_FIXTURE_MUST_BE_EXPLICIT")
    for key, value in (("observation_date", observation_date), ("target_session", target_session),
                       ("decision_cutoff", cutoff)):
        if value is not None:
            _need(summary[key] == value, "VIEW_EXACT_QUERY_CONTEXT_MISMATCH_" + key)


def _exports(artifacts, runtime):
    _need(type(artifacts) is dict and set(artifacts) == EXPORT_ROLES, "VIEW_EXACT_FIVE_EXPORT_ROLES_REQUIRED")
    report = _path(runtime.profile["report_root"])
    total = 0
    parents = set()
    for role, reference in artifacts.items():
        _reference(reference, maximum=MAX_EXPORT)
        path = Path(reference["path"])
        _need(path.name == role and report in path.parents, "VIEW_EXPORT_OUTSIDE_ORIGINAL_REPORT_ROOT")
        parents.add(path.parent)
        total += reference["bytes"]
    _need(len(parents) == 1 and total <= MAX_EXPORT_TOTAL, "VIEW_EXPORT_SET_SIZE_OR_DIRECTORY")


def _load(runtime, view_id, fixture):
    _hex(view_id)
    binding = _runtime(runtime)
    directory, _ = _locations(runtime)
    _private_directory(directory)
    raw, ref = _read_file(directory / (view_id + ".json"), maximum=MAX_CONTROL, private=True)
    envelope = _json(raw)
    _need(type(envelope) is dict and set(envelope) == {"schema_version", "payload", "key_id", "hmac_sha256"}
          and envelope["schema_version"] == VERSION, "VIEW_ENVELOPE_SCHEMA")
    key, key_ref = _key(runtime)
    _need(envelope["key_id"] == hashlib.sha256(key).hexdigest(), "VIEW_SIGNING_KEY_CHANGED")
    expected = hmac.new(key, DOMAIN + _canonical(envelope["payload"]), hashlib.sha256).hexdigest()
    _need(type(envelope["hmac_sha256"]) is str and hmac.compare_digest(expected, envelope["hmac_sha256"]),
          "VIEW_LOCAL_SIGNATURE_INVALID")
    payload = envelope["payload"]
    _need(type(payload) is dict and set(payload) == {"identity", "summary", "artifacts", "source_proof",
          "last_full_authenticated_at", "result_role", "source_authority_restored"}, "VIEW_PAYLOAD_SCHEMA")
    identity = payload["identity"]
    _need(type(identity) is dict and identity.get("archive_profile") in PROFILES and
          identity == _identity(binding, identity.get("archive_id"), identity["archive_profile"], fixture)
          and _sha(identity) == view_id, "VIEW_RUNTIME_OR_IDENTITY_MISMATCH")
    _need(payload["result_role"] == ROLE_DESCRIPTION and payload["source_authority_restored"] is False,
          "VIEW_SOURCE_AUTHORITY_FORBIDDEN")
    proof = payload["source_proof"]
    _need(type(proof) is dict and set(proof) == {"archive_record", "archive_evidence", "authenticated_summary_sha256",
          "authenticated_export_set_sha256", "archive_schema_version", "original_reference_count"}, "VIEW_SOURCE_PROOF_SCHEMA")
    _reference(proof["archive_record"], maximum=MAX_CONTROL)
    _reference(proof["archive_evidence"], maximum=MAX_CONTROL)
    _need(proof["archive_schema_version"] == PROFILES[identity["archive_profile"]]
          and proof["authenticated_summary_sha256"] == _sha(payload["summary"])
          and proof["authenticated_export_set_sha256"] == _sha(payload["artifacts"])
          and type(proof["original_reference_count"]) is int and 0 < proof["original_reference_count"] <= MAX_REFS,
          "VIEW_SIGNED_SOURCE_PROOF_BINDING")
    _context(payload["summary"], fixture)
    _exports(payload["artifacts"], runtime)
    try:
        authenticated = datetime.fromisoformat(payload["last_full_authenticated_at"].replace("Z", "+00:00"))
        _need(authenticated.tzinfo is not None and authenticated.utcoffset() is not None
              and authenticated <= datetime.now(timezone.utc), "VIEW_AUTHENTICATION_CLOCK_INVALID")
    except (TypeError, ValueError, AttributeError):
        raise ResultViewError("VIEW_AUTHENTICATION_CLOCK_INVALID") from None
    return payload, ref, key_ref


def read_view(runtime, view_id, *, role="summary", allow_test_fixture=False,
              observation_date=None, target_session=None, cutoff=None):
    """Verify a sealed view and its five exports, without source reauthentication."""
    _need(type(allow_test_fixture) is bool, "VIEW_FIXTURE_FLAG_MUST_BE_BOOLEAN")
    _need(type(role) is str and (role == "summary" or role in EXPORT_ROLES), "VIEW_UNKNOWN_EXPORT_ROLE")
    payload, seal_ref, key_ref = _load(runtime, view_id, allow_test_fixture)
    _context(payload["summary"], allow_test_fixture, observation_date=observation_date,
             target_session=target_session, cutoff=cutoff)
    selected = None
    for name, reference in sorted(payload["artifacts"].items()):
        raw, _ = _read_file(reference["path"], maximum=MAX_EXPORT, expected=reference, retain=(name == role))
        if name == role:
            selected = raw
    for reference in payload["artifacts"].values():
        _metadata(reference)
    _metadata(seal_ref)
    _metadata(key_ref)
    runtime.check_binding()
    result = dict(schema_version=VERSION, status="VERIFIED_SAVED_RESULT_VIEW", view_id=view_id,
        archive_id=payload["identity"]["archive_id"], archive_profile=payload["identity"]["archive_profile"],
        result_role=ROLE_DESCRIPTION, summary=deepcopy(payload["summary"]),
        artifacts=deepcopy(payload["artifacts"]), role=role,
        last_full_authenticated_at=payload["last_full_authenticated_at"],
        source_reauthenticated_now=False, source_authority_restored=False,
        currentness_recomputed=False, full_premarket_ready=False,
        freshness="ORIGINAL_SAVED_OBSERVATION_AND_CUTOFF_NOT_REFRESHED",
        local_seal_authenticated_now=True, all_five_exports_verified_now=True,
        normalizer_calls=0, selector_calls=0, provider_calls=0, new_large_artifact_bytes=0,
        local_authentication="PRIVATE_OS_ACCOUNT_HMAC_SHA256_NOT_REMOTE_SOURCE_SIGNATURE")
    if selected is not None:
        try:
            result["content"] = selected.decode("utf-8")
        except UnicodeError:
            raise ResultViewError("VIEW_EXPORT_UTF8_REQUIRED") from None
        result["media_type"] = "application/json" if role.endswith(".json") else "text/csv"
    return result


class _AlreadyLockedRuntime:
    """Original reuse runs inside our existing exact runtime resource locks."""
    def __init__(self, runtime):
        self._runtime = runtime

    def __getattr__(self, name):
        return getattr(self._runtime, name)

    def exclusive(self):
        self._runtime.check_binding()
        return nullcontext()


def _archive_basis(runtime, archive_id, profile, fixture):
    record_path = runtime.directory / "operations" / (archive_id + ".json")
    raw, record_ref = _read_file(record_path, maximum=MAX_CONTROL)
    record = _json(raw)
    identity = record.get("identity", {})
    _need(record.get("schema_version") == workspace.VERSION and record.get("phase") == "RECORDED"
          and record.get("operation_id") == archive_id and record.get("scope") == runtime.scope
          and identity.get("schema_version") == PROFILES[profile] and identity.get("profile") == profile
          and identity.get("allow_test_fixture") is fixture,
          "VIEW_RECORDED_MATCHING_ARCHIVE_REQUIRED")
    evidence_ref = record.get("receipt", {}).get("evidence")
    _reference(evidence_ref, maximum=MAX_CONTROL)
    path = Path(evidence_ref["path"])
    _need(path.parent == Path(runtime.profile["report_root"]) / "core-archives" and path.name == archive_id + ".json",
          "VIEW_ARCHIVE_EVIDENCE_LOCATION")
    raw, _ = _read_file(path, maximum=MAX_CONTROL, expected=evidence_ref)
    evidence = _json(raw)
    _need(evidence.get("schema_version") == PROFILES[profile] and evidence.get("identity") == identity,
          "VIEW_ARCHIVE_EVIDENCE_BINDING")
    references = evidence.get("references")
    _need(type(references) is list and 0 < len(references) <= MAX_REFS,
          "VIEW_COMPLETE_SOURCE_REFERENCE_SET_REQUIRED")
    seen = set()
    for reference in references:
        _reference(reference)
        _need(reference["path"] not in seen, "VIEW_DUPLICATE_SOURCE_REFERENCE")
        seen.add(reference["path"])
        _metadata(reference)
    return evidence, record_ref, evidence_ref, references


def publish_view(runtime, archive_id, *, profile, allow_test_fixture=False,
                 observation_date=None, target_session=None, cutoff=None):
    """First publish fully authenticates; repeat publish returns the original seal."""
    _need(type(allow_test_fixture) is bool, "VIEW_FIXTURE_FLAG_MUST_BE_BOOLEAN")
    _need(profile in PROFILES, "VIEW_EXPLICIT_ARCHIVE_PROFILE_REQUIRED")
    binding = _runtime(runtime)
    _need(workspace.interpreter_path(sys.executable) == Path(runtime.profile["python"])
          and Path(runtime.profile["source_root"]) == ROOT, "VIEW_REGISTERED_EXECUTION_REQUIRED")
    identity = _identity(binding, archive_id, profile, allow_test_fixture)
    view_id = _sha(identity)
    directory, _ = _locations(runtime)
    destination = directory / (view_id + ".json")
    with runtime.exclusive():
        # An immutable completed view has no growth reservation to make. A
        # later capacity shortage must not turn a repeat publish into a write
        # or prevent reading the original authenticated result.
        try:
            _private_directory(directory)
        except FileNotFoundError:
            existing = []
        else:
            existing = _entries(directory)
        if destination.name in existing:
            result = read_view(runtime, view_id, allow_test_fixture=allow_test_fixture,
                observation_date=observation_date, target_session=target_session, cutoff=cutoff)
            result.update(published_now=False, full_authentication_performed=False, reused=True)
            return result
        # A new metadata/key/lock operation receives a conservative bounded reservation.
        admission = runtime.preflight(expected_growth_bytes=MAX_CONTROL + 65536, destination=directory)
        _need(admission["allowed"], "VIEW_METADATA_CAPACITY_BLOCKED")
        _private_directory(directory, create=True)
        with workspace._lock(directory / ".publish.lock"):
            existing = _entries(directory)
            if destination.name in existing:
                result = read_view(runtime, view_id, allow_test_fixture=allow_test_fixture,
                    observation_date=observation_date, target_session=target_session, cutoff=cutoff)
                result.update(published_now=False, full_authentication_performed=False, reused=True)
                return result
            _need(len(existing) < MAX_VIEWS, "VIEW_SNAPSHOT_COUNT_LIMIT")
            evidence, record_ref, evidence_ref, references = _archive_basis(runtime, archive_id, profile, allow_test_fixture)
            if profile == "FULL_CORE_ARCHIVE_READONLY_V1":
                from scripts import codex_core_archive as archive
            else:
                from scripts import codex_core_archive_v2 as archive
            # No caller-supplied validator or source summary is accepted.
            verified = archive.reuse_archive(_AlreadyLockedRuntime(runtime), archive_id, profile=profile,
                allow_test_fixture=allow_test_fixture, observation_date=observation_date,
                target_session=target_session, cutoff=cutoff)
            _need(verified.get("status") == "REUSED" and verified.get("archive_id") == archive_id
                  and verified.get("source_authority_restored") is False and
                  verified.get("validation", {}).get("verified") is True
                  and verified.get("summary") == evidence.get("summary")
                  and verified.get("artifacts") == evidence.get("artifacts"), "VIEW_FULL_ARCHIVE_AUTHENTICATION_REQUIRED")
            _need(set(verified["artifacts"]) == CORE_ROLES, "VIEW_COMPLETE_ORIGINAL_CORE_REQUIRED")
            _context(verified["summary"], allow_test_fixture, observation_date=observation_date,
                     target_session=target_session, cutoff=cutoff)
            artifacts = {name: deepcopy(verified["artifacts"][name]) for name in sorted(EXPORT_ROLES)}
            _exports(artifacts, runtime)
            for reference in artifacts.values():
                _read_file(reference["path"], maximum=MAX_EXPORT, expected=reference, retain=False)
            for reference in [record_ref, evidence_ref, *references]:
                _metadata(reference)
            runtime.check_binding()
            authenticated_at = datetime.now(timezone.utc).isoformat()
            payload = dict(identity=identity, summary=deepcopy(verified["summary"]), artifacts=artifacts,
                last_full_authenticated_at=authenticated_at, result_role=ROLE_DESCRIPTION, source_authority_restored=False,
                source_proof=dict(archive_record=record_ref, archive_evidence=evidence_ref,
                    authenticated_summary_sha256=_sha(verified["summary"]), authenticated_export_set_sha256=_sha(artifacts),
                    archive_schema_version=PROFILES[profile], original_reference_count=len(references)))
            key, _ = _key(runtime, create=True)
            envelope = dict(schema_version=VERSION, payload=payload, key_id=hashlib.sha256(key).hexdigest(),
                            hmac_sha256=hmac.new(key, DOMAIN + _canonical(payload), hashlib.sha256).hexdigest())
            encoded = _canonical(envelope)
            _need(len(encoded) <= MAX_CONTROL, "VIEW_CONTROL_SIZE_LIMIT")
            admission = runtime.preflight(expected_growth_bytes=len(encoded) + 8192, destination=directory)
            _need(admission["allowed"], "VIEW_METADATA_CAPACITY_BLOCKED")
            # Authentication completed before issuance. Final source metadata detects
            # ordinary mutations in the short post-authentication publication window.
            for reference in [record_ref, evidence_ref, *references]:
                _metadata(reference)
            _write_new(destination, encoded)
            result = read_view(runtime, view_id, allow_test_fixture=allow_test_fixture)
            result.update(published_now=True, full_authentication_performed=True, reused=False,
                          full_authentication_metrics=deepcopy(verified.get("metrics", {})))
            return result


def list_views(runtime, *, limit=100, allow_test_fixture=False):
    """List signed identities; stock content still requires read_view export checks."""
    _need(type(limit) is int and 0 < limit <= MAX_VIEWS and type(allow_test_fixture) is bool, "VIEW_LIST_LIMIT")
    _runtime(runtime)
    directory, _ = _locations(runtime)
    try:
        _private_directory(directory)
    except FileNotFoundError:
        return dict(schema_version=VERSION, status="NO_SEALED_VIEWS", views=[], source_reauthenticated_now=False)
    names = _entries(directory)
    _need(len(names) <= limit, "VIEW_LIST_LIMIT_EXCEEDED_NO_TRUNCATION")
    entries = []
    for name in names:
        payload, seal_ref, key_ref = _load(runtime, name[:-5], allow_test_fixture)
        _metadata(seal_ref)
        _metadata(key_ref)
        summary = payload["summary"]
        entries.append(dict(view_id=name[:-5], archive_id=payload["identity"]["archive_id"],
            archive_profile=payload["identity"]["archive_profile"],
            last_full_authenticated_at=payload["last_full_authenticated_at"],
            observation_date=summary["observation_date"], target_session=summary["target_session"],
            decision_cutoff=summary["decision_cutoff"], result_role=ROLE_DESCRIPTION))
    runtime.check_binding()
    return dict(schema_version=VERSION, status="SIGNED_LOCATORS_READ_REQUIRED", views=entries,
                source_reauthenticated_now=False, exports_revalidated_now=False)
