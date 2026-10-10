"""R2 single-strategy persistence and delivery on isolated TEST_FIXTURE data.

The price paths come from the independent R1 fixture.  Participation differs
without changing those prices: a prior active day permits a quiet current day,
while activity alone cannot turn the weak price path into a selected setup.
"""
import csv
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import io
import json
from pathlib import Path
import time

import pytest

from scripts import codex_research as entry
from stockfish_core.api import create_app
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu import candidate_service, stage_aware_momentum, strategy_experiments
from stockfish_core.yaogu.contracts import MarketInput, digest
from tests.support.short_horizon_strength_fixture import (
    CONTINUATION, PULLBACK, START, WEAK, payload, strength_bundle,
)

R1 = "SHORT_HORIZON_STRENGTH_R1"
R2 = "SHORT_HORIZON_STRENGTH_R2"
FEATURE = "SHORT_HORIZON_PARTICIPATION_DYNAMIC_LIQUIDITY_V2"
FLOAT_BASIS = "BAOSTOCK_TURN_PERCENT_FLOAT_SHARES"


def participation_fixture(mode="above_threshold"):
    from stockfish_core.yaogu import short_horizon_participation

    threshold = Fraction(short_horizon_participation.CONFIG["participation"]["minimum_turnover_fraction_inclusive"])
    # Fixture role values follow the explicitly frozen profile, rather than
    # forcing a future 5% profile to accept an obsolete 2.5% example.
    assert Fraction(".004") < threshold < Fraction(".5")
    active = threshold if mode == "at_threshold" else threshold + Fraction(".005")
    original, cutoff = strength_bundle()
    value = payload(original)
    recent = value["sessions"][-5:]
    for bar in value["bars"]:
        bar["turnover_basis"] = FLOAT_BASIS
        bar["turnover_fraction"] = .004
        bar["amount_cny"] = Decimal("250000000") if bar["instrument_id"] == START else Decimal("600000000")
        if bar["session"] == recent[-1]:
            bar["amount_cny"] = Decimal("500000000")
        if bar["instrument_id"] == START:
            if bar["session"] == recent[0]:
                bar["turnover_fraction"] = float(active)
            elif bar["session"] == recent[-1]:
                bar["turnover_fraction"] = .003
        elif bar["instrument_id"] == PULLBACK and bar["session"] == recent[1]:
            bar["turnover_fraction"] = float(threshold + Fraction(".015"))
        elif bar["instrument_id"] == WEAK and bar["session"] == recent[-1]:
            bar["turnover_fraction"] = float(active)
    # These are synthetic capture receipts, not copied hashes of real data.
    next(r for r in value["receipts"] if r["receipt_id"] == "TEST_FIXTURE_BARS")["content_hash"] = digest(value["bars"])
    return MarketInput.model_validate(value), cutoff


