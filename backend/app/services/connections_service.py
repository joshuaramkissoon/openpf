"""Connections: every broker, exchange and aggregator account in one list.

A *connection* is one login at one provider (Trading 212, Kraken, IBKR, …). It
holds one or more *accounts*; each account has an ``account_kind`` key that
snapshots, holdings, the book and trade intents use:

* Trading 212 — ``invest`` / ``stocks_isa``. Keys stay where they always were
  (the ``credentials`` row, Settings → Broker & keys); the connection is a view
  over them, so behaviour is unchanged.
* The original crypto connection — ``crypto`` (the ``crypto_config`` row).
* Connections added here — stored in the ``connections`` row with their secrets
  encrypted (``secret_box``): ``crypto:<exchange>``, ``ibkr:<account id>``,
  ``alpaca:<live|paper>``, ``snap:<hash>`` (SnapTrade).

Capabilities: every connection can ``read``. Only Trading 212 and crypto
connections added with a trade key can ``trade``. Everything else is
read-only: Archie can analyse it and suggest trades, but ``execute_intent``
refuses them (``trade_refusal``) and the UI says "Place it in <broker>".

Secrets are never returned by any function here except the private
``_secret`` helper, and never decrypted inside an agent process.
"""

from __future__ import annotations

import hashlib
import logging
import secrets as _secrets
import time
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import AccountSnapshot, PositionSnapshot
from app.services import providers as catalog
from app.services.config_store import ConfigStore
from app.services.crypto_exchanges import CRYPTO_ACCOUNT, EXCHANGES, crypto_account_kind, is_crypto_account

logger = logging.getLogger(__name__)

REGISTRY_KEY = "connections"
STATUS_KEY = "connection_status"
INSTRUMENTS_KEY = "connection_instruments"
T212_ID = "t212"
T212_ACCOUNTS = {"invest": "Invest", "stocks_isa": "Stocks ISA"}


class ConnectionError_(ValueError):
    """A connection change was refused — the message is shown to the user as-is."""


# ── Storage ───────────────────────────────────────────────────────────────

def registry_items(store: ConfigStore) -> list[dict[str, Any]]:
    raw = store.get(REGISTRY_KEY, {"items": []}) or {}
    return [dict(i) for i in (raw.get("items") or []) if isinstance(i, dict) and i.get("id")]


def _save_items(store: ConfigStore, items: list[dict[str, Any]]) -> None:
    store.set(REGISTRY_KEY, {"items": items})


def _get_item(store: ConfigStore, conn_id: str) -> dict[str, Any] | None:
    return next((i for i in registry_items(store) if i["id"] == conn_id), None)


def _upsert(store: ConfigStore, item: dict[str, Any]) -> None:
    items = [i for i in registry_items(store) if i["id"] != item["id"]]
    items.append(item)
    _save_items(store, items)


def _secret(item: dict[str, Any]) -> dict[str, Any]:
    """Decrypted secret fields (backend only; {} in an agent process)."""
    from app.services.secret_box import open_secret

    return open_secret(item.get("secret"))


def _hint(value: str) -> str:
    value = str(value or "")
    return (f"…{value[-4:]}" if len(value) > 8 else "set") if value else ""


def _status_map(store: ConfigStore) -> dict[str, Any]:
    return dict(store.get(STATUS_KEY, {}) or {})


def _set_status(store: ConfigStore, conn_id: str, **fields: Any) -> None:
    current = _status_map(store)
    entry = dict(current.get(conn_id) or {})
    entry.update(fields)
    current[conn_id] = entry
    store.set(STATUS_KEY, current)


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ── Crypto connections (registry side) ────────────────────────────────────

def crypto_config(store: ConfigStore, item: dict[str, Any]) -> dict[str, Any] | None:
    """A registry item as a ``crypto_service`` config (with secrets), or None."""
    from app.core.sandbox import is_sandbox
    from app.services import crypto_service

    exchange = item.get("provider")
    if exchange not in EXCHANGES:
        return None
    cfg: dict[str, Any] = {
        "id": item["id"], "account_kind": crypto_account_kind(exchange), "exchange": exchange,
        "enabled": bool(item.get("enabled", True)), "mode": item.get("mode") or "read",
        "verified": item.get("verified"), "fees": item.get("fees"), "sandbox": is_sandbox(),
    }
    if is_sandbox():
        from app.services.crypto_sandbox import SANDBOX_READ_CREDENTIALS

        # Sandbox: the fixed fake read-only key, never anything stored.
        key = SANDBOX_READ_CREDENTIALS["api_key"]
        cfg.update(api_key=key, api_secret=SANDBOX_READ_CREDENTIALS["api_secret"], mode="read",
                   verified={**(item.get("verified") or {}), "can_withdraw": False, "key_fp": crypto_service._fp(key)})
        return cfg
    if crypto_service.in_agent_process():
        cfg["key_ok"] = bool(item.get("secret")) and bool(item.get("verified")) and \
            (item.get("verified") or {}).get("can_withdraw") is False
        cfg["api_key"] = cfg["api_secret"] = ""
        return cfg
    try:
        sec = _secret(item)
    except Exception:  # noqa: BLE001 — undecryptable: the connection needs reconnecting
        sec = {}
    cfg["api_key"] = str(sec.get("api_key") or "")
    cfg["api_secret"] = str(sec.get("api_secret") or "")
    return cfg


