from datetime import datetime, timezone
import json
import subprocess
import time

from stockfish_core.api import create_app
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.candidate_service import CandidateRequest
from stockfish_core.yaogu.data_pipeline import MainboardPipeline
from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
from stockfish_core.yaogu.provider_requests import execute_request
from stockfish_core.yaogu.request_queue import RequestBudget
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.provider_requests_worker import eastmoney_daily_limits
from stockfish_core.yaogu.rules import certify_rule_derived_limits, derive_verified_limits
from tests.r03_pipeline.test_data_dag import fixtures, normalizer, source
from tests.yaogu.fixtures import make_bundle


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.content = self.text.encode()

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
    raise AssertionError("candidate job did not finish")


def test_eastmoney_direct_limit_batch_keeps_successful_rows(monkeypatch):
    epoch = datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc).timestamp()

    def fake_get(url, params, timeout):
        code = params["secid"].split(".")[1]
        if code == "600519":
            return Response({"rc": 0, "data": {"f57": code, "f43": 1330.0, "f51": 1463.0, "f52": 1197.0, "f86": epoch}})
        return Response({"rc": 0, "data": {"f57": "OTHER", "f43": 1.0, "f51": 2.0, "f52": 0.5, "f86": epoch}})

    monkeypatch.setattr("requests.get", fake_get)
    captured = []
    result = eastmoney_daily_limits({"date": "2026-09-04", "codes": ["600519.SH", "000001.SZ"]}, captured, 10)
    assert result["status"] == "PARTIAL"
    assert result["rows"][0]["instrument_id"] == "600519.SH"
    assert captured[0]["parameters"]["secid"] == "1.600519"
    assert result["rows"][0]["limit_up"] == 1463.0
    assert len(result["coverage"]["security_statuses"]) == 2
    assert len(captured) == 2


def test_parent_timeout_retains_worker_partial_rows(tmp_path, monkeypatch):
    def fake_run(args, input, **kwargs):
        request = json.loads(input)
        partial = {
            "status": "PARTIAL",
            "rows": [{"code": "sh.600519", "instrument_id": "600519.SH", "date": "2026-09-04"}],
            "pagination_complete": False,
            "http_responses": [],
            "coverage": {"security_statuses": [{"code": "sh.600519", "status": "COMPLETE"}]},
            "gaps": [],
            "field_version": "TEST_PARTIAL",
            "first_seen_at": datetime(2026, 9, 4, 7, tzinfo=timezone.utc).isoformat(),
        }
        Path(request["partial_path"]).write_text(json.dumps(partial), encoding="utf-8")
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    from pathlib import Path
    monkeypatch.setattr("subprocess.run", fake_run)
    result = execute_request("daily_limits_eastmoney", {"date": "2026-09-04", "codes": ["600519.SH"]}, tmp_path, timeout=1)
    assert result["status"] == "PARTIAL"
    assert result["error_code"] == "REQUEST_TIMEOUT_AFTER_PARTIAL"
    assert result["rows"][0]["instrument_id"] == "600519.SH"


def test_eastmoney_limit_uses_shanghai_business_date_not_utc_date(monkeypatch):
    epoch = datetime(2026, 9, 4, 16, 30, tzinfo=timezone.utc).timestamp()

    def fake_get(url, params, timeout):
        code = params["secid"].split(".")[1]
        return Response({"rc": 0, "data": {"f57": code, "f43": 10.0, "f51": 11.0, "f52": 9.0, "f86": epoch}})

    monkeypatch.setattr("requests.get", fake_get)
    result = eastmoney_daily_limits({"date": "2026-09-04", "codes": ["600519.SH"]}, [], 10)
    assert result["status"] == "FETCH_FAILED"
    assert result["gaps"][0]["error_code"] == "EASTMONEY_LIMIT_DATE_MISMATCH"


