"""The trade execution gate: the agent can only PROPOSE; a human approves; only
execute_intent (with every rail re-checked) places orders. Isolated: in-memory
DB, no live keys/network."""

from pathlib import Path
import ast
import asyncio
import re
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models import entities  # noqa: F401 — registers tables on Base.metadata
from app.models.entities import TradeIntent
from app.services import claude_sdk_config, execution_service, mcp_registry, proposal_service
from app.services.config_store import ConfigStore
from app.services.execution_service import ExecutionError, execute_intent
from app.services.leveraged_service import filter_agent_policy_patch
from app.services.telegram_service import _is_authorized

_ORDER_WRITE = re.compile(r"place_\w*order|cancel_order|modify_order|execute|approve", re.IGNORECASE)


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture()
def db(session_factory):
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def no_db_creds(monkeypatch):
    monkeypatch.setattr(claude_sdk_config, "resolve_t212_env", lambda: {"T212_BASE_ENV": "demo"})


# ── tool exposure ────────────────────────────────────────────────────────


@pytest.mark.parametrize("context", sorted(mcp_registry.ALL_CONTEXTS))
def test_no_context_allows_order_placement_tools(context, no_db_creds):
    _, allowed = mcp_registry.build_mcp_servers(context)
    assert allowed
    bad = [t for t in allowed if _ORDER_WRITE.search(t)]
    assert not bad, f"{context} exposes order-writing tools: {bad}"


def test_registry_declares_no_order_tools():
    for spec in mcp_registry.MCP_SERVERS:
        assert not [t for t in spec.tools if _ORDER_WRITE.search(t)], spec.name


def test_subagents_have_no_order_tools_and_no_execution_agent():
    agents = claude_sdk_config.build_subagents()
    assert "execution" not in agents
    for name, agent in agents.items():
        bad = [t for t in (agent.tools or []) if _ORDER_WRITE.search(t) or t.startswith("mcp__trading212__")]
        assert not bad, f"subagent {name} has {bad}"


def test_t212_mcp_server_defines_no_write_tools():
    src = (mcp_registry.MCP_SERVER_DIR / "t212.py").read_text()
    tools = [
        node.name
        for node in ast.walk(ast.parse(src))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any("tool" in ast.unparse(d) for d in node.decorator_list)
    ]
    assert tools and not [t for t in tools if _ORDER_WRITE.search(t)]
    assert "T212_EXEC" not in src


# ── credentials in subprocess envs ───────────────────────────────────────


def test_sdk_and_mcp_envs_never_carry_exec_keys_or_api_token(monkeypatch, session_factory):
    # Exec keys + API token exported in the backend's own env must be blanked.
    monkeypatch.setenv("T212_EXEC_API_KEY_INVEST", "LEAKED-EXEC")
    monkeypatch.setenv("T212_EXEC_API_SECRET_STOCKS_ISA", "LEAKED-EXEC")
    monkeypatch.setenv("OPENPF_API_TOKEN", "LEAKED-TOKEN")
    monkeypatch.setenv("T212_BASE_ENV", "demo")  # non-secret: passes through

    # Exec keys stored in the DB must not be injected either.
    import app.core.database as database

    with session_factory() as s:
        ConfigStore(s).set_account_credentials(
            "invest",
            {"t212_api_key": "RK", "t212_api_secret": "RS", "exec_api_key": "EK", "exec_api_secret": "ES"},
        )
    monkeypatch.setattr(database, "SessionLocal", session_factory)

    env = mcp_registry.sdk_env()
    assert env["T212_EXEC_API_KEY_INVEST"] == ""
    assert env["T212_EXEC_API_SECRET_STOCKS_ISA"] == ""
    assert env["OPENPF_API_TOKEN"] == ""
    assert "T212_BASE_ENV" not in env

    for context in mcp_registry.ALL_CONTEXTS:
        servers, _ = mcp_registry.build_mcp_servers(context)
        for name, cfg in servers.items():
            values = set(cfg["env"].values())
            assert not values & {"LEAKED-EXEC", "LEAKED-TOKEN", "EK", "ES"}, f"{context}/{name}"
            assert not any(k.startswith("T212_EXEC") and v for k, v in cfg["env"].items())
            if name == "trading212":
                assert cfg["env"]["T212_API_KEY_INVEST"] == "RK"  # read key still delivered
            else:
                assert "T212_API_KEY_INVEST" not in cfg["env"]