# ── Account directory: labels and capabilities ────────────────────────────

def _t212_configured(store: ConfigStore) -> dict[str, bool]:
    pub = store.credentials_public()
    return {k: bool(pub.get(k, {}).get("configured")) and bool(pub.get(k, {}).get("enabled", True)) for k in T212_ACCOUNTS}


def account_directory(store: ConfigStore) -> dict[str, dict[str, Any]]:
    """``{account_kind: {label, short, provider, broker, tradable, connection_id}}``
    for every account a connection knows about (no secrets)."""
    from app.services import crypto_service

    out: dict[str, dict[str, Any]] = {}
    for kind, name in T212_ACCOUNTS.items():
        out[kind] = {"account_kind": kind, "label": f"Trading 212 {name}", "short": "ISA" if kind == "stocks_isa" else "Invest",
                     "provider": "t212", "broker": "Trading 212", "tradable": True, "read_only": False,
                     "connection_id": T212_ID}
    for cfg in crypto_service.crypto_configs(store):
        ex = EXCHANGES[cfg["exchange"]]
        tradable = crypto_service.tradable(cfg)
        out[cfg["account_kind"]] = {"account_kind": cfg["account_kind"], "label": ex["label"], "short": ex["label"],
                                    "provider": cfg["exchange"], "broker": ex["label"], "tradable": tradable,
                                    "read_only": not tradable, "connection_id": cfg.get("id") or CRYPTO_ACCOUNT}
    for item in registry_items(store):
        spec = catalog.provider(item.get("provider") or "") or {}
        if item.get("provider") in EXCHANGES:
            continue
        for acct in item.get("accounts") or []:
            inst = acct.get("institution") or spec.get("label") or item.get("provider")
            short = _short_label(item.get("provider"), acct, inst)
            out[acct["account_kind"]] = {
                "account_kind": acct["account_kind"], "label": f"{inst} · {acct.get('name') or acct.get('external_id')}",
                "short": short, "provider": item.get("provider"), "broker": inst, "tradable": False, "read_only": True,
                "connection_id": item["id"],
            }
    return out


def _short_label(provider: str | None, acct: dict[str, Any], institution: str) -> str:
    if provider == "ibkr":
        return "IBKR"
    if provider == "alpaca":
        return "Alpaca paper" if acct.get("external_id") == "paper" else "Alpaca"
    return {"Charles Schwab": "Schwab", "Interactive Brokers": "IBKR", "Fidelity Investments": "Fidelity",
            "Vanguard US": "Vanguard"}.get(institution, institution)


def account_info(store: ConfigStore, account_kind: str | None) -> dict[str, Any] | None:
    kind = str(account_kind or "").strip().lower()
    if kind == CRYPTO_ACCOUNT:
        from app.services import crypto_service

        kind = crypto_service.resolve_account(store, kind)
    return account_directory(store).get(kind)


def account_label(store: ConfigStore | None, account_kind: str | None) -> str:
    kind = str(account_kind or "")
    if store is not None:
        try:
            info = account_directory(store).get(kind)
            if info:
                return info["short"]
        except Exception:  # noqa: BLE001
            pass
    return {"stocks_isa": "Stocks ISA", "invest": "Invest", "crypto": "Crypto"}.get(kind, kind or "account")


def trade_refusal(store: ConfigStore, account_kind: str | None) -> str | None:
    """Why OpenPF must not execute in ``account_kind`` (None → it may).

    Read-only connections are refused with "trade this in <broker>"; unknown
    accounts are refused outright."""
    kind = str(account_kind or "").strip().lower()
    if kind in T212_ACCOUNTS:
        return None
    info = account_info(store, kind)
    if info is None:
        if is_crypto_account(kind):
            return None  # crypto with nothing connected: the crypto rail explains it
        return f"invalid account '{account_kind}': no connection holds it"
    if info["tradable"]:
        return None
    return (f"read-only: {info['label']} is connected read-only, so OpenPF can't place this order. "
            f"Place it yourself in {info['broker']}.")


