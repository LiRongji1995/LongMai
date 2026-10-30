"""Explicit factor-source V3 on the existing daily request scope.

Only the execution source is selected per invocation. The actual registered
runtime owns its unchanged locks, journal, state, configuration and budgets.
This entry cannot resume or retry an existing failed request.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import FunctionType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_premarket_source_bundle_v3 as bundle
from scripts import codex_daily_v2 as daily
from scripts import codex_premarket as original
from scripts import codex_recovered_premarket_v1 as bounded
from scripts import codex_runtime_recovery_v1 as recovery
from scripts import codex_workspace as workspace

VERSION = "CODEX_FACTOR_CHECKPOINT_PREMARKET_V1"
need = original._need


class SourceGuard:
    def __init__(self, runtime, directory, manifest_sha256):
        self.runtime = runtime
        self.directory = Path(directory).expanduser().absolute()
        self.sha256 = manifest_sha256
        self.verified = bundle.verify(self.directory, self.sha256)
        raw, self.manifest = bundle.read(self.directory / bundle.MANIFEST)
        self.document = bundle.previous.strict_json(raw)
        need(self.manifest["sha256"] == self.sha256, "FACTOR_ENTRY_MANIFEST_CHANGED")
        need(self.document["source_root"] == runtime.profile["source_root"],
             "FACTOR_ENTRY_REGISTERED_SOURCE_ORIGIN_REQUIRED")
        self.entry = bundle.read(Path(__file__).absolute())[1]
        member = self.document["files"]["scripts/" + Path(__file__).name]
        need(member["sha256"] == self.entry["sha256"], "FACTOR_ENTRY_BUNDLE_GATEWAY_DIFFERS")
        self.snapshot = dict(schema_version=VERSION, bundle_root=str(self.directory),
            manifest=self.manifest, entry_sha256=self.entry["sha256"],
            registered_source_root=runtime.profile["source_root"],
            producer_hashes=self.document["producer_hashes"],
            source_selection_scope="THIS_INVOCATION_ONLY_NOT_REGISTRY_REPLACEMENT")
        self.check()

    def check(self):
        self.runtime.check_binding()
        need(bundle.verify(self.directory, self.sha256) == self.verified,
             "FACTOR_ENTRY_BUNDLE_VERIFICATION_CHANGED")
        need(bundle.read(self.directory / bundle.MANIFEST)[1] == self.manifest
             and bundle.read(Path(__file__).absolute())[1] == self.entry,
             "FACTOR_ENTRY_SOURCE_IDENTITY_CHANGED")


def prior_request(runtime, identity):
    """No new version, source, engine or key can hide a prior business request."""
    guard = getattr(runtime, "guard_ancestor_request", None)
    if guard is not None:
        guard(identity)
    directory = runtime.directory / "operations"
    if not directory.exists():
        need(not directory.is_symlink(), "FACTOR_ENTRY_OPERATION_DIRECTORY_ALIAS")
        return
    workspace._directory(directory)
    paths = sorted(directory.glob("*.json"))
    need(len(paths) <= 10000, "FACTOR_ENTRY_OPERATION_SCAN_LIMIT")
    for path in paths:
        record = workspace._read(path)
        other = record.get("identity", {})
        need(record.get("schema_version") == workspace.VERSION
             and record.get("scope") == runtime.scope
             and record.get("operation_id") == path.stem
             and path.stem == workspace._digest({"scope": runtime.scope, "identity": other}),
             "FACTOR_ENTRY_OPERATION_BINDING_INVALID")
        if other == identity:
            continue  # Original run_once permits RECORDED reuse only.
        if other.get("backend") != "premarket":
            continue
        same_key = other.get("request_key") == identity.get("request_key")
        same_day = (other.get("observation_date"), other.get("target_session")) == (
            identity.get("observation_date"), identity.get("target_session"))
        need(not same_key and not same_day, "FACTOR_ENTRY_EXISTING_REQUEST_RECONCILE_NO_RETRY")


class BundleRuntime(recovery.RecoveryRuntime):
    """Execution facade; the original object still owns all physical bindings."""
    def __init__(self, registered, guard):
        need(isinstance(registered, recovery.RecoveryRuntime), "FACTOR_ENTRY_ATTESTED_RUNTIME_REQUIRED")
        self.registered, self.guard = registered, guard
        self.profile = deepcopy(registered.profile)
        self.profile["source_root"] = str(guard.directory)
        for name in ("registry_root", "binding", "scope", "resources", "directory"):
            setattr(self, name, getattr(registered, name))
        self._active_identity = None

    def check_binding(self):
        self.guard.check()

    def guard_ancestor_request(self, identity):
        if isinstance(self.registered, recovery.RecoveryRuntime):
            self.registered.guard_ancestor_request(identity)

    @contextmanager
    def exclusive(self):
        # This is the real registered lock set, including recovery ancestors.
        with self.registered.exclusive():
            self.check_binding()
            if self._active_identity is not None:
                prior_request(self, self._active_identity)
            try:
                yield
            finally:
                self.check_binding()

    def preflight(self, **kwargs):
        self.check_binding()
        return self.registered.preflight(**kwargs)

    def run_once(self, identity, action, validate):
        need(self._active_identity is None, "FACTOR_ENTRY_RUNTIME_REENTRANCY_FORBIDDEN")
        self._active_identity = deepcopy(identity)
        try:
            return workspace.Runtime.run_once(self, identity, action, validate)
        finally:
            self._active_identity = None


def bound_functions(guard):
    """Reuse frozen code with private globals; never mutate imported modules."""
    def snapshot(prepared):
        guard.check()
        return {**daily._snapshot(prepared), "factor_source_epoch": deepcopy(guard.snapshot)}

    def same(prepared, expected):
        need(snapshot(prepared) == expected, "FACTOR_ENTRY_IMPLEMENTATION_OR_CONFIG_CHANGED")

    validate = FunctionType(daily._validate.__code__,
        {**daily._validate.__globals__, "_same": same}, daily._validate.__name__)
    api = SimpleNamespace(VERSION=daily.VERSION, _snapshot=snapshot, _same=same,
                          _validate=validate, _prior_request=prior_request)
    run = FunctionType(bounded.run.__code__, {**bounded.run.__globals__, "daily": api},
                       bounded.run.__name__)
    return run


def readonly_plan(runtime, args):
    """New finite source profile admission, separate from full-source readiness."""
    observation, target = str(args.observation_date), str(args.target_session)
    need(date.fromisoformat(observation).isoformat() == observation
         and date.fromisoformat(target).isoformat() == target and observation < target,
         "FACTOR_ENTRY_ORDERED_SESSIONS_REQUIRED")
    need(re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", args.request_key) is not None,
         "FACTOR_ENTRY_REQUEST_KEY_REQUIRED")
    identity = dict(backend="premarket", request_key=args.request_key,
                    observation_date=observation, target_session=target)
    prepared = original._prepared(runtime.profile)
    config = bounded.configuration(runtime, args.profile)
    with runtime.exclusive():
        prior_request(runtime, identity)
        gate = bounded.calendar_gate(prepared, config, observation, target, bounded.Budget(config["limits"]))
        capacity = runtime.preflight(expected_growth_bytes=max(
            runtime.profile["storage"]["expected_growth_bytes"], config["limits"]["output_bytes"]))
        need(capacity["allowed"], "FACTOR_ENTRY_REGISTERED_CAPACITY_BLOCKED")
        full = daily._plan(runtime)
        runtime.check_binding()
    return dict(entry_version=VERSION, status="BOUNDED_NEW_SOURCE_PROFILE_CHECKED",
        observation_date=observation, target_session=target, request_key=args.request_key,
        source_bundle=runtime.guard.snapshot, original_full_graph_preflight=full,
        calendar_gate=gate, capacity=workspace.Runtime._capacity_summary(capacity),
        limits=config["limits"], explicit_collection_profile=args.profile,
        full_source_graph_authenticated=bool(full.get("execution_admitted")),
        collection_admission_scope="ORIGINAL_PER_REQUEST_AND_STAGE_BUDGETS_RECHECKED_AT_EXECUTION",
        retry_authorized=False, request_claimed=False, worker_started=False, network_calls=0,
        complete_daily_delivery=False, market_edge="NOT_ESTABLISHED")


def execute(registered, guard, args):
    need(ROOT == guard.directory and Path(sys.executable).absolute() ==
         Path(registered.profile["python"]).absolute(), "FACTOR_ENTRY_BUNDLE_PROCESS_REQUIRED")
    runtime = BundleRuntime(registered, guard)
    if args.command == "premarket-plan":
        return 0, readonly_plan(runtime, args)
    run = bound_functions(guard)
    code, result = run(runtime, args)
    guard.check()
    return code, {**result, "entry_version": VERSION, "source_bundle": guard.snapshot,
                  "registered_runtime_changed": False, "retry_authorized": False}


def parser():
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    result.add_argument("--runtime", required=True)
    result.add_argument("--registry-root", type=Path)
    result.add_argument("--bundle", type=Path, required=True)
    result.add_argument("--manifest-sha256", required=True)
    result.add_argument("--profile", choices=[bounded.PROFILE], required=True)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("premarket-plan", "premarket"):
        sub = commands.add_parser(name, allow_abbrev=False)
        sub.add_argument("--observation-date", type=date.fromisoformat, required=True)
        sub.add_argument("--target-session", type=date.fromisoformat, required=True)
        sub.add_argument("--request-key", required=True)
    return result


def main(argv=None):
    selected = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(selected)
    try:
        registered = recovery.load_runtime(args.runtime, args.registry_root)
        need(isinstance(registered, recovery.RecoveryRuntime)
             and registered.profile.get("backend") == "premarket", "FACTOR_ENTRY_ATTESTED_RUNTIME_REQUIRED")
        guard = SourceGuard(registered, args.bundle, args.manifest_sha256)
        python = Path(registered.profile["python"]).absolute()
        if ROOT != guard.directory or Path(sys.executable).absolute() != python or \
                sys.pycache_prefix is None or Path(sys.pycache_prefix).exists():
            with tempfile.TemporaryDirectory(prefix="stockfish-factor-source-v3-") as directory:
                environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
                for key in ("PYTHONHOME", "PYTHONPATH"):
                    environment.pop(key, None)
                command = [str(python), "-I", "-B", "-X", "pycache_prefix=" +
                    str(Path(directory) / "absent"), str(guard.directory / "scripts" / Path(__file__).name), *selected]
                code = subprocess.call(command, cwd=guard.directory, env=environment)
                guard.check()
                return code
        code, result = execute(registered, guard, args)
    except KeyboardInterrupt:
        code, result = 130, dict(status="INTERRUPTED", reason="USER_INTERRUPTED")
    except Exception as error:
        code, result = 2, dict(status="BLOCKED_OR_FAILED", reason=daily.legacy._safe_reason(error))
    print(json.dumps({**result, "entry_version": VERSION, "command_exit_code": code},
                     ensure_ascii=False, allow_nan=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