def test_rule_derived_limit_requires_auditable_normal_proof_and_matches_direct_sample():
    proof = {
        "proof_status": "NORMAL_VERIFIED",
        "source_receipt_ids": ["receipt-normal-state"],
        "source_fact_ids": ["security-state-600519-2026-09-04"],
        "official_source_hash": "a" * 64,
        "reference_price_semantics": "RAW_PREVIOUS_CLOSE",
        "reference_price_receipt": "receipt-raw-preclose",
        "validation_suite_id": "TEST_FIXTURE_R04_RULE_BRANCH_VALIDATION",
        "sample_evidence_refs": ["direct-eastmoney-sample"],
        "definition_version": "TEST_FIXTURE_NORMAL_PROOF_V1",
        "branch_cross_validated": True,
    }
    certified = certify_rule_derived_limits(
        "10.02",
        exchange="SSE",
        board="MAIN",
        session="2026-09-04",
        st=False,
        normal_or_exempt_proof=proof,
        source_sample={"limit_up": "11.02", "limit_down": "9.02"},
    )
    derived = derive_verified_limits(
        "10.02",
        exchange="SSE",
        board="MAIN",
        session="2026-09-04",
        st=False,
        normal_or_exempt_proof=certified,
        source_sample={"limit_up": "11.02", "limit_down": "9.02"},
    )
    assert derived["status"] == "RULE_DERIVED_VERIFIED"
    assert (derived["limit_up"], derived["limit_down"]) == ("11.02", "9.02")
    assert derive_verified_limits(
        "10.02",
        exchange="SSE",
        board="MAIN",
        session="2026-09-04",
        st=False,
        normal_or_exempt_proof=proof,
    )["reason"] == "LOCAL_RULE_CERTIFICATION_REQUIRED"
    assert derive_verified_limits(
        "10.02",
        exchange="SSE",
        board="MAIN",
        session="2026-09-04",
        st=False,
        normal_or_exempt_proof={**proof, "reference_price_semantics": "HFQ_CLOSE"},
    )["reason"] == "REFERENCE_PRICE_SEMANTICS_UNSUPPORTED"
    assert derive_verified_limits(
        "10.02",
        exchange="SSE",
        board="MAIN",
        session="2026-09-04",
        st=False,
        normal_or_exempt_proof={},
    )["status"] == "UNKNOWN"


def provider_result(endpoint, rows, stamp, *, parameters=None, provider="test", coverage=None):
    receipt_id = endpoint + "-receipt"
    return {
        "endpoint": endpoint,
        "provider": provider,
        "parameters": parameters or {},
        "status": "COMPLETE",
        "pagination_complete": True,
        "field_version": "TEST_FIXTURE_R04",
        "request_receipt_id": receipt_id,
        "receipts": [{
            "receipt_id": receipt_id,
            "content_hash": digest({"endpoint": endpoint, "rows": rows}),
            "first_seen_at": stamp.isoformat(),
            "ingested_at": stamp.isoformat(),
            "source_url": "https://example.org/TEST_FIXTURE",
        }],
        "rows": rows,
        "coverage": coverage or {},
    }


def test_normalizer_consumes_direct_published_limits_into_daily_bar():
    stamp = datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc)
    day = "2026-09-04"
    results = [
        provider_result("mainboard_sse", [{"证券代码": "600519", "证券简称": "贵州茅台", "上市日期": "2001-08-27"}], stamp),
        provider_result("master_szse", [], stamp),
        provider_result("market_daily", [{
            "date": day,
            "code": "sh.600519",
            "open": "1320.00",
            "high": "1335.00",
            "low": "1318.00",
            "close": "1330.00",
            "preclose": "1326.00",
            "volume": "100000",
            "amount": "133000000",
            "adjustflag": "3",
            "turn": "0.01",
            "tradestatus": "1",
            "isST": "0",
        }], stamp, parameters={"date": day}, provider="baostock"),
        provider_result("daily_limits_eastmoney", [{
            "code": "sh.600519",
            "instrument_id": "600519.SH",
            "date": day,
            "close": 1330.0,
            "limit_up": 1458.6,
            "limit_down": 1193.4,
            "source_timestamp": stamp.isoformat(),
            "source_business_date": day,
            "field_mapping": "EASTMONEY_STOCK_GET_F51_F52_R04_V1",
        }], stamp, parameters={"date": day, "codes": ["sh.600519"]}, provider="eastmoney",
            coverage={"returned_instrument_ids": ["600519.SH"], "business_sessions": [day]}),
    ]
    bundle, report = normalize_capture_with_report(results, day, stamp, [day])
    bar = next(b for b in bundle.bars if b.instrument_id == "600519.SH")
    assert bar.limit_verified is True
    assert bar.rule_id == "DIRECT_PUBLISHED_EASTMONEY_F51_F52_R04"
    assert str(bar.limit_up) == "1458.6"
    assert report["fields_retained"]["direct_published_limit_rows"] == 1
    assert report["limit_cross_validation"][0]["verification"] == "DIRECT_PUBLISHED"


