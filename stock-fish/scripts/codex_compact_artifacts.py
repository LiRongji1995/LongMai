"""Lossless, bounded storage for newly generated native core artifacts.

This is a new storage protocol, not a replacement native owner. Only a live
``OwnedStaging`` created here can be consumed; an old run directory cannot be
adopted or cleaned. The original eight bytestrings, including the native
manifest's historical paths, remain exact. Their logical hashes are distinct
from the new storage hashes. No source capability is restored from JSON.

Integration: create_staging -> unchanged run_core(output_dir=stage.core_dir)
-> finish native validation/presentation -> compact_owned. The caller must
reserve the declared peak (original plus compact output), and must not reuse
the native owner after successful compaction. Reading an archive does not run
a strategy, authenticate a market source, or attest a native terminal state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import time
import uuid
import weakref

from scripts import codex_workspace as workspace

VERSION = "CODEX_COMPACT_CORE_ARTIFACTS_V1"
CORE_FILES = frozenset(("canonical-run.json.gz", "input.json.gz", "observation.json",
    "observation.csv", "all.csv", "focus.csv", "unselected.csv", "manifest.json"))
FP_KEYS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_flags")
MIB = 1024 ** 2
CHUNK = 64 * 1024
MANIFEST_NAME = "compact-manifest.json"
_MINT = object()
_STAGES = weakref.WeakKeyDictionary()


class CompactError(ValueError):
    pass


def _need(ok, reason):
    if not ok:
        raise CompactError(reason)


@dataclass(frozen=True)
class CompactLimits:
    """One invocation's limits; limits are not capacity admission or OS quotas."""
    logical_bytes: int = 128 * MIB
    stored_bytes: int = 128 * MIB
    peak_bytes: int = 256 * MIB + 2 * MIB
    returned_read_bytes: int = 1024 * MIB
    manifest_bytes: int = 2 * MIB
    max_seconds: float = 180

    def __post_init__(self):
        for key in ("logical_bytes", "stored_bytes", "peak_bytes", "returned_read_bytes", "manifest_bytes"):
            _need(type(getattr(self, key)) is int and getattr(self, key) > 0, "COMPACT_POSITIVE_BYTE_LIMIT_REQUIRED")
        _need(type(self.max_seconds) in (int, float) and 0 < self.max_seconds < float("inf"),
              "COMPACT_FINITE_TIME_LIMIT_REQUIRED")
        _need(self.manifest_bytes <= 2 * MIB, "COMPACT_MANIFEST_LIMIT_CANNOT_EXCEED_2_MIB")


class _Meter:
    def __init__(self, limits):
        _need(type(limits) is CompactLimits, "COMPACT_LIMITS_REQUIRED")
        self.limits, self.started, self.read_bytes = limits, time.monotonic(), 0
        self.stored_bytes, self.original_bytes = 0, 0

    def check(self):
        _need(time.monotonic() - self.started < self.limits.max_seconds, "COMPACT_TIME_LIMIT")

    def read(self, size):
        self.check()
        _need(self.read_bytes + size <= self.limits.returned_read_bytes, "COMPACT_READ_LIMIT")
        self.read_bytes += size

    def write(self, size):
        self.check()
        _need(self.stored_bytes + size <= self.limits.stored_bytes, "COMPACT_STORED_LIMIT")
        _need(self.original_bytes + self.stored_bytes + size <= self.limits.peak_bytes, "COMPACT_PEAK_LIMIT")
        self.stored_bytes += size


def _fp(value):
    _need(stat.S_ISREG(value.st_mode) and value.st_nlink == 1, "COMPACT_REGULAR_SINGLE_LINK_FILE_REQUIRED")
    _need(not getattr(value, "st_flags", 0) & 0x40000000 and
          (not value.st_size or getattr(value, "st_blocks", 1) > 0), "COMPACT_RESIDENT_FILE_REQUIRED")
    return {key: int(getattr(value, key, 0)) for key in FP_KEYS}


def _path(value):
    path = Path(value)
    _need(path.is_absolute() and ".." not in path.parts, "COMPACT_ABSOLUTE_PATH_REQUIRED")
    return path


