"""One authority-bound, offline native five-horizon evaluation; no new selector.

Only original archive authentication and the frozen evaluator produce facts.
The parent PREMARKET receipt, two original archives and every published product
remain necessary. Failed or unknown requests are never retried by this module.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
VERSION = "ACTIVE_OUTCOME_PIPELINE_V1"
BINDING = VERSION + "_BINDING"


def need(value, code):
    if not value:
        raise ValueError(code)


def archive_admission(job_dir, expected_final_sha256, session):
    """Validate the shared envelope before the original core bridge checks it.

    No carrier, schema string, timestamp or hash is rewritten. The generated
    bridge accepts precisely the envelope schema verified here; its complete
    source/raw/normalization/run/export assertions remain in force.
    """
    from stockfish_core.yaogu import premarket_runtime_v1 as runtime
    from scripts import codex_premarket_shared_v1 as shared
    raw = session.read(Path(job_dir)/"final-control.json", expected_final_sha256, limit=65536)
    value = json.loads(raw)
    schema = value.get("schema_version")
    if schema == "STANDALONE_PREMARKET_WORKFLOW_V1":
        return schema
    need(schema == shared.VERSION, "ACTIVE_ARCHIVE_SCHEMA_NOT_DECLARED")
    remaining = session.max_total_read_bytes-session.read_bytes
    seconds = session.max_seconds-(time.monotonic()-session.started)
    need(remaining > 0 and seconds > 0, "ACTIVE_ARCHIVE_VALIDATION_BUDGET_EXHAUSTED")
    meter = runtime.Budget({**runtime.DEFAULT_LIMITS, "capture_read_bytes": remaining,
        "max_seconds": seconds, "self_rss_bytes": session.max_rss_bytes})
    # The shared archive session accounts these same reads below. Do not also
    # charge the active workflow heartbeat as an independent capture phase.
    meter._active_telemetry = True
    try:
        with meter.meter_files():
            verified = shared.verify_job(job_dir)
    finally:
        # These are actual returned reads, including failed compatibility checks.
        session.read_bytes += sum(meter.metrics["returned_read_bytes"].values())
        need(session.read_bytes <= session.max_total_read_bytes, "ACTIVE_ARCHIVE_VALIDATION_READ_BUDGET_EXCEEDED")
        session.check()
    need(verified.get("archived_bytes_verified") is True and verified.get("owned_derivatives_verified") is True,
         "ACTIVE_SHARED_ARCHIVE_NOT_VERIFIED")
    completion = json.loads(session.read(value["completion"]["path"], value["completion"], limit=16*1024**2))
    sessions = value.get("expected_sessions")
    need(value.get("execution_engine") in {"original", "shared"}
         and completion.get("execution_engine") == value["execution_engine"]
         and isinstance(sessions, list) and len(sessions) == 3
         and completion.get("expected_sessions") == sessions
         and sessions[:2] == [completion.get("observation_date"), completion.get("target_session")],
         "ACTIVE_SHARED_ARCHIVE_ENGINE_OR_SESSION_BINDING")
    registry = json.loads(session.read(value["code_registry"]["path"], value["code_registry"], limit=16*1024**2))
    def account(count):
        session.read_bytes += count
        need(session.read_bytes <= session.max_total_read_bytes, "ACTIVE_ARCHIVE_VALIDATION_READ_BUDGET_EXCEEDED")
        session.check()
    need(registry == shared.code_refs(read_meter=account), "ACTIVE_SHARED_ARCHIVE_EXACT_SOURCE_CLOSURE_REQUIRED")
    from scripts.business_authority_v1 import digest
    approved = getattr(session, "_active_shared_registry_digests", set())
    approved.add(digest(registry))
    session._active_shared_registry_digests = approved
    return schema


def archive_source_path_allowed(session, registry, name, reference, root):
    """Additional script paths require the exact independently verified registry."""
    from scripts import active_business_gateway_v1 as gateway
    from scripts.business_authority_v1 import digest
    if digest(registry) not in getattr(session, "_active_shared_registry_digests", set()):
        return False
    with gateway._control():
        manifest = gateway._manifest()
    return (Path(root) == gateway._SOURCE_ROOT and name.startswith("scripts/") and name.endswith(".py")
        and Path(reference["path"]) == Path(root)/name
        and manifest["output_sha256"].get(name) == reference["sha256"])


def _locator(job, final_sha256):
    path = Path(job).absolute()
    need(path.resolve(strict=True) == path, "ACTIVE_OUTCOME_CANONICAL_ARCHIVE_REQUIRED")
    need(isinstance(final_sha256, str) and len(final_sha256) == 64
         and all(c in "0123456789abcdef" for c in final_sha256), "ACTIVE_OUTCOME_FINAL_SHA_REQUIRED")
    return {"job": str(path), "final_sha256": final_sha256}


def _parents(runtime, parent_business_id, outcome_business_id, signal, outcome, reader):
    from scripts import active_business_gateway_v1 as gateway
    from scripts import durable_stage_metrics_v1 as metrics
    with gateway._control():
        parent = runtime.authority.inspect(parent_business_id)
    need(parent is not None and parent["state"] == "RECORDED" and parent["budget_known"]
         and parent["business"]["operation"] == "PREMARKET", "ACTIVE_OUTCOME_RECORDED_PARENT_REQUIRED")
    receipt = parent.get("receipt") or {}
    need(receipt.get("schema_version") == "DAILY_DELIVERY_CONTRACT_V1_BINDING"
         and receipt.get("identity") == metrics.identity(parent), "ACTIVE_OUTCOME_NATIVE_SIGNAL_BINDING_REQUIRED")
    signal_final, signal_ref = reader.read(Path(signal["job"])/"final-control.json", control=True)
    need(signal_ref["sha256"] == signal["final_sha256"] and receipt.get("job_final") == signal_ref,
         "ACTIVE_OUTCOME_PARENT_FINAL_MISMATCH")
    run_ref = receipt.get("canonical_run")
    need(isinstance(run_ref, dict), "ACTIVE_OUTCOME_PARENT_CANONICAL_RUN_REQUIRED")
    completion, _ = reader.read(signal_final["completion"]["path"], expected=signal_final["completion"], control=True)
    need(completion.get("core_artifacts", {}).get("canonical-run.json.gz") == run_ref,
         "ACTIVE_OUTCOME_PARENT_CORE_ROLE_MISMATCH")
    final, ref = reader.read(Path(outcome["job"])/"final-control.json", control=True)
    need(ref["sha256"] == outcome["final_sha256"], "ACTIVE_OUTCOME_INPUT_FINAL_MISMATCH")
    with gateway._control():
        input_parent = runtime.authority.inspect(outcome_business_id)
    need(input_parent is not None and input_parent["state"] == "RECORDED" and input_parent["budget_known"]
         and input_parent["business"]["operation"] == "PREMARKET"
         and (input_parent.get("receipt") or {}).get("job_final") == ref,
         "ACTIVE_OUTCOME_RECORDED_INPUT_PARENT_REQUIRED")
    need(input_parent["receipt"].get("schema_version") == "DAILY_DELIVERY_CONTRACT_V1_BINDING"
         and input_parent["receipt"].get("identity") == metrics.identity(input_parent),
         "ACTIVE_OUTCOME_NATIVE_INPUT_BINDING_REQUIRED")
    completed, _ = reader.read(final["completion"]["path"], expected=final["completion"], control=True)
    need((input_parent.get("receipt") or {}).get("canonical_run") ==
         completed.get("core_artifacts", {}).get("canonical-run.json.gz"),
         "ACTIVE_OUTCOME_INPUT_PARENT_CORE_ROLE_MISMATCH")
    manifest_ref = completed.get("core_artifacts", {}).get("manifest.json")
    need(isinstance(manifest_ref, dict), "ACTIVE_OUTCOME_INPUT_MANIFEST_REQUIRED")
    manifest, _ = reader.read(manifest_ref["path"], expected=manifest_ref, control=True)
    input_hash = manifest.get("input_hash")
    need(isinstance(input_hash, str) and len(input_hash) == 64
         and all(c in "0123456789abcdef" for c in input_hash), "ACTIVE_OUTCOME_INPUT_HASH_REQUIRED")
    need(isinstance(completion.get("run_id"), str), "ACTIVE_OUTCOME_SIGNAL_RUN_REQUIRED")
    reader.terminal()
    return parent, input_parent, run_ref, completion["run_id"], "market_"+input_hash


def execute(runtime, *, parent_business_id, signal_job, signal_final_sha256,
            outcome_job, outcome_final_sha256, destination, request_key, outcome_business_id=None,
            daily_archives=None, daily_archives_sha256=None):
    from scripts import active_business_gateway_v1 as gateway
    from scripts import durable_stage_metrics_v1 as metrics
    from scripts import codex_delivery_audit_v1 as delivery
    from scripts import codex_p0 as io
    from stockfish_core.yaogu import premarket_workflow_v1 as workflow
    from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
    from stockfish_core.yaogu import short_horizon_outcomes_v1 as outcomes
    runtime.check_binding()
    signal = _locator(signal_job, signal_final_sha256)
    later = _locator(outcome_job, outcome_final_sha256)
    if outcome_business_id is None:
        need(later == signal, "ACTIVE_OUTCOME_INPUT_BUSINESS_ID_REQUIRED")
        outcome_business_id = parent_business_id
    destination = Path(destination).absolute()
    need(destination.parent.resolve(strict=True) == destination.parent, "ACTIVE_OUTCOME_CANONICAL_OUTPUT_PARENT_REQUIRED")
    protected = [Path(signal["job"]), Path(later["job"]), Path(runtime.profile["source_root"])]
    if getattr(runtime, "directory", None) is not None:
        protected.append(Path(runtime.directory))
    need(not any(destination.is_relative_to(path) for path in protected), "ACTIVE_OUTCOME_OUTPUT_OVERLAPS_ORIGINAL_EVIDENCE")
    with gateway._control():
        config = workflow.load_config(runtime.profile["config"])
    limits = config["limits"]
    need(type(limits.get("max_seconds")) in (int,float) and limits["max_seconds"] > 0,
         "ACTIVE_OUTCOME_FINITE_ORIGINAL_BUDGET_REQUIRED")
    fixture = config.get("capture", {}).get("allow_test_fixture") is True
    if fixture:
        need(all(any(part.startswith("TEST_FIXTURE") for part in Path(path).parts)
                 for path in (destination, signal["job"], later["job"])), "ACTIVE_OUTCOME_FIXTURE_PATH_REQUIRED")
    read_limit = min(limits["core_read_bytes"], limits["supervisor_read_bytes"])
    reader = delivery._Reader(min(read_limit, 16*1024**2), min(limits["max_seconds"], 30))
    started = time.monotonic()
    parent, input_parent, run_ref, run_id, input_id = _parents(runtime, parent_business_id, outcome_business_id, signal, later, reader)
    daily_records, daily_parents = [], []
    if daily_archives is not None:
        if isinstance(daily_archives, (str, Path)):
            need(daily_archives_sha256 is not None, "ACTIVE_OUTCOME_DAILY_DESCRIPTOR_SHA_REQUIRED")
            descriptor, _ = reader.read(daily_archives, expected=daily_archives_sha256, control=True)
            need(type(descriptor) is dict and set(descriptor) == {"archives"}, "ACTIVE_OUTCOME_DAILY_DESCRIPTOR_SCHEMA")
            daily_archives = descriptor["archives"]
        else:
            need(daily_archives_sha256 is None, "ACTIVE_OUTCOME_DAILY_DESCRIPTOR_PATH_REQUIRED")
        need(type(daily_archives) is list and 1 <= len(daily_archives) <= 32,
             "ACTIVE_OUTCOME_BOUNDED_DAILY_ARCHIVES_REQUIRED")
        seen = set()
        for item in daily_archives:
            need(type(item) is dict and set(item) == {"business_id", "job", "final_sha256"},
                 "ACTIVE_OUTCOME_DAILY_ARCHIVE_FIELDS_REQUIRED")
            locator = _locator(item["job"], item["final_sha256"])
            need(item["business_id"] not in seen, "ACTIVE_OUTCOME_DUPLICATE_DAILY_BUSINESS")
            seen.add(item["business_id"])
            need(not destination.is_relative_to(Path(locator["job"])), "ACTIVE_OUTCOME_OUTPUT_OVERLAPS_ORIGINAL_EVIDENCE")
            if fixture:
                need(any(part.startswith("TEST_FIXTURE") for part in Path(locator["job"]).parts),
                     "ACTIVE_OUTCOME_FIXTURE_PATH_REQUIRED")
            old_parent, daily_parent, old_run_ref, old_run_id, daily_input_id = _parents(
                runtime, parent_business_id, item["business_id"], signal, locator, reader)
            need(old_parent == parent and old_run_ref == run_ref and old_run_id == run_id,
                 "ACTIVE_OUTCOME_SIGNAL_CHANGED_DURING_DAILY_PREFLIGHT")
            need(daily_parent["business"]["observation_date"] > parent["business"]["observation_date"]
                 and daily_parent["business"]["strategy_id"] == parent["business"]["strategy_id"],
                 "ACTIVE_OUTCOME_DAILY_DATE_OR_STRATEGY_CONFLICT")
            daily_records.append({**locator, "business_id":item["business_id"], "input_id":daily_input_id})
            daily_parents.append(daily_parent)
        paired = sorted(zip(daily_records, daily_parents), key=lambda pair:pair[1]["business"]["observation_date"])
        daily_records, daily_parents = [pair[0] for pair in paired], [pair[1] for pair in paired]
        need(daily_records[-1] == {**later, "business_id":outcome_business_id, "input_id":input_id}
             and len({row["business"]["observation_date"] for row in daily_parents}) == len(daily_parents),
             "ACTIVE_OUTCOME_PRIMARY_MUST_BE_UNIQUE_LAST_DAILY_SOURCE")
    else:
        need(daily_archives_sha256 is None, "ACTIVE_OUTCOME_DAILY_DESCRIPTOR_PATH_REQUIRED")
    source_input_ids = sorted({row["input_id"] for row in daily_records}) if daily_records else [input_id]
    identity = {"backend":"active-outcome", "run_id":run_id, "input_ids":source_input_ids, "request_key":request_key}
    wall_accounting = {"charged": 0.0}

    def action():
        value = gateway.current("worker")
        owner, claim = value["owner"], value["claim"]
        with gateway._control():
            owner.charge(claim, {"supervisor_read_bytes":reader.returned_bytes})
            reservation = owner.consume_once(value["capability"], stage="worker",
                effect_kind="WORKER_START", stable_effect_id="BUSINESS_WORKER")
        need(not destination.exists() and not destination.is_symlink(), "ACTIVE_OUTCOME_OUTPUT_EXISTS_PRESERVED")
        destination.mkdir(mode=0o700)
        journal = metrics.StageJournal(destination/"events", claim, role="WORKER", test_fixture=fixture)
        journal.record("METRICS_SNAPSHOT", details={"operation":"OUTCOME", "execution":"SYNCHRONOUS_NATIVE_EVALUATOR"})
        token = gateway._JOURNAL.set(journal)
        session = None
        charged = 0
        def checkpoint(*, force=False):
            nonlocal charged
            delta = {}
            if session is not None and session.read_bytes > charged:
                delta["core_read_bytes"] = session.read_bytes-charged
            elapsed = time.monotonic()-started
            if force or delta or elapsed-wall_accounting["charged"] >= .25:
                delta["outcome_wall_seconds"] = elapsed-wall_accounting["charged"]
            if delta:
                with gateway._control():
                    owner.charge(claim, delta)
                wall_accounting["charged"] = elapsed
            if session is not None:
                charged = session.read_bytes
            need(time.monotonic()-started <= limits["max_seconds"], "ACTIVE_OUTCOME_TOTAL_TIME_BUDGET_EXCEEDED")
        def counters():
            return {"returned_read_bytes": {"core": session.read_bytes}}
        try:
            session = bridge.ArchiveVerificationSession(checkpoint=checkpoint,
                max_total_read_bytes=limits["core_read_bytes"], max_unique_bytes=limits["core_read_bytes"],
                max_seconds=limits["max_seconds"], max_rss_bytes=limits["self_rss_bytes"])
            with journal.stage("source_revalidation", counters=counters):
                signal_cap = bridge.authenticate_archive(signal["job"], expected_final_sha256=signal["final_sha256"],
                    session=session, retain_input=True, allow_test_fixture=fixture)
                input_cap = bridge.authenticate_archive(later["job"], expected_final_sha256=later["final_sha256"],
                    session=session, retain_input=True, allow_test_fixture=fixture)
                signal_state, input_state = bridge._state(signal_cap), bridge._state(input_cap)
                run = bridge._verified_run(signal_state)
                bundle = input_state["retained_input"]
                need(run["run_id"] == run_id and input_state["input_index"]["input_id"] == input_id
                     and signal_state["run_reference"] == run_ref, "ACTIVE_OUTCOME_AUTHENTICATED_IDENTITY_CHANGED")
                for state, source in ((signal_state, parent), (input_state, input_parent)):
                    need(all(state["audit"][key] == source["business"][key]
                             for key in ("observation_date", "target_session"))
                         and source["business"]["strategy_id"] == run["strategy_id"],
                         "ACTIVE_OUTCOME_NATIVE_BUSINESS_IDENTITY_CONFLICT")
                composite_manifest = None
                if daily_records:
                    from scripts.active_outcome_input_v1 import compose
                    capabilities = []
                    for record, source in zip(daily_records, daily_parents):
                        cap = bridge.authenticate_archive(record["job"], expected_final_sha256=record["final_sha256"],
                            session=session, retain_input=True, allow_test_fixture=fixture)
                        state = bridge._state(cap)
                        need(state["input_index"]["input_id"] == record["input_id"]
                             and state["run_reference"] == source["receipt"]["canonical_run"]
                             and session.files.get(str(Path(record["job"])/"final-control.json")) == source["receipt"]["job_final"],
                             "ACTIVE_OUTCOME_DAILY_SOURCE_ORIGINAL_MISMATCH")
                        capabilities.append(cap)
                    bundle, composite_manifest = compose(run, signal_cap, capabilities, session)
                    need(composite_manifest["source_input_ids"] == source_input_ids,
                         "ACTIVE_OUTCOME_COMPOSITE_SOURCE_INPUTS_CHANGED")
                else:
                    signal_day = run["observation_date"]
                    prefix = tuple(str(day) for day in signal_state["retained_input"].sessions if str(day) <= signal_day)
                    need(not prefix or tuple(str(day) for day in bundle.sessions if prefix[0] <= str(day) <= signal_day) == prefix,
                         "ACTIVE_OUTCOME_SIGNAL_CALENDAR_PREFIX_CONFLICT")
            evaluated_at = datetime.now(timezone.utc)
            with journal.stage("evaluate_five_horizons", counters=counters):
                products = outcomes.evaluate(run, bundle, evaluated_at, horizons=(1,2,3,5,10), checkpoint=session.check)
                if composite_manifest is not None:
                    need(composite_manifest["input_id"] == products["outcomes"]["outcome_input_id"],
                         "ACTIVE_OUTCOME_COMPOSITE_PRODUCT_ID_MISMATCH")
                session.terminal()
                checkpoint()
            with journal.stage("publish_products", counters=counters):
                refs = {}
                need(sum(len(metrics.encoded(product)) for product in products.values()) <= limits["output_bytes"],
                     "ACTIVE_OUTCOME_OUTPUT_BUDGET_EXCEEDED")
                product_reader = delivery._Reader(min(limits["output_bytes"]*3, limits["supervisor_read_bytes"]),
                                                  max(.001, limits["max_seconds"]-(time.monotonic()-started)))
                for kind, product in products.items():
                    path = destination/(kind+".json")
                    need(len(metrics.encoded(product)) <= limits["output_bytes"], "ACTIVE_OUTCOME_OUTPUT_BUDGET_EXCEEDED")
                    io.publish_new(path, product)
                    saved, ref = product_reader.read(path, control=True)
                    need(saved == product, "ACTIVE_OUTCOME_PRODUCT_PUBLICATION_CHANGED")
                    refs[kind] = ref
                product_reader.terminal()
                with gateway._control():
                    owner.charge(claim, {"supervisor_read_bytes":product_reader.returned_bytes})
                session.terminal()
                checkpoint()
                runtime.check_binding()
                with gateway._control():
                    need(owner.inspect(parent_business_id) == parent, "ACTIVE_OUTCOME_PARENT_CHANGED")
                    need(owner.inspect(outcome_business_id) == input_parent, "ACTIVE_OUTCOME_INPUT_PARENT_CHANGED")
                    for record, source in zip(daily_records, daily_parents):
                        need(owner.inspect(record["business_id"]) == source, "ACTIVE_OUTCOME_DAILY_PARENT_CHANGED")
            writer = journal.finish("COMPLETE")
            io.publish_new(destination/"writer-receipt.json", writer)
            verifier = delivery._Reader(2*1024**2, 10)
            saved_writer, writer_ref = verifier.read(destination/"writer-receipt.json", control=True)
            need(saved_writer == writer, "ACTIVE_OUTCOME_WRITER_CHANGED")
            verifier.terminal()
            audit = metrics.audit_journal(journal.root)
            need(sum(p.stat().st_size for p in destination.rglob("*") if p.is_file()) <= limits["output_bytes"],
                 "ACTIVE_OUTCOME_TOTAL_OUTPUT_BUDGET_EXCEEDED")
            checkpoint(force=True)
            with gateway._control():
                owner.charge(claim, {"supervisor_read_bytes":verifier.returned_bytes+audit["read_bytes"]})
                owner.complete_effect(value["capability"], reservation, receipt={"status":"COMPLETE","revision_id":products["outcomes"]["revision_id"]})
            product = products["outcomes"]
            return {"schema_version":BINDING, "execution_id":claim["attempt_id"],
                "parent_business_id":parent_business_id, "signal_archive":signal, "outcome_archive":later,
                "outcome_source_business_id":outcome_business_id,
                **({"outcome_daily_archives":daily_records, "composite_input_manifest":composite_manifest,
                    "source_input_ids":source_input_ids} if daily_records else {}),
                "product":refs["outcomes"], "products":refs, "run_id":run_id, "input_id":product["outcome_input_id"],
                "revision_id":product["revision_id"], "evaluated_at":product["evaluated_at"],
                "outcome_product_hash":metrics.digest(product), "identity":metrics.identity(claim),
                "journal_directory":str(journal.root), "journal_terminal_event_hash":audit["terminal_event_hash"],
                "journal_evidence_hash":metrics.digest(audit["evidence"]), "writer_receipt":writer,
                "writer_receipt_reference":writer_ref, "native_execution_completed":True,
                "verification_budget":session.audit(), "test_fixture":fixture,
                "publication_tail_cost":"UNKNOWN_NOT_OBSERVED", "source_authority_restored":False,
                "historical_pit_verified":False, "market_edge":"NOT_ESTABLISHED"}
        except BaseException:
            if not journal.closed:
                try: journal.finish("FAILED")
                except BaseException: pass  # Original exception and partial tail remain.
            raise
        finally:
            gateway._JOURNAL.reset(token)

    def validate(receipt):
        need(receipt.get("schema_version") == BINDING and receipt.get("run_id") == run_id
             and receipt.get("parent_business_id") == parent_business_id
             and receipt.get("source_input_ids", [receipt.get("input_id")]) == source_input_ids,
             "ACTIVE_OUTCOME_RECEIPT_BINDING_REQUIRED")
        check = delivery._Reader(min(limits["supervisor_read_bytes"], 256*1024**2), min(limits["max_seconds"], 60))
        saved, _ = check.read(receipt["product"]["path"], expected=receipt["product"], control=True)
        need(metrics.digest(saved) == receipt["outcome_product_hash"], "ACTIVE_OUTCOME_SAVED_PRODUCT_CHANGED")
        need(saved.get("outcome_input_id") == receipt["input_id"], "ACTIVE_OUTCOME_PRODUCT_INPUT_BINDING_CHANGED")
        check.terminal()
        context = gateway._CURRENT.get()
        if context is not None:
            with gateway._control():
                elapsed = time.monotonic()-started
                context["owner"].charge(context["claim"], {"supervisor_read_bytes":check.returned_bytes,
                    "outcome_wall_seconds":elapsed-wall_accounting["charged"]})
                wall_accounting["charged"] = elapsed
        return {"verified":True, "source_reauthenticated_now":False, "saved_product_bound":True,
                "validation_returned_read_bytes":check.returned_bytes}
    return gateway.run_once(runtime, identity, action, validate)


def main(argv=None):
    from scripts import active_business_gateway_v1 as gateway
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--parent-business-id", required=True)
    parser.add_argument("--signal-job", type=Path, required=True)
    parser.add_argument("--signal-final-sha256", required=True)
    parser.add_argument("--outcome-job", type=Path, required=True)
    parser.add_argument("--outcome-business-id")
    parser.add_argument("--outcome-final-sha256", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--request-key", required=True)
    parser.add_argument("--daily-archives", type=Path)
    parser.add_argument("--daily-archives-sha256")
    args = parser.parse_args(argv)
    runtime = gateway.load_runtime(args.runtime, args.registry_root)
    result = execute(runtime, **{key:value for key,value in vars(args).items() if key not in {"runtime","registry_root"}})
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
