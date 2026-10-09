"""Registered research API/save/export tests in an isolated TEST_FIXTURE DB."""
import csv
from copy import deepcopy
import io
import json
import time

import pytest

from stockfish_core.api import create_app
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu import candidate_service, stage_aware_momentum, strategy_experiments
from scripts import codex_research as entry
from tests.support.short_horizon_strength_fixture import CONTINUATION, PULLBACK, START, WEAK, strength_bundle

ID = "SHORT_HORIZON_STRENGTH_R1"


def wait(core, job_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = core.jobs.get(job_id)
        if job["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}:
            return job
        time.sleep(.005)
    raise AssertionError("TEST_FIXTURE short-horizon job exceeded its test budget")


@pytest.fixture
def research_api(tmp_path, monkeypatch):
    bundle, cutoff = strength_bundle()
    monkeypatch.setattr(candidate_service, "utc_now", lambda: cutoff)
    core = ApplicationService(root=tmp_path / "TEST_FIXTURE-state",
        store=SQLiteStore(tmp_path / "TEST_FIXTURE-short-horizon.sqlite3"),
        allow_test_fixture=True, data_dir=tmp_path / "NO_QLIB_MODEL")
    try:
        input_id = core.candidates.register_input(bundle, activate=False)
        yield core, create_app(core).test_client(), input_id
    finally:
        core.close()


def launch(core, client, input_id):
    response = client.post("/api/strategy-experiments/run", json={"input_id": input_id, "strategy_id": ID})
    assert response.status_code == 202, response.get_data(as_text=True)
    job = wait(core, response.json["job_id"])
    assert job["state"] == "SUCCEEDED", job
    return job["result"]["run_id"]


def test_targeted_registered_run_never_invokes_full_or_v2_and_preserves_active_configuration(research_api, monkeypatch):
    core, client, input_id = research_api
    before_config = core.candidates.current_config().config_hash
    before_activations = core.candidates._all("strategy_config_activations")
    def forbidden(*args, **kwargs):
        raise AssertionError("targeted short-horizon research called another strategy")
    monkeypatch.setattr(strategy_experiments, "run_strategy_experiments", forbidden)
    monkeypatch.setattr(stage_aware_momentum, "run_stage_aware_momentum", forbidden)
    run_id = launch(core, client, input_id)
    saved = core.candidates.strategy_experiment(run_id)
    assert set(saved["results"]) == {ID}
    assert saved["input_id"] == input_id
    assert saved["market_edge"] == "NOT_ESTABLISHED"
    assert saved["parameter_status"] == "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED"
    assert core.candidates.current_config().config_hash == before_config
    assert core.candidates._all("strategy_config_activations") == before_activations
    assert core.candidates._all("stage_aware_prospective_primary_runs") == []
    assert core.candidates._all("stage_aware_prospective_validation_epochs") == []
    assert saved["prospective_validation"]["official_primary_capture"] is False


def test_summary_detail_and_both_exports_derive_one_saved_full_pool(research_api):
    core, client, input_id = research_api
    run_id = launch(core, client, input_id)
    base = "/api/strategy-experiments/" + run_id
    saved = core.candidates.strategy_experiment(run_id)
    full = saved["results"][ID]["items"]
    expected = {row["instrument_id"] for row in full}
    selected = {row["instrument_id"] for row in full if row["research_selected"]}
    assert expected == {START, CONTINUATION, PULLBACK, WEAK}
    assert selected == {START, CONTINUATION, PULLBACK}
    # Existing unselected is the formal FOCUS complement, including research observations.
    for selection, ids in (("all", expected), ("observation", selected), ("focus", set()), ("unselected", expected)):
        suffix = "?strategy_id=" + ID + "&selection=" + selection
        summary = client.get(base + "/summary" + suffix)
        assert summary.status_code == 200, summary.get_data(as_text=True)
        assert summary.json["filtered_total"] == len(ids)
        assert {row["instrument_id"] for row in summary.json["items"]} == ids
        exported = client.get(base + "/export" + suffix + "&format=json")
        assert exported.status_code == 200, exported.get_data(as_text=True)
        document = exported.json
        assert document["run_id"] == run_id
        assert document["export_view"]["source_content_hash"] == saved["content_hash"]
        assert {row["instrument_id"] for row in document["results"][ID]["items"]} == ids
        view = document["results"][ID]
        assert "diagnostics" not in view and "coverage_diagnostics" not in view
        assert view["diagnostics_reference"] == view["coverage_diagnostics_ref"] == "items"
        assert view["diagnostics_count"] == view["coverage_diagnostics_count"] == len(ids)
        assert exported.get_data(as_text=True).count('"exact_features"') == len(ids)
        csv_export = client.get(base + "/export" + suffix + "&format=csv")
        assert csv_export.status_code == 200, csv_export.get_data(as_text=True)
        text = csv_export.get_data(as_text=True).lstrip("\ufeff")
        reader = csv.DictReader(io.StringIO(text))
        columns, csv_rows = set(reader.fieldnames), list(reader)
        assert {"research_selected", "matched_arms", "repair_status"}.issubset(columns)
        assert {row["instrument_id"] for row in csv_rows} == ids
        assert all(row["selected_focus"] == "False" for row in csv_rows)
        assert all(row["run_id"] == run_id and row["strategy_id"] == ID for row in csv_rows)
    detail = client.get(base + "/items/" + PULLBACK)
    assert detail.status_code == 200, detail.get_data(as_text=True)
    # Existing detail API permits multiple saved rows per strategy/security.
    rows = detail.json["strategy_results"][ID]
    assert len(rows) == 1
    row = rows[0]
    assert row == next(row for row in full if row["instrument_id"] == PULLBACK)
    assert row["repair_status"] == "UNCONFIRMED"
    # Read/export routes leave the immutable producer document unchanged.
    assert core.candidates.strategy_experiment(run_id) == saved


def test_specs_register_explicit_research_and_unknown_strategy_is_rejected(research_api):
    core, client, input_id = research_api
    specs = client.get("/api/strategy-experiments/specs")
    assert specs.status_code == 200
    assert ID in {row["strategy_id"] for row in specs.json}
    assert "STAGE_AWARE_MOMENTUM_V2" in {row["strategy_id"] for row in specs.json}
    rejected = client.post("/api/strategy-experiments/run", json={"input_id": input_id, "strategy_id": "UNREGISTERED_TEST_FIXTURE"})
    assert rejected.status_code == 400
    assert core.candidates._all("strategy_experiment_runs") == []


def test_same_bound_input_rule_identity_is_idempotent_without_overwriting_old_documents(research_api):
    core, client, input_id = research_api
    first = launch(core, client, input_id)
    original = core.candidates.strategy_experiment(first)
    second = launch(core, client, input_id)
    assert first == second
    assert core.candidates.strategy_experiment(first) == original
    assert len(core.candidates._all("strategy_experiment_runs")) == 1


def test_unimplemented_research_outcomes_never_create_zero_returns_or_v2_labels(research_api):
    core, client, input_id = research_api
    run_id = launch(core, client, input_id)
    base = "/api/strategy-experiments/" + run_id
    for endpoint in ("outcomes", "tracking", "review"):
        response = client.get(base + "/" + endpoint)
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.json["status"] == "NOT_IMPLEMENTED"
        assert response.json.get("returns") is None
    for endpoint in ("outcomes", "tracking"):
        response = client.post(base + "/" + endpoint, json={})
        assert response.status_code == 503
        assert "SHORT_HORIZON_SETUP_OUTCOME_POLICY_NOT_IMPLEMENTED" in response.get_data(as_text=True)
    assert core.candidates._all("strategy_research_pending") == []
    assert core.candidates._all("stage_aware_prospective_primary_runs") == []
    assert core.candidates.strategy_experiment(run_id)["results"][ID]["counts"]["focus"] == 0


def test_thin_wrapper_validates_actual_saved_exports_and_rejects_fixture_daily_delivery(research_api, tmp_path):
    core, api, input_id = research_api
    run_id = launch(core, api, input_id)
    class LocalClient:
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
    client = LocalClient()
    for selection, count in (("all", 4), ("observation", 3), ("focus", 0), ("unselected", 4)):
        bundle = entry.official_exports(client, run_id, ID, selection)
        assert bundle[0]["export_view"]["filtered_total"] == count
        assert bundle[0]["coverage_type"] == "TEST_FIXTURE"
        output = tmp_path / ("REJECT_TEST_FIXTURE_" + selection)
        with pytest.raises(entry.ResearchError, match="NON_REAL_OR_UNVERIFIABLE_INPUT_NOT_FOR_DAILY_RESEARCH"):
            entry.save_bundle(client, bundle, output, strategy_id=ID)
        assert not output.exists()


def test_thin_wrapper_inflight_reuse_requires_same_registered_input_and_strategy():
    desired = {"input_id": "market_" + "a" * 64, "strategy_id": ID}
    class JobsClient:
        def __init__(self, matching):
            self.calls = []
            self.jobs = [dict(job_id="OTHER_STRATEGY_TEST_FIXTURE", kind="strategy_experiments",
                state="RUNNING", payload={"input_id": desired["input_id"]}),
                dict(job_id="OTHER_INPUT_TEST_FIXTURE", kind="strategy_experiments", state="RUNNING",
                    payload={"input_id": "market_" + "b" * 64, "strategy_id": ID})]
            if matching:
                self.jobs.append(dict(job_id="MATCH_TEST_FIXTURE", kind="strategy_experiments",
                    state="RUNNING", payload=dict(desired)))
        def get(self, path, payload=None):
            self.calls.append((path, payload))
            if path == "/api/jobs?limit=100":
                return self.jobs
            if path == "/api/strategy-experiments/run":
                assert payload == desired
                return {"job_id": "NEW_TEST_FIXTURE"}
            if path.startswith("/api/jobs/"):
                return {"job_id": path.rsplit("/", 1)[-1], "state": "RUNNING"}
            raise AssertionError(path)
    for matching in (True, False):
        client = JobsClient(matching)
        result = entry.submit_or_reuse_job(client, "strategy_experiments", "/api/strategy-experiments/run",
            desired, 0, payload_match=desired)
        assert result["reused_inflight_job"] is matching
        assert result["job_id"] == ("MATCH_TEST_FIXTURE" if matching else "NEW_TEST_FIXTURE")
        assert len([call for call in client.calls if call[1] is not None]) == (0 if matching else 1)


def test_thin_wrapper_parser_requires_explicit_input_for_new_strategy():
    parsed = entry.parser().parse_args(["run", "--strategy", ID, "--input-id", "market_" + "a" * 64])
    assert parsed.strategy == ID and parsed.input_id == "market_" + "a" * 64
    with pytest.raises(SystemExit):
        entry.parser().parse_args(["run", "--strategy", ID])


def test_large_exact_fraction_legacy_view_deduplicates_full_detail_without_mutating_saved_run(research_api):
    core, client, input_id = research_api
    run_id = launch(core, client, input_id)
    legacy = deepcopy(core.candidates.strategy_experiment(run_id))
    legacy["run_id"] = run_id + "_LEGACY_TEST_FIXTURE"
    legacy.pop("content_hash", None)
    result = legacy["results"][ID]
    denominator = 10 ** 1999 + 7
    large_fraction = {"numerator": denominator + 1, "denominator": denominator}
    marker = "TEST_FIXTURE_LARGE_EXACT_FRACTIONS"
    for row in result["items"]:
        # Synthetic exact payload scaling, with all original rule booleans retained.
        row["exact_features"][marker] = []
        while len(json.dumps(row, ensure_ascii=False, indent=2).encode("utf-8")) < 76000:
            row["exact_features"][marker].append(dict(large_fraction))
    result["diagnostics"] = deepcopy(result["items"])
    result.pop("diagnostics_reference", None)
    result.pop("diagnostics_detail_policy", None)
    with core.store.transaction() as connection:
        core.candidates._write_strategy_result(connection, legacy, created_at=legacy["generated_at"])
    before = core.candidates.strategy_experiment(legacy["run_id"])
    base = "/api/strategy-experiments/" + legacy["run_id"]
    all_rows = before["results"][ID]["items"]
    ids_by_selection = {"all": {row["instrument_id"] for row in all_rows},
        "observation": {row["instrument_id"] for row in all_rows if row["research_selected"]},
        "focus": set(), "unselected": {row["instrument_id"] for row in all_rows}}
    for selection, expected_ids in ids_by_selection.items():
        suffix = "?strategy_id=" + ID + "&selection=" + selection
        exported = client.get(base + "/export" + suffix + "&format=json")
        assert exported.status_code == 200
        view = exported.json["results"][ID]
        assert "diagnostics" not in view and "coverage_diagnostics" not in view
        assert view["diagnostics_reference"] == view["coverage_diagnostics_ref"] == "items"
        assert view["diagnostics_count"] == view["coverage_diagnostics_count"] == len(expected_ids)
        assert {row["instrument_id"] for row in view["items"]} == expected_ids
        assert exported.get_data(as_text=True).count('"' + marker + '"') == len(expected_ids)
        assert view["items"] == [row for row in all_rows if row["instrument_id"] in expected_ids]
        summary = client.get(base + "/summary" + suffix)
        assert summary.status_code == 200 and summary.json["filtered_total"] == len(expected_ids)
        assert {row["instrument_id"] for row in summary.json["items"]} == expected_ids
        csv_response = client.get(base + "/export" + suffix + "&format=csv")
        csv_rows = list(csv.DictReader(io.StringIO(csv_response.get_data(as_text=True).lstrip("\ufeff"))))
        assert {row["instrument_id"] for row in csv_rows} == expected_ids
        if selection == "all":
            # The 4-row allocation projects a 3037-row payload near the unchanged CLI budget.
            # Two full copies exceed it; the unique-items response remains below it.
            # Global identities/receipts belong to the one header; do not replicate them per row.
            header_only = deepcopy(exported.json)
            header_only["results"][ID]["items"] = []
            header_bytes = len(json.dumps(header_only, ensure_ascii=False, indent=2).encode("utf-8"))
            four_row_bytes = len(exported.data) - header_bytes
            projected_once = header_bytes + four_row_bytes * 3037 // len(all_rows)
            projected_duplicate = header_bytes + four_row_bytes * 3037 * 2 // len(all_rows)
            assert projected_once < 256 * 1024 * 1024
            assert projected_duplicate > 256 * 1024 * 1024
    detail = client.get(base + "/items/" + PULLBACK)
    assert detail.status_code == 200
    assert detail.json["strategy_results"][ID] == [row for row in all_rows if row["instrument_id"] == PULLBACK]
    assert core.candidates.strategy_experiment(legacy["run_id"]) == before
