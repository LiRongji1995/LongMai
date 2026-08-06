"""Canonical JSON and deterministic dataset identity for synthetic Bars."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .models import (
    BAR_SCHEMA_NAME,
    BAR_SCHEMA_VERSION,
    DERIVED_BAR_FIELDS,
    Bar,
    ValidationError,
)


class CanonicalizationError(ValidationError):
    """Raised when an identity input cannot be represented canonically."""


def _reject_identity_values(value: object, path: str = "$") -> None:
    if isinstance(value, float):
        raise CanonicalizationError(f"float is not allowed in identity input at {path}")
    if isinstance(value, bytes | bytearray | memoryview):
        raise CanonicalizationError(f"binary value is not allowed in identity input at {path}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"identity mapping keys must be strings at {path}")
            _reject_identity_values(item, f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _reject_identity_values(item, f"{path}[{index}]")
        return
    if value is not None and not isinstance(value, str | int | bool):
        raise CanonicalizationError(f"unsupported identity value at {path}: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Serialize JSON using UTF-8, sorted keys, and stable separators."""

    _reject_identity_values(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CanonicalizationError("identity input is not canonical JSON") from error


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise CanonicalizationError(f"unable to read input file: {path.name}") from error
    return digest.hexdigest()


def _bar_from_value(value: Bar | Mapping[str, object]) -> Bar:
    if isinstance(value, Bar):
        return value
    return Bar.from_mapping(value)


def canonical_bar_payload(value: Bar | Mapping[str, object]) -> dict[str, object]:
    """Return a Bar logical payload with derived lineage fields excluded."""

    payload = _bar_from_value(value).to_mapping(include_derived=False)
    if set(payload) & DERIVED_BAR_FIELDS:
        raise CanonicalizationError("derived Bar fields must not enter data_version identity")
    return payload


def canonical_record_bytes(value: Bar | Mapping[str, object]) -> bytes:
    return canonical_json_bytes(canonical_bar_payload(value))


def record_sort_key(value: Bar) -> tuple[str, str, str, str, str, str]:
    return (*value.identity_key(), value.record_id)


def canonicalize_records(
    records: Sequence[Bar], *, reject_out_of_order: bool = True
) -> tuple[Bar, ...]:
    """Return stable record order, optionally rejecting unordered input.

    Quality validation calls this with the default so an out-of-order source is
    rejected rather than silently normalized.  A caller that has explicitly
    recorded an INVALID issue may request a normalized view with
    ``reject_out_of_order=False``.
    """

    materialized = tuple(records)
    if any(not isinstance(record, Bar) for record in materialized):
        raise ValidationError("canonicalization requires validated Bar objects")
    record_ids = [record.record_id for record in materialized]
    if len(record_ids) != len(set(record_ids)):
        raise ValidationError("record_id must be unique within a data_version")
    identity_keys = [record.identity_key() for record in materialized]
    if len(identity_keys) != len(set(identity_keys)):
        raise ValidationError("Bar identity must be unique within a data_version")
    ordered = tuple(sorted(materialized, key=record_sort_key))
    if reject_out_of_order and tuple(record_sort_key(item) for item in materialized) != tuple(
        record_sort_key(item) for item in ordered
    ):
        raise ValidationError("records are out of order; input order was not silently normalized")
    return ordered


def _relative_input_file_hashes(
    value: Mapping[str, str] | Sequence[str],
) -> dict[str, str] | list[str]:
    def normalize_path(path: object) -> str:
        if not isinstance(path, str) or not path:
            raise CanonicalizationError(
                "input file paths must be non-empty POSIX-relative strings"
            )
        if "\\" in path:
            raise CanonicalizationError("input file paths must use POSIX separators")
        posix_path = PurePosixPath(path)
        windows_path = PureWindowsPath(path)
        if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
            raise CanonicalizationError("absolute paths must not enter dataset identity")
        if (
            windows_path.root
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or posix_path.as_posix() != path
        ):
            raise CanonicalizationError(
                "input file paths must be absolute-free and safe POSIX-relative paths"
            )
        return path

    if isinstance(value, Mapping):
        if not value:
            raise CanonicalizationError("input file SHA-256 mapping must not be empty")
        normalized: dict[str, str] = {}
        for raw_path, digest in value.items():
            path = normalize_path(raw_path)
            if not isinstance(digest, str) or len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise CanonicalizationError("input file SHA-256 must be lowercase hexadecimal")
            if path in normalized:
                raise CanonicalizationError("input file paths must not normalize to duplicates")
            normalized[path] = digest
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, str):
        raise CanonicalizationError("input file SHA-256 sequence is invalid")
    try:
        hashes = list(value)
    except TypeError as error:
        raise CanonicalizationError("input file SHA-256 sequence is invalid") from error
    if not hashes:
        raise CanonicalizationError("input file SHA-256 sequence must not be empty")
    if any(
        not isinstance(item, str)
        or len(item) != 64
        or any(character not in "0123456789abcdef" for character in item)
        for item in hashes
    ):
        raise CanonicalizationError("input file SHA-256 sequence is invalid")
    if len(hashes) != len(set(hashes)):
        raise CanonicalizationError("input file SHA-256 sequence must not contain duplicates")
    return sorted(hashes)


