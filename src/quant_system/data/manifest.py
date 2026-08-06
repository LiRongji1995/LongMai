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
from .quality import QualityPolicy, QualityResult

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
    return {
        "policy": policy.to_mapping(),
        "result": quality_result.to_mapping(),
    }


def _build_manifest_mapping(
    descriptor: DatasetDescriptor,
    *,
    data_version: str,
    quality_identity: dict[str, object],
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
    if quality_result.policy_id != quality_policy.policy_id:
        raise ManifestValidationError("quality policy does not match quality result")
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
    manifest = _build_manifest_mapping(
        descriptor,
        data_version=data_version,
        quality_identity=identity_quality,
    )
    if "data_manifest_sha256" in manifest:
        raise ManifestValidationError("manifest hash must not be a manifest input")
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_sha = sha256_bytes(manifest_bytes)
    bound_records = tuple(
        record.with_lineage(data_version=data_version, data_manifest_sha256=manifest_sha)
        for record in descriptor_records(descriptor, records)
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
    """Bind both derived lineage fields to records after manifest creation."""

    if result.data_manifest_sha256 != compute_manifest_sha256(result.manifest):
        raise ManifestValidationError("ManifestResult contains an invalid manifest SHA")
    ordered = descriptor_records(
        DatasetDescriptor(
            schema_name=str(result.manifest["schema_name"]),
            schema_version=str(result.manifest["schema_version"]),
            source_id=str(result.manifest["source_id"]),
            license_id=str(result.manifest["license_id"]),
            instrument_coverage=tuple(result.manifest["instrument_coverage"]),  # type: ignore[arg-type]
            interval=str(result.manifest["interval"]),
            exchange_timezone=str(result.manifest["exchange_timezone"]),
            calendar_id=str(result.manifest["calendar_id"]),
            ordered_canonical_records=tuple(result.manifest["ordered_canonical_records"]),  # type: ignore[arg-type]
            quality_result=result.manifest["quality"],
            input_file_sha256=result.manifest["input_file_sha256"],  # type: ignore[arg-type]
        ),
        records,
    )
    return tuple(
        record.with_lineage(
            data_version=result.data_version,
            data_manifest_sha256=result.data_manifest_sha256,
        )
        for record in ordered
    )


def verify_manifest(
    manifest: Mapping[str, object],
    *,
    expected_manifest_sha256: str,
    expected_data_version: str | None = None,
    records: Sequence[Bar] | None = None,
) -> None:
    """Fail closed on manifest hash, record, or data_version mismatch."""

    if "data_manifest_sha256" in manifest:
        raise ManifestValidationError("manifest must not contain data_manifest_sha256")
    actual_sha = compute_manifest_sha256(manifest)
    if actual_sha != expected_manifest_sha256:
        raise ManifestValidationError(
            "data_manifest_sha256 does not match canonical manifest bytes"
        )
    data_version = manifest.get("data_version")
    if not isinstance(data_version, str):
        raise ManifestValidationError("manifest data_version is missing")
    if expected_data_version is not None and data_version != expected_data_version:
        raise ManifestValidationError("manifest data_version mismatch")
    if records is not None:
        expected_records = manifest.get("ordered_canonical_records")
        actual_records = [
            canonical_bar_payload(record)
            for record in sorted(records, key=lambda item: (*item.identity_key(), item.record_id))
        ]
        if expected_records != actual_records:
            raise ManifestValidationError("manifest canonical record list mismatch")


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
