"""Build/verify a new original-workflow source bundle; never execute capture.

Two exact producer patches fix future factor checkpoints. Historical sources,
requests and checkpoints remain untouched. The manifest authenticates a local
source snapshot, not market data, an old physical owner or retry permission.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import build_premarket_source_bundle_v2 as previous
from scripts import codex_factor_capture_v3 as patches

VERSION = "PREMARKET_FACTOR_CHECKPOINT_SOURCE_BUNDLE_V3"
WORKER, PARENT = patches.WORKER, patches.PARENT
MANIFEST = "source-bundle-manifest.json"
MAX_FILES = 512
MAX_FILE_BYTES = previous.MAX_FILE_BYTES
MAX_SOURCE_BYTES = previous.MAX_SOURCE_BYTES
FP_KEYS = previous.FP_KEYS
SCRIPT_ROOTS = frozenset((
    "codex_premarket_shared_v1", "codex_native_theme_shared_v1",
    "codex_daily_session_v1", "codex_daily_v2", "codex_premarket_preflight_v1",
))
# Exact original/shared worker closure, derived from its _script_refs contract.
# Discovering another scripts import requires a reviewed new allowlist.
SCRIPT_NAMES = frozenset((
    "codex_capacity", "codex_core", "codex_core_archive", "codex_core_archive_v2",
    "codex_core_sources", "codex_daily", "codex_daily_plan_v1", "codex_daily_session_v1",
    "codex_daily_v2", "codex_native_theme_shared_v1", "codex_premarket",
    "codex_premarket_preflight_v1", "codex_premarket_shared_v1", "codex_result_view_v1",
    "codex_shared_outcomes_v1", "codex_storage_growth_v1", "codex_verification_v2",
    "codex_workspace",
))
GATEWAY_SCRIPTS = frozenset((
    "codex_factor_checkpoint_premarket_v1", "codex_runtime_recovery_v1",
    "codex_recovered_premarket_v1",
    "build_premarket_source_bundle_v3", "build_premarket_source_bundle_v2",
    "codex_factor_capture_v3",
))
PUBLIC_RESOURCES = frozenset(previous.PUBLIC_RESOURCES) | {
    "scripts/" + name + ".py" for name in SCRIPT_NAMES
} | {
    "scripts/" + name + ".py" for name in GATEWAY_SCRIPTS
}
FROZEN = {name: value for name, value in previous.UNCHANGED.items() if name != PARENT}
PATCHED = {
    WORKER: (patches.ORIGINAL_WORKER_SHA256, patches.patch_worker),
    PARENT: (patches.ORIGINAL_PARENT_SHA256, patches.patch_parent),
}
PROVENANCE = {
    name: ("_source_provenance/" + Path(name).name + ".original.txt",
           "_source_provenance/" + Path(name).name + ".patch") for name in PATCHED
}
TOOLS = (Path(__file__).absolute(), Path(previous.__file__).absolute(), Path(patches.__file__).absolute())


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _fp(info):
    return {key: getattr(info, key, 0) for key in FP_KEYS}


def _absolute(path):
    path = Path(path).absolute()
    need(".." not in path.parts, "CANONICAL_PATH_REQUIRED")
    return path


@contextmanager
def _directory_fd(path, *, create=False):
    """Pin every ancestor, refusing symlinks even during a path swap."""
    path = _absolute(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def read(path, limit=MAX_FILE_BYTES):
    path = _absolute(path)
    with _directory_fd(path.parent) as parent:
        parent_identity = os.fstat(parent)
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        need(stat.S_ISREG(before.st_mode) and not getattr(before, "st_flags", 0) & 0x40000000,
             "RESIDENT_REGULAR_FILE_REQUIRED")
        need(before.st_size <= limit, "SOURCE_FILE_TOO_LARGE")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        descriptor = os.open(path.name, flags, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as handle:
            need(_fp(os.fstat(handle.fileno())) == _fp(before), "OPENED_SOURCE_IDENTITY_CHANGED")
            raw = handle.read(limit + 1)
            need(_fp(os.fstat(handle.fileno())) == _fp(before), "OPENED_SOURCE_CHANGED_DURING_READ")
        need(len(raw) <= limit and _fp(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) == _fp(before),
             "SOURCE_FILE_CHANGED")
    with _directory_fd(path.parent) as terminal:
        current = os.fstat(terminal)
        need((current.st_dev, current.st_ino) == (parent_identity.st_dev, parent_identity.st_ino)
             and _fp(os.stat(path.name, dir_fd=terminal, follow_symlinks=False)) == _fp(before),
             "SOURCE_PATH_CHANGED")
    return raw, dict(path=str(path), bytes=len(raw), sha256=sha(raw), fp=_fp(before))


def _tree(path):
    """Names only; each selected file is independently descriptor-authenticated."""
    root = _absolute(path)
    files, directories = set(), set()
    def walk(fd, prefix):
        names = os.listdir(fd)
        need(len(files) + len(directories) + len(names) <= MAX_FILES * 3, "SOURCE_TREE_LIMIT")
        for name in names:
            relative = (prefix + "/" if prefix else "") + name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            need(not stat.S_ISLNK(info.st_mode), "SOURCE_TREE_SYMLINK")
            if stat.S_ISDIR(info.st_mode):
                directories.add(relative)
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    need((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino), "SOURCE_DIRECTORY_CHANGED")
                    walk(child, relative)
                    after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    need((after.st_dev, after.st_ino) == (info.st_dev, info.st_ino), "SOURCE_DIRECTORY_CHANGED")
                finally:
                    os.close(child)
            else:
                need(stat.S_ISREG(info.st_mode), "SOURCE_TREE_REGULAR_FILES_REQUIRED")
                files.add(relative)
    with _directory_fd(root) as descriptor:
        initial = os.fstat(descriptor)
        walk(descriptor, "")
    with _directory_fd(root) as terminal:
        final = os.fstat(terminal)
        need((initial.st_dev, initial.st_ino) == (final.st_dev, final.st_ino), "SOURCE_ROOT_CHANGED")
    return files, directories


def _script_closure(source_bytes):
    pending, seen = set(SCRIPT_ROOTS), set()
    while pending:
        name = pending.pop()
        need(name in SCRIPT_NAMES, "UNREGISTERED_SCRIPT_DEPENDENCY")
        tree = ast.parse(source_bytes["scripts/" + name + ".py"])
        seen.add(name)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name[8:] for alias in node.names if alias.name.startswith("scripts."))
            elif isinstance(node, ast.ImportFrom):
                need(not node.level, "RELATIVE_SCRIPT_IMPORT_UNSUPPORTED")
                if node.module == "scripts":
                    imports.update(alias.name for alias in node.names)
                elif node.module and node.module.startswith("scripts."):
                    imports.add(node.module[8:])
        pending.update(imports - seen)
    need(seen == SCRIPT_NAMES, "ORIGINAL_WORKFLOW_SCRIPT_CLOSURE_CHANGED")
    # The explicit gateway is also self-contained; no arbitrary sibling script
    # is copied merely because an import was added to the developer checkout.
    allowed = SCRIPT_NAMES | GATEWAY_SCRIPTS
    for name in GATEWAY_SCRIPTS:
        for node in ast.walk(ast.parse(source_bytes["scripts/" + name + ".py"])):
            if isinstance(node, ast.Import):
                need(all(alias.name[8:] in allowed for alias in node.names
                         if alias.name.startswith("scripts.")), "UNREGISTERED_GATEWAY_SCRIPT_DEPENDENCY")
            elif isinstance(node, ast.ImportFrom):
                need(not node.level, "RELATIVE_GATEWAY_IMPORT_UNSUPPORTED")
                if node.module == "scripts":
                    need(all(alias.name in allowed for alias in node.names), "UNREGISTERED_GATEWAY_SCRIPT_DEPENDENCY")
                elif node.module and node.module.startswith("scripts."):
                    need(node.module[8:] in allowed, "UNREGISTERED_GATEWAY_SCRIPT_DEPENDENCY")


def inventory(source_root):
    root = _absolute(source_root)
    files, _ = _tree(root / "stockfish_core")
    selected = {"stockfish_core/" + name: root / "stockfish_core" / name
                for name in files if Path(name).suffix in (".py", ".json")}
    selected.update({name: root / name for name in PUBLIC_RESOURCES})
    selected["THIRD_PARTY_NOTICES.md"] = root.parent / "THIRD_PARTY_NOTICES.md"
    need(len(selected) <= MAX_FILES and {WORKER, PARENT, *FROZEN,
         "stockfish_core/exchange_calendar_2026.json", "stockfish_core/provider_classification.json"}
         <= set(selected), "SOURCE_CLOSURE_MISSING_OR_TOO_LARGE")
    return selected


def _tools():
    return {path.name: read(path)[1] for path in TOOLS}


def _tool_hashes(references):
    return {name: {key: ref[key] for key in ("sha256", "bytes")} for name, ref in references.items()}


def _directory_identity(path):
    with _directory_fd(path) as descriptor:
        info = os.fstat(descriptor)
        return info.st_dev, info.st_ino


def _write_new(path, raw, *, executable=False):
    path = _absolute(path)
    with _directory_fd(path.parent, create=True) as parent:
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fchmod(handle.fileno(), 0o555 if executable else 0o444)
            os.fsync(handle.fileno())
        os.fsync(parent)
    written, reference = read(path)
    need(written == raw, "NEW_SOURCE_WRITE_CHANGED")
    return reference


def _revoke_manifest(reference):
    """A failed creation retains payloads, but not its owned success manifest."""
    path = Path(reference["path"])
    with _directory_fd(path.parent) as parent:
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        need((current.st_dev, current.st_ino) == (reference["fp"]["st_dev"], reference["fp"]["st_ino"]),
             "FAILED_CREATION_MANIFEST_IDENTITY_CHANGED")
        os.unlink(path.name, dir_fd=parent)
        os.fsync(parent)


def create(source_root, destination):
    root, destination = _absolute(source_root), _absolute(destination)
    source_identity = _directory_identity(root)
    need(not destination.is_relative_to(root) and not root.is_relative_to(destination),
         "BUNDLE_MUST_BE_SEPARATE_FROM_SOURCE")
    tool_refs = _tools()
    selected = inventory(root)
    source_refs, contents, total = {}, {}, 0
    for name, path in sorted(selected.items()):
        raw, source_refs[name] = read(path)
        contents[name] = raw
        total += len(raw)
        need(total <= MAX_SOURCE_BYTES, "SOURCE_BUNDLE_TOO_LARGE")
        if name in FROZEN:
            need(sha(raw) == FROZEN[name], "FROZEN_DEPENDENCY_MISMATCH")
    _script_closure(contents)
    need(all(contents["scripts/" + name] == read(path)[0] for name, path in
             ((path.name, path) for path in TOOLS)), "BUNDLED_BUILD_TOOL_BYTES_DIFFER")
    with _directory_fd(destination.parent) as parent:
        os.mkdir(destination.name, mode=0o700, dir_fd=parent)
        os.fsync(parent)
        created = os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
        destination_identity = (created.st_dev, created.st_ino)
    need(_directory_identity(destination) == destination_identity, "BUNDLE_ROOT_CHANGED")
    references = {}
    for name, raw in sorted(contents.items()):
        if name in PATCHED:
            expected, transform = PATCHED[name]
            need(sha(raw) == expected, "ORIGINAL_PRODUCER_SHA256_MISMATCH")
            changed, diff = transform(raw)
            compile(changed, name, "exec")
            preimage, patch = PROVENANCE[name]
            references[preimage] = _write_new(destination / preimage, raw)
            references[patch] = _write_new(destination / patch, diff)
            raw = changed
        references[name] = _write_new(destination / name, raw, executable=name == "premarket")
    need(inventory(root) == selected, "SOURCE_INVENTORY_CHANGED")
    for name, path in selected.items():
        need(read(path)[1] == source_refs[name], "SOURCE_CHANGED_DURING_BUILD")
    need(_tools() == tool_refs, "BUILD_TOOL_CHANGED")
    need(_directory_identity(destination) == destination_identity, "BUNDLE_ROOT_CHANGED")
    manifest = dict(schema_version=VERSION, created_at=datetime.now(timezone.utc).isoformat(),
        source_root=str(root), bundle_root=str(destination), tools=_tool_hashes(tool_refs),
        sources=source_refs, files=references, source_bytes=total,
        producer_hashes={Path(name).name: references[name]["sha256"] for name in (WORKER, PARENT,
            "stockfish_core/yaogu/provider_budget.py")},
        original_producer_hashes={name: item[0] for name, item in PATCHED.items()},
        frozen_hashes=FROZEN, original_workflow_scripts=sorted(SCRIPT_NAMES),
        contract=patches.FACTOR_CHECKPOINT_CONTRACT,
        source_reference_scope="CREATION_TIME_SOURCE_GUARD_ONLY_NOT_LIVE_DEPENDENCY",
        source_changed_during_creation=False, original_physical_owner_restored=False,
        capture_executed=False, production_switched=False, retry_authorized=False,
        source_capability_issued=False, dependency_install_executed=False)
    manifest_ref = _write_new(destination / MANIFEST, previous.canonical(manifest))
    try:
        verified = verify(destination, manifest_ref["sha256"])
        # A late source/tool mutation during bundle verification fails creation.
        need(inventory(root) == selected, "SOURCE_INVENTORY_CHANGED")
        for name, path in selected.items():
            need(read(path)[1] == source_refs[name], "SOURCE_CHANGED_DURING_BUILD")
        need(_tools() == tool_refs, "BUILD_TOOL_CHANGED")
        need(_directory_identity(destination) == destination_identity, "BUNDLE_ROOT_CHANGED")
        need(_directory_identity(root) == source_identity, "SOURCE_ROOT_CHANGED")
    except BaseException:
        _revoke_manifest(manifest_ref)
        raise
    return dict(status="CREATED_AND_VERIFIED_NOT_EXECUTED", manifest=manifest_ref, verification=verified)


def verify(destination, manifest_sha256):
    """Verify the pinned copy without re-opening its historical source_root."""
    destination = _absolute(destination)
    destination_identity = _directory_identity(destination)
    tool_refs = _tools()
    raw, manifest_ref = read(destination / MANIFEST)
    need(type(manifest_sha256) is str and sha(raw) == manifest_sha256, "EXPLICIT_MANIFEST_SHA256_MISMATCH")
    manifest = previous.strict_json(raw)
    need(manifest["schema_version"] == VERSION and manifest["bundle_root"] == str(destination), "BUNDLE_IDENTITY_MISMATCH")
    need(manifest["tools"] == _tool_hashes(tool_refs), "BUILD_TOOL_IDENTITY_MISMATCH")
    need(manifest["contract"] == patches.FACTOR_CHECKPOINT_CONTRACT
         and manifest["source_reference_scope"] == "CREATION_TIME_SOURCE_GUARD_ONLY_NOT_LIVE_DEPENDENCY"
         and all(manifest[name] is False for name in ("source_changed_during_creation",
             "original_physical_owner_restored", "capture_executed", "production_switched",
             "retry_authorized", "source_capability_issued", "dependency_install_executed")), "BUNDLE_AUTHORITY_SCOPE_MISMATCH")
    sources, files = manifest["sources"], manifest["files"]
    need(type(sources) is dict and type(files) is dict and len(files) <= MAX_FILES, "BOUNDED_BUNDLE_MAPPING_REQUIRED")
    for name in set(sources) | set(files):
        previous.relative_name(name)
    evidence = {name for pair in PROVENANCE.values() for name in pair}
    need(set(files) == set(sources) | evidence and not set(sources) & evidence, "BUNDLE_FILE_SET_MISMATCH")
    need({WORKER, PARENT, *FROZEN, *PUBLIC_RESOURCES, "THIRD_PARTY_NOTICES.md",
          "stockfish_core/exchange_calendar_2026.json", "stockfish_core/provider_classification.json"}
         <= set(sources), "SOURCE_CLOSURE_MISSING")
    need(all(name in PUBLIC_RESOURCES or name == "THIRD_PARTY_NOTICES.md"
             or name.startswith("stockfish_core/") and Path(name).suffix in (".py", ".json")
             for name in sources), "UNREGISTERED_PUBLIC_SOURCE")
    actual_files, actual_dirs = _tree(destination)
    expected_files = set(files) | {MANIFEST}
    expected_dirs = {parent.as_posix() for name in expected_files for parent in Path(name).parents
                     if parent != Path(".")}
    need(actual_files == expected_files and actual_dirs == expected_dirs, "UNREGISTERED_OR_MISSING_BUNDLE_FILE")
    contents, total = {}, 0
    for name, expected in sorted(files.items()):
        need(expected["path"] == str(destination / name), "BUNDLE_FILE_PATH_MISMATCH")
        contents[name], reference = read(destination / name)
        total += len(contents[name])
        need(total <= MAX_SOURCE_BYTES, "SOURCE_BUNDLE_TOO_LARGE")
        need(reference == expected, "BUNDLE_REFERENCE_CHANGED")
        if name in sources and name not in PATCHED:
            need(reference["sha256"] == sources[name]["sha256"]
                 and reference["bytes"] == sources[name]["bytes"], "UNAUTHORIZED_SOURCE_CHANGE")
        if name in FROZEN:
            need(reference["sha256"] == FROZEN[name], "FROZEN_DEPENDENCY_MISMATCH")
    need(manifest["source_bytes"] == sum(value["bytes"] for value in sources.values()), "SOURCE_BYTE_COUNT_CHANGED")
    for name, (expected, transform) in PATCHED.items():
        preimage, patch = PROVENANCE[name]
        need(sha(contents[preimage]) == expected and sources[name]["sha256"] == expected
             and sources[name]["bytes"] == len(contents[preimage]), "PRODUCER_ORIGIN_PIN_MISMATCH")
        changed, diff = transform(contents[preimage])
        need(contents[name] == changed and contents[patch] == diff, "PRODUCER_PATCH_BYTES_DIFFER")
    need(all(files["scripts/" + name]["sha256"] == reference["sha256"]
             and files["scripts/" + name]["bytes"] == reference["bytes"]
             for name, reference in tool_refs.items()), "BUNDLED_BUILD_TOOL_BYTES_DIFFER")
    _script_closure(contents)
    need(manifest["frozen_hashes"] == FROZEN and manifest["original_workflow_scripts"] == sorted(SCRIPT_NAMES)
         and manifest["original_producer_hashes"] == {name: item[0] for name, item in PATCHED.items()}
         and manifest["producer_hashes"] == {Path(name).name: files[name]["sha256"] for name in
             (WORKER, PARENT, "stockfish_core/yaogu/provider_budget.py")}, "SOURCE_DECLARATION_CHANGED")
    # Full terminal reads; metadata-only checks cannot catch same-size mutations.
    for name, expected in files.items():
        need(read(destination / name)[1] == expected, "BUNDLE_TERMINAL_REFERENCE_CHANGED")
    need(_tree(destination) == (actual_files, actual_dirs) and read(destination / MANIFEST)[1] == manifest_ref,
         "BUNDLE_TERMINAL_CHANGED")
    need(_tools() == tool_refs, "BUILD_TOOL_CHANGED")
    need(_directory_identity(destination) == destination_identity, "BUNDLE_ROOT_CHANGED")
    return dict(status="VERIFIED_NOT_EXECUTED", schema_version=VERSION,
        manifest_sha256=manifest_ref["sha256"], files=len(files), producer_hashes=manifest["producer_hashes"],
        original_workflow_scripts=manifest["original_workflow_scripts"], snapshot_contents_unchanged=True,
        source_reference_scope=manifest["source_reference_scope"], current_original_source_revalidated=False,
        original_physical_owner_restored=False, capture_executed=False, retry_authorized=False,
        source_capability_issued=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    creation = commands.add_parser("create")
    creation.add_argument("--source-root", type=Path, required=True)
    creation.add_argument("--destination", type=Path, required=True)
    verification = commands.add_parser("verify")
    verification.add_argument("--destination", type=Path, required=True)
    verification.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        value = create(args.source_root, args.destination) if args.action == "create" else verify(
            args.destination, args.manifest_sha256)
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps(dict(status="FAILED", reason="SOURCE_BUNDLE_VALIDATION_FAILED",
            capture_executed=False, retry_authorized=False)))
        return 4
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
