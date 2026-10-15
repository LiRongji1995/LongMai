"""TEST_FIXTURE transport only. No DNS, server, real token or market data."""
import json

import pytest
import requests

from stockfish_core.yaogu import provider_requests_worker as worker


TEST_ENDPOINT = "https://api.tushare.pro/"
TEST_TOKEN = 'TEST_FIXTURE_"\\_令牌'


class FixtureResponse:
    def __init__(self, body=None, *, status=200, url=TEST_ENDPOINT, history=()):
        self.body = body if body is not None else {"code": 0, "data": {
            "fields": ["ts_code", "trade_date"], "items": [["600000.SH", "20260904"]]}}
        self.status_code, self.url, self.history = status, url, history
        self.closed = False
        self.text = json.dumps(self.body, ensure_ascii=False)

    def json(self):
        return self.body

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("TEST_FIXTURE_HTTP_ERROR")


@pytest.fixture(autouse=True)
def isolated_transport(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", TEST_TOKEN)
    monkeypatch.delenv("TUSHARE_PRO_TOKEN", raising=False)
    monkeypatch.setattr(worker, "TUSHARE_VERIFIED_REST_ENDPOINT", None)
    def forbidden(*args, **kwargs):
        pytest.fail("TEST_FIXTURE forbids all unmocked HTTP")
    monkeypatch.setattr(requests, "post", forbidden)


def call(captured=None, partial_path=None, max_requests=2):
    return worker.tushare_request("tushare_daily", {"trade_date": "2026-09-04"},
                                  captured if captured is not None else [], max_requests, partial_path)


def test_default_transport_blocks_before_network_and_does_not_claim_empty():
    captured = []
    result = call(captured)
    assert result["status"] == "FETCH_FAILED"
    assert result["error_code"] == "TUSHARE_HTTPS_REST_NOT_VERIFIED"
    assert result["failure_category"] == "IMPLEMENTATION"
    assert result["coverage"]["http_requests"] == 0
    assert result["pagination_complete"] is False
    assert result["diagnostics"]["transport"] == "NOT_REQUESTED"
    assert result["diagnostics"]["endpoint_verified"] is False
    assert "empty_status" not in result["coverage"]
    assert captured == [] and TEST_TOKEN not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("endpoint", ["http://api.tushare.pro/", "https://evil.invalid/",
    "https://api.tushare.pro.evil.invalid/", "https://api.tushare.pro@evil.invalid/",
    "https://api.tushare.pro/mcp/", "https://api.tushare.pro/?token=x", "https://api.tushare.pro/#x"])
def test_plaintext_other_host_or_other_contract_rejected_before_credentials(monkeypatch, endpoint):
    monkeypatch.setattr(worker, "TUSHARE_VERIFIED_REST_ENDPOINT", endpoint)
    result = call()
    assert result["error_code"] == "TUSHARE_HTTPS_REST_ENDPOINT_REJECTED"
    assert result["coverage"]["http_requests"] == 0


@pytest.mark.parametrize("status,url,history", [(301, TEST_ENDPOINT, ()), (302, TEST_ENDPOINT, ()),
    (303, TEST_ENDPOINT, ()), (307, TEST_ENDPOINT, ()), (308, TEST_ENDPOINT, ()),
    (200, "http://api.tushare.pro/", ()), (200, "https://evil.invalid/", ()),
    (200, TEST_ENDPOINT, ("TEST_FIXTURE_REDIRECT",))])
def test_redirect_downgrade_and_changed_final_origin_are_rejected(monkeypatch, status, url, history):
    monkeypatch.setattr(worker, "TUSHARE_VERIFIED_REST_ENDPOINT", TEST_ENDPOINT)
    response = FixtureResponse(status=status, url=url, history=history)
    calls = []
    def post(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        assert endpoint == TEST_ENDPOINT
        assert kwargs["allow_redirects"] is False and kwargs["verify"] is True
        assert kwargs["json"]["token"] == TEST_TOKEN
        return response
    monkeypatch.setattr(requests, "post", post)
    captured = []
    result = call(captured)
    assert result["status"] == "FETCH_FAILED" and len(calls) == 1
    assert result["error_code"] == "TUSHARE_HTTPS_REST_REDIRECT_OR_ORIGIN_REJECTED"
    assert response.closed and captured == []
    assert TEST_TOKEN not in json.dumps(result, ensure_ascii=False)


def test_echoed_escaped_token_is_redacted_from_values_keys_rows_and_checkpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "TUSHARE_VERIFIED_REST_ENDPOINT", TEST_ENDPOINT)
    body = {"code": 0, "msg": TEST_TOKEN, TEST_TOKEN: [TEST_TOKEN], "data": {
        "fields": ["ts_code", "echo"], "items": [["600000.SH", TEST_TOKEN]]}}
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: FixtureResponse(body))
    captured = []
    checkpoint = tmp_path / "TEST_FIXTURE_checkpoint.json"
    result = call(captured, checkpoint)
    assert result["status"] == "COMPLETE"
    assert result["rows"][0]["echo"] == "[REDACTED]"
    saved = json.loads(checkpoint.read_text())
    for value in (result, captured, saved):
        serialized = json.dumps(value, ensure_ascii=False)
        assert TEST_TOKEN not in serialized
        assert json.dumps(TEST_TOKEN, ensure_ascii=False)[1:-1] not in serialized
        assert "[REDACTED]" in serialized
    assert captured[0]["body"]["[REDACTED]"] == ["[REDACTED]"]


