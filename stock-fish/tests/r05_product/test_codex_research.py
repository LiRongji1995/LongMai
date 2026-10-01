"""Isolated synthetic HTTP contracts; no live service, market capture or model use."""
from copy import deepcopy
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scripts import codex_research as entry
from stockfish_core.yaogu.strategy_experiments import IMPLEMENTATION_VERSION, source_identity


@pytest.fixture
def saved_http():
    """Tiny generated protocol example, not a real market research result."""
    rows = [{"instrument_id": iid, "rank": rank, "A": 1.4, "R": 0.02, "H": 0.03, "S_PV": 75}
            for rank, iid in enumerate(("600001.SH", "000001.SZ", "600002.SH"), 1)]
    run = {"run_id": "strategy_experiment_test", "input_id": "market_" + "a" * 64,
           "input_hash": "a" * 64, "effective_input_hash": "b" * 64, "universe_hash": "c" * 64,
           "implementation_version": IMPLEMENTATION_VERSION, **source_identity(),
           "decision_cutoff": "2026-09-04T16:30:00+08:00", "generated_at": "2026-09-06T23:33:00+08:00",
           "observation_date": "2026-09-04", "coverage_type": "RECONSTRUCTED_HISTORICAL",
           "results": {entry.PV: {"items": rows, "status": "LIMITED_SCOPE", "counts": {"eligible": 4, "evaluated": 3, "missing": 1}}},
           "selection_results": {entry.FOCUS: {"selected_ids": [rows[0]["instrument_id"]]}},
           "export_view": {"strategy_id": entry.PV, "selection": "all", "source_content_hash": "d" * 64,
                           "filtered_total": 3, "ranked_total": 3}}
    summary = {key: run[key] for key in ("run_id", "input_id", "observation_date", "decision_cutoff", "generated_at", "coverage_type")}
    summary.update(source_run_hash="d" * 64, items=rows, filtered_total=3, page=1, has_next=False,
                   focus_ids=[rows[0]["instrument_id"]], results={entry.PV: {"status": "LIMITED_SCOPE", "item_count": 3}})
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=["run_id", "observation_date", "strategy_id", "instrument_id", "rank"])
    writer.writeheader()
    writer.writerows({"run_id": run["run_id"], "observation_date": run["observation_date"], "strategy_id": entry.PV,
                     "instrument_id": row["instrument_id"], "rank": row["rank"]} for row in rows)
    return run, summary, ("\ufeff" + stream.getvalue()).encode("utf-8")


class FakeClient:
    base_url = "http://127.0.0.1:8000"

    def __init__(self, fixture):
        self.run, self.summary, self.csv = deepcopy(fixture)
        self.calls = []

    def raw(self, path, payload=None):
        self.calls.append((path, payload))
        assert payload is None, "read commands must never POST"
        assert "selection=all" in path
        return self.csv if "format=csv" in path else json.dumps(self.run).encode()

    def get(self, path, payload=None):
        self.calls.append((path, payload))
        assert payload is None, "read commands must never POST"
        if path.startswith("/api/strategy-experiments?"):
            return [self.summary]
        if "/summary?" in path:
            return self.summary
        if path.endswith("/review"):
            return {"run_id": self.run["run_id"], "status": "PENDING_MARKET_VALIDATION", "market_edge": "NOT_ESTABLISHED"}
        raise AssertionError(path)


@pytest.mark.parametrize("target,expected", [("next-session", "2026-09-07"), ("tomorrow", "2026-09-08"), ("2026-09-25", "2026-09-28")])
def test_exchange_target_distinguishes_upcoming_open_tomorrow_and_holiday(target, expected):
    context = entry.calendar_context(target, datetime(2026, 9, 7, 0, 30, tzinfo=entry.SH))
    assert context["target_session"] == expected
    assert context["latest_completed_session"] == "2026-09-04"


def test_after_open_next_session_is_not_current_trading_session():
    context = entry.calendar_context("next-session", datetime(2026, 9, 7, 10, tzinfo=entry.SH))
    assert context["target_session"] == "2026-09-08"
    assert context["expected_observation_date"] == "2026-09-07"


