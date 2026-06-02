"""Endpoint tests for the orders router: pending/history normalisation, cancel
guarding + typed errors, and execution health/test. Isolated via a get_db
override (in-memory DB) with the T212 client and egress-IP lookup mocked."""

from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base, get_db
from app.main import app
from app.models import entities  # noqa: F401
from app.api import orders as orders_api
from app.services.config_store import ConfigStore
from app.services.t212_client import T212AuthError


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    yield Session
    engine.dispose()


@pytest.fixture()
def client(session_factory, monkeypatch):
    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(orders_api, "get_egress_ip", lambda **kw: "203.0.113.7")
    # Stub the name resolver (now takes a db) so tests stay hermetic — no T212
    # bulk-metadata network call via _instrument_meta_map.
    monkeypatch.setattr(orders_api, "_name_map", lambda db=None: {"AAPL_US_EQ": "Apple Inc."})
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


class FakeClient:
    def __init__(self, *, pending=None, history=None, summary=None, error=None):
        self._pending = pending or []
        self._history = history or []
        self._summary = summary or {}
        self._error = error
        self.cancelled = []

    def get_pending_orders(self):
        if self._error:
            raise self._error
        return self._pending

    def get_orders_history_page(self, *, limit=50, ticker=None):
        if self._error:
            raise self._error
        return self._history

    def get_account_summary(self):
        if self._error:
            raise self._error
        return self._summary

    def cancel_order(self, order_id):
        if self._error:
            raise self._error
        self.cancelled.append(order_id)


def _seed_invest(session_factory, *, read=True, exec_=True, exec_enabled=True):
    db = session_factory()
    store = ConfigStore(db)
    payload = {}
    if read:
        payload.update({"t212_api_key": "RK", "t212_api_secret": "RS"})
    if exec_:
        payload.update({"exec_api_key": "EK", "exec_api_secret": "ES"})
    payload["exec_enabled"] = exec_enabled
    store.set_account_credentials("invest", payload)
    db.commit()
    db.close()


def test_pending_orders_normalised(client, session_factory, monkeypatch):
    _seed_invest(session_factory)
    fake = FakeClient(pending=[
        {"id": "o1", "ticker": "AAPL_US_EQ", "quantity": -3, "limitPrice": 195.5, "type": "LIMIT", "status": "NEW"},
    ])
    monkeypatch.setattr(orders_api, "build_t212_client", lambda *a, **k: fake)

    resp = client.get("/api/orders/pending?account=invest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == []
    assert len(body["orders"]) == 1
    o = body["orders"][0]
    assert o["order_id"] == "o1"
    assert o["side"] == "sell"  # negative qty
    assert o["name"] == "Apple Inc."
    assert o["limit_price"] == 195.5


def test_pending_orders_per_account_error_surfaced(client, session_factory, monkeypatch):
    _seed_invest(session_factory)
    err = T212AuthError("auth failed")
    err.status_code = 403
    monkeypatch.setattr(orders_api, "build_t212_client", lambda *a, **k: FakeClient(error=err))

    resp = client.get("/api/orders/pending?account=invest")
    assert resp.status_code == 200  # endpoint stays up, error reported inline
    body = resp.json()
    assert body["orders"] == []
    assert body["errors"][0]["code"] == "ip_restricted"


def test_cancel_requires_exec_key(client, session_factory, monkeypatch):
    _seed_invest(session_factory, exec_=False)  # read key only
    monkeypatch.setattr(orders_api, "build_t212_client", lambda *a, **k: FakeClient())

    resp = client.delete("/api/orders/o1?account=invest")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "validation"


def test_cancel_success(client, session_factory, monkeypatch):
    _seed_invest(session_factory)
    fake = FakeClient()
    monkeypatch.setattr(orders_api, "build_t212_client", lambda *a, **k: fake)

    resp = client.delete("/api/orders/o1?account=invest")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert fake.cancelled == ["o1"]


def test_execution_health_shape(client, session_factory, monkeypatch):
    _seed_invest(session_factory)
    resp = client.get("/api/orders/execution-health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["egress_ip"] == "203.0.113.7"
    assert body["accounts"]["invest"]["read_configured"] is True
    assert body["accounts"]["invest"]["exec_configured"] is True
    assert body["accounts"]["invest"]["last_test"]["result"] == "untested"


def test_execution_test_ok_and_persists(client, session_factory, monkeypatch):
    _seed_invest(session_factory)
    monkeypatch.setattr(orders_api, "build_t212_client", lambda *a, **k: FakeClient(summary={"cash": {}}))

    resp = client.post("/api/orders/execution-test", json={"account_kind": "invest"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["test"]["result"] == "ok"
    assert body["egress_ip"] == "203.0.113.7"

    # Health now reflects the persisted last-test result.
    health = client.get("/api/orders/execution-health").json()
    assert health["accounts"]["invest"]["last_test"]["result"] == "ok"


def test_execution_test_ip_restricted(client, session_factory, monkeypatch):
    _seed_invest(session_factory)
    err = T212AuthError("ip blocked")
    err.status_code = 403
    monkeypatch.setattr(orders_api, "build_t212_client", lambda *a, **k: FakeClient(error=err))

    resp = client.post("/api/orders/execution-test", json={"account_kind": "invest"})
    assert resp.status_code == 200
    assert resp.json()["test"]["result"] == "ip_restricted"
