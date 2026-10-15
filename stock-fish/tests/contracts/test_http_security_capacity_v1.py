"""TEST_FIXTURE: in-memory HTTP security and stream admission; no server or DB."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from stockfish_core.api import create_app


class FixtureJobs:
    def __init__(self):
        self.value = dict(job_id="TEST_FIXTURE", state="RUNNING", revision=1,
                          progress=0.1, result=None, error=None, message="TEST_FIXTURE")

    def register(self, *args, **kwargs):
        pass

    def get(self, job_id):
        return dict(self.value) if job_id == "TEST_FIXTURE" else None

    def cancel(self, job_id):
        self.value.update(state="CANCELLED", revision=2)
        return self.get(job_id)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("STOCKFISH_API_KEY", raising=False)
    monkeypatch.delenv("STOCKFISH_ALLOWED_HOSTS", raising=False)
    jobs = FixtureJobs()
    core = SimpleNamespace(jobs=jobs, health=lambda **kwargs: {"status": "healthy"},
                           readiness=lambda: {"status": "ready", "ready": True})
    return create_app(core)


@pytest.mark.parametrize("path", ["/health/ready", "/health/deep", "/api/health/deep",
                                  "/api/health", "/api/config/health"])
def test_health_diagnostic_aliases_share_auth_peer_origin_and_no_store(monkeypatch, path):
    calls = []
    def health(**kwargs):
        calls.append(kwargs)
        return {"status": "healthy", "ready": True}
    core = SimpleNamespace(jobs=FixtureJobs(), health=health, readiness=health)
    monkeypatch.setenv("STOCKFISH_API_KEY", "TEST_FIXTURE_KEY")
    http = create_app(core).test_client()
    assert http.get(path).status_code == 401
    auth = {"Authorization": "Bearer TEST_FIXTURE_KEY"}
    assert http.get(path, headers={**auth, "Origin": "https://evil.invalid"}).status_code == 403
    assert http.get(path, headers=auth, base_url="http://evil.invalid").status_code == 403
    assert calls == []
    response = http.get(path, headers=auth)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert len(calls) == 1
    monkeypatch.delenv("STOCKFISH_API_KEY")
    http = create_app(core).test_client()
    assert http.get(path, environ_overrides={"REMOTE_ADDR": "192.0.2.9"},
                    headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403
    assert len(calls) == 1


def test_public_liveness_remains_constant_cost_with_configured_key(monkeypatch):
    monkeypatch.setenv("STOCKFISH_API_KEY", "TEST_FIXTURE_KEY")
    def forbidden(*args, **kwargs):
        pytest.fail("liveness must not inspect application dependencies")
    core = SimpleNamespace(jobs=FixtureJobs(), health=forbidden, readiness=forbidden)
    response = create_app(core).test_client().get("/health/live",
        environ_overrides={"REMOTE_ADDR": "192.0.2.9"})
    assert response.status_code == 200
    assert response.json == {"status": "alive", "alive": True, "health_contract": "liveness",
                             "checks": {"process": "alive", "http": "responsive"}}


def test_two_streams_leave_four_thread_capacity_for_status_and_cancel(app):
    ready = [Event(), Event()]
    release = Event()
    def hold_stream(index):
        response = app.test_client().get("/api/jobs/TEST_FIXTURE/stream", buffered=False)
        try:
            assert response.status_code == 200
            ready[index].set()
            assert release.wait(5), "TEST_FIXTURE controller did not release stream"
        finally:
            response.close()
    def request(path, method="get"):
        return getattr(app.test_client(), method)(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        streams = [pool.submit(hold_stream, n) for n in range(2)]
        try:
            assert all(event.wait(2) for event in ready)
            refused = pool.submit(request, "/api/jobs/TEST_FIXTURE/stream").result(timeout=2)
            assert refused.status_code == 429
            assert refused.json["error"] == "STREAM_CAPACITY_EXCEEDED"
            assert refused.headers["Retry-After"] == "1"
            assert pool.submit(request, "/api/jobs/TEST_FIXTURE").result(timeout=2).status_code == 200
            cancelled = pool.submit(request, "/api/jobs/TEST_FIXTURE/cancel", "post").result(timeout=2)
            assert cancelled.status_code == 200 and cancelled.json["state"] == "CANCELLED"
        finally:
            release.set()
        for future in streams:
            future.result(timeout=2)


def test_stream_admission_releases_on_unconsumed_close_and_head(app):
    for _ in range(5):
        with app.test_request_context("/api/jobs/TEST_FIXTURE/stream"):
            response = app.view_functions["job_stream"]("TEST_FIXTURE")
            assert response.status_code == 200
            response.close()  # Also close twice: release must be idempotent.
            response.close()
        response = app.test_client().head("/api/jobs/TEST_FIXTURE/stream")
        assert response.status_code == 200
        response.close()


def test_stream_admission_releases_after_terminal_or_generator_error(app, monkeypatch):
    jobs = app.extensions["stockfish_core"].jobs
    jobs.value.update(state="SUCCEEDED")
    for _ in range(4):
        response = app.test_client().get("/api/jobs/TEST_FIXTURE/stream", buffered=True)
        assert response.status_code == 200 and b'SUCCEEDED' in response.data
    original_get = jobs.get
    calls = 0
    def broken_get(job_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("TEST_FIXTURE_STREAM_READ_FAILURE")
        return original_get(job_id)
    monkeypatch.setattr(jobs, "get", broken_get)
    with pytest.raises(RuntimeError, match="TEST_FIXTURE_STREAM_READ_FAILURE"):
        app.test_client().get("/api/jobs/TEST_FIXTURE/stream", buffered=True)
    monkeypatch.setattr(jobs, "get", original_get)
    for _ in range(3):
        assert app.test_client().get("/api/jobs/TEST_FIXTURE/stream", buffered=True).status_code == 200


def test_stream_aliases_share_capacity_and_auth_failures_do_not_consume_slots(app):
    # Exercise aliases in separate threads so each streamed Flask context closes
    # on its own thread, just as it does in the synchronous WSGI deployment.
    held = [Event(), Event()]
    release = Event()
    def hold(path, index):
        response = app.test_client().get(path, buffered=False)
        try:
            assert response.status_code == 200
            held[index].set()
            assert release.wait(5)
        finally:
            response.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(hold, path, index) for index, path in enumerate((
            "/api/predict/TEST_FIXTURE/stream", "/api/qlib/train/TEST_FIXTURE/stream"))]
        try:
            assert all(event.wait(2) for event in held)
            assert app.test_client().get("/api/jobs/TEST_FIXTURE/stream").status_code == 429
        finally:
            release.set()
        for future in futures:
            future.result(timeout=2)
    for _ in range(4):
        assert app.test_client().get("/api/jobs/TEST_FIXTURE/stream",
            headers={"Origin": "https://evil.invalid"}).status_code == 403
    response = app.test_client().get("/api/jobs/TEST_FIXTURE/stream", buffered=False)
    assert response.status_code == 200
    response.close()
