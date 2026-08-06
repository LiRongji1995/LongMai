"""Two-stage deterministic dataset manifest generation and verification."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .canonical import (
    DatasetDescriptor,
    canonical_bar_payload,
    canonical_json_bytes,
    compute_data_version,
    sha256_bytes,
    sha256_file,
)
from .models import Bar, ValidationError
from .quality import QualityPolicy, QualityResult, validate_quality

MANIFEST_SCHEMA_NAME = "BarDatasetManifest"
MANIFEST_SCHEMA_VERSION = "1.0.0"


class ManifestValidationError(ValidationError):
    """Raised when manifest bytes or lineage do not match their inputs."""


@dataclass(frozen=True, slots=True)
class ManifestResult:
    data_version: str
    manifest: dict[str, object]
    manifest_bytes: bytes
    data_manifest_sha256: str
    bound_records: tuple[Bar, ...]


def _quality_identity(quality_result: QualityResult, policy: QualityPolicy) -> dict[str, object]:
    if quality_result.policy is None:
        raise ManifestValidationError("quality result has no complete policy identity")
    if quality_result.policy_id != policy.policy_id:
        raise ManifestValidationError("quality policy id does not match quality result")
    if quality_result.policy_sha256 != policy.policy_sha256:
        raise ManifestValidationError("quality policy SHA-256 does not match quality result")
    if quality_result.policy.to_mapping() != policy.to_mapping():
        raise ManifestValidationError("quality policy mapping does not match quality result")
    return {
        "policy": policy.to_mapping(),
        "result": quality_result.to_mapping(),
    }


def _build_manifest_mapping(
    descriptor: DatasetDescriptor,
    *,
    data_version: str,
    quality_identity: dict[str, object],
    bound_record_ids: Sequence[str],
) -> dict[str, object]:
    return {
        "manifest_schema_name": MANIFEST_SCHEMA_NAME,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "schema_name": descriptor.schema_name,
        "schema_version": descriptor.schema_version,
        "source_id": descriptor.source_id,
        "license_id": descriptor.license_id,
        "instrument_coverage": list(descriptor.instrument_coverage),
        "interval": descriptor.interval,
        "exchange_timezone": descriptor.exchange_timezone,
        "calendar_id": descriptor.calendar_id,
        "ordered_canonical_records": list(descriptor.ordered_canonical_records),
        "quality": quality_identity,
        "input_file_sha256": descriptor.input_file_sha256,
        "data_version": data_version,
        "bound_record_ids": list(bound_record_ids),
    }


def build_manifest(
    records: Sequence[Bar],
    *,
    quality_result: QualityResult,
    quality_policy: QualityPolicy,
    source_id: str,
    license_id: str,
    exchange_timezone: str,
    calendar_id: str,
    input_file_sha256: Mapping[str, str] | Sequence[str],
    instrument_coverage: Sequence[str] | None = None,
    interval: str = "1d",
) -> ManifestResult:
    """Run phase A (data_version) then phase B (manifest SHA-256)."""

    if not isinstance(quality_result, QualityResult):
        raise ManifestValidationError("quality_result must be a QualityResult")
    identity_quality = _quality_identity(quality_result, quality_policy)
    descriptor = DatasetDescriptor.from_records(
        records,
        source_id=source_id,
        license_id=license_id,
        instrument_coverage=instrument_coverage,
        interval=interval,
        exchange_timezone=exchange_timezone,
        calendar_id=calendar_id,
        quality_result=identity_quality,
        input_file_sha256=input_file_sha256,
    )
    data_version = compute_data_version(
        records,
        source_id=source_id,
        license_id=license_id,
        exchange_timezone=exchange_timezone,
        calendar_id=calendar_id,
        quality_result=identity_quality,
        input_file_sha256=input_file_sha256,
        instrument_coverage=instrument_coverage,
        interval=interval,
    )
    ordered_records = descriptor_records(descriptor, records)
    downstream_ids = tuple(record.record_id for record in quality_result.downstream_records)
    ordered_by_id = {record.record_id: record for record in ordered_records}
    if any(record_id not in ordered_by_id for record_id in downstream_ids):
        raise ManifestValidationError("downstream records do not match descriptor records")
    manifest = _build_manifest_mapping(
        descriptor,
        data_version=data_version,
        quality_identity=identity_quality,
        bound_record_ids=downstream_ids,
    )
    if "data_manifest_sha256" in manifest:
        raise ManifestValidationError("manifest hash must not be a manifest input")
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_sha = sha256_bytes(manifest_bytes)
    bound_records = tuple(
        ordered_by_id[record_id].with_lineage(
            data_version=data_version,
            data_manifest_sha256=manifest_sha,
        )
        for record_id in downstream_ids
    )
    return ManifestResult(
        data_version=data_version,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        data_manifest_sha256=manifest_sha,
        bound_records=bound_records,
    )


def descriptor_records(descriptor: DatasetDescriptor, records: Sequence[Bar]) -> tuple[Bar, ...]:
    """Return records in the exact order represented by the descriptor."""

    ordered = tuple(sorted(records, key=lambda item: (*item.identity_key(), item.record_id)))
    if (
        tuple(canonical_bar_payload(item) for item in ordered)
        != descriptor.ordered_canonical_records
    ):
        raise ManifestValidationError("descriptor records do not match input records")
    return ordered


def compute_manifest_sha256(manifest: Mapping[str, object]) -> str:
    if "data_manifest_sha256" in manifest:
        raise ManifestValidationError("manifest must not contain data_manifest_sha256")
    return sha256_bytes(canonical_json_bytes(dict(manifest)))


def bind_manifest(records: Sequence[Bar], result: ManifestResult) -> tuple[Bar, ...]:
    """Re-run the quality gate before binding any lineage fields."""

    _validate_manifest_structure(result.manifest)
    verify_manifest(
        result.manifest,
        expected_manifest_sha256=result.data_manifest_sha256,
        expected_data_version=result.data_version,
        records=records,
    )
    policy, stored_quality = _quality_from_manifest(result.manifest)
    stored_result = stored_quality["result"]
    if not isinstance(stored_result, dict):
        raise ManifestValidationError("manifest quality result must be an object")
    missing = stored_result.get("missing_record_ids")
    if not isinstance(missing, list) or any(not isinstance(item, str) for item in missing):
        raise ManifestValidationError("manifest missing_record_ids are invalid")
    expected_ids = {record.record_id for record in records} | set(missing)
    rerun = validate_quality(records, expected_record_ids=expected_ids, policy=policy)
    if rerun.to_mapping() != stored_result:
        raise ManifestValidationError("manifest quality result does not pass the quality gate")
    bound_ids = _manifest_string_list(result.manifest, "bound_record_ids", allow_empty=True)
    downstream_ids = [record.record_id for record in rerun.downstream_records]
    if bound_ids != downstream_ids:
        raise ManifestValidationError("manifest bound records bypass the quality gate")
    if tuple(record.record_id for record in result.bound_records) != tuple(bound_ids):
        raise ManifestValidationError("ManifestResult bound records are inconsistent")
    ordered = descriptor_records(_descriptor_from_manifest(result.manifest), records)
    ordered_by_id = {record.record_id: record for record in ordered}
    return tuple(
        ordered_by_id[record_id].with_lineage(
            data_version=result.data_version,
            data_manifest_sha256=result.data_manifest_sha256,
        )
        for record_id in bound_ids
    )


def verify_manifest(
    manifest: Mapping[str, object],
    *,
    expected_manifest_sha256: str,
    expected_data_version: str | None = None,
    records: Sequence[Bar] | None = None,
) -> None:
    """Recompute phase A identity before checking manifest bytes."""

    descriptor = _descriptor_from_manifest(manifest)
    recomputed_data_version = sha256_bytes(canonical_json_bytes(descriptor.to_mapping()))
    data_version = manifest["data_version"]
    if data_version != recomputed_data_version:
        raise ManifestValidationError("manifest data_version does not match its descriptor")
    if expected_data_version is not None and data_version != expected_data_version:
        raise ManifestValidationError("manifest data_version mismatch")
    actual_sha = compute_manifest_sha256(manifest)
    if actual_sha != expected_manifest_sha256:
        raise ManifestValidationError(
            "data_manifest_sha256 does not match canonical manifest bytes"
        )
    if records is not None:
        expected_records = descriptor.ordered_canonical_records
        actual_records = tuple(
            canonical_bar_payload(record)
            for record in sorted(records, key=lambda item: (*item.identity_key(), item.record_id))
        )
        if expected_records != actual_records:
            raise ManifestValidationError("manifest canonical record list mismatch")


def _manifest_string_list(
    manifest: Mapping[str, object], field: str, *, allow_empty: bool = False
) -> list[str]:
    value = manifest.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ManifestValidationError(f"manifest {field} must be a string array")
    if not allow_empty and not value:
        raise ManifestValidationError(f"manifest {field} must not be empty")
    if len(value) != len(set(value)):
        raise ManifestValidationError(f"manifest {field} contains duplicates")
    return value


def _quality_from_manifest(
    manifest: Mapping[str, object],
) -> tuple[QualityPolicy, dict[str, object]]:
    quality = manifest.get("quality")
    if not isinstance(quality, dict):
        raise ManifestValidationError("manifest quality must be an object")
    policy_mapping = quality.get("policy")
    result_mapping = quality.get("result")
    if not isinstance(policy_mapping, dict) or not isinstance(result_mapping, dict):
        raise ManifestValidationError("manifest quality identity is incomplete")
    try:
        policy = QualityPolicy.from_mapping(policy_mapping)
    except ValidationError as error:
        raise ManifestValidationError("manifest quality policy is invalid") from error
    if policy.to_mapping() != policy_mapping:
        raise ManifestValidationError("manifest quality policy is not canonical")
    if result_mapping.get("policy_id") != policy.policy_id:
        raise ManifestValidationError("manifest quality result policy_id mismatch")
    if result_mapping.get("policy_sha256") != policy.policy_sha256:
        raise ManifestValidationError("manifest quality result policy SHA-256 mismatch")
    return policy, quality


def _descriptor_from_manifest(manifest: Mapping[str, object]) -> DatasetDescriptor:
    _validate_manifest_structure(manifest)
    _, quality = _quality_from_manifest(manifest)
    ordered = manifest["ordered_canonical_records"]
    if not isinstance(ordered, list):
        raise ManifestValidationError("manifest ordered_canonical_records are invalid")
    ordered_records: list[dict[str, object]] = []
    for item in ordered:
        if not isinstance(item, dict):
            raise ManifestValidationError("manifest ordered_canonical_records are invalid")
        ordered_records.append(item)
    input_hashes = manifest["input_file_sha256"]
    normalized_hashes: dict[str, str] | list[str]
    if isinstance(input_hashes, dict):
        normalized_hashes = {}
        for key, value in input_hashes.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ManifestValidationError("manifest input_file_sha256 mapping is invalid")
            normalized_hashes[key] = value
    elif isinstance(input_hashes, list):
        normalized_hashes = []
        for item in input_hashes:
            if not isinstance(item, str):
                raise ManifestValidationError("manifest input_file_sha256 sequence is invalid")
            normalized_hashes.append(item)
    else:
        raise ManifestValidationError("manifest input_file_sha256 is invalid")
    return DatasetDescriptor(
        schema_name=_manifest_string(manifest, "schema_name"),
        schema_version=_manifest_string(manifest, "schema_version"),
        source_id=_manifest_string(manifest, "source_id"),
        license_id=_manifest_string(manifest, "license_id"),
        instrument_coverage=tuple(_manifest_string_list(manifest, "instrument_coverage")),
        interval=_manifest_string(manifest, "interval"),
        exchange_timezone=_manifest_string(manifest, "exchange_timezone"),
        calendar_id=_manifest_string(manifest, "calendar_id"),
        ordered_canonical_records=tuple(ordered_records),
        quality_result=quality,
        input_file_sha256=normalized_hashes,
    )


def _manifest_string(manifest: Mapping[str, object], field: str) -> str:
    value = manifest.get(field)
    if not isinstance(value, str) or not value:
        raise ManifestValidationError(f"manifest {field} is invalid")
    return value


def _validate_manifest_structure(manifest: Mapping[str, object]) -> None:
    required = {
        "manifest_schema_name",
        "manifest_schema_version",
        "schema_name",
        "schema_version",
        "source_id",
        "license_id",
        "instrument_coverage",
        "interval",
        "exchange_timezone",
        "calendar_id",
        "ordered_canonical_records",
        "quality",
        "input_file_sha256",
        "data_version",
        "bound_record_ids",
    }
    if "data_manifest_sha256" in manifest:
        raise ManifestValidationError("manifest must not contain data_manifest_sha256")
    if set(manifest) != required:
        raise ManifestValidationError("manifest fields are incomplete or unknown")
    expected_values = {
        "manifest_schema_name": MANIFEST_SCHEMA_NAME,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "schema_name": "Bar",
        "schema_version": "1.0.0",
    }
    for field, expected in expected_values.items():
        if manifest.get(field) != expected:
            raise ManifestValidationError(f"manifest {field} is invalid")
    for field in (
        "source_id",
        "license_id",
        "interval",
        "exchange_timezone",
        "calendar_id",
        "data_version",
    ):
        _manifest_string(manifest, field)
    data_version = _manifest_string(manifest, "data_version")
    if len(data_version) != 64 or any(
        character not in "0123456789abcdef" for character in data_version
    ):
        raise ManifestValidationError("manifest data_version is not a lowercase SHA-256")
    _manifest_string_list(manifest, "instrument_coverage")
    _manifest_string_list(manifest, "bound_record_ids", allow_empty=True)


def verify_input_file_sha256(path: Path, expected_sha256: str) -> None:
    """Verify the explicit source checksum; paths never enter identity."""

    if sha256_file(path) != expected_sha256:
        raise ManifestValidationError(f"input file checksum mismatch: {path.name}")


def manifest_to_json(result: ManifestResult) -> str:
    return result.manifest_bytes.decode("utf-8")


def load_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestValidationError("manifest file is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ManifestValidationError("manifest JSON root must be an object")
    return payload


build_dataset_manifest = build_manifest
