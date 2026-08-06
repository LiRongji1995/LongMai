"""Contract-boundary tests for the synthetic Bar runtime model."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from quant_system.data import Bar, QualityIssue, QualityStatus, ValidationError, load_bar_json

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "phase2_bar_contract"


def fixture_payload(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def valid_payload() -> dict[str, object]:
    return deepcopy(fixture_payload("valid_regular_est.json")["records"][0])  # type: ignore[index]


def test_valid_est_edt_and_half_day_fixtures_have_explicit_boundaries() -> None:
    est = Bar.from_mapping(valid_payload())
    edt = fixture_payload("valid_regular_edt.json")["records"][0]
    half = fixture_payload("valid_half_day.json")["records"][0]
    edt_bar = Bar.from_mapping(edt)  # type: ignore[arg-type]
    half_bar = Bar.from_mapping(half)  # type: ignore[arg-type]

    assert est.exchange_timezone == "America/New_York"
    assert est.bar_start_time.hour == 14
    assert est.bar_end_time.hour == 21
    assert edt_bar.bar_start_time.hour == 13
    assert edt_bar.bar_end_time.hour == 20
    assert half_bar.bar_end_time.hour == 18
    assert est.event_time == est.bar_end_time
    assert est.as_of_time >= est.ingestion_time > est.availability_time
    assert str(est.open) == "100.00"
    assert est.session.value == "REGULAR"
    assert est.resolution.value == "1d"
    assert est.price_basis.value == "RAW"
    assert est.adjustment_method_version is None


def test_json_boundary_requires_every_field_and_rejects_unknown_fields() -> None:
    payload = valid_payload()
    payload.pop("as_of_time")
    with pytest.raises(ValidationError, match="missing required"):
        Bar.from_mapping(payload)

    payload = valid_payload()
    payload["unexpected"] = "closed-world"
    with pytest.raises(ValidationError, match="unknown fields"):
        Bar.from_mapping(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("session", "AFTER_HOURS", "session"),
        ("resolution", "1h", "resolution"),
        ("price_basis", "ADJUSTED", "price_basis"),
        ("schema_name", "Tick", "schema_name"),
        ("schema_version", "2.0.0", "schema_version"),
        ("exchange_timezone", "Not/AZone", "exchange_timezone"),
        ("instrument_id", "US-EQUITY:NYSE:SYNTH", "instrument_id"),
        ("instrument_id", "SYNTH", "instrument_id"),
    ],
)
def test_enum_timezone_schema_and_opaque_identity_rejection(
    field: str, value: object, message: str
) -> None:
    payload = valid_payload()
    payload[field] = value
    with pytest.raises(ValidationError, match=message):
        Bar.from_mapping(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("bar_start_time", "2024-01-02T21:00:00Z", "bar_start_time"),
        ("event_time", "2024-01-02T20:59:59Z", "event_time"),
        ("ingestion_time", "2024-01-02T20:00:00Z", "ingestion_time"),
        ("as_of_time", "2024-01-02T20:00:01Z", "as_of_time"),
        ("availability_time", "2024-01-02T21:00:03Z", "availability_time"),
        ("bar_end_time", "2024-01-02T21:00:00+00:00", "bar_end_time"),
        ("bar_start_time", "2024-01-02T14:30:00", "bar_start_time"),
    ],
)
def test_time_contract_is_utc_and_ordered(field: str, value: object, message: str) -> None:
    payload = valid_payload()
    payload[field] = value
    with pytest.raises(ValidationError, match=message):
        Bar.from_mapping(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", 100.0),
        ("open", 100),
        ("open", "1e2"),
        ("open", "NaN"),
        ("open", "Infinity"),
        ("open", ""),
        ("open", "-1.00"),
        ("volume", -1),
        ("volume", 1.5),
        ("volume", True),
    ],
)
def test_decimal_and_volume_boundary_rejects_lossy_values(field: str, value: object) -> None:
    payload = valid_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        Bar.from_mapping(payload)


def test_ohlc_quality_and_lineage_invariants_are_fail_closed() -> None:
    payload = valid_payload()
    payload["high"] = "98.00"
    with pytest.raises(ValidationError, match="high"):
        Bar.from_mapping(payload)

    payload = valid_payload()
    payload["adjustment_method_version"] = "adjusted-v1"
    with pytest.raises(ValidationError, match="adjustment"):
        Bar.from_mapping(payload)

    payload = valid_payload()
    payload["data_version"] = "not-a-sha"
    with pytest.raises(ValidationError, match="data_version"):
        Bar.from_mapping(payload)

    payload = valid_payload()
    payload["quality_status"] = QualityStatus.VALID.value
    payload["quality_issues"] = [QualityIssue.DUPLICATE.value]
    with pytest.raises(ValidationError, match="quality issues"):
        Bar.from_mapping(payload)

    payload = valid_payload()
    payload["quality_status"] = QualityStatus.SUSPECT.value
    payload["quality_issues"] = [QualityIssue.INVALID.value]
    with pytest.raises(ValidationError, match="INVALID"):
        Bar.from_mapping(payload)


def test_round_trip_normalizes_times_but_preserves_decimal_semantics() -> None:
    bar = Bar.from_mapping(valid_payload())
    round_trip = Bar.from_mapping(bar.to_mapping())
    assert round_trip == bar
    assert round_trip.canonical_payload()["ingestion_time"] == "2024-01-02T21:00:02.000000Z"
    assert round_trip.canonical_payload()["open"] == "100.00"
    assert "data_version" not in round_trip.canonical_payload()
    assert "data_manifest_sha256" not in round_trip.canonical_payload()


def test_json_non_finite_constant_is_rejected() -> None:
    payload = valid_payload()
    text = json.dumps(payload).replace('"100.00"', "NaN", 1)
    with pytest.raises(ValidationError, match="non-finite"):
        Bar.from_json(text)


def test_fixture_loader_is_strict_about_bar_root() -> None:
    with pytest.raises(ValidationError, match="missing required"):
        load_bar_json(FIXTURE_ROOT / "valid_regular_est.json")
