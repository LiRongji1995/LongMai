"""Fail-closed quality validation and caller-owned quarantine evidence."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from .canonical import canonical_record_bytes, sha256_file
from .models import Bar, QualityIssue, QualityStatus, ValidationError

SYNTHETIC_POLICY_LABEL = "SYNTHETIC_TEST_ONLY"


class QualityPolicyError(ValidationError):
    """Raised for missing or unsafe quality policy configuration."""


class QuarantineWriteError(ValidationError):
    """Raised when quarantine evidence cannot be written completely."""


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    """Explicit test-only latency policy; no production thresholds are supplied."""

    policy_id: str
    late_after: timedelta | None = None
    stale_after: timedelta | None = None
    label: str = SYNTHETIC_POLICY_LABEL

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise QualityPolicyError("policy_id is required")
        if self.label != SYNTHETIC_POLICY_LABEL:
            raise QualityPolicyError("only SYNTHETIC_TEST_ONLY quality policies are supported")
        for field_name, threshold in (
            ("late_after", self.late_after),
            ("stale_after", self.stale_after),
        ):
            if threshold is not None and (
                not isinstance(threshold, timedelta) or threshold < timedelta(0)
            ):
                raise QualityPolicyError(f"{field_name} must be a non-negative timedelta")

    def to_mapping(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "label": self.label,
            "late_after_microseconds": (
                None if self.late_after is None else self.late_after // timedelta(microseconds=1)
            ),
            "stale_after_microseconds": (
                None if self.stale_after is None else self.stale_after // timedelta(microseconds=1)
            ),
        }


@dataclass(frozen=True, slots=True)
class QualityResult:
    """Deterministic quality outcome and the records safe for downstream use."""

    status: QualityStatus
    issues: tuple[str, ...]
    accepted_records: tuple[Bar, ...]
    missing_record_ids: tuple[str, ...] = ()
    duplicate_record_ids: tuple[str, ...] = ()
    idempotent_replays: tuple[str, ...] = ()
    out_of_order_record_ids: tuple[str, ...] = ()
    late_record_ids: tuple[str, ...] = ()
    stale_record_ids: tuple[str, ...] = ()
    rejected_record_ids: tuple[str, ...] = ()
    policy_id: str | None = None
    deterministic_reason: str = "valid"

    @property
    def has_invalid(self) -> bool:
        return QualityIssue.INVALID.value in self.issues

    @property
    def downstream_records(self) -> tuple[Bar, ...]:
        if self.status in {QualityStatus.QUARANTINED, QualityStatus.REJECTED}:
            return ()
        return self.accepted_records

    def to_mapping(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "issues": list(self.issues),
            "accepted_record_ids": [record.record_id for record in self.accepted_records],
            "missing_record_ids": list(self.missing_record_ids),
            "duplicate_record_ids": list(self.duplicate_record_ids),
            "idempotent_replays": list(self.idempotent_replays),
            "out_of_order_record_ids": list(self.out_of_order_record_ids),
            "late_record_ids": list(self.late_record_ids),
            "stale_record_ids": list(self.stale_record_ids),
            "rejected_record_ids": list(self.rejected_record_ids),
            "policy_id": self.policy_id,
            "deterministic_reason": self.deterministic_reason,
        }


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _invalid_result(reason: str, record_ids: Iterable[str] = ()) -> QualityResult:
    identifiers = _unique_sorted(record_ids)
    return QualityResult(
        status=QualityStatus.REJECTED,
        issues=(QualityIssue.INVALID.value,),
        accepted_records=(),
        rejected_record_ids=identifiers,
        deterministic_reason=reason,
    )


def validate_quality(
    records: Sequence[Bar],
    *,
    expected_record_ids: Iterable[str] | None = None,
    policy: QualityPolicy | None,
) -> QualityResult:
    """Validate duplicates, ordering, missing intervals, and explicit latency policy.

    No default late/stale threshold exists.  Omitting ``policy`` is a
    deterministic rejection, which keeps the quality gate fail-closed.
    """

    if policy is None:
        return _invalid_result("quality policy missing; fail closed")
    try:
        policy = QualityPolicy(
            policy_id=policy.policy_id,
            late_after=policy.late_after,
            stale_after=policy.stale_after,
            label=policy.label,
        )
    except QualityPolicyError as error:
        return _invalid_result(str(error))

    materialized = tuple(records)
    if any(not isinstance(record, Bar) for record in materialized):
        return _invalid_result("quality validation requires validated Bar objects")

    ordered = tuple(sorted(materialized, key=lambda item: (*item.identity_key(), item.record_id)))
    out_of_order: tuple[str, ...] = ()
    if tuple((*item.identity_key(), item.record_id) for item in materialized) != tuple(
        (*item.identity_key(), item.record_id) for item in ordered
    ):
        out_of_order = _unique_sorted(item.record_id for item in materialized)

    by_record_id: dict[str, Bar] = {}
    duplicate_ids: set[str] = set()
    idempotent_replays: set[str] = set()
    conflicting_ids: set[str] = set()
    for record in materialized:
        previous = by_record_id.get(record.record_id)
        if previous is None:
            by_record_id[record.record_id] = record
            continue
        duplicate_ids.add(record.record_id)
        if canonical_record_bytes(previous) == canonical_record_bytes(record):
            idempotent_replays.add(record.record_id)
        else:
            conflicting_ids.add(record.record_id)

    deduplicated = tuple(
        sorted(by_record_id.values(), key=lambda item: (*item.identity_key(), item.record_id))
    )
    identity_groups: dict[tuple[str, str, str, str, str], list[str]] = {}
    for record in deduplicated:
        identity_groups.setdefault(record.identity_key(), []).append(record.record_id)
    identity_duplicate_ids = {
        record_id
        for record_ids in identity_groups.values()
        if len(record_ids) > 1
        for record_id in record_ids
    }

    expected = set(expected_record_ids or ())
    missing = _unique_sorted(expected - set(by_record_id))
    late = {
        record.record_id
        for record in deduplicated
        if policy.late_after is not None
        and record.ingestion_time - record.availability_time > policy.late_after
    }
    stale = {
        record.record_id
        for record in deduplicated
        if policy.stale_after is not None
        and record.as_of_time - record.availability_time > policy.stale_after
    }
    existing_invalid = {
        record.record_id
        for record in deduplicated
        if QualityIssue.INVALID.value in record.quality_issues
        or record.quality_status is QualityStatus.REJECTED
    }
    existing_quarantine = {
        record.record_id
        for record in deduplicated
        if record.quality_status is QualityStatus.QUARANTINED
    }

    fatal = set(out_of_order) | conflicting_ids | identity_duplicate_ids | existing_invalid
    quarantined = late | stale | existing_quarantine
    rejected = fatal | quarantined
    issues: list[str] = []
    for issue, present in (
        (QualityIssue.MISSING.value, bool(missing)),
        (QualityIssue.DUPLICATE.value, bool(duplicate_ids or identity_duplicate_ids)),
        (QualityIssue.LATE.value, bool(late)),
        (QualityIssue.STALE.value, bool(stale)),
        (QualityIssue.INVALID.value, bool(fatal)),
    ):
        if present:
            issues.append(issue)

    accepted = tuple(record for record in deduplicated if record.record_id not in rejected)
    if fatal:
        status = QualityStatus.REJECTED
    elif quarantined:
        status = QualityStatus.QUARANTINED
    elif issues:
        status = QualityStatus.SUSPECT
    else:
        status = QualityStatus.VALID
    reason_parts = [
        f"policy={policy.policy_id}",
        f"status={status.value}",
        *(f"{issue.lower()}={issue in issues}" for issue in issues),
    ]
    return QualityResult(
        status=status,
        issues=tuple(issues),
        accepted_records=accepted,
        missing_record_ids=missing,
        duplicate_record_ids=_unique_sorted(duplicate_ids | identity_duplicate_ids),
        idempotent_replays=_unique_sorted(idempotent_replays),
        out_of_order_record_ids=out_of_order,
        late_record_ids=_unique_sorted(late),
        stale_record_ids=_unique_sorted(stale),
        rejected_record_ids=_unique_sorted(rejected),
        policy_id=policy.policy_id,
        deterministic_reason=";".join(reason_parts),
    )


def validate_dataset(
    records: Sequence[Bar],
    *,
    expected_record_ids: Iterable[str] | None = None,
    policy: QualityPolicy | None,
) -> QualityResult:
    """Alias emphasizing that missing is a dataset/interval result."""

    return validate_quality(records, expected_record_ids=expected_record_ids, policy=policy)


def parse_and_validate(
    payloads: Sequence[Mapping[str, object]],
    *,
    expected_record_ids: Iterable[str] | None = None,
    policy: QualityPolicy | None,
) -> QualityResult:
    """Parse strict mappings and convert any parse failure into INVALID."""

    parsed: list[Bar] = []
    invalid_ids: list[str] = []
    for index, payload in enumerate(payloads):
        record_id = payload.get("record_id", f"payload-{index:04d}")
        try:
            parsed.append(Bar.from_mapping(payload))
        except ValidationError:
            invalid_ids.append(record_id if isinstance(record_id, str) else f"payload-{index:04d}")
    result = validate_quality(parsed, expected_record_ids=expected_record_ids, policy=policy)
    if not invalid_ids:
        return result
    all_rejected = _unique_sorted((*result.rejected_record_ids, *invalid_ids))
    issues = tuple(dict.fromkeys((*result.issues, QualityIssue.INVALID.value)))
    return QualityResult(
        status=QualityStatus.REJECTED,
        issues=issues,
        accepted_records=(),
        missing_record_ids=result.missing_record_ids,
        duplicate_record_ids=result.duplicate_record_ids,
        idempotent_replays=result.idempotent_replays,
        out_of_order_record_ids=result.out_of_order_record_ids,
        late_record_ids=result.late_record_ids,
        stale_record_ids=result.stale_record_ids,
        rejected_record_ids=all_rejected,
        policy_id=result.policy_id,
        deterministic_reason=f"{result.deterministic_reason};invalid_parse=true",
    )


def _safe_component(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or ":" in value
    ):
        raise QuarantineWriteError(f"unsafe quarantine {field}")
    return value


def quarantine_evidence(
    root: Path,
    *,
    data_version: str,
    record_id: str,
    quality_status: QualityStatus,
    quality_issues: Sequence[str],
    deterministic_reason: str,
    source_fixture_sha256: str,
    code_commit: str,
) -> Path:
    """Write one deterministic evidence file below a caller-owned root."""

    safe_version = _safe_component(data_version, "data_version")
    safe_record = _safe_component(record_id, "record_id")
    if not isinstance(quality_status, QualityStatus):
        raise QuarantineWriteError("quality_status must be a QualityStatus")
    if not isinstance(deterministic_reason, str) or not deterministic_reason:
        raise QuarantineWriteError("deterministic reason is required")
    if (
        not isinstance(source_fixture_sha256, str)
        or len(source_fixture_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_fixture_sha256)
    ):
        raise QuarantineWriteError("source fixture SHA-256 is invalid")
    if not isinstance(code_commit, str) or not code_commit:
        raise QuarantineWriteError("code commit or placeholder is required")
    allowed = {item.value for item in QualityIssue}
    if any(issue not in allowed for issue in quality_issues):
        raise QuarantineWriteError("quarantine quality issue is not a contract reason code")
    evidence = {
        "data_version": data_version,
        "record_id": record_id,
        "quality_status": quality_status.value,
        "quality_issues": list(dict.fromkeys(quality_issues)),
        "deterministic_reason": deterministic_reason,
        "source_fixture_sha256": source_fixture_sha256,
        "code_commit": code_commit,
    }
    target = root / safe_version / f"{safe_record}.json"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError as error:
        raise QuarantineWriteError("quarantine write failed; fail closed") from error
    return target


def quarantine_result(
    result: QualityResult,
    root: Path,
    *,
    data_version: str,
    source_fixture: Path,
    code_commit: str,
    records: Sequence[Bar] | None = None,
) -> tuple[Path, ...]:
    """Quarantine all rejected/quarantined records; missing IDs have no fake Bar."""

    by_id = {record.record_id: record for record in (records or result.accepted_records)}
    source_sha = sha256_file(source_fixture)
    paths: list[Path] = []
    for record_id in result.rejected_record_ids:
        record = by_id.get(record_id)
        issues = result.issues if record is None else record.quality_issues or result.issues
        status = (
            QualityStatus.REJECTED if result.status is QualityStatus.REJECTED else result.status
        )
        paths.append(
            quarantine_evidence(
                root,
                data_version=data_version,
                record_id=record_id,
                quality_status=status,
                quality_issues=issues or (QualityIssue.INVALID.value,),
                deterministic_reason=result.deterministic_reason,
                source_fixture_sha256=source_sha,
                code_commit=code_commit,
            )
        )
    return tuple(paths)
