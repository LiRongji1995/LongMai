"""R16 independent-review counterexamples using real project imports (TEST_FIXTURE)."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from stockfish_core.yaogu import feature_evidence_contract as contract


def good_fact():
    return {
        "contract_version": contract.CONTRACT_VERSION,
        "schema_revision": "FEATURE_EVIDENCE_SCHEMA_R16_R1",
        "instrument": "600000.SH", "exchange": "SSE", "session": "2026-09-08",
        "eligibility": "ELIGIBLE", "eligibility_reason": None,
        "field_group": "PRICE", "field": "close", "value": 10.0, "value_json": "10.0",
        "status": "KNOWN", "unit": "CNY", "reason": None,
        "provenance": {"status": "KNOWN", "pit_status": "VISIBLE",
                       "source_revision_id": "TEST_FIXTURE_rev_A", "source_refs": ["TEST_FIXTURE"],
                       "visible_at": "2026-09-08T15:00:00+08:00",
                       "decision_cutoff": "2026-09-08T20:30:00+08:00"},
    }


@pytest.mark.parametrize("change", [
    {"unit": "USD"}, {"exchange": "SZSE"}, {"eligibility": "INELIGIBLE"},
    {"eligibility_reason": "TEST_FIXTURE_CHANGED"},
    {"contract_version": "BOGUS"}, {"schema_revision": "BOGUS"},
])
def test_identity_and_unit_mutations_are_rejected(change):
    left = good_fact()
    assert not contract.compare_facts([left], [{**left, **change}])["matched"]


def test_duplicate_key_cannot_overwrite_wrong_row():
    fact = good_fact()
    wrong = {**fact, "value": 999, "value_json": "999"}
    assert not contract.compare_facts([fact], [wrong, fact])["matched"]
    assert not contract.validate_facts([wrong, fact])["valid"]


def test_undeclared_fact_identity_cannot_be_silently_ignored():
    left = {**good_fact(), "unregistered_feature_identity": "TEST_FIXTURE_A"}
    right = {**left, "unregistered_feature_identity": "TEST_FIXTURE_B"}
    assert not contract.validate_facts([left])["valid"]
    assert not contract.compare_facts([left], [right])["matched"]


@pytest.mark.parametrize("value", [None, [], {"status": []}, {"exchange": {}}, {"source_label": {}}])
def test_malformed_fact_shapes_fail_without_exception(value):
    fact = {**good_fact(), **value} if isinstance(value, dict) else value
    assert not contract.compare_facts([fact], [fact])["matched"]


@pytest.mark.parametrize("change", [{"snapshot_id": []}, {"identity_status": []}, {"fact_row_count": True}, {"as_of_session": "20260908"}])
def test_malformed_manifest_self_hash_cannot_authorize_bad_types(change):
    manifest = contract.manifest_from_run({"observation_date": "2026-09-08", "decision_cutoff": "2026-09-08T20:30:00+08:00"}, [good_fact()])
    manifest.update(change)
    manifest["manifest_hash"] = contract.digest({k: v for k, v in manifest.items() if k != "manifest_hash"})
    assert not contract.validate_manifest(manifest)["valid"]
    assert not contract.compare_manifests(manifest, manifest)["matched"]


@pytest.mark.parametrize("script", ["r16_compare_contract_snapshots.py", "r16_validate_contract_schema.py"])
def test_real_cli_rejects_duplicate_manifest_members(tmp_path, script):
    (tmp_path / "feature_evidence_manifest_v1.json").write_text(
        '{"contract_version":"INVALID","contract_version":"FEATURE_EVIDENCE_CONTRACT_V1"}', encoding="utf-8")
    (tmp_path / "feature_evidence_facts_v1.jsonl").write_text(json.dumps(good_fact()) + "\n", encoding="utf-8")
    output = tmp_path / "failure.json"
    command = [sys.executable, str(Path(__file__).resolve().parents[2] / "scripts" / script), "--output-json", str(output)]
    command += (["--left", str(tmp_path), "--right", str(tmp_path)] if "compare" in script else ["--snapshot-dir", str(tmp_path)])
    result = subprocess.run(command, capture_output=True, timeout=30)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert result.returncode == 2
    assert report["status"] == "INVALID_INPUT"
    assert "feature_evidence_manifest_v1.json" in report["error"]
    assert "contract_version" in report["error"]


def test_tolerance_never_skips_provenance():
    left = good_fact()
    right = deepcopy(left)
    right.update(value_json="10")
    right["provenance"]["source_revision_id"] = "TEST_FIXTURE_rev_B"
    assert not contract.compare_facts([left], [right], numeric_tolerances={"close": 1e-12})["matched"]


@pytest.mark.parametrize("change", [
    {"value": 999}, {"value": "NaN", "value_json": '"NaN"'},
    {"value": float("inf"), "value_json": "Infinity"},
    {"value": True, "value_json": "true"},
    {"value": 123, "value_json": "123", "status": "UNKNOWN"},
    {"value": None, "value_json": "null", "status": "KNOWN"},
])
def test_invalid_values_fail_validation_and_comparison(change):
    left = good_fact()
    bad = {**left, **change}
    assert not contract.validate_facts([bad])["valid"]
    assert not contract.compare_facts([left], [bad])["matched"]


def test_unverified_provenance_is_not_upgraded():
    row = contract._field({"instrument_id": "600000.SH", "observation_date": "2026-09-08"},
                          "close", 10.0, group="PRICE", provenance={"status": "UNVERIFIED"})
    assert row["status"] == "UNVERIFIED"


def test_known_requires_visible_provenance():
    fact = good_fact()
    for mutation in ({"status": "UNVERIFIED"}, {"pit_status": "FUTURE"},
                     {"visible_at": "2026-09-09T15:00:00+08:00"}):
        bad = deepcopy(fact)
        bad["provenance"].update(mutation)
        assert not contract.validate_facts([bad])["valid"]


@pytest.mark.parametrize("change", [
    {"source_refs": "TEST_FIXTURE"}, {"source_refs": []}, {"source_refs": [""]},
    {"source_refs": [123]}, {"source_revision_id": ""}, {"source_revision_id": 123},
])
def test_provenance_identity_types_fail_closed(change):
    fact = good_fact()
    fact["provenance"].update(change)
    assert not contract.validate_facts([fact])["valid"]
    assert not contract.compare_facts([fact], [deepcopy(fact)])["matched"]


@pytest.mark.parametrize("field,value", [("reason", {}), ("reason", 123), ("eligibility_reason", 123)])
def test_reason_types_fail_closed(field, value):
    fact = {**good_fact(), field: value}
    assert not contract.validate_facts([fact])["valid"]


@pytest.mark.parametrize("value", ["1_0", "01", "+10", "0x10", "1/2", " 10", "10 "])
def test_numeric_strings_use_json_decimal_syntax(value):
    fact = {**good_fact(), "value": value, "value_json": json.dumps(value)}
    assert not contract.validate_facts([fact])["valid"]


@pytest.mark.parametrize("value", ["10", "10.0", "1e1", "1E+1"])
def test_valid_exact_decimal_strings_remain_supported(value):
    fact = {**good_fact(), "value": value, "value_json": json.dumps(value)}
    assert contract.validate_facts([fact])["valid"]


def test_unknown_event_and_real_ohlc_mapping():
    run = {"observation_date": "2026-09-08", "results": {"STAGE_AWARE_MOMENTUM_V2": {"items": [{
        "instrument_id": "000001.SZ", "observation_date": "2026-09-08",
        "comparable_basis": "TEST_FIXTURE", "daily_path": {
            "open": 11.66, "high": 11.81, "low": 11.65,
            "close_limit_up": "UNKNOWN", "limit_event_quality": "LIMIT_RULE_UNVERIFIED"},
    }]}}}
    facts = {row["field"]: row for row in contract.facts_from_run(run)}
    assert [facts[name]["value"] for name in ("open", "high", "low")] == [11.66, 11.81, 11.65]
    assert facts["close_limit_up"]["status"] == "UNKNOWN"
    assert facts["close_limit_up"]["value"] is None
    assert facts["close_limit_up"]["reason"]


def test_valid_tolerance_zero_and_lossless_jsonl(tmp_path):
    left = good_fact()
    right = {**left, "value_json": "10"}
    assert contract.validate_facts([left])["valid"]
    assert contract.compare_facts([left], [right], numeric_tolerances={"close": 0})["matched"]
    path = tmp_path / "TEST_FIXTURE.jsonl"
    path.write_text(json.dumps(left) + "\n", encoding="utf-8")
    assert contract.read_facts_jsonl(path) == [left]


def test_manifest_does_not_invent_calendar_from_observation_date():
    manifest = contract.manifest_from_run({"observation_date": "2026-09-08"}, [])
    assert manifest["calendar_revision"] is None
    assert manifest["identity_status"]["calendar_revision"] == "UNVERIFIED"


def test_exact_value_cannot_be_hidden_by_tolerance():
    fact = good_fact()
    fact.update(value=2.0, value_json="2.0", field_group="RETURN", field="ret1_pct", unit="percent",
                exact_value={"numerator": "2", "denominator": "1"})
    assert contract.validate_facts([fact])["valid"]
    wrong = {**fact, "exact_value": {"numerator": "200001", "denominator": "100000"}}
    assert contract.validate_facts([wrong])["valid"]  # same rounded display, different exact fact
    assert not contract.compare_facts([fact], [wrong], numeric_tolerances={"ret1_pct": 1})["matched"]
    assert not contract.validate_facts([{**fact, "exact_value": {"numerator": "3", "denominator": "1"}}])["valid"]


def test_comparator_cli_nonzero_on_mutation_and_schema_validation(tmp_path):
    root = Path(__file__).resolve().parents[2]
    fact = good_fact()
    run = {"observation_date": "2026-09-08", "decision_cutoff": "2026-09-08T20:30:00+08:00",
           "calendar_revision": "TEST_FIXTURE_CALENDAR", "source_policy_revision": "TEST_FIXTURE_SOURCE_POLICY"}
    manifest = contract.manifest_from_run(run, [fact])
    for side in ("left", "right"):
        folder = tmp_path / side
        folder.mkdir()
        (folder / "feature_evidence_manifest_v1.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "feature_evidence_facts_v1.jsonl").write_text(json.dumps(fact) + "\n", encoding="utf-8")
    command = [sys.executable, str(root / "scripts/r16_compare_contract_snapshots.py"),
               "--left", str(tmp_path / "left"), "--right", str(tmp_path / "right"),
               "--output-json", str(tmp_path / "comparison.json")]
    assert subprocess.run(command, capture_output=True, timeout=30).returncode == 0
    fact["unit"] = "USD"
    (tmp_path / "right/feature_evidence_facts_v1.jsonl").write_text(json.dumps(fact) + "\n", encoding="utf-8")
    assert subprocess.run(command, capture_output=True, timeout=30).returncode == 2
    schema_command = [sys.executable, str(root / "scripts/r16_validate_contract_schema.py"),
                      "--snapshot-dir", str(tmp_path / "left"), "--output-json", str(tmp_path / "validation.json")]
    result = subprocess.run(schema_command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
