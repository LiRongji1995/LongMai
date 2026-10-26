"""Explicit compact V2 validation with one logical consumption per artifact.

Native computation, compressed storage, cleanup ownership, original terminal
checks and all budgets are inherited without modifying their source or globals.
"""
from pathlib import Path
import sys
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_compact_core as legacy
from scripts import codex_verification_v2 as verification
for _name, _value in vars(legacy).items():
    if not _name.startswith("__") and _name not in {"ROOT", "legacy", "verification"}:
        globals()[_name] = _value
VERSION = "CODEX_COMPACT_NATIVE_CORE_REUSE_V2"
PROFILE = "FULL_CORE_COMPACT_VERIFY_V2"


def _snapshot(profile):
    snapshot = legacy._snapshot(profile)
    snapshot["verification_v2_sources"] = [original._reference(Path(path).resolve())
        for path in (__file__, verification.__file__)]
    snapshot["verification_profile"] = PROFILE
    return snapshot


def _verify_logical_core(archive, logical, manifest, summary, request, meter, *, source_read=None):
    """Run the original archive semantic checks over exact logical bytes only.

    This adapter is deliberately confined to the eight historical output names.
    It cannot read market files or mint any source or native-owner capability.
    """
    from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
    from stockfish_core.yaogu import strategy_experiments
    from stockfish_core.yaogu.contracts import MarketInput, SourceReceipt, digest
    from stockfish_core.yaogu.short_horizon_participation import short_horizon_source_identity
    role_by_path = {ref["path"]: role for role, ref in logical.items()}
    need(len(role_by_path) == 8, "COMPACT_CORE_UNIQUE_LOGICAL_PATHS_REQUIRED")

    class LogicalReader:
        def check(self):
            meter.check()
            need(bridge._rss() <= LIMITS["self_rss_bytes"], "COMPACT_CORE_VERIFICATION_RSS_LIMIT")

        def read(self, path, expected):
            role = role_by_path.get(str(path))
            need(role is not None and expected == logical[role], "COMPACT_CORE_LOGICAL_READER_SCOPE")
            self.check()
            return archive.read_artifact(role, max_bytes=128 * MIB)

    reader = LogicalReader()

    def document(role):
        raw = reader.read(logical[role]["path"], logical[role])
        return verification.decompress_document(raw, reader, charge=meter.charge)

    need(manifest.get("schema_version") == "PREMARKET_NATIVE_CORE_V1" and manifest.get("status") == "PASS"
         and manifest.get("source_capability_serialized") is False
         and manifest.get("source_database_written") is False, "COMPACT_CORE_NATIVE_MANIFEST_ROLE")
    source = document("input.json.gz")
    need(source.get("history_artifact_ref") is None and source.get("history_artifact_hash") is None
         and not any(row.get("historical_time_proof") is not None for row in source["receipts"]),
         "COMPACT_CORE_INLINE_INPUT_REQUIRED")
    need(source["coverage_type"] == request["coverage_type"]
         and source["observation_date"] == request["observation_date"], "COMPACT_CORE_INPUT_REQUEST_CONTEXT")
    input_hash = bridge.digest_json_stream(source, checkpoint=reader.check)
    source_receipts = {row["receipt_id"]: row for row in source["receipts"]}
    native_receipts = {row["receipt_id"]: row for row in manifest["source_receipts"]}
    need(len(source_receipts) == len(source["receipts"]) == len(native_receipts)
         == len(manifest["source_receipts"]) == len(request["captures"])
         and source_receipts == native_receipts, "COMPACT_CORE_EXACT_INPUT_RECEIPTS_REQUIRED")

    # Reuse the original read-only trio validator, never a source-capability
    # factory. Matching the two output receipt lists alone would allow both to
    # be replaced by the same unrelated list while the request stayed intact.
    source_refs = {ref["path"]: ref for capture in request["captures"]
        for ref in [capture[key] for key in ("result", "raw", "receipt")] + capture.get("producer_files", [])}
    typed_receipts = {rid: SourceReceipt.model_validate(row) for rid, row in source_receipts.items()}

    class SourceReader:
        def __init__(self):
            self.locators = []

        def read(self, path, expected, *, limit=16 * MIB, retain=True):
            reader.check()
            need(source_refs.get(str(path)) == expected, "COMPACT_CORE_SOURCE_READER_SCOPE")
            if source_read is None:
                reference, raw = original._read_file(path, min(limit, 16 * MIB), retain=True)
            else:
                # Explicit versioned capture coordinator only: physical identity
                # remains the exact request reference, decoded bytes are passed
                # through the unchanged original trio's strict JSON validator.
                raw, reference = source_read(path, expected=expected, maximum=min(limit, 16 * MIB))
                need(type(raw) in (bytes, bytearray) and len(raw) <= min(limit, 16 * MIB),
                     "COMPACT_CORE_DECODED_SOURCE_LIMIT")
            if source_read is None and original._ACTIVE_BUDGET.get() is not meter:
                meter.charge(len(raw))
            need(reference == expected, "COMPACT_CORE_ORIGINAL_TRIO_CHANGED")
            reader.check()
            return raw

    checked_receipts, source_reader = {}, SourceReader()
    for capture in request["captures"]:
        _, typed, _ = bridge._trio(source_reader, capture, source_refs, typed_receipts, {},
            request["coverage_type"], datetime.fromisoformat(request["cutoff"]))
        need(typed.receipt_id not in checked_receipts, "COMPACT_CORE_DUPLICATE_ORIGINAL_RECEIPT")
        checked_receipts[typed.receipt_id] = typed.model_dump(mode="json")
    need(checked_receipts == source_receipts, "COMPACT_CORE_EXACT_ORIGINAL_RECEIPTS_REQUIRED")

    # Qualification depends only on the saved security, receipt and calendar
    # evidence. Run that original rule on a small typed projection; do not
    # normalize, evaluate prices or select the strategy again during reuse.
    projection = MarketInput.model_validate({key: source[key] for key in
        ("coverage_type", "observation_date", "sessions", "universe", "receipts")} | {"bars": []})
    reader.check()
    qualification = strategy_experiments._prepare_context(projection,
        cutoff=datetime.fromisoformat(request["cutoff"])).qualification
    reader.check()
    del source, source_receipts, native_receipts
    del source_reader, typed_receipts, checked_receipts, source_refs
    gc.collect()
    run = document("canonical-run.json.gz")
    need(input_hash == run.get("input_hash") == manifest.get("input_hash") == summary["input_hash"],
         "COMPACT_CORE_INPUT_HASH_MISMATCH")
    need(run.get("content_hash") == manifest.get("canonical_content_hash") == summary["canonical_content_hash"],
         "COMPACT_CORE_CANONICAL_HASH_MISMATCH")
    need(run.get("decision_cutoff") == request["cutoff"]
         and run.get("target_session") == request["target_session"], "COMPACT_CORE_RUN_REQUEST_CONTEXT")
    need(original._clock(run["decision_cutoff"]) <= original._clock(run["generated_at"]),
         "COMPACT_CORE_RUN_CLOCK_ORDER")
    for key, expected in short_horizon_source_identity().items():
        if key in run:
            need(run[key] == expected, "COMPACT_CORE_FROZEN_STRATEGY_CHANGED:" + key)
    result = run["results"][original.STRATEGY]
    need(run.get("qualification") == result.get("qualification") == qualification
         and run.get("universe_hash") == digest(qualification), "COMPACT_CORE_ORIGINAL_QUALIFICATION_MISMATCH")
    eligible = qualification["eligible_ids"]
    need(len(eligible) == len(set(eligible)) == qualification["eligible"]
         and result.get("eligible_pool_ids") == eligible
         and len(result.get("items", [])) == len(eligible)
         and {row.get("instrument_id") for row in result["items"]} == set(eligible),
         "COMPACT_CORE_FULL_QUALIFIED_POOL_REQUIRED")
    bridge._validate_run(run, projection, manifest, logical, reader, input_hash)
    need(original._summary(run, manifest, request) == summary, "COMPACT_CORE_COMPLETE_SUMMARY_MISMATCH")
    del run, projection
    gc.collect()
    reader.check()
    archive.finish()


