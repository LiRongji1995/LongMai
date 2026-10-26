"""Explicit read-only posterior profiling using the original CLI and its budgets.

Only archive verification, plans and status reads are accepted. This command
never submits/retries a worker or changes the old 300-second default. Profiling
adds overhead and is not an unprofiled performance acceptance measurement.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_verification_v2 as verification
from stockfish_core.yaogu import r2_closure_cli as original

VERSION = "CODEX_OUTCOME_PROFILE_V1"
READ_COMMANDS = frozenset(command for command in original.COMMANDS
                         if command.endswith(("-plan", "-status", "-pending"))) | {"r2-archive-verify"}


def main(argv=None):
    selected = list(sys.argv[1:] if argv is None else argv)
    if not selected or selected[0] not in READ_COMMANDS:
        if selected == ["--help"]:
            print(__doc__)
            print("Supported original commands: " + ", ".join(sorted(READ_COMMANDS)))
            print("Pass the original command's arguments unchanged; append --help for its help.")
            return 0
        print(json.dumps(dict(schema_version=VERSION, status="BLOCKED",
                              reason="OUTCOME_PROFILE_READ_ONLY_COMMAND_REQUIRED")))
        return 2
    args = original.parser().parse_args(selected)
    original_command = args.command
    try:
        (code, result), diagnostics = verification.outcome_diagnostics(original.execute, args)
    except KeyboardInterrupt as error:
        code, result = 130, dict(status="INTERRUPTED", reason="USER_INTERRUPTED")
        diagnostics = getattr(error, "verification_v2", None)
    except Exception as error:
        code, result = 3, dict(status="FAILED", **original._safe_error(error))
        diagnostics = getattr(error, "verification_v2", None)
    print(json.dumps(dict(schema_version=VERSION, original_command=original_command,
        command_exit_code=code, original_result=result, diagnostics=diagnostics,
        read_only_business_state=True, worker_started=False, original_budgets_preserved=True,
        performance_acceptance=False), ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