# ── execute_intent requires approval ─────────────────────────────────────


def _intent(db, **overrides):
    intent = TradeIntent(
        status="proposed",
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


def test_execute_intent_rejects_proposed(db):
    ConfigStore(db).set_broker({"broker_mode": "paper"})
    intent = _intent(db)
    with pytest.raises(ExecutionError, match="approved"):
        execute_intent(db, intent.id)
    assert db.get(TradeIntent, intent.id).status == "proposed"


def test_execute_with_human_approve_flag_is_audited(db, monkeypatch):
    ConfigStore(db).set_broker({"broker_mode": "paper"})
    monkeypatch.setattr(execution_service, "_paper_fill_price", lambda symbol: 100.0)
    intent = _intent(db)
    result = execute_intent(db, intent.id, approve=True, approved_via="ui")
    assert result.status == "executed"
    events = execution_service.list_events(db)
    approved = [e for e in events if e.message == "intent approved"]
    assert approved and approved[0].payload.get("actor") == "human"


def test_execute_still_enforces_caps_after_approval(db):
    store = ConfigStore(db)
    store.set_broker({"broker_mode": "paper"})
    store.set_risk({**store.get_risk(), "max_single_order_notional": 50.0})
    intent = _intent(db, status="approved", estimated_notional=500.0)
    with pytest.raises(ExecutionError, match="max single-order notional"):
        execute_intent(db, intent.id)


# ── propose_trade ────────────────────────────────────────────────────────


@pytest.fixture()
def fake_market(monkeypatch):
    monkeypatch.setattr(
        proposal_service,
        "_resolve_instrument",
        lambda db, symbol, code: {
            "instrument_code": "AAPL_US_EQ",
            "ticker": "AAPL",
            "currency": "USD",
            "name": "Apple",
            "short_name": "AAPL",
            "verified": True,
        },
    )
    monkeypatch.setattr(proposal_service, "_reference_price", lambda inst: (200.0, "USD"))
    monkeypatch.setattr(proposal_service, "_to_gbp", lambda amount, ccy: amount * 0.5)


def test_propose_trade_links_core_and_drops_unknown_play(db, fake_market):
    core = proposal_service.propose_trade(
        db, side="buy", account_kind="stocks_isa", symbol="AAPL", notional_gbp=50.0, rationale="core top-up", play_id="core",
    )
    assert core["ok"] and core["intent"]["owner"] == "core"
    ghost = proposal_service.propose_trade(
        db, side="buy", account_kind="stocks_isa", symbol="AAPL", notional_gbp=60.0, rationale="x", play_id="nope",
    )
    assert ghost["ok"] and ghost["intent"]["play_id"] is None
    assert any("not found" in w for w in ghost["warnings"])


def test_propose_trade_creates_proposed_intent(db, fake_market):
    from app.models.entities import Play

    db.add(Play(id="play-1", title="AI power"))
    db.commit()
    out = proposal_service.propose_trade(
        db, side="buy", account_kind="stocks_isa", symbol="AAPL", notional_gbp=100.0,
        rationale="test", play_id="play-1", source="chat",
    )
    assert out["ok"], out
    intent = db.get(TradeIntent, out["intent"]["id"])
    assert intent.status == "proposed"
    assert intent.quantity == pytest.approx(1.0)  # £100 / (200 USD * 0.5)
    assert intent.estimated_notional == pytest.approx(100.0)
    assert intent.meta["account_kind"] == "stocks_isa"
    assert intent.meta["play_id"] == "play-1"
    assert intent.meta["source"] == "chat"
    # No cash snapshot → reported, not blocking.
    assert any("insufficient available cash" in w for w in out["warnings"])


def test_propose_trade_reports_cap_violations_without_creating(db, fake_market):
    store = ConfigStore(db)
    store.set_risk({**store.get_risk(), "max_single_order_notional": 50.0})
    out = proposal_service.propose_trade(
        db, side="buy", account_kind="invest", symbol="AAPL", quantity=2, rationale="too big",
    )
    assert out["ok"] is False
    assert any("max single-order notional" in v for v in out["violations"])
    assert out["caps"]["max_single_order_notional_gbp"] == 50.0
    assert db.query(TradeIntent).count() == 0


def test_propose_trade_validates_inputs(db, fake_market):
    base = dict(side="buy", symbol="AAPL", rationale="x")
    assert not proposal_service.propose_trade(db, account_kind="", quantity=1, **base)["ok"]
    assert not proposal_service.propose_trade(db, account_kind="invest", **base)["ok"]  # no size
    assert not proposal_service.propose_trade(
        db, account_kind="invest", quantity=1, order_type="limit", **base
    )["ok"]  # limit without price
    assert db.query(TradeIntent).count() == 0


# ── hooks ────────────────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def _denied(result) -> bool:
    return (result.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "curl -X POST http://localhost:8000/api/agent/intents/abc/execute",
        "curl http://127.0.0.1:8000/api/orders",
        "wget 0.0.0.0:8000/api/leveraged/signals/x/execute",
        "curl https://live.trading212.com/api/v0/equity/orders/market",
        "python -c 'from app.services.execution_service import execute_intent'",
        "sqlite3 ../../backend/mypf.db 'select * from app_config'",
    ],
)
def test_bash_hook_blocks_trading_routes(command):
    assert _denied(_run(claude_sdk_config._bash_guard({"tool_input": {"command": command}}, None, None)))


