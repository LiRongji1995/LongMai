"""R16-R1 post-commit review packaging; execution is always explicit.

Inclusion spec (UTF-8 JSON, paths relative to the spec file unless absolute)::

    {"version": "R16_R1_REVIEW_INCLUSION_V1", "entries": [
      {"source": "REPRODUCE.md", "destination": "REPRODUCE.md", "role": "reproduce"},
      {"source": "matrix.md", "destination": "R16_R1_ISSUE_MATRIX.md", "role": "closure"},
      {"source": "facts.jsonl", "destination": "evidence/facts.jsonl", "role": "raw-facts",
       "sha256": "optional expected 64 hexadecimal characters"}],
     "required_bundle_paths": ["evidence/facts.jsonl"],
     "external_assets": [], "metadata": {"model": "UNKNOWN", "reasoning": "UNKNOWN"}}

Entries name files, not directory globs. Every declared file is copied regardless
of suffix, or rejected explicitly by the secret/runtime-data policy. There is no
JSONL/profile allowlist omission. This script never commits, pushes, changes a
checkout, downloads history, or deletes a previous package/extraction.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
import zipfile

import psutil

BASE_COMMIT = "e2855d695f8818bb13103997e2ba1f1552538d99"
BASE_TREE = "81da2f17ea2adbc1e9488a1b53e2be1da2b31c8e"
# These existing upstream examples are actual PyTorch weights, not needed by
# StockRecommendation. Keep their identities; never package their payloads.
EXCLUDED_TRACKED_WEIGHTS = {
    "qlib/examples/benchmarks/GRU/csi300_gru_ts.pkl",
    "qlib/examples/benchmarks/GRU/model_gru_csi300.pkl",
    "qlib/examples/benchmarks/LSTM/csi300_lstm_ts.pkl",
    "qlib/examples/benchmarks/LSTM/model_lstm_csi300.pkl",
}
WEIGHT_EXCLUSION_REASON = "EXISTING_UPSTREAM_PYTORCH_EXAMPLE_WEIGHT; NO_MODEL_WEIGHTS_IN_REVIEW_BUNDLE; NOT_REQUIRED_BY_STOCKRECOMMENDATION"
EXPECTED_REMOTE = "github.com/LiRongji1995/stockfish-qlib-quant-system"
CONTROL_FILES = {"INCLUSION_MANIFEST.json", "SHA256SUMS"}
GENERATED_PREFIXES = {"final-source", "executed-source", "git", "packaging-smoke"}
GENERATED_FILES = CONTROL_FILES | {"REVIEW_METADATA.json", "SOURCE_BINDING.json", "INCLUSION_SPEC.json"}
DENIED_PARTS = {".git", ".venv", "venv", "virtualenv", "node_modules", "__pycache__", ".pytest_cache",
                ".mypy_cache", ".ruff_cache", ".tox", ".ssh", "provider_cache", "provider-cache"}
DENIED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".db3", ".mdb", ".accdb", ".pem", ".key", ".p12", ".pfx", ".jks"}
SOURCE_CODE_SUFFIXES = {".py", ".go", ".js", ".ts", ".tsx", ".jsx", ".md", ".txt", ".rst", ".toml",
                        ".json", ".yaml", ".yml", ".ini", ".cfg", ".c", ".h", ".cpp", ".sh", ".ps1"}
PEM_PRIVATE = re.compile(rb"(?:^|[\r\n])-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")
SECRET_BASENAMES = {"credentials", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml", "id_rsa", "id_ed25519"}


def utc():
    return datetime.now(timezone.utc).isoformat()


def json_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest_file(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def relative_name(value):
    if not isinstance(value, str) or not value or "\\" in value or any(c in value for c in "\r\n\t\0"):
        raise ValueError("INVALID_PORTABLE_RELATIVE_PATH")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} or ":" in part or part.endswith((" ", ".")) for part in path.parts):
        raise ValueError("UNSAFE_RELATIVE_PATH:" + value)
    if str(path) != value:
        raise ValueError("NONCANONICAL_RELATIVE_PATH:" + value)
    reserved = {"CON", "PRN", "AUX", "NUL", *("COM" + str(i) for i in range(1, 10)), *("LPT" + str(i) for i in range(1, 10))}
    if any(part.split(".")[0].upper() in reserved for part in path.parts):
        raise ValueError("WINDOWS_RESERVED_PATH:" + value)
    return value


def policy(path, *, tracked_source=False):
    """Return a rejection reason; source cache modules are code, not a cache dump."""
    path = Path(path)
    parts = {part.casefold() for part in path.parts}
    name = path.name.casefold()
    if parts & DENIED_PARTS:
        return "RUNTIME_OR_PRIVATE_DIRECTORY"
    if parts & {"cache", "caches", "gocache", "gomodcache"} and not (
            tracked_source and path.suffix.casefold() in SOURCE_CODE_SUFFIXES):
        return "CACHE_DIRECTORY"
    if name == ".env" or name.startswith(".env.") and not name.endswith((".example", ".sample", ".template")):
        return "ENVIRONMENT_SECRET_FILE"
    if name in SECRET_BASENAMES or path.suffix.casefold() in DENIED_SUFFIXES:
        return "DATABASE_OR_PRIVATE_KEY_FILE"
    if any(name.endswith(suffix) for suffix in (".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm", ".db-wal", ".db-shm")):
        return "DATABASE_SIDECAR"
    return None


class Packaging:
    def __init__(self, args):
        self.args = args
        self.repo = Path(args.repo_root).resolve()
        self.spec_path = Path(args.inclusion_spec).resolve()
        self.output = Path(args.output_dir).resolve()
        if self.output.exists():
            raise ValueError("OUTPUT_EXISTS_USE_A_NEW_DIRECTORY")
        self.output.mkdir(parents=True)
        self.staging = self.output / "staging"
        self.staging.mkdir()
        self.start = utc()
        self.started_perf = time.perf_counter()
        self.deadline = self.started_perf + args.budget_seconds
        self.origins = {}
        self.last_progress = 0.0
        self.git_commands = []
        self.smoke_commands = []
        self.progress("STARTED", repo=str(self.repo), output=str(self.output), budget_seconds=args.budget_seconds)

    def check_budget(self):
        if time.perf_counter() > self.deadline:
            raise TimeoutError("PACKAGING_BUDGET_EXCEEDED_PARTIAL_OUTPUT_RETAINED")

    def progress(self, phase, **values):
        self.check_budget()
        event = {"at": utc(), "phase": phase, "elapsed_seconds": time.perf_counter() - self.started_perf, **values}
        with (self.output / "packaging-progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        json_write(self.output / "packaging-checkpoint.json", event)
        print(json.dumps(event, ensure_ascii=False), flush=True)
        self.last_progress = time.perf_counter()

    def pulse(self, phase, **values):
        self.check_budget()
        if time.perf_counter() - self.last_progress >= 10:
            self.progress(phase, **values)

    def target(self, relative):
        relative = relative_name(relative)
        target = self.staging / relative
        if not target.resolve().is_relative_to(self.staging):
            raise ValueError("DESTINATION_ESCAPES_STAGING")
        if target.exists() or relative.casefold() in {name.casefold() for name in self.origins}:
            raise ValueError("DUPLICATE_BUNDLE_DESTINATION:" + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def copy(self, source, relative, *, role, expected=None, tracked_source=False, origin=None):
        source = Path(source)
        if source.is_symlink() or not source.is_file():
            raise ValueError("ONLY_REGULAR_FILES_MAY_BE_PACKAGED:" + str(source))
        source = source.resolve()
        if any(source.as_posix().endswith("/" + name) or relative.endswith("/" + name) or relative == name
               for name in EXCLUDED_TRACKED_WEIGHTS):
            raise ValueError("EXPLICITLY_EXCLUDED_MODEL_WEIGHT:" + str(source))
        rejected = policy(source, tracked_source=tracked_source) or policy(Path(relative), tracked_source=tracked_source)
        if rejected:
            raise ValueError(rejected + ":" + str(source))
        limit = self.args.max_source_file_bytes if tracked_source else self.args.max_evidence_file_bytes
        before = source.stat()
        if before.st_size > limit:
            raise ValueError("FILE_EXCEEDS_DECLARED_PACKAGING_LIMIT:" + str(source))
        target = self.target(relative)
        hash_value, tail, first = hashlib.sha256(), b"", True
        with source.open("rb") as src, target.open("xb") as dst:
            while chunk := src.read(1024 * 1024):
                self.pulse("COPYING", file=relative)
                if first and chunk.startswith(b"SQLite format 3\x00"):
                    raise ValueError("SQLITE_CONTENT_REJECTED:" + str(source))
                if PEM_PRIVATE.search(tail + chunk):
                    raise ValueError("PRIVATE_KEY_CONTENT_REJECTED:" + str(source))
                tail, first = chunk[-128:], False
                hash_value.update(chunk)
                dst.write(chunk)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("SOURCE_CHANGED_DURING_COPY:" + str(source))
        actual = hash_value.hexdigest()
        if expected is not None and actual != expected:
            raise ValueError("SOURCE_SHA256_MISMATCH:" + str(source))
        self.origins[relative] = {"role": role, "source": origin or str(source), "sha256": actual, "bytes": before.st_size}
        return actual

    def generated(self, relative, value, role):
        target = self.target(relative)
        json_write(target, value)
        self.origins[relative] = {"role": role, "source": "GENERATED", "sha256": digest_file(target), "bytes": target.stat().st_size}

    def command(self, label, argv, *, cwd=None, input_bytes=None, required=True, record=True, timeout=60):
        self.check_budget()
        started = utc()
        captured = subprocess.run(argv, cwd=cwd or self.repo, input=input_bytes, capture_output=True,
            timeout=max(.1, min(timeout, self.deadline-time.perf_counter())))
        record_value = {"label": label, "command_argv": list(map(str, argv)), "cwd": str(cwd or self.repo),
            "start": started, "end": utc(), "exit_code": captured.returncode}
        if record:
            folder = self.staging / "git/raw"
            folder.mkdir(parents=True, exist_ok=True)
            for suffix, content in (("stdout", captured.stdout), ("stderr", captured.stderr)):
                path = folder / (label + "." + suffix + ".log")
                path.write_bytes(content)
                self.origins[str(path.relative_to(self.staging)).replace("\\", "/")] = {
                    "role": "RAW_COMMAND", "source": "GENERATED", "sha256": digest_file(path), "bytes": len(content)}
            self.git_commands.append(record_value)
        if required and captured.returncode:
            raise RuntimeError("COMMAND_FAILED:" + label + ":" + str(captured.returncode))
        return captured

    def git(self, label, *arguments, **kwargs):
        return self.command(label, ["git", *arguments], **kwargs)

    def outside_git_temp(self, label):
        parent = Path(self.args.temp_root or tempfile.gettempdir()).resolve()
        parent.mkdir(parents=True, exist_ok=True)
        probe = subprocess.run(["git", "-C", str(parent), "rev-parse", "--show-toplevel"], capture_output=True, timeout=15)
        if probe.returncode == 0:
            raise ValueError("SMOKE_TEMP_ROOT_MUST_BE_OUTSIDE_ANY_GIT_CHECKOUT:" + str(parent))
        folder = Path(tempfile.mkdtemp(prefix="stock-r16-r1-" + label + "-", dir=parent)).resolve()
        self.progress("EXTRACTION_DIRECTORY_CREATED", phase_label=label, path=str(folder))
        return folder

    def extract(self, archive, destination, *, max_total=None):
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        seen, total = set(), 0
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                self.pulse("EXTRACTING", archive=str(archive), file=member.filename)
                raw = member.filename.rstrip("/") if member.is_dir() else member.filename
                relative_name(raw)
                folded = raw.casefold()
                if folded in seen:
                    raise ValueError("DUPLICATE_ZIP_MEMBER:" + raw)
                seen.add(folded)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError("SYMLINK_ZIP_MEMBER_REJECTED:" + raw)
                target = destination / raw
                if not target.resolve().is_relative_to(destination):
                    raise ValueError("ZIP_PATH_ESCAPES_DESTINATION")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total += member.file_size
                if total > (max_total or self.args.max_bundle_bytes):
                    raise ValueError("ZIP_EXCEEDS_UNCOMPRESSED_BUDGET")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("xb") as dst:
                    while chunk := src.read(1024 * 1024):
                        self.pulse("EXTRACTING", file=raw, extracted_bytes=total)
                        dst.write(chunk)
        return {"archive": str(archive), "destination": str(destination), "members": len(seen), "uncompressed_bytes": total}

    def source_archive(self):
        actual = self.git("repo-root", "rev-parse", "--show-toplevel").stdout.decode().strip()
        if Path(actual).resolve() != self.repo:
            raise ValueError("REPO_ROOT_DOES_NOT_MATCH_GIT_ROOT")
        base = self.git("base-commit", "rev-parse", self.args.base_commit + "^{commit}").stdout.decode().strip()
        base_tree = self.git("base-tree", "rev-parse", base + "^{tree}").stdout.decode().strip()
        if base != BASE_COMMIT or base_tree != BASE_TREE:
            raise ValueError("UNEXPECTED_R16_BASE_COMMIT_OR_TREE")
        final = self.git("final-commit", "rev-parse", self.args.final_commit + "^{commit}").stdout.decode().strip()
        head = self.git("head", "rev-parse", "HEAD").stdout.decode().strip()
        tree = self.git("final-tree", "rev-parse", final + "^{tree}").stdout.decode().strip()
        branch = self.git("branch", "branch", "--show-current").stdout.decode().strip()
        dirty = self.git("tracked-status", "status", "--porcelain", "--untracked-files=no").stdout
        all_status = self.git("all-status", "status", "--porcelain", "--untracked-files=normal").stdout.decode()
        if final != head or dirty or branch != "main":
            raise ValueError("PACKAGING_REQUIRES_REQUESTED_FINAL_HEAD_ON_CLEAN_TRACKED_MAIN")
        remote = self.git("remote-precheck", "remote", "get-url", "origin", record=False).stdout.decode().strip()
        parsed = urlsplit(remote) if "://" in remote else None
        if parsed and (parsed.username or parsed.password):
            raise ValueError("CREDENTIAL_BEARING_REMOTE_URL_REJECTED")
        normalized = remote.removesuffix(".git").replace("https://", "").replace("ssh://git@", "").replace("git@github.com:", "github.com/")
        if normalized != EXPECTED_REMOTE:
            raise ValueError("UNEXPECTED_REMOTE_TARGET")
        self.git("remote-origin", "remote", "get-url", "origin")
        local_origin = self.git("origin-main", "rev-parse", "refs/remotes/origin/main", required=False).stdout.decode().strip()
        remote_result = self.git("ls-remote-main", "ls-remote", "--heads", "origin", "main", required=False, timeout=45)
        remote_head = remote_result.stdout.decode().split()[0] if remote_result.returncode == 0 and remote_result.stdout.strip() else None
        self.git("base-final-log", "log", "--format=fuller", "--stat", base + ".." + final)
        changed_weights = self.git("excluded-weight-diff-check", "diff", "--name-only", "-z", base + ".." + final,
            "--", *sorted(EXCLUDED_TRACKED_WEIGHTS)).stdout
        if changed_weights:
            raise ValueError("EXCLUDED_WEIGHT_CHANGED; BINARY_PATCH_WOULD_PACKAGE_MODEL_BYTES")
        patch = self.git("base-final-patch", "diff", "--binary", "--full-index", base + ".." + final).stdout
        patch_path = self.target("git/base..final.patch")
        patch_path.write_bytes(patch)
        self.origins["git/base..final.patch"] = {"role": "BASE_TO_FINAL_PATCH", "source": "git diff --binary --full-index", "sha256": digest_file(patch_path), "bytes": len(patch)}
        archive = self.output / "final-tracked-source.git-archive.zip"
        self.git("final-source-archive", "archive", "--format=zip", "--output=" + str(archive), final, timeout=180)
        # The complete archive and its extraction are retained OUTSIDE staging.
        # The bundle gets a filtered archive with exactly four explicit omissions.
        original_archive_sha = digest_file(archive)
        unfiltered = self.output / "unfiltered-final-source-external"
        self.extract(archive, unfiltered)
        listing = self.git("final-tree-raw", "ls-tree", "-rz", "--full-tree", final).stdout
        entries = []
        for item in listing.split(b"\0"):
            if not item:
                continue
            header, name = item.split(b"\t", 1)
            mode, kind, oid = header.decode("ascii").split()
            relative = relative_name(name.decode("utf-8"))
            if kind != "blob" or mode not in {"100644", "100755"}:
                raise ValueError("UNSUPPORTED_GIT_SPECIAL_ENTRY:" + relative)
            if policy(Path(relative), tracked_source=True):
                raise ValueError("PROHIBITED_TRACKED_SOURCE:" + relative)
            entries.append((relative, mode, oid))
        expected_paths = {relative for relative, _, _ in entries}
        actual_paths = {p.relative_to(unfiltered).as_posix() for p in unfiltered.rglob("*") if p.is_file()}
        if not EXCLUDED_TRACKED_WEIGHTS <= expected_paths:
            raise ValueError("DECLARED_UPSTREAM_WEIGHT_EXCLUSION_SET_CHANGED")
        if expected_paths != actual_paths:
            raise ValueError("GIT_ARCHIVE_IS_NOT_FULL_TRACKED_TREE_EXPORT_IGNORE_OR_MISSING_PATH")
        # --stdin-paths uses Git's actual attributes/clean filters for each path.
        clean = self.git("working-clean-filter-hashes", "hash-object", "--stdin-paths",
            input_bytes=("\n".join(relative for relative, _, _ in entries) + "\n").encode("utf-8"), timeout=180).stdout.decode().splitlines()
        if len(clean) != len(entries):
            raise ValueError("CLEAN_FILTER_HASH_COUNT_MISMATCH")
        object_format = self.git("object-format", "rev-parse", "--show-object-format").stdout.decode().strip()
        bindings = {}
        for index, ((relative, mode, oid), clean_oid) in enumerate(zip(entries, clean)):
            self.pulse("BINDING_TRACKED_SOURCE", processed=index, total=len(entries), file=relative)
            if clean_oid != oid:
                raise ValueError("WORKING_BYTES_DO_NOT_CLEAN_TO_FINAL_BLOB:" + relative)
            archived = unfiltered / relative
            if archived.stat().st_size > self.args.max_source_file_bytes:
                raise ValueError("TRACKED_SOURCE_TOO_LARGE:" + relative)
            blob_hash = hashlib.new(object_format)
            blob_hash.update(("blob " + str(archived.stat().st_size) + "\0").encode())
            with archived.open("rb") as stream:
                first, tail = True, b""
                while chunk := stream.read(1024 * 1024):
                    if first and chunk.startswith(b"SQLite format 3\x00") or PEM_PRIVATE.search(tail + chunk):
                        raise ValueError("PROHIBITED_ARCHIVED_CONTENT:" + relative)
                    blob_hash.update(chunk)
                    first, tail = False, chunk[-128:]
            if blob_hash.hexdigest() != oid:
                raise ValueError("ARCHIVED_BYTES_DO_NOT_MATCH_GIT_BLOB_EXPORT_SUBST:" + relative)
            archive_sha = digest_file(archived)
            excluded = relative in EXCLUDED_TRACKED_WEIGHTS
            if excluded:
                raw_sha = digest_file(self.repo / relative)
            else:
                self.copy(archived, "final-source/" + relative, role="CANONICAL_FINAL_TRACKED_SOURCE",
                    expected=archive_sha, tracked_source=True, origin=final + ":" + relative)
                raw_sha = self.copy(self.repo / relative, "executed-source/" + relative,
                    role="EXECUTED_WORKING_BYTES", tracked_source=True)
            bindings[relative] = {"git_blob": oid, "git_mode": mode, "archive_sha256": archive_sha,
                "executed_working_sha256": raw_sha, "clean_filter_git_blob": clean_oid,
                "archive_equals_executed_bytes": archive_sha == raw_sha, "bytes": archived.stat().st_size,
                "included_in_bundle": not excluded, "exclusion_reason": WEIGHT_EXCLUSION_REASON if excluded else None}
        after_binding = self.git("tracked-status-after-binding", "status", "--porcelain", "--untracked-files=no").stdout
        if after_binding:
            raise ValueError("TRACKED_SOURCE_CHANGED_DURING_BINDING")
        filtered = self.output / "final-tracked-source.excluding-declared-weights.zip"
        with zipfile.ZipFile(archive) as original, zipfile.ZipFile(filtered, "x", allowZip64=True) as filtered_zip:
            filtered_zip.comment = ("Final commit " + final + "; four explicit upstream PyTorch weights excluded; see SOURCE_BINDING.json").encode()
            for member in original.infolist():
                if member.filename.rstrip("/") in EXCLUDED_TRACKED_WEIGHTS:
                    continue
                with original.open(member) as src, filtered_zip.open(copy.copy(member), "w", force_zip64=True) as dst:
                    while chunk := src.read(1024 * 1024):
                        self.pulse("FILTERING_SOURCE_ARCHIVE", file=member.filename)
                        dst.write(chunk)
        self.copy(filtered, "git/final-tracked-source.excluding-declared-weights.zip", role="FINAL_SOURCE_ARCHIVE_WITH_EXPLICIT_WEIGHT_EXCLUSIONS")
        filtered_sha = digest_file(filtered)
        included_paths = {p.relative_to(self.staging / "final-source").as_posix()
            for p in (self.staging / "final-source").rglob("*") if p.is_file()}
        if included_paths != expected_paths - EXCLUDED_TRACKED_WEIGHTS:
            raise ValueError("FILTERED_SOURCE_COVERAGE_MISMATCH")
        exclusions = {name: bindings[name] for name in sorted(EXCLUDED_TRACKED_WEIGHTS)}
        self.generated("git/EXPLICIT_SOURCE_EXCLUSIONS.json", {
            "original_complete_archive": {"path_external_to_bundle": str(archive), "sha256": original_archive_sha, "packaged": False},
            "filtered_archive_sha256": filtered_sha, "all_tracked_files": len(entries),
            "included_tracked_files": len(included_paths), "excluded_files": exclusions,
            "completeness_statement": "All final tracked files except these four declared upstream model weights; application source/dependency closure is complete",
        }, "EXPLICIT_MODEL_WEIGHT_EXCLUSIONS")
        self.generated("SOURCE_BINDING.json", {"version": "R16_R1_ARCHIVE_EXECUTED_BYTES_BINDING_V1", "base_commit": base,
            "base_tree": base_tree, "final_commit": final, "final_tree": tree, "git_object_format": object_format,
            "source_policy": "final-source contains every canonical final tracked file except four explicitly declared upstream PyTorch weights; executed-source follows the same exclusions; all tracked files including excluded weights retain blob/SHA/mode/clean-filter identity",
            "original_complete_archive_sha256": original_archive_sha, "original_complete_archive_packaged": False,
            "filtered_archive_sha256": filtered_sha, "excluded_files": exclusions,
            "extracted_git_identity": "UNKNOWN_NO_DOT_GIT; FINAL_IDENTITY_COMES_FROM_THIS_EXTERNAL_BINDING", "files": bindings}, "SOURCE_BINDING")
        self.final_commit, self.final_tree = final, tree
        return {"base_commit": base, "base_tree": base_tree, "final_commit": final, "final_tree": tree, "branch": branch,
            "remote_target": remote, "origin_main_local": local_origin or None, "remote_main_observed": remote_head,
            "push_verified": remote_head == final and local_origin == final, "tracked_clean": True,
            "all_status_raw": all_status, "all_clean": not bool(all_status), "archive_sha256": filtered_sha,
            "original_complete_archive_sha256": original_archive_sha, "original_complete_archive_packaged": False,
            "tracked_file_count": len(entries), "included_tracked_file_count": len(included_paths), "excluded_tracked_files": exclusions}

    def include_evidence(self):
        spec = json.loads(self.spec_path.read_text(encoding="utf-8"))
        if spec.get("version") != "R16_R1_REVIEW_INCLUSION_V1" or not isinstance(spec.get("entries"), list):
            raise ValueError("INVALID_INCLUSION_SPEC")
        self.copy(self.spec_path, "INCLUSION_SPEC.json", role="EXPLICIT_INCLUSION_SPEC")
        for entry in spec["entries"]:
            if not isinstance(entry, dict) or set(entry) - {"source", "destination", "role", "sha256"}:
                raise ValueError("INVALID_INCLUSION_ENTRY")
            relative = relative_name(entry["destination"])
            if relative in GENERATED_FILES or PurePosixPath(relative).parts[0] in GENERATED_PREFIXES:
                raise ValueError("EVIDENCE_CANNOT_REPLACE_GENERATED_ARTIFACT:" + relative)
            if not isinstance(entry.get("role"), str) or not entry["role"]:
                raise ValueError("EVIDENCE_ROLE_REQUIRED")
            source = Path(entry["source"])
            if not source.is_absolute():
                source = self.spec_path.parent / source
            wanted = entry.get("sha256")
            if wanted is not None and (not isinstance(wanted, str) or re.fullmatch("[0-9a-f]{64}", wanted) is None):
                raise ValueError("INVALID_EXPECTED_SHA256")
            self.copy(source, relative, role=entry["role"], expected=wanted)
        required = {"REPRODUCE.md", *spec.get("required_bundle_paths", [])}
        if not any((self.staging / name).is_file() for name in ("R16_R1_ISSUE_MATRIX.md", "R16_R1_ISSUE_MATRIX.json")):
            raise ValueError("ISSUE_CLOSURE_MATRIX_REQUIRED_AT_BUNDLE_ROOT")
        for relative in required:
            relative_name(relative)
            if not (self.staging / relative).is_file():
                raise ValueError("REQUIRED_BUNDLE_PATH_MISSING:" + relative)
        return spec

    def controls(self, phase):
        rows, total = {}, 0
        for path in sorted(self.staging.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.staging).as_posix()
            if relative in CONTROL_FILES:
                continue
            self.pulse("HASHING_BUNDLE", phase_label=phase, file=relative)
            if relative not in self.origins:
                raise ValueError("UNREGISTERED_STAGING_FILE:" + relative)
            size, sha = path.stat().st_size, digest_file(path)
            if sha != self.origins[relative]["sha256"]:
                raise ValueError("STAGING_FILE_CHANGED:" + relative)
            total += size
            rows[relative] = {**self.origins[relative], "sha256": sha, "bytes": size}
        if total > self.args.max_bundle_bytes:
            raise ValueError("BUNDLE_EXCEEDS_DECLARED_UNCOMPRESSED_LIMIT")
        manifest = {"version": "R16_R1_REVIEW_INCLUSION_MANIFEST_V1", "phase": phase, "created_at": utc(),
            "final_commit": self.final_commit, "final_tree": self.final_tree, "file_count": len(rows), "payload_bytes": total,
            "hash_policy": "Manifest lists payload files only; SHA256SUMS covers all files except itself, including this manifest",
            "files": rows}
        json_write(self.staging / "INCLUSION_MANIFEST.json", manifest)
        checksums = {name: row["sha256"] for name, row in rows.items()}
        checksums["INCLUSION_MANIFEST.json"] = digest_file(self.staging / "INCLUSION_MANIFEST.json")
        (self.staging / "SHA256SUMS").write_text("".join(sha + "  " + name + "\n" for name, sha in sorted(checksums.items())), encoding="utf-8")
        return manifest

    def zip_staging(self, path):
        if path.exists():
            raise ValueError("ZIP_ALREADY_EXISTS")
        self.progress("WRITING_ZIP", path=str(path))
        with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
            for item in sorted(self.staging.rglob("*")):
                if item.is_file():
                    self.pulse("WRITING_ZIP", file=item.relative_to(self.staging).as_posix())
                    zf.write(item, item.relative_to(self.staging).as_posix())
        return {"path": str(path), "sha256": digest_file(path), "bytes": path.stat().st_size}

    def verify(self, extraction):
        extraction = Path(extraction)
        expected = {}
        for line in (extraction / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            sha, relative = line.split("  ", 1)
            relative_name(relative)
            if re.fullmatch("[0-9a-f]{64}", sha) is None or relative.casefold() in {p.casefold() for p in expected}:
                raise ValueError("INVALID_SHA256SUMS")
            expected[relative] = sha
        actual = {p.relative_to(extraction).as_posix() for p in extraction.rglob("*") if p.is_file()}
        if actual != set(expected) | {"SHA256SUMS"}:
            raise ValueError("EXTRACTED_MEMBERS_DO_NOT_MATCH_CHECKSUM_LIST")
        for relative, wanted in expected.items():
            self.pulse("VERIFYING_EXTRACTED_SHA256", file=relative)
            if digest_file(extraction / relative) != wanted:
                raise ValueError("EXTRACTED_CHECKSUM_MISMATCH:" + relative)
        manifest = json.loads((extraction / "INCLUSION_MANIFEST.json").read_text(encoding="utf-8"))
        if set(manifest["files"]) != actual - CONTROL_FILES:
            raise ValueError("INCLUSION_MANIFEST_COVERAGE_MISMATCH")
        for relative, record in manifest["files"].items():
            if record["sha256"] != expected[relative] or record["bytes"] != (extraction / relative).stat().st_size:
                raise ValueError("INCLUSION_RECORD_MISMATCH:" + relative)
        return {"status": "PASS", "checked_files": len(expected), "extraction": str(extraction),
                "manifest_sha256": expected["INCLUSION_MANIFEST.json"], "source_git_identity": "UNKNOWN_NO_DOT_GIT"}

    @contextmanager
    def smoke_environment(self, extracted, work):
        updates = {"PYTHONPATH": str(extracted / "final-source/stock-fish"), "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1", "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off",
            "GOCACHE": str(work / "go-cache"), "GOTMPDIR": str(work / "go-tmp")}
        for directory in (work / "go-cache", work / "go-tmp"):
            directory.mkdir(parents=True, exist_ok=True)
        previous = {key: os.environ.get(key) for key in updates}
        os.environ.update(updates)
        try:
            yield updates
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def smoke(self, extraction, label):
        from r16_r1_supervise import supervise
        root = extraction / "final-source"
        output = extraction.parent / (extraction.name + "-" + label + "-smoke")
        output.mkdir()
        work = output / "runtime-work"
        work.mkdir()
        results = []
        def command(name, argv, cwd, seconds):
            self.progress("SMOKE_COMMAND", phase_label=label, name=name, command_argv=argv, budget_seconds=seconds)
            budget = min(seconds, self.deadline-time.perf_counter())
            if budget <= 0:
                raise TimeoutError("PACKAGING_BUDGET_EXCEEDED")
            row = supervise(argv, output / "commands" / name, cwd=cwd, source_root=root,
                registry=output / "smoke-registry.json", budget_seconds=budget,
                input_manifest=extraction / "INCLUSION_MANIFEST.json", label=label + ":" + name)
            results.append({"name": name, "command_argv": argv, "cwd": str(cwd), "budget_seconds": budget,
                "start": row["start"], "end": row["end"], "exit_code": row["exit_code"], "status": row["status"],
                "source_identity": "UNKNOWN_NO_DOT_GIT", "bound_final_commit": self.final_commit,
                "bound_final_tree": self.final_tree, "raw_run_path": str(output / "commands" / name / "run.json")})
            json_write(output / "smoke-checkpoint.json", {"status": "RUNNING", "commands": results})
            if row["exit_code"] != 0:
                raise RuntimeError("EXTRACTED_SMOKE_FAILED:" + name)
        with self.smoke_environment(extraction, work) as environment:
            command("python-contract", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                "--basetemp=" + str(work / "pytest"), "tests/r05_product/test_r16_r1_contract_regressions.py",
                "tests/r05_product/test_r16_go_migration_phase0.py"], root / "stock-fish", self.args.test_budget_seconds)
            command("go-test", [self.args.go_command, "test", "./..."], root / "go-core", self.args.test_budget_seconds)
            oracle = output / "oracle"
            command("production-oracle", [sys.executable, str(root / "stock-fish/scripts/r16_production_oracle.py"),
                "--output-dir", str(oracle), "--fixtures-dir", str(root / "stock-fish/tests/fixtures/r16_r1_equivalence"),
                "--go-binary-command", self.args.go_command, "--case", "NORMAL", "--case", "UNIT_AND_ROUNDING",
                "--case", "BUSINESS_SLICE_RANK_TIE"], root / "stock-fish", self.args.oracle_budget_seconds)
            for side, prefix, facts_name in (("left", "python", "python_production_facts.jsonl"), ("right", "go", "go_facts.jsonl")):
                folder = output / "contract" / side
                folder.mkdir(parents=True)
                shutil.copyfile(oracle / "NORMAL" / (prefix + "_manifest.json"), folder / "feature_evidence_manifest_v1.json")
                shutil.copyfile(oracle / "NORMAL" / facts_name, folder / "feature_evidence_facts_v1.jsonl")
                command("schema-" + side, [sys.executable, str(root / "stock-fish/scripts/r16_validate_contract_schema.py"),
                    "--snapshot-dir", str(folder), "--output-json", str(output / ("schema-" + side + ".json"))], root / "stock-fish", 30)
            command("snapshot-comparator", [sys.executable, str(root / "stock-fish/scripts/r16_compare_contract_snapshots.py"),
                "--left", str(output / "contract/left"), "--right", str(output / "contract/right"),
                "--output-json", str(output / "comparison.json")], root / "stock-fish", 30)
        cases = {}
        for name in ("NORMAL", "UNIT_AND_ROUNDING", "BUSINESS_SLICE_RANK_TIE"):
            result = json.loads((output / "oracle" / name / "result.json").read_text(encoding="utf-8"))
            identity = result["provenance"]
            if any(identity.get(key) is not None for key in ("commit", "tree", "dirty")) or identity.get("git_root_matches") is not False:
                raise ValueError("EXTRACTED_ORACLE_MUST_REPORT_UNKNOWN_GIT_IDENTITY")
            if result["status"] != "PASS":
                raise ValueError("PACKAGING_ORACLE_CASE_DID_NOT_PASS:" + name)
            cases[name] = {"status": result["status"], "source_git_identity": "UNKNOWN", "result_sha256": digest_file(output / "oracle" / name / "result.json")}
        # Tests must not change any extracted source/evidence file.
        checksum_after = self.verify(extraction)
        report = {"version": "R16_R1_EXTRACTED_PACKAGING_SMOKE_V1", "status": "PASS", "label": label,
            "extracted_source": str(root), "git_identity": "UNKNOWN_NO_DOT_GIT", "binding_commit": self.final_commit,
            "binding_tree": self.final_tree, "python_executable": sys.executable, "environment_overrides": environment,
            "commands": results, "oracle_cases": cases, "checksum_after_smoke": checksum_after,
            "scope": "Actual extracted source contract pytest, Go tests, three frozen production-oracle cases, both schemas, comparator; no production DB/network provider"}
        json_write(output / "smoke-report.json", report)
        return output, report

    def include_smoke(self, output):
        omitted = []
        for path in sorted(output.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(output)
            rejected = "SMOKE_RUNTIME_WORK_DIRECTORY" if relative.parts[0] == "runtime-work" else policy(relative)
            if rejected:
                omitted.append({"path": relative.as_posix(), "reason": rejected, "bytes": path.stat().st_size})
                continue
            self.copy(path, "packaging-smoke/initial/" + relative.as_posix(), role="INITIAL_EXTRACTED_PACKAGING_SMOKE")
        self.generated("packaging-smoke/initial/RUNTIME_ASSETS_NOT_PACKAGED.json", {"policy": "Only generated smoke runtime DB/cache/temp files omitted; JSONL/profile/log outputs retained", "omitted": omitted}, "SMOKE_RUNTIME_EXCLUSIONS")

    def run(self):
        sys.path.insert(0, str(self.repo / "stock-fish/scripts"))
        git_state = self.source_archive()
        spec = self.include_evidence()
        python_packages = self.command("python-packages", [sys.executable, "-m", "pip", "list", "--format=json", "--disable-pip-version-check"], timeout=60)
        go = self.command("go-toolchain", [self.args.go_command, "version"], timeout=30)
        metadata = {"version": "R16_R1_REVIEW_METADATA_V1", "generated_at": utc(), **git_state,
            "os": platform.platform(), "machine": platform.machine(), "logical_cpus": psutil.cpu_count(),
            "physical_cpus": psutil.cpu_count(logical=False), "ram_bytes": psutil.virtual_memory().total,
            "python": sys.version, "python_executable": sys.executable, "go": go.stdout.decode().strip(),
            "installed_python_packages": json.loads(python_packages.stdout), "model": spec.get("metadata", {}).get("model", "UNKNOWN"),
            "reasoning": spec.get("metadata", {}).get("reasoning", "UNKNOWN"), "provided_metadata": spec.get("metadata", {}),
            "external_assets_not_in_bundle": spec.get("external_assets", []), "inclusion_spec_sha256": digest_file(self.spec_path),
            "packaging_script": {"path": str(Path(__file__).resolve()), "sha256": digest_file(__file__)},
            "packaging_policy": "POST_COMMIT_COMPLETE_SOURCE_EXCEPT_FOUR_EXPLICIT_UPSTREAM_WEIGHTS_PLUS_EXECUTED_BYTES; ORIGINAL_FULL_ARCHIVE_EXTERNAL_ONLY; TWO_ZIP_EXTRACTION_SMOKES; FINAL_VERIFICATION_SIDECAR",
            "source_git_identity_inside_extraction": "UNKNOWN", "final_identity_source": "SOURCE_BINDING.json and Git raw evidence",
            "market_edge": "NOT_ESTABLISHED"}
        self.generated("REVIEW_METADATA.json", metadata, "REVIEW_METADATA")
        self.generated("git/COMMANDS.json", self.git_commands, "RAW_GIT_AND_TOOLCHAIN_COMMAND_METADATA")
        self.controls("INITIAL_BEFORE_PACKAGING_SMOKE")
        initial_zip = self.output / "initial-review-candidate.zip"
        initial_archive = self.zip_staging(initial_zip)
        first = self.outside_git_temp("initial")
        initial_extract = self.extract(initial_zip, first)
        initial_verify = self.verify(first)
        smoke_path, initial_smoke = self.smoke(first, "initial")
        self.include_smoke(smoke_path)
        self.controls("FINAL_WITH_INITIAL_PACKAGING_SMOKE")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        final_zip = self.output / ("StockRecommendation_R16_R1_ReviewBundle_" + timestamp + ".zip")
        final_archive = self.zip_staging(final_zip)
        second = self.outside_git_temp("final")
        final_extract = self.extract(final_zip, second)
        final_verify = self.verify(second)
        final_smoke_path, final_smoke = self.smoke(second, "final")
        post = self.git("post-packaging-tracked-status", "status", "--porcelain", "--untracked-files=no", record=False).stdout
        head = self.git("post-packaging-head", "rev-parse", "HEAD", record=False).stdout.decode().strip()
        if post or head != self.final_commit:
            raise ValueError("CHECKOUT_CHANGED_DURING_PACKAGING")
        sidecar = {"version": "R16_R1_FINAL_PACKAGE_VERIFICATION_V1", "status": "PASS", "start": self.start, "end": utc(),
            "final_commit": self.final_commit, "final_tree": self.final_tree, "final_zip": final_archive,
            "initial_zip": initial_archive, "initial_extraction": initial_extract, "initial_checksums": initial_verify,
            "initial_smoke": initial_smoke, "final_extraction": final_extract, "final_checksums": final_verify,
            "final_smoke": final_smoke, "final_smoke_evidence_path": str(final_smoke_path),
            "tracked_clean_after": True, "sidecar_policy": "Outside final ZIP; never inserted into artifact it hashes"}
        sidecar_path = self.output / (final_zip.name + ".verification.json")
        json_write(sidecar_path, sidecar)
        (self.output / (final_zip.name + ".sha256")).write_text(final_archive["sha256"] + "  " + final_zip.name + "\n", encoding="utf-8")
        self.progress("COMPLETE", zip_path=str(final_zip), sha256=final_archive["sha256"], verification_sidecar=str(sidecar_path))
        return sidecar


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--base-commit", default=BASE_COMMIT)
    parser.add_argument("--final-commit", required=True)
    parser.add_argument("--inclusion-spec", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--temp-root", help="Must be outside every Git checkout; default OS temp directory")
    parser.add_argument("--go-command", default="go")
    parser.add_argument("--budget-seconds", type=float, default=1800)
    parser.add_argument("--test-budget-seconds", type=float, default=180)
    parser.add_argument("--oracle-budget-seconds", type=float, default=45)
    parser.add_argument("--max-source-file-bytes", type=int, default=64 * 1024**2)
    parser.add_argument("--max-evidence-file-bytes", type=int, default=4 * 1024**3)
    parser.add_argument("--max-bundle-bytes", type=int, default=8 * 1024**3)
    args = parser.parse_args(argv)
    for name in ("budget_seconds", "test_budget_seconds", "oracle_budget_seconds", "max_source_file_bytes", "max_evidence_file_bytes", "max_bundle_bytes"):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            parser.error("positive finite budgets/limits required: " + name)
    package = Packaging(args)
    try:
        package.run()
    except BaseException as exc:
        json_write(package.output / "PACKAGING_FAILED.json", {"status": "FAILED", "error_type": type(exc).__name__,
            "error": str(exc), "start": package.start, "end": utc(), "partial_output_retained": str(package.output)})
        raise


if __name__ == "__main__":
    main()
