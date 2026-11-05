"""Portable local file integrity, independent of filesystem object identity.

Legacy physical fingerprints remain readable audit metadata.  A reference is
trusted only for its complete bytes, never because its inode or timestamps match.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import stat

_PHYSICAL = frozenset(("st_dev", "st_ino", "st_mode", "st_mtime_ns", "st_ctime_ns",
                      "st_flags", "device", "inode", "mode", "mtime_ns", "ctime_ns"))


def _size(value):
    if "bytes" in value:
        return value["bytes"]
    if "st_size" in value:
        return value["st_size"]
    if "size" in value:
        return value["size"]
    fp = value.get("fp", value.get("fingerprint"))
    if isinstance(fp, dict):
        return fp.get("st_size")
    if isinstance(fp, (list, tuple)) and len(fp) == 7:
        return fp[3]
    return None


def _content(value):
    if isinstance(value, dict):
        # Only a SHA-bearing file record permits dropping physical metadata.
        # An arbitrary fingerprint, request identity, or business row does not.
        is_file = (isinstance(value.get("sha256"), str)
                   and (isinstance(value.get("path"), str) or type(value.get("bytes")) is int))
        result = {key: _content(item) for key, item in value.items()
                  if not (is_file and (key in ("fp", "fingerprint") or key in _PHYSICAL))}
        if is_file and _size(value) is not None:
            result.pop("st_size", None)
            result.pop("size", None)
            result["bytes"] = _size(value)
        return result
    if isinstance(value, (list, tuple)):
        return [_content(item) for item in value]
    return value


def content_equal(actual, expected):
    """Compare nested records, ignoring only SHA-bearing file stat metadata."""
    return _content(actual) == _content(expected)


def content_reference_equal(actual, expected):
    """Compare file locators and complete content, accepting legacy ref4."""
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    for value in (actual, expected):
        if (not isinstance(value.get("path"), str)
                or not isinstance(value.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None):
            return False
        size = _size(value)
        if type(size) is not int or size < 0:
            return False
    return (actual["path"], actual["sha256"], _size(actual)) == (
        expected["path"], expected["sha256"], _size(expected))


def content_fingerprint(path, read_meter=None):
    """Hash the actual whole file; stat is used only for file type/size facts."""
    path = Path(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("CONTENT_REGULAR_FILE_REQUIRED")
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            if read_meter is not None:
                read_meter(len(block))
            digest.update(block)
            size += len(block)
    return {"sha256": digest.hexdigest(), "bytes": size}


file_digest = content_fingerprint


def verify_content_reference(reference, read_meter=None):
    actual = {"path": reference["path"], **content_fingerprint(reference["path"], read_meter)}
    if not content_reference_equal(actual, reference):
        raise ValueError("FILE_CONTENT_REFERENCE_CHANGED")
    return actual


def verify_file_reference(path, reference, read_meter=None):
    actual = {"path": str(path), **content_fingerprint(path, read_meter)}
    if not content_reference_equal(actual, reference):
        raise ValueError("FILE_CONTENT_REFERENCE_CHANGED")
    return actual