def test_bash_hook_allows_normal_analysis():
    for command in ["python analysis.py", "python -c 'import app.quant; print(1)'", "ls artifacts"]:
        assert _run(claude_sdk_config._bash_guard({"tool_input": {"command": command}}, None, None)) == {}


@pytest.mark.parametrize(
    "url",
    ["http://localhost:8000/api/agent/intents", "http://[::1]:8000/api/x", "https://demo.trading212.com/api/v0/x"],
)
def test_webfetch_hook_blocks_self_api_and_broker(url):
    assert _denied(_run(claude_sdk_config._web_fetch_guard({"tool_input": {"url": url}}, None, None)))


def test_webfetch_hook_allows_public_sites():
    assert _run(claude_sdk_config._web_fetch_guard({"tool_input": {"url": "https://www.reuters.com/markets"}}, None, None)) == {}


def test_write_hook_blocks_app_code():
    target = str(claude_sdk_config.project_root() / "backend" / "app" / "services" / "execution_service.py")
    assert _denied(_run(claude_sdk_config._file_guard({"tool_name": "Edit", "tool_input": {"file_path": target}}, None, None)))


# ── other agent paths ────────────────────────────────────────────────────


def test_agent_policy_patch_cannot_enable_auto_execute_or_loosen_rails():
    current = {"per_position_notional": 200.0, "max_daily_trades": 3, "auto_execute_enabled": False}
    allowed, rejected = filter_agent_policy_patch(
        current,
        {
            "auto_execute_enabled": True,
            "per_position_notional": 500.0,
            "max_daily_trades": 2,
            "scan_symbols": ["3USL"],
            "account_kind": "invest",
        },
    )
    assert allowed == {"max_daily_trades": 2, "scan_symbols": ["3USL"]}
    assert set(rejected) == {"auto_execute_enabled", "per_position_notional", "account_kind"}


def test_telegram_empty_allowlist_denies_everyone():
    assert _is_authorized(12345, {"allowed_user_ids": []}) is False
    assert _is_authorized(12345, {"allowed_user_ids": [12345]}) is True


# ── standing permissions: only the user grants them ──────────────────────


_PERMISSION_WRITE = re.compile(r"permission|kill_switch|autonomy", re.IGNORECASE)
_PERMISSION_WRITERS = {
    "create_permission", "update_permission", "confirm_live", "revoke_permission", "set_kill_switch",
    "StandingPermission", "try_auto_approve", "process_pending",
}


def test_no_mcp_tool_can_create_or_loosen_permissions():
    # The only permission-related tool is a suggestion, which grants nothing.
    for spec in mcp_registry.MCP_SERVERS:
        tools = [t for t in spec.tools if _PERMISSION_WRITE.search(t)]
        assert set(tools) <= {"suggest_permission"}, f"{spec.name} exposes {tools}"


