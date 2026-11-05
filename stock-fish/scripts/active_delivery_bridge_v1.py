"""Direct native supervision and delivery binding for the original active worker.

The bridge forwards the unchanged original/shared worker entry in the same
child process that owns its durable journal. It never collects, retries or
creates a business claim, and incomplete supervision cannot become success.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_sdk_tree_supervisor_v3 as supervisor
from scripts import daily_delivery_contract_v1 as contract
from scripts import durable_stage_metrics_v1 as metrics
from scripts import codex_delivery_audit_v1 as delivery
from scripts import codex_operational_report_v1 as operational
from scripts import codex_p0 as io

VERSION = "ACTIVE_DELIVERY_BRIDGE_V1"
WORKER_RECEIPT = "authority-writer-receipt.json"
CONTROL = "authority-supervisor"
SUPERVISOR_RETURN = "authority-supervisor-return.json"
need = contract.need


class _ControlCost:
    def __init__(self):
        self.reads, self.native_charge, self.written = {}, 0, 0
        self.started, self.cpu_started = time.monotonic(), time.process_time()
        self.byte_limit, self.seconds = None, None

    @property
    def used(self):
        return sum(self.reads.values()) + self.native_charge

    def configure(self, limits):
        self.byte_limit, self.seconds = limits["supervisor_read_bytes"], limits["max_seconds"]
        self.check()

    def check(self):
        if self.byte_limit is not None:
            need(self.used <= self.byte_limit, "ACTIVE_BRIDGE_CUMULATIVE_READ_LIMIT")
        if self.seconds is not None:
            need(time.monotonic()-self.started <= self.seconds, "ACTIVE_BRIDGE_CUMULATIVE_TIME_LIMIT")

    def remaining(self, cap):
        self.check()
        return cap if self.byte_limit is None else min(cap, self.byte_limit-self.used)

    def charge(self, scope, amount):
        need(type(amount) is int and amount >= 0, "ACTIVE_BRIDGE_READ_COUNT_INVALID")
        self.reads[scope] = self.reads.get(scope, 0) + amount
        self.check()

    def snapshot(self):
        return {"returned_read_bytes_by_scope": deepcopy(self.reads),
            "returned_read_bytes": sum(self.reads.values()), "native_supervisor_budget_charge_bytes": self.native_charge,
            "total_read_budget_charge_bytes": self.used, "written_receipt_bytes": self.written,
            "wall_seconds": time.monotonic()-self.started,
            "current_process_cpu_seconds": time.process_time()-self.cpu_started,
            "scope": "BRIDGE_ENTRY_FILE_READ_RETURNS_AND_PINNED_NATIVE_COUNTER; NOT_SQL_OR_KERNEL_IO",
            "bootstrap_before_bridge_entry_cost": "UNKNOWN_NOT_MEASURED_HERE"}


def _job(value):
    path = Path(value).absolute()
    need(path.resolve(strict=True) == path and path.is_dir(), "ACTIVE_BRIDGE_CANONICAL_JOB_REQUIRED")
    return path


def checked_worker_args(entry, job, worker_args=None):
    need(entry in {"original", "shared"}, "ACTIVE_BRIDGE_ENTRY_REQUIRED")
    job = _job(job)
    args = list(worker_args) if worker_args is not None else ["_worker", "--job", str(job)]
    need(args[:1] == ["_worker"] and all(type(x) is str for x in args), "ACTIVE_BRIDGE_WORKER_ARGUMENTS")
    allowed = {"--job"} if entry == "original" else {"--job", "--engine", "--observation-date", "--target-session", "--planning-local-date"}
    tail, values = args[1:], {}
    need(len(tail) % 2 == 0, "ACTIVE_BRIDGE_WORKER_ARGUMENTS")
    for key, value in zip(tail[::2], tail[1::2]):
        need(key in allowed and key not in values, "ACTIVE_BRIDGE_UNKNOWN_OR_DUPLICATE_ARGUMENT")
        values[key] = value
    need(values.get("--job") == str(job), "ACTIVE_BRIDGE_JOB_CHANGED")
    if entry == "shared":
        need(values.get("--engine", "original") in {"original", "shared"}, "ACTIVE_BRIDGE_ENGINE_REJECTED")
        dates = [values.get(key) for key in ("--observation-date", "--target-session", "--planning-local-date")]
        need(all(x is not None for x in dates) or all(x is None for x in dates), "ACTIVE_BRIDGE_PARTIAL_SESSION_BINDING")
        for value in dates:
            if value is not None: need(date.fromisoformat(value).isoformat() == value, "ACTIVE_BRIDGE_SESSION_INVALID")
    return args


def _command(role, source_root, job, *, entry="original", worker_args=None, python=None):
    root, job = Path(source_root).absolute(), _job(job)
    need(root.resolve(strict=True) == root and root.is_dir(), "ACTIVE_BRIDGE_CANONICAL_SOURCE_REQUIRED")
    args = checked_worker_args(entry, job, worker_args)
    absent = job/"authority-bytecode-must-remain-absent"
    need(not absent.exists() and not absent.is_symlink(), "ACTIVE_BRIDGE_BYTECODE_PATH_MUST_BE_ABSENT")
    return [python or sys.executable, "-I", "-B", "-X", "pycache_prefix="+str(absent), str(root/"scripts/active_worker_bootstrap_v1.py"),
        "--module", "scripts.active_delivery_bridge_v1", "--", role, "--entry", entry,
        "--job", str(job), "--", *args]


def supervisor_command(source_root, job, *, entry="original", worker_args=None, python=None):
    return _command("supervise", source_root, job, entry=entry, worker_args=worker_args, python=python)


def worker_command(source_root, job, *, entry="original", worker_args=None, python=None):
    return _command("worker", source_root, job, entry=entry, worker_args=worker_args, python=python)


def _configuration(job, meter=None):
    reader = delivery._Reader(meter.remaining(16*1024**2) if meter else 16*1024**2, 15)
    value, reference = reader.read(job/"configuration.json", control=True)
    reader.terminal()
    if meter is not None: meter.charge("configuration_initial_and_terminal", reader.returned_bytes)
    return value, reference


def _limits(config):
    limits = config.get("limits", {})
    need(type(limits) is dict and all(key in limits for key in supervisor.CONSUMED_LIMITS), "ACTIVE_BRIDGE_EXPLICIT_LIMITS_REQUIRED")
    result = {key: limits[key] for key in supervisor.CONSUMED_LIMITS}
    need(all(type(x) in (int, float) and math.isfinite(x) and x > 0 for x in result.values()),
         "ACTIVE_BRIDGE_FINITE_POSITIVE_LIMITS_REQUIRED")
    return result


def _charge_active_control(amount):
    # Library fixtures outside an active tree have no authority to mutate.
    # Every actual active invocation must use its existing live capability.
    if (ROOT/"ACTIVE_RUNTIME_MANIFEST.json").is_file():
        from scripts import active_business_gateway_v1 as gateway
        value = gateway.current("metrics")
        with gateway._control():
            value["owner"].charge(value["claim"], {"supervisor_read_bytes": amount})


@contextmanager
def _termination():
    # SIGKILL cannot execute a finally block. Its missing terminal stays UNKNOWN.
    previous = {kind: signal.getsignal(kind) for kind in (signal.SIGTERM, signal.SIGINT)}
    def stop(kind, frame):
        raise InterruptedError("ACTIVE_BRIDGE_TERMINATED")
    try:
        for kind in previous: signal.signal(kind, stop)
        yield
    finally:
        for kind, handler in previous.items(): signal.signal(kind, handler)


def run_supervised_worker(job, command, *, source_root, environment, entry="original",
                          worker_args=None, source_references=(), test_fixture=False, _meter=None):
    """Bound adapter; alternate commands are restricted to explicit fixture jobs."""
    job = _job(job)
    meter = _meter or _ControlCost()
    config, reference = _configuration(job, meter)
    fixture = config.get("capture", {}).get("allow_test_fixture") is True
    need(type(test_fixture) is bool and fixture == test_fixture, "ACTIVE_BRIDGE_FIXTURE_CONFIG_MISMATCH")
    expected = worker_command(source_root, job, entry=entry, worker_args=worker_args)
    if list(command) != expected:
        need(fixture and any("TEST_FIXTURE" in part for part in job.parts), "ACTIVE_BRIDGE_ALTERNATE_WORKER_FORBIDDEN")
    limits = _limits(config)  # Reject unlimited old defaults before spawning.
    meter.configure(limits)
    limits["supervisor_read_bytes"] -= meter.used
    limits["max_seconds"] -= time.monotonic()-meter.started
    need(limits["supervisor_read_bytes"] > 0 and limits["max_seconds"] > 0,
         "ACTIVE_BRIDGE_NO_REMAINING_BUDGET")
    with _termination():
        report = supervisor.run_supervised(command, cwd=Path(source_root).absolute(), env=dict(environment),
            limits=limits, control_dir=job/CONTROL, source_references=[reference, *source_references],
            accepted_exit_codes=(0, 2), test_fixture=fixture)
    meter.native_charge += report["terminal_supervisor_returned_read_bytes"]
    meter.check()
    terminal = {"schema_version": VERSION+"_SUPERVISOR_RETURN", "completion": report["completion"],
        "native_status": report["status"],
        "native_terminal": {key: report[key] for key in ("terminal_supervisor_returned_read_bytes",
            "terminal_actual_utc_elapsed_seconds", "terminal_monotonic_elapsed_seconds", "terminal_supervisor_peak_self_rss_bytes")},
        "bridge_cost_lower_bound": meter.snapshot(),
        "final_self_publication_and_post_verify_cost": "UNKNOWN_NOT_IN_PERSISTED_LOWER_BOUND"}
    raw_size = len(json.dumps(terminal, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    # publish_new performs two full physical verification reads on success.
    need(2*raw_size <= meter.remaining(2*raw_size), "ACTIVE_BRIDGE_PUBLICATION_READ_RESERVE")
    need(sum(p.stat().st_size for p in job.rglob("*") if p.is_file()) + raw_size <= config["limits"]["output_bytes"],
         "ACTIVE_BRIDGE_FINAL_OUTPUT_LIMIT")
    io.publish_new(job/SUPERVISOR_RETURN, terminal)
    meter.written += raw_size
    meter.charge("successful_publish_new_two_physical_reads", 2*raw_size)
    return {**report, "bridge_control_cost_receipt": meter.snapshot(),
        "bridge_control_cost_receipt_scope": "RETURNED_IN_PROCESS; PERSISTED_COPY_IS_EXPLICIT_LOWER_BOUND"}


def publish_writer_receipt(job, receipt):
    job = _job(job)
    need(type(receipt) is dict and receipt.get("schema_version") == metrics.VERSION+"_WRITER_RECEIPT",
         "ACTIVE_BRIDGE_WRITER_RECEIPT_REQUIRED")
    size = len(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    # Reserve the pinned publisher's two verification reads before writing.
    # Failed publication keeps this conservative charge; it is not zero usage.
    _charge_active_control(2*size)
    # Exclusive publication preserves any previous partial/final file on failure.
    return io.publish_new(job/WORKER_RECEIPT, deepcopy(receipt))


def prepare_authority_receipt(original_receipt, claim):
    need(type(original_receipt) is dict and original_receipt.get("job"), "ACTIVE_BRIDGE_ORIGINAL_RECEIPT_REQUIRED")
    job = _job(original_receipt["job"])
    meter = _ControlCost()
    config, _ = _configuration(job, meter)
    limits = _limits(config)
    meter.configure(limits)
    budget = contract._AuditBudget(min(2*1024**3, limits["supervisor_read_bytes"]), min(600, limits["max_seconds"]))
    budget.charge(meter.used)
    journal_dir = job/("authority-metrics-worker-"+metrics.identity(claim)["attempt_id"])
    journal = contract._journal(budget, journal_dir, claim)
    need(journal["status"] == "COMPLETE", "ACTIVE_BRIDGE_WORKER_METRICS_NOT_COMPLETE")
    reader = delivery._Reader(budget.remaining(64*1024**2), budget.remaining_seconds(60))
    final, final_ref = reader.read(job/"final-control.json", control=True)
    need(original_receipt.get("final") == final_ref, "ACTIVE_BRIDGE_ORIGINAL_FINAL_CHANGED")
    need(type(final.get("completion")) is dict, "ACTIVE_BRIDGE_NATIVE_COMPLETION_REQUIRED")
    completion, _ = reader.read(final["completion"]["path"], expected=final["completion"], control=True)
    canonical = completion.get("core_artifacts", {}).get("canonical-run.json.gz")
    need(type(canonical) is dict, "ACTIVE_BRIDGE_CANONICAL_ROLE_REQUIRED")
    budget.charge(reader.returned_bytes)
    reader_charged = reader.returned_bytes
    inspection = operational.Inspection(max_bytes=budget.remaining(operational.MAX_BYTES),
        max_seconds=budget.remaining_seconds(operational.MAX_SECONDS))
    combined, inspection = operational.combined_delivery(job, canonical["path"], canonical["sha256"], inspection=inspection)
    budget.charge(inspection.used)
    inspected = inspection.used
    need(combined["workflow"]["workflow_status"] in delivery.SUCCESS
         and combined["workflow"]["archived_workflow_complete"] is True,
         "ACTIVE_BRIDGE_ORIGINAL_WORKFLOW_NOT_COMPLETE")
    business = claim.get("business")
    need(type(business) is dict and business.get("operation") == "PREMARKET" and
         all(combined["workflow"][key] == business[key] for key in ("observation_date", "target_session", "strategy_id")),
         "ACTIVE_BRIDGE_BUSINESS_JOB_CONFLICT")
    reader.max_bytes = reader.returned_bytes+budget.remaining(64*1024**2)
    writer, _ = reader.read(job/WORKER_RECEIPT, control=True)
    contract._writer_receipt(writer, journal)
    report, report_ref = reader.read(job/CONTROL/"completion.json", control=True)
    native = contract._supervisor_audit(reader, report_ref, journal)
    need(native["verified"] is True, "ACTIVE_BRIDGE_NATIVE_CLEANUP_NOT_COMPLETE")
    terminal, terminal_ref = reader.read(job/SUPERVISOR_RETURN, control=True)
    need(terminal.get("schema_version") == VERSION+"_SUPERVISOR_RETURN" and
         terminal.get("completion") == report_ref and terminal.get("native_status") == "COMPLETE",
         "ACTIVE_BRIDGE_NATIVE_TERMINAL_RECEIPT_REQUIRED")
    for key, prior in (("terminal_supervisor_returned_read_bytes", "supervisor_returned_read_bytes"),
                       ("terminal_actual_utc_elapsed_seconds", "actual_utc_elapsed_seconds"),
                       ("terminal_monotonic_elapsed_seconds", "monotonic_elapsed_seconds"),
                       ("terminal_supervisor_peak_self_rss_bytes", "supervisor_peak_self_rss_bytes")):
        value = terminal.get("native_terminal", {}).get(key)
        need(metrics._number(value) and value >= report[prior], "ACTIVE_BRIDGE_TERMINAL_COST_UNDERSTATED")
    budget.charge(reader.returned_bytes-reader_charged)
    reader_charged = reader.returned_bytes
    binding = contract.bind_terminal_evidence(job=job, journal_directory=journal_dir,
        canonical_run_path=canonical["path"], supervisor_report_path=job/CONTROL/"completion.json", writer_receipt=writer,
        _budget=budget)
    need(binding["identity"] == metrics.identity(claim), "ACTIVE_BRIDGE_CLAIM_CHANGED")
    reader.max_bytes = reader.returned_bytes+budget.remaining(64*1024**2)
    reader.max_seconds = time.monotonic()-reader.started+budget.remaining_seconds(60)
    reader.terminal()
    budget.charge(reader.returned_bytes-reader_charged)
    inspection.limit = min(inspection.limit, inspection.used+budget.remaining(operational.MAX_BYTES))
    inspection.seconds = time.monotonic()-inspection.started+budget.remaining_seconds(operational.MAX_SECONDS)
    inspection.terminal()
    budget.charge(inspection.used-inspected)
    return {**binding, "gateway_receipt": deepcopy(original_receipt), "bridge_version": VERSION,
        "native_supervisor_terminal_receipt": terminal_ref,
        "bridge_supervision_cost": terminal,
        "authority_receipt_validation_resources": {"returned_read_bytes": budget.used,
            "wall_seconds": time.monotonic()-meter.started,
            "current_process_cpu_seconds": time.process_time()-meter.cpu_started,
            "limits": {"bytes": budget.limit, "seconds": budget.seconds},
            "scope": "ACTUAL_CONFIG_JOURNAL_WORKFLOW_NATIVE_CONTROL_AND_BINDING_INITIAL_TERMINAL_READS",
            "final_authority_database_write_cost": "NOT_INCLUDED; AUTHORITY_OWNER_RESPONSIBILITY"}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("role", choices=("supervise", "worker"))
    parser.add_argument("--entry", choices=("original", "shared"), required=True)
    parser.add_argument("--job", required=True)
    arguments = list(sys.argv[1:] if argv is None else argv)
    need(arguments.count("--") == 1, "ACTIVE_BRIDGE_EXPLICIT_WORKER_ARGUMENT_BOUNDARY")
    split = arguments.index("--")
    for flag in ("--entry", "--job"):
        need(arguments[:split].count(flag) == 1, "ACTIVE_BRIDGE_DUPLICATE_CONTROL_ARGUMENT")
    args = parser.parse_args(arguments[:split])
    job = _job(args.job)
    worker_args = arguments[split+1:]
    worker_args = checked_worker_args(args.entry, job, worker_args)
    from scripts import active_business_gateway_v1 as gateway
    from scripts.active_runtime_v1 import verify
    meter = _ControlCost()
    config, _ = _configuration(job, meter)
    meter.configure(_limits(config))
    gateway.current("worker")
    manifest = verify(ROOT, read_meter=lambda count: meter.charge("active_source_verification", count))
    if args.role == "worker":
        context = gateway.validate_workflow_worker(job, entry=args.entry, worker_args=worker_args)
        meter.charge("worker_live_ticket_and_bound_source_reads", context["worker_context_returned_read_bytes"])
        _charge_active_control(meter.used)
        if args.entry == "original":
            from stockfish_core.yaogu.premarket_workflow_v1 import main as original_main
        else:
            from scripts.codex_premarket_shared_v1 import main as original_main
        return original_main(worker_args)
    references = []
    for name in ("ACTIVE_RUNTIME_MANIFEST.json", "scripts/active_worker_bootstrap_v1.py", "scripts/active_delivery_bridge_v1.py",
                 "stockfish_core/yaogu/premarket_workflow_v1.py", "scripts/codex_premarket_shared_v1.py"):
        raw, reference = supervisor.reference(ROOT/name)
        meter.charge("native_source_reference_construction", len(raw))
        references.append(reference)
    gateway.reserve_workflow_worker(job, entry=args.entry, worker_args=worker_args)
    report = run_supervised_worker(job, worker_command(ROOT, job, entry=args.entry, worker_args=worker_args),
        source_root=ROOT, environment=gateway.child_environment(), entry=args.entry, worker_args=worker_args,
        source_references=references, test_fixture=config.get("capture", {}).get("allow_test_fixture") is True, _meter=meter)
    need(verify(ROOT, read_meter=lambda count: meter.charge("active_source_verification", count)) == manifest,
         "ACTIVE_BRIDGE_SOURCE_CHANGED")
    _charge_active_control(meter.used)
    if report["status"] == "COMPLETE":
        gateway.complete_workflow_supervisor(job, entry=args.entry, worker_args=worker_args, report=report)
    return report["child_exit_code"] if report["status"] == "COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