def _reference_shape(ref):
    _need(type(ref) is dict and set(ref) == {"path", "sha256", "bytes", "fp"}, "COMPACT_FULL_REFERENCE_REQUIRED")
    _path(ref["path"])
    _need(type(ref["bytes"]) is int and ref["bytes"] >= 0, "COMPACT_REFERENCE_BYTES_INVALID")
    _need(type(ref["sha256"]) is str and len(ref["sha256"]) == 64 and
          all(c in "0123456789abcdef" for c in ref["sha256"]), "COMPACT_REFERENCE_HASH_INVALID")
    _need(type(ref["fp"]) is dict and set(ref["fp"]) == set(FP_KEYS) and
          all(type(v) is int for v in ref["fp"].values()) and ref["fp"]["st_size"] == ref["bytes"],
          "COMPACT_REFERENCE_IDENTITY_INVALID")


def _chunks(path, meter, *, expected=None, maximum=None):
    """Yield bounded physical bytes; final exhaustion pins full hash and 7FP."""
    path = _path(path)
    if expected is not None:
        _reference_shape(expected)
        _need(str(path) == expected["path"], "COMPACT_REFERENCE_PATH_CHANGED")
    maximum = meter.limits.logical_bytes if maximum is None else maximum
    with workspace._directory_fd(path.parent) as parent:
        initial = _fp(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
        _need(initial["st_size"] <= maximum, "COMPACT_FILE_LIMIT")
        if expected is not None:
            _need(initial == expected["fp"], "COMPACT_PHYSICAL_IDENTITY_CHANGED")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            _need(_fp(os.fstat(fd)) == initial, "COMPACT_PHYSICAL_IDENTITY_CHANGED")
            digest, size = hashlib.sha256(), 0
            while size < initial["st_size"]:
                meter.check()
                amount = min(CHUNK, initial["st_size"] - size)
                _need(meter.read_bytes + amount <= meter.limits.returned_read_bytes, "COMPACT_READ_LIMIT")
                raw = os.read(fd, amount)
                _need(raw, "COMPACT_TRUNCATED_FILE")
                meter.read(len(raw))
                digest.update(raw)
                size += len(raw)
                yield raw
            _need(_fp(os.fstat(fd)) == initial == _fp(os.stat(path.name, dir_fd=parent,
                  follow_symlinks=False)), "COMPACT_FILE_CHANGED_DURING_READ")
            if expected is not None:
                _need(size == expected["bytes"] and digest.hexdigest() == expected["sha256"],
                      "COMPACT_PHYSICAL_BYTES_CHANGED")
        finally:
            os.close(fd)


def _ref(path, meter, maximum=None):
    with workspace._directory_fd(Path(path).parent) as parent:
        initial = _fp(os.stat(Path(path).name, dir_fd=parent, follow_symlinks=False))
    digest, size = hashlib.sha256(), 0
    for raw in _chunks(path, meter, maximum=maximum):
        digest.update(raw)
        size += len(raw)
    with workspace._directory_fd(Path(path).parent) as parent:
        identity = _fp(os.stat(Path(path).name, dir_fd=parent, follow_symlinks=False))
    _need(identity == initial and identity["st_size"] == size, "COMPACT_FILE_CHANGED_DURING_READ")
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": size, "fp": identity}


class OwnedStaging:
    """Invocation-local ownership; never reconstructable from a saved path."""
    __slots__ = ("__weakref__",)

    def __new__(cls, token=None):
        _need(token is _MINT, "COMPACT_LIVE_OWNED_STAGING_REQUIRED")
        return super().__new__(cls)

    @property
    def core_dir(self):
        return _stage(self)["root"] / "core"

    def __reduce__(self):
        raise TypeError("COMPACT_STAGING_NOT_SERIALIZABLE")


def _dir_identity(value):
    _need(stat.S_ISDIR(value.st_mode), "COMPACT_DIRECTORY_REQUIRED")
    return {key: int(getattr(value, key)) for key in ("st_dev", "st_ino", "st_mode")}


def _stage(stage):
    _need(type(stage) is OwnedStaging and stage in _STAGES, "COMPACT_LIVE_OWNED_STAGING_REQUIRED")
    state = _STAGES[stage]
    _need(not state["consumed"], "COMPACT_STAGING_ALREADY_CONSUMED")
    with workspace._directory_fd(state["root"]) as fd:
        _need(_dir_identity(os.fstat(fd)) == state["identity"], "COMPACT_STAGING_DIRECTORY_CHANGED")
    return state


def create_staging(parent):
    """Create only an exclusive marker/root; the native core child stays absent."""
    parent = _path(parent)
    name = ".codex-compact-stage-" + uuid.uuid4().hex
    token = uuid.uuid4().hex.encode("ascii")
    with workspace._directory_fd(parent) as fd:
        os.mkdir(name, mode=0o700, dir_fd=fd)
        root = parent / name
        with workspace._directory_fd(root) as stage_fd:
            identity = _dir_identity(os.fstat(stage_fd))
            marker_fd = os.open("owned-token", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600, dir_fd=stage_fd)
            with os.fdopen(marker_fd, "wb") as stream:
                stream.write(token)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(stage_fd)
    stage = OwnedStaging(_MINT)
    marker = _ref(root / "owned-token", _Meter(CompactLimits()), maximum=64)
    _STAGES[stage] = dict(root=root, identity=identity, marker=marker, consumed=False)
    return stage


class _Writer:
    def __init__(self, stream, meter):
        self.stream, self.meter = stream, meter

    def write(self, data):
        self.meter.write(len(data))
        return self.stream.write(data)

    def flush(self):
        self.stream.flush()


def _write_artifact(path, source, codec, meter, directory_identity):
    with workspace._directory_fd(path.parent) as parent:
        _need(_dir_identity(os.fstat(parent)) == directory_identity, "COMPACT_DESTINATION_CHANGED")
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as stream:
            writer = _Writer(stream, meter)
            if codec == "gzip":
                with gzip.GzipFile(fileobj=writer, mode="wb", filename="", mtime=0, compresslevel=6) as zipped:
                    for raw in _chunks(source["path"], meter, expected=source):
                        zipped.write(raw)
            else:
                for raw in _chunks(source["path"], meter, expected=source):
                    writer.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
    return _ref(path, meter, maximum=meter.limits.stored_bytes)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _need(key not in result, "COMPACT_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(CompactError("COMPACT_NONFINITE_JSON")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CompactError("COMPACT_INVALID_JSON") from exc


class _IteratorFile(io.RawIOBase):
    def __init__(self, chunks):
        self.chunks, self.pending, self.finished = iter(chunks), b"", False

    def readable(self):
        return True

    def readinto(self, buffer):
        if not self.pending and not self.finished:
            try:
                self.pending = next(self.chunks)
            except StopIteration:
                self.finished = True
        count = min(len(buffer), len(self.pending))
        buffer[:count] = self.pending[:count]
        self.pending = self.pending[count:]
        return count

    def close(self):
        try:
            self.chunks.close()
        finally:
            super().close()


def _logical_chunks(item, meter):
    physical = _chunks(item["stored_reference"]["path"], meter, expected=item["stored_reference"],
                       maximum=meter.limits.stored_bytes)
    digest, size = hashlib.sha256(), 0
    reader = buffered = None
    try:
        if item["codec"] == "gzip":
            buffered = io.BufferedReader(_IteratorFile(physical), buffer_size=CHUNK)
            reader = gzip.GzipFile(fileobj=buffered, mode="rb")
            def blocks():
                while True:
                    # One extra byte detects expansion beyond the exact original bound.
                    raw = reader.read(min(CHUNK, item["logical_bytes"] - size + 1))
                    if not raw:
                        break
                    yield raw
            chunks = blocks()
        else:
            chunks = physical
        for raw in chunks:
            meter.check()
            if reader is not None:
                meter.read(len(raw))
            size += len(raw)
            _need(size <= item["logical_bytes"] <= meter.limits.logical_bytes, "COMPACT_LOGICAL_SIZE_LIMIT")
            digest.update(raw)
            yield raw
        if reader is not None:
            # Exhaust physical generator even if gzip buffered its final input.
            for _ in physical:
                raise CompactError("COMPACT_TRAILING_PHYSICAL_BYTES")
        _need(size == item["logical_bytes"] and digest.hexdigest() == item["logical_sha256"],
              "COMPACT_LOGICAL_BYTES_CHANGED")
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise CompactError("COMPACT_INVALID_GZIP") from exc
    finally:
        if reader is not None:
            reader.close()
        if buffered is not None:
            buffered.close()
        physical.close()


def _manifest_shape(value, directory, limits):
    _need(type(value) is dict and set(value) == {"schema_version", "created_at", "artifacts",
          "logical_bytes", "stored_bytes", "source_paths_role", "source_capability_restored"},
          "COMPACT_MANIFEST_SCHEMA")
    _need(value["schema_version"] == VERSION and value["source_capability_restored"] is False and
          value["source_paths_role"] == "HISTORICAL_UNPUBLISHED_STAGING_LOCATORS_ONLY",
          "COMPACT_MANIFEST_SEMANTICS")
    _need(type(value["artifacts"]) is dict and set(value["artifacts"]) == CORE_FILES,
          "COMPACT_COMPLETE_EIGHT_ARTIFACTS_REQUIRED")
    logical, stored = 0, 0
    for name, item in value["artifacts"].items():
        _need(type(item) is dict and set(item) == {"logical_sha256", "logical_bytes", "codec",
              "source_reference", "stored_reference"}, "COMPACT_ARTIFACT_SCHEMA")
        source, reference = item["source_reference"], item["stored_reference"]
        _reference_shape(source)
        _reference_shape(reference)
        codec = "identity" if name.endswith(".gz") else "gzip"
        filename = name if codec == "identity" else name + ".gz"
        _need(item["codec"] == codec and reference["path"] == str(directory / filename),
              "COMPACT_STORAGE_PATH_OR_CODEC_INVALID")
        _need(item["logical_bytes"] == source["bytes"] and item["logical_sha256"] == source["sha256"],
              "COMPACT_LOGICAL_REFERENCE_MISMATCH")
        logical += item["logical_bytes"]
        stored += reference["bytes"]
    _need(type(value["logical_bytes"]) is int and type(value["stored_bytes"]) is int and
          value["logical_bytes"] == logical <= limits.logical_bytes and
          value["stored_bytes"] == stored <= limits.stored_bytes, "COMPACT_TOTAL_SIZE_INVALID")


class CompactArchive:
    """Bounded byte reader; authentication is storage-only, not source authority.

    ``iter_artifact`` must be exhausted before using a result. ``read_artifact``
    and ``verify_all`` do that themselves. A reader has one cumulative budget.
    """
    def __init__(self, manifest_reference, *, limits=CompactLimits(), _meter=None):
        _reference_shape(manifest_reference)
        _need(_meter is None or type(_meter) is _Meter and _meter.limits == limits,
              "COMPACT_SHARED_INVOCATION_METER_REQUIRED")
        self.meter = _Meter(limits) if _meter is None else _meter
        self.reference = json.loads(json.dumps(manifest_reference))
        path = _path(manifest_reference["path"])
        _need(path.name == MANIFEST_NAME, "COMPACT_MANIFEST_FILENAME_REQUIRED")
        raw = b"".join(_chunks(path, self.meter, expected=self.reference, maximum=limits.manifest_bytes))
        self._manifest = _json(raw)
        _manifest_shape(self._manifest, path.parent, limits)

    @property
    def manifest(self):
        return json.loads(json.dumps(self._manifest))

    def iter_artifact(self, role):
        _need(role in CORE_FILES, "COMPACT_UNKNOWN_ARTIFACT_ROLE")
        # Recheck the manifest on every logical read, with the same invocation budget.
        for _ in _chunks(self.reference["path"], self.meter, expected=self.reference,
                         maximum=self.meter.limits.manifest_bytes):
            pass
        yield from _logical_chunks(self._manifest["artifacts"][role], self.meter)

    def read_artifact(self, role, *, max_bytes):
        _need(type(max_bytes) is int and 0 <= max_bytes <= self.meter.limits.logical_bytes,
              "COMPACT_EXPLICIT_BOUNDED_READ_REQUIRED")
        _need(role in CORE_FILES and self._manifest["artifacts"][role]["logical_bytes"] <= max_bytes,
              "COMPACT_READ_RESULT_SIZE_LIMIT")
        return b"".join(self.iter_artifact(role))

    def verify_all(self):
        for role in sorted(CORE_FILES):
            for _ in self.iter_artifact(role):
                pass
        return {"schema_version": VERSION, "status": "VERIFIED_STORAGE_BYTES_ONLY",
                "artifact_count": len(CORE_FILES), "logical_bytes": self._manifest["logical_bytes"],
                "stored_bytes": self._manifest["stored_bytes"], "returned_read_bytes": self.meter.read_bytes,
                "source_capability_restored": False}


def compact_owned(stage, artifact_refs, *, destination, terminal_validator, limits=CompactLimits()):
    """Consume only new owned output after native and compact terminal checks.

    Before cleanup, failures preserve all original files. A cleanup I/O failure
    can leave a partial staging directory beside the fully verified compact
    package; it does not return success and must not be retried implicitly.
    There is no recursive cleanup, adoption of a saved directory, native-owner
    reconstruction, or old-run migration.
    The callback must finish the caller's original full terminal checks; its
    result is not serialized as source authority by this byte-storage module.
    """
    state, meter = _stage(stage), _Meter(limits)
    root, core = state["root"], stage.core_dir
    destination = _path(destination)
    _need(destination.parent == root.parent and destination != root, "COMPACT_NEW_SIBLING_DESTINATION_REQUIRED")
    _need(callable(terminal_validator), "COMPACT_NATIVE_TERMINAL_VALIDATOR_REQUIRED")
    _need(type(artifact_refs) is dict and set(artifact_refs) == CORE_FILES, "COMPACT_COMPLETE_EIGHT_ARTIFACTS_REQUIRED")
    refs = json.loads(json.dumps(artifact_refs))
    for role, ref in refs.items():
        _reference_shape(ref)
        _need(ref["path"] == str(core / role), "COMPACT_ONLY_OWNED_NATIVE_OUTPUT_ALLOWED")
    original_bytes = sum(ref["bytes"] for ref in refs.values())
    _need(original_bytes <= limits.logical_bytes, "COMPACT_LOGICAL_SIZE_LIMIT")
    # Admission uses the full configured write envelope, not guessed compression.
    _need(original_bytes + limits.stored_bytes + limits.manifest_bytes <= limits.peak_bytes,
          "COMPACT_ORIGINAL_PLUS_OUTPUT_PEAK_RESERVATION_REQUIRED")
    meter.original_bytes = original_bytes
    with workspace._directory_fd(core) as fd:
        core_identity = _dir_identity(os.fstat(fd))

    def originals():
        _stage(stage)
        with workspace._directory_fd(root) as fd:
            _need(set(os.listdir(fd)) == {"owned-token", "core"}, "COMPACT_UNKNOWN_STAGING_ENTRY_PRESERVED")
        with workspace._directory_fd(core) as fd:
            _need(_dir_identity(os.fstat(fd)) == core_identity, "COMPACT_CORE_DIRECTORY_CHANGED")
            _need(set(os.listdir(fd)) == CORE_FILES, "COMPACT_UNKNOWN_CORE_ENTRY_PRESERVED")
        for _ in _chunks(state["marker"]["path"], meter, expected=state["marker"], maximum=64):
            pass
        for ref in refs.values():
            for _ in _chunks(ref["path"], meter, expected=ref):
                pass

    originals()
    _need(terminal_validator() is True, "COMPACT_NATIVE_TERMINAL_VALIDATION_FAILED")
    with workspace._directory_fd(destination.parent) as parent:
        os.mkdir(destination.name, mode=0o700, dir_fd=parent)
    with workspace._directory_fd(destination) as fd:
        destination_identity = _dir_identity(os.fstat(fd))
    items = {}
    for role in sorted(CORE_FILES):
        codec = "identity" if role.endswith(".gz") else "gzip"
        target = destination / (role if codec == "identity" else role + ".gz")
        reference = _write_artifact(target, refs[role], codec, meter, destination_identity)
        items[role] = {"logical_sha256": refs[role]["sha256"], "logical_bytes": refs[role]["bytes"],
                       "codec": codec, "source_reference": refs[role], "stored_reference": reference}
    manifest = {"schema_version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                "artifacts": items, "logical_bytes": original_bytes,
                "stored_bytes": sum(item["stored_reference"]["bytes"] for item in items.values()),
                "source_paths_role": "HISTORICAL_UNPUBLISHED_STAGING_LOCATORS_ONLY",
                "source_capability_restored": False}
    _manifest_shape(manifest, destination, limits)
    for item in items.values():
        for _ in _logical_chunks(item, meter):
            pass
    originals()
    raw = json.dumps(manifest, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    _need(len(raw) <= limits.manifest_bytes, "COMPACT_MANIFEST_SIZE_LIMIT")
    meter.write(len(raw))
    with workspace._directory_fd(destination) as fd:
        _need(_dir_identity(os.fstat(fd)) == destination_identity and
              set(os.listdir(fd)) == {Path(i["stored_reference"]["path"]).name for i in items.values()},
              "COMPACT_DESTINATION_CHANGED")
        out = os.open(MANIFEST_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
    manifest_ref = _ref(destination / MANIFEST_NAME, meter, maximum=limits.manifest_bytes)
    # The final stored manifest is itself re-read; no originals are removed
    # until every compressed bytestring has passed exact logical/physical SHA.
    archive = CompactArchive(manifest_ref, limits=limits, _meter=meter)
    archive.verify_all()
    _need(terminal_validator() is True, "COMPACT_NATIVE_TERMINAL_VALIDATION_FAILED")
    # Native terminal callbacks may read many sources; the compact files must
    # still be exact after that work before we remove owned temporary bytes.
    archive.verify_all()
    originals()
    # Original-source checks may take time. Pin the complete new package once
    # more immediately before removing its temporary, uncompressed counterpart.
    final_refs = [item["stored_reference"] for item in items.values()] + [manifest_ref]
    with workspace._directory_fd(destination) as fd:
        _need(_dir_identity(os.fstat(fd)) == destination_identity and
              set(os.listdir(fd)) == {Path(ref["path"]).name for ref in final_refs},
              "COMPACT_DESTINATION_CHANGED")
        for ref in final_refs:
            _need(_fp(os.stat(Path(ref["path"]).name, dir_fd=fd, follow_symlinks=False)) == ref["fp"],
                  "COMPACT_DESTINATION_CHANGED_BEFORE_CLEANUP")
    # No shutil.rmtree: refuse unknown children and recheck each owned inode.
    with workspace._directory_fd(core) as fd:
        _need(_dir_identity(os.fstat(fd)) == core_identity, "COMPACT_CORE_DIRECTORY_CHANGED")
        for role in sorted(CORE_FILES):
            _need(_fp(os.stat(role, dir_fd=fd, follow_symlinks=False)) == refs[role]["fp"],
                  "COMPACT_OWNED_OUTPUT_CHANGED_BEFORE_CLEANUP")
        for role in sorted(CORE_FILES):
            _need(_fp(os.stat(role, dir_fd=fd, follow_symlinks=False)) == refs[role]["fp"],
                  "COMPACT_OWNED_OUTPUT_CHANGED_BEFORE_CLEANUP")
            os.unlink(role, dir_fd=fd)
        os.fsync(fd)
    with workspace._directory_fd(root) as fd:
        _need(_dir_identity(os.fstat(fd)) == state["identity"] and
              _dir_identity(os.stat("core", dir_fd=fd, follow_symlinks=False)) == core_identity,
              "COMPACT_STAGING_DIRECTORY_CHANGED")
        _need(_fp(os.stat("owned-token", dir_fd=fd, follow_symlinks=False)) == state["marker"]["fp"],
              "COMPACT_MARKER_CHANGED_BEFORE_CLEANUP")
        os.rmdir("core", dir_fd=fd)
        os.unlink("owned-token", dir_fd=fd)
        os.fsync(fd)
    with workspace._directory_fd(root.parent) as fd:
        _need(_dir_identity(os.stat(root.name, dir_fd=fd, follow_symlinks=False)) == state["identity"],
              "COMPACT_STAGING_DIRECTORY_CHANGED")
        os.rmdir(root.name, dir_fd=fd)
        os.fsync(fd)
    state["consumed"] = True
    return {"schema_version": VERSION, "manifest": manifest_ref, "logical_bytes": original_bytes,
            "stored_bytes": manifest["stored_bytes"] + manifest_ref["bytes"],
            "saved_bytes": original_bytes - manifest["stored_bytes"] - manifest_ref["bytes"],
            "staging_removed": True, "source_capability_restored": False,
            "returned_read_bytes": meter.read_bytes, "elapsed_seconds": time.monotonic() - meter.started,
            "peak_reserved_bytes": original_bytes + limits.stored_bytes + limits.manifest_bytes}