def test_mcp_servers_never_touch_permission_writers():
    files = sorted(mcp_registry.MCP_SERVER_DIR.glob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        bad = names & _PERMISSION_WRITERS
        assert not bad, f"{path.name} references {bad}"


def test_suggesting_a_permission_grants_nothing(db):
    from app.models.entities import StandingPermission
    from app.services.permissions_service import record_suggestion

    out = record_suggestion(db, kind="play_tranche", per_trade_cap_gbp=10_000, note="trust me")
    assert out["ok"] is False  # no record to support it
    assert db.query(StandingPermission).count() == 0


# ── crypto: same gate, no keys anywhere near the agent ───────────────────


def test_crypto_mcp_server_is_read_only_and_keyless():
    spec = mcp_registry.server_spec("crypto")
    assert not spec.needs_t212_read_keys
    assert not [t for t in spec.tools if _ORDER_WRITE.search(t)]
    src = (mcp_registry.MCP_SERVER_DIR / "crypto.py").read_text()
    for forbidden in ("build_crypto_client", "client_for", "place_market_order", "execute_intent", "api_secret", "ccxt"):
        assert forbidden not in src, forbidden


def test_sdk_and_mcp_envs_never_carry_crypto_keys(monkeypatch, session_factory):
    for name in ("CRYPTO_API_KEY", "CRYPTO_API_SECRET", "KRAKEN_API_KEY", "COINBASE_API_SECRET", "BINANCE_API_KEY"):
        monkeypatch.setenv(name, "LEAKED-CRYPTO")
    monkeypatch.setenv("CRYPTO_EXCHANGE", "kraken")  # non-secret: passes through
    import app.core.database as database

    with session_factory() as s:
        ConfigStore(s).set("crypto_config", {"exchange": "kraken", "api_key": "CK-REAL", "api_secret": "CS-REAL"})
    monkeypatch.setattr(database, "SessionLocal", session_factory)
    monkeypatch.setattr(claude_sdk_config, "resolve_t212_env", lambda: {"T212_BASE_ENV": "demo"})

    env = mcp_registry.sdk_env()
    for name in ("CRYPTO_API_KEY", "CRYPTO_API_SECRET", "KRAKEN_API_KEY", "COINBASE_API_SECRET", "BINANCE_API_KEY"):
        assert env[name] == ""
    assert "CRYPTO_EXCHANGE" not in env
    for context in mcp_registry.ALL_CONTEXTS:
        servers, _ = mcp_registry.build_mcp_servers(context)
        for name, cfg in servers.items():
            assert not set(cfg["env"].values()) & {"LEAKED-CRYPTO", "CK-REAL", "CS-REAL"}, f"{context}/{name}"


def test_agent_process_never_holds_or_uses_exchange_keys(monkeypatch, db):
    from app.services import crypto_service

    ConfigStore(db).set("crypto_config", {
        "exchange": "kraken", "enabled": True, "api_key": "CK-REAL", "api_secret": "CS-REAL",
        "verified": {"can_withdraw": False, "can_view": True, "can_trade": True, "key_fp": crypto_service._fp("CK-REAL")},
    })
    assert crypto_service.is_connected(ConfigStore(db))
    monkeypatch.setenv("OPENPF_MCP_CONTEXT", "chat")
    cfg = crypto_service.get_config(ConfigStore(db))
    assert cfg["api_key"] == "" and cfg["api_secret"] == ""
    assert crypto_service.is_connected(ConfigStore(db))  # knows a verified key exists…
    assert crypto_service.client_for(ConfigStore(db)) is None  # …but can never build a keyed client
    assert "CK-REAL" not in str(crypto_service.public(ConfigStore(db)))


@pytest.mark.parametrize(
    "command",
    [
        "curl -X POST http://localhost:8000/api/permissions -d '{}'",
        "curl -X PUT http://127.0.0.1:8010/api/permissions/kill-switch",
    ],
)
def test_bash_hook_blocks_permission_routes(command):
    assert _denied(_run(claude_sdk_config._bash_guard({"tool_input": {"command": command}}, None, None)))


def test_webfetch_hook_blocks_permission_routes():
    url = "http://localhost:8010/api/permissions"
    assert _denied(_run(claude_sdk_config._web_fetch_guard({"tool_input": {"url": url}}, None, None)))


def test_no_autopilot_path_remains():
    from app.schemas.agent import AgentRunRequest
    from app.services.agent_service import run_agent
    import inspect

    assert "execute_auto" not in AgentRunRequest.model_fields
    assert "execute_auto" not in inspect.signature(run_agent).parameters
    src = Path(inspect.getsourcefile(run_agent)).read_text()
    assert "approve_intent(" not in src and "execute_intent(" not in src


def test_old_autopilot_config_loads_as_off(db):
    from app.models.entities import AppConfig

    db.add(AppConfig(key="broker_config", value={"broker_mode": "paper", "autopilot_enabled": True}))
    db.commit()
    broker = ConfigStore(db).get_broker()
    assert "autopilot_enabled" not in broker
    assert broker["broker_mode"] == "paper"


@pytest.mark.parametrize(
    "command",
    [
        "curl https://api.kraken.com/0/private/AddOrder",
        "python -c 'import ccxt; ccxt.kraken().fetch_balance()'",
        "python -c 'from app.services.crypto_client import build_crypto_client'",
        "curl http://localhost:8000/api/crypto/connection",
    ],
)
def test_bash_hook_blocks_crypto_exchange_access(command):
    assert _denied(_run(claude_sdk_config._bash_guard({"tool_input": {"command": command}}, None, None)))


@pytest.mark.parametrize("url", ["https://api.kraken.com/0/public/Ticker", "https://api.binance.com/api/v3/order"])
def test_webfetch_hook_blocks_crypto_exchange_api(url):
    assert _denied(_run(claude_sdk_config._web_fetch_guard({"tool_input": {"url": url}}, None, None)))


def test_crypto_intent_needs_approval_and_rechecks_cap_at_execution(db):
    from datetime import datetime as _dt

    from app.models.entities import AccountSnapshot
    from app.services import crypto_service

    store = ConfigStore(db)
    store.set_broker({"broker_mode": "paper"})
    store.set("crypto_config", {
        "exchange": "kraken", "enabled": True, "api_key": "CK", "api_secret": "CS",
        "verified": {"can_withdraw": False, "can_view": True, "can_trade": True, "key_fp": crypto_service._fp("CK")},
    })
    now = _dt.utcnow()
    db.add(AccountSnapshot(fetched_at=now, account_kind="invest", currency="GBP", free_cash=1000, invested=9000, total=10000))
    db.add(AccountSnapshot(fetched_at=now, account_kind="crypto", currency="GBP", free_cash=500, invested=0, total=500))
    db.commit()
    intent = _intent(db, symbol="BTC", instrument_code="BTC/GBP", side="buy", estimated_notional=400.0, quantity=0.006,
                     meta={"account_kind": "crypto", "crypto": {"price": 62000.0}})
    with pytest.raises(ExecutionError, match="approved"):
        execute_intent(db, intent.id)
    execution_service.approve_intent(db, intent.id)
    # Cap 2% of a £10.5k book = £210 → the gate refuses the £400 buy even though it's approved.
    store.set_risk({**store.get_risk(), "max_crypto_pct_of_book": 0.02})
    with pytest.raises(ExecutionError, match="crypto-guard"):
        execute_intent(db, intent.id)
    store.set_risk({**store.get_risk(), "max_crypto_pct_of_book": 0.05})
    done = execute_intent(db, intent.id)
    assert done.status == "executed" and done.broker_order_id.startswith("paper-")


def test_crypto_intent_refused_when_key_was_swapped_after_verification(db):
    from app.services import crypto_service

    store = ConfigStore(db)
    store.set_broker({"broker_mode": "live"})
    store.set("crypto_config", {
        "exchange": "kraken", "enabled": True, "api_key": "SWAPPED-KEY", "api_secret": "CS",
        "verified": {"can_withdraw": False, "can_view": True, "can_trade": True, "key_fp": crypto_service._fp("CK")},
    })
    assert not crypto_service.is_connected(store)
    assert crypto_service.client_for(store) is None
    intent = _intent(db, status="approved", symbol="BTC", instrument_code="BTC/GBP", side="sell", quantity=0.001,
                     estimated_notional=60.0, meta={"account_kind": "crypto"})
    with pytest.raises(ExecutionError, match="crypto-guard"):
        execute_intent(db, intent.id)
    assert db.get(TradeIntent, intent.id).status == "approved"  # nothing sent; retryable once re-verified