def test_exception_containing_token_has_safe_error_and_no_response_payload(monkeypatch):
    monkeypatch.setattr(worker, "TUSHARE_VERIFIED_REST_ENDPOINT", TEST_ENDPOINT)
    def post(*args, **kwargs):
        raise requests.ConnectionError(TEST_TOKEN)
    monkeypatch.setattr(requests, "post", post)
    result = call()
    assert result["error_code"] == "UPSTREAM_CONNECTION_CLOSED"
    assert TEST_TOKEN not in json.dumps(result, ensure_ascii=False)
    assert result["http_responses"] == []


@pytest.mark.parametrize("prior_transport", ["HTTP_POST", "HTTPS_POST"])
def test_complete_checkpoint_is_reused_without_new_transport_or_rewrite(monkeypatch, tmp_path, prior_transport):
    monkeypatch.setattr(worker, "TUSHARE_VERIFIED_REST_ENDPOINT", TEST_ENDPOINT)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: FixtureResponse())
    checkpoint = tmp_path / "TEST_FIXTURE_complete.json"
    original = call(partial_path=checkpoint)
    # A frozen pre-policy HTTP checkpoint must never be relabelled as HTTPS.
    frozen = worker.read_partial(checkpoint)
    frozen["diagnostics"] = {"transport": prior_transport, "token_redacted": True}
    frozen["http_responses"][0]["url"] = TEST_ENDPOINT if prior_transport == "HTTPS_POST" else "http://api.tushare.pro"
    worker.write_partial(checkpoint, frozen)
    before = checkpoint.read_bytes()
    monkeypatch.setattr(worker, "TUSHARE_VERIFIED_REST_ENDPOINT", None)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: pytest.fail("cache made HTTP call"))
    recovered = call(partial_path=checkpoint)
    assert recovered["status"] == "COMPLETE" and recovered["rows"] == original["rows"]
    assert recovered["coverage"]["http_requests"] == 0
    assert recovered["coverage"]["cache_hit"] is True
    assert recovered["diagnostics"]["transport"] == "NOT_REQUESTED"
    assert recovered["diagnostics"]["transport_action"] == "READ_ONLY_CHECKPOINT_REUSE"
    assert recovered["diagnostics"]["endpoint_verified"] is False
    assert recovered["diagnostics"]["checkpoint_transport_diagnostics"] == frozen["diagnostics"]
    assert recovered["http_responses"] == frozen["http_responses"]
    assert checkpoint.read_bytes() == before


def test_partial_checkpoint_retains_rows_but_cannot_resume_insecure_transport(monkeypatch, tmp_path):
    from stockfish_core.yaogu.provider_requests import TUSHARE_ENDPOINTS
    monkeypatch.setitem(TUSHARE_ENDPOINTS["tushare_daily"], "page_limit", 1)
    monkeypatch.setattr(worker, "TUSHARE_VERIFIED_REST_ENDPOINT", TEST_ENDPOINT)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: FixtureResponse())
    checkpoint = tmp_path / "TEST_FIXTURE_partial.json"
    original = call(partial_path=checkpoint, max_requests=1)
    assert original["status"] == "PARTIAL" and original["error_code"] == "EXPLICIT_HTTP_REQUEST_BUDGET_EXHAUSTED"
    monkeypatch.setattr(worker, "TUSHARE_VERIFIED_REST_ENDPOINT", None)
    recovered = call(partial_path=checkpoint)
    assert recovered["status"] == "PARTIAL" and recovered["rows"] == original["rows"]
    assert recovered["error_code"] == "TUSHARE_HTTPS_REST_NOT_VERIFIED"
    assert recovered["coverage"]["http_requests"] == 0 and recovered["next_offset"] == 1
    assert recovered["pagination_complete"] is False
    assert recovered["diagnostics"]["transport"] == "NOT_REQUESTED"
    assert recovered["diagnostics"]["endpoint_verified"] is False
    assert recovered["diagnostics"]["checkpoint_transport_diagnostics"]["transport"] == "HTTPS_POST"
