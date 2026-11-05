"""Thin, persistent-chat adapter for the existing standalone premarket workflow.

Only the caller's coordinator owns request records and capacity admission. This
module never creates a business database, copies an archive, changes a strategy,
or retries a failed/uncertain execution. Saved results remain archived results;
checking their bytes does not restore a source capability.
"""
from __future__ import annotations

from stockfish_core.file_integrity import (
    content_equal, content_reference_equal, content_fingerprint, verify_content_reference,
)

import hashlib
import json
import os
from pathlib import Path
import re
import stat

VERSION = "CODEX_PREMARKET_ADAPTER_V1"
MAX_CONTROL_BYTES = 16 * 1024 * 1024
MAX_CONSOLE_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_SOURCE_FILES = 4096
VERIFY_TIMEOUT_SECONDS = 300
FP_KEYS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_flags")
SUCCESS = {"COMPLETE": 0, "COMPLETED_WITH_UNKNOWNS": 2}
CORE_FILES = {"canonical-run.json.gz", "input.json.gz", "observation.json", "observation.csv",
              "all.csv", "focus.csv", "unselected.csv", "manifest.json"}
# Explicit dependencies of the original R1/R2 and theme source identities.
# Keep this separate from _code_refs; never enumerate private config files.
STRATEGY_SUPPORT = (
    "scripts/codex_daily_v2.py", "config/r05_strategy_experiments.yaml",
    "config/r05_completion_requirements.yaml", "config/r05_source_lock.json",
    "config/yaogu_v1.defaults.yaml", "config/short_horizon_strength_r1.yaml",
    "config/short_horizon_strength_r2.yaml", "config/theme_capital_trend_v1.yaml",
    "config/stage_aware_momentum_v2.yaml", "config/theme_evolution_r1.yaml",
    "docs/SHORT_HORIZON_STRENGTH_R1.md", "docs/SHORT_HORIZON_STRENGTH_R2.md",
    "docs/THEME_EVOLUTION_R1.md",
)


class PremarketError(ValueError):
    """Credential-free, controlled error code suitable for the chat entry."""


def _need(condition, code):
    if not condition:
        raise PremarketError(code)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _need(key not in result, "PREMARKET_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(PremarketError("PREMARKET_NONFINITE_JSON")))
    except (UnicodeError, json.JSONDecodeError):
        raise PremarketError("PREMARKET_INVALID_CONTROL_JSON") from None


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _fp(path):
    value = Path(path).lstat()
    _need(stat.S_ISREG(value.st_mode), "PREMARKET_REGULAR_FILE_REQUIRED")
    _need(not getattr(value, "st_flags", 0) & 0x40000000 and
          (not value.st_size or getattr(value, "st_blocks", 1) > 0), "PREMARKET_RESIDENT_FILE_REQUIRED")
    return {key: int(getattr(value, key, 0)) for key in FP_KEYS}


def _reference(path, *, maximum=None):
    path = Path(path)
    _need(path.is_absolute() and str(path.resolve(strict=True)) == str(path), "PREMARKET_CANONICAL_FILE_REQUIRED")
    before = _fp(path)
    if maximum is not None:
        _need(before["st_size"] <= maximum, "PREMARKET_FILE_SIZE_LIMIT")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    _need(_fp(path) == before, "PREMARKET_FILE_CHANGED_DURING_READ")
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": before["st_size"], "fp": before}


def _control(path):
    reference = _reference(path, maximum=MAX_CONTROL_BYTES)
    value = _json(Path(path).read_bytes())
    _need(_fp(path) == reference["fp"], "PREMARKET_CONTROL_CHANGED_DURING_READ")
    return value, reference


def _directory(path):
    result = Path(path).expanduser().resolve(strict=True)
    _need(result.is_dir(), "PREMARKET_EXISTING_DIRECTORY_REQUIRED")
    return result


def _prepared(profile):
    _need(isinstance(profile, dict), "PREMARKET_PROFILE_REQUIRED")
    try:
        source = _directory(profile["source_root"])
        state = _directory(profile["state_root"])
        configured_python = Path(profile["python"]).expanduser().absolute()
        python = configured_python.resolve(strict=True)
        config = Path(profile["config"]).expanduser().resolve(strict=True)
        _need(os.access(configured_python, os.X_OK), "PREMARKET_EXECUTABLE_PYTHON_REQUIRED")
        value, _ = _control(config)
        _need(isinstance(value, dict) and isinstance(value.get("state_root"), str), "PREMARKET_EXPLICIT_STATE_ROOT_REQUIRED")
        _need(Path(value["state_root"]).expanduser().is_absolute(), "PREMARKET_ABSOLUTE_CONFIG_STATE_REQUIRED")
        _need(Path(value["state_root"]).expanduser().resolve(strict=True) == state,
              "PREMARKET_CONFIG_STATE_ROOT_MISMATCH")
        _need((source / "stockfish_core").is_dir(), "PREMARKET_CORE_SOURCE_REQUIRED")
        return {"source": source, "state": state, "python": configured_python,
                "python_real": python, "config": config}
    except (KeyError, OSError, TypeError, RuntimeError):
        raise PremarketError("PREMARKET_PROFILE_PATH_UNAVAILABLE") from None


def _snapshot(prepared):
    source = prepared["source"]
    # Exactly the original workflow's _code_refs closure. Never recursively
    # inspect private configuration, .env files, runtime data or other projects.
    paths = set((source / "stockfish_core").rglob("*.py"))
    paths.update(source / name for name in ("scripts/codex_daily_v2.py",))
    _need(len(paths) <= MAX_SOURCE_FILES, "PREMARKET_SOURCE_FILE_COUNT_LIMIT")
    references, total = [], 0
    for path in sorted(paths):
        ref = _reference(path, maximum=MAX_SOURCE_BYTES - total)
        total += ref["bytes"]
        references.append(ref)
    _need(prepared["python"].resolve(strict=True) == prepared["python_real"], "PREMARKET_PYTHON_TARGET_CHANGED")
    return {"source_references": references,
            "strategy_support": [_reference(source / name, maximum=MAX_CONTROL_BYTES)
                                 for name in STRATEGY_SUPPORT],
            "configuration": _reference(prepared["config"], maximum=MAX_CONTROL_BYTES),
            "python": _reference(prepared["python_real"], maximum=MAX_SOURCE_BYTES)}






def _inside_job(reference, job):
    _need(isinstance(reference, dict) and set(reference) == {"path", "sha256", "bytes", "fp"},
          "PREMARKET_INVALID_ARCHIVE_REFERENCE")
    path = Path(reference["path"])
    _need(path.is_absolute() and str(path.resolve(strict=True)) == str(path), "PREMARKET_ARCHIVE_PATH_CHANGED")
    try:
        path.relative_to(job)
    except ValueError:
        raise PremarketError("PREMARKET_REFERENCE_OUTSIDE_JOB") from None
    _need(content_reference_equal(_reference(path, maximum=max(reference["bytes"], 1)), reference),
          "PREMARKET_ARCHIVE_CONTENT_CHANGED")


def _job_path(prepared, value):
    path = Path(value)
    _need(path.is_absolute() and str(path.resolve(strict=True)) == str(path), "PREMARKET_JOB_CANONICAL_PATH_REQUIRED")
    _need(path.is_dir() and path.parent == prepared["state"] / "jobs", "PREMARKET_JOB_OUTSIDE_REGISTERED_STATE")
    return path


# This script imports the selected original implementation in its selected
# interpreter. It does not import a different installed core into this process.
