"""Offline final red-team probes. No live provider, database, job or Docker run."""
from pathlib import Path
from types import SimpleNamespace
from importlib.metadata import version
import json

import pytest
import yaml

from stockfish_core.api import create_app
from stockfish_core.model_registry import ModelRegistry


class FakeJobs:
    def register(self, *args, **kwargs):
        pass


def client(monkeypatch, *, key=""):
    monkeypatch.setenv("STOCKFISH_API_KEY", key)
    monkeypatch.delenv("STOCKFISH_ALLOWED_HOSTS", raising=False)
    core = SimpleNamespace(jobs=FakeJobs(), health=lambda: {"status":"healthy"},
        store=SimpleNamespace(get_document=lambda *a: {"stored_evidence":"<script>alert('stored')</script>"}))
    return create_app(core).test_client()


def test_remote_peer_cannot_spoof_loopback_via_forwarded_headers(monkeypatch):
    response = client(monkeypatch).get("/api/health", headers={"X-Forwarded-For":"127.0.0.1"},
        environ_overrides={"REMOTE_ADDR":"198.51.100.20"})
    assert response.status_code == 403
    assert response.json["error"] == "REMOTE_API_REQUIRES_CONFIGURED_KEY"


def test_remote_host_and_cross_origin_gates_with_valid_key(monkeypatch):
    http = client(monkeypatch, key="offline-fixture-key")
    headers = {"Authorization":"Bearer offline-fixture-key"}
    assert http.get("/api/health",headers=headers,environ_overrides={"REMOTE_ADDR":"198.51.100.20"}).status_code == 200
    assert http.get("/api/health",headers=headers,base_url="http://evil.test").status_code == 403
    assert http.get("/api/health",headers=headers|{"Origin":"https://evil.test"}).status_code == 403
    assert http.get("/api/health",headers={"Authorization":"Bearer wrong"}).status_code == 401


def test_http_cannot_supply_trusted_bars_or_entitlement(monkeypatch):
    http = client(monkeypatch)
    value = {"bars":[],"securities":[],"data_license":"APPROVED","model_id":"fixture"}
    response = http.post("/api/recommendations",json=value)
    assert response.status_code == 400
    assert http.post("/api/snapshots",json=value).status_code in (404, 405)
    assert http.post("/api/config",json={"data_license":"APPROVED"}).status_code == 409


def test_reports_escape_saved_data_and_ignore_submitted_html(monkeypatch):
    response = client(monkeypatch).post("/api/report/download",json={
        "recommendation_run_id":"saved", "html":"ATTACKER_HTML_SHOULD_NOT_APPEAR"})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "ATTACKER_HTML_SHOULD_NOT_APPEAR" not in html
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("identity",["../checkpoint", "/tmp/payload.pkl", "C:\\payload", "ok/../../bad"])
def test_model_lookup_rejects_path_identifiers(tmp_path,identity):
    registry = ModelRegistry(tmp_path/"registry")
    with pytest.raises(ValueError): registry.get(identity)


def test_docker_defaults_are_scoped_without_running_docker():
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root/"docker-compose.yml").read_text(encoding="utf-8"))
    active = {name: value for name,value in compose["services"].items() if not value.get("profiles")}
    assert set(active) == {"stockfish"}
    core = active["stockfish"]
    assert core["build"]["dockerfile"] == "Dockerfile"
    assert core["user"] == "10001:10001" and core["read_only"] is True
    assert core["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in core["security_opt"]
    assert "STOCKFISH_API_KEY:?" in core["environment"]["STOCKFISH_API_KEY"]
    assert not core.get("depends_on") and not core.get("privileged")
    for service in compose["services"].values():
        assert all(port.startswith("127.0.0.1:") for port in service.get("ports",[]))
        assert not service.get("env_file")
        assert all("docker.sock" not in v and not v.startswith("./:") for v in service.get("volumes",[]))
    dockerfile = (root/"Dockerfile").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile and "python:3.12-slim" in dockerfile
    assert "COPY . ." not in dockerfile and "download.docker.com" not in dockerfile
    assert "COPY sxsc_tushare" not in dockerfile
    ignore = (root/".dockerignore").read_text(encoding="utf-8")
    assert all(pattern in ignore for pattern in (".venv","**/.env.*","**/*.sqlite*","**/*.key","**/*_tk.csv"))


def test_core_container_direct_pins_match_verified_runtime_metadata():
    root = Path(__file__).resolve().parents[2]
    pins = {}
    def requirement_lines(path):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("-r "):
                yield from requirement_lines(path.parent / line[3:].strip())
            else:
                yield line

    for line in requirement_lines(root / "requirements-core.txt"):
        if line and not line.startswith("#"):
            name, pin = line.split("==")
            pins[name.lower()] = pin
            assert version(name) == pin
    assert pins["pyqlib"] == "0.9.7" and pins["lightgbm"] == "4.7.0"
    assert not ({"pytdx","pymupdf","lark-oapi","sxsc_tushare","camel-oasis"} & set(pins))
