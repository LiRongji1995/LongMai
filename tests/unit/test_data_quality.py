"""Synthetic positive and negative quality-gate tests."""

from __future__ import annotations

import json
import tomllib
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant_system.data import (
    Bar,
    QualityIssue,
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


def bar_with_quality(status: QualityStatus, issues: list[str]) -> Bar:
    payload = fixture_payload("valid_regular_est.json")["records"][0]
    assert isinstance(payload, dict)
    payload["quality_status"] = status.value
    payload["quality_issues"] = issues
    return Bar.from_mapping(payload)  # type: ignore[arg-type]


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
    replay = validate_quality(
        bars("duplicate_idempotent.json"),
        policy=QualityPolicy(
            "synthetic-quality-v1",
            allowed_suspect_issues=frozenset({QualityIssue.DUPLICATE.value}),
        ),
    )
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


def test_policy_identity_round_trips_and_rejects_unsafe_allowlists() -> None:
    policy = QualityPolicy(
        "identity",
        late_after=timedelta(seconds=1),
        stale_after=timedelta(seconds=2),
        allowed_suspect_issues=frozenset(
            {QualityIssue.MISSING.value, QualityIssue.LATE.value, QualityIssue.STALE.value}
        ),
    )
    mapping = policy.to_mapping()
    assert QualityPolicy.from_mapping(mapping) == policy
    assert QualityPolicy.from_mapping(mapping).policy_sha256 == policy.policy_sha256

    with pytest.raises(QualityPolicyError):
        QualityPolicy("none", allowed_suspect_issues=None)  # type: ignore[arg-type]
    with pytest.raises(QualityPolicyError):
        QualityPolicy("non-string", allowed_suspect_issues=frozenset({1}))  # type: ignore[arg-type]
    with pytest.raises(QualityPolicyError):
        QualityPolicy("unsupported", allowed_suspect_issues=frozenset({"UNKNOWN"}))
    with pytest.raises(QualityPolicyError):
        QualityPolicy("invalid", allowed_suspect_issues=frozenset({QualityIssue.INVALID.value}))
    with pytest.raises(QualityPolicyError):
        QualityPolicy("late-without-threshold", allowed_suspect_issues=frozenset({"LATE"}))
    with pytest.raises(QualityPolicyError):
        QualityPolicy("stale-without-threshold", allowed_suspect_issues=frozenset({"STALE"}))

    incomplete = dict(mapping)
    incomplete.pop("allowed_suspect_issues")
    with pytest.raises(QualityPolicyError):
        QualityPolicy.from_mapping(incomplete)
    bad_late = dict(mapping)
    bad_late["late_after_microseconds"] = True
    with pytest.raises(QualityPolicyError):
        QualityPolicy.from_mapping(bad_late)
    bad_stale = dict(mapping)
    bad_stale["stale_after_microseconds"] = True
    with pytest.raises(QualityPolicyError):
        QualityPolicy.from_mapping(bad_stale)
    bad_allowlist = dict(mapping)
    bad_allowlist["allowed_suspect_issues"] = ("MISSING",)
    with pytest.raises(QualityPolicyError):
        QualityPolicy.from_mapping(bad_allowlist)


def test_quality_result_identity_is_fail_closed_when_tampered() -> None:
    record = bars("valid_regular_est.json")
    valid = validate_quality(record, policy=POLICY)
    assert (
        replace(
            valid,
            record_quality_issues=((record[0].record_id, (QualityIssue.LATE.value,)),),
        ).downstream_records
        == ()
    )
    suspect = validate_quality(
        record,
        expected_record_ids=[record[0].record_id, "missing-record"],
        policy=QualityPolicy(
            "missing-allowed",
            allowed_suspect_issues=frozenset({QualityIssue.MISSING.value}),
        ),
    )
    assert replace(suspect, policy=None).downstream_records == ()
    assert replace(suspect, issues=(QualityIssue.INVALID.value,)).downstream_records == ()
    assert replace(suspect, policy_sha256="0" * 64).downstream_records == ()


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


def test_quarantine_evidence_validates_all_identity_fields(tmp_path: Path) -> None:
    base = {
        "root": tmp_path,
        "data_version": "a" * 64,
        "record_id": "record-synth-late-0001",
        "quality_status": QualityStatus.QUARANTINED,
        "quality_issues": [QualityIssue.LATE.value],
        "deterministic_reason": "test",
        "source_fixture_sha256": "b" * 64,
        "code_commit": "placeholder",
    }
    invalid_cases = (
        ("data_version", ".."),
        ("record_id", "../record"),
        ("quality_status", "QUARANTINED"),
        ("deterministic_reason", ""),
        ("source_fixture_sha256", "bad"),
        ("code_commit", ""),
        ("quality_issues", ["UNKNOWN"]),
    )
    for field, value in invalid_cases:
        payload = dict(base)
        payload[field] = value
        with pytest.raises(QuarantineWriteError):
            quarantine_evidence(**payload)  # type: ignore[arg-type]


@pytest.mark.parametrize("data_version", ("A" * 64, "a" * 63, "g" * 64))
def test_quarantine_requires_sha256_data_version(tmp_path: Path, data_version: str) -> None:
    with pytest.raises(QuarantineWriteError, match="lowercase SHA-256"):
        quarantine_evidence(
            tmp_path,
            data_version=data_version,
            record_id="record-synth-late-0001",
            quality_status=QualityStatus.QUARANTINED,
            quality_issues=[QualityIssue.LATE.value],
            deterministic_reason="late synthetic record",
            source_fixture_sha256="b" * 64,
            code_commit="placeholder",
        )


def test_quarantine_rejects_valid_status(tmp_path: Path) -> None:
    with pytest.raises(QuarantineWriteError, match="QUARANTINED or REJECTED"):
        quarantine_evidence(
            tmp_path,
            data_version="a" * 64,
            record_id="record-synth-late-0001",
            quality_status=QualityStatus.VALID,
            quality_issues=[QualityIssue.LATE.value],
            deterministic_reason="late synthetic record",
            source_fixture_sha256="b" * 64,
            code_commit="placeholder",
        )


def test_quarantine_rejects_empty_issues(tmp_path: Path) -> None:
    with pytest.raises(QuarantineWriteError, match="must not be empty"):
        quarantine_evidence(
            tmp_path,
            data_version="a" * 64,
            record_id="record-synth-late-0001",
            quality_status=QualityStatus.REJECTED,
            quality_issues=[],
            deterministic_reason="rejected synthetic record",
            source_fixture_sha256="b" * 64,
            code_commit="placeholder",
        )


def test_quarantine_result_rejects_unknown_record_id(tmp_path: Path) -> None:
    fixture = FIXTURE_ROOT / "late_synthetic_policy.json"
    result = replace(
        validate_quality(bars("late_synthetic_policy.json"), policy=POLICY),
        rejected_record_ids=("unknown-record",),
        record_quality_issues=(("unknown-record", (QualityIssue.LATE.value,)),),
    )
    with pytest.raises(QuarantineWriteError, match="not present"):
        quarantine_result(
            result,
            tmp_path / "quarantine",
            data_version="a" * 64,
            source_fixture=fixture,
            code_commit="placeholder",
            records=bars("late_synthetic_policy.json"),
        )


def test_quarantine_result_skips_missing_ids_without_fabricating_bars(tmp_path: Path) -> None:
    missing_id = "missing-record"
    result = replace(
        validate_quality([], expected_record_ids=[missing_id], policy=POLICY),
        status=QualityStatus.REJECTED,
        rejected_record_ids=(missing_id,),
    )
    paths = quarantine_result(
        result,
        tmp_path / "quarantine",
        data_version="a" * 64,
        source_fixture=FIXTURE_ROOT / "valid_regular_est.json",
        code_commit="placeholder",
        records=[],
    )
    assert paths == ()
    assert not (tmp_path / "quarantine").exists()


def test_quarantine_result_rejects_existing_rejected_record_without_reason(
    tmp_path: Path,
) -> None:
    record = bar_with_quality(QualityStatus.REJECTED, [])
    result = validate_quality([record], policy=POLICY)
    with pytest.raises(QuarantineWriteError, match="record-specific"):
        quarantine_result(
            result,
            tmp_path / "quarantine",
            data_version="a" * 64,
            source_fixture=FIXTURE_ROOT / "valid_regular_est.json",
            code_commit="placeholder",
            records=[record],
        )


def test_quarantine_result_rejects_valid_result_with_rejected_ids(tmp_path: Path) -> None:
    record = bars("valid_regular_est.json")[0]
    result = replace(
        validate_quality([record], policy=POLICY),
        rejected_record_ids=(record.record_id,),
        record_quality_issues=((record.record_id, (QualityIssue.INVALID.value,)),),
    )
    with pytest.raises(QuarantineWriteError, match="QUARANTINED or REJECTED"):
        quarantine_result(
            result,
            tmp_path / "quarantine",
            data_version="a" * 64,
            source_fixture=FIXTURE_ROOT / "valid_regular_est.json",
            code_commit="placeholder",
            records=[record],
        )


def test_quarantine_result_rejects_non_bar_records_argument(tmp_path: Path) -> None:
    result = validate_quality([], policy=POLICY)
    with pytest.raises(QuarantineWriteError, match="validated Bar"):
        quarantine_result(
            result,
            tmp_path / "quarantine",
            data_version="a" * 64,
            source_fixture=FIXTURE_ROOT / "valid_regular_est.json",
            code_commit="placeholder",
            records=[object()],  # type: ignore[list-item]
        )


def test_quality_result_mapping_is_deterministic() -> None:
    result = validate_quality(bars("valid_regular_est.json"), policy=POLICY)
    mapping = result.to_mapping()
    assert mapping["status"] == "VALID"
    assert mapping["policy_id"] == "synthetic-quality-v1"
    assert mapping["accepted_record_ids"] == ["record-synth-est-0001"]


def test_existing_suspect_late_is_not_reclassified_valid() -> None:
    result = validate_quality(
        [bar_with_quality(QualityStatus.SUSPECT, [QualityIssue.LATE.value])],
        policy=QualityPolicy("suspect-late", late_after=timedelta(seconds=2)),
    )
    assert result.status is QualityStatus.SUSPECT
    assert result.issues == (QualityIssue.LATE.value,)
    assert result.downstream_records == ()


def test_suspect_requires_explicit_policy_allowance() -> None:
    record = bar_with_quality(QualityStatus.SUSPECT, [QualityIssue.MISSING.value])
    denied = validate_quality([record], policy=QualityPolicy("suspect-default"))
    allowed = validate_quality(
        [record],
        policy=QualityPolicy(
            "suspect-allow-missing",
            allowed_suspect_issues=frozenset({QualityIssue.MISSING.value}),
        ),
    )
    assert denied.status is QualityStatus.SUSPECT
    assert denied.downstream_records == ()
    assert allowed.status is QualityStatus.SUSPECT
    assert [item.record_id for item in allowed.downstream_records] == [record.record_id]


def test_suspect_without_issues_fails_closed() -> None:
    result = validate_quality(
        [bar_with_quality(QualityStatus.SUSPECT, [])],
        policy=QualityPolicy("suspect-empty"),
    )
    assert result.status is QualityStatus.SUSPECT
    assert result.issues == ()
    assert result.downstream_records == ()


def test_quality_validation_rejects_non_bar_objects() -> None:
    result = validate_quality([object()], policy=POLICY)  # type: ignore[list-item]
    assert result.status is QualityStatus.REJECTED
    assert result.issues == (QualityIssue.INVALID.value,)


def test_quarantined_and_rejected_records_never_reach_downstream() -> None:
    quarantined = validate_quality(
        [bar_with_quality(QualityStatus.QUARANTINED, [QualityIssue.LATE.value])],
        policy=QualityPolicy("quarantined", late_after=timedelta(seconds=2)),
    )
    rejected = validate_quality(
        [bar_with_quality(QualityStatus.REJECTED, [])],
        policy=QualityPolicy("rejected"),
    )
    assert quarantined.status is QualityStatus.QUARANTINED
    assert quarantined.downstream_records == ()
    assert rejected.status is QualityStatus.REJECTED
    assert rejected.downstream_records == ()


def test_quarantine_issues_are_record_specific(tmp_path: Path) -> None:
    late = bars("late_synthetic_policy.json")[0]
    stale = bars("stale_synthetic_policy.json")[0]
    result = validate_quality(
        [late, stale],
        policy=QualityPolicy(
            "mixed-latency",
            late_after=timedelta(seconds=1),
            stale_after=timedelta(seconds=4),
        ),
    )
    assert result.record_issue_map[late.record_id] == (QualityIssue.LATE.value,)
    assert result.record_issue_map[stale.record_id] == (QualityIssue.STALE.value,)
    paths = quarantine_result(
        result,
        tmp_path / "quarantine",
        data_version="a" * 64,
        source_fixture=FIXTURE_ROOT / "late_synthetic_policy.json",
        code_commit="CANDIDATE-COMMIT-PLACEHOLDER",
        records=[late, stale],
    )
    assert len(paths) == 2
    evidence = {
        payload["record_id"]: payload["quality_issues"]
        for payload in (json.loads(path.read_text(encoding="utf-8")) for path in paths)
    }
    assert evidence == {
        late.record_id: [QualityIssue.LATE.value],
        stale.record_id: [QualityIssue.STALE.value],
    }


def test_quarantine_reason_matrix_is_record_specific(tmp_path: Path) -> None:
    cases = (
        (
            "duplicate-conflict",
            parse_and_validate(
                fixture_payload("duplicate_conflict.json")["records"],  # type: ignore[arg-type]
                policy=POLICY,
            ),
            FIXTURE_ROOT / "duplicate_conflict.json",
            bars("duplicate_conflict.json"),
            {"record-synth-conflict-0001": [QualityIssue.DUPLICATE.value]},
        ),
        (
            "out-of-order",
            validate_quality(bars("out_of_order.json"), policy=POLICY),
            FIXTURE_ROOT / "out_of_order.json",
            bars("out_of_order.json"),
            {
                "record-synth-order-0001": [QualityIssue.INVALID.value],
                "record-synth-order-0002": [QualityIssue.INVALID.value],
            },
        ),
        (
            "existing-invalid",
            validate_quality(
                [bar_with_quality(QualityStatus.REJECTED, [QualityIssue.INVALID.value])],
                policy=POLICY,
            ),
            FIXTURE_ROOT / "valid_regular_est.json",
            [bar_with_quality(QualityStatus.REJECTED, [QualityIssue.INVALID.value])],
            {"record-synth-est-0001": [QualityIssue.INVALID.value]},
        ),
        (
            "existing-quarantined",
            validate_quality(
                [bar_with_quality(QualityStatus.QUARANTINED, [QualityIssue.LATE.value])],
                policy=QualityPolicy("existing-quarantine", late_after=timedelta(seconds=2)),
            ),
            FIXTURE_ROOT / "late_synthetic_policy.json",
            [bar_with_quality(QualityStatus.QUARANTINED, [QualityIssue.LATE.value])],
            {"record-synth-est-0001": [QualityIssue.LATE.value]},
        ),
    )
    for label, result, fixture, records, expected in cases:
        paths = quarantine_result(
            result,
            tmp_path / label,
            data_version="b" * 64,
            source_fixture=fixture,
            code_commit="CANDIDATE-COMMIT-PLACEHOLDER",
            records=records,
        )
        actual = {
            payload["record_id"]: payload["quality_issues"]
            for payload in (json.loads(path.read_text(encoding="utf-8")) for path in paths)
        }
        assert actual == expected


def test_windows_timezone_dependency_is_declared() -> None:
    project_root = Path(__file__).parents[2]
    with (project_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    dev_dependencies = project["project"]["optional-dependencies"]["dev"]
    assert "tzdata>=2024.1,<2026; sys_platform == 'win32'" in dev_dependencies
    assert ZoneInfo("America/New_York").key == "America/New_York"