def wait_for_job(core, job_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = core.jobs.get(job_id)
        if job["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}:
            return job
        time.sleep(.005)
    raise AssertionError("TEST_FIXTURE R2 job exceeded its bounded test wait")


@pytest.fixture
def r2_api(tmp_path, monkeypatch, request):
    bundle, cutoff = participation_fixture(getattr(request, "param", "above_threshold"))
    monkeypatch.setattr(candidate_service, "utc_now", lambda: cutoff)
    core = ApplicationService(
        root=tmp_path / "TEST_FIXTURE-R2-state",
        store=SQLiteStore(tmp_path / "TEST_FIXTURE-R2.sqlite3"),
        allow_test_fixture=True,
        data_dir=tmp_path / "NO_QLIB_MODEL",
    )
    try:
        input_id = core.candidates.register_input(bundle, activate=False)
        yield core, create_app(core).test_client(), input_id, bundle, cutoff
    finally:
        core.close()


def launch(core, client, input_id, strategy=R2):
    response = client.post("/api/strategy-experiments/run", json={
        "input_id": input_id, "strategy_id": strategy,
    })
    assert response.status_code == 202, response.get_data(as_text=True)
    job = wait_for_job(core, response.json["job_id"])
    assert job["state"] == "SUCCEEDED", job
    return job["result"]["run_id"]


def indexed(saved, strategy=R2):
    return {row["instrument_id"]: row for row in saved["results"][strategy]["items"]}


def test_explicit_r2_job_is_single_strategy_and_keeps_prior_r1_and_active_v2(r2_api, monkeypatch):
    core, client, input_id, bundle, cutoff = r2_api
    old_id = launch(core, client, input_id, R1)
    old = deepcopy(core.candidates.strategy_experiment(old_id))
    config = core.candidates.current_config().config_hash
    activations = deepcopy(core.candidates._all("strategy_config_activations"))
    r1_config = Path(candidate_service.__file__).resolve().parents[2] / "config" / "short_horizon_strength_r1.yaml"
    config_bytes = r1_config.read_bytes()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("explicit R2 invoked the full suite or active V2")

    monkeypatch.setattr(strategy_experiments, "run_strategy_experiments", forbidden)
    monkeypatch.setattr(stage_aware_momentum, "run_stage_aware_momentum", forbidden)
    run_id = launch(core, client, input_id)
    saved = core.candidates.strategy_experiment(run_id)
    assert set(saved["results"]) == {R2}
    assert saved["strategy_id"] == R2
    assert saved["execution_profile"] == "SHORT_HORIZON_RESEARCH_SINGLE"
    assert saved["input_id"] == input_id
    assert saved["observation_date"] == str(bundle.observation_date)
    assert saved["decision_cutoff"] == cutoff.isoformat()
    assert saved["feature_version"] == FEATURE
    assert saved["market_edge"] == "NOT_ESTABLISHED"
    assert saved["parameter_status"] == "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED"
    assert saved["prospective_validation"]["official_primary_capture"] is False
    assert run_id != old_id
    assert core.candidates.strategy_experiment(old_id) == old
    assert core.candidates.current_config().config_hash == config
    assert core.candidates._all("strategy_config_activations") == activations
    assert r1_config.read_bytes() == config_bytes
    assert core.candidates._all("stage_aware_prospective_primary_runs") == []
    assert core.candidates._all("stage_aware_prospective_validation_epochs") == []
    old_detail = client.get("/api/strategy-experiments/" + old_id + "/items/" + CONTINUATION)
    assert old_detail.status_code == 200
    assert old_detail.json["strategy_results"][R1] == [indexed(old, R1)[CONTINUATION]]
    assert launch(core, client, input_id) == run_id
    assert core.candidates.strategy_experiment(run_id) == saved
    assert len(core.candidates._all("strategy_experiment_runs")) == 2


@pytest.mark.parametrize("r2_api", ["at_threshold", "above_threshold"], indirect=True)
def test_saved_r2_retains_base_setup_and_prior_activity_allows_current_contraction(r2_api):
    from stockfish_core.yaogu import short_horizon_participation

    core, client, input_id, bundle, _ = r2_api
    run_id = launch(core, client, input_id)
    saved = core.candidates.strategy_experiment(run_id)
    rows = indexed(saved)
    assert set(rows) == {START, CONTINUATION, PULLBACK, WEAK}
    assert {iid for iid, row in rows.items() if row["research_selected"]} == {START, PULLBACK}
    assert rows[START]["turnover_fraction"] == .003
    assert rows[START]["participation_status"] == "PASS"
    assert rows[START]["liquidity_status"] == "UNKNOWN"
    assert rows[START]["style_status"] == "PASS"
    assert rows[CONTINUATION]["participation_status"] == "FAIL"
    assert rows[CONTINUATION]["liquidity_status"] == "UNKNOWN"
    assert rows[CONTINUATION]["style_status"] == "FAIL"
    assert rows[CONTINUATION]["r1_research_selected"] is True
    assert rows[CONTINUATION]["matched_arms"] == ["STRONG_CONTINUATION"]
    assert rows[CONTINUATION]["decision_status"] == "REJECTED"
    assert rows[PULLBACK]["participation_status"] == "PASS"
    assert rows[PULLBACK]["liquidity_status"] == "UNKNOWN"
    assert rows[PULLBACK]["repair_status"] == "UNCONFIRMED"
    assert rows[WEAK]["participation_status"] == "PASS"
    assert rows[WEAK]["style_status"] == "PASS"
    assert rows[WEAK]["r1_research_selected"] is False
    assert rows[WEAK]["decision_status"] == "REJECTED"
    assert all(row["formal_focus"] is False for row in rows.values())
    for row in rows.values():
        evidence = row["participation"]
        threshold = Fraction(short_horizon_participation.CONFIG["participation"]["minimum_turnover_fraction_inclusive"])
        assert evidence["threshold"] == {"numerator": threshold.numerator, "denominator": threshold.denominator}
        assert evidence["window_sessions"] == [str(day) for day in bundle.sessions[-5:]]
        assert len(evidence["observations"]) == 5
        assert row["conditions"]["recent_participation"] == evidence
        assert row["conditions"]["current_liquidity"] == row["liquidity"]
        # This fixture has no authenticated whole-market RAW reader. A legal
        # symbol amount cannot manufacture the five-day market denominator.
        assert row["liquidity"]["reference_cny"] is None
        assert row["liquidity"]["ratio_to_reference"] is None
        assert row["liquidity"]["soft_score_exact"] is None
        assert row["liquidity"]["selection_gate"] is False
        assert row["liquidity_selection_gate"] is False
        assert "recent_participation" in row["exact_features"]
        assert row["feature_version"] == FEATURE
    assert saved["results"][R2]["counts"]["eligible"] == 4
    assert saved["results"][R2]["counts"]["focus"] == 0
    assert saved["results"][R2]["market_turnover_context"]["status"] == "UNKNOWN"


def test_summary_detail_json_and_csv_keep_one_saved_run_and_participation_evidence(r2_api):
    core, client, input_id, _, _ = r2_api
    run_id = launch(core, client, input_id)
    saved = deepcopy(core.candidates.strategy_experiment(run_id))
    rows = indexed(saved)
    selections = {"all": set(rows), "observation": {START, PULLBACK},
        "focus": set(), "unselected": set(rows)}
    base = "/api/strategy-experiments/" + run_id
    for selection, ids in selections.items():
        query = "?strategy_id=" + R2 + "&selection=" + selection
        summary = client.get(base + "/summary" + query)
        assert summary.status_code == 200, summary.get_data(as_text=True)
        assert summary.json["run_id"] == run_id
        assert summary.json["filtered_total"] == len(ids)
        assert {row["instrument_id"] for row in summary.json["items"]} == ids
        for row in summary.json["items"]:
            assert row["participation_status"] == rows[row["instrument_id"]]["participation_status"]
            assert row["liquidity_status"] == rows[row["instrument_id"]]["liquidity_status"]
            assert row["style_status"] == rows[row["instrument_id"]]["style_status"]
        response = client.get(base + "/export" + query + "&format=json")
        assert response.status_code == 200, response.get_data(as_text=True)
        document = response.json
        assert document["run_id"] == run_id
        assert document["input_id"] == input_id
        assert document["decision_cutoff"] == saved["decision_cutoff"]
        assert document["export_view"]["source_content_hash"] == saved["content_hash"]
        actual = document["results"][R2]["items"]
        assert actual == [row for row in saved["results"][R2]["items"] if row["instrument_id"] in ids]
        assert all(row["participation"] == rows[row["instrument_id"]]["participation"] for row in actual)
        csv_response = client.get(base + "/export" + query + "&format=csv")
        assert csv_response.status_code == 200, csv_response.get_data(as_text=True)
        reader = csv.DictReader(io.StringIO(csv_response.get_data(as_text=True).lstrip("\ufeff")))
        assert {"participation_status", "participation_peak_turnover_fraction", "liquidity_status", "style_status", "feature_version",
                "dynamic_reference_cny", "amount_to_dynamic_reference", "dynamic_liquidity_soft_score",
                "liquidity_selection_gate"}.issubset(reader.fieldnames)
        csv_rows = list(reader)
        assert {row["instrument_id"] for row in csv_rows} == ids
        for row in csv_rows:
            original = rows[row["instrument_id"]]
            assert row["run_id"] == run_id and row["strategy_id"] == R2
            assert row["participation_status"] == original["participation_status"]
            assert row["liquidity_status"] == original["liquidity_status"]
            assert row["style_status"] == original["style_status"]
            assert row["liquidity_selection_gate"] == "False"
            assert original["liquidity_selection_gate"] is False
            for column, field in (("dynamic_reference_cny", "reference_cny"),
                                  ("amount_to_dynamic_reference", "ratio_to_reference"),
                                  ("dynamic_liquidity_soft_score", "soft_score_exact")):
                source = original["liquidity"][field]
                exported = json.loads(row[column])
                assert exported == source
                if source is None:
                    assert exported is None
                else:
                    assert Fraction(exported["numerator"], exported["denominator"]) == Fraction(source["numerator"], source["denominator"])
            maximum = original["participation"]["maximum_verified_turnover_fraction"]
            assert row["participation_peak_turnover_fraction"]
            assert Fraction(row["participation_peak_turnover_fraction"]) == Fraction(maximum["numerator"], maximum["denominator"])
            assert row["feature_version"] == FEATURE
            assert row["selected_focus"] == "False"
    for iid, expected in rows.items():
        response = client.get(base + "/items/" + iid)
        assert response.status_code == 200
        assert response.json["strategy_results"][R2] == [expected]
    assert core.candidates.strategy_experiment(run_id) == saved


@pytest.mark.parametrize("gap", ["missing_value", "unrecognized_basis"])
def test_missing_participation_remains_unknown_in_saved_detail_and_export(r2_api, gap):
    core, client, _, bundle, _ = r2_api
    value = payload(bundle)
    for row in value["bars"]:
        if row["instrument_id"] == CONTINUATION and row["session"] in bundle.sessions[-5:]:
            if gap == "missing_value":
                row["turnover_fraction"] = None
                row["turnover_basis"] = None
            else:
                row["turnover_basis"] = "TEST_FIXTURE_UNKNOWN_SHARE_DENOMINATOR"
    next(r for r in value["receipts"] if r["receipt_id"] == "TEST_FIXTURE_BARS")["content_hash"] = digest(value["bars"])
    input_id = core.candidates.register_input(MarketInput.model_validate(value), activate=False)
    run_id = launch(core, client, input_id)
    row = indexed(core.candidates.strategy_experiment(run_id))[CONTINUATION]
    assert row["r1_research_selected"] is True
    assert row["participation_status"] == "UNKNOWN"
    assert row["liquidity_status"] == "UNKNOWN"
    assert row["style_status"] == "UNKNOWN"
    assert row["decision_status"] == "UNKNOWN"
    assert row["research_selected"] is False
    assert all(observation["turnover_fraction"] is None for observation in row["participation"]["observations"])
    detail = client.get("/api/strategy-experiments/" + run_id + "/items/" + CONTINUATION)
    assert detail.status_code == 200
    assert detail.json["strategy_results"][R2] == [row]
    response = client.get("/api/strategy-experiments/" + run_id + "/export?strategy_id=" + R2 + "&selection=all&format=json")
    assert response.status_code == 200
    delivered = next(x for x in response.json["results"][R2]["items"] if x["instrument_id"] == CONTINUATION)
    assert delivered["participation"] == row["participation"]


@pytest.mark.parametrize("amount,base_selected", [
    (Decimal("500000000"), True),
    (Decimal("499999999.99"), True),
    (None, False),
])
def test_current_amount_is_soft_context_and_preserves_r1_missing_fact_boundaries(r2_api, amount, base_selected):
    core, client, _, bundle, _ = r2_api
    value = payload(bundle)
    current = next(row for row in value["bars"] if row["instrument_id"] == START
                   and row["session"] == bundle.observation_date)
    current["amount_cny"] = amount
    next(r for r in value["receipts"] if r["receipt_id"] == "TEST_FIXTURE_BARS")["content_hash"] = digest(value["bars"])
    input_id = core.candidates.register_input(MarketInput.model_validate(value), activate=False)
    run_id = launch(core, client, input_id)
    row = indexed(core.candidates.strategy_experiment(run_id))[START]
    assert row["participation_status"] == "PASS"
    assert row["liquidity_status"] == "UNKNOWN"
    assert row["style_status"] == "PASS"
    assert row["r1_research_selected"] is base_selected
    assert row["research_selected"] is base_selected
    assert row["liquidity"]["selection_gate"] is False
    assert row["liquidity"]["reference_cny"] is None
    if amount is None:
        assert row["liquidity"]["value"] is None
        assert row["decision_status"] == "UNKNOWN"
    else:
        exact = Fraction(amount)
        assert row["liquidity"]["value"] == {"numerator": exact.numerator, "denominator": exact.denominator}
        assert row["r1_research_selected"] is True
    response = client.get("/api/strategy-experiments/" + run_id + "/export?strategy_id=" + R2 + "&selection=all&format=json")
    assert response.status_code == 200
    delivered = next(x for x in response.json["results"][R2]["items"] if x["instrument_id"] == START)
    assert delivered["liquidity"] == row["liquidity"]


def test_current_amount_parent_seen_after_cutoff_is_unknown_with_verified_prior_participation(r2_api):
    from datetime import timedelta

    core, client, _, bundle, cutoff = r2_api
    value = payload(bundle)
    late = cutoff + timedelta(microseconds=1)
    current = next(row for row in value["bars"] if row["instrument_id"] == START
                   and row["session"] == bundle.observation_date)
    for field in ("first_seen_at", "source_published_at", "ingested_at"):
        current[field] = late
    current["source_refs"] = ["TEST_FIXTURE_R2_LATE_CURRENT"]
    old_parent = next(r for r in value["receipts"] if r["receipt_id"] == "TEST_FIXTURE_BARS")
    prior_capture_rows = [row for row in value["bars"] if row is not current]
    old_parent["content_hash"] = digest(prior_capture_rows)
    old_parent["row_count"] = len(prior_capture_rows)
    late_parent = {**old_parent, "receipt_id": "TEST_FIXTURE_R2_LATE_CURRENT",
        "instrument_ids": [START], "business_sessions": [bundle.observation_date],
        "content_hash": digest([current]), "row_count": 1,
        "event_time": current["event_time"], "source_published_at": late,
        "first_seen_at": late, "ingested_at": late}
    value["receipts"].append(late_parent)
    input_id = core.candidates.register_input(MarketInput.model_validate(value), activate=False)
    run_id = launch(core, client, input_id)
    row = indexed(core.candidates.strategy_experiment(run_id))[START]
    assert row["participation_status"] == "PASS"
    assert row["liquidity_status"] == "UNKNOWN"
    assert row["style_status"] == "PASS"
    assert row["liquidity"]["value"] is None
    assert row["research_selected"] is False
    detail = client.get("/api/strategy-experiments/" + run_id + "/items/" + START)
    assert detail.status_code == 200
    assert detail.json["strategy_results"][R2] == [row]
    response = client.get("/api/strategy-experiments/" + run_id + "/export?strategy_id=" + R2 + "&selection=all&format=json")
    assert response.status_code == 200
    delivered = next(x for x in response.json["results"][R2]["items"] if x["instrument_id"] == START)
    assert delivered["liquidity"] == row["liquidity"]


def test_activity_received_after_cutoff_cannot_become_saved_participation_authority(r2_api):
    from datetime import timedelta

    core, client, _, bundle, cutoff = r2_api
    value = payload(bundle)
    late_session = bundle.sessions[-5]
    late = cutoff + timedelta(microseconds=1)
    for row in value["bars"]:
        if row["instrument_id"] == START and row["session"] == late_session:
            for field in ("first_seen_at", "source_published_at", "ingested_at"):
                row[field] = late
    next(r for r in value["receipts"] if r["receipt_id"] == "TEST_FIXTURE_BARS")["content_hash"] = digest(value["bars"])
    input_id = core.candidates.register_input(MarketInput.model_validate(value), activate=False)
    run_id = launch(core, client, input_id)
    saved = core.candidates.strategy_experiment(run_id)
    row = indexed(saved)[START]
    assert saved["input_id"] == input_id
    assert saved["decision_cutoff"] == cutoff.isoformat()
    assert row["participation_status"] == "UNKNOWN"
    assert row["research_selected"] is False
    observation = next(x for x in row["participation"]["observations"] if x["session"] == str(late_session))
    assert observation["status"] == "UNKNOWN"
    assert observation["turnover_fraction"] is None
    assert observation["reason_codes"]
    detail = client.get("/api/strategy-experiments/" + run_id + "/items/" + START)
    assert detail.status_code == 200
    assert detail.json["strategy_results"][R2] == [row]


def test_registry_and_cli_select_explicit_version_without_default_active_switch(r2_api, monkeypatch):
    core, client, input_id, _, _ = r2_api
    specs = client.get("/api/strategy-experiments/specs")
    assert specs.status_code == 200
    ids = {row["strategy_id"] for row in specs.json}
    assert {R1, R2, "STAGE_AWARE_MOMENTUM_V2"}.issubset(ids)
    for strategy in (R1, R2):
        parsed = entry.parser().parse_args(["run", "--strategy", strategy, "--input-id", input_id])
        assert parsed.strategy == strategy and parsed.input_id == input_id
        for command in ("export", "report"):
            parsed = entry.parser().parse_args([command, "TEST_FIXTURE_SAVED_RUN", "--profile", strategy])
            assert parsed.profile == strategy
        parsed = entry.parser().parse_args(["select", "--profile", strategy])
        assert parsed.profile == strategy
        with pytest.raises(SystemExit):
            entry.parser().parse_args(["run", "--strategy", strategy])
    default = entry.parser().parse_args(["export", "TEST_FIXTURE_SAVED_RUN"])
    assert default.profile == entry.PV
    monkeypatch.setattr(entry, "calendar_context", lambda *_args: {"role": "TEST_FIXTURE"})

    class NoRequest:
        def get(self, *_args, **_kwargs):
            raise AssertionError("mismatched explicit profiles reached an API")

    mismatched = entry.parser().parse_args(["run", "--strategy", R2, "--profile", R1, "--input-id", input_id])
    with pytest.raises(entry.ResearchError, match="PROFILE_AND_EXPLICIT_STRATEGY_MISMATCH"):
        entry.execute(mismatched, NoRequest())
    rejection = client.post("/api/strategy-experiments/run", json={
        "input_id": input_id, "strategy_id": "UNREGISTERED_TEST_FIXTURE_R2",
    })
    assert rejection.status_code == 400
    assert core.candidates._all("strategy_experiment_runs") == []


def test_cli_checks_native_exports_but_cannot_publish_fixture_as_daily_research(r2_api, tmp_path):
    core, api, input_id, _, _ = r2_api
    run_id = launch(core, api, input_id)

    class LocalSavedClient:
        base_url = "TEST_FIXTURE_LOCAL_FLASK_CLIENT"

        def raw(self, path, payload=None):
            assert payload is None
            response = api.get(path)
            assert response.status_code == 200, response.get_data(as_text=True)
            return response.data

        def get(self, path, payload=None):
            assert payload is None
            response = api.get(path)
            assert response.status_code == 200, response.get_data(as_text=True)
            return response.json

    client = LocalSavedClient()
    for selection, expected in (("all", 4), ("observation", 2), ("focus", 0), ("unselected", 4)):
        exports = entry.official_exports(client, run_id, R2, selection)
        assert exports[0]["export_view"]["filtered_total"] == expected
        assert exports[0]["coverage_type"] == "TEST_FIXTURE"
        output = tmp_path / ("DO_NOT_PUBLISH_TEST_FIXTURE_R2_" + selection)
        with pytest.raises(entry.ResearchError, match="NON_REAL_OR_UNVERIFIABLE_INPUT_NOT_FOR_DAILY_RESEARCH"):
            entry.save_bundle(client, exports, output, strategy_id=R2)
        assert not output.exists()
    assert core.candidates.strategy_experiment(run_id)["decision_cutoff"] == r2_api[-1].isoformat()
