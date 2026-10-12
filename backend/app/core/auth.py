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