# ── Public view (Settings → Connections) ──────────────────────────────────

def _last_snapshots(db: Session) -> dict[str, datetime]:
    rows = db.execute(select(AccountSnapshot.account_kind, AccountSnapshot.fetched_at)
                      .order_by(desc(AccountSnapshot.fetched_at)).limit(400)).all()
    out: dict[str, datetime] = {}
    for kind, ts in rows:
        out.setdefault(kind, ts)
    return out


def _latest_account_rows(db: Session) -> dict[str, AccountSnapshot]:
    ts = db.execute(select(AccountSnapshot.fetched_at).order_by(desc(AccountSnapshot.fetched_at)).limit(1)).scalar_one_or_none()
    if ts is None:
        return {}
    return {a.account_kind: a for a in db.execute(select(AccountSnapshot).where(AccountSnapshot.fetched_at == ts)).scalars()}


def _account_public(kind: str, name: str, latest: dict[str, AccountSnapshot], last: dict[str, datetime],
                    **extra: Any) -> dict[str, Any]:
    from app.services.fx import get_fx_rate

    row = latest.get(kind)
    value = None
    if row is not None:
        try:
            value = round(max(float(row.total or 0.0), 0.0) * get_fx_rate(row.currency or "GBP", "GBP"), 2)
        except Exception:  # noqa: BLE001
            value = None
    ts = last.get(kind)
    return {"account_kind": kind, "name": name, "value_gbp": value, "currency": row.currency if row is not None else None,
            "last_snapshot": ts.isoformat() + "Z" if ts else None, **extra}


def list_connections(db: Session) -> dict[str, Any]:
    from app.core.sandbox import is_sandbox
    from app.services import crypto_service

    store = ConfigStore(db)
    status = _status_map(store)
    latest = _latest_account_rows(db)
    last = _last_snapshots(db)
    out: list[dict[str, Any]] = []

    # Trading 212: a view over the existing credentials row.
    creds = store.credentials_public()
    t212_accounts = [
        _account_public(kind, name, latest, last, configured=bool(creds.get(kind, {}).get("configured")),
                        enabled=bool(creds.get(kind, {}).get("enabled", True)),
                        trade_key=bool(creds.get(kind, {}).get("exec_configured")))
        for kind, name in T212_ACCOUNTS.items()
    ]
    if any(a["configured"] for a in t212_accounts) or is_sandbox():
        configured = [a for a in t212_accounts if a["configured"]]
        any_trade = any(a["trade_key"] for a in configured)
        out.append({
            "id": T212_ID, "provider": "t212", "type": "broker", "label": "Trading 212",
            "detail": f"{store.get_broker().get('t212_base_env', 'demo')} environment · "
                      f"{'execution key set' if any_trade else 'read key only'}",
            "mode": "trade" if any_trade else "read", "capabilities": ["read", "trade"] if any_trade else ["read"],
            "can_trade_provider": True, "managed_in": "/settings/broker", "enabled": True,
            "status": _status_from_snapshots([a for a in configured], status.get(T212_ID)),
            "accounts": configured, "sandbox": is_sandbox(), "removable": False,
        })

    # Crypto: the original slot + registry exchanges.
    for cfg in crypto_service.crypto_configs(store):
        pub = crypto_service.public_cfg(store, cfg)
        cid = cfg.get("id") or CRYPTO_ACCOUNT
        tradable = crypto_service.tradable(cfg)
        acct = _account_public(cfg["account_kind"], f"{EXCHANGES[cfg['exchange']]['product']}", latest, last,
                               configured=pub["connected"], enabled=pub["enabled"], trade_key=tradable)
        st = dict(status.get(cid) or {})
        if pub["needs_verification"]:
            st.update(state="error", message="Key not verified — reconnect it.")
        out.append({
            "id": cid, "provider": cfg["exchange"], "type": "crypto", "label": pub["label"],
            "detail": f"key {pub['key_hint']}" if pub["key_hint"] else "",
            "mode": "trade" if tradable else "read", "capabilities": ["read", "trade"] if tradable else ["read"],
            "can_trade_provider": True, "enabled": pub["enabled"], "verified": pub["verified"], "fees": pub["fees"],
            "status": _status_from_snapshots([acct], st), "accounts": [acct], "sandbox": pub["sandbox"],
            "removable": not (pub["sandbox"] and cid == CRYPTO_ACCOUNT),
        })

    for item in registry_items(store):
        if item.get("provider") in EXCHANGES:
            continue
        spec = catalog.provider(item["provider"]) or {}
        accts = [_account_public(a["account_kind"], a.get("name") or a.get("external_id"), latest, last,
                                 institution=a.get("institution"), configured=True, enabled=bool(item.get("enabled", True)),
                                 trade_key=False)
                 for a in item.get("accounts") or []]
        st = dict(status.get(item["id"]) or {})
        out.append({
            "id": item["id"], "provider": item["provider"], "type": spec.get("type", "broker"),
            "label": item.get("label") or spec.get("label") or item["provider"],
            "detail": " · ".join(x for x in (item.get("public", {}).get("detail"),) if x),
            "mode": "read", "capabilities": ["read"], "can_trade_provider": False,
            "trade_note": spec.get("trade_note"), "enabled": bool(item.get("enabled", True)),
            "status": _status_from_snapshots(accts, st), "accounts": accts, "sandbox": is_sandbox(), "removable": True,
            "link_url": item.get("public", {}).get("link_url"),
        })
    return {"connections": out, "sandbox": is_sandbox()}


