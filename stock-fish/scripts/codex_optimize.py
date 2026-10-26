"""Versioned engineering entry using the existing shared runtime and old evidence.

Examples: storage-plan; storage-record --sample-id before; storage-history;
core-run --manifest REQUEST --profile FULL_CORE_COMPACT_VERIFY_V2;
archive-reuse --archive-id ID --profile FULL_CORE_ARCHIVE_READONLY_V2.
No service startup, migration or new network capture is implicit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_workspace as workspace
from scripts import codex_premarket as premarket

VERSION = "CODEX_SPACE_TIME_ENTRY_V1"
# Child commands retain their own parser, source identity, mutex and budgets.
COMMANDS = {
    "storage-plan": ("codex_storage_growth_v1.py", "plan"),
    "storage-record": ("codex_storage_growth_v1.py", "record"),
    "storage-history": ("codex_storage_growth_v1.py", "history"),
    "core-run": ("codex_compact_core_v2.py", None),
    "core-first-run": ("codex_first_core_v2.py", None),
    "archive-register": ("codex_core_archive_v2.py", "register"),
    "archive-reuse": ("codex_core_archive_v2.py", "reuse"),
    "archive-status": ("codex_core_archive_v2.py", "status"),
    "capture": ("codex_capture_compact_v1.py", "capture"),
    "verify-capture": ("codex_capture_compact_v1.py", "verify"),
    "core-from-captures": ("codex_capture_compact_v1.py", "core"),
    "outcome-profile": ("codex_outcome_profile_v1.py", None),
    "scheduler-status": ("codex_scheduler_query_v1.py", "status"),
    "scheduler-explain": ("codex_scheduler_query_v1.py", "explain"),
    "scheduler-migrate": ("codex_scheduler_query_v1.py", "migrate"),
}
UNREGISTERED_CHILD_FLAGS = frozenset({"--allow-test-fixture", "--worker-result", "--supervisor-control",
                                     "--runtime", "--registry-root"})


def execute(runtime, command, arguments):
    if runtime is None or runtime.profile.get("backend") != "premarket":
        raise workspace.WorkspaceError("OPTIMIZE_REGISTERED_PREMARKET_RUNTIME_REQUIRED")
    if command not in COMMANDS:
        raise workspace.WorkspaceError("OPTIMIZE_UNKNOWN_COMMAND")
    # Existing child parsers accept unambiguous long-option abbreviations.
    # Block those spellings too: otherwise --worker-resul could bypass the
    # supervisor, or --runtim could select a different registered instance.
    options = [argument.split("=", 1)[0] for argument in arguments if argument.startswith("--")]
    if any(option != "--" and any(flag.startswith(option) for flag in UNREGISTERED_CHILD_FLAGS)
           for option in options):
        raise workspace.WorkspaceError("OPTIMIZE_INTERNAL_OR_LOCATOR_OVERRIDE_FORBIDDEN")
    runtime.check_binding()
    prepared = premarket._prepared(runtime.profile)
    script, action = COMMANDS[command]
    path = prepared["source"] / "scripts" / script
    if path.is_symlink() or not path.is_file() or path.resolve(strict=True) != path:
        raise workspace.WorkspaceError("OPTIMIZE_REGISTERED_VERSION_NOT_INSTALLED")
    premarket._fp(path)  # Refuse a nonresident script before launching Python.
    common = ([] if command.startswith("scheduler-") or command == "outcome-profile" else
              ["--runtime", runtime.profile["name"], "--registry-root", str(runtime.registry_root)])
    # -B prevents writes but still permits existing bytecode reads. A fresh,
    # absent lookup prefix ensures the registered source is actually executed.
    with tempfile.TemporaryDirectory(prefix="stockfish-optimize-bytecode-") as temporary:
        lookup = Path(temporary) / "absent"
        argv = [str(prepared["python"]), "-I", "-B", "-X", f"pycache_prefix={lookup}", str(path), *common]
        if action is not None:
            argv.append(action)
        argv.extend(arguments)
        # Keep the original child's watchdog and outcome. Never retry unknown work.
        code, raw, _ = premarket._run(argv, prepared)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise workspace.WorkspaceError("OPTIMIZE_CHILD_INVALID_RESPONSE") from None
    if not isinstance(value, dict):
        raise workspace.WorkspaceError("OPTIMIZE_CHILD_INVALID_RESPONSE")
    runtime.check_binding()
    return {**value, "command_exit_code": code, "runtime": runtime.profile["name"],
            "entry_version": VERSION}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--runtime")
    result.add_argument("--registry-root", type=Path)
    result.add_argument("command", choices=tuple(COMMANDS))
    result.add_argument("arguments", nargs=argparse.REMAINDER,
                        help="Original child arguments; use the named script for detailed --help")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(workspace.load_runtime(args.runtime, args.registry_root), args.command, args.arguments)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return result["command_exit_code"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        reason = str(error) if isinstance(error, (workspace.WorkspaceError, premarket.PremarketError)) else "OPTIMIZE_LOCAL_IO_OR_SCHEMA_ERROR"
        print(json.dumps(dict(status="BLOCKED", reason=reason), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
