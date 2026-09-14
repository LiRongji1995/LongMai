"""TEST_FIXTURE: independent chronology regressions against actual proof module.

No Proof constructor, database, provider call, asset read, or artifact builder
runs. A bound-complete-fragment stub isolates only the source chronology logic.
The independent expected outcomes drive assertions; production factor_series
is a separate cross-check, never the implementation of the proof under test.
"""
import argparse
from datetime import datetime, timezone
import io
from pathlib import Path
import re
import sys
import types


def test_independent_base_basis_proof_regressions():
    root = Path(__file__).resolve().parents[3]
    source_file = root / 'stock-fish/scripts/r16_r1_base_basis_proof.py'
    import importlib.util
    spec = importlib.util.spec_from_file_location('r16_r1_base_basis_proof_under_test', source_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.path.insert(0, str(root / "stock-fish"))
    from stockfish_core.yaogu.provider_comparability import factor_series as production_factor_series

    anchor = {"dividOperateDate": "2000-01-01", "backAdjustFactor": "2"}
    historic = {"dividOperateDate": "2020-05-01", "backAdjustFactor": "3"}
    future = {"dividOperateDate": "2026-09-09", "backAdjustFactor": "4"}

    def snapshot(rows, clock, *, end="2026-09-08", start="1990-01-01"):
        return {"rows": rows, "clock": clock, "parameters": {"start_date": start, "end_date": end}}

    def make(snapshots):
        proof = module.Proof.__new__(module.Proof)
        proof.series = {}
        proof.parents = {ref: {"ingested_at": value["clock"]} for ref, value in snapshots.items()}
        proof.factor_fragment = lambda ref, iid: (
            {"parameters": snapshots[ref]["parameters"]}, snapshots[ref]["rows"],
            {"receipt_status": "COMPLETE", "scope": "BOUND_COMPLETE_FRAGMENT_TEST_STUB"},
        )
        return proof

    def production(snapshots):
        ordered = sorted(snapshots.items(), key=lambda item: datetime.fromisoformat(item[1]["clock"]).astimezone(timezone.utc))
        return production_factor_series([(value["rows"], ref, value["parameters"]["end_date"],
                                          value["parameters"]["start_date"]) for ref, value in ordered])

    def expect_error(name, snapshots, reason, *, production_conflict=None, refs=None):
        proof = make(snapshots)
        try:
            proof.factor_series("600000.SH", refs or tuple(snapshots))
        except ValueError as exc:
            assert reason in str(exc), (name, str(exc))
            if "CONFLICTING_FACTOR_SERIES" not in reason:
                assert isinstance(exc, module.Unverified), (name, "must remain UNVERIFIED")
        else:
            raise AssertionError(name + ": EXPECTED_REFUSAL")
        if production_conflict is not None:
            assert production(snapshots)[2] is production_conflict, name
        print("PASS", name, reason, flush=True)

    def expect_value(name, snapshots, expected, *, refs=None):
        proof = make(snapshots)
        result = proof.factor_series("600000.SH", refs or tuple(snapshots))
        assert {key: str(value) for key, value in result[0].items()} == expected, name
        assert production(snapshots)[2] is False, name
        assert dict(production(snapshots)[0]) == result[0], name
        assert proof.factor_series("600000.SH", tuple(reversed(tuple(snapshots)))) is result, "cache must be order independent"
        print("PASS", name, flush=True)

    old_clock, new_clock = "2026-09-08T15:00:00+08:00", "2026-09-08T08:00:00+00:00"
    # Source IDs are deliberately opposite to receipt chronology.
    expect_error("complete_window_deletion", {
        "a_new": snapshot([anchor], new_clock), "z_old": snapshot([anchor, historic], old_clock),
    }, "FACTOR_COMPLETE_WINDOW_DELETION_REQUIRES_REVIEW", production_conflict=True)
    expect_error("historical_insertion_into_complete_window", {
        "a_new": snapshot([anchor, historic], new_clock), "z_old": snapshot([anchor], old_clock),
    }, "FACTOR_HISTORICAL_INSERTION_REQUIRES_REVIEW", production_conflict=True)
    expect_value("legitimate_future_extension_reversed_refs", {
        "a_new": snapshot([anchor, future], new_clock, end="2026-09-09"),
        "z_old": snapshot([anchor], old_clock),
    }, {"2000-01-01": "2", "2026-09-09": "4"})
    expect_value("same_values_later_capture", {
        "new": snapshot([anchor, historic], new_clock), "old": snapshot([anchor, historic], old_clock),
    }, {"2000-01-01": "2", "2020-05-01": "3"})
    expect_error("equal_instant_different_offsets_not_id_tiebreak", {
        "a": snapshot([anchor], "2026-09-08T15:00:00+08:00"),
        "z": snapshot([anchor], "2026-09-08T07:00:00+00:00"),
    }, "FACTOR_CAPTURE_CHRONOLOGY_AMBIGUOUS_SAME_INSTANT", production_conflict=False)
    expect_error("existing_factor_value_conflict", {
        "old": snapshot([anchor], old_clock),
        "new": snapshot([{**anchor, "backAdjustFactor": "5"}], new_clock),
    }, "CONFLICTING_FACTOR_SERIES", production_conflict=True)
    expect_error("later_complete_empty_retracts_anchor", {
        "new": snapshot([], new_clock), "old": snapshot([anchor], old_clock),
    }, "FACTOR_COMPLETE_WINDOW_DELETION_REQUIRES_REVIEW", production_conflict=True)
    expect_value("disjoint_empty_window_does_not_retract_outside_scope", {
        "new": snapshot([], new_clock, start="2026-09-09", end="2026-09-09"),
        "old": snapshot([anchor], old_clock),
    }, {"2000-01-01": "2"})

    def raw_case(flag):
        proof = module.Proof.__new__(module.Proof)
        proof.series, proof.raw_indexes, proof.counts = {}, {}, module.Counter()
        proof.cutoff = datetime.fromisoformat("2026-09-08T20:30:00+08:00")
        clocks = {key: "2026-09-08T15:00:00+08:00" for key in ("source_published_at", "first_seen_at", "ingested_at")}
        proof.parents = {"raw": {"dataset": "market_daily", **clocks}}
        raw = {"code": "sh.600000", "date": "2026-09-08", "adjustflag": flag,
               "open": "10", "high": "11", "low": "9", "close": "10", "preclose": "10",
               "volume": "100", "amount": "1000", "turn": "3"}
        capture = {"provider": "baostock", "status": "COMPLETE", "pagination_complete": True, "rows": [raw]}
        proof.capture = lambda ref: capture
        payload = {"instrument_id": "600000.SH", "session": "2026-09-08", "source_refs": ["raw"],
                   "event_time": "2026-09-08T15:00:00+08:00", **clocks,
                   "open": "10", "high": "11", "low": "9", "close": "10", "reference_prev_close": "10",
                   "volume_shares": "100", "amount_cny": "1000", "turnover_fraction": .03,
                   "comparable_open": None, "comparable_high": None, "comparable_low": None,
                   "comparable_close": None, "comparable_basis": None}
        encoded = module.canonical(payload)
        stored = {"payload": encoded.decode(), "row_hash": module.sha(encoded), "instrument_id": "600000.SH",
                  "member_iid": "600000.SH", "session": "2026-09-08", "member_session": "2026-09-08",
                  "kind": "bar", "member_kind": "bar"}
        return proof.row(stored, "2026-09-08")

    assert raw_case("3")["status"] == "VERIFIED_RAW_ONLY_COMPARABILITY_NOT_ASSERTED"
    print("PASS raw_adjustflag_3")
    for flag in ("1", "2", None):
        try:
            raw_case(flag)
        except module.Unverified as exc:
            assert "RAW_CAPTURE_ADJUSTFLAG_NOT_UNADJUSTED_3" in str(exc)
        else:
            raise AssertionError("adjustflag must establish raw provenance: " + str(flag))
    print("PASS adjusted_or_missing_flag_rejected")

    def fragment_case(captured):
        proof = module.Proof.__new__(module.Proof)
        proof.cutoff = datetime.fromisoformat("2026-09-08T20:30:00+08:00")
        proof.parents = {"factor": {"ingested_at": "2026-09-08T08:00:00+00:00"}}
        proof.factor_groups = {"factor": {"600000.SH": [anchor]}}
        capture = {"provider": "baostock", "endpoint": "adjustment_factors", "status": "PARTIAL",
                   "parameters": {"codes": ["sh.600000"]}, "coverage": {"security_statuses": [
                       {"code": "sh.600000", "status": "COMPLETE", "cursor_exhausted": True,
                        "row_count": 1, "captured_at": captured}]}}
        proof.capture = lambda ref: capture
        return proof.factor_fragment("factor", "600000.SH")

    assert fragment_case("2026-09-08T15:00:00+08:00")[2]["receipt_status"] == "PARTIAL"
    print("PASS fragment_capture_before_ingestion_across_offsets")
    try:
        fragment_case("2026-09-08T17:00:00+08:00")
    except module.Unverified as exc:
        assert "FACTOR_FRAGMENT_CAPTURE_AFTER_PARENT_INGESTION" in str(exc)
    else:
        raise AssertionError("fragment after parent ingestion must not acquire earlier visibility")
    print("PASS fragment_capture_after_ingestion_rejected")

    data = b"bounded-failure-stream\n" * 150000
    sizes = []
    class BoundedRead(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024, "whole-file hash read forbidden"
            sizes.append(size)
            return super().read(size)
    class FakePath:
        def open(self, mode):
            assert mode == "rb"
            return BoundedRead(data)
    original_path = module.Path
    try:
        module.Path = lambda value: FakePath()
        assert module.file_sha("NO_FILESYSTEM_READ") == module.sha(data)
    finally:
        module.Path = original_path
    assert len(sizes) >= 4
    print("PASS streamed_failure_hash_reads_at_most_one_mib")
    print("13_PURE_MEMORY_TEST_GROUPS_PASSED", "INTEGRATED_SOURCE")
