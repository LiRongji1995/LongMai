from types import SimpleNamespace

from stockfish_core.api import create_app
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.scheduler import CandidateScheduler


class Forbidden:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected dependency access: {name}")


class FakeJobs:
    def register(self, *args, **kwargs):
        pass


class FakeQuant:
    def health(self):
        return {"status": "healthy"}


def test_liveness_is_process_only_and_does_not_touch_core(monkeypatch):
    monkeypatch.delenv("STOCKFISH_API_KEY", raising=False)
    core = SimpleNamespace(jobs=FakeJobs(), health=lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("liveness touched core health")))

    response = create_app(core).test_client().get("/health/live")

    assert response.status_code == 200
    assert response.json["status"] == "alive"
    assert response.json["health_contract"] == "liveness"


def test_readiness_reports_ready_without_deep_storage_scan(tmp_path, monkeypatch):
    monkeypatch.delenv("STOCKFISH_API_KEY", raising=False)
    core = ApplicationService(root=tmp_path / "state", store=SQLiteStore(tmp_path / "ready.sqlite3"),
                              quant=Forbidden(), allow_test_fixture=True, data_dir=tmp_path / "NO_QLIB")
    scheduler = CandidateScheduler(core.candidates, capture=lambda *args: None, interval=60)
    core.candidate_scheduler = scheduler
    scheduler.start()
    calls = {"deep": 0, "ready": 0}
    original_health = core.store.health
    original_readiness = core.store.readiness

    def forbidden_deep():
        calls["deep"] += 1
        raise AssertionError("readiness invoked deep storage health")

    def counted_readiness():
        calls["ready"] += 1
        return original_readiness()

    monkeypatch.setattr(core.store, "health", forbidden_deep)
    monkeypatch.setattr(core.store, "readiness", counted_readiness)
    try:
        response = create_app(core).test_client().get("/health/ready")
    finally:
        scheduler.stop()
        monkeypatch.setattr(core.store, "health", original_health)
        core.close()

    assert response.status_code == 200
    assert response.json["status"] == "ready"
    assert response.json["checks"]["database_connection"]["status"] == "ok"
    assert response.json["checks"]["scheduler"]["thread_alive"] is True
    assert calls == {"deep": 0, "ready": 1}


def test_readiness_reports_not_ready_when_database_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("STOCKFISH_API_KEY", raising=False)
    core = ApplicationService(root=tmp_path / "state", store=SQLiteStore(tmp_path / "not_ready.sqlite3"),
                              quant=Forbidden(), allow_test_fixture=True, data_dir=tmp_path / "NO_QLIB")
    scheduler = CandidateScheduler(core.candidates, capture=lambda *args: None, interval=60)
    core.candidate_scheduler = scheduler
    scheduler.start()
    monkeypatch.setattr(core.store, "readiness", lambda: {
        "component": "storage",
        "status": "unavailable",
        "path": str(core.store.path),
        "reason_codes": ["STORAGE_UNAVAILABLE"],
    })
    try:
        response = create_app(core).test_client().get("/health/ready")
    finally:
        scheduler.stop()
        core.close()

    assert response.status_code == 503
    assert response.json["status"] == "not_ready"
    assert "DATABASE_NOT_READY" in response.json["reason_codes"]
    assert response.json["checks"]["database_connection"]["status"] == "not_ready"


def test_deep_health_keeps_explicit_integrity_contract(monkeypatch):
    monkeypatch.delenv("STOCKFISH_API_KEY", raising=False)
    calls = []

    def health(*, deep=False):
        calls.append(deep)
        return {"status": "healthy", "components": {"tracker": {"status": "healthy"}},
                "production_ready": False, "health_contract": "deep_integrity" if deep else "readiness_compatible"}

    core = SimpleNamespace(jobs=FakeJobs(), health=health)

    response = create_app(core).test_client().get("/health/deep")

    assert response.status_code == 200
    assert response.json["health_contract"] == "deep_integrity"
    assert calls == [True]


def test_legacy_api_health_uses_light_readiness_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("STOCKFISH_API_KEY", raising=False)
    core = ApplicationService(root=tmp_path / "state", store=SQLiteStore(tmp_path / "api_health.sqlite3"),
                              quant=FakeQuant(), allow_test_fixture=True, data_dir=tmp_path / "NO_QLIB")
    monkeypatch.setattr(core.store, "health", lambda: (_ for _ in ()).throw(
        AssertionError("/api/health invoked deep storage health")))
    try:
        response = create_app(core).test_client().get("/api/health")
    finally:
        core.close()

    assert response.status_code == 200
    assert response.json["health_contract"] == "readiness_compatible"
    assert response.json["components"]["tracker"]["scope"]["deep_integrity"] == "NOT_CHECKED_USE_/health/deep"
