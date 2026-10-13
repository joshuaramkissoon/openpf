"""The provider catalog for Settings → Connections (static, no I/O).

Every provider says what OpenPF can do with it (``modes``: ``read`` and, where
OpenPF can place orders, ``trade``), which credential fields the user pastes,
and the exact steps to create a READ-ONLY key. Trading is added later, per
connection, only where OpenPF can trade (Trading 212 and the crypto exchanges).

Facts are deliberately conservative; see docs/connections.md for sources.
"""

from __future__ import annotations

from typing import Any

from app.services.crypto_exchanges import EXCHANGES

GROUPS = [("broker", "Brokers"), ("crypto", "Crypto exchanges"), ("aggregator", "Aggregators")]

_SECRET = True

PROVIDERS: dict[str, dict[str, Any]] = {
    "t212": {
        "id": "t212", "type": "broker", "label": "Trading 212", "product": "Invest and Stocks ISA",
        "modes": ["read", "trade"], "managed_in": "/settings/broker",
        "availability": "UK and EU. Official public API (beta) for Invest and Stocks ISA accounts.",
        "summary": "Your Invest and Stocks ISA accounts. Keys are managed under Broker & keys.",
        "read_steps": [
            "Trading 212 app → Settings → API (Beta) → Generate API key.",
            "Read-only: tick the Account data, Portfolio, History and Metadata permissions only. Leave Orders "
            "execute UNTICKED.",
            "Paste the key and secret under Settings → Broker & keys → Read key, for each account (Invest, Stocks ISA).",
        ],
        "trade_steps": [
            "Create a second key with Orders execute ticked and restrict it to this machine's IP address.",
            "Paste it as the Execution key. OpenPF only ever uses it for orders you approve.",
        ],
        "fields": [],
    },
    "ibkr": {
        "id": "ibkr", "type": "broker", "label": "Interactive Brokers", "product": "Flex Web Service",
        "modes": ["read"],
        "availability": "Worldwide, including IBKR UK. Any IBKR account can enable the Flex Web Service.",
        "summary": "Positions and cash from a daily Flex report. The Flex service can only return reports, so "
                   "this connection can never trade.",
        "trade_note": "OpenPF can't place IBKR orders. Archie can still suggest trades; you place them in IBKR.",
        "key_url": "https://www.interactivebrokers.com/sso/Login?RL=1&action=CS_FLEX_WEB_SERVICE",
        "read_steps": [
            "IBKR Portal → Performance & Reports → Flex Queries → Activity Flex Query → Create (+).",
            "Name it \"OpenPF\". Sections: tick Open Positions (Options: Summary) and Cash Report, plus Account "
            "Information. Leave the default fields selected.",
            "Delivery configuration: Format XML, Period \"Last Business Day\". Save, then note the Query ID shown "
            "next to the query.",
            "Flex Queries page → Flex Web Service Configuration (gear icon) → enable it and Generate a token. Pick "
            "an expiry (up to a year) and, if you like, restrict it to this machine's IP.",
            "Paste the token and the Query ID below.",
        ],
        "fields": [
            {"key": "token", "label": "Flex Web Service token", "secret": _SECRET, "placeholder": "A long number"},
            {"key": "query_id", "label": "Flex Query ID", "secret": False, "placeholder": "e.g. 912345"},
        ],
        "sync_minutes": 60,
        "hosts": ["ndcdyn.interactivebrokers.com", "gdcdyn.interactivebrokers.com"],
    },
    "alpaca": {
        "id": "alpaca", "type": "broker", "label": "Alpaca", "product": "Trading API",
        "modes": ["read"],
        "availability": "US, and individual accounts in many other countries (check Alpaca's supported list; "
                        "not open to UK residents for live trading at the time of writing). Paper accounts are open "
                        "to anyone.",
        "summary": "Account cash and positions through Alpaca's Trading API (read endpoints only).",
        "trade_note": "Alpaca keys aren't scoped: the key could trade, but OpenPF only calls read endpoints and "
                      "refuses Alpaca orders. Place Alpaca trades in Alpaca.",
        "key_url": "https://app.alpaca.markets/",
        "read_steps": [
            "Alpaca dashboard → pick Live or Paper (top left) → Home → API Keys → Generate New Keys.",
            "Copy the Key ID and the Secret Key (the secret is shown once).",
            "Alpaca keys can't be limited to read-only. OpenPF only calls GET /v2/account and /v2/positions with it, "
            "and the agent never sees it. Alpaca's Trading API can't withdraw funds.",
        ],
        "fields": [
            {"key": "key_id", "label": "API Key ID", "secret": _SECRET, "placeholder": "PK… or AK…"},
            {"key": "secret", "label": "Secret Key", "secret": _SECRET, "placeholder": "Paste the secret"},
            {"key": "environment", "label": "Account", "secret": False, "options": [["live", "Live"], ["paper", "Paper"]]},
        ],
        "sync_minutes": 0,
        "hosts": ["api.alpaca.markets", "paper-api.alpaca.markets"],
    },
    "snaptrade": {
        "id": "snaptrade", "type": "aggregator", "label": "SnapTrade", "product": "Robinhood, Schwab, Fidelity, Vanguard and more",
        "modes": ["read"],
        "availability": "Brokers in the US, Canada and some of Europe (see SnapTrade's broker list). You use your own "
                        "free SnapTrade Personal key (up to 20 of your own brokerage connections).",
        "summary": "One connection for brokers without a public API — Robinhood, Schwab, Fidelity, Vanguard, "
                   "Webull, E*TRADE and others. Read-only: you log in to your broker on SnapTrade's page, not in OpenPF.",
        "trade_note": "Connected read-only. Place trades in your broker's app.",
        "key_url": "https://dashboard.snaptrade.com/signup",
        "read_steps": [
            "Sign up at dashboard.snaptrade.com with a Personal account (free) and verify your email.",
            "Turn on two-factor authentication, then API Keys → create your Personal API key.",
            "Copy the Client ID and Consumer Key and paste them below.",
            "OpenPF opens SnapTrade's connection page. Pick your broker and log in there — OpenPF never sees "
            "that password, and it asks for a read-only connection, so nothing can be traded.",
            "Come back and press Sync. Each broker account appears as its own account in OpenPF.",
        ],
        "fields": [
            {"key": "client_id", "label": "SnapTrade Client ID", "secret": _SECRET, "placeholder": "Your client ID"},
            {"key": "consumer_key", "label": "Consumer Key", "secret": _SECRET, "placeholder": "Paste the consumer key"},
        ],
        "sync_minutes": 15,
        "hosts": ["api.snaptrade.com"],
    },
}