_compact_reader = SimpleNamespace(**vars(compact))
_compact_reader.CompactArchive = verification.single_pass_archive_class(compact)
_api = verification.bind_functions(legacy,
    ("_execute", "execute_compact_core", "_monitor_owned_job", "supervised_command", "main"),
    dict(VERSION=VERSION, PROFILE=PROFILE, _snapshot=_snapshot, compact=_compact_reader,
         _verify_logical_core=verification.timed("compact_semantic_validation", _verify_logical_core),
         subprocess=verification.isolated_subprocess(legacy.subprocess, __file__),
         __file__=__file__))


def execute_compact_core(*args, **kwargs):
    with verification.metrics_scope() as metrics:
        result = _api["execute_compact_core"](*args, **kwargs)
        result["verification_v2_metrics"] = metrics.report()
        return result


# CLI must call the instrumented public function in its isolated worker.
_main = verification.bind_functions(legacy, ("main",), dict(VERSION=VERSION, PROFILE=PROFILE,
    supervised_command=_api["supervised_command"], execute_compact_core=execute_compact_core,
    __file__=__file__))["main"]
supervised_command = _api["supervised_command"]
_monitor_owned_job = _api["_monitor_owned_job"]
main = _main

if __name__ == "__main__":
    raise SystemExit(main())