def _quality_mapping(value: object) -> object:
    converter = getattr(value, "to_mapping", None)
    if callable(converter):
        value = converter()
    return value


@dataclass(frozen=True, slots=True)
class DatasetDescriptor:
    """All explicit inputs to the non-circular data_version calculation."""

    schema_name: str
    schema_version: str
    source_id: str
    license_id: str
    instrument_coverage: tuple[str, ...]
    interval: str
    exchange_timezone: str
    calendar_id: str
    ordered_canonical_records: tuple[dict[str, object], ...]
    quality_result: object
    input_file_sha256: dict[str, str] | list[str]

    @classmethod
    def from_records(
        cls,
        records: Sequence[Bar],
        *,
        source_id: str,
        license_id: str,
        instrument_coverage: Sequence[str] | None = None,
        interval: str = "1d",
        exchange_timezone: str,
        calendar_id: str,
        quality_result: object,
        input_file_sha256: Mapping[str, str] | Sequence[str],
        schema_name: str = BAR_SCHEMA_NAME,
        schema_version: str = BAR_SCHEMA_VERSION,
    ) -> DatasetDescriptor:
        ordered = canonicalize_records(records)
        for field_name, field_value in (
            ("schema_name", schema_name),
            ("schema_version", schema_version),
            ("source_id", source_id),
            ("license_id", license_id),
            ("interval", interval),
            ("exchange_timezone", exchange_timezone),
            ("calendar_id", calendar_id),
        ):
            if not isinstance(field_value, str) or not field_value:
                raise CanonicalizationError(f"{field_name} must be a non-empty string")
        if not ordered:
            raise CanonicalizationError("records must not be empty")
        for record in ordered:
            if record.schema_name != schema_name:
                raise CanonicalizationError("record schema_name does not match descriptor")
            if record.schema_version != schema_version:
                raise CanonicalizationError("record schema_version does not match descriptor")
            if record.source != source_id:
                raise CanonicalizationError("record source does not match source_id")
            if record.exchange_timezone != exchange_timezone:
                raise CanonicalizationError("record timezone does not match exchange_timezone")
            if record.resolution.value != interval:
                raise CanonicalizationError("record resolution does not match interval")
        actual_coverage = tuple(sorted({record.instrument_id for record in ordered}))
        if instrument_coverage is None:
            coverage = actual_coverage
        else:
            supplied_coverage = tuple(instrument_coverage)
            if any(not isinstance(item, str) or not item for item in supplied_coverage):
                raise CanonicalizationError("instrument coverage must contain stable IDs")
            if len(supplied_coverage) != len(set(supplied_coverage)):
                raise CanonicalizationError("instrument coverage must not contain duplicates")
            coverage = tuple(sorted(supplied_coverage))
            if coverage != actual_coverage:
                raise CanonicalizationError(
                    "instrument coverage does not match record instrument IDs"
                )
        return cls(
            schema_name=schema_name,
            schema_version=schema_version,
            source_id=source_id,
            license_id=license_id,
            instrument_coverage=coverage,
            interval=interval,
            exchange_timezone=exchange_timezone,
            calendar_id=calendar_id,
            ordered_canonical_records=tuple(canonical_bar_payload(record) for record in ordered),
            quality_result=_quality_mapping(quality_result),
            input_file_sha256=_relative_input_file_hashes(input_file_sha256),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_name": self.schema_name,
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "license_id": self.license_id,
            "instrument_coverage": list(self.instrument_coverage),
            "interval": self.interval,
            "exchange_timezone": self.exchange_timezone,
            "calendar_id": self.calendar_id,
            "ordered_canonical_records": list(self.ordered_canonical_records),
            "quality_result": self.quality_result,
            "input_file_sha256": self.input_file_sha256,
        }


def compute_data_version(
    records: Sequence[Bar],
    *,
    source_id: str,
    license_id: str,
    exchange_timezone: str,
    calendar_id: str,
    quality_result: object,
    input_file_sha256: Mapping[str, str] | Sequence[str],
    instrument_coverage: Sequence[str] | None = None,
    interval: str = "1d",
    schema_name: str = BAR_SCHEMA_NAME,
    schema_version: str = BAR_SCHEMA_VERSION,
) -> str:
    """Compute the lowercase SHA-256 of the canonical dataset descriptor."""

    descriptor = DatasetDescriptor.from_records(
        records,
        source_id=source_id,
        license_id=license_id,
        instrument_coverage=instrument_coverage,
        interval=interval,
        exchange_timezone=exchange_timezone,
        calendar_id=calendar_id,
        quality_result=quality_result,
        input_file_sha256=input_file_sha256,
        schema_name=schema_name,
        schema_version=schema_version,
    )
    return sha256_bytes(canonical_json_bytes(descriptor.to_mapping()))


data_version_for_dataset = compute_data_version
