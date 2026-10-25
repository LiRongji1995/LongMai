"""Read-only storage inventory and conservative admission for the Codex entry.

Only directory handles and metadata are read. No market data, SQLite connection,
cloud file content, cleanup, compression, or source mutation is performed here.
An inventory is an observation, not a filesystem snapshot or a reclaim estimate.
"""
from __future__ import annotations

from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time


ROLES = frozenset({"protected", "runtime", "cache", "output"})
MAX_ROOTS = 128
MAX_ISSUE_BYTES = 256 * 1024
MAX_ISSUE_PATH_CHARS = 1024
_ROOT_KEYS = frozenset({"name", "path", "role"})
_COUNT_KEYS = ("entries", "regular_files", "directories", "symlinks", "special_files",
               "cloud_placeholders", "duplicate_inode_entries")


class CapacityError(ValueError):
    """Invalid configuration; messages are controlled codes, not file content."""


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise CapacityError("INVALID_" + name.upper())
    return value


def validate_roots(roots):
    """Validate explicit, disjoint absolute paths, without resolving symlinks."""
    if type(roots) is not list or not roots:
        raise CapacityError("NONEMPTY_ROOTS_LIST_REQUIRED")
    if len(roots) > MAX_ROOTS:
        raise CapacityError("STORAGE_ROOT_COUNT_LIMIT_EXCEEDED")
    result, names = [], set()
    for row in roots:
        if type(row) is not dict or set(row) != _ROOT_KEYS:
            raise CapacityError("INVALID_ROOT_FIELDS")
        name, path, role = row["name"], row["path"], row["role"]
        if type(name) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
            raise CapacityError("INVALID_ROOT_NAME")
        if name in names:
            raise CapacityError("DUPLICATE_ROOT_NAME")
        if type(role) is not str or role not in ROLES:
            raise CapacityError("INVALID_ROOT_ROLE")
        if (type(path) is not str or not path or "\x00" in path or not os.path.isabs(path)
                or ".." in Path(path).parts):
            raise CapacityError("ABSOLUTE_ROOT_PATH_REQUIRED")
        path = os.path.normpath(path)
        normalized = os.path.normcase(path)
        for other in result:
            other_path = os.path.normcase(other["path"])
            try:
                overlap = os.path.commonpath((normalized, other_path)) in {normalized, other_path}
            except ValueError:  # Different Windows drives have no common path.
                overlap = False
            if overlap:
                raise CapacityError("OVERLAPPING_STORAGE_ROOTS")
        result.append({"name": name, "path": path, "role": role})
        names.add(name)
    return result


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, value.st_nlink,
            getattr(value, "st_blocks", None), getattr(value, "st_flags", 0))


def _cloud_placeholder(name, value):
    # macOS SF_DATALESS and Windows offline/recall flags are metadata only.
    return (name.endswith(".icloud")
            or bool(getattr(value, "st_flags", 0) & 0x40000000)
            or bool(getattr(value, "st_file_attributes", 0) & (0x1000 | 0x40000 | 0x400000)))


def _metadata(name, directory_fd):
    return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)


def _new_totals():
    return {**{key: 0 for key in _COUNT_KEYS}, "logical_bytes": 0,
            "unique_logical_bytes": 0, "allocated_bytes": 0,
            "allocation_metadata_complete": True}


def _error_code(exc):
    if isinstance(exc, FileNotFoundError):
        return "PATH_MISSING_DURING_SCAN"
    if isinstance(exc, PermissionError):
        return "METADATA_PERMISSION_DENIED"
    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
        return "SYMLINK_OR_DIRECTORY_CHANGED"
    return "METADATA_IO_ERROR"


