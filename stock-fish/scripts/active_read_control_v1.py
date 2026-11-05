"""Fixed read-only audits in their attested active source tree.

The control-plane parent owns optional new report publication. The child has no
business capability, performs the existing delivery audits, and emits JSON only.
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
VERSION = "ACTIVE_READ_CONTROL_V1"
OPERATIONS = ("delivery-audit", "inspect-sequence")
MAX_OUTPUT = 64 * 1024**2


def need(value, code):
    if not value:
        raise ValueError(code)


def module(name):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("scripts." + name)


def parser(operation):
    need(operation in OPERATIONS, "ACTIVE_READ_OPERATION_FORBIDDEN")
    result = argparse.ArgumentParser(prog="active-read " + operation, allow_abbrev=False)
    result.add_argument("--source-root", type=Path, required=True)
    result.add_argument("--runtime", required=True)
    result.add_argument("--registry-root", type=Path, required=True)
    result.add_argument("--workspace-id", required=True)
    result.add_argument("--max-read-bytes", type=int, default=512*1024**2)
    result.add_argument("--max-seconds", type=float, default=180)
    result.add_argument("--output", type=Path)
    if operation == "delivery-audit":
        result.add_argument("--authority-db", type=Path, required=True)
        result.add_argument("--business-id", required=True)
        result.add_argument("--job", type=Path, required=True)
        result.add_argument("--journal", type=Path, required=True)
        for name in ("run", "outcomes"):
            result.add_argument("--"+name, type=Path)
            result.add_argument("--"+name+"-sha256")
        result.add_argument("--outcome-business-id")
        result.add_argument("--reauthenticate-signal", action="store_true")
        result.add_argument("--allow-test-fixture", action="store_true")
        result.add_argument("--deadline")
    else:
        result.add_argument("--authority-database", dest="authority_db", type=Path, required=True)
        for name in ("protocol", "requests"):
            result.add_argument("--"+name, required=True)
            result.add_argument("--"+name+"-sha256", required=True)
        result.add_argument("--protocol-business-id")
        result.add_argument("--calendar-state-root", type=Path)
        result.add_argument("--calendar-record")
        result.add_argument("--calendar-record-sha256")
    return result


def arguments(operation, argv):
    flags = [value.split("=", 1)[0] for value in argv if value.startswith("--")]
    need(len(flags) == len(set(flags)), "ACTIVE_READ_DUPLICATE_ARGUMENT")
    selected = parser(operation).parse_args(argv)
    need(0 < selected.max_read_bytes <= 2*1024**3 and math.isfinite(selected.max_seconds)
         and 0 < selected.max_seconds <= 600, "ACTIVE_READ_FINITE_AUDIT_BOUNDS_REQUIRED")
    return selected


def _runtime(args):
    source, registry = args.source_root.absolute(), args.registry_root.absolute()
    need(source.resolve(strict=True) == source and registry.resolve(strict=True) == registry,
         "ACTIVE_READ_CANONICAL_ROOTS_REQUIRED")
    build = module("active_runtime_v1")
    manifest = build.verify(source)
    active_parent = (ROOT / "ACTIVE_RUNTIME_MANIFEST.json").is_file()
    if active_parent:
        need(source == ROOT, "ACTIVE_READ_CROSS_ACTIVE_TREE_FORBIDDEN")
        runtime = module("active_business_gateway_v1").load_runtime(args.runtime, registry)
    else:
        runtime = module("codex_runtime_recovery_v2").load_runtime(args.runtime, registry)
    need(runtime.active_source_attested is True and runtime.profile.get("backend") == "premarket"
         and Path(runtime.profile["source_root"]) == source,
         "ACTIVE_READ_REGISTERED_SOURCE_MISMATCH")
    need(str(runtime.authority.path) == str(args.authority_db.absolute())
         and runtime.authority.workspace_id == args.workspace_id,
         "ACTIVE_READ_REGISTERED_AUTHORITY_MISMATCH")
    runtime.check_binding()
    need(build.verify(source) == manifest, "ACTIVE_READ_SOURCE_CHANGED")
    return runtime, manifest


def install_read_boundary(database):
    """No broad control exemption, source acquisition, service or new process."""
    uri = Path(database).absolute().as_uri() + "?mode=ro"
    forbidden = {"os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.truncate", "os.chmod",
                 "os.chown", "os.link", "os.symlink", "subprocess.Popen", "os.system", "os.exec",
                 "socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}
    def audit(event, values):
        if event in forbidden:
            raise ValueError("ACTIVE_READ_SIDE_EFFECT_FORBIDDEN")
        if event == "open":
            _, mode, flags = values
            need(not ((isinstance(mode, str) and any(char in mode for char in "wax+")) or
                 isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)),
                 "ACTIVE_READ_FILE_WRITE_FORBIDDEN")
        if event == "sqlite3.connect":
            need(values[0] == uri, "ACTIVE_READ_SQLITE_CARRIER_OR_MODE_FORBIDDEN")
        if event in {"sqlite3.enable_load_extension", "sqlite3.load_extension"}:
            raise ValueError("ACTIVE_READ_SQLITE_EXTENSION_FORBIDDEN")
    sys.addaudithook(audit)


def execute_read(operation, args, runtime):
    """Existing algorithms only; a bound runtime may be injected by isolated tests."""
    need(operation in OPERATIONS and args.output is None, "ACTIVE_READ_CHILD_STDOUT_ONLY")
    need(Path(runtime.profile["source_root"]) == ROOT
         and str(runtime.authority.path) == str(args.authority_db.absolute())
         and runtime.authority.workspace_id == args.workspace_id,
         "ACTIVE_READ_RUNTIME_IDENTITY_MISMATCH")
    runtime.check_binding()
    gateway = module("active_business_gateway_v1")
    owner = gateway.readonly_authority(args.authority_db, args.workspace_id)
    io, contract = module("codex_p0"), module("daily_delivery_contract_v1")
    refs, calendar_budget = [], None
    if operation == "delivery-audit":
        result = contract.audit_delivery(owner, args.business_id, job=args.job,
            journal_directory=args.journal, run_path=args.run, run_sha256=args.run_sha256,
            outcomes_path=args.outcomes, outcomes_sha256=args.outcomes_sha256,
            outcome_business_id=args.outcome_business_id, reauthenticate_signal=args.reauthenticate_signal,
            allow_test_fixture=args.allow_test_fixture, deadline=args.deadline,
            _budget=contract._AuditBudget(args.max_read_bytes, args.max_seconds))
        code = 0 if result["status"] == "COMPLETE" else 2
    else:
        protocol, ref = io.read_snapshot(args.protocol, args.protocol_sha256); refs.append(ref)
        if protocol.get("entry_version") == "CODEX_ACTIVE_V1":
            protocol = protocol["result"]
        batch, ref = io.read_snapshot(args.requests, args.requests_sha256); refs.append(ref)
        need(type(batch) is dict and set(batch) == {"requests"} and type(batch["requests"]) is list,
             "ACTIVE_READ_REQUEST_DESCRIPTOR_LIST_REQUIRED")
        supplied = [args.calendar_state_root is not None, args.calendar_record is not None,
                    args.calendar_record_sha256 is not None]
        need(all(supplied) or not any(supplied), "ACTIVE_READ_COMPLETE_CALENDAR_LOCATOR_REQUIRED")
        native = {}
        if all(supplied):
            current = module("codex_current_calendar_v2")
            record, ref = io.read_snapshot(args.calendar_record, args.calendar_record_sha256); refs.append(ref)
            calendar_budget = current._budget(dict(capture_read_bytes=args.max_read_bytes, max_seconds=args.max_seconds))
            cache = current._cache(SimpleNamespace(profile={"state_root": str(args.calendar_state_root)}), calendar_budget, readonly=True)
            native = dict(calendar_cache=cache, calendar_record=record)
        result = contract.audit_sequence(owner, protocol, batch["requests"],
            protocol_business_id=args.protocol_business_id, max_read_bytes=args.max_read_bytes,
            max_seconds=args.max_seconds, **native)
        code = 0 if result["status"] == "REAL_DAILY_ACCEPTANCE_ESTABLISHED" else 4
    io.verify_snapshots(refs)
    runtime.check_binding()
    return code, dict(schema_version=VERSION, operation=operation, result=result,
        source_root=str(ROOT), input_carriers=refs,
        calendar_native_resources=calendar_budget.metrics if calendar_budget else None,
        collection_started=False, business_capability_issued=False, source_authority_restored=False)


def _output_path(args, operation, runtime):
    if args.output is None:
        return None
    need(not (ROOT/"ACTIVE_RUNTIME_MANIFEST.json").exists(), "ACTIVE_READ_REPORT_REQUIRES_CONTROL_PLANE")
    output = args.output.absolute()
    need(output.parent.resolve(strict=True) == output.parent and not output.exists() and not output.is_symlink(),
         "ACTIVE_READ_NEW_CANONICAL_REPORT_REQUIRED")
    protected = [ROOT, ROOT.parent, args.source_root.absolute(), args.registry_root.absolute()]
    descriptors = []
    if operation == "delivery-audit":
        descriptors.append(dict(job=args.job, journal_directory=args.journal,
            run_path=args.run, outcomes_path=args.outcomes))
    else:
        batch, _ = module("codex_p0").read_snapshot(args.requests, args.requests_sha256)
        descriptors = batch.get("requests", [])
        if args.calendar_state_root is not None:
            protected.append(args.calendar_state_root.absolute()/"cache")
    for row in descriptors:
        if type(row) is not dict:
            continue
        for key in ("job", "journal_directory", "run_path", "outcomes_path"):
            if row.get(key) is not None:
                path = Path(row[key]).absolute()
                protected.append(path.parent if key.endswith("_path") else path)
    need(not any(output.is_relative_to(path.resolve(strict=True)) for path in protected),
         "ACTIVE_READ_REPORT_INSIDE_PROTECTED_SOURCE_OR_ARCHIVE")
    return output


def dispatch(operation, argv):
    """Control-plane routing only; all native audit code executes in the child."""
    args = arguments(operation, argv)
    runtime, manifest = _runtime(args)
    output = _output_path(args, operation, runtime)
    entry = args.source_root.absolute()/"scripts/active_read_control_v1.py"
    need("scripts/active_read_control_v1.py" in manifest["output_sha256"], "ACTIVE_READ_ENTRY_NOT_ATTESTED")
    forwarded, index = [], 0
    while index < len(argv):
        value = argv[index]
        if value == "--output":
            index += 2; continue
        if value.startswith("--output="):
            index += 1; continue
        forwarded.append(value); index += 1
    environment = dict(PATH=os.defpath, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([runtime.profile["python"], "-I", "-B", str(entry), operation, *forwarded],
        cwd=args.source_root, env=environment, capture_output=True, text=True, timeout=args.max_seconds+30)
    runtime.check_binding()
    need(module("active_runtime_v1").verify(args.source_root) == manifest, "ACTIVE_READ_SOURCE_CHANGED_AFTER_CHILD")
    need(result.returncode in {0, 2, 4} and len(result.stdout.encode()) <= MAX_OUTPUT,
         "ACTIVE_READ_CHILD_REJECTED_OR_UNAVAILABLE")
    envelope = json.loads(result.stdout)
    need(type(envelope) is dict and envelope.get("schema_version") == VERSION
         and envelope.get("operation") == operation and envelope.get("source_root") == str(args.source_root.absolute())
         and all(envelope.get(key) is False for key in ("collection_started", "business_capability_issued", "source_authority_restored")),
         "ACTIVE_READ_CHILD_ENVELOPE_MISMATCH")
    if output is not None:
        io = module("codex_p0")
        def terminal():
            runtime.check_binding()
            need(module("active_runtime_v1").verify(args.source_root) == manifest, "ACTIVE_READ_SOURCE_CHANGED_AT_PUBLICATION")
            io.verify_snapshots(envelope["input_carriers"])
        io.publish_new(output, envelope, validate=terminal)
    print(json.dumps(envelope, ensure_ascii=False, sort_keys=True))
    return result.returncode


def main(argv=None):
    selected = list(sys.argv[1:] if argv is None else argv)
    try:
        need(selected and selected[0] in OPERATIONS, "ACTIVE_READ_OPERATION_FORBIDDEN")
        operation = selected.pop(0)
        args = arguments(operation, selected)
        need(args.output is None and args.source_root.absolute() == ROOT,
             "ACTIVE_READ_CHILD_SOURCE_OR_OUTPUT_REJECTED")
        need(not any(os.environ.get(key) for key in ("STOCKFISH_WORKER_CAPABILITY", "STOCKFISH_BUSINESS_ID",
             "STOCKFISH_ACTIVE_SERVICE_DESCRIPTOR")), "ACTIVE_READ_EXISTING_CAPABILITY_FORBIDDEN")
        install_read_boundary(args.authority_db)
        gateway = module("active_business_gateway_v1"); gateway.install(__file__)
        runtime, manifest = _runtime(args)
        code, result = execute_read(operation, args, runtime)
        need(module("active_runtime_v1").verify(ROOT) == manifest, "ACTIVE_READ_SOURCE_CHANGED_AFTER_AUDIT")
        raw = json.dumps(result, ensure_ascii=False, sort_keys=True)
        need(len(raw.encode()) <= MAX_OUTPUT, "ACTIVE_READ_OUTPUT_BOUND")
        print(raw)
        return code
    except (ValueError, OSError, TimeoutError) as error:
        print(json.dumps(dict(schema_version=VERSION, status="REJECTED", error_type=type(error).__name__,
                             business_capability_issued=False, collection_started=False)))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
