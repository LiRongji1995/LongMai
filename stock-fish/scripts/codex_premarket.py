"""Thin, persistent-chat adapter for the existing standalone premarket workflow.

Only the caller's coordinator owns request records and capacity admission. This
module never creates a business database, copies an archive, changes a strategy,
or retries a failed/uncertain execution. Saved results remain archived results;
checking their bytes does not restore a source capability.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import threading
import time

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
    "scripts/codex_research.py", "config/r05_strategy_experiments.yaml",
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
    paths.update(source / name for name in ("scripts/stockfish.py", "premarket", "premarket.ps1"))
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


def _same_snapshot(prepared, expected):
    _need(_snapshot(prepared) == expected, "PREMARKET_IMPLEMENTATION_OR_CONFIG_CHANGED")


def _run(command, prepared, *, timeout=None):
    """Drain console output continuously, retain at most 1 MiB, never log it.

    The existing supervisor owns its separately-sessioned worker and cleanup.
    Its configured execution deadline remains authoritative. Verification calls
    have no workers and may additionally use the bounded wrapper timeout.
    """
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.Popen(command, cwd=str(prepared["source"]), env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=os.name == "posix")
    captured = bytearray()
    counts = {"bytes": 0, "read_error": False}

    def drain():
        try:
            while True:
                block = process.stdout.read(65536)
                if not block:
                    break
                counts["bytes"] += len(block)
                room = MAX_CONSOLE_BYTES - len(captured)
                if room > 0:
                    captured.extend(block[:room])
        except OSError:
            counts["read_error"] = True

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=5)
        raise PremarketError("PREMARKET_ARCHIVE_VERIFICATION_TIMEOUT") from None
    except BaseException:
        # SIGINT lets the original supervisor run its own _stop_owned cleanup.
        if process.poll() is None:
            try:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                pass  # The coordinator retains an uncertain, non-retryable claim.
        raise
    finally:
        reader.join(timeout=5)
        if not reader.is_alive():
            process.stdout.close()
    _need(not reader.is_alive() and not counts["read_error"], "PREMARKET_CONSOLE_STATE_UNKNOWN")
    _need(counts["bytes"] <= MAX_CONSOLE_BYTES, "PREMARKET_CONSOLE_LIMIT_EXCEEDED")
    return process.returncode, bytes(captured), counts["bytes"]


def _inside_job(reference, job):
    _need(isinstance(reference, dict) and set(reference) == {"path", "sha256", "bytes", "fp"},
          "PREMARKET_INVALID_ARCHIVE_REFERENCE")
    path = Path(reference["path"])
    _need(path.is_absolute() and str(path.resolve(strict=True)) == str(path), "PREMARKET_ARCHIVE_PATH_CHANGED")
    try:
        path.relative_to(job)
    except ValueError:
        raise PremarketError("PREMARKET_REFERENCE_OUTSIDE_JOB") from None
    _need(_fp(path) == reference["fp"], "PREMARKET_ARCHIVE_PHYSICAL_IDENTITY_CHANGED")


def _job_path(prepared, value):
    path = Path(value)
    _need(path.is_absolute() and str(path.resolve(strict=True)) == str(path), "PREMARKET_JOB_CANONICAL_PATH_REQUIRED")
    _need(path.is_dir() and path.parent == prepared["state"] / "jobs", "PREMARKET_JOB_OUTSIDE_REGISTERED_STATE")
    return path


# This script imports the selected original implementation in its selected
# interpreter. It does not import a different installed core into this process.
_GATE_SCRIPT = r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from stockfish_core.yaogu import premarket_workflow_v1 as w
job=Path(sys.argv[2])
final=w._read_control(job/'final-control.json')
completion=w._read_control(final['completion']['path'])
actual_config=w._read_control(final['configuration']['path'])
if actual_config != w.load_config(sys.argv[3]):
    raise ValueError('PREMARKET_EXECUTED_CONFIG_MISMATCH')
if w._read_control(final['code_registry']['path']) != w._code_refs():
    raise ValueError('PREMARKET_EXECUTED_CODE_MISMATCH')
gate=w._latest_gate(completion['calendar_rows'],sys.argv[4],sys.argv[5])
print(json.dumps(gate,sort_keys=True))
'''


def _validate_archive(prepared, snapshot, receipt, observation_date, target_session):
    _same_snapshot(prepared, snapshot)
    _need(receipt.get("schema_version") == VERSION, "PREMARKET_RECEIPT_VERSION_MISMATCH")
    _need(receipt.get("observation_date") == observation_date and receipt.get("target_session") == target_session,
          "PREMARKET_RECEIPT_SESSION_MISMATCH")
    job = _job_path(prepared, receipt["job"])
    final, final_ref = _control(job / "final-control.json")
    _need(final_ref == receipt["final"], "PREMARKET_FINAL_CONTROL_CHANGED")
    _need(receipt.get("status") == final.get("status") and
          receipt.get("execution_exit_code") == final.get("external_worker_exit_code") and
          receipt.get("source_authority_restored") is False and
          receipt.get("result_role") == "ARCHIVED_REUSE_NOT_NEW_CAPTURE", "PREMARKET_RECEIPT_CLAIMS_CHANGED")
    _need(final.get("schema_version") == "STANDALONE_PREMARKET_WORKFLOW_V1" and
          final.get("status") in SUCCESS and final.get("core_verified") is True and
          final.get("external_worker_exit_code") == SUCCESS[final["status"]] and
          final.get("external_worker_reaped") is True and final.get("implementation_epoch_verified") is True and
          final.get("source_terminal_binding_verified") is True and final.get("job") == str(job),
          "PREMARKET_SUCCESSFUL_SEALED_CORE_REQUIRED")
    for name in ("completion", "configuration", "code_registry"):
        _inside_job(final[name], job)
    archive_references = [final[name] for name in ("completion", "configuration", "code_registry")]
    completion, completion_ref = _control(Path(final["completion"]["path"]))
    _need(completion_ref == final["completion"] == receipt["completion"], "PREMARKET_COMPLETION_CHANGED")
    _need(completion.get("schema_version") == final["schema_version"] and
          completion.get("status") == final["status"] and completion.get("core_verified") is True and
          completion.get("observation_date") == observation_date and completion.get("target_session") == target_session,
          "PREMARKET_COMPLETION_SESSION_OR_STATE_MISMATCH")
    _need(isinstance(completion.get("run_id"), str) and bool(completion["run_id"]) and
          completion["run_id"] == receipt["run_id"], "PREMARKET_RUN_ID_MISMATCH")
    _need(completion.get("strategy_id") == "SHORT_HORIZON_STRENGTH_R2" and
          receipt.get("strategy_id") == completion["strategy_id"] and
          receipt.get("theme_status") == completion.get("theme_status"), "PREMARKET_STRATEGY_OR_UNKNOWNS_CHANGED")
    _need(set(completion.get("core_artifacts") or {}) == CORE_FILES, "PREMARKET_COMPLETE_CORE_ARTIFACTS_REQUIRED")
    display = final.get("display") or {}
    _need(display == completion.get("display") and isinstance(display.get("artifacts"), dict) and
          len(display["artifacts"]) == 3, "PREMARKET_COMPLETE_DISPLAY_REQUIRED")
    _need(receipt.get("display_artifacts") == {name: ref["path"] for name, ref in display["artifacts"].items()},
          "PREMARKET_RECEIPT_DISPLAY_PATH_CHANGED")
    for role in ("core_artifacts", "theme_artifacts"):
        for reference in (completion.get(role) or {}).values():
            _inside_job(reference, job)
            archive_references.append(reference)
    for reference in display["artifacts"].values():
        _inside_job(reference, job)
        archive_references.append(reference)
    if completion.get("news") is not None:
        _inside_job(completion["news"]["metadata"], job)
        archive_references.append(completion["news"]["metadata"])
    code, raw, verify_bytes = _run([str(prepared["python"]), "-I", "-B",
        str(prepared["source"] / "scripts/stockfish.py"), "premarket-verify", str(job)],
        prepared, timeout=VERIFY_TIMEOUT_SECONDS)
    _need(code == 0, "PREMARKET_ORIGINAL_ARCHIVE_VERIFICATION_FAILED")
    verified = _json(raw)
    _need(verified.get("archived_bytes_verified") is True and verified.get("job") == str(job) and
          verified.get("status") == final["status"] and verified.get("source_authority_restored") is False,
          "PREMARKET_ORIGINAL_VERIFICATION_RESULT_MISMATCH")
    code, raw, gate_bytes = _run([str(prepared["python"]), "-I", "-B", "-c", _GATE_SCRIPT,
        str(prepared["source"]), str(job), str(prepared["config"]), observation_date, target_session],
        prepared, timeout=VERIFY_TIMEOUT_SECONDS)
    _need(code == 0, "PREMARKET_ORIGINAL_CURRENTNESS_OR_CONFIG_CHECK_FAILED")
    gate = _json(raw)
    _need(gate.get("observation_date") == observation_date and gate.get("target_session") == target_session and
          gate.get("original_calendar_used") is True, "PREMARKET_ORIGINAL_CURRENTNESS_RESULT_MISMATCH")
    _same_snapshot(prepared, snapshot)
    for reference in archive_references:
        _inside_job(reference, job)
    _need(_reference(job / "final-control.json", maximum=MAX_CONTROL_BYTES) == final_ref and
          _reference(Path(final["completion"]["path"]), maximum=MAX_CONTROL_BYTES) == completion_ref,
          "PREMARKET_ARCHIVE_CHANGED_DURING_VERIFICATION")
    return {"verified": True, "archived_bytes_verified": True, "currentness": gate,
            "source_authority_restored": False, "console_bytes": verify_bytes + gate_bytes}


def _execute_premarket(profile, *, observation_date: str, target_session: str, request_key: str, coordinator) -> dict:
    """Run once or verify the same archived result across independent chats."""
    _need(date.fromisoformat(observation_date).isoformat() == observation_date and
          date.fromisoformat(target_session).isoformat() == target_session and observation_date < target_session,
          "PREMARKET_EXPLICIT_ORDERED_SESSIONS_REQUIRED")
    _need(isinstance(request_key, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", request_key),
          "PREMARKET_REQUEST_KEY_REQUIRED")
    prepared = _prepared(profile)
    snapshot = _snapshot(prepared)
    identity = {"schema_version": VERSION, "backend": "premarket", "state_root": str(prepared["state"]),
                "source_root": str(prepared["source"]), "python_path": str(prepared["python"]),
                "configuration": snapshot["configuration"], "python": snapshot["python"],
                "source_closure_sha256": hashlib.sha256(_canonical(snapshot["source_references"])).hexdigest(),
                "strategy_support_sha256": hashlib.sha256(_canonical(snapshot["strategy_support"])).hexdigest(),
                "observation_date": observation_date, "target_session": target_session, "request_key": request_key}

    def validate(receipt):
        return _validate_archive(prepared, snapshot, receipt, observation_date, target_session)

    def action():
        _same_snapshot(prepared, snapshot)
        latest = prepared["state"] / "latest-job.json"
        before_latest = _reference(latest, maximum=MAX_CONTROL_BYTES) if latest.exists() else None
        jobs = prepared["state"] / "jobs"
        before_jobs = {item.name for item in jobs.iterdir()} if jobs.exists() else set()
        started_ns = time.time_ns()
        started = time.monotonic()
        code, raw, console_bytes = _run([str(prepared["python"]), "-I", "-B",
            str(prepared["source"] / "scripts/stockfish.py"), "premarket", "--config", str(prepared["config"]), "--quiet"], prepared)
        _same_snapshot(prepared, snapshot)
        _need(code in (0, 2), "PREMARKET_EXECUTION_FAILED_OR_UNKNOWN_NO_AUTOMATIC_RETRY")
        printed = _json(raw)
        locator, after_latest = _control(latest)
        _need(after_latest != before_latest, "PREMARKET_LATEST_JOB_NOT_UPDATED")
        job = _job_path(prepared, locator["job"])
        _need(job.name not in before_jobs and printed.get("job") == str(job), "PREMARKET_NEW_JOB_ASSOCIATION_FAILED")
        job_stat = job.stat()
        created_ns = getattr(job_stat, "st_birthtime_ns", None)
        if created_ns is None:
            created_ns = int(job_stat.st_birthtime * 1_000_000_000) if hasattr(job_stat, "st_birthtime") else job_stat.st_ctime_ns
        _need(created_ns >= started_ns, "PREMARKET_JOB_PREDATES_EXECUTION")
        final, final_ref = _control(job / "final-control.json")
        _need(locator.get("final") == final_ref and printed == final, "PREMARKET_LATEST_FINAL_BINDING_MISMATCH")
        sealed = datetime.fromisoformat(final["final_sealed_at"].replace("Z", "+00:00"))
        _need(sealed.tzinfo is not None and sealed.timestamp() * 1_000_000_000 >= started_ns,
              "PREMARKET_FINAL_PREDATES_EXECUTION")
        _need(final.get("status") in SUCCESS and code == SUCCESS[final["status"]], "PREMARKET_EXIT_STATE_MISMATCH")
        _inside_job(final["completion"], job)
        completion, completion_ref = _control(Path(final["completion"]["path"]))
        receipt = {"schema_version": VERSION, "status": final["status"], "job": str(job),
                   "run_id": completion["run_id"], "observation_date": observation_date,
                   "strategy_id": completion.get("strategy_id"), "theme_status": completion.get("theme_status"),
                   "target_session": target_session, "final": final_ref, "completion": completion_ref,
                   "display_artifacts": {name: ref["path"] for name, ref in (final.get("display") or {}).get("artifacts", {}).items()},
                   "execution_seconds": round(time.monotonic() - started, 6), "execution_exit_code": code,
                   "execution_console_bytes": console_bytes, "source_authority_restored": False,
                   "result_role": "ARCHIVED_REUSE_NOT_NEW_CAPTURE"}
        return receipt

    try:
        return coordinator.run_once(identity, action, validate)
    except PremarketError:
        raise
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise PremarketError("PREMARKET_LOCAL_CONTROL_OR_PROCESS_UNAVAILABLE") from None


def execute_premarket(profile, *, observation_date: str, target_session: str, request_key: str, coordinator) -> dict:
    """Public boundary: do not expose provider console or private path errors."""
    try:
        return _execute_premarket(profile, observation_date=observation_date,
                                  target_session=target_session, request_key=request_key,
                                  coordinator=coordinator)
    except PremarketError:
        raise
    except ValueError as exc:
        # The coordinator/capacity layer also returns controlled uppercase codes.
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,159}", str(exc)):
            raise
        raise PremarketError("PREMARKET_LOCAL_CONTROL_OR_PROCESS_UNAVAILABLE") from None
    except (OSError, KeyError, TypeError, AttributeError, RuntimeError):
        raise PremarketError("PREMARKET_LOCAL_CONTROL_OR_PROCESS_UNAVAILABLE") from None
