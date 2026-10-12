"""OPENPF_API_TOKEN middleware: /api routes + websockets need the token; health is open."""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.auth import ApiTokenMiddleware


def _app(token: str) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/thing")
    def thing():
        return {"ok": True}

    @app.websocket("/api/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    app.add_middleware(ApiTokenMiddleware, token=token)
    return app


def test_requires_bearer_token_when_set():
    client = TestClient(_app("s3cret"))
    assert client.get("/health").status_code == 200
    assert client.get("/api/thing").status_code == 401
    assert client.get("/api/thing", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/thing", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_websocket_requires_query_token():
    client = TestClient(_app("s3cret"))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws") as ws:
            ws.receive_json()
    with client.websocket_connect("/api/ws?token=s3cret") as ws:
        assert ws.receive_json() == {"ok": True}


def test_open_when_token_unset():
    client = TestClient(_app(""))
    assert client.get("/api/thing").status_code == 200
