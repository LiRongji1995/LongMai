from datetime import datetime, timezone
from decimal import Decimal
import json
import time

from tests.support.local_service import ResearchService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.provider_requests import validate_request
from stockfish_core.yaogu.provider_requests_worker import tushare_request
from stockfish_core.yaogu.strategy_experiments import percentile_rank_midpoint, run_pv_observation, run_strategy_experiments
from tests.yaogu.fixtures import make_bundle


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.content = self.text.encode()
        self.url = "https://api.tushare.pro/"
        self.history = []

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP_" + str(self.status_code))


def wait(core, job_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = core.jobs.get(job_id)
        if job["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}:
            return job
        time.sleep(0.005)
    raise AssertionError("job did not finish")


def boosted_bundle():
    bundle, cutoff = make_bundle(8)
    current = bundle.observation_date
    previous = bundle.sessions[-2]
    updates = []
    for bar in bundle.bars:
        if bar.instrument_id == "TEST000" and bar.session == current:
            updates.append(bar.model_copy(update={"amount_cny": Decimal("42000000"), "high": bar.close, "low": bar.reference_prev_close}))
        elif bar.instrument_id == "TEST001" and bar.session == current:
            updates.append(bar.model_copy(update={"amount_cny": Decimal("32000000")}))
        elif bar.instrument_id == "TEST002" and bar.session == previous:
            updates.append(bar.model_copy(update={"comparable_basis": "DIFFERENT"}))
        else:
            updates.append(bar)
    return bundle.model_copy(update={"bars": tuple(updates)}), cutoff


def test_percentile_rank_midpoint_definition():
    assert percentile_rank_midpoint(2, [1, 2, 2, 3]) == 50
    assert percentile_rank_midpoint(1, [1]) == 50


def test_pv_observation_formula_and_missing_are_separate():
    bundle, cutoff = boosted_bundle()
    result = run_pv_observation(bundle, cutoff=cutoff)
    assert result["strategy_id"] == "PV_OBSERVATION_V1"
    assert result["parameter_status"] == "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED"
    assert result["counts"]["filter_passed"] >= 2
    first = result["items"][0]
    assert first["A"] >= 1.2 and first["R"] > 0
    assert "S_PV" in first and first["rank"] == 1
    assert all("TEST002" != row["instrument_id"] for row in result["items"])
    assert any(row["instrument_id"] == "TEST002" for row in result["missing"])


def test_personal_strategy_bundle_keeps_pv_and_active_v2():
    bundle, cutoff = boosted_bundle()
    result = run_strategy_experiments(bundle, cutoff=cutoff)
    assert set(result["results"]) == {"PV_OBSERVATION_V1", "STAGE_AWARE_MOMENTUM_V2"}
    assert result["results"]["PV_OBSERVATION_V1"]["top_ids"]
    assert result["parameter_status"] == "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED"


def test_strategy_experiment_service_uses_registered_market_input(tmp_path, monkeypatch):
    import stockfish_core.yaogu.candidate_service as candidate_module

    bundle, cutoff = boosted_bundle()
    monkeypatch.setattr(candidate_module, "utc_now", lambda: cutoff)
    core = ResearchService(
        root=tmp_path / "state",
        store=SQLiteStore(tmp_path / "r05.sqlite3"),
        allow_test_fixture=True,
        data_dir=tmp_path / "NO_QLIB",
    )
    try:
        input_id = core.candidates.register_input(bundle, activate=False)
        service = core.candidates
        assert {row["strategy_id"] for row in service.strategy_specs()} >= {"PV_OBSERVATION_V1", "DAILY_V1"}
        job = wait(core, service.submit_strategy_experiments(input_id=input_id)["job_id"])
        assert job["state"] == "SUCCEEDED", job
        run_id = job["result"]["run_id"]
        saved = service.strategy_experiment_summary(run_id)
        assert saved["results"]["PV_OBSERVATION_V1"]["top_ids"]
        assert saved["items"]
        detail = service.strategy_experiment_item_detail(run_id, saved["items"][0]["instrument_id"])
        assert "PV_OBSERVATION_V1" in detail["strategy_results"]
        assert "selected_focus" in service.export_strategy_experiment_csv(run_id)
        for submit in (service.submit_strategy_outcomes, service.submit_strategy_tracking):
            outcome_job = wait(core, submit(run_id, horizons=[1,2,3,4,5,10,20])["job_id"])
            assert outcome_job["state"] == "SUCCEEDED", outcome_job
        assert service.strategy_outcomes(run_id)
        assert service.strategy_review(run_id)["market_edge"] == "NOT_ESTABLISHED"
        assert service.latest_strategy_comparison()["comparison_version"] == "PERSONAL_DAILY_COMPARISON_V1"
    finally:
        core.close()


def test_tushare_request_contract_and_auth_required(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("TUSHARE_PRO_TOKEN", raising=False)
    params = validate_request("tushare_daily", {"trade_date": "20260904"})
    assert params["trade_date"] == "2026-09-04"
    result = tushare_request("tushare_daily", params, [], 2)
    assert result["status"] == "FETCH_FAILED"
    assert result["error_code"] == "AUTH_REQUIRED"
    assert result["coverage"]["auth_configured"] is False


def test_tushare_http_pagination_redacts_token(monkeypatch):
    seen = []
    # TEST_FIXTURE only: no ordinary HTTPS REST endpoint is production-verified.
    monkeypatch.setattr("stockfish_core.yaogu.provider_requests_worker.TUSHARE_VERIFIED_REST_ENDPOINT",
                        "https://api.tushare.pro/")

    def fake_post(url, json, timeout, allow_redirects, verify):
        assert url == "https://api.tushare.pro/" and allow_redirects is False and verify is True
        seen.append(json)
        offset = json["params"]["offset"]
        fields = ["ts_code", "trade_date", "close"]
        rows = [["600519.SH", "20260904", 1330.0]] if offset == 0 else []
        return Response({"code": 0, "msg": "", "data": {"fields": fields, "items": rows}})

    monkeypatch.setenv("TUSHARE_TOKEN", "real-looking-test-token")
    monkeypatch.setattr("requests.post", fake_post)
    captured = []
    result = tushare_request("tushare_daily", validate_request("tushare_daily", {"trade_date": "2026-09-04"}), captured, 2)
    assert result["status"] == "COMPLETE"
    assert result["rows"] == [{"ts_code": "600519.SH", "trade_date": "20260904", "close": 1330.0}]
    assert seen[0]["token"] == "real-looking-test-token"
    assert "token" not in captured[0]["parameters"]
    assert result["diagnostics"]["token_redacted"] is True