def test_normalizer_rejects_direct_limit_with_wrong_shanghai_date():
    stamp = datetime(2026, 9, 4, 20, 30, tzinfo=timezone.utc)
    day = "2026-09-04"
    results = [
        provider_result("mainboard_sse", [{"证券代码": "600519", "证券简称": "贵州茅台", "上市日期": "2001-08-27"}], stamp),
        provider_result("master_szse", [], stamp),
        provider_result("market_daily", [{
            "date": day, "code": "sh.600519", "open": "10", "high": "11", "low": "9",
            "close": "10", "preclose": "10", "volume": "100000", "amount": "1000000",
            "adjustflag": "3", "turn": "0.01", "tradestatus": "1", "isST": "0",
        }], stamp, parameters={"date": day}, provider="baostock"),
        provider_result("daily_limits_eastmoney", [{
            "code": "sh.600519", "instrument_id": "600519.SH", "date": day,
            "close": 10.0, "limit_up": 11.0, "limit_down": 9.0,
            "source_timestamp": stamp.isoformat(), "source_business_date": "2026-09-05",
            "field_mapping": "EASTMONEY_STOCK_GET_F51_F52_R04_V1",
        }], stamp, parameters={"date": day, "codes": ["sh.600519"]}, provider="eastmoney",
            coverage={"returned_instrument_ids": ["600519.SH"], "business_sessions": [day]}),
    ]
    bundle, report = normalize_capture_with_report(results, day, stamp, [day])
    assert not bundle.bars[0].limit_verified
    assert any(g.get("reason") == "DIRECT_LIMIT_SOURCE_BUSINESS_DATE_MISMATCH" for g in report["gaps"])


def test_pipeline_registers_direct_limit_batches_for_every_master(tmp_path):
    bundle, cutoff = fixtures()
    calls = []
    pipeline = MainboardPipeline(
        tmp_path / "cache",
        executor=source(bundle, calls),
        normalizer=normalizer(bundle),
        budget=RequestBudget(max_requests=1000, max_seconds=60, minimum_interval_seconds=0),
    )
    pipeline.capture(tmp_path / "capture", cutoff, observation_date=bundle.observation_date)
    requested = {iid for endpoint, params in calls if endpoint == "daily_limits_eastmoney" for iid in params["codes"]}
    assert requested == {security.instrument_id for security in bundle.universe.securities}
    assert any(stage["stage"] == "MarketBreadthLimitEvidence" for stage in pipeline.last_report["stages"])
    assert pipeline.last_report["actual_daily_ready"]


def test_summary_endpoint_is_paged_and_full_export_stays_immutable(tmp_path, monkeypatch):
    import stockfish_core.yaogu.candidate_service as candidate_module

    bundle, cutoff = make_bundle(80)
    monkeypatch.setattr(candidate_module, "utc_now", lambda: cutoff)
    core = ApplicationService(
        root=tmp_path / "state",
        store=SQLiteStore(tmp_path / "summary.sqlite3"),
        allow_test_fixture=True,
        data_dir=tmp_path / "NO_QLIB",
    )
    try:
        input_id = core.candidates.register_input(bundle, activate=True)
        submitted = core.candidates.submit(CandidateRequest(input_id=input_id), cutoff=cutoff)
        job = wait(core, submitted["job_id"])
        assert job["state"] == "SUCCEEDED", job
        run_id = job["result"]["candidate_run_id"]
        client = create_app(core).test_client()
        full = client.get("/api/candidate-runs/" + run_id)
        summary = client.get("/api/candidate-runs/" + run_id + "/summary?page_size=50")
        assert full.status_code == 200 and summary.status_code == 200
        assert summary.json["view"] == "SUMMARY_V1"
        assert len(summary.json["items"]) == 50
        assert summary.json["filtered_total"] == len(full.json["items"])
        assert "source_refs" not in summary.json
        assert len(json.dumps(summary.json, ensure_ascii=False)) < len(json.dumps(full.json, ensure_ascii=False)) / 2
        overview = client.get("/api/candidate-runs/" + run_id + "/summary?selection=display&page_size=50")
        assert overview.status_code == 200
        assert overview.json["filtered_total"] == len(full.json["display_ids"])
        family = full.json["items"][0]["primary_family"]
        family_summary = client.get("/api/candidate-runs/" + run_id + "/summary?family=" + family + "&page_size=10")
        assert family_summary.status_code == 200
        assert family_summary.json["filtered_total"] == sum(1 for item in full.json["items"] if item["primary_family"] == family)
        assert all(item["primary_family"] == family for item in family_summary.json["items"])
        original_get_run = core.candidates.get_run
        core.candidates.get_run = lambda _run_id: (_ for _ in ()).throw(AssertionError("summary must use projection"))
        projected = client.get("/api/candidate-runs/" + run_id + "/summary?page_size=10")
        assert projected.status_code == 200 and len(projected.json["items"]) == 10
        core.candidates.get_run = original_get_run
        detail = client.get("/api/candidate-runs/" + run_id + "/items/TEST000")
        assert detail.status_code == 200 and detail.json["item"] == full.json["items"][0]
        assert client.get("/api/candidate-runs/" + run_id + "/export?format=json").json == full.json
    finally:
        core.close()