for _ex in EXCHANGES.values():
    PROVIDERS[_ex["id"]] = {
        "id": _ex["id"], "type": "crypto", "label": _ex["label"], "product": _ex["product"],
        "modes": ["read", "trade"], "availability": _ex["uk"],
        "summary": f"Balances on {_ex['product']}. Start read-only; add a trade key later if you want OpenPF to "
                   "place the crypto orders you approve.",
        "key_url": _ex["key_url"], "read_steps": _ex.get("read_steps") or [], "trade_steps": _ex["key_steps"],
        "notes": _ex["notes"], "fees": _ex["fees"], "quote": _ex["quote"],
        "fields": [
            {"key": "api_key", "label": "API key name" if _ex["id"] == "coinbase" else "API key", "secret": _SECRET,
             "placeholder": "organizations/…/apiKeys/…" if _ex["id"] == "coinbase" else "Paste the key"},
            {"key": "api_secret", "label": "Private key" if _ex["id"] == "coinbase" else "Secret", "secret": _SECRET,
             "multiline": _ex["id"] == "coinbase",
             "placeholder": "-----BEGIN EC PRIVATE KEY-----" if _ex["id"] == "coinbase" else "Paste the secret"},
        ],
        "sync_minutes": 0, "hosts": _ex["hosts"],
    }

# Providers researched but not built (shown in the picker as "not available").
UNAVAILABLE: list[dict[str, Any]] = [
    {"id": "schwab", "type": "broker", "label": "Charles Schwab",
     "why": "Schwab's own API needs a registered developer app and a manual re-login every 7 days. Connect Schwab "
            "through SnapTrade instead."},
    {"id": "robinhood", "type": "broker", "label": "Robinhood",
     "why": "No official API for stocks. Connect it read-only through SnapTrade."},
    {"id": "tastytrade", "type": "broker", "label": "tastytrade",
     "why": "Has a personal OAuth API, but it isn't built in yet. SnapTrade covers it read-only today."},
    {"id": "robinhood_crypto", "type": "crypto", "label": "Robinhood Crypto",
     "why": "US-only API with read-only keys; not built in yet (planned)."},
    {"id": "plaid", "type": "aggregator", "label": "Plaid Investments",
     "why": "A free Trial plan exists for US/Canada developers (10 items), but it needs Plaid Link and webhooks. "
            "SnapTrade is simpler for one person's own accounts."},
]


def provider(provider_id: str) -> dict[str, Any] | None:
    return PROVIDERS.get((provider_id or "").strip().lower())


def secret_fields(provider_id: str) -> list[str]:
    return [f["key"] for f in (PROVIDERS.get(provider_id) or {}).get("fields", []) if f.get("secret")]


def setting_fields(provider_id: str) -> list[str]:
    return [f["key"] for f in (PROVIDERS.get(provider_id) or {}).get("fields", []) if not f.get("secret")]


def public_catalog() -> dict[str, Any]:
    from app.core.sandbox import is_sandbox

    sandbox = is_sandbox()
    items = []
    for p in PROVIDERS.values():
        item = {k: v for k, v in p.items() if k not in {"hosts"}}
        if sandbox:
            item["sandbox_values"] = sandbox_values(p["id"])
        items.append(item)
    return {"groups": [{"id": g, "label": label} for g, label in GROUPS], "providers": items,
            "unavailable": UNAVAILABLE, "sandbox": sandbox}


def sandbox_values(provider_id: str) -> dict[str, str]:
    """The fake credentials a sandbox instance accepts (prefilled in the form)."""
    from app.services.provider_clients import SANDBOX_TOKENS

    if provider_id in EXCHANGES:
        from app.services.crypto_sandbox import SANDBOX_READ_CREDENTIALS

        return dict(SANDBOX_READ_CREDENTIALS)
    values = dict(SANDBOX_TOKENS.get(provider_id) or {})
    if provider_id == "alpaca":
        values["environment"] = "paper"
    return values
