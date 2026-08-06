"""Canonical identity, manifest, checksum, and A/B reproducibility tests."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from quant_system.data import (
    Bar,
    ManifestResult,
    QualityIssue,
    QualityPolicy,
    QualityStatus,
    build_manifest,
    compute_manifest_sha256,
    quarantine_result,
    validate_quality,
    verify_input_file_sha256,
    verify_manifest,
)
from quant_system.data.canonical import (
    CanonicalizationError,
    DatasetDescriptor,
    canonical_bar_payload,
    canonical_json_bytes,
    canonicalize_records,
    compute_data_version,
    sha256_bytes,
    sha256_file,
)
from quant_system.data.manifest import (
    ManifestValidationError,
    bind_manifest,
    load_manifest,
    manifest_to_json,
)
from quant_system.data.models import ValidationError

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "phase2_bar_contract"
POLICY = QualityPolicy("synthetic-quality-v1")


def fixture_payload(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def bars(name: str) -> list[Bar]:
    records = fixture_payload(name)["records"]
    return [Bar.from_mapping(item) for item in records]  # type: ignore[arg-type]


def file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def quality_bar(status: QualityStatus, issues: list[str]) -> Bar:
    payload = fixture_payload("valid_regular_est.json")["records"][0]
    assert isinstance(payload, dict)
    payload["quality_status"] = status.value
    payload["quality_issues"] = issues
    return Bar.from_mapping(payload)  # type: ignore[arg-type]


def manifest_for(
    records: list[Bar],
    *,
    policy: QualityPolicy = POLICY,
    expected_record_ids: list[str] | None = None,
) -> ManifestResult:
    quality = validate_quality(
        records,
        expected_record_ids=expected_record_ids,
        policy=policy,
    )
    return build_manifest(
        records,
        quality_result=quality,
        quality_policy=policy,
        source_id="project-synthetic-fixture",
        license_id="project-owned-synthetic",
        exchange_timezone="America/New_York",
        calendar_id="synthetic-controlled-session-v1",
        input_file_sha256={"valid_regular_est.json": "a" * 64},
    )


def test_canonical_json_is_utf8_sorted_and_rejects_float_identity() -> None:
    assert (
        canonical_json_bytes({"é": "合成", "b": 1})
        == b'{"b":1,"\xc3\xa9":"\xe5\x90\x88\xe6\x88\x90"}'
    )
    with pytest.raises(CanonicalizationError, match="float"):
        canonical_json_bytes({"value": 1.25})
    with pytest.raises(CanonicalizationError, match="binary"):
        canonical_json_bytes(b"binary")
    with pytest.raises(CanonicalizationError, match="keys"):
        canonical_json_bytes({1: "not-a-string-key"})
    with pytest.raises(CanonicalizationError, match="unsupported"):
        canonical_json_bytes({"value": object()})


def test_bar_payload_identity_excludes_derived_fields_but_keeps_ingestion() -> None:
    record = bars("valid_regular_est.json")[0]
    payload = canonical_bar_payload(record)
    assert "data_version" not in payload
    assert "data_manifest_sha256" not in payload
    assert payload["ingestion_time"] == "2024-01-02T21:00:02.000000Z"


def test_data_version_is_deterministic_and_has_explicit_inputs() -> None:
    records = bars("valid_regular_est.json")
    inputs = {
        "valid_regular_est.json": "a" * 64,
    }
    kwargs = {
        "source_id": "project-synthetic-fixture",
        "license_id": "project-owned-synthetic",
        "exchange_timezone": "America/New_York",
        "calendar_id": "synthetic-controlled-session-v1",
        "quality_result": validate_quality(records, policy=POLICY),
        "input_file_sha256": inputs,
    }
    first = compute_data_version(records, **kwargs)
    second = compute_data_version(records, **kwargs)
    assert first == second
    assert len(first) == 64

    changed = bars("valid_regular_est.json")[0].with_lineage(
        data_version="2" * 64,
        data_manifest_sha256="3" * 64,
    )
    changed_payload = changed.to_mapping()
    changed_payload["ingestion_time"] = "2024-01-02T21:00:03Z"
    changed_payload["as_of_time"] = "2024-01-02T21:00:04Z"
    changed_record = Bar.from_mapping(changed_payload)
    assert compute_data_version([changed_record], **kwargs) != first

    with pytest.raises(CanonicalizationError, match="absolute"):
        compute_data_version(records, **{**kwargs, "input_file_sha256": {"C:/secret": "a" * 64}})


def test_ordering_is_explicitly_rejected_or_explicitly_normalized() -> None:
    later, earlier = bars("out_of_order.json")
    with pytest.raises(ValidationError, match="out of order"):
        canonicalize_records([later, earlier])
    normalized = canonicalize_records([later, earlier], reject_out_of_order=False)
    assert normalized[0].record_id == earlier.record_id
    with pytest.raises(ValidationError, match="record_id"):
        canonicalize_records([earlier, earlier])


def test_dataset_descriptor_contains_required_identity_inputs() -> None:
    records = bars("valid_regular_est.json")
    result = validate_quality(records, policy=POLICY)
    descriptor = DatasetDescriptor.from_records(
        records,
        source_id="project-synthetic-fixture",
        license_id="project-owned-synthetic",
        exchange_timezone="America/New_York",
        calendar_id="synthetic-controlled-session-v1",
        quality_result=result,
        input_file_sha256={"fixture.json": "a" * 64},
    )
    mapping = descriptor.to_mapping()
    assert mapping["schema_name"] == "Bar"
    assert mapping["schema_version"] == "1.0.0"
    assert mapping["instrument_coverage"] == ["instrument-synth-0001"]
    assert mapping["interval"] == "1d"
    assert mapping["ordered_canonical_records"]
    assert mapping["quality_result"] == result.to_mapping()


def test_manifest_has_two_phase_hash_and_binds_bars() -> None:
    records = bars("valid_regular_est.json")
    quality = validate_quality(records, policy=POLICY)
    result = build_manifest(
        records,
        quality_result=quality,
        quality_policy=POLICY,
        source_id="project-synthetic-fixture",
        license_id="project-owned-synthetic",
        exchange_timezone="America/New_York",
        calendar_id="synthetic-controlled-session-v1",
        input_file_sha256={"valid_regular_est.json": "a" * 64},
    )
    assert result.data_version == result.bound_records[0].data_version
    assert result.data_manifest_sha256 == result.bound_records[0].data_manifest_sha256
    assert result.manifest["data_version"] == result.data_version
    assert "data_manifest_sha256" not in result.manifest
    assert result.manifest_bytes == json.dumps(
        result.manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert compute_manifest_sha256(result.manifest) == result.data_manifest_sha256
    verify_manifest(
        result.manifest,
        expected_manifest_sha256=result.data_manifest_sha256,
        expected_data_version=result.data_version,
        records=result.bound_records,
    )
    assert bind_manifest(records, result) == result.bound_records

    with pytest.raises(ManifestValidationError, match="quality policy"):
        build_manifest(
            records,
            quality_result=quality,
            quality_policy=QualityPolicy("different-policy"),
            source_id="project-synthetic-fixture",
            license_id="project-owned-synthetic",
            exchange_timezone="America/New_York",
            calendar_id="synthetic-controlled-session-v1",
            input_file_sha256={"valid_regular_est.json": "a" * 64},
        )


def test_manifest_and_file_checksum_mismatches_fail_closed(tmp_path: Path) -> None:
    records = bars("valid_regular_est.json")
    quality = validate_quality(records, policy=POLICY)
    result = build_manifest(
        records,
        quality_result=quality,
        quality_policy=POLICY,
        source_id="project-synthetic-fixture",
        license_id="project-owned-synthetic",
        exchange_timezone="America/New_York",
        calendar_id="synthetic-controlled-session-v1",
        input_file_sha256={"valid_regular_est.json": "a" * 64},
    )
    tampered = dict(result.manifest)
    tampered["source_id"] = "tampered"
    with pytest.raises(ManifestValidationError, match="data_version"):
        verify_manifest(tampered, expected_manifest_sha256=result.data_manifest_sha256)
    with pytest.raises(ManifestValidationError, match="must not contain"):
        verify_manifest(
            {**result.manifest, "data_manifest_sha256": "a" * 64},
            expected_manifest_sha256=result.data_manifest_sha256,
        )

    input_file = tmp_path / "input.json"
    input_file.write_text("synthetic", encoding="utf-8")
    expected = hashlib.sha256(input_file.read_bytes()).hexdigest()
    verify_input_file_sha256(input_file, expected)
    with pytest.raises(ManifestValidationError, match="checksum"):
        verify_input_file_sha256(input_file, "a" * 64)
    with pytest.raises(CanonicalizationError, match="unable to read"):
        sha256_file(tmp_path / "missing-input")


def test_manifest_reverification_checks_phase_a_phase_b_and_records() -> None:
    records = bars("valid_regular_est.json")
    quality = validate_quality(records, policy=POLICY)
    result = build_manifest(
        records,
        quality_result=quality,
        quality_policy=POLICY,
        source_id="project-synthetic-fixture",
        license_id="project-owned-synthetic",
        exchange_timezone="America/New_York",
        calendar_id="synthetic-controlled-session-v1",
        input_file_sha256=["a" * 64],
    )
    verify_manifest(
        result.manifest,
        expected_manifest_sha256=result.data_manifest_sha256,
        records=records,
    )
    with pytest.raises(ManifestValidationError, match="data_version"):
        verify_manifest(
            result.manifest,
            expected_manifest_sha256=result.data_manifest_sha256,
            expected_data_version="2" * 64,
        )
    with pytest.raises(ManifestValidationError, match="canonical manifest bytes"):
        verify_manifest(result.manifest, expected_manifest_sha256="0" * 64)
    with pytest.raises(ManifestValidationError, match="canonical record list"):
        verify_manifest(
            result.manifest,
            expected_manifest_sha256=result.data_manifest_sha256,
            records=[],
        )


def test_quality_identity_must_be_complete_and_matching() -> None:
    records = bars("valid_regular_est.json")
    quality = validate_quality(records, policy=POLICY)
    kwargs = {
        "records": records,
        "quality_policy": POLICY,
        "source_id": "project-synthetic-fixture",
        "license_id": "project-owned-synthetic",
        "exchange_timezone": "America/New_York",
        "calendar_id": "synthetic-controlled-session-v1",
        "input_file_sha256": {"valid_regular_est.json": "a" * 64},
    }
    with pytest.raises(ManifestValidationError, match="complete policy"):
        build_manifest(quality_result=replace(quality, policy=None), **kwargs)
    with pytest.raises(ManifestValidationError, match="id"):
        build_manifest(quality_result=replace(quality, policy_id="other"), **kwargs)
    with pytest.raises(ManifestValidationError, match="SHA-256"):
        build_manifest(quality_result=replace(quality, policy_sha256="0" * 64), **kwargs)
    other_policy = QualityPolicy(POLICY.policy_id, late_after=timedelta(seconds=1))
    with pytest.raises(ManifestValidationError, match="mapping"):
        build_manifest(
            quality_result=replace(
                quality,
                policy=other_policy,
                policy_sha256=POLICY.policy_sha256,
            ),
            **kwargs,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("manifest_schema_name", "WrongManifest"),
        ("source_id", ""),
        ("data_version", "g" * 64),
        ("instrument_coverage", []),
        ("instrument_coverage", ["instrument-synth-0001"] * 2),
        ("instrument_coverage", "not-an-array"),
        ("bound_record_ids", "not-an-array"),
        ("ordered_canonical_records", ["not-an-object"]),
    ),
)
def test_manifest_structure_rejects_invalid_fields(field: str, value: object) -> None:
    result = manifest_for(bars("valid_regular_est.json"))
    tampered = deepcopy(result.manifest)
    tampered[field] = value
    with pytest.raises(ManifestValidationError):
        verify_manifest(tampered, expected_manifest_sha256=result.data_manifest_sha256)

    missing = deepcopy(result.manifest)
    del missing["source_id"]
    with pytest.raises(ManifestValidationError, match="incomplete"):
        verify_manifest(missing, expected_manifest_sha256=result.data_manifest_sha256)


def test_manifest_quality_identity_and_loader_fail_closed(tmp_path: Path) -> None:
    result = manifest_for(bars("valid_regular_est.json"))
    malformed_cases = (
        "not-an-object",
        {"policy": POLICY.to_mapping()},
        {"policy": {}, "result": {}},
        {"policy": POLICY.to_mapping(), "result": {}},
    )
    for quality in malformed_cases:
        tampered = deepcopy(result.manifest)
        tampered["quality"] = quality
        with pytest.raises(ManifestValidationError):
            verify_manifest(tampered, expected_manifest_sha256=result.data_manifest_sha256)

    invalid_root = tmp_path / "array.json"
    invalid_root.write_text("[]", encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="root"):
        load_manifest(invalid_root)


def test_bind_manifest_rejects_tampered_bound_ids_and_result_lineage() -> None:
    records = bars("valid_regular_est.json")
    result = manifest_for(records)
    tampered = deepcopy(result.manifest)
    tampered["bound_record_ids"] = []
    tampered_bytes = canonical_json_bytes(tampered)
    tampered_result = replace(
        result,
        manifest=tampered,
        manifest_bytes=tampered_bytes,
        data_manifest_sha256=sha256_bytes(tampered_bytes),
    )
    with pytest.raises(ManifestValidationError, match="bypass"):
        bind_manifest(records, tampered_result)
    with pytest.raises(ManifestValidationError, match="inconsistent"):
        bind_manifest(records, replace(result, bound_records=()))


def test_ab_runs_have_identical_identity_manifest_and_quarantine_bytes(tmp_path: Path) -> None:
    fixture = FIXTURE_ROOT / "late_synthetic_policy.json"
    records = bars("late_synthetic_policy.json")
    policy = QualityPolicy("synthetic-late", late_after=timedelta(seconds=1))
    outputs: list[tuple[dict[str, str], bytes, str, tuple[str, ...]]] = []
    for root in (tmp_path / "A", tmp_path / "B"):
        root.mkdir()
        quality = validate_quality(records, policy=policy)
        manifest = build_manifest(
            records,
            quality_result=quality,
            quality_policy=policy,
            source_id="project-synthetic-fixture",
            license_id="project-owned-synthetic",
            exchange_timezone="America/New_York",
            calendar_id="synthetic-controlled-session-v1",
            input_file_sha256={"late_synthetic_policy.json": sha256_file(fixture)},
        )
        quarantine_result(
            quality,
            root / "quarantine",
            data_version=manifest.data_version,
            source_fixture=fixture,
            code_commit="CANDIDATE-COMMIT-PLACEHOLDER",
            records=records,
        )
        outputs.append(
            (
                file_hashes(root),
                manifest.manifest_bytes,
                manifest.data_version,
                quality.rejected_record_ids,
            )
        )
    assert outputs[0] == outputs[1]


def test_manifest_json_loader_round_trips_canonical_bytes(tmp_path: Path) -> None:
    records = bars("valid_regular_est.json")
    quality = validate_quality(records, policy=POLICY)
    result = build_manifest(
        records,
        quality_result=quality,
        quality_policy=POLICY,
        source_id="project-synthetic-fixture",
        license_id="project-owned-synthetic",
        exchange_timezone="America/New_York",
        calendar_id="synthetic-controlled-session-v1",
        input_file_sha256={"valid_regular_est.json": "a" * 64},
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(result.manifest_bytes)
    assert load_manifest(manifest_path) == result.manifest
    assert manifest_to_json(result).encode("utf-8") == result.manifest_bytes
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="valid UTF-8 JSON"):
        load_manifest(invalid_path)


def test_quarantined_records_are_never_bound() -> None:
    record = quality_bar(QualityStatus.QUARANTINED, [QualityIssue.LATE.value])
    result = manifest_for(
        [record],
        policy=QualityPolicy("quarantine", late_after=timedelta(seconds=2)),
    )
    assert result.bound_records == ()
    assert result.manifest["bound_record_ids"] == []


def test_rejected_records_are_never_bound() -> None:
    record = quality_bar(QualityStatus.REJECTED, [])
    result = manifest_for([record], policy=QualityPolicy("rejected"))
    assert result.bound_records == ()
    assert result.manifest["bound_record_ids"] == []


def test_bind_manifest_cannot_bypass_quality_gate() -> None:
    record = quality_bar(QualityStatus.REJECTED, [])
    result = manifest_for([record], policy=QualityPolicy("rejected-bind"))
    assert bind_manifest([record], result) == ()


def test_allowed_suspect_only_binds_downstream_records() -> None:
    record = bars("valid_regular_est.json")[0]
    policy = QualityPolicy(
        "allow-missing",
        allowed_suspect_issues=frozenset({QualityIssue.MISSING.value}),
    )
    result = manifest_for(
        [record],
        policy=policy,
        expected_record_ids=[record.record_id, "missing-record"],
    )
    assert [item.record_id for item in result.bound_records] == [record.record_id]
    assert [item.record_id for item in bind_manifest([record], result)] == [record.record_id]


def test_policy_same_id_different_threshold_is_rejected() -> None:
    records = bars("valid_regular_est.json")
    source_policy = QualityPolicy("same-id", late_after=timedelta(seconds=1))
    quality = validate_quality(records, policy=source_policy)
    with pytest.raises(ManifestValidationError, match="SHA-256"):
        build_manifest(
            records,
            quality_result=quality,
            quality_policy=QualityPolicy("same-id", late_after=timedelta(seconds=2)),
            source_id="project-synthetic-fixture",
            license_id="project-owned-synthetic",
            exchange_timezone="America/New_York",
            calendar_id="synthetic-controlled-session-v1",
            input_file_sha256={"valid_regular_est.json": "a" * 64},
        )


def test_policy_same_id_different_allowlist_is_rejected() -> None:
    records = bars("valid_regular_est.json")
    source_policy = QualityPolicy(
        "same-allowlist",
        allowed_suspect_issues=frozenset({QualityIssue.MISSING.value}),
    )
    quality = validate_quality(records, policy=source_policy)
    with pytest.raises(ManifestValidationError, match="SHA-256"):
        build_manifest(
            records,
            quality_result=quality,
            quality_policy=QualityPolicy(
                "same-allowlist",
                allowed_suspect_issues=frozenset({QualityIssue.DUPLICATE.value}),
            ),
            source_id="project-synthetic-fixture",
            license_id="project-owned-synthetic",
            exchange_timezone="America/New_York",
            calendar_id="synthetic-controlled-session-v1",
            input_file_sha256={"valid_regular_est.json": "a" * 64},
        )


def test_manifest_recomputes_data_version() -> None:
    records = bars("valid_regular_est.json")
    result = manifest_for(records)
    verify_manifest(
        result.manifest,
        expected_manifest_sha256=result.data_manifest_sha256,
        records=records,
    )


def test_manifest_rejects_tampered_data_version_with_rehashed_manifest() -> None:
    result = manifest_for(bars("valid_regular_est.json"))
    tampered = deepcopy(result.manifest)
    tampered["data_version"] = "2" * 64
    with pytest.raises(ManifestValidationError, match="data_version"):
        verify_manifest(
            tampered,
            expected_manifest_sha256=sha256_bytes(canonical_json_bytes(tampered)),
        )


def test_manifest_rejects_tampered_policy_with_rehashed_manifest() -> None:
    result = manifest_for(bars("valid_regular_est.json"))
    tampered = deepcopy(result.manifest)
    tampered["quality"]["policy"]["late_after_microseconds"] = 1  # type: ignore[index]
    with pytest.raises(ManifestValidationError, match="policy"):
        verify_manifest(
            tampered,
            expected_manifest_sha256=sha256_bytes(canonical_json_bytes(tampered)),
        )


def test_manifest_rejects_tampered_quality_result_with_rehashed_manifest() -> None:
    result = manifest_for(bars("valid_regular_est.json"))
    tampered = deepcopy(result.manifest)
    tampered["quality"]["result"]["deterministic_reason"] = "tampered"  # type: ignore[index]
    with pytest.raises(ManifestValidationError, match="data_version"):
        verify_manifest(
            tampered,
            expected_manifest_sha256=sha256_bytes(canonical_json_bytes(tampered)),
        )


def test_manifest_rejects_tampered_records_with_rehashed_manifest() -> None:
    result = manifest_for(bars("valid_regular_est.json"))
    tampered = deepcopy(result.manifest)
    tampered["ordered_canonical_records"][0]["close"] = "999.00"  # type: ignore[index]
    with pytest.raises(ManifestValidationError, match="data_version"):
        verify_manifest(
            tampered,
            expected_manifest_sha256=sha256_bytes(canonical_json_bytes(tampered)),
        )


def test_all_round_fixtures_are_synthetic_and_have_stable_file_hashes() -> None:
    expected = {
        "valid_regular_est.json",
        "valid_regular_edt.json",
        "valid_half_day.json",
        "missing_session_record.json",
        "duplicate_idempotent.json",
        "duplicate_conflict.json",
        "out_of_order.json",
        "invalid_ohlc.json",
        "invalid_decimal.json",
        "invalid_time_relationship.json",
        "late_synthetic_policy.json",
        "stale_synthetic_policy.json",
    }
    actual = {path.name for path in FIXTURE_ROOT.glob("*.json")}
    assert actual == expected
    assert len(actual) <= 12
    hashes = {name: sha256_file(FIXTURE_ROOT / name) for name in sorted(actual)}
    assert all(len(digest) == 64 for digest in hashes.values())
    assert all(
        fixture_payload(name)["synthetic"] is True
        and fixture_payload(name)["fixture_label"] == "SYNTHETIC_TEST_ONLY"
        for name in actual
    )