def _status_from_snapshots(accounts: list[dict[str, Any]], st: dict[str, Any] | None) -> dict[str, Any]:
    st = dict(st or {})
    snaps = [a["last_snapshot"] for a in accounts if a.get("last_snapshot")]
    last_sync = st.get("last_sync") or (max(snaps) if snaps else None)
    state = st.get("state") or ("ok" if snaps else "pending")
    return {"state": state, "message": st.get("message") or "", "last_sync": last_sync,
            "last_attempt": st.get("last_attempt")}


# ── Adding / changing connections ─────────────────────────────────────────

def _clean_fields(provider_id: str, fields: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    spec = catalog.provider(provider_id) or {}
    secret, settings = {}, {}
    for f in spec.get("fields", []):
        raw = str((fields or {}).get(f["key"]) or "").strip()
        if f.get("options"):
            allowed = [o[0] for o in f["options"]]
            raw = raw if raw in allowed else allowed[0]
        if f.get("secret"):
            secret[f["key"]] = raw
        else:
            settings[f["key"]] = raw
    return secret, settings


def _new_id(provider_id: str) -> str:
    return f"{provider_id}-{_secrets.token_hex(3)}"


def add_connection(db: Session, provider_id: str, fields: dict[str, Any], *, mode: str = "read",
                   conn_id: str | None = None) -> dict[str, Any]:
    """Verify credentials with the provider and save the connection.

    Crypto: the key is checked (withdrawal always refused; ``trade`` mode needs a
    key that can trade). Brokers/aggregators: one read-only fetch proves the
    credentials and discovers the accounts. ``conn_id`` reconnects an existing
    connection in place (new keys, same accounts)."""
    from app.core.sandbox import is_sandbox
    from app.services import crypto_service
    from app.services.secret_box import seal

    provider_id = (provider_id or "").strip().lower()
    spec = catalog.provider(provider_id)
    if spec is None or provider_id == "t212":
        raise ConnectionError_("Pick a provider from the list." if spec is None else
                               "Trading 212 keys are managed under Settings → Broker & keys.")
    mode = (mode or "read").strip().lower()
    if mode not in spec["modes"]:
        raise ConnectionError_(f"OpenPF can't trade on {spec['label']} — connect it read-only.")
    store = ConfigStore(db)
    existing = _get_item(store, conn_id) if conn_id else None
    if conn_id and existing is None and conn_id != CRYPTO_ACCOUNT:
        raise ConnectionError_("That connection no longer exists.")
    secret, settings = _clean_fields(provider_id, fields)

    if provider_id in EXCHANGES:
        if conn_id == CRYPTO_ACCOUNT:
            try:
                crypto_service.connect(store, provider_id, secret.get("api_key", ""), secret.get("api_secret", ""), mode)
            except crypto_service.CryptoConnectError as exc:
                raise ConnectionError_(str(exc)) from exc
            _set_status(store, CRYPTO_ACCOUNT, state="pending", message="", last_attempt=_now_iso())
            return {"id": CRYPTO_ACCOUNT}
        for cfg in crypto_service.crypto_configs(store):
            if cfg["exchange"] == provider_id and cfg.get("id") != conn_id:
                raise ConnectionError_(f"{spec['label']} is already connected — use Reconnect on it to change the key.")
        if is_sandbox() and mode == "trade":
            raise ConnectionError_("In the sandbox only the original mock Kraken account trades; connect this one read-only.")
        try:
            key, sec, perms, fees = crypto_service.verify_key(provider_id, secret.get("api_key", ""),
                                                              secret.get("api_secret", ""), mode)
        except crypto_service.CryptoConnectError as exc:
            raise ConnectionError_(str(exc)) from exc
        item = {
            **(existing or {}), "id": conn_id or _new_id(provider_id), "provider": provider_id, "label": spec["label"],
            "mode": mode, "enabled": True, "created_at": (existing or {}).get("created_at") or _now_iso(),
            "secret": seal({"api_key": key, "api_secret": sec}), "verified": crypto_service.verified_record(key, perms),
            "fees": fees, "public": {"key_hint": _hint(key), "detail": f"key {_hint(key)}"},
            "accounts": [{"account_kind": crypto_account_kind(provider_id), "external_id": provider_id,
                          "name": spec["product"]}],
        }
        _upsert(store, item)
        _set_status(store, item["id"], state="pending", message="", last_attempt=_now_iso())
        return {"id": item["id"]}

    # Brokers and aggregators: read-only.
    from app.services.provider_clients import ProviderAuthError, ProviderError, build_provider_client

    if provider_id == "snaptrade":
        return _add_snaptrade(db, store, spec, secret, existing)
    missing = [f["label"] for f in spec["fields"] if f.get("secret") and not secret.get(f["key"])]
    missing += [f["label"] for f in spec["fields"] if not f.get("secret") and not f.get("options") and not settings.get(f["key"])]
    if missing:
        raise ConnectionError_(f"Fill in: {', '.join(missing)}.")
    try:
        client = build_provider_client(provider_id, secret, settings)
        accounts = client.fetch()
    except ProviderAuthError as exc:
        raise ConnectionError_(f"{spec['label']} rejected those credentials: {exc}") from exc
    except ProviderError as exc:
        raise ConnectionError_(f"Couldn't read from {spec['label']}: {exc}") from exc
    if not accounts:
        raise ConnectionError_(f"{spec['label']} returned no accounts for those credentials.")
    item = {
        **(existing or {}), "id": conn_id or _new_id(provider_id), "provider": provider_id, "label": spec["label"],
        "mode": "read", "enabled": True, "created_at": (existing or {}).get("created_at") or _now_iso(),
        "secret": seal(secret), "settings": settings,
        "public": {"detail": _detail(provider_id, secret, settings)},
    }
    item["accounts"] = _account_records(provider_id, accounts)
    _check_account_clash(store, item)
    _upsert(store, item)
    _remember(store, item, accounts)
    return {"id": item["id"]}


def _detail(provider_id: str, secret: dict[str, str], settings: dict[str, str]) -> str:
    if provider_id == "ibkr":
        return f"Flex query {settings.get('query_id')} · token {_hint(secret.get('token', ''))}"
    if provider_id == "alpaca":
        return f"{settings.get('environment', 'live')} · key {_hint(secret.get('key_id', ''))}"
    return ""


def _account_kind_for(provider_id: str, external_id: str) -> str:
    ext = str(external_id or "").strip()
    if provider_id == "ibkr":
        return f"ibkr:{ext.lower()}"[:24]
    if provider_id == "alpaca":
        return f"alpaca:{ext.lower()}"[:24]
    return f"snap:{hashlib.sha256(ext.encode()).hexdigest()[:10]}"


def _account_records(provider_id: str, accounts: list[Any]) -> list[dict[str, Any]]:
    return [{"account_kind": _account_kind_for(provider_id, a.external_id), "external_id": a.external_id,
             "name": a.name, "currency": a.currency, "institution": a.institution} for a in accounts]


def _check_account_clash(store: ConfigStore, item: dict[str, Any]) -> None:
    mine = {a["account_kind"] for a in item.get("accounts") or []}
    for other in registry_items(store):
        if other["id"] == item["id"]:
            continue
        clash = mine & {a["account_kind"] for a in other.get("accounts") or []}
        if clash:
            raise ConnectionError_("That account is already connected — use Reconnect on the existing connection.")


def _add_snaptrade(db: Session, store: ConfigStore, spec: dict[str, Any], secret: dict[str, str],
                   existing: dict[str, Any] | None) -> dict[str, Any]:
    """Register a SnapTrade user (once) and return the connection portal link.
    Accounts appear after the user links a broker there and presses Sync."""
    from app.core.sandbox import is_sandbox
    from app.services.provider_clients import (
        ProviderAuthError,
        ProviderError,
        SnapTradeClient,
        build_provider_client,
    )
    from app.services.secret_box import seal

    if not secret.get("client_id") or not secret.get("consumer_key"):
        raise ConnectionError_("Paste both the SnapTrade Client ID and Consumer Key.")
    prev = _secret(existing) if existing else {}
    if prev.get("client_id") == secret["client_id"] and prev.get("user_secret"):
        secret = {**secret, "user_id": prev.get("user_id", ""), "user_secret": prev["user_secret"]}
    conn_id = (existing or {}).get("id") or _new_id("snaptrade")
    link = None
    try:
        if is_sandbox():
            client = build_provider_client("snaptrade", secret)
            accounts = client.fetch()  # fixtures: already "linked"
            secret = {**secret, "user_id": "openpf-sandbox", "user_secret": "sandbox"}
        else:
            client = SnapTradeClient(secret["client_id"], secret["consumer_key"], secret.get("user_id", ""),
                                     secret.get("user_secret", ""))
            if not secret.get("user_secret"):
                user_id = f"openpf-{_secrets.token_hex(8)}"
                client.register_user(user_id)
                secret = {**secret, "user_id": client.user_id, "user_secret": client.user_secret}
            link = client.login_link()
            try:
                accounts = client.fetch()
            except ProviderError:
                accounts = []
    except ProviderAuthError as exc:
        raise ConnectionError_(f"SnapTrade rejected those keys: {exc}") from exc
    except ProviderError as exc:
        raise ConnectionError_(f"Couldn't reach SnapTrade: {exc}") from exc
    item = {
        **(existing or {}), "id": conn_id, "provider": "snaptrade", "label": spec["label"], "mode": "read",
        "enabled": True, "created_at": (existing or {}).get("created_at") or _now_iso(), "secret": seal(secret),
        "settings": {}, "public": {"detail": f"client {_hint(secret['client_id'])}", "link_url": link},
        "accounts": _account_records("snaptrade", accounts),
    }
    _check_account_clash(store, item)
    _upsert(store, item)
    if accounts:
        _remember(store, item, accounts)
    else:
        _set_status(store, conn_id, state="pending", message="Link a broker on SnapTrade's page, then press Sync.",
                    last_attempt=_now_iso())
    return {"id": conn_id, "link_url": link}


def snaptrade_link(db: Session, conn_id: str) -> str:
    """A fresh SnapTrade connection-portal link (links expire after a few minutes)."""
    from app.core.sandbox import is_sandbox
    from app.services.provider_clients import ProviderError, SnapTradeClient

    store = ConfigStore(db)
    item = _get_item(store, conn_id)
    if item is None or item.get("provider") != "snaptrade":
        raise ConnectionError_("That isn't a SnapTrade connection.")
    if is_sandbox():
        raise ConnectionError_("The sandbox's SnapTrade accounts are fixtures — there's nothing to link.")
    sec = _secret(item)
    try:
        return SnapTradeClient(sec.get("client_id", ""), sec.get("consumer_key", ""), sec.get("user_id", ""),
                               sec.get("user_secret", "")).login_link()
    except ProviderError as exc:
        raise ConnectionError_(f"Couldn't get a SnapTrade link: {exc}") from exc


def remove_connection(db: Session, conn_id: str) -> None:
    """Delete a connection and its stored keys. Its past snapshots stay in the
    history; it simply stops appearing in new ones."""
    from app.core.sandbox import is_sandbox
    from app.services import crypto_service

    store = ConfigStore(db)
    if conn_id == T212_ID:
        raise ConnectionError_("Trading 212 keys are managed under Settings → Broker & keys.")
    if conn_id == CRYPTO_ACCOUNT:
        if is_sandbox():
            raise ConnectionError_("The sandbox's mock Kraken account can't be removed.")
        crypto_service.disconnect(store)
    else:
        if _get_item(store, conn_id) is None:
            raise ConnectionError_("That connection no longer exists.")
        _save_items(store, [i for i in registry_items(store) if i["id"] != conn_id])
    st = _status_map(store)
    st.pop(conn_id, None)
    store.set(STATUS_KEY, st)


def set_enabled(db: Session, conn_id: str, enabled: bool) -> None:
    from app.services import crypto_service

    store = ConfigStore(db)
    if conn_id == CRYPTO_ACCOUNT:
        crypto_service.set_enabled(store, enabled)
        return
    item = _get_item(store, conn_id)
    if item is None:
        raise ConnectionError_("That connection no longer exists.")
    item["enabled"] = bool(enabled)
    _upsert(store, item)


def request_sync(db: Session, conn_id: str | None = None) -> None:
    """Make the next refresh fetch ``conn_id`` (or everything) now, ignoring the
    per-provider sync interval."""
    store = ConfigStore(db)
    st = _status_map(store)
    for cid in ([conn_id] if conn_id else list(st)):
        if cid in st:
            st[cid] = {**st[cid], "next_sync_after": 0}
    store.set(STATUS_KEY, st)


# ── Snapshots ─────────────────────────────────────────────────────────────

def _remember(store: ConfigStore, item: dict[str, Any], accounts: list[Any]) -> None:
    """Keep display names / market tickers for this connection's holdings."""
    inst = dict(store.get(INSTRUMENTS_KEY, {}) or {})
    for rec, acct in zip(_account_records(item["provider"], accounts), accounts):
        inst[rec["account_kind"]] = {p.symbol.upper(): {"name": p.name, "yf": p.yfinance, "cls": p.asset_class,
                                                        "isin": p.isin} for p in acct.positions}
    store.set(INSTRUMENTS_KEY, inst)


def _external_rows(acct: Any, account_kind: str, fetched_at: datetime) -> tuple[AccountSnapshot, list[PositionSnapshot]]:
    """Provider account → snapshot rows. Money fields (value/ppl/cost) are in
    the ACCOUNT currency, prices in the instrument currency (same convention as
    the Trading 212 rows)."""
    from app.services.fx import get_fx_rate

    base = (acct.currency or "USD").upper()
    rows: list[PositionSnapshot] = []
    invested = ppl = 0.0
    for p in acct.positions:
        ccy = (p.currency or base).upper()
        rate = 1.0 if ccy == base else get_fx_rate(ccy, base)
        value = p.quantity * p.price * rate
        avg = p.avg_cost or p.price
        cost = p.quantity * avg * rate
        invested += value
        ppl += value - cost
        rows.append(PositionSnapshot(
            fetched_at=fetched_at, account_kind=account_kind, ticker=p.symbol.upper()[:64],
            instrument_code=(p.yfinance or p.symbol).upper()[:64], quantity=p.quantity, average_price=avg,
            current_price=p.price, total_cost=cost, value=value, ppl=value - cost, currency=ccy,
        ))
    account = AccountSnapshot(fetched_at=fetched_at, account_kind=account_kind, currency=base, free_cash=float(acct.cash),
                              invested=invested, pie_cash=0.0, total=invested + float(acct.cash), ppl=ppl)
    return account, rows


def _carry_forward(db: Session, account_kind: str, fetched_at: datetime) -> tuple[list[AccountSnapshot], list[PositionSnapshot]]:
    """Copy the latest stored rows of ``account_kind`` to ``fetched_at`` — so a
    provider that wasn't fetched this time (throttled or briefly down) doesn't
    vanish from the book."""
    last = db.execute(select(AccountSnapshot).where(AccountSnapshot.account_kind == account_kind)
                      .order_by(desc(AccountSnapshot.fetched_at)).limit(1)).scalars().first()
    if last is None:
        return [], []
    acct = AccountSnapshot(fetched_at=fetched_at, account_kind=account_kind, currency=last.currency,
                           free_cash=last.free_cash, invested=last.invested, pie_cash=last.pie_cash, total=last.total,
                           ppl=last.ppl)
    positions = [PositionSnapshot(fetched_at=fetched_at, account_kind=account_kind, ticker=p.ticker,
                                  instrument_code=p.instrument_code, quantity=p.quantity, average_price=p.average_price,
                                  current_price=p.current_price, total_cost=p.total_cost, value=p.value, ppl=p.ppl,
                                  currency=p.currency)
                 for p in db.execute(select(PositionSnapshot).where(PositionSnapshot.account_kind == account_kind,
                                                                    PositionSnapshot.fetched_at == last.fetched_at)).scalars()]
    return [acct], positions


def snapshot_rows(db: Session, store: ConfigStore, fetched_at: datetime) -> tuple[list[AccountSnapshot], list[PositionSnapshot], list[str]]:
    """Rows for every non-Trading-212 connection for one portfolio refresh.

    Best-effort per connection: a failure (or a provider inside its sync
    interval, e.g. IBKR's daily Flex report) carries the last good rows
    forward and records the error on the connection's status."""
    from app.services import crypto_service
    from app.services.provider_clients import ProviderAuthError, ProviderError, ProviderPending, build_provider_client

    accounts: list[AccountSnapshot] = []
    positions: list[PositionSnapshot] = []
    sources: list[str] = []
    status = _status_map(store)
    changed: dict[str, dict[str, Any]] = {}

    # Crypto exchanges.
    for cfg in crypto_service.crypto_configs(store):
        if not crypto_service._connected_cfg(cfg):
            continue
        cid = cfg.get("id") or CRYPTO_ACCOUNT
        client = crypto_service._client_for_cfg(cfg)
        a, p, src = ([], [], None) if client is None else crypto_service._exchange_rows(db, client, cfg["account_kind"], fetched_at)
        if a:
            changed[cid] = {"state": "ok", "message": "", "last_sync": _now_iso(), "last_attempt": _now_iso()}
        else:
            a, p = _carry_forward(db, cfg["account_kind"], fetched_at)
            changed[cid] = {"state": "error", "message": (src or "unreachable").split(":")[-1], "last_attempt": _now_iso()}
        accounts.extend(a)
        positions.extend(p)
        if src:
            sources.append(src)

    # Brokers and aggregators.
    now = time.time()
    for item in registry_items(store):
        if item.get("provider") in EXCHANGES or not item.get("enabled", True):
            continue
        spec = catalog.provider(item["provider"]) or {}
        st = dict(status.get(item["id"]) or {})
        kinds = [a["account_kind"] for a in item.get("accounts") or []]
        due = now >= float(st.get("next_sync_after") or 0)
        fetched = None
        if due:
            try:
                client = build_provider_client(item["provider"], _secret(item), item.get("settings") or {})
                fetched = client.fetch()
                records = _account_records(item["provider"], fetched)
                if [r["account_kind"] for r in records] != kinds:
                    item["accounts"] = records
                    kinds = [r["account_kind"] for r in records]
                    _upsert(store, item)
                _remember(store, item, fetched)
                changed[item["id"]] = {"state": "ok", "message": "", "last_sync": _now_iso(), "last_attempt": _now_iso(),
                                       "next_sync_after": now + 60 * float(spec.get("sync_minutes") or 0)}
                sources.append(f"{item['provider']}:{len(fetched)}")
            except ProviderPending as exc:
                changed[item["id"]] = {**st, "state": st.get("state") or "pending", "message": str(exc),
                                       "last_attempt": _now_iso(), "next_sync_after": now + 120}
            except ProviderAuthError as exc:
                changed[item["id"]] = {**st, "state": "error", "message": f"{exc} — reconnect it.",
                                       "last_attempt": _now_iso(), "next_sync_after": now + 900}
            except Exception as exc:  # noqa: BLE001 — one provider never breaks the refresh
                logger.warning("connection %s sync failed: %s", item["id"], exc)
                changed[item["id"]] = {**st, "state": "error", "message": str(exc)[:300], "last_attempt": _now_iso(),
                                       "next_sync_after": now + 300}
                sources.append(f"error:{item['provider']}:{exc.__class__.__name__}")
        by_kind = {r["account_kind"]: acct for r, acct in zip(_account_records(item["provider"], fetched or []), fetched or [])}
        for kind in kinds:
            if kind in by_kind:
                a_row, p_rows = _external_rows(by_kind[kind], kind, fetched_at)
                _maybe_opening_balance(db, a_row, fetched_at)
                accounts.append(a_row)
                positions.extend(p_rows)
            else:
                a, p = _carry_forward(db, kind, fetched_at)
                accounts.extend(a)
                positions.extend(p)

    if changed:
        status = _status_map(store)
        for cid, entry in changed.items():
            status[cid] = {**(status.get(cid) or {}), **entry}
        store.set(STATUS_KEY, status)
    return accounts, positions, sources


def _maybe_opening_balance(db: Session, account: AccountSnapshot, when: datetime) -> None:
    """Connecting an account isn't a gain: its first value is an opening contribution."""
    from app.services.crypto_service import record_opening_balance

    first = db.execute(select(AccountSnapshot.id).where(AccountSnapshot.account_kind == account.account_kind).limit(1)).first()
    if first is None:
        try:
            record_opening_balance(db, float(account.total or 0.0), account.currency or "USD", when,
                                   account_kind=account.account_kind)
        except Exception:  # noqa: BLE001
            logger.info("opening balance not recorded for %s", account.account_kind, exc_info=True)


def position_meta(store: ConfigStore | None, account_kind: str, ticker: str, instrument_code: str | None,
                  currency: str | None, _cache: dict[str, Any] | None = None) -> dict[str, Any]:
    """Display/market-data identity for a holding from a broker/aggregator connection."""
    inst = _cache if _cache is not None else (dict(store.get(INSTRUMENTS_KEY, {}) or {}) if store else {})
    meta = (inst.get(account_kind) or {}).get((ticker or "").upper()) or {}
    yf = meta.get("yf") or instrument_code or ticker
    return {"name": meta.get("name") or ticker, "display_ticker": ticker, "instrument_currency": currency,
            "yfinance_ticker": yf}


def instruments_cache(store: ConfigStore) -> dict[str, Any]:
    return dict(store.get(INSTRUMENTS_KEY, {}) or {})


def is_external_account(account_kind: str | None) -> bool:
    """Accounts from broker/aggregator connections (not T212, not crypto)."""
    k = str(account_kind or "").strip().lower()
    return bool(k) and k not in T212_ACCOUNTS and not is_crypto_account(k)