@pytest.mark.parametrize("target,now", [("2027-01-01", datetime(2026, 9, 7, tzinfo=entry.SH)),
                                       ("next-session", datetime(2027, 1, 1, tzinfo=entry.SH))])
def test_calendar_never_guesses_beyond_existing_official_coverage(target, now):
    with pytest.raises(entry.ResearchError, match="CALENDAR_OUT_OF_COVERAGE"):
        entry.calendar_context(target, now)


def test_selection_export_and_report_reuse_one_saved_order_without_post(saved_http, tmp_path):
    client = FakeClient(saved_http)
    context = entry.calendar_context("tomorrow", datetime(2026, 9, 7, 0, 30, tzinfo=entry.SH))
    bundle, skipped = entry.select_saved(client, context)
    result = entry.save_bundle(client, bundle, tmp_path / "research", context)
    assert skipped == [] and result["reused_saved_run"] is True
    assert result["newly_computed_ranking"] is False
    assert result["observation_date"] == "2026-09-04"  # generated_at 9/6 cannot become market freshness.
    assert result["target"]["target_session"] == "2026-09-08"
    destination = Path(result["saved_directory"])
    assert (destination / "candidates.csv").read_bytes() == saved_http[2]
    exported = json.loads((destination / "source.json").read_text())
    assert exported["results"][entry.PV]["items"] == saved_http[0]["results"][entry.PV]["items"]
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["ranking_count"] == 3
    report = (destination / "report.md").read_text(encoding="utf-8")
    assert "最后可用结果" in report and "LIMITED_SCOPE" in report
    assert all(payload is None for _, payload in client.calls)


def test_theme_capital_profile_can_reuse_insufficient_evidence_view(saved_http, tmp_path):
    saved, summary, _ = deepcopy(saved_http)
    saved["results"] = {entry.THEME_CAPITAL: {
        "items": [],
        "status": "INSUFFICIENT_EVIDENCE",
        "counts": {"eligible": 4, "evaluated": 4, "passed": 0, "unknown": 4},
        "parameter_status_label_zh": "尚未验证",
    }}
    saved["export_view"].update(strategy_id=entry.THEME_CAPITAL, filtered_total=0, ranked_total=0)
    summary["results"] = {entry.THEME_CAPITAL: {"status": "INSUFFICIENT_EVIDENCE", "item_count": 0}}
    summary["items"] = []
    summary["filtered_total"] = 0
    stream = io.StringIO(newline="")
    csv.DictWriter(stream, fieldnames=["run_id", "observation_date", "strategy_id", "instrument_id", "rank"]).writeheader()
    client = FakeClient((saved, summary, ("\ufeff" + stream.getvalue()).encode("utf-8")))
    bundle = entry.official_exports(client, saved["run_id"], entry.THEME_CAPITAL)
    entry.verify_reusable(bundle[0], entry.THEME_CAPITAL)
    result = entry.save_bundle(client, bundle, tmp_path / "theme-capital", strategy_id=entry.THEME_CAPITAL)
    manifest = json.loads((Path(result["saved_directory"]) / "manifest.json").read_text(encoding="utf-8"))
    report = (Path(result["saved_directory"]) / "report.md").read_text(encoding="utf-8")
    assert manifest["strategy_id"] == entry.THEME_CAPITAL
    assert result["research_profile"] == entry.THEME_CAPITAL
    assert "尚未验证" in report and "INSUFFICIENT_EVIDENCE" in report


def test_legacy_v4_pv_source_hash_is_read_only_reusable_not_new_profile(saved_http):
    saved = deepcopy(saved_http[0])
    saved["source_hash"] = next(iter(entry.READ_ONLY_LEGACY_PV_SOURCE_HASHES))
    entry.verify_reusable(saved, entry.PV)
    with pytest.raises(entry.ResearchError, match="NOT_CONFORMANT"):
        entry.verify_reusable(saved, entry.THEME_CAPITAL)


