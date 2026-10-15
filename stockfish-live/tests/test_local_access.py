"""TEST_FIXTURE local HTTP/WebSocket admission, with no listening server."""
import asyncio
import json

from fastapi.testclient import TestClient
from httpx import Headers
import pytest
from starlette.websockets import WebSocketDisconnect

from stockfish_live import cli
from stockfish_live.api.app import create_app
from stockfish_live.api.local_access import LocalAccessMiddleware, WebSocketCapacity
from test_api import saved_run


def client_for(app, **kwargs):
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000), **kwargs)


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example"}, {"Host": "localhost.evil.example"},
    {"Host": "0.0.0.0"}, {"Host": "[::]"}, {"Host": "127.0.0.1:99999"},
    {"Host": "127.0.0.1:"}, {"Host": "127.0.0.1?"}, {"Host": "127.0.0.1#"},
    {"Host": "user@127.0.0.1"}, {"Host": "127.0.0.1/evil"},
    {"Origin": "https://evil.example"}, {"Origin": "null"},
    {"Origin": "http://127.0.0.1:8765"}, {"Origin": "http://localhost"},
    {"Origin": "https://127.0.0.1"}, {"Origin": "http://127.0.0.1/path"},
    {"Origin": "http://user@127.0.0.1"}, {"Origin": "http://127.0.0.1?x"},
    {"Origin": "http://127.0.0.1#"}, {"Origin": "http://127.0.0.1?"},
    {"Host": "localhost:9999", "Origin": "http://localhost:9999"},
    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
    [("Host", "127.0.0.1"), ("Host", "evil.example")],
    [("Origin", "http://127.0.0.1"), ("Origin", "http://evil.example")],
])
def test_http_and_socket_reject_invalid_authority_before_reading_run(tmp_path, monkeypatch, headers):
    app = create_app(tmp_path / "TEST_FIXTURE_absent")
    def forbidden(*args, **kwargs):
        pytest.fail("denied transport reached the saved-run reader")
    monkeypatch.setattr(app.state.run_catalog, "select", forbidden)
    with client_for(app) as client:
        response = client.get("/api/live/market", headers=Headers(headers))
        assert response.status_code == 403
        assert response.json() == {"error": "LOCAL_ACCESS_DENIED"}
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect("ws://127.0.0.1/ws/live", headers=Headers(headers)):
                pytest.fail("denied socket was accepted")
        assert rejected.value.code == 1008
    assert not (tmp_path / "TEST_FIXTURE_absent").exists()


