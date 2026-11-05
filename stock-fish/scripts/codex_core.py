"""Bounded source snapshots and byte readers used by the admission planner."""
from __future__ import annotations

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




def _control(path, maximum=MAX_MANIFEST):
    reference, raw = _read_file(path, maximum, retain=True)
    return premarket._json(raw), reference






def _snapshot(profile):
    source, python = Path(profile["source_root"]), Path(profile["python"])
    _need(source == ROOT, "CORE_REGISTERED_SOURCE_MUST_MATCH_EXECUTING_MODULE")
    _need(workspace.interpreter_path(sys.executable) == python, "CORE_REGISTERED_INTERPRETER_REQUIRED")
    configuration, config_ref = _control(Path(profile["config"]), 65536)
    _need(configuration.get("state_root") == profile["state_root"], "CORE_EXPLICIT_CONFIG_STATE_ROOT_REQUIRED")
    paths = {*source.joinpath("stockfish_core").rglob("*.py"),
             *(source / name for name in ("scripts/codex_daily_v2.py",))}
    _need(len(paths) <= premarket.MAX_SOURCE_FILES, "CORE_SOURCE_FILE_COUNT_LIMIT")
    snapshot = {"source_references": [_reference(path) for path in sorted(paths)],
                "strategy_support": [_reference(source / name) for name in premarket.STRATEGY_SUPPORT],
                "configuration": config_ref, "python": _reference(python.resolve(strict=True))}
    snapshot["coordinator_sources"] = [_reference(Path(module.__file__).resolve())
                                       for module in (sys.modules[__name__], workspace, premarket)]
    return snapshot
