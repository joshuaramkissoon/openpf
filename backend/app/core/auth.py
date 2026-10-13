"""Optional shared-secret API auth (``OPENPF_API_TOKEN``).

When the token is set, every ``/api`` HTTP route and websocket requires it —
``Authorization: Bearer <token>`` for HTTP, ``?token=<token>`` for websockets
(browsers can't set WS headers). Health checks and CORS preflights stay open.
When unset, the API is open (fine on loopback; a startup warning is logged when
bound elsewhere).
"""

from __future__ import annotations

import hmac
import json
from urllib.parse import parse_qs

_OPEN_PATHS = {"/health", "/api/health"}


def _token_matches(candidate: str, expected: str) -> bool:
    return bool(candidate) and hmac.compare_digest(candidate.encode(), expected.encode())


_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class OriginGuardMiddleware:
    """Cross-site request forgery guard for state-changing requests.

    CORS only stops a foreign page *reading* responses; a browser still sends a
    body-less cross-site POST (e.g. ``/leveraged/signals/{id}/execute``) or opens
    a websocket without any preflight. So unsafe HTTP methods and websocket
    handshakes that carry an ``Origin`` must come from an allowed origin: one in
    the CORS config, or the page's own origin (``Host``/``X-Forwarded-Host``, set
    by the Vite proxy). Requests without an Origin (curl, server-to-server) pass.
    """

    def __init__(self, app, allowed_origins: list[str], allowed_regex: str | None, api_prefix: str = "/api") -> None:
        import re

        self.app = app
        self.allowed = {o.rstrip("/").lower() for o in allowed_origins if o and o != "*"}
        self.allow_all = "*" in allowed_origins
        self.regex = re.compile(allowed_regex) if allowed_regex else None
        self.api_prefix = api_prefix.rstrip("/") or "/api"

    def _origin_ok(self, origin: str, headers: dict[str, str]) -> bool:
        from urllib.parse import urlparse

        origin = origin.rstrip("/").lower()
        if self.allow_all or origin in self.allowed or (self.regex and self.regex.match(origin)):
            return True
        netloc = urlparse(origin).netloc
        same_site = {
            headers.get("host", "").lower(),
            headers.get("x-forwarded-host", "").split(",")[0].strip().lower(),
        }
        return bool(netloc) and netloc in same_site

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        path = scope.get("path", "")
        if kind not in {"http", "websocket"} or not (path == self.api_prefix or path.startswith(self.api_prefix + "/")):
            await self.app(scope, receive, send)
            return
        if kind == "http" and scope.get("method") not in _UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        origin = headers.get("origin", "")
        if not origin or self._origin_ok(origin, headers):
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 4403})
            return
        body = json.dumps({"detail": f"origin {origin} is not allowed — add it to CORS_ORIGINS"}).encode()
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})


class ApiTokenMiddleware:
    """Pure ASGI middleware so it covers websockets as well as HTTP."""

    def __init__(self, app, token: str, api_prefix: str = "/api") -> None:
        self.app = app
        self.token = token
        self.api_prefix = api_prefix.rstrip("/") or "/api"

    def _protected(self, path: str) -> bool:
        if path in _OPEN_PATHS:
            return False
        return path == self.api_prefix or path.startswith(self.api_prefix + "/")

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if not self.token or kind not in {"http", "websocket"} or not self._protected(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        if kind == "http" and scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        auth = headers.get("authorization", "")
        candidate = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not candidate and kind == "websocket":
            query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
            candidate = (query.get("token") or [""])[0]

        if _token_matches(candidate, self.token):
            await self.app(scope, receive, send)
            return

        if kind == "websocket":
            # Closing before accept rejects the handshake (HTTP 403).
            await send({"type": "websocket.close", "code": 4401})
            return
        body = json.dumps({"detail": "unauthorized"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