def storage_report(roots, *, max_entries=1_000_000, max_depth=128, disk_usage_fn=None):
    """Inventory explicit roots, with global inode deduplication and bounded work.

    Roots are [{"name": str, "path": absolute str, "role": protected/runtime/cache/output}].
    Runtime denotes shared mixed evidence/state; no role grants deletion rights.
    ``logical_bytes`` counts regular-file paths; ``unique_logical_bytes`` and
    ``allocated_bytes`` count each (device, inode) once across all roots. Per-root
    unique counts belong to the first encountered root; they sum to the totals.
    Allocation excludes directory/filesystem metadata and is not reclaimable
    space: APFS/reflink clones, compression, snapshots, and open deleted files
    cannot be quantified from these metadata. No content hash is calculated.

    POSIX no-follow directory handles prevent traversal through symlinks, including
    a directory replaced during the scan. Unsupported platforms fail closed.
    ``disk_usage_fn(path)`` is injectable and must return total/used/free integers.
    """
    roots = validate_roots(roots)
    _integer(max_entries, "max_entries", 1)
    _integer(max_depth, "max_depth", 1)
    if max_depth > 256:
        raise CapacityError("INVALID_MAX_DEPTH")
    usage_fn = shutil.disk_usage if disk_usage_fn is None else disk_usage_fn
    if not callable(usage_fn):
        raise CapacityError("INVALID_DISK_USAGE_FUNCTION")
    started = time.monotonic()
    report = {"schema": "CODEX_STORAGE_REPORT_V1", "observed_at": datetime.now(timezone.utc).isoformat(),
              "read_only": True, "file_contents_read": False, "symlinks_followed": False,
              "snapshot_consistent": False, "scan_complete": True, "roots": [],
              "totals": _new_totals(), "issues": [], "omitted_issue_count": 0,
              "accounting_basis": "REGULAR_FILES; UNIQUE_DEVICE_INODE; ALLOCATION_NOT_RECLAIMABLE",
              "limits": {"max_entries": max_entries, "max_depth": max_depth,
                         "max_roots": MAX_ROOTS, "max_issue_serialized_bytes": MAX_ISSUE_BYTES,
                         "max_issue_path_chars": MAX_ISSUE_PATH_CHARS}}
    inodes, directory_inodes = {}, set()
    total = report["totals"]
    issue_bytes = 2  # Compact JSON array brackets.

    def issue(root, code, path):
        nonlocal issue_bytes
        root["scan_complete"] = report["scan_complete"] = False
        # Unusual filesystem byte names remain printable diagnostics, never
        # unbounded copies of deeply nested paths in durable operation records.
        diagnostic = str(path).encode("utf-8", errors="backslashreplace").decode("utf-8")
        item = {"root": root["name"], "code": code,
                "path": diagnostic[:MAX_ISSUE_PATH_CHARS]}
        if len(diagnostic) > MAX_ISSUE_PATH_CHARS:
            item.update(path_truncated=True, original_path_chars=len(diagnostic))
        serialized_bytes = len(json.dumps(item, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
        projected_bytes = issue_bytes + serialized_bytes + bool(report["issues"])
        if len(report["issues"]) < 1000 and projected_bytes <= MAX_ISSUE_BYTES:
            report["issues"].append(item)
            issue_bytes = projected_bytes
        else:
            report["omitted_issue_count"] += 1

    def add(root, key, amount=1):
        root["totals"][key] += amount
        total[key] += amount

    def visit(root, parent_fd, name, path, depth):
        if total["entries"] >= max_entries:
            issue(root, "SCAN_ENTRY_LIMIT_REACHED", path)
            return
        add(root, "entries")
        before = _metadata(name, parent_fd)
        cloud = _cloud_placeholder(name, before)
        if cloud:
            add(root, "cloud_placeholders")
            issue(root, "CLOUD_PLACEHOLDER_NOT_HYDRATED", path)
        if stat.S_ISLNK(before.st_mode):
            add(root, "symlinks")
            return  # Link destinations are deliberately outside inventory scope.
        if stat.S_ISREG(before.st_mode):
            add(root, "regular_files")
            add(root, "logical_bytes", before.st_size)
            inode = (before.st_dev, before.st_ino)
            if inode in inodes:
                add(root, "duplicate_inode_entries")
                if inodes[inode] != _identity(before):
                    issue(root, "INODE_CHANGED_DURING_SCAN", path)
            else:
                inodes[inode] = _identity(before)
                add(root, "unique_logical_bytes", before.st_size)
                blocks = getattr(before, "st_blocks", None)
                if type(blocks) is int and blocks >= 0:
                    add(root, "allocated_bytes", blocks * 512)
                else:
                    root["totals"]["allocation_metadata_complete"] = False
                    total["allocation_metadata_complete"] = False
            if _identity(_metadata(name, parent_fd)) != _identity(before):
                issue(root, "FILE_CHANGED_DURING_SCAN", path)
            return
        if not stat.S_ISDIR(before.st_mode):
            add(root, "special_files")
            issue(root, "SPECIAL_FILE_NOT_ACCOUNTED", path)
            return
        add(root, "directories")
        if cloud:
            return
        if depth >= max_depth:
            issue(root, "SCAN_DEPTH_LIMIT_REACHED", path)
            return
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            opened = os.fstat(descriptor)
            if _identity(opened) != _identity(before):
                issue(root, "DIRECTORY_CHANGED_DURING_SCAN", path)
                return
            inode = (opened.st_dev, opened.st_ino)
            if inode in directory_inodes:
                issue(root, "DIRECTORY_ALIAS_NOT_TRAVERSED", path)
                return
            directory_inodes.add(inode)
            with os.scandir(descriptor) as children:
                for child in children:
                    if total["entries"] >= max_entries:
                        issue(root, "SCAN_ENTRY_LIMIT_REACHED", path)
                        break
                    try:
                        visit(root, descriptor, child.name, path / child.name, depth + 1)
                    except OSError as exc:
                        issue(root, _error_code(exc), path / child.name)
            if (_identity(os.fstat(descriptor)) != _identity(before)
                    or _identity(_metadata(name, parent_fd)) != _identity(before)):
                issue(root, "DIRECTORY_CHANGED_DURING_SCAN", path)
        finally:
            os.close(descriptor)

    def scan(root):
        # Walk ancestors by handles as well: O_NOFOLLOW on the final root alone
        # would still follow a symlink in any of its parent components.
        path = Path(root["path"])
        descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in path.parts[1:-1]:
                ancestor = _metadata(part, descriptor)
                if _cloud_placeholder(part, ancestor):
                    issue(root, "CLOUD_ANCESTOR_NOT_HYDRATED", path)
                    return
                next_descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            root_name = path.name if path.name else "."
            before = _metadata(root_name, descriptor)
            if stat.S_ISLNK(before.st_mode):
                issue(root, "SYMLINK_STORAGE_ROOT_REJECTED", path)
                return
            visit(root, descriptor, root_name, path, 0)
            root["device"] = before.st_dev
            if _cloud_placeholder(root_name, before):
                return
            try:
                usage = usage_fn(str(path))
                values = {key: getattr(usage, key) for key in ("total", "used", "free")}
                if any(type(value) is not int or value < 0 for value in values.values()):
                    raise ValueError("invalid disk metadata")
                if values["free"] > values["total"] or values["used"] > values["total"]:
                    raise ValueError("invalid disk metadata")
                root["filesystem"] = {key + "_bytes": value for key, value in values.items()}
            except (OSError, ValueError, TypeError, AttributeError):
                issue(root, "FILESYSTEM_CAPACITY_UNAVAILABLE", path)
            if _identity(_metadata(root_name, descriptor)) != _identity(before):
                issue(root, "ROOT_CHANGED_DURING_SCAN", path)
        finally:
            os.close(descriptor)

    supported = (os.name == "posix" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
                 and os.stat in os.supports_dir_fd and os.scandir in os.supports_fd)
    for config in roots:
        root = {**config, "scan_complete": True, "totals": _new_totals(), "filesystem": None}
        report["roots"].append(root)
        if not supported:
            issue(root, "SAFE_DIRECTORY_METADATA_SCAN_UNAVAILABLE", root["path"])
        else:
            try:
                scan(root)
            except OSError as exc:
                issue(root, _error_code(exc), root["path"])
        if not root["totals"]["allocation_metadata_complete"]:
            root["totals"]["allocated_bytes"] = None
    if not total["allocation_metadata_complete"]:
        total["allocated_bytes"] = None
    report["status"] = "COMPLETE" if report["scan_complete"] else "PARTIAL"
    report["issue_serialized_bytes"] = issue_bytes
    report["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return report


def preflight(roots, *, expected_growth_bytes, min_free_bytes, max_managed_bytes,
              target_root=None, max_entries=1_000_000, max_depth=128, disk_usage_fn=None):
    """Reject incomplete inventories or growth that would exceed either budget.

    This is admission advice, not a disk reservation or a runtime write limit.
    The caller must hold its shared operation lock and enforce its write budget.
    A protected root is never an admissible destination.
    """
    roots = validate_roots(roots)
    for name, value in (("expected_growth_bytes", expected_growth_bytes),
                        ("min_free_bytes", min_free_bytes), ("max_managed_bytes", max_managed_bytes)):
        _integer(value, name)
    if target_root is None and len(roots) == 1:
        target_root = roots[0]["name"]
    if type(target_root) is not str or target_root not in {row["name"] for row in roots}:
        raise CapacityError("EXPLICIT_TARGET_ROOT_REQUIRED")
    report = storage_report(roots, max_entries=max_entries, max_depth=max_depth, disk_usage_fn=disk_usage_fn)
    target = next(root for root in report["roots"] if root["name"] == target_root)
    reasons = []
    if not report["scan_complete"]:
        reasons.append("STORAGE_INVENTORY_INCOMPLETE")
    if target["role"] == "protected":
        reasons.append("TARGET_ROOT_PROTECTED")
    projected = report["totals"]["unique_logical_bytes"] + expected_growth_bytes
    if projected > max_managed_bytes:
        reasons.append("MANAGED_LOGICAL_BUDGET_EXCEEDED")
    if target["filesystem"] is None:
        reasons.append("TARGET_FREE_SPACE_UNKNOWN")
        free_after = None
    else:
        free_after = target["filesystem"]["free_bytes"] - expected_growth_bytes
        if free_after < min_free_bytes:
            reasons.append("MINIMUM_FREE_SPACE_WOULD_BE_BREACHED")
    return {"schema": "CODEX_CAPACITY_PREFLIGHT_V1", "allowed": not reasons,
            "reason_codes": reasons, "target_root": target_root,
            "expected_growth_bytes": expected_growth_bytes, "min_free_bytes": min_free_bytes,
            "max_managed_bytes": max_managed_bytes, "projected_unique_logical_bytes": projected,
            "projected_free_bytes": free_after, "reservation_created": False, "report": report}
