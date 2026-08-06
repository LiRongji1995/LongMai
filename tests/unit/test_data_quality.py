"""Synthetic positive and negative quality-gate tests."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from quant_system.data import (
    Bar,
    QualityPolicy,
    QualityPolicyError,
    QualityStatus,
    QuarantineWriteError,
    parse_and_validate,
    quarantine_evidence,
    quarantine_result,
    validate_dataset,
    validate_quality,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "phase2_bar_contract"
POLICY = QualityPolicy("synthetic-quality-v1")


def fixture_payload(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def bars(name: str) -> list[Bar]:
    records = fixture_payload(name)["records"]
    return [Bar.from_mapping(item) for item in records]  # type: ignore[arg-type]


def test_missing_is_reported_without_fabricating_a_bar() -> None:
    payload = fixture_payload("missing_session_record.json")
    result = validate_dataset(
        bars("missing_session_record.json"),
        expected_record_ids=payload["expected_record_ids"],  # type: ignore[arg-type]
        policy=POLICY,
    )
    assert result.status is QualityStatus.SUSPECT
    assert result.issues == ("MISSING",)
    assert result.missing_record_ids == ("record-synth-missing-0002",)
    assert len(result.accepted_records) == 1
    assert all(
        record.record_id != "record-synth-missing-0002" for record in result.accepted_records
    )


def test_identical_duplicate_is_idempotent_but_conflicting_duplicate_rejects() -> None:
    replay = validate_quality(bars("duplicate_idempotent.json"), policy=POLICY)
    assert replay.status is QualityStatus.SUSPECT
    assert replay.issues == ("DUPLICATE",)
    assert replay.idempotent_replays == ("record-synth-duplicate-0001",)
    assert len(replay.downstream_records) == 1

    conflict = parse_and_validate(
        fixture_payload("duplicate_conflict.json")["records"],  # type: ignore[arg-type]
        policy=POLICY,
    )
    assert conflict.status is QualityStatus.REJECTED
    assert conflict.issues == ("DUPLICATE", "INVALID")
    assert conflict.duplicate_record_ids == ("record-synth-conflict-0001",)
    assert conflict.downstream_records == ()


def test_out_of_order_is_invalid_and_not_silently_sorted() -> None:
    result = validate_quality(bars("out_of_order.json"), policy=POLICY)
    assert result.status is QualityStatus.REJECTED
    assert result.issues == ("INVALID",)
    assert result.out_of_order_record_ids == (
        "record-synth-order-0001",
        "record-synth-order-0002",
    )
    assert result.downstream_records == ()


def test_late_and_stale_require_explicit_synthetic_policy() -> None:
    late = validate_quality(
        bars("late_synthetic_policy.json"),
        policy=QualityPolicy("synthetic-late", late_after=timedelta(seconds=1)),
    )
    stale = validate_quality(
        bars("stale_synthetic_policy.json"),
        policy=QualityPolicy("synthetic-stale", stale_after=timedelta(seconds=2)),
    )
    assert late.status is QualityStatus.QUARANTINED
    assert late.issues == ("LATE",)
    assert late.late_record_ids == ("record-synth-late-0001",)
    assert late.downstream_records == ()
    assert stale.status is QualityStatus.QUARANTINED
    assert stale.issues == ("STALE",)
    assert stale.stale_record_ids == ("record-synth-stale-0001",)


def test_missing_policy_and_unsafe_policy_fail_closed() -> None:
    result = validate_quality(bars("valid_regular_est.json"), policy=None)
    assert result.status is QualityStatus.REJECTED
    assert result.issues == ("INVALID",)
    assert result.deterministic_reason == "quality policy missing; fail closed"
    with pytest.raises(QualityPolicyError):
        QualityPolicy("", label="SYNTHETIC_TEST_ONLY")
    with pytest.raises(QualityPolicyError):
        QualityPolicy("production-default", label="PRODUCTION")
    with pytest.raises(QualityPolicyError):
        QualityPolicy("negative", late_after=timedelta(seconds=-1))


def test_duplicate_interval_with_different_record_ids_is_rejected() -> None:
    first = bars("valid_regular_est.json")[0]
    payload = first.to_mapping()
    payload["record_id"] = "record-synth-est-0002"
    second = Bar.from_mapping(payload)
    result = validate_quality([first, second], policy=POLICY)
    assert result.status is QualityStatus.REJECTED
    assert result.issues == ("DUPLICATE", "INVALID")


def test_parse_and_validate_converts_invalid_payload_to_invalid_quality() -> None:
    payload = fixture_payload("invalid_decimal.json")["records"]
    result = parse_and_validate(payload, policy=POLICY)  # type: ignore[arg-type]
    assert result.status is QualityStatus.REJECTED
    assert "INVALID" in result.issues
    assert result.rejected_record_ids == ("record-synth-invalid-decimal-0001",)

    invalid_ohlc = parse_and_validate(
        fixture_payload("invalid_ohlc.json")["records"],  # type: ignore[arg-type]
        policy=POLICY,
    )
    assert invalid_ohlc.status is QualityStatus.REJECTED
    invalid_time = parse_and_validate(
        fixture_payload("invalid_time_relationship.json")["records"],  # type: ignore[arg-type]
        policy=POLICY,
    )
    assert invalid_time.status is QualityStatus.REJECTED


def test_quarantine_evidence_is_caller_scoped_and_fail_closed(tmp_path: Path) -> None:
    result = validate_quality(
        bars("late_synthetic_policy.json"),
        policy=QualityPolicy("synthetic-late", late_after=timedelta(seconds=1)),
    )
    paths = quarantine_result(
        result,
        tmp_path / "quarantine",
        data_version="a" * 64,
        source_fixture=FIXTURE_ROOT / "late_synthetic_policy.json",
        code_commit="CANDIDATE-COMMIT-PLACEHOLDER",
        records=bars("late_synthetic_policy.json"),
    )
    assert len(paths) == 1
    evidence = json.loads(paths[0].read_text(encoding="utf-8"))
    assert evidence["data_version"] == "a" * 64
    assert evidence["record_id"] == "record-synth-late-0001"
    assert evidence["quality_status"] == "QUARANTINED"
    assert evidence["quality_issues"] == ["LATE"]
    assert evidence["code_commit"] == "CANDIDATE-COMMIT-PLACEHOLDER"
    assert "credential" not in paths[0].read_text(encoding="utf-8").lower()

    blocked_root = tmp_path / "not-a-directory"
    blocked_root.write_text("occupied", encoding="utf-8")
    with pytest.raises(QuarantineWriteError, match="fail closed"):
        quarantine_evidence(
            blocked_root,
            data_version="a" * 64,
            record_id="record-synth-late-0001",
            quality_status=QualityStatus.QUARANTINED,
            quality_issues=["LATE"],
            deterministic_reason="test",
            source_fixture_sha256="b" * 64,
            code_commit="placeholder",
        )


def test_quality_result_mapping_is_deterministic() -> None:
    result = validate_quality(bars("valid_regular_est.json"), policy=POLICY)
    mapping = result.to_mapping()
    assert mapping["status"] == "VALID"
    assert mapping["policy_id"] == "synthetic-quality-v1"
    assert mapping["accepted_record_ids"] == ["record-synth-est-0001"]
