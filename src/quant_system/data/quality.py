"""Fail-closed quality validation and caller-owned quarantine evidence."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from .canonical import (
    canonical_bar_payload,
    canonical_json_bytes,
    canonical_record_bytes,
    sha256_bytes,
    sha256_file,
)
from .models import Bar, QualityIssue, QualityStatus, ValidationError

SYNTHETIC_POLICY_LABEL = "SYNTHETIC_TEST_ONLY"
QUALITY_ISSUE_ORDER = tuple(issue.value for issue in QualityIssue)
EXPECTATION_MODE_EXPLICIT = "EXPLICIT"
EXPECTATION_MODE_OBSERVED_ONLY = "OBSERVED_ONLY"
QUALITY_STATUS_ORDER = tuple(status.value for status in QualityStatus)


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
    allowed_suspect_issues: frozenset[str] = frozenset()

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
        try:
            allowed = frozenset(self.allowed_suspect_issues)
        except TypeError as error:
            raise QualityPolicyError("allowed_suspect_issues must be an immutable set") from error
        if any(not isinstance(issue, str) for issue in allowed):
            raise QualityPolicyError("allowed_suspect_issues must contain strings")
        unsupported = allowed - set(QUALITY_ISSUE_ORDER)
        if unsupported:
            raise QualityPolicyError("allowed_suspect_issues contains an unsupported reason code")
        if QualityIssue.INVALID.value in allowed:
            raise QualityPolicyError("INVALID must not be allowed for SUSPECT downstream")
        if QualityIssue.LATE.value in allowed and self.late_after is None:
            raise QualityPolicyError("LATE allowance requires late_after")
        if QualityIssue.STALE.value in allowed and self.stale_after is None:
            raise QualityPolicyError("STALE allowance requires stale_after")
        object.__setattr__(self, "allowed_suspect_issues", allowed)

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
            "allowed_suspect_issues": sorted(
                self.allowed_suspect_issues,
                key=lambda issue: QUALITY_ISSUE_ORDER.index(issue),
            ),
        }

    @property
    def policy_sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.to_mapping()))

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> QualityPolicy:
        required = {
            "policy_id",
            "label",
            "late_after_microseconds",
            "stale_after_microseconds",
            "allowed_suspect_issues",
        }
        if set(mapping) != required:
            raise QualityPolicyError("quality policy mapping is incomplete or has unknown fields")
        late = mapping["late_after_microseconds"]
        stale = mapping["stale_after_microseconds"]
        if late is not None and type(late) is not int:
            raise QualityPolicyError("late_after_microseconds must be an integer or null")
        if stale is not None and type(stale) is not int:
            raise QualityPolicyError("stale_after_microseconds must be an integer or null")
        allowed = mapping["allowed_suspect_issues"]
        if not isinstance(allowed, list):
            raise QualityPolicyError("allowed_suspect_issues must be an array")
        return cls(
            policy_id=mapping["policy_id"],  # type: ignore[arg-type]
            label=mapping["label"],  # type: ignore[arg-type]
            late_after=None if late is None else timedelta(microseconds=late),
            stale_after=None if stale is None else timedelta(microseconds=stale),
            allowed_suspect_issues=frozenset(allowed),
        )


@dataclass(frozen=True, slots=True)
class QualityResult:
    """Deterministic quality outcome and the records safe for downstream use."""

    status: QualityStatus
    issues: tuple[str, ...]
    accepted_records: tuple[Bar, ...]
    input_records_sha256: str | None = None
    expected_record_ids: tuple[str, ...] = ()
    expectation_mode: str = EXPECTATION_MODE_OBSERVED_ONLY
    record_quality_statuses: tuple[tuple[str, str], ...] = ()
    missing_record_ids: tuple[str, ...] = ()
    duplicate_record_ids: tuple[str, ...] = ()
    idempotent_replays: tuple[str, ...] = ()
    out_of_order_record_ids: tuple[str, ...] = ()
    late_record_ids: tuple[str, ...] = ()
    stale_record_ids: tuple[str, ...] = ()
    rejected_record_ids: tuple[str, ...] = ()
    policy_id: str | None = None
    policy_sha256: str | None = None
    policy: QualityPolicy | None = None
    record_quality_issues: tuple[tuple[str, tuple[str, ...]], ...] = ()
    record_deterministic_reasons: tuple[tuple[str, str], ...] = ()
    deterministic_reason: str = "valid"

    @property
    def has_invalid(self) -> bool:
        return QualityIssue.INVALID.value in self.issues

    @property
    def record_issue_map(self) -> dict[str, tuple[str, ...]]:
        return dict(self.record_quality_issues)

    @property
    def record_status_map(self) -> dict[str, str]:
        return dict(self.record_quality_statuses)

    @property
    def record_reason_map(self) -> dict[str, str]:
        return dict(self.record_deterministic_reasons)

    @property
    def downstream_allowed(self) -> bool:
        if self.status is QualityStatus.VALID:
            return not self.issues and all(not issues for _, issues in self.record_quality_issues)
        if self.status is not QualityStatus.SUSPECT or not self.issues:
            return False
        if self.has_invalid or self.policy is None:
            return False
        if self.policy_sha256 != self.policy.policy_sha256:
            return False
        return set(self.issues).issubset(self.policy.allowed_suspect_issues) and all(
            set(issues).issubset(self.policy.allowed_suspect_issues)
            for _, issues in self.record_quality_issues
        )

    @property
    def downstream_records(self) -> tuple[Bar, ...]:
        if not self.downstream_allowed:
            return ()
        return self.accepted_records

    def to_mapping(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "issues": list(self.issues),
            "accepted_record_ids": [record.record_id for record in self.accepted_records],
            "input_records_sha256": self.input_records_sha256,
            "expected_record_ids": list(self.expected_record_ids),
            "expectation_mode": self.expectation_mode,
            "record_quality_statuses": [
                {"record_id": record_id, "quality_status": status}
                for record_id, status in self.record_quality_statuses
            ],
            "missing_record_ids": list(self.missing_record_ids),
            "duplicate_record_ids": list(self.duplicate_record_ids),
            "idempotent_replays": list(self.idempotent_replays),
            "out_of_order_record_ids": list(self.out_of_order_record_ids),
            "late_record_ids": list(self.late_record_ids),
            "stale_record_ids": list(self.stale_record_ids),
            "rejected_record_ids": list(self.rejected_record_ids),
            "policy_id": self.policy_id,
            "policy_sha256": self.policy_sha256,
            "record_quality_issues": {
                record_id: list(issues) for record_id, issues in self.record_quality_issues
            },
            "record_deterministic_reasons": {
                record_id: reason
                for record_id, reason in self.record_deterministic_reasons
            },
            "downstream_allowed": self.downstream_allowed,
            "deterministic_reason": self.deterministic_reason,
        }


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _unique_sorted_issues(values: Iterable[str]) -> tuple[str, ...]:
    unique = set(values)
    return tuple(sorted(unique, key=lambda issue: QUALITY_ISSUE_ORDER.index(issue)))


def _freeze_record_issues(
    record_issues: Mapping[str, Iterable[str]],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (record_id, _unique_sorted_issues(issues))
        for record_id, issues in sorted(record_issues.items())
    )


def _freeze_record_statuses(
    record_statuses: Mapping[str, QualityStatus | str],
) -> tuple[tuple[str, str], ...]:
    frozen: list[tuple[str, str]] = []
    for record_id, status in sorted(record_statuses.items()):
        status_value = status.value if isinstance(status, QualityStatus) else status
        if status_value not in QUALITY_STATUS_ORDER:
            raise ValidationError("record quality status is invalid")
        frozen.append((record_id, status_value))
    return tuple(frozen)


def _freeze_record_reasons(record_reasons: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    if any(not isinstance(reason, str) or not reason for reason in record_reasons.values()):
        raise ValidationError("record deterministic reason is invalid")
    return tuple(sorted(record_reasons.items()))


def _normalize_expected_record_ids(
    expected_record_ids: Iterable[str] | None,
) -> tuple[str, tuple[str, ...]]:
    if expected_record_ids is None:
        return EXPECTATION_MODE_OBSERVED_ONLY, ()
    if isinstance(expected_record_ids, str | bytes | bytearray | memoryview):
        raise ValidationError("expected_record_ids must be an iterable of record ID strings")
    try:
        values = tuple(expected_record_ids)
    except TypeError as error:
        raise ValidationError("expected_record_ids must be iterable") from error
    if any(
        type(value) is not str or not value or value.strip() != value for value in values
    ):
        raise ValidationError("expected_record_ids must contain non-empty strings")
    if len(values) != len(set(values)):
        raise ValidationError("expected_record_ids must not contain duplicates")
    return EXPECTATION_MODE_EXPLICIT, tuple(sorted(values))


def _input_records_sha256(records: Sequence[Bar]) -> str | None:
    if any(not isinstance(record, Bar) for record in records):
        return None
    return sha256_bytes(
        canonical_json_bytes([canonical_bar_payload(record) for record in records])
    )


def _invalid_result(
    reason: str,
    record_ids: Iterable[str] = (),
    *,
    policy: QualityPolicy | None = None,
    record_quality_issues: Mapping[str, Iterable[str]] | None = None,
    input_records_sha256: str | None = None,
    expected_record_ids: Iterable[str] = (),
    expectation_mode: str = EXPECTATION_MODE_OBSERVED_ONLY,
) -> QualityResult:
    identifiers = _unique_sorted(record_ids)
    return QualityResult(
        status=QualityStatus.REJECTED,
        issues=(QualityIssue.INVALID.value,),
        accepted_records=(),
        input_records_sha256=input_records_sha256,
        expected_record_ids=tuple(expected_record_ids),
        expectation_mode=expectation_mode,
        rejected_record_ids=identifiers,
        policy_id=None if policy is None else policy.policy_id,
        policy_sha256=None if policy is None else policy.policy_sha256,
        policy=policy,
        record_quality_issues=_freeze_record_issues(record_quality_issues or {}),
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

    try:
        expectation_mode, normalized_expected_ids = _normalize_expected_record_ids(
            expected_record_ids
        )
    except ValidationError as error:
        return _invalid_result(str(error))
    if policy is None:
        return _invalid_result(
            "quality policy missing; fail closed",
            expected_record_ids=normalized_expected_ids,
            expectation_mode=expectation_mode,
        )
    try:
        policy = QualityPolicy(
            policy_id=policy.policy_id,
            late_after=policy.late_after,
            stale_after=policy.stale_after,
            label=policy.label,
            allowed_suspect_issues=policy.allowed_suspect_issues,
        )
    except QualityPolicyError as error:
        return _invalid_result(
            str(error),
            expected_record_ids=normalized_expected_ids,
            expectation_mode=expectation_mode,
        )

    try:
        materialized = tuple(records)
    except TypeError:
        return _invalid_result(
            "quality validation requires an iterable of records",
            policy=policy,
            expected_record_ids=normalized_expected_ids,
            expectation_mode=expectation_mode,
        )
    input_hash = _input_records_sha256(materialized)
    if any(not isinstance(record, Bar) for record in materialized):
        return _invalid_result(
            "quality validation requires validated Bar objects",
            policy=policy,
            input_records_sha256=input_hash,
            expected_record_ids=normalized_expected_ids,
            expectation_mode=expectation_mode,
        )

    record_issues: dict[str, set[str]] = {
        record.record_id: set(record.quality_issues) for record in materialized
    }
    existing_suspect = {
        record.record_id
        for record in materialized
        if record.quality_status is QualityStatus.SUSPECT
    }

    ordered = tuple(sorted(materialized, key=lambda item: (*item.identity_key(), item.record_id)))
    out_of_order: tuple[str, ...] = ()
    if tuple((*item.identity_key(), item.record_id) for item in materialized) != tuple(
        (*item.identity_key(), item.record_id) for item in ordered
    ):
        out_of_order = _unique_sorted(item.record_id for item in materialized)
        for record_id in out_of_order:
            record_issues.setdefault(record_id, set()).add(QualityIssue.INVALID.value)

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
        record_issues.setdefault(record.record_id, set()).add(QualityIssue.DUPLICATE.value)
        if canonical_record_bytes(previous) == canonical_record_bytes(record):
            idempotent_replays.add(record.record_id)
        else:
            conflicting_ids.add(record.record_id)
            record_issues.setdefault(record.record_id, set()).add(QualityIssue.INVALID.value)

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
    for record_id in identity_duplicate_ids:
        record_issues.setdefault(record_id, set()).add(QualityIssue.DUPLICATE.value)
        record_issues.setdefault(record_id, set()).add(QualityIssue.INVALID.value)

    expected = set(normalized_expected_ids)
    unexpected = (
        set(by_record_id) - expected
        if expectation_mode == EXPECTATION_MODE_EXPLICIT
        else set()
    )
    for record_id in unexpected:
        record_issues.setdefault(record_id, set()).add(QualityIssue.INVALID.value)
    missing = _unique_sorted(expected - set(by_record_id))
    late = {
        record.record_id
        for record in deduplicated
        if policy.late_after is not None
        and record.ingestion_time - record.availability_time > policy.late_after
    }
    for record_id in late:
        record_issues.setdefault(record_id, set()).add(QualityIssue.LATE.value)
    stale = {
        record.record_id
        for record in deduplicated
        if policy.stale_after is not None
        and record.as_of_time - record.availability_time > policy.stale_after
    }
    for record_id in stale:
        record_issues.setdefault(record_id, set()).add(QualityIssue.STALE.value)
    unexplained_non_valid = {
        record.record_id
        for record in deduplicated
        if record.quality_status is not QualityStatus.VALID and not record.quality_issues
    }
    for record_id in unexplained_non_valid:
        record_issues.setdefault(record_id, set()).add(QualityIssue.INVALID.value)
    existing_invalid = {
        record.record_id
        for record in deduplicated
        if QualityIssue.INVALID.value in record.quality_issues
        or record.quality_status is QualityStatus.REJECTED
        or record.record_id in unexplained_non_valid
    }
    existing_quarantine = {
        record.record_id
        for record in deduplicated
        if record.quality_status is QualityStatus.QUARANTINED
    }
    existing_rejected = {
        record.record_id
        for record in deduplicated
        if record.quality_status is QualityStatus.REJECTED
    }

    fatal = (
        set(out_of_order)
        | conflicting_ids
        | identity_duplicate_ids
        | unexpected
        | existing_invalid
        | existing_rejected
    )
    quarantined = late | stale | existing_quarantine
    rejected = fatal | quarantined
    dataset_issue_set = {
        issue for issues_for_record in record_issues.values() for issue in issues_for_record
    }
    if missing:
        dataset_issue_set.add(QualityIssue.MISSING.value)
    if duplicate_ids or identity_duplicate_ids:
        dataset_issue_set.add(QualityIssue.DUPLICATE.value)
    if fatal & (set(out_of_order) | conflicting_ids | identity_duplicate_ids | existing_invalid):
        dataset_issue_set.add(QualityIssue.INVALID.value)
    issues: list[str] = []
    for issue, present in (
        (QualityIssue.MISSING.value, QualityIssue.MISSING.value in dataset_issue_set),
        (QualityIssue.DUPLICATE.value, QualityIssue.DUPLICATE.value in dataset_issue_set),
        (QualityIssue.LATE.value, QualityIssue.LATE.value in dataset_issue_set),
        (QualityIssue.STALE.value, QualityIssue.STALE.value in dataset_issue_set),
        (QualityIssue.INVALID.value, QualityIssue.INVALID.value in dataset_issue_set),
    ):
        if present:
            issues.append(issue)

    accepted = tuple(record for record in deduplicated if record.record_id not in rejected)
    if fatal:
        status = QualityStatus.REJECTED
    elif quarantined:
        status = QualityStatus.QUARANTINED
    elif issues or existing_suspect:
        status = QualityStatus.SUSPECT
    else:
        status = QualityStatus.VALID
    record_statuses: dict[str, QualityStatus] = {}
    record_reasons: dict[str, str] = {}
    for record_id in sorted(by_record_id):
        if record_id in fatal:
            record_status = QualityStatus.REJECTED
        elif record_id in quarantined:
            record_status = QualityStatus.QUARANTINED
        elif record_id in existing_suspect or record_issues.get(record_id):
            record_status = QualityStatus.SUSPECT
        else:
            record_status = QualityStatus.VALID
        record_statuses[record_id] = record_status
        record_reasons[record_id] = (
            f"status={record_status.value};"
            "issues="
            f"{','.join(_unique_sorted_issues(record_issues.get(record_id, ())) or ('NONE',))}"
        )
    reason_parts = [
        f"policy={policy.policy_id}",
        f"policy_sha256={policy.policy_sha256}",
        f"status={status.value}",
        *(f"{issue.lower()}={issue in issues}" for issue in issues),
    ]
    return QualityResult(
        status=status,
        issues=tuple(issues),
        accepted_records=accepted,
        input_records_sha256=input_hash,
        expected_record_ids=normalized_expected_ids,
        expectation_mode=expectation_mode,
        record_quality_statuses=_freeze_record_statuses(record_statuses),
        missing_record_ids=missing,
        duplicate_record_ids=_unique_sorted(duplicate_ids | identity_duplicate_ids),
        idempotent_replays=_unique_sorted(idempotent_replays),
        out_of_order_record_ids=out_of_order,
        late_record_ids=_unique_sorted(late),
        stale_record_ids=_unique_sorted(stale),
        rejected_record_ids=_unique_sorted(rejected),
        policy_id=policy.policy_id,
        policy_sha256=policy.policy_sha256,
        policy=policy,
        record_quality_issues=_freeze_record_issues(record_issues),
        record_deterministic_reasons=_freeze_record_reasons(record_reasons),
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
        input_records_sha256=result.input_records_sha256,
        expected_record_ids=result.expected_record_ids,
        expectation_mode=result.expectation_mode,
        record_quality_statuses=_freeze_record_statuses(
            {
                **result.record_status_map,
                **{record_id: QualityStatus.REJECTED for record_id in invalid_ids},
            }
        ),
        missing_record_ids=result.missing_record_ids,
        duplicate_record_ids=result.duplicate_record_ids,
        idempotent_replays=result.idempotent_replays,
        out_of_order_record_ids=result.out_of_order_record_ids,
        late_record_ids=result.late_record_ids,
        stale_record_ids=result.stale_record_ids,
        rejected_record_ids=all_rejected,
        policy_id=result.policy_id,
        policy_sha256=result.policy_sha256,
        policy=result.policy,
        record_quality_issues=_freeze_record_issues(
            {
                **result.record_issue_map,
                **{record_id: (QualityIssue.INVALID.value,) for record_id in invalid_ids},
            }
        ),
        record_deterministic_reasons=_freeze_record_reasons(
            {
                **result.record_reason_map,
                **{
                    record_id: "status=REJECTED;issues=INVALID" for record_id in invalid_ids
                },
            }
        ),
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
    if (
        len(data_version) != 64
        or any(character not in "0123456789abcdef" for character in data_version)
    ):
        raise QuarantineWriteError("data_version must be a lowercase SHA-256")
    if not isinstance(quality_status, QualityStatus):
        raise QuarantineWriteError("quality_status must be a QualityStatus")
    if quality_status not in {QualityStatus.QUARANTINED, QualityStatus.REJECTED}:
        raise QuarantineWriteError("quarantine evidence requires QUARANTINED or REJECTED status")
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
    allowed = set(QUALITY_ISSUE_ORDER)
    if not quality_issues:
        raise QuarantineWriteError("quarantine quality issues must not be empty")
    if any(issue not in allowed for issue in quality_issues):
        raise QuarantineWriteError("quarantine quality issue is not a contract reason code")
    evidence = {
        "data_version": data_version,
        "record_id": record_id,
        "quality_status": quality_status.value,
        "quality_issues": list(_unique_sorted_issues(quality_issues)),
        "deterministic_reason": deterministic_reason,
        "source_fixture_sha256": source_fixture_sha256,
        "code_commit": code_commit,
    }
    payload_bytes = (
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    target = root / safe_version / f"{safe_record}.json"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(payload_bytes)
    except FileExistsError:
        try:
            existing_bytes = target.read_bytes()
        except OSError as error:
            raise QuarantineWriteError(
                "quarantine evidence already exists but cannot be read; fail closed"
            ) from error
        if existing_bytes != payload_bytes:
            raise QuarantineWriteError(
                "quarantine evidence conflicts with immutable existing content"
            ) from None
        return target
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
    records: Sequence[Bar],
    policy: QualityPolicy | None = None,
) -> tuple[Path, ...]:
    """Quarantine caller-provided rejected records without fabricating Bars."""

    if not isinstance(result, QualityResult):
        raise QuarantineWriteError("quarantine result must be a QualityResult")
    if result.policy is None:
        raise QuarantineWriteError("quarantine result has no complete quality policy")
    current_policy = result.policy if policy is None else policy
    if not isinstance(current_policy, QualityPolicy):
        raise QuarantineWriteError("quarantine policy must be a complete QualityPolicy")
    if result.expectation_mode not in {
        EXPECTATION_MODE_EXPLICIT,
        EXPECTATION_MODE_OBSERVED_ONLY,
    }:
        raise QuarantineWriteError("quarantine result expectation mode is invalid")
    if result.expectation_mode == EXPECTATION_MODE_OBSERVED_ONLY:
        if result.expected_record_ids:
            raise QuarantineWriteError("observed-only result must not contain expected record IDs")
        stored_expected: Iterable[str] | None = None
    else:
        try:
            mode, stored_ids = _normalize_expected_record_ids(result.expected_record_ids)
        except ValidationError as error:
            raise QuarantineWriteError("quarantine result expected IDs are invalid") from error
        if mode != EXPECTATION_MODE_EXPLICIT or stored_ids != result.expected_record_ids:
            raise QuarantineWriteError("quarantine result expected IDs are not canonical")
        stored_expected = stored_ids

    try:
        rerun = validate_quality(
            records,
            expected_record_ids=stored_expected,
            policy=current_policy,
        )
    except (TypeError, ValidationError) as error:
        raise QuarantineWriteError("current quarantine quality validation failed") from error
    if rerun.to_mapping() != result.to_mapping():
        raise QuarantineWriteError(
            "quarantine result does not match current records, policy, and payloads"
        )

    by_id: dict[str, Bar] = {}
    for record in records:
        if not isinstance(record, Bar):
            raise QuarantineWriteError("quarantine records must be validated Bar objects")
        by_id[record.record_id] = record
    record_issues = rerun.record_issue_map
    record_statuses = rerun.record_status_map
    record_reasons = rerun.record_reason_map
    source_sha = sha256_file(source_fixture)
    missing_ids = set(result.missing_record_ids)
    paths: list[Path] = []
    for record_id in result.rejected_record_ids:
        if record_id in missing_ids:
            continue
        if record_id not in by_id:
            raise QuarantineWriteError(
                f"rejected record {record_id} is not present in quarantine records"
            )
        issues = record_issues.get(record_id, ())
        if not issues:
            raise QuarantineWriteError(
                f"rejected record {record_id} has no record-specific quality issues"
            )
        try:
            quality_status = QualityStatus(record_statuses[record_id])
        except (KeyError, ValueError) as error:
            raise QuarantineWriteError(
                f"rejected record {record_id} has no valid record-specific status"
            ) from error
        if quality_status not in {QualityStatus.QUARANTINED, QualityStatus.REJECTED}:
            raise QuarantineWriteError(
                f"rejected record {record_id} has a non-quarantine record status"
            )
        paths.append(
            quarantine_evidence(
                root,
                data_version=data_version,
                record_id=record_id,
                quality_status=quality_status,
                quality_issues=issues,
                deterministic_reason=record_reasons.get(
                    record_id,
                    f"status={quality_status.value};issues={','.join(issues)}",
                ),
                source_fixture_sha256=source_sha,
                code_commit=code_commit,
            )
        )
    return tuple(paths)