@pytest.mark.parametrize("corruption", ["csv_order", "source_hash", "summary_order", "count"])
def test_export_mismatch_does_not_create_output_or_replace_previous(saved_http, tmp_path, corruption):
    client = FakeClient(saved_http)
    old = tmp_path / "old.md"
    old.write_text("prior successful research")
    if corruption == "csv_order":
        lines = client.csv.decode("utf-8-sig").splitlines()
        client.csv = ("\n".join([lines[0], lines[2], lines[1], lines[3]])).encode()
    elif corruption == "source_hash":
        client.summary["source_run_hash"] = "e" * 64
    elif corruption == "summary_order":
        client.summary["items"] = list(reversed(client.summary["items"]))
    else:
        client.run["export_view"]["ranked_total"] = 2
    with pytest.raises(entry.ResearchError, match="MISMATCH"):
        entry.save_bundle(client, entry.official_exports(client, client.run["run_id"]), tmp_path / "new")
    assert not (tmp_path / "new").exists()
    assert old.read_text() == "prior successful research"


def test_existing_export_directory_is_never_overwritten(saved_http, tmp_path):
    client = FakeClient(saved_http)
    output = tmp_path / "existing"
    output.mkdir()
    (output / "source.json").write_text("old")
    with pytest.raises(entry.ResearchError, match="OUTPUT_DIRECTORY_EXISTS_PRESERVED"):
        entry.save_bundle(client, entry.official_exports(client, client.run["run_id"]), output)
    assert (output / "source.json").read_text() == "old"


@pytest.mark.parametrize("corruption,reason", [("fixture", "NON_REAL"), ("input", "INPUT_HASH"),
                                               ("implementation", "NOT_CONFORMANT"), ("metadata", "METADATA_INCOMPLETE"),
                                               ("unavailable", "NOT_A_ZERO")])
def test_reuse_requires_real_input_identity_version_and_available_data(saved_http, corruption, reason):
    saved = deepcopy(saved_http[0])
    if corruption == "fixture":
        saved["coverage_type"] = "TEST_FIXTURE"
    elif corruption == "input":
        saved["input_id"] = "market_" + "f" * 64
    elif corruption == "implementation":
        saved["implementation_version"] = "R05_STRATEGY_EXPERIMENTS_V2"
    elif corruption == "metadata":
        del saved["effective_input_hash"]
    else:
        saved["results"][entry.PV]["status"] = "UNAVAILABLE"
        saved["results"][entry.PV]["items"] = []
    with pytest.raises(entry.ResearchError, match=reason):
        entry.verify_reusable(saved)


def test_full_coverage_true_empty_can_be_reused(saved_http):
    saved = deepcopy(saved_http[0])
    saved["results"][entry.PV].update(status="EMPTY", items=[])
    entry.verify_reusable(saved)


def test_run_with_unmatched_input_fails_closed_without_new_job(saved_http):
    client = FakeClient(saved_http)
    context = entry.calendar_context("next-session", datetime(2026, 9, 7, 0, 30, tzinfo=entry.SH))
    with pytest.raises(entry.ResearchError, match="INPUT_VERIFICATION_REQUIRED"):
        entry.select_saved(client, context, input_id="market_" + "f" * 64)
    assert len(client.calls) == 1 and client.calls[0][1] is None


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1.example.com", "http://localhost:8000",
                                  "http://user:password@127.0.0.1", "http://127.0.0.1?token=x", "http://127.0.0.1/path"])
def test_credentials_cannot_be_sent_to_arbitrary_url(url):
    with pytest.raises(entry.ResearchError, match="LOCAL_LOOPBACK_HTTP_URL_REQUIRED"):
        entry.Client(url, key="test-secret")


