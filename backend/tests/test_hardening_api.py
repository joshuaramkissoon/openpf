"""API surface: bounded config, CSRF origin guard, no leaked internals."""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.auth import OriginGuardMiddleware
from app.schemas.config import LeveragedConfig, RiskConfig
from app.services import artifact_service


def _risk(**over):
    base = dict(max_single_order_notional=500, max_daily_notional=1500, max_position_weight=0.25,
                duplicate_order_window_seconds=90)
    return {**base, **over}


@pytest.mark.parametrize(
    "over",
    [
        {"max_single_order_notional": float("inf")},
        {"max_daily_notional": float("nan")},
        {"max_single_order_notional": -1},
        {"max_daily_notional": 1e12},
        {"max_position_weight": 1.5},
        {"duplicate_order_window_seconds": 0},
        {"duplicate_order_window_seconds": 10**9},
    ],
)
def test_risk_rails_reject_nonsense(over):
    with pytest.raises(ValidationError):
        RiskConfig(**_risk(**over))


def test_risk_rails_accept_sane_values():
    assert RiskConfig(**_risk(max_single_order_notional=0)).max_single_order_notional == 0  # kill switch ok


def test_leveraged_close_time_validated():
    good = dict(enabled=False, auto_execute_enabled=False, per_position_notional=200, max_total_exposure=600,
                max_open_positions=3, take_profit_pct=0.08, stop_loss_pct=0.05, close_time_uk="15:30",
                allow_overnight=False, scan_symbols=[], instrument_priority=[])
    LeveragedConfig(**good)
    with pytest.raises(ValidationError):
        LeveragedConfig(**{**good, "close_time_uk": "25:99"})


@pytest.fixture()
def guarded():
    app = FastAPI()

    @app.post("/api/do")
    def do():
        return {"ok": True}

    @app.get("/api/read")
    def read():
        return {"ok": True}

    @app.websocket("/api/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_json({"ok": True})
        await socket.close()

    app.add_middleware(OriginGuardMiddleware, allowed_origins=["http://localhost:5173"],
                       allowed_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")
    return TestClient(app)


def test_cross_site_post_blocked(guarded):
    r = guarded.post("/api/do", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert "not allowed" in r.json()["detail"]


def test_allowed_and_same_site_origins_pass(guarded):
    assert guarded.post("/api/do", headers={"origin": "http://localhost:5184"}).status_code == 200
    assert guarded.post("/api/do").status_code == 200  # no Origin (curl)
    # Vite proxy on a tailnet hostname: Origin matches X-Forwarded-Host.
    assert guarded.post("/api/do", headers={"origin": "http://box.tail.ts.net:5173",
                                            "x-forwarded-host": "box.tail.ts.net:5173"}).status_code == 200
    # Reads are never blocked (CORS already stops cross-site reading).
    assert guarded.get("/api/read", headers={"origin": "https://evil.example"}).status_code == 200


def test_cross_site_websocket_blocked(guarded):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with guarded.websocket_connect("/api/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    with guarded.websocket_connect("/api/ws", headers={"origin": "http://localhost:5173"}) as ws:
        assert ws.receive_json() == {"ok": True}


def test_unhandled_errors_do_not_leak(monkeypatch, tmp_path):
    from app.main import app
    import app.api.agent as agent

    def boom():
        raise RuntimeError("secret internals /Users/x/.env")

    monkeypatch.setattr(agent, "list_artifacts", boom)
    r = TestClient(app, raise_server_exceptions=False).get("/api/agent/artifacts")
    assert r.status_code == 500
    assert "secret" not in r.text and "Traceback" not in r.text


@pytest.mark.parametrize("path", ["../../backend/.env", "..%2f..%2fetc/passwd", "/etc/passwd", "a\x00b.md"])
def test_artifact_path_traversal(monkeypatch, tmp_path, path):
    root = tmp_path / "artifacts"
    root.mkdir()
    (tmp_path / "secret.md").write_text("nope")
    monkeypatch.setattr(artifact_service, "_artifacts_root", lambda: root)
    monkeypatch.setattr(artifact_service, "_legacy_cron_root", lambda: tmp_path / "none")
    assert artifact_service.get_artifact(path) is None
    assert artifact_service.get_artifact("../secret.md") is None


def test_old_db_gets_missing_columns(monkeypatch, tmp_path):
    from sqlalchemy import create_engine, inspect, text

    import app.core.database as database

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}", future=True)
    with engine.begin() as conn:  # an old trade_intents without most of today's columns
        conn.execute(text("CREATE TABLE trade_intents (id VARCHAR(36) PRIMARY KEY, symbol VARCHAR(32), quantity FLOAT)"))
        conn.execute(text("INSERT INTO trade_intents VALUES ('a', 'AAPL', 1.0)"))
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()
    cols = {c["name"] for c in inspect(engine).get_columns("trade_intents")}
    assert {"status", "metadata", "failure_reason", "estimated_notional"} <= cols
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status FROM trade_intents")).scalar() == "proposed"


def test_scheduler_start_failure_is_not_fatal(monkeypatch):
    import app.main as main

    def broken():
        raise RuntimeError("apscheduler exploded")

    monkeypatch.setattr(main, "_scheduler_should_run", lambda: True)
    monkeypatch.setattr(main, "start_scheduler", broken)
    assert main._start_scheduler_safely() is False  # logged, not raised


def test_cancel_order_id_must_be_numeric():
    from app.main import app

    r = TestClient(app).delete("/api/orders/..%2F..%2Faccount?account=invest")
    assert r.status_code in (404, 422)
