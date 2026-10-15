"""Loopback-only ASGI admission shared by HTTP and WebSocket transports.

The dashboard has no remote authentication. Forwarded headers are not identity
evidence, and browser requests must use the dashboard's exact origin.
"""
from __future__ import annotations

import ipaddress
from threading import Lock
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse


def is_loopback_host(host: str) -> bool:
    if not isinstance(host, str) or not host or host != host.strip() or "%" in host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _authority(value: str, scheme: str) -> tuple[str, int]:
    if (not value or len(value) > 255 or any(char.isspace() for char in value)
            or any(char in value for char in "\\/?#@")):
        raise ValueError("invalid authority")
    parsed = urlsplit("//" + value)
    if (parsed.username is not None or parsed.password is not None or parsed.path
            or parsed.query or parsed.fragment or not parsed.hostname
            or value.endswith(":")):
        raise ValueError("invalid authority")
    host = parsed.hostname.lower()
    if not is_loopback_host(host):
        raise ValueError("nonlocal host")
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("invalid port")
    return host, port if port is not None else (443 if scheme == "https" else 80)


def local_request_allowed(scope: dict) -> bool:
    """Validate transport addresses and single, unambiguous browser headers."""
    try:
        # Missing or non-IP peer evidence fails closed. No DNS resolution or
        # special test-client names can grant access to a deployed application.
        client, server = scope.get("client"), scope.get("server")
        if not client or not server or not ipaddress.ip_address(client[0]).is_loopback:
            return False
        if not is_loopback_host(server[0]):
            return False
        scheme = {"http": "http", "https": "https", "ws": "http", "wss": "https"}.get(scope.get("scheme"))
        if scheme is None:
            return False
        headers = {}
        for key, value in scope.get("headers", ()):
            key = key.lower()
            if key in (b"host", b"origin", b"sec-fetch-site"):
                if key in headers:
                    return False
                headers[key] = value.decode("ascii")
        host = _authority(headers.get(b"host", ""), scheme)
        if type(server[1]) is not int or not 1 <= server[1] <= 65535 or host[1] != server[1]:
            return False
        origin = headers.get(b"origin")
        if origin is not None:
            if any(char.isspace() for char in origin) or any(char in origin for char in "\\?#"):
                return False
            parsed = urlsplit(origin)
            if (parsed.scheme != scheme or not parsed.netloc or parsed.path
                    or parsed.query or parsed.fragment
                    or _authority(parsed.netloc, parsed.scheme) != host):
                return False
        # Browser same-site is broader than same-origin (including other local
        # ports). Native clients without Origin/Fetch Metadata remain supported.
        if headers.get(b"sec-fetch-site", "none") not in ("none", "same-origin"):
            return False
        return True
    except (ValueError, TypeError, UnicodeError, IndexError):
        return False


class WebSocketCapacity:
    """An application-wide hard bound, also safe across ASGI test loops."""
    def __init__(self, limit: int):
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("max_websocket_connections must be between 1 and 128")
        self.limit = limit
        self._active = 0
        self._lock = Lock()

    def acquire(self) -> bool:
        with self._lock:
            if self._active >= self.limit:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._active -= 1


class LocalAccessMiddleware:
    def __init__(self, app, *, capacity: WebSocketCapacity):
        self.app, self.capacity = app, capacity

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if not local_request_allowed(scope):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": "LOCAL_ACCESS_DENIED"})
            else:
                response = JSONResponse(status_code=403, content={"error": "LOCAL_ACCESS_DENIED"},
                                        headers={"Cache-Control": "no-store"})
                await response(scope, receive, send)
            return
        if scope["type"] == "http":
            await self.app(scope, receive, send)
            return
        if not self.capacity.acquire():
            await send({"type": "websocket.close", "code": 1013, "reason": "WEBSOCKET_CAPACITY_EXCEEDED"})
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self.capacity.release()
