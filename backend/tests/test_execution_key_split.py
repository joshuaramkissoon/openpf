"""Unit tests for the T212 read/execute key split, error taxonomy, and explicit
account selection on intent execution. Isolated: in-memory DB, no live keys/network."""

from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models import entities  # noqa: F401 — registers tables on Base.metadata
from app.services import execution_service
from app.services.config_store import ConfigStore
from app.services.execution_service import ExecutionError, execute_intent
from app.services.t212_client import (
    T212AuthError,
    T212Error,
    T212RateLimitError,
    build_t212_client,
)
from app.services import t212_errors
from app.services.t212_errors import classify_t212_error


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ── credential model ────────────────────────────────────────────────────


def test_exec_key_stored_and_exposed_separately(db):
    store = ConfigStore(db)
    store.set_account_credentials(
        "invest",
        {"t212_api_key": "READK", "t212_api_secret": "READS", "exec_api_key": "EXECK", "exec_api_secret": "EXECS"},
    )

    read = store.get_account_credentials("invest")
    assert read["t212_api_key"] == "READK"
    exec_creds = store.get_account_exec_credentials("invest")
    assert exec_creds["t212_api_key"] == "EXECK"
    assert exec_creds["t212_api_secret"] == "EXECS"
    assert exec_creds["exec_enabled"] is True

    public = store.credentials_public()["invest"]
    assert public["configured"] is True
    assert public["exec_configured"] is True
    assert public["exec_enabled"] is True
    # Secrets never leak into the public projection.
    assert "exec_api_key" not in public


def test_partial_update_preserves_other_key_and_toggle(db):
    store = ConfigStore(db)
    store.set_account_credentials(
        "invest",
        {"t212_api_key": "RK", "t212_api_secret": "RS", "exec_api_key": "EK", "exec_api_secret": "ES"},
    )

    # Update ONLY the read key — exec key + exec_enabled must survive.
    store.set_account_credentials("invest", {"t212_api_key": "RK2", "t212_api_secret": "RS2"})
    assert store.get_account_credentials("invest")["t212_api_key"] == "RK2"
    assert store.get_account_exec_credentials("invest")["t212_api_key"] == "EK"
    assert store.get_account_exec_credentials("invest")["exec_enabled"] is True

    # Toggle exec off without resending keys — keys survive.
    store.set_account_credentials("invest", {"exec_enabled": False})
    assert store.get_account_exec_credentials("invest")["t212_api_key"] == "EK"
    assert store.get_account_exec_credentials("invest")["exec_enabled"] is False


def test_exec_key_accepts_colon_paste_format(db):
    store = ConfigStore(db)
    store.set_account_credentials("stocks_isa", {"exec_api_key": "KEYPART:SECRETPART"})
    creds = store.get_account_exec_credentials("stocks_isa")
    assert creds["t212_api_key"] == "KEYPART"
    assert creds["t212_api_secret"] == "SECRETPART"


# ── client routing + write guard ─────────────────────────────────────────


def test_build_client_routes_read_vs_exec_key(db):
    store = ConfigStore(db)
    store.set_account_credentials(
        "invest",
        {"t212_api_key": "RK", "t212_api_secret": "RS", "exec_api_key": "EK", "exec_api_secret": "ES"},
    )

    read_client = build_t212_client(store, account_kind="invest")
    assert read_client.purpose == "read"
    assert read_client.api_key == "RK"

    exec_client = build_t212_client(store, account_kind="invest", purpose="execute")
    assert exec_client.purpose == "execute"
    assert exec_client.api_key == "EK"


def test_read_client_cannot_place_orders():
    from app.services.t212_client import T212Client

    client = T212Client(api_key="RK", api_secret="RS", base_env="demo", purpose="read")
    with pytest.raises(T212Error):
        client.place_market_order("AAPL_US_EQ", 1)
    with pytest.raises(T212Error):
        client.cancel_order("order-123")


# ── error taxonomy ───────────────────────────────────────────────────────


def _auth_error(status):
    err = T212AuthError("Trading 212 auth failed")
    err.status_code = status
    return err


def test_classifier_maps_ip_restriction():
    c = classify_t212_error(_auth_error(403), account_kind="invest")
    assert c.code == t212_errors.CODE_IP_RESTRICTED
    assert c.meta.get("account_kind") == "invest"


def test_classifier_maps_auth_failure():
    assert classify_t212_error(_auth_error(401)).code == t212_errors.CODE_AUTH_FAILED


def test_classifier_maps_rate_limit():
    assert classify_t212_error(T212RateLimitError("429")).code == t212_errors.CODE_RATE_LIMITED


def test_classifier_insufficient_funds_beats_risk_guard():
    # Our cash guard message contains both "risk-guard" and "insufficient".
    c = classify_t212_error(ExecutionError("risk-guard: insufficient available cash"), account_kind="invest")
    assert c.code == t212_errors.CODE_INSUFFICIENT_FUNDS
    assert "invest" in c.message


def test_classifier_maps_risk_blocks():
    assert classify_t212_error(ExecutionError("risk-guard: order exceeds max single-order notional")).code == t212_errors.CODE_RISK_BLOCKED
    assert classify_t212_error(ExecutionError("duplicate-order-guard: similar order")).code == t212_errors.CODE_RISK_BLOCKED


def test_classifier_maps_config_guard_to_validation():
    c = classify_t212_error(ExecutionError("no execution key configured for invest (add it in Settings)"))
    assert c.code == t212_errors.CODE_VALIDATION


def test_classifier_generic_broker_error():
    err = T212Error("Trading 212 API error 500: upstream boom")
    err.status_code = 500
    assert classify_t212_error(err).code == t212_errors.CODE_BROKER_ERROR


def test_classified_status_codes():
    assert classify_t212_error(_auth_error(403)).status_code == 502
    assert classify_t212_error(ExecutionError("risk-guard: insufficient available cash")).status_code == 400


# ── explicit account selection on execute_intent (paper mode) ─────────────


def _make_intent(db, **overrides):
    intent = entities.TradeIntent(
        status="approved",
        symbol="AAPL",
        instrument_code="AAPL_US_EQ",
        side="sell",  # sell skips the buy-side cash guard
        order_type="market",
        quantity=1.0,
        estimated_notional=10.0,
        meta={},
    )
    for k, v in overrides.items():
        setattr(intent, k, v)
    db.add(intent)
    db.commit()
    db.refresh(intent)
    return intent


def test_execute_intent_records_explicit_account(db, monkeypatch):
    ConfigStore(db).set_broker({"broker_mode": "paper"})  # exercise the paper path explicitly
    monkeypatch.setattr(execution_service, "_paper_fill_price", lambda symbol: 123.0)
    intent = _make_intent(db)
    result = execute_intent(db, intent.id, account_kind="stocks_isa")
    assert result.status == "executed"
    assert result.broker_mode == "paper"
    assert (result.meta or {}).get("account_kind") == "stocks_isa"


def test_execute_intent_uses_intent_meta_account_when_not_overridden(db, monkeypatch):
    ConfigStore(db).set_broker({"broker_mode": "paper"})
    monkeypatch.setattr(execution_service, "_paper_fill_price", lambda symbol: 50.0)
    intent = _make_intent(db, meta={"account_kind": "stocks_isa"})
    result = execute_intent(db, intent.id)
    assert (result.meta or {}).get("account_kind") == "stocks_isa"


def test_execute_intent_rejects_invalid_account(db):
    intent = _make_intent(db)
    with pytest.raises(ExecutionError):
        execute_intent(db, intent.id, account_kind="margin")