def test_key_file_reads_only_named_literal_variable_and_environment_wins(tmp_path, monkeypatch):
    private = tmp_path / ".env.private"
    private.write_text('OTHER_SECRET=do-not-load\nSTOCKFISH_API_KEY="$(not-executed)"\n', encoding="utf-8")
    monkeypatch.delenv("STOCKFISH_API_KEY", raising=False)
    assert entry.api_key(private) == "$(not-executed)"
    monkeypatch.setenv("STOCKFISH_API_KEY", "environment-key")
    assert entry.api_key(private) == "environment-key"
    monkeypatch.delenv("STOCKFISH_API_KEY", raising=False)
    private.write_text("STOCKFISH_API_KEY=one\nSTOCKFISH_API_KEY=two", encoding="utf-8")
    with pytest.raises(entry.ResearchError, match="AMBIGUOUS"):
        entry.api_key(private)


def test_http_redirect_is_not_followed_and_secret_error_body_is_not_exposed():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/api/should-not-be-followed")
            self.end_headers()
            self.wfile.write(b"server-echo-test-secret")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = entry.Client("http://127.0.0.1:" + str(server.server_port), key="test-secret")
        with pytest.raises(entry.ResearchError) as exc:
            client.get("/api/health")
        assert str(exc.value) == "HTTP_302"
        assert calls == ["/api/health"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_post_failure_has_no_automatic_retry_or_raw_secret_message():
    class Broken:
        calls = 0

        def open(self, *args, **kwargs):
            self.calls += 1
            raise OSError("provider echoed secret")

    client = entry.Client()
    client.opener = Broken()
    with pytest.raises(entry.ResearchError, match="NETWORK_FAILED_POST_STATE_UNKNOWN") as exc:
        client.get("/api/candidate-data-pipeline/capture", {})
    assert client.opener.calls == 1
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED", "INTERRUPTED"])
def test_non_success_job_never_reported_complete(state):
    class Jobs:
        def get(self, path):
            return {"job_id": "job_test", "state": state, "error": "secret body"}

    with pytest.raises(entry.ResearchError, match="JOB_" + state):
        entry.await_job(Jobs(), {"job_id": "job_test"}, 0)


def test_zero_wait_returns_pending_job_id_without_resubmission():
    class Jobs:
        def get(self, path):
            return {"job_id": "job_test", "state": "RUNNING"}

    result = entry.await_job(Jobs(), {"job_id": "job_test"}, 0)
    assert result["wait_expired"] is True and result["state"] == "RUNNING"


def test_invalid_wait_is_rejected_before_any_post():
    class Never:
        def get(self, *args):
            pytest.fail("invalid wait must not submit a job")

    args = entry.parser().parse_args(["capture", "--wait-seconds", "61"])
    with pytest.raises(entry.ResearchError, match="WAIT_SECONDS"):
        entry.execute(args, Never())


def test_status_and_rules_omit_large_market_records_and_json_schema():
    bulky = [{"secret_free_market_row": "large" * 1000}] * 5000

    class Status:
        def get(self, path):
            return {
                "/api/universe/coverage": {"status": "UNAVAILABLE", "observation_date": "2026-09-04",
                    "current_event_previews": bulky, "receipts": bulky,
                    "coverage": {"eligible": 3043, "eligibility_unknown_records": bulky}},
                "/api/candidate-data-pipeline": {"latest": {"consumer_status": "UNAVAILABLE", "remaining_requests": bulky,
                    "field_level_gaps": bulky, "queue": {"pending_request_ids": bulky, "status": "PARTIAL", "states": {"PENDING": 397}}}},
                "/api/health": {"status": "degraded", "components": {"tracker": {"status": "healthy", "record_details": bulky}}},
                "/api/strategy-configs/current": {"config": {"strategy_id": "DAILY_V1"}, "config_hash": "a" * 64, "schema": {"properties": bulky}},
                "/api/strategy-experiments/specs": [{"strategy_id": entry.PV}],
            }[path]

    status = entry.execute(entry.parser().parse_args(["status"]), Status())
    assert len(json.dumps(status)) < 3000
    assert status["coverage"]["coverage_counts"]["eligible"] == 3043
    assert status["pipeline"]["queue"]["states"]["PENDING"] == 397
    assert status["pipeline"]["field_gap_record_count"] == 5000
    rules = entry.execute(entry.parser().parse_args(["rules"]), Status())
    assert "schema" not in rules["current_formal_config"]
    assert rules["current_formal_config"]["config"]["strategy_id"] == "DAILY_V1"


def test_review_summary_and_report_keep_maturity_and_missing_dates(saved_http):
    review = {"run_id": saved_http[0]["run_id"], "status": "PENDING_MARKET_VALIDATION",
              "counts": {"outcome_observations": 24976, "mature": 0, "not_matured": 24976, "focus_episode_count": 17},
              "rolling_windows": {"20": {"requested_exchange_sessions": 20, "available_calendar_sessions": 20,
                  "observed_run_dates": ["2026-09-04"], "missing_run_dates": ["2026-09-03"],
                  "statistics": [{"strategy_id": entry.PV, "horizon": 5, "pooled_signals": {"mature_valid": 0, "up_rate": None},
                                  "huge_diagnostics": ["omit"] * 10000}]}},
              "uncertainty": {"status": "INSUFFICIENT_DATES_FOR_STABLE_INFERENCE"}}
    summary = entry.review_summary(review)
    assert len(json.dumps(summary)) < 2000
    assert summary["counts"]["mature"] == 0
    assert summary["rolling_windows"]["20"]["observed_run_date_count"] == 1
    assert summary["rolling_windows"]["20"]["pv_horizon_statistics"]["5"]["up_rate"] is None
    report = entry.report_text(saved_http[0], review)
    assert "24976" in report and "不是股票只数" in report
    assert "2026-09-06T23:33:00+08:00" in report
    assert "INSUFFICIENT_DATES_FOR_STABLE_INFERENCE" in report


def test_detail_summary_preserves_reasons_risks_and_bounds_observation_excerpt():
    row = {"instrument_id": "600001.SH", "rank": 1, "A": 1.4, "risk_flags": ["SOURCE_GAP"],
           "source_refs": ["large-source"] * 10000, "reason_codes": ["FOCUS_GATE_MET"], "selected": True}
    detail = {"run_id": "run_test", "instrument_id": "600001.SH", "observation_date": "2026-09-04",
              "strategy_results": {entry.PV: [row]}, "focus_selection": row,
              "outcomes": [{"horizon": 5, "label_end": "2026-09-11", "status": "NOT_MATURED", "metrics": {"close_reference_change": None},
                            "session_states": ["large"] * 10000}] * 100,
              "tracking": []}
    result = entry.detail_summary(detail)
    assert len(json.dumps(result)) < 6000
    assert result["focus_selection"]["reason_codes"] == ["FOCUS_GATE_MET"]
    assert result["strategy_results"][entry.PV][0]["risk_flags"] == ["SOURCE_GAP"]
    assert len(result["outcomes"]) == 20 and result["outcome_observation_count"] == 100


@pytest.mark.parametrize("kind,run_id", [("scheduled_candidate_capture", None), ("strategy_tracking", "run_test")])
def test_existing_inflight_job_is_reused_without_post(kind, run_id):
    class Jobs:
        def get(self, path, payload=None):
            assert payload is None
            if path == "/api/jobs?limit=100":
                return [{"kind": kind, "payload": {"run_id": run_id}, "state": "RUNNING", "job_id": "existing"}]
            assert path == "/api/jobs/existing"
            return {"job_id": "existing", "state": "RUNNING"}

    result = entry.submit_or_reuse_job(Jobs(), kind, "/api/unused", {}, 0, run_id)
    assert result["reused_inflight_job"] is True and result["job_id"] == "existing"


def test_saturated_job_window_fails_closed_without_claiming_no_older_inflight_job():
    class Jobs:
        def get(self, path, payload=None):
            assert payload is None
            return [{"kind": "candidate_run", "state": "SUCCEEDED"}] * 100

    with pytest.raises(entry.ResearchError, match="JOB_WINDOW_FULL"):
        entry.submit_or_reuse_job(Jobs(), "scheduled_candidate_capture", "/api/unused", {}, 0)


def test_default_repeated_export_reuses_verified_directory_without_copying(saved_http, tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "ROOT", tmp_path)
    client = FakeClient(saved_http)
    bundle = entry.official_exports(client, client.run["run_id"])
    first = entry.save_bundle(client, bundle)
    old_manifest = (Path(first["saved_directory"]) / "manifest.json").read_bytes()
    second = entry.save_bundle(client, bundle)
    assert second["saved_directory"] == first["saved_directory"]
    assert second["reused_local_bundle"] is True
    assert (Path(first["saved_directory"]) / "manifest.json").read_bytes() == old_manifest
    assert len(list((tmp_path / "reports" / "codex" / client.run["run_id"]).iterdir())) == 1


def test_changed_review_or_corrupt_local_copy_is_never_claimed_reusable(saved_http, tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "ROOT", tmp_path)
    client = FakeClient(saved_http)
    bundle = entry.official_exports(client, client.run["run_id"])
    first = entry.save_bundle(client, bundle)
    old_directory = Path(first["saved_directory"])
    (old_directory / "candidates.csv").write_bytes(b"corrupted local copy")
    second = entry.save_bundle(client, bundle)
    assert second["reused_local_bundle"] is False
    assert second["saved_directory"] != first["saved_directory"]
    assert (old_directory / "candidates.csv").read_bytes() == b"corrupted local copy"
    manifest = json.loads((Path(second["saved_directory"]) / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["review.json"]["sha256"] = "0" * 64
    assert entry.reusable_local_bundle(old_directory.parent, manifest) is None


def _stage_export_fixture(saved_http, with_focus=True):
    saved, summary, _ = deepcopy(saved_http)
    rows = [{"instrument_id": f"60000{index}.SH", "rank": 1 if status == "FOCUS" else None,
             "decision_status": status} for index, status in enumerate(
                 ("FOCUS" if with_focus else "WATCH", "WATCH", "REJECTED", "UNKNOWN"), 1)]
    saved["results"] = {entry.STAGE_AWARE: {"items": rows}}
    saved["export_view"].update(strategy_id=entry.STAGE_AWARE, filtered_total=4, ranked_total=int(with_focus))
    summary.update(items=deepcopy(rows), filtered_total=4)
    stream = io.StringIO(newline="")
    fields = ["run_id", "observation_date", "strategy_id", "instrument_id", "rank", "decision_status"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows({"run_id": saved["run_id"], "observation_date": saved["observation_date"],
                     "strategy_id": entry.STAGE_AWARE, **row} for row in rows)
    return saved, summary, stream.getvalue().encode()


@pytest.mark.parametrize("with_focus", [True, False])
def test_v2_export_verifies_all_four_groups_and_nullable_rank(saved_http, with_focus):
    fixture = _stage_export_fixture(saved_http, with_focus)
    client = FakeClient(fixture)
    saved, raw_json, raw_csv, summary = entry.official_exports(client, client.run["run_id"], entry.STAGE_AWARE)
    assert len(saved["results"][entry.STAGE_AWARE]["items"]) == 4
    assert raw_csv == fixture[2]
    assert [r["decision_status"] for r in summary["items"]][-2:] == ["REJECTED", "UNKNOWN"]


@pytest.mark.parametrize("corruption", ["csv_decision", "summary_decision", "focus_count", "order", "null_rank"])
def test_v2_export_rejects_decision_rank_and_count_corruption(saved_http, corruption):
    client = FakeClient(_stage_export_fixture(saved_http))
    if corruption == "csv_decision":
        client.csv = client.csv.replace(b"WATCH", b"FOCUS")
    elif corruption == "summary_decision":
        client.summary["items"][1]["decision_status"] = "FOCUS"
    elif corruption == "focus_count":
        client.run["export_view"]["ranked_total"] = 4
    elif corruption == "order":
        client.summary["items"].reverse()
    else:
        client.csv = client.csv.replace(b",,WATCH", b",0,WATCH")
    with pytest.raises(entry.ResearchError, match="MISMATCH"):
        entry.official_exports(client, client.run["run_id"], entry.STAGE_AWARE)


def test_v2_exports_from_actual_saved_daily_primary_api(tmp_path, monkeypatch):
    from tests.r05_product.test_daily_primary_critical_capture_boundary_v1 import _market_fixture, _service, _run_daily_primary
    from stockfish_core.api import create_app
    bundle, cutoff = _market_fixture(12, session_count=260)
    core = _service(tmp_path, monkeypatch, now=cutoff)
    try:
        input_id = core.candidates.register_input(bundle, activate=False)
        job, attempt = _run_daily_primary(core, input_id)
        assert job["state"] == "SUCCEEDED" and attempt["final_status"] == "READY"
        api = create_app(core).test_client()
        class SavedApiClient:
            def raw(self, path):
                response = api.get(path)
                assert response.status_code == 200, response.text
                return response.data
            def get(self, path):
                return json.loads(self.raw(path))
        saved, _, _, summary = entry.official_exports(SavedApiClient(), attempt["run_id"], entry.STAGE_AWARE)
        rows = saved["results"][entry.STAGE_AWARE]["items"]
        assert len(rows) == 12 == summary["filtered_total"]
        assert saved["export_view"]["ranked_total"] == sum(row["decision_status"] == "FOCUS" for row in rows)
    finally:
        core.close()


@pytest.mark.parametrize('path,payload,size,allowed', [
    ('/api/strategy-experiments/saved/export?format=json', None, 16, True),
    ('/api/strategy-experiments/saved/export?format=json', None, 17, False),
    ('/api/daily-research/report?target_session=2026-10-08', None, 16, True),
    ('/api/daily-research/report', None, 17, False),
    ('/api/daily-research/report', {}, 9, False),
    ('/api/daily-research/report-extra', None, 9, False),
    ('/api/jobs/job_test', None, 9, False),
    ('/api/jobs/job_test', None, 8, True),
    ('/api/strategy-experiments/saved/export', {}, 9, False),
    ('/api/strategy-experiments/saved/export-extra', None, 9, False),
])
def test_bounded_larger_response_only_for_readonly_saved_export(monkeypatch, path, payload, size, allowed):
    monkeypatch.setattr(entry, 'RESPONSE_MAX_BYTES', 8)
    monkeypatch.setattr(entry, 'SAVED_REPORT_MAX_BYTES', 16)
    class Response(io.BytesIO):
        def read(self, amount):
            assert amount in (9, 17)
            return super().read(amount)
    class Opener:
        calls = 0
        def open(self, *args, **kwargs):
            self.calls += 1
            return Response(b'x' * size)
    client = entry.Client()
    client.opener = Opener()
    if allowed:
        assert client.raw(path, payload) == b'x' * size
    else:
        with pytest.raises(entry.ResearchError, match='RESPONSE_SIZE_LIMIT_EXCEEDED'):
            client.raw(path, payload)
    assert client.opener.calls == 1


def test_export_timeout_is_bounded_and_does_not_change_post_or_job_timeout():
    class Opener:
        timeouts = []
        def open(self, req, *, timeout):
            self.timeouts.append(timeout)
            return io.BytesIO(b'{}')
    client = entry.Client(timeout=20, export_timeout=150)
    client.opener = Opener()
    client.raw('/api/strategy-experiments/saved/export?format=json')
    client.raw('/api/strategy-experiments/saved/export', {})
    client.raw('/api/jobs/job_test')
    client.raw('/api/daily-research/report?run_id=saved')
    assert client.opener.timeouts == [150, 20, 20, 150]
    for invalid in (0, -1, 181, float('nan'), float('inf')):
        with pytest.raises(entry.ResearchError, match='EXPORT_TIMEOUT_MUST_BE_0_TO_180_SECONDS'):
            entry.Client(export_timeout=invalid)