@pytest.mark.parametrize("base_url,peer,headers", [
    ("http://127.0.0.1", "127.0.0.1", {}),
    ("http://127.0.0.1:8765", "127.0.0.1", {"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"}),
    ("http://localhost:8765", "127.0.0.1", {"Origin": "http://localhost:8765"}),
    ("http://[::1]:8765", "::1", {"Origin": "http://[::1]:8765"}),
    ("https://127.0.0.1", "127.0.0.1", {"Origin": "https://127.0.0.1:443"}),
])
def test_local_native_and_same_origin_browser_access_preserve_saved_bytes(tmp_path, base_url, peer, headers):
    root, _ = saved_run(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with TestClient(create_app(root), base_url=base_url, client=(peer, 50000)) as client:
        assert client.get("/api/live/market", headers=headers).status_code == 200
        # TestClient defaults to ws://testserver for relative WebSocket URLs.
        websocket_url = base_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws/live"
        with client.websocket_connect(websocket_url, headers=headers) as socket:
            assert socket.receive_json()["type"] == "snapshot"
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("peer", ["192.0.2.10", "::ffff:192.0.2.10", "testclient"])
def test_nonlocal_peer_cannot_be_overridden_by_forwarded_headers(tmp_path, peer):
    with TestClient(create_app(tmp_path / "TEST_FIXTURE_absent"), base_url="http://127.0.0.1", client=(peer, 1234)) as client:
        headers = {"X-Forwarded-For": "127.0.0.1", "X-Forwarded-Host": "localhost"}
        assert client.get("/", headers=headers).status_code == 403
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect("ws://127.0.0.1/ws/live", headers=headers):
                pytest.fail("nonlocal peer was accepted")
        assert rejected.value.code == 1008


def test_socket_limit_is_shared_and_disconnect_releases_slot(tmp_path):
    root, _ = saved_run(tmp_path)
    with client_for(create_app(root, max_websocket_connections=1)) as client:
        with client.websocket_connect("ws://127.0.0.1/ws/live") as first:
            assert first.receive_json()["type"] == "snapshot"
            with pytest.raises(WebSocketDisconnect) as rejected:
                with client.websocket_connect("ws://127.0.0.1/ws/live"):
                    pytest.fail("capacity was exceeded")
            assert rejected.value.code == 1013
            assert client.get("/api/live/market").status_code == 200
        with client.websocket_connect("ws://127.0.0.1/ws/live") as replacement:
            assert replacement.receive_json()["type"] == "snapshot"


def test_socket_internal_failure_is_generic_and_releases_capacity(tmp_path, monkeypatch):
    root, _ = saved_run(tmp_path)
    app = create_app(root, max_websocket_connections=1)
    original = app.state.run_catalog.select
    def fail(*args, **kwargs):
        raise RuntimeError("TEST_FIXTURE_PRIVATE_PATH_AND_SECRET")
    monkeypatch.setattr(app.state.run_catalog, "select", fail)
    with client_for(app) as client:
        with client.websocket_connect("ws://127.0.0.1/ws/live") as socket:
            result = socket.receive_json()
            assert result["error"] == "LIVE_VIEW_UNAVAILABLE"
            assert "SECRET" not in json.dumps(result)
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            assert closed.value.code == 1011
        monkeypatch.setattr(app.state.run_catalog, "select", original)
        with client.websocket_connect("ws://127.0.0.1/ws/live") as replacement:
            assert replacement.receive_json()["type"] == "snapshot"


@pytest.mark.parametrize("limit", [0, -1, 129, True, 1.5])
def test_invalid_connection_limits_fail_before_reading_run(tmp_path, limit):
    with pytest.raises(ValueError):
        create_app(tmp_path / "TEST_FIXTURE_absent", max_websocket_connections=limit)


def test_cancelled_socket_releases_slot():
    async def check():
        entered = asyncio.Event()
        async def downstream(scope, receive, send):
            entered.set()
            await asyncio.Future()
        capacity = WebSocketCapacity(1)
        middleware = LocalAccessMiddleware(downstream, capacity=capacity)
        scope = {"type": "websocket", "scheme": "ws", "client": ("127.0.0.1", 1234),
                 "server": ("127.0.0.1", 80), "headers": [(b"host", b"127.0.0.1")]}
        task = asyncio.create_task(middleware(scope, None, None))
        await entered.wait()
        assert not capacity.acquire()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert capacity.acquire()
        capacity.release()
    asyncio.run(check())


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "example.com", "localhost.example.com", "127.1", "2130706433"])
def test_cli_refuses_remote_listener_before_archive_or_server_access(tmp_path, capsys, monkeypatch, host):
    import stockfish_live.api.app as api_module
    def forbidden(*args, **kwargs):
        pytest.fail("unsafe listener reached archive startup")
    monkeypatch.setattr(api_module, "create_app", forbidden)
    assert cli.main(["serve", "--run-dir", str(tmp_path / "TEST_FIXTURE_absent"), "--host", host]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["error"] == "REMOTE_LISTEN_REQUIRES_AUTHENTICATION"


@pytest.mark.parametrize("requested,bound", [("localhost", "127.0.0.1"), ("LOCALHOST", "127.0.0.1"),
                                            ("127.0.0.2", "127.0.0.2"), ("::1", "::1")])
def test_cli_local_listener_uses_literal_loopback_without_proxy_trust(tmp_path, monkeypatch, capsys, requested, bound):
    from types import SimpleNamespace
    import stockfish_live.api.app as api_module
    import uvicorn
    calls = []
    app = SimpleNamespace(state=SimpleNamespace(run_catalog=SimpleNamespace(validate_startup=lambda: calls.append("checked"))))
    monkeypatch.setattr(api_module, "create_app", lambda root: app)
    def run(actual, *, host, port, proxy_headers):
        assert actual is app and calls == ["checked"]
        assert host == bound and port == 8765 and proxy_headers is False
        calls.append("TEST_FIXTURE_NO_LISTENER")
    monkeypatch.setattr(uvicorn, "run", run)
    assert cli.main(["serve", "--run-dir", str(tmp_path), "--host", requested]) == 0
    assert calls == ["checked", "TEST_FIXTURE_NO_LISTENER"]
    assert json.loads(capsys.readouterr().out) == {"server": "STOPPED"}
