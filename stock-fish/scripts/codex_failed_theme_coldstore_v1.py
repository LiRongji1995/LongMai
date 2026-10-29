"""Explicit, bounded ARCHIVE_ONLY cold storage of a failed V2 price journal.

Only manifest-declared payloads may be retired. Original controls, manifest,
core, RAW and producers remain in place. A verified byte copy does not restore
an inode, historical visibility, a source capability or a successful workflow.
External consumer review is an explicit operator assertion about a named scope;
this tool does not prove global absence of consumers. No network or database IO.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import time

VERSION = "FAILED_THEME_JOURNAL_ARCHIVE_ONLY_V1"
JOURNAL_VERSION = "EXACT_EVIDENCE_ROW_AUDIT_JOINT_SPOOL_V2"
FP_KEYS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_flags")
DEFAULTS = dict(read_bytes=8*1024**3, output_bytes=2*1024**3, files=256, seconds=900)
CHUNK, CONTROL, RESERVE = 1024**2, 1024**2, 16384
HEX = re.compile(r"[0-9a-f]{64}\Z")
ROW = re.compile(r"rows-[0-9]{6}\.ndjson\Z")
FAILED = ("FAILED", "BUDGET_EXCEEDED")


def need(value, reason):
    if not value:
        raise ValueError(VERSION+":"+reason)


def raw_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: need(False, "NONFINITE_JSON"))


class Budget:
    def __init__(self, limits=None):
        self.limits = {**DEFAULTS, **(limits or {})}
        need(set(self.limits) == set(DEFAULTS) and all(type(value) is int and 0 < value <= DEFAULTS[key]
             for key, value in self.limits.items()), "FINITE_LIMITS_MAY_ONLY_BE_TIGHTENED")
        self.started, self.read, self.written, self.paths = time.monotonic(), 0, 0, set()

    def check(self, *, read=0, write=0, path=None):
        self.read += read
        self.written += write
        if path is not None:
            self.paths.add(str(path))
        need(self.read <= self.limits["read_bytes"], "READ_BUDGET")
        need(self.written <= self.limits["output_bytes"], "OUTPUT_BUDGET")
        need(len(self.paths) <= self.limits["files"], "FILE_BUDGET")
        need(time.monotonic()-self.started <= self.limits["seconds"], "TIME_BUDGET")

    def metrics(self):
        return dict(returned_read_bytes=self.read, new_output_bytes_reserved=self.written,
                    distinct_files=len(self.paths), wall_seconds=time.monotonic()-self.started)


def path(value, *, exists=True):
    value = Path(value).expanduser().absolute()
    need(value == value.resolve(strict=exists), "ALIASED_PATH")
    for parent in (value, *value.parents):
        if parent.exists() or parent.is_symlink():
            need(not parent.is_symlink(), "SYMLINK_PATH")
    return value


def fp(value):
    info = path(value).lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid(),
         "OWNED_UNSHARED_REGULAR_FILE_REQUIRED")
    need(not getattr(info, "st_flags", 0) & 0x40000000
         and not (info.st_size and getattr(info, "st_blocks", 1) == 0), "RESIDENT_FILE_REQUIRED")
    return {key: int(getattr(info, key, 0)) for key in FP_KEYS}


def dirpin(value):
    value = path(value)
    info = value.lstat()
    need(stat.S_ISDIR(info.st_mode), "REGULAR_DIRECTORY_REQUIRED")
    return {key: int(getattr(info, key)) for key in ("st_dev", "st_ino", "st_mode")}


def same_dir(value, expected):
    need(dirpin(value) == expected, "DIRECTORY_IDENTITY_CHANGED")


class Reader:
    def __init__(self, handle, budget):
        self.handle, self.budget = handle, budget
        self.digest, self.size = hashlib.sha256(), 0

    def read(self, size=-1):
        need(type(size) is int and 0 <= size <= CHUNK, "BOUNDED_STREAM_READ_REQUIRED")
        raw = self.handle.read(size)
        self.budget.check(read=len(raw))
        self.digest.update(raw)
        self.size += len(raw)
        return raw


@contextmanager
def reader(value, budget, expected=None):
    value = path(value)
    before = fp(value)
    budget.check(path=value)
    if expected is not None:
        need(before == expected["fp"], "SOURCE_7FP_CHANGED")
    fd = os.open(value, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        need({key: int(getattr(os.fstat(fd), key, 0)) for key in FP_KEYS} == before,
             "OPENED_SOURCE_IDENTITY_CHANGED")
        with os.fdopen(fd, "rb") as handle:
            fd = None
            value_reader = Reader(handle, budget)
            yield value_reader
            need(fp(value) == before and {key: int(getattr(os.fstat(handle.fileno()), key, 0))
                 for key in FP_KEYS} == before, "SOURCE_CHANGED_DURING_READ")
            if expected is not None:
                need(value_reader.size == expected["bytes"] and value_reader.digest.hexdigest() == expected["sha256"],
                     "FULL_ORIGINAL_SHA_OR_SIZE_CHANGED")
            budget.check()
    finally:
        if fd is not None:
            os.close(fd)


def reference(value, budget):
    value = path(value)
    before = fp(value)
    with reader(value, budget) as stream:
        while stream.read(CHUNK):
            pass
        result = dict(path=str(value), bytes=stream.size, sha256=stream.digest.hexdigest(), fp=before)
    need(fp(value) == before and result["bytes"] == before["st_size"], "REFERENCE_CHANGED")
    return result


def verify_ref(ref, budget):
    need(reference(ref["path"], budget) == ref, "PHYSICAL_REFERENCE_CHANGED")


def load(value, budget, expected_sha=None):
    value = path(value)
    need(fp(value)["st_size"] <= CONTROL, "CONTROL_SIZE_LIMIT")
    with reader(value, budget) as stream:
        raw = stream.read(CONTROL)
        need(stream.read(1) == b"", "CONTROL_SIZE_LIMIT")
    ref = dict(path=str(value), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), fp=fp(value))
    if expected_sha is not None:
        need(HEX.fullmatch(expected_sha) and ref["sha256"] == expected_sha, "EXPLICIT_SHA_MISMATCH")
    document = parse(raw)
    if value.name in {name+".receipt.json" for name in
            ("prepare", "pack", "verify", "verify-copy", "restore", "retire", "release")}:
        need(len(raw) == RESERVE, "OPERATION_RECEIPT_FIXED_SIZE_REQUIRED")
        if document.get("status") not in ("UNSEALED", "FAILED_PARTIAL_RETAINED"):
            reserve = document.get("failure_reserve", {})
            need(reserve.get("path") == str(value.with_name(value.name.replace(".receipt.json", ".failure-reserve.json"))),
                 "UNUSED_OWN_FAILURE_RESERVE_REQUIRED")
            verify_ref(reserve, budget)
    return document, ref, raw


def write_new(value, raw, budget):
    value = path(value, exists=False)
    budget.check(write=len(raw), path=value)
    fd = os.open(value, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        need(handle.write(raw) == len(raw), "SHORT_WRITE")
        handle.flush()
        os.fsync(handle.fileno())
    budget.check()


def fresh(value, *, separate=()):
    value = path(value, exists=False)
    need(value.parent.is_dir() and not value.exists(), "NEW_DIRECTORY_REQUIRED")
    for other in separate:
        other = Path(other)
        need(not value.is_relative_to(other) and not other.is_relative_to(value), "OUTPUT_SOURCE_OVERLAP")
    value.mkdir(mode=0o700)
    return value


def sync_dir(value):
    fd = os.open(value, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def lock(value, budget):
    need(os.name == "posix", "POSIX_LOCK_AND_DIRFD_REQUIRED")
    import fcntl
    value = path(value)
    before = fp(value)
    fd = os.open(value, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        need({key: int(getattr(os.fstat(fd), key, 0)) for key in FP_KEYS} == before, "LOCK_IDENTITY_CHANGED")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
        need(fp(value) == before, "LOCK_IDENTITY_CHANGED")
        budget.check()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class Outcome(dict):
    """Publication guards are private attributes, never serialized claims."""
    def __init__(self, budget, **values):
        super().__init__(values)
        self.budget, self.references, self.directories = budget, {}, {}

    def protect(self, *refs):
        for ref in refs:
            first = ref["path"] not in self.references
            prior = self.references.setdefault(ref["path"], ref)
            need(prior == ref, "TERMINAL_REFERENCE_CONFLICT")
            if first and Path(ref["path"]).name in {name+".receipt.json" for name in
                    ("prepare", "pack", "verify", "verify-copy", "restore", "retire", "release")}:
                document, actual, _ = load(ref["path"], self.budget)
                need(actual == ref, "GUARDED_RECEIPT_CHANGED")
                if document.get("failure_reserve") is not None:
                    self.protect(document["failure_reserve"])

    def directory(self, value, *, pin=None, names=None):
        value = str(value)
        self.directories[value] = (dirpin(value) if pin is None else pin, names)

    def check(self, full):
        for ref in self.references.values():
            if full:
                verify_ref(ref, self.budget)
            else:
                need(fp(ref["path"]) == ref["fp"], "FINAL_PROTECTED_REFERENCE_CHANGED")
        for value, (pin, names) in self.directories.items():
            same_dir(value, pin)
            if names is not None:
                need(sorted(p.name for p in Path(value).iterdir()) == names, "FINAL_DIRECTORY_SCOPE_CHANGED")
        self.budget.check()


def protect_source(result, plan, *, retired=False):
    snapshot = plan["source"]
    result.protect(snapshot["manifest"], *snapshot["controls"].values())
    if not retired:
        result.protect(*snapshot["payloads"].values())
    for value, pin in snapshot["directories"].items():
        names = snapshot["directory_entries"].get(value)
        if value == plan["journal"] and retired:
            names = ["manifest.json"]
        result.directory(value, pin=pin, names=names)


def protect_archive(result, directory, plan_ref, packed, packed_ref):
    result.protect(plan_ref, packed["archive"], packed_ref)
    result.directory(directory, names=sorted(p.name for p in directory.iterdir()))


def protect_copy(result, mapping_ref, budget, *, released=False):
    mapping, _, _ = load(mapping_ref["path"], budget)
    result.protect(mapping_ref)
    for name, row in mapping["files"].items():
        if not released or name in ("plan.json", "journal/manifest.json"):
            result.protect(row["restored"])
    for value, pin in mapping["directories"].items():
        names = sorted(p.name for p in Path(value).iterdir())
        if Path(value).name == "journal":
            names = ["manifest.json"] if released else sorted(name[8:] for name in mapping["files"] if name.startswith("journal/"))
        result.directory(value, pin=pin, names=names)


@contextmanager
def operation(directory, name, budget, *, implementation):
    """A durable once-only claim and preallocated final/failure receipt."""
    claim, receipt = directory / (name+".claim.json"), directory / (name+".receipt.json")
    write_new(claim, raw_json(dict(schema_version=VERSION, operation=name, pid=os.getpid(),
              started_at=datetime.now(timezone.utc).isoformat(), retry_allowed=False)), budget)
    write_new(receipt, raw_json(dict(schema_version=VERSION, status="UNSEALED")).ljust(RESERVE, b" "), budget)
    identity = fp(receipt)
    failure_slot = directory / (name+".failure-reserve.json")
    write_new(failure_slot, raw_json(dict(schema_version=VERSION, status="RESERVED_FAILURE_ONLY", operation=name)).ljust(RESERVE, b" "), budget)
    failure_ref = reference(failure_slot, budget)
    result = Outcome(budget, schema_version=VERSION, archive_only=True, source_capability_restored=False,
                  original_inode_restored=False, historical_PIT_restored=False,
                  global_consumer_absence_verified=False, failure_reserve=failure_ref)
    result.protect(failure_ref)
    def seal(value, *, success=False):
        current = fp(receipt)
        need(all(current[key] == identity[key] for key in ("st_dev", "st_ino", "st_mode"))
             and current["st_size"] == RESERVE, "OWNED_RECEIPT_REPLACED_OR_RESIZED")
        raw = raw_json(value)
        need(len(raw) <= RESERVE, "RECEIPT_RESERVE_EXCEEDED")
        raw = raw.ljust(RESERVE, b" ")
        fd = os.open(receipt, os.O_RDWR | os.O_NOFOLLOW)
        with os.fdopen(fd, "r+b") as handle:
            need({key: int(getattr(os.fstat(handle.fileno()), key, 0)) for key in FP_KEYS} == current,
                 "OPENED_RECEIPT_IDENTITY_CHANGED")
            need(handle.write(raw) == len(raw), "SHORT_RECEIPT_WRITE")
            handle.flush()
            os.fsync(handle.fileno())
            after = {key: int(getattr(os.fstat(handle.fileno()), key, 0)) for key in FP_KEYS}
        need(after == fp(receipt) and after["st_size"] == RESERVE, "SEALED_RECEIPT_CHANGED")
        sync_dir(directory)
        sealed = dict(path=str(receipt), fp=after, bytes=RESERVE, sha256=hashlib.sha256(raw).hexdigest())
        if success:
            verify_ref(sealed, budget)
        return sealed
    try:
        yield result
        budget.check()
        need(result.get("status") not in (None, "UNSEALED"), "EXPLICIT_OPERATION_OUTCOME_REQUIRED")
        result.protect(implementation)
        result.check(full=True)
        result["metrics"] = budget.metrics()
        seal(result, success=True)
        result.check(full=True)
        result["metrics"] = budget.metrics()
        final_ref = seal(result, success=True)
        result.check(full=False)
        need(fp(final_ref["path"]) == final_ref["fp"], "FINAL_RECEIPT_CHANGED")
        budget.check()
    except BaseException as error:
        result.update(status="FAILED_PARTIAL_RETAINED", error=dict(type=type(error).__name__,
            reason=str(error).encode()[:1800].decode(errors="ignore")), metrics=budget.metrics())
        raw = raw_json(result)
        need(len(raw) <= RESERVE and fp(failure_slot) == failure_ref["fp"], "FAILURE_SLOT_CHANGED")
        fd = os.open(failure_slot, os.O_RDWR | os.O_NOFOLLOW)
        with os.fdopen(fd, "r+b") as handle:
            need({key: int(getattr(os.fstat(handle.fileno()), key, 0)) for key in FP_KEYS} == failure_ref["fp"],
                 "OPENED_FAILURE_SLOT_CHANGED")
            handle.write(raw.ljust(RESERVE, b" ")); handle.flush(); os.fsync(handle.fileno())
        # Preserve appended bytes of our own receipt. A replacement inode is
        # never overwritten: its positive claim is invalidated by the pinned
        # failure slot changing, which every operation-receipt reader verifies.
        if receipt.exists() and not receipt.is_symlink():
            current = receipt.lstat()
            if (current.st_dev, current.st_ino, current.st_mode) == tuple(identity[key] for key in ("st_dev", "st_ino", "st_mode")):
                rejected = directory / (name+".rejected.json")
                if not rejected.exists() and not rejected.is_symlink():
                    receipt.rename(rejected)
        if not receipt.exists() and not receipt.is_symlink():
            failure_slot.rename(receipt)
        sync_dir(directory)
        raise


def terminal(job, budget):
    final, final_ref, _ = load(job / "final-control.json", budget)
    need(final.get("schema_version") in ("STANDALONE_PREMARKET_WORKFLOW_V1", "CODEX_PREMARKET_SHARED_WORKFLOW_V1")
         and final.get("status") in FAILED and final.get("external_worker_reaped") is True
         and final.get("job") == str(job) and type(final.get("external_worker_exit_code")) is int
         and final["external_worker_exit_code"] not in (0, 2) and final.get("cleanup_error") is None
         and final.get("display") is None and final.get("completion") is None, "FAILED_REAPED_ORIGINAL_JOB_REQUIRED")
    refs = {"final-control.json": final_ref}
    for name in ("configuration.json", "code-registry.json", "worker-completion.json"):
        candidate = job / name
        if candidate.exists() or candidate.is_symlink():
            value, refs[name], _ = load(candidate, budget)
            if name == "configuration.json":
                need(Path(value["state_root"]) == job.parent.parent, "ORIGINAL_STATE_BINDING_REQUIRED")
            if name == "worker-completion.json":
                need(value.get("status") in FAILED and not value.get("display")
                     and not value.get("theme_artifacts") and
                     all(value.get("phases", {}).get(key) != "COMPLETE" for key in ("theme", "presentation")),
                     "SUCCESSFUL_WORKER_OR_THEME_FORBIDDEN")
    need("configuration.json" in refs and "code-registry.json" in refs, "ORIGINAL_JOB_CONTROLS_REQUIRED")
    for name in ("display", "theme/native-theme-parent.json.gz", "theme/native-theme-parent.json",
                 "theme/theme-context.json", "theme/theme-context.json.gz"):
        need(not (job / name).exists() and not (job / name).is_symlink(), "THEME_PARENT_CONTEXT_OR_DISPLAY_PRESENT")
    return dict(status=final["status"], external_worker_exit_code=final["external_worker_exit_code"]), refs


def source(state, job, budget):
    state, job = path(state), path(job)
    need(job.parent == state / "jobs" and job.name not in (".", ".."), "ONLY_ORIGINAL_STATE_JOBS_SCOPE")
    journal = path(job / "theme" / "price-journal")
    pins = {str(p): dirpin(p) for p in (state, state / "jobs", job, job / "theme", journal)}
    entries = {str(p): sorted(entry.name for entry in p.iterdir()) for p in (job, job / "theme", journal)}
    status, controls = terminal(job, budget)
    manifest, manifest_ref, _ = load(journal / "manifest.json", budget)
    need(set(manifest) == {"schema_version", "policy_id", "row_count", "segment_bytes_limit",
        "complete_allowed_row_audit", "authority", "files"}
        and manifest["schema_version"] == JOURNAL_VERSION and manifest["policy_id"] == "R03_EVIDENCE_VISIBILITY_V1"
        and manifest["complete_allowed_row_audit"] is True
        and manifest["authority"] == "CURRENT_OWNED_WRITE_LEDGER_AND_ACTUAL_WHOLE_SHA_7FP"
        and type(manifest["row_count"]) is int and manifest["row_count"] > 0
        and type(manifest["segment_bytes_limit"]) is int and 0 < manifest["segment_bytes_limit"] <= 32*CHUNK,
        "COMPLETE_V2_JOURNAL_MANIFEST_REQUIRED")
    declared = manifest["files"]
    need(isinstance(declared, dict) and 2 <= len(declared) <= 64 and "record-index.ndjson" in declared,
         "BOUNDED_EXACT_PAYLOAD_SET_REQUIRED")
    rows = sorted(name for name in declared if ROW.fullmatch(name))
    need(set(declared) == {*rows, "record-index.ndjson"}
         and rows == ["rows-%06d.ndjson" % index for index in range(len(rows))], "CONTIGUOUS_V2_PAYLOAD_NAMES_REQUIRED")
    need({p.name for p in journal.iterdir()} == set(declared) | {"manifest.json"}, "JOURNAL_EXTRA_OR_MISSING_FILE")
    payloads = {}
    for name, declaration in sorted(declared.items()):
        need(set(declaration) == {"bytes", "sha256", "fp"} and set(declaration["fp"]) == set(FP_KEYS)
             and all(type(value) is int for value in declaration["fp"].values())
             and type(declaration["bytes"]) is int and declaration["bytes"] > 0
             and isinstance(declaration["sha256"], str) and HEX.fullmatch(declaration["sha256"])
             and (not ROW.fullmatch(name) or declaration["bytes"] <= manifest["segment_bytes_limit"]),
             "ORIGINAL_PAYLOAD_SHA_7FP_REQUIRED")
        actual = reference(journal / name, budget)
        need({key: actual[key] for key in declaration} == declaration, "MANIFEST_ORIGINAL_PAYLOAD_CHANGED")
        payloads[name] = actual
    for p, pin in pins.items():
        same_dir(p, pin)
    need(all(sorted(entry.name for entry in Path(p).iterdir()) == names for p, names in entries.items()),
         "SOURCE_DIRECTORY_SCOPE_CHANGED")
    for ref in [manifest_ref, *controls.values()]:
        verify_ref(ref, budget)
    need(terminal(job, budget) == (status, controls), "TERMINAL_JOB_CHANGED")
    return dict(state_root=str(state), job=str(job), journal=str(journal), directories=pins, directory_entries=entries,
        terminal=status, controls=controls, manifest=manifest_ref, payloads=payloads,
        payload_bytes=sum(ref["bytes"] for ref in payloads.values()))


def check_source(plan, budget, *, retired=False):
    if not retired:
        need(source(plan["state_root"], plan["job"], budget) == plan["source"], "ORIGINAL_SOURCE_PLAN_CHANGED")
        return
    original = plan["source"]
    for p, pin in original["directories"].items():
        same_dir(p, pin)
    need({p.name for p in Path(plan["journal"]).iterdir()} == {"manifest.json"}, "RETIRED_SOURCE_SCOPE_CHANGED")
    verify_ref(original["manifest"], budget)
    need(terminal(Path(plan["job"]), budget) == (original["terminal"], original["controls"]), "ORIGINAL_FAILURE_CONTROLS_CHANGED")


def prepare(state_root, job, output, *, limits=None):
    budget = Budget(limits)
    state, job = path(state_root), path(job)
    code = reference(Path(__file__), budget)
    output = fresh(output, separate=(state, Path(__file__).resolve().parents[1]))
    with operation(output, "prepare", budget, implementation=code) as result:
        with lock(state / "premarket.lock", budget):
            snapshot = source(state, job, budget)
            plan = dict(schema_version=VERSION, archive_only=True, state_root=str(state), job=str(job),
                journal=snapshot["journal"], source=snapshot, implementation=code, limits=budget.limits,
                global_consumer_absence_verified=False, operator_scoped_dependency_review_required=True,
                original_source_capability_restored=False, original_failed_status_must_remain=True)
            write_new(output / "plan.json", raw_json(plan), budget)
            plan_ref = reference(output / "plan.json", budget)
            check_source(plan, budget)
            sync_dir(output)
            result.update(status="PREPARED_NO_SOURCE_CHANGES", plan=plan_ref,
                payload_bytes=snapshot["payload_bytes"], payload_count=len(snapshot["payloads"]))
            protect_source(result, plan)
            result.protect(plan_ref)
            result.directory(output, names=sorted(p.name for p in output.iterdir()))
    return result


def read_plan(value, sha, budget):
    plan, ref, raw = load(value, budget, sha)
    need(plan.get("schema_version") == VERSION and plan.get("archive_only") is True
         and plan.get("global_consumer_absence_verified") is False, "ARCHIVE_ONLY_PLAN_REQUIRED")
    need(plan["state_root"] == plan["source"]["state_root"] and plan["job"] == plan["source"]["job"]
         and plan["journal"] == plan["source"]["journal"], "PLAN_SOURCE_BINDING")
    need(all(budget.limits[key] <= plan["limits"][key] for key in DEFAULTS), "ORIGINAL_PLAN_LIMIT_ENLARGEMENT")
    need(reference(Path(__file__), budget) == plan["implementation"], "EXACT_EXECUTING_IMPLEMENTATION_REQUIRED")
    return plan, ref, raw


def members(plan, plan_ref):
    return {"plan.json": plan_ref, "journal/manifest.json": plan["source"]["manifest"],
            **{"journal/"+name: ref for name, ref in plan["source"]["payloads"].items()}}


class Writer:
    def __init__(self, handle, budget):
        self.handle, self.budget = handle, budget
    def write(self, raw):
        self.budget.check(write=len(raw))
        count = self.handle.write(raw)
        need(count == len(raw), "SHORT_WRITE")
        return count
    def flush(self):
        self.handle.flush()


def pack(plan_path, plan_sha256, output, *, limits=None):
    budget = Budget(limits)
    plan, plan_ref, plan_raw = read_plan(plan_path, plan_sha256, budget)
    prepared, prepared_ref, _ = load(Path(plan_path).parent / "prepare.receipt.json", budget)
    need(prepared.get("schema_version") == VERSION and prepared.get("status") == "PREPARED_NO_SOURCE_CHANGES"
         and prepared.get("plan") == plan_ref, "SUCCESSFUL_PREPARATION_RECEIPT_REQUIRED")
    output = fresh(output, separate=(plan["state_root"], Path(plan_path).parent, Path(__file__).resolve().parents[1]))
    write_new(output / "operation.lock", b"", budget)
    with operation(output, "pack", budget, implementation=plan["implementation"]) as result:
        with lock(Path(plan["state_root"]) / "premarket.lock", budget), lock(output / "operation.lock", budget):
            check_source(plan, budget)
            write_new(output / "plan.json", plan_raw, budget)
            target = output / "archive.tar.gz"
            budget.check(path=target)
            with target.open("xb") as physical:
                with gzip.GzipFile(filename="", mode="wb", fileobj=Writer(physical, budget), mtime=0) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
                        for name, ref in members(plan, plan_ref).items():
                            info = tarfile.TarInfo(name)
                            info.size, info.mode, info.mtime = ref["bytes"], 0o600, 0
                            with reader(ref["path"], budget, ref) as stream:
                                archive.addfile(info, stream)
                physical.flush()
                os.fsync(physical.fileno())
            check_source(plan, budget)
            verify_ref(plan_ref, budget)
            verify_ref(prepared_ref, budget)
            result.update(status="PACKED_ORIGINALS_RETAINED", plan_sha256=plan_sha256, preparation=prepared_ref,
                          archive=reference(target, budget), copied_plan=reference(output / "plan.json", budget))
            protect_source(result, plan)
            result.protect(plan_ref, prepared_ref, result["archive"], result["copied_plan"])
            result.directory(output, names=sorted(p.name for p in output.iterdir()))
    return result


def open_archive(directory, plan_sha, budget):
    directory = path(directory)
    plan, plan_ref, _ = read_plan(directory / "plan.json", plan_sha, budget)
    packed, packed_ref, _ = load(directory / "pack.receipt.json", budget)
    need(packed.get("schema_version") == VERSION and packed.get("status") == "PACKED_ORIGINALS_RETAINED"
         and packed.get("plan_sha256") == plan_sha and packed.get("copied_plan") == plan_ref
         and packed["archive"]["path"] == str(directory / "archive.tar.gz"), "PACK_RECEIPT_BINDING_REQUIRED")
    verify_ref(packed["archive"], budget)
    return plan, plan_ref, packed, packed_ref


def safe_member(name):
    need(name in ("plan.json", "journal/manifest.json", "journal/record-index.ndjson")
         or type(name) is str and name.startswith("journal/") and ROW.fullmatch(name[8:]),
         "TAR_MEMBER_OUTSIDE_EXACT_JOURNAL_SCOPE")
    return name


def walk_archive(archive_ref, expected, budget, destination=None):
    """Independent sequential USTAR reader: no extractall, seeks, PAX or links."""
    seen, restored = set(), {}
    def decoded_read(stream, size):
        raw = stream.read(size)
        budget.check(read=len(raw))
        need(len(raw) == size, "TRUNCATED_TAR")
        return raw
    with reader(archive_ref["path"], budget, archive_ref) as physical:
        with gzip.GzipFile(filename="", mode="rb", fileobj=physical) as decoded:
            while True:
                header = decoded_read(decoded, 512)
                if not any(header):
                    need(not any(decoded_read(decoded, 512)), "SECOND_TAR_EOF_BLOCK_REQUIRED")
                    tail = 0
                    while raw := decoded.read(CHUNK):
                        budget.check(read=len(raw))
                        tail += len(raw)
                        need(tail <= 10240 and not any(raw), "EXTRA_TAR_DATA_OR_PADDING")
                    break
                need(header[257:263] == b"ustar\0" and header[263:265] == b"00", "EXACT_USTAR_HEADER_REQUIRED")
                member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
                name = safe_member(member.name)
                need(member.type == tarfile.REGTYPE and not member.linkname and not member.sparse
                     and not member.pax_headers and name in expected and name not in seen,
                     "EXTRA_DUPLICATE_LINK_OR_NONREGULAR_TAR_MEMBER")
                declaration = expected[name]
                need(member.size == declaration["bytes"] and member.size >= 0, "TAR_DECLARED_SIZE_CHANGED")
                hasher, remaining = hashlib.sha256(), member.size
                sink = None
                if destination is not None:
                    target = destination / name
                    budget.check(path=target)
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    sink = os.fdopen(fd, "wb")
                try:
                    while remaining:
                        raw = decoded_read(decoded, min(CHUNK, remaining))
                        remaining -= len(raw)
                        hasher.update(raw)
                        if sink is not None:
                            budget.check(write=len(raw))
                            need(sink.write(raw) == len(raw), "SHORT_RESTORE_WRITE")
                    need(hasher.hexdigest() == declaration["sha256"], "TAR_MEMBER_SHA_CHANGED")
                finally:
                    if sink is not None:
                        sink.flush()
                        os.fsync(sink.fileno())
                        sink.close()
                padding = (-member.size) % 512
                need(not any(decoded_read(decoded, padding)), "NONZERO_MEMBER_PADDING")
                seen.add(name)
                if destination is not None:
                    restored[name] = reference(destination / name, budget)
                    need(restored[name]["sha256"] == declaration["sha256"]
                         and restored[name]["bytes"] == declaration["bytes"], "RESTORED_BYTES_CHANGED")
            # Gzip EOF above validates CRC/trailer, including concatenated
            # streams; nonzero extra decoded content is never ignored.
            need(physical.read(1) == b"", "ARCHIVE_PHYSICAL_EOF_REQUIRED")
    need(seen == set(expected), "MISSING_ARCHIVE_MEMBERS")
    verify_ref(archive_ref, budget)
    return restored


def restore_contents(destination, plan, plan_ref, packed, budget, *, verification_copy):
    (destination / "journal").mkdir(mode=0o700)
    files = walk_archive(packed["archive"], members(plan, plan_ref), budget, destination)
    for ref in files.values():
        verify_ref(ref, budget)
    mapping = dict(schema_version=VERSION, role="VERIFICATION_COPY" if verification_copy else "MANUAL_RESTORE",
        archive_only=True, source_capability_restored=False, original_inode_restored=False,
        plan_sha256=plan_ref["sha256"], archive=packed["archive"], destination=str(destination),
        directories={str(p): dirpin(p) for p in (destination, destination / "journal")},
        files={name: dict(original=members(plan, plan_ref)[name], restored=ref) for name, ref in files.items()})
    write_new(destination / "restore-map.json", raw_json(mapping), budget)
    result = dict(status="RESTORED_BYTES_ONLY", mapping=reference(destination / "restore-map.json", budget),
        plan_sha256=plan_ref["sha256"], file_count=len(files), role=mapping["role"])
    sync_dir(destination / "journal")
    return result


def restore_body(directory, destination, plan, plan_ref, packed, budget, *, verification_copy):
    role = "verify-copy" if verification_copy else "restore"
    destination = fresh(destination, separate=(plan["state_root"], directory, Path(__file__).resolve().parents[1]))
    with operation(destination, role, budget, implementation=plan["implementation"]) as result:
        result.update(restore_contents(destination, plan, plan_ref, packed, budget, verification_copy=verification_copy))
        protect_copy(result, result["mapping"], budget)
        result.protect(packed["archive"], plan_ref)
    return result, reference(destination / (role+".receipt.json"), budget)


def verify(directory, plan_sha256, restore_output, *, limits=None):
    budget = Budget(limits)
    directory = path(directory)
    code = reference(Path(__file__), budget)
    with operation(directory, "verify", budget, implementation=code) as result:
        with lock(directory / "operation.lock", budget):
            plan, plan_ref, packed, packed_ref = open_archive(directory, plan_sha256, budget)
            restored, restore_ref = restore_body(directory, restore_output, plan, plan_ref, packed, budget,
                                                 verification_copy=True)
            verify_ref(packed_ref, budget)
            verify_ref(plan_ref, budget)
            result.update(status="FULL_ARCHIVE_AND_ENTITY_RESTORE_VERIFIED", plan_sha256=plan_sha256,
                archive=packed["archive"], mapping=restored["mapping"], restore_receipt=restore_ref,
                restored_files=restored["file_count"], all_members_SHA_size_and_gzip_CRC_EOF_verified=True)
            protect_archive(result, directory, plan_ref, packed, packed_ref)
            protect_copy(result, restored["mapping"], budget)
            result.protect(restore_ref)
    return result


def restore(directory, plan_sha256, output, *, limits=None):
    budget = Budget(limits)
    directory = path(directory)
    plan, plan_ref, packed, packed_ref = open_archive(directory, plan_sha256, budget)
    output = fresh(output, separate=(plan["state_root"], directory, Path(__file__).resolve().parents[1]))
    with operation(output, "restore", budget, implementation=plan["implementation"]) as result:
        with lock(directory / "operation.lock", budget):
            result.update(restore_contents(output, plan, plan_ref, packed, budget, verification_copy=False))
            verify_ref(packed_ref, budget)
            verify_ref(plan_ref, budget)
            protect_archive(result, directory, plan_ref, packed, packed_ref)
            protect_copy(result, result["mapping"], budget)
    return result


def verified_copy(directory, plan, plan_ref, packed, receipt_path, receipt_sha, budget, *, released=False):
    need(path(receipt_path) == directory / "verify.receipt.json", "EXACT_OWN_VERIFICATION_RECEIPT_REQUIRED")
    receipt, receipt_ref, _ = load(receipt_path, budget, receipt_sha)
    need(receipt.get("schema_version") == VERSION and receipt.get("status") == "FULL_ARCHIVE_AND_ENTITY_RESTORE_VERIFIED"
         and receipt.get("plan_sha256") == plan_ref["sha256"] and receipt.get("archive") == packed["archive"]
         and receipt.get("all_members_SHA_size_and_gzip_CRC_EOF_verified") is True,
         "FULL_RESTORE_VERIFICATION_RECEIPT_REQUIRED")
    verify_ref(receipt["mapping"], budget)
    verify_ref(receipt["restore_receipt"], budget)
    mapping, _, _ = load(receipt["mapping"]["path"], budget)
    need(mapping.get("schema_version") == VERSION and mapping.get("role") == "VERIFICATION_COPY"
         and mapping.get("plan_sha256") == plan_ref["sha256"] and mapping.get("archive") == packed["archive"]
         and set(mapping["files"]) == set(members(plan, plan_ref)), "OWN_VERIFICATION_COPY_REQUIRED")
    destination = path(mapping["destination"])
    need(not destination.is_relative_to(plan["state_root"]) and not destination.is_relative_to(directory)
         and not directory.is_relative_to(destination), "VERIFICATION_COPY_SCOPE_OVERLAP")
    need(path(receipt["mapping"]["path"]) == destination / "restore-map.json"
         and path(receipt["restore_receipt"]["path"]) == destination / "verify-copy.receipt.json",
         "OWN_VERIFICATION_COPY_CONTROLS_REQUIRED")
    need({p.name for p in destination.iterdir()} == {"journal", "plan.json", "restore-map.json",
          "verify-copy.claim.json", "verify-copy.receipt.json", "verify-copy.failure-reserve.json"}, "VERIFICATION_COPY_EXTRA_OR_MISSING_FILE")
    for p, pin in mapping["directories"].items():
        same_dir(p, pin)
    expected_names = {"manifest.json"} if released else {"manifest.json", *plan["source"]["payloads"]}
    need({p.name for p in (destination / "journal").iterdir()} == expected_names, "VERIFICATION_JOURNAL_SCOPE_CHANGED")
    for name, value in mapping["files"].items():
        safe_member(name)
        need(value["original"] == members(plan, plan_ref)[name]
             and value["restored"]["path"] == str(destination / name), "RESTORE_MAPPING_CHANGED")
        if not released or name in ("plan.json", "journal/manifest.json"):
            verify_ref(value["restored"], budget)
    return mapping, receipt_ref


def external_review(value, sha, plan, plan_ref, budget):
    review, ref, _ = load(value, budget, sha)
    need(review.get("schema_version") == "FAILED_THEME_SCOPED_DEPENDENCY_REVIEW_V1"
         and review.get("plan_sha256") == plan_ref["sha256"] and review.get("job") == plan["job"]
         and review.get("journal") == plan["journal"]
         and review.get("no_successful_consumer_in_reviewed_scope") is True
         and review.get("no_active_managed_owner") is True
         and review.get("global_absence_claimed") is False
         and isinstance(review.get("reviewed_scope"), str) and bool(review["reviewed_scope"].strip())
         and isinstance(review.get("managed_roots"), list) and bool(review["managed_roots"])
         and review.get("root_wrapper_resource_lock_held") is True,
         "EXPLICIT_SCOPED_OPERATOR_DEPENDENCY_REVIEW_REQUIRED")
    need(isinstance(review.get("wrapper_script"), dict) and isinstance(review.get("audit_receipt"), dict),
         "OPERATOR_REVIEW_EVIDENCE_REFERENCES_REQUIRED")
    for evidence in (review["wrapper_script"], review["audit_receipt"]):
        verify_ref(evidence, budget)
    return review, ref


def unlink_payloads(journal, records, directory_pin, events_path, budget, result):
    """Only the exact pinned basenames, anchored to the held directory fd."""
    same_dir(journal, directory_pin)
    budget.check(path=events_path)
    with events_path.open("xb") as events:
        fd = os.open(journal, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        result["removed_payloads"] = []
        try:
            for name, ref in records.items():
                need(name == "record-index.ndjson" or ROW.fullmatch(name), "ONLY_DECLARED_PAYLOAD_UNLINK_ALLOWED")
                same_dir(journal, directory_pin)
                need(fp(journal / name) == ref["fp"], "PAYLOAD_CHANGED_AT_UNLINK")
                budget.check()
                event = raw_json(dict(action="PRE_UNLINK", name=name, original=ref))+b"\n"
                budget.check(write=len(event))
                events.write(event); events.flush(); os.fsync(events.fileno())
                # Check once more after intent fsync, under both writer locks.
                need(fp(journal / name) == ref["fp"], "PAYLOAD_CHANGED_AFTER_UNLINK_INTENT")
                os.unlink(name, dir_fd=fd)
                result["removed_payloads"].append(name)
                event = raw_json(dict(action="UNLINK_RETURNED", name=name))+b"\n"
                budget.check(write=len(event))
                events.write(event); events.flush(); os.fsync(events.fileno())
            os.fsync(fd)
            budget.check()
        finally:
            os.close(fd)


def retire(directory, plan_sha256, verification_receipt, verification_sha256,
           dependency_review, dependency_review_sha256, *, allow_retire_payloads=False, limits=None):
    need(allow_retire_payloads is True, "EXPLICIT_RETIRE_PAYLOADS_REQUIRED")
    budget = Budget(limits)
    directory = path(directory)
    plan, plan_ref, packed, packed_ref = open_archive(directory, plan_sha256, budget)
    with operation(directory, "retire", budget, implementation=plan["implementation"]) as result:
        with lock(Path(plan["state_root"]) / "premarket.lock", budget), lock(directory / "operation.lock", budget):
            review, review_ref = external_review(dependency_review, dependency_review_sha256, plan, plan_ref, budget)
            mapping, verification_ref = verified_copy(directory, plan, plan_ref, packed,
                                                  verification_receipt, verification_sha256, budget)
            walk_archive(packed["archive"], members(plan, plan_ref), budget)
            check_source(plan, budget)
            for ref in (plan_ref, packed_ref, review_ref, verification_ref):
                verify_ref(ref, budget)
            journal = Path(plan["journal"])
            unlink_payloads(journal, plan["source"]["payloads"], plan["source"]["directories"][str(journal)],
                            directory / "retire-events.ndjson", budget, result)
            check_source(plan, budget, retired=True)
            for ref in (packed["archive"], plan_ref, packed_ref, review_ref, verification_ref,
                        review["wrapper_script"], review["audit_receipt"]):
                verify_ref(ref, budget)
            result.update(status="RETIRED_ARCHIVE_ONLY", plan_sha256=plan_sha256, archive=packed["archive"],
                verification=verification_ref, operator_dependency_review=review_ref,
                reviewed_scope=review["reviewed_scope"], payload_bytes_removed=plan["source"]["payload_bytes"],
                original_failed_status=plan["source"]["terminal"]["status"], original_manifest_retained=True,
                verification_copy_retained=True, automatic_retry_allowed=False)
            protect_source(result, plan, retired=True)
            protect_archive(result, directory, plan_ref, packed, packed_ref)
            result.protect(review_ref, verification_ref, review["wrapper_script"], review["audit_receipt"])
            verified_document, _, _ = load(verification_ref["path"], budget)
            protect_copy(result, verified_document["mapping"], budget)
            result.protect(verified_document["restore_receipt"])
    return result


def release_verification_copy(directory, plan_sha256, verification_receipt, verification_sha256,
                              *, allow_release_verification_copy=False, limits=None):
    need(allow_release_verification_copy is True, "EXPLICIT_VERIFICATION_COPY_RELEASE_REQUIRED")
    budget = Budget(limits)
    directory = path(directory)
    plan, plan_ref, packed, packed_ref = open_archive(directory, plan_sha256, budget)
    with operation(directory, "release", budget, implementation=plan["implementation"]) as result:
        with lock(Path(plan["state_root"]) / "premarket.lock", budget), lock(directory / "operation.lock", budget):
            retired, retired_ref, _ = load(directory / "retire.receipt.json", budget)
            need(retired.get("status") == "RETIRED_ARCHIVE_ONLY" and retired.get("schema_version") == VERSION
                 and retired.get("plan_sha256") == plan_sha256 and retired.get("archive") == packed["archive"],
                 "COMPLETED_OWN_RETIREMENT_REQUIRED")
            mapping, verification_ref = verified_copy(directory, plan, plan_ref, packed,
                                                       verification_receipt, verification_sha256, budget)
            need(retired.get("verification") == verification_ref, "RETIREMENT_VERIFICATION_BINDING_CHANGED")
            walk_archive(packed["archive"], members(plan, plan_ref), budget)
            check_source(plan, budget, retired=True)
            journal = Path(mapping["destination"]) / "journal"
            records = {name: mapping["files"]["journal/"+name]["restored"] for name in plan["source"]["payloads"]}
            unlink_payloads(journal, records, mapping["directories"][str(journal)],
                            directory / "release-events.ndjson", budget, result)
            verified_copy(directory, plan, plan_ref, packed, verification_receipt, verification_sha256,
                          budget, released=True)
            for ref in (packed["archive"], plan_ref, packed_ref, retired_ref, verification_ref):
                verify_ref(ref, budget)
            result.update(status="OWN_VERIFICATION_PAYLOAD_COPY_RELEASED", plan_sha256=plan_sha256,
                released_directory=mapping["destination"], payload_bytes_removed=plan["source"]["payload_bytes"],
                archive_retained=True, original_manifest_and_copy_controls_retained=True)
            protect_source(result, plan, retired=True)
            protect_archive(result, directory, plan_ref, packed, packed_ref)
            result.protect(retired_ref, verification_ref)
            verified_document, _, _ = load(verification_ref["path"], budget)
            protect_copy(result, verified_document["mapping"], budget, released=True)
            result.protect(verified_document["restore_receipt"])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "pack", "verify", "restore", "retire", "release-verification-copy"))
    for name in ("state-root", "job", "output", "plan", "plan-sha256", "archive", "restore-output",
                 "verification-receipt", "verification-sha256", "dependency-review", "dependency-review-sha256"):
        parser.add_argument("--"+name)
    parser.add_argument("--allow-retire-payloads", action="store_true")
    parser.add_argument("--allow-release-verification-copy", action="store_true")
    for key in DEFAULTS:
        parser.add_argument("--max-"+key.replace("_", "-"), type=int, default=DEFAULTS[key])
    args = parser.parse_args(argv)
    limits = {key: getattr(args, "max_"+key) for key in DEFAULTS}
    required = dict(prepare=("state_root", "job", "output"), pack=("plan", "plan_sha256", "output"),
        verify=("archive", "plan_sha256", "restore_output"), restore=("archive", "plan_sha256", "output"),
        retire=("archive", "plan_sha256", "verification_receipt", "verification_sha256", "dependency_review", "dependency_review_sha256"),
        **{"release-verification-copy": ("archive", "plan_sha256", "verification_receipt", "verification_sha256")})
    need(all(getattr(args, key) is not None for key in required[args.command]), "EXPLICIT_COMMAND_ARGUMENTS_REQUIRED")
    values = [getattr(args, key) for key in required[args.command]]
    functions = dict(prepare=prepare, pack=pack, verify=verify, restore=restore, retire=retire,
                     **{"release-verification-copy": release_verification_copy})
    options = dict(limits=limits)
    if args.command == "retire":
        options["allow_retire_payloads"] = args.allow_retire_payloads
    elif args.command == "release-verification-copy":
        options["allow_release_verification_copy"] = args.allow_release_verification_copy
    result = functions[args.command](*values, **options)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
