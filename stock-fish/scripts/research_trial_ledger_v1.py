"""Append-only local research attempts across plan IDs; never source authority.

The explicitly chosen scope is the denominator. A different directory, offline
experiments, or undisclosed work cannot be certified complete by this ledger.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_p0 as io
from scripts import codex_research_protocol_v1 as old

VERSION = "RESEARCH_TRIAL_LEDGER_V1"
MAX_EVENTS = 4096
MAX_BYTES = 16 * 1024**2
need, digest = old.need, old.digest


def _clock():
    return datetime.now(timezone.utc).isoformat()


def _root(path):
    path = Path(path).absolute()
    need(path.resolve(strict=True) == path and path.is_dir(), "LEDGER_CANONICAL_DIRECTORY_REQUIRED")
    need(path.stat().st_mode & 0o077 == 0, "LEDGER_PRIVATE_DIRECTORY_REQUIRED")
    return path


@contextmanager
def _locked(root, *, exclusive):
    root = _root(root)
    with io.workspace._directory_fd(root) as parent:
        fd = os.open(".lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_mode & 0o077 == 0,
                 "LEDGER_LOCK_IDENTITY_INVALID")
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            current = os.stat(".lock", dir_fd=parent, follow_symlinks=False)
            need((before.st_dev, before.st_ino) == (current.st_dev, current.st_ino), "LEDGER_LOCK_CHANGED")
            yield root
            with io.workspace._directory_fd(root) as after:
                need((os.fstat(parent).st_dev, os.fstat(parent).st_ino) ==
                     (os.fstat(after).st_dev, os.fstat(after).st_ino), "LEDGER_ROOT_CHANGED")
            current = os.stat(".lock", dir_fd=parent, follow_symlinks=False)
            need((before.st_dev, before.st_ino) == (current.st_dev, current.st_ino), "LEDGER_LOCK_CHANGED")
        finally:
            os.close(fd)


def initialize(root, scope_id):
    need(type(scope_id) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", scope_id), "LEDGER_SCOPE_ID_REQUIRED")
    root = Path(root).absolute()
    with io.workspace._directory_fd(root.parent) as parent:
        os.mkdir(root.name, 0o700, dir_fd=parent)
    # A failed initialization is retained, never automatically retried/reset.
    with io.workspace._directory_fd(root) as parent:
        fd = os.open(".lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        os.fsync(fd)
        os.close(fd)
        os.fsync(parent)
    scope = old.seal(dict(schema_version=VERSION + "_SCOPE", scope_id=scope_id, created_at=_clock(),
        completeness="SELECTED_LOCAL_SCOPE_ONLY", offline_or_other_scope_attempts="UNKNOWN",
        external_preregistration=False))
    io.publish_new(root / "scope.json", scope)
    return inspect(root)


def _read(path, refs, budget):
    raw, ref = io.files._read_file(path, maximum=min(io.MAX_REPORT_BYTES, budget[0]), private=True)
    budget[0] -= len(raw)
    need(budget[0] >= 0, "LEDGER_READ_BUDGET")
    refs.append(ref)
    return io.files._json(raw)


def validate_attempt(attempt):
    need(type(attempt) is dict and set(attempt) == {"attempt_id", "configuration_hash", "status", "registered_at", "result_hash"},
         "LEDGER_ATTEMPT_FIELDS")
    old.nonempty(attempt["attempt_id"], "LEDGER_ATTEMPT_ID_REQUIRED")
    old.hash_value(attempt["configuration_hash"], "LEDGER_CONFIGURATION_HASH_REQUIRED")
    need(attempt["status"] in {"PLANNED", "COMPLETED", "FAILED", "UNKNOWN"}, "LEDGER_ATTEMPT_STATUS")
    need(old.coverage._aware(attempt["registered_at"]) <= old.now(), "LEDGER_FUTURE_DECLARED_REGISTRATION")
    if attempt["result_hash"] is not None:
        old.hash_value(attempt["result_hash"], "LEDGER_RESULT_HASH_REQUIRED")
    need(attempt["status"] not in {"COMPLETED", "FAILED"} or attempt["result_hash"] is not None,
         "LEDGER_TERMINAL_ATTEMPT_RESULT_REQUIRED")
    return attempt


def _load(root):
    refs, budget = [], [MAX_BYTES]
    scope = _read(root / "scope.json", refs, budget)
    old.check_seal(scope, VERSION + "_SCOPE")
    names = sorted(p.name for p in root.iterdir() if p.name not in {"scope.json", ".lock"})
    need(len(names) <= MAX_EVENTS and names == [f"{i:06d}.json" for i in range(1, len(names) + 1)],
         "LEDGER_GAP_UNRECOGNIZED_FILE_OR_EVENT_LIMIT")
    events, attempts, plans, assigned_attempts, studies = [], {}, {}, set(), {}
    head, previous_clock = scope["content_hash"], old.coverage._aware(scope["created_at"])
    for index, name in enumerate(names, 1):
        event = _read(root / name, refs, budget)
        old.check_seal(event, VERSION + "_EVENT")
        need(set(event) == {"schema_version", "sequence", "scope_hash", "previous_hash", "recorded_at", "kind", "payload", "content_hash"},
             "LEDGER_EVENT_FIELDS")
        clock = old.coverage._aware(event["recorded_at"])
        need(event["sequence"] == index and event["scope_hash"] == scope["content_hash"]
             and event["previous_hash"] == head and previous_clock <= clock <= old.now(), "LEDGER_CHAIN_OR_CLOCK_MISMATCH")
        payload = event["payload"]
        if event["kind"] == "ATTEMPT":
            validate_attempt(payload)
            need(payload["attempt_id"] not in attempts, "LEDGER_ATTEMPT_ALREADY_REGISTERED")
            need(old.coverage._aware(payload["registered_at"]) <= clock, "LEDGER_ATTEMPT_DECLARATION_AFTER_RECORD")
            attempts[payload["attempt_id"]] = deepcopy(payload)
        elif event["kind"] == "RESULT":
            need(type(payload) is dict and set(payload) == {"attempt_id", "status", "result_hash"}, "LEDGER_RESULT_FIELDS")
            need(payload["attempt_id"] in attempts and payload["status"] in {"COMPLETED", "FAILED", "UNKNOWN"}, "LEDGER_RESULT_ATTEMPT_REQUIRED")
            old.hash_value(payload["result_hash"], "LEDGER_RESULT_HASH_REQUIRED")
            prior = attempts[payload["attempt_id"]]
            need(prior["status"] == "PLANNED", "LEDGER_TERMINAL_ATTEMPT_IMMUTABLE")
            prior.update(status=payload["status"], result_hash=payload["result_hash"])
        elif event["kind"] == "FREEZE":
            need(type(payload) is dict and set(payload) == {"plan_id", "plan_hash", "attempts_hash", "frozen_plan", "active_attempt_id"}, "LEDGER_FREEZE_FIELDS")
            old.nonempty(payload["plan_id"], "LEDGER_PLAN_ID_REQUIRED")
            old.hash_value(payload["plan_hash"], "LEDGER_PLAN_HASH_REQUIRED")
            need(payload["plan_id"] not in plans and payload["attempts_hash"] == digest(list(attempts.values())),
                 "LEDGER_PLAN_REUSED_OR_ATTEMPTS_OMITTED")
            active = payload["active_attempt_id"]
            need(active in attempts and attempts[active]["status"] == "PLANNED" and active not in assigned_attempts,
                 "LEDGER_FRESH_PLANNED_ATTEMPT_REQUIRED")
            assigned_attempts.add(active)
            if payload["frozen_plan"] is not None:
                frozen = payload["frozen_plan"]
                need(type(frozen) is dict and frozen.get("content_hash") == payload["plan_hash"]
                     == digest({k:v for k,v in frozen.items() if k != "content_hash"}), "LEDGER_FROZEN_PLAN_HASH_MISMATCH")
            plans[payload["plan_id"]] = deepcopy(event)
        elif event["kind"] in {"STUDY_CLAIM", "STUDY_RESULT"}:
            _validate_study_event(event["kind"], payload, plans, studies)
            key = payload["plan_id"] + ":" + payload["phase"]
            if event["kind"] == "STUDY_CLAIM":
                studies[key] = dict(claim=deepcopy(event), result=None)
            else:
                studies[key]["result"] = deepcopy(event)
        else:
            raise ValueError("LEDGER_UNKNOWN_EVENT")
        events.append(event)
        head, previous_clock = event["content_hash"], clock
    io.verify_snapshots(refs)
    return dict(schema_version=VERSION, scope=scope, head=head, events=events,
        attempts=list(attempts.values()), plans=plans, studies=studies, assigned_attempt_ids=sorted(assigned_attempts), event_count=len(events), references=refs,
        offline_or_other_scope_attempts="UNKNOWN", external_preregistration=False, bytes_read=MAX_BYTES - budget[0])


def inspect(root):
    with _locked(root, exclusive=False) as path:
        return _load(path)


def _validate_study_event(kind, payload, plans, studies):
    fields = {"plan_id", "phase", "plan_hash", "input_hash"} if kind == "STUDY_CLAIM" else {"plan_id", "phase", "claim_hash", "report"}
    need(type(payload) is dict and set(payload) == fields, "LEDGER_STUDY_FIELDS")
    need(payload["plan_id"] in plans and payload["phase"] in {"validation", "holdout"}, "LEDGER_STUDY_PLAN_PHASE")
    key = payload["plan_id"] + ":" + payload["phase"]
    if kind == "STUDY_CLAIM":
        need(key not in studies, "LEDGER_PHASE_ALREADY_CONSUMED_NO_RETRY")
        need(payload["plan_hash"] == plans[payload["plan_id"]]["payload"]["plan_hash"], "LEDGER_STUDY_PLAN_HASH")
        old.hash_value(payload["input_hash"], "LEDGER_STUDY_INPUT_HASH")
        if payload["phase"] == "holdout":
            previous = studies.get(payload["plan_id"] + ":validation")
            need(previous is not None and previous["result"] is not None, "LEDGER_VALIDATION_TERMINAL_REQUIRED")
    else:
        need(key in studies and studies[key]["result"] is None and
             payload["claim_hash"] == studies[key]["claim"]["content_hash"], "LEDGER_STUDY_RESULT_CLAIM")
        report = payload["report"]
        claim = studies[key]["claim"]
        need(type(report) is dict and report.get("content_hash") == digest({k:v for k,v in report.items() if k != "content_hash"})
             and report.get("study_claim_hash") == payload["claim_hash"], "LEDGER_STUDY_REPORT_HASH")
        need(report.get("schema_version") == "CODEX_RESEARCH_PROTOCOL_V2_STUDY"
             and report.get("phase") == payload["phase"]
             and report.get("frozen_plan_hash") == claim["payload"]["plan_hash"]
             and report.get("study_input_hash") == claim["payload"]["input_hash"]
             and report.get("ledger_head") == claim["content_hash"]
             and report.get("status") in {"CONDITIONAL_ISOLATED_ARCHIVE_ESTIMATE", "BLOCKED_DATA"}
             and all(report.get(k) == v for k,v in old.SAFE.items())
             and report.get("sample_is_independent_holdout_verified") is False,
             "LEDGER_STUDY_REPORT_BINDING_OR_AUTHORITY")


def _append(root, kind, payload, expected_head, *, expected_attempts=None):
    with _locked(root, exclusive=True) as path:
        current = _load(path)
        need(current["head"] == expected_head, "LEDGER_STALE_HEAD_NO_AUTOMATIC_RETRY")
        need(current["event_count"] < MAX_EVENTS, "LEDGER_EVENT_LIMIT")
        if expected_attempts is not None:
            need(expected_attempts == current["attempts"], "LEDGER_ALL_SCOPE_ATTEMPTS_REQUIRED")
        if kind == "ATTEMPT":
            validate_attempt(payload)
            need(payload["attempt_id"] not in {a["attempt_id"] for a in current["attempts"]}, "LEDGER_ATTEMPT_ALREADY_REGISTERED")
        elif kind == "RESULT":
            need(type(payload) is dict and set(payload) == {"attempt_id", "status", "result_hash"}, "LEDGER_RESULT_FIELDS")
            matches = [a for a in current["attempts"] if a["attempt_id"] == payload["attempt_id"]]
            need(len(matches) == 1 and matches[0]["status"] == "PLANNED", "LEDGER_TERMINAL_ATTEMPT_IMMUTABLE")
            need(payload["status"] in {"COMPLETED", "FAILED", "UNKNOWN"}, "LEDGER_RESULT_STATUS")
            old.hash_value(payload["result_hash"], "LEDGER_RESULT_HASH_REQUIRED")
        elif kind == "FREEZE":
            need(payload["plan_id"] not in current["plans"], "LEDGER_PLAN_ID_ALREADY_FROZEN")
            need(payload["attempts_hash"] == digest(current["attempts"]), "LEDGER_ALL_SCOPE_ATTEMPTS_REQUIRED")
            active = payload["active_attempt_id"]
            need(active not in current["assigned_attempt_ids"] and any(a["attempt_id"] == active and a["status"] == "PLANNED" for a in current["attempts"]),
                 "LEDGER_FRESH_PLANNED_ATTEMPT_REQUIRED")
        elif kind in {"STUDY_CLAIM", "STUDY_RESULT"}:
            _validate_study_event(kind, payload, current["plans"], current["studies"])
        else:
            raise ValueError("LEDGER_UNKNOWN_EVENT")
        recorded = _clock()
        prior_clock = current["events"][-1]["recorded_at"] if current["events"] else current["scope"]["created_at"]
        need(old.coverage._aware(recorded) >= old.coverage._aware(prior_clock), "LEDGER_CLOCK_MOVED_BACKWARD")
        event = old.seal(dict(schema_version=VERSION + "_EVENT", sequence=current["event_count"] + 1,
            scope_hash=current["scope"]["content_hash"], previous_hash=current["head"], recorded_at=recorded,
            kind=kind, payload=deepcopy(payload)))
        event_size = len(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        need(current["bytes_read"] + event_size <= MAX_BYTES, "LEDGER_PROJECTED_BYTE_BUDGET")
        io.publish_new(path / f"{event['sequence']:06d}.json", event,
                       validate=lambda: io.verify_snapshots(current["references"]))
        verified = _load(path)
        need(verified["head"] == event["content_hash"], "LEDGER_APPEND_TERMINAL_MISMATCH")
        return event


def append_attempt(root, attempt, *, expected_head):
    return _append(root, "ATTEMPT", attempt, expected_head)


def record_result(root, attempt_id, status, result_hash, *, expected_head):
    return _append(root, "RESULT", dict(attempt_id=attempt_id, status=status, result_hash=result_hash), expected_head)


def register_plan(root, plan_id, plan_hash, attempts, *, expected_head, active_attempt_id, frozen_plan=None):
    old.nonempty(plan_id, "LEDGER_PLAN_ID_REQUIRED")
    old.hash_value(plan_hash, "LEDGER_PLAN_HASH_REQUIRED")
    if frozen_plan is not None:
        need(type(frozen_plan) is dict and frozen_plan.get("content_hash") == plan_hash
             == digest({k:v for k,v in frozen_plan.items() if k != "content_hash"}), "LEDGER_FROZEN_PLAN_HASH_MISMATCH")
    return _append(root, "FREEZE", dict(plan_id=plan_id, plan_hash=plan_hash, attempts_hash=digest(attempts), frozen_plan=frozen_plan, active_attempt_id=active_attempt_id),
                   expected_head, expected_attempts=attempts)


def claim_study(root, plan_id, plan_hash, phase, input_hash, *, expected_head):
    """Consume a phase before reading outcomes. Failure remains UNKNOWN, never retried."""
    return _append(root, "STUDY_CLAIM", dict(plan_id=plan_id, plan_hash=plan_hash, phase=phase,
        input_hash=input_hash), expected_head)


def finish_study(root, plan_id, phase, claim_hash, report, *, expected_head):
    return _append(root, "STUDY_RESULT", dict(plan_id=plan_id, phase=phase, claim_hash=claim_hash,
        report=report), expected_head)


def main(argv=None):
    parser = argparse.ArgumentParser(description="显式本地跨计划试验账本；线下探索完整性未知", allow_abbrev=False)
    parser.add_argument("command", choices=("init", "inspect", "attempt", "result"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--scope-id")
    parser.add_argument("--input")
    parser.add_argument("--input-sha256")
    parser.add_argument("--expected-head")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    refs = []
    try:
        need(not Path(args.output).exists(), "LEDGER_NEW_OUTPUT_REQUIRED")
        if args.command == "init":
            result = initialize(args.root, args.scope_id)
        elif args.command == "inspect":
            result = inspect(args.root)
        else:
            payload, ref = io.read_snapshot(args.input, args.input_sha256, max_stored=65536, max_json=65536)
            refs.append(ref)
            io.verify_snapshots(refs)
            result = _append(args.root, "ATTEMPT" if args.command == "attempt" else "RESULT", payload, args.expected_head)
        io.publish_new(args.output, result, validate=lambda: io.verify_snapshots(refs))
        print(json.dumps(dict(status="RECORDED" if args.command != "inspect" else "INSPECTED", output=args.output,
                              source_authority=False, offline_or_other_scope_attempts="UNKNOWN")))
        return 0
    except Exception as error:
        reason = str(error)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,160}", reason) is None:
            reason = "LEDGER_REJECTED_OR_UNAVAILABLE"
        print(json.dumps(dict(status="REJECTED", reason=reason, mutation_may_have_committed="INSPECT_SELECTED_LEDGER_DO_NOT_RETRY")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
