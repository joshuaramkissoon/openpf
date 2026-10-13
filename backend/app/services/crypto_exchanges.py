"""Supported crypto exchanges and tradable assets (static catalog, no I/O).

OpenPF talks to exchanges through CCXT, so adding one is a catalog entry plus,
ideally, a way to read the API key's permissions. Every entry says:

* which quote currency market orders use (GBP where the exchange lists GBP
  pairs, else USDC);
* the default fee schedule (used until the connected account reports its own
  tier — always shown as an estimate);
* how the "this key cannot withdraw" check works (``permission_check``);
* the exact key settings to tick, shown verbatim in Settings.

Facts here are deliberately conservative; nothing is advice.
"""

from __future__ import annotations

from typing import Any

CRYPTO_ACCOUNT = "crypto"

# Assets OpenPF will propose. Kept short on purpose: large, liquid coins only.
CRYPTO_ASSETS: dict[str, str] = {
    "BTC": "Bitcoin",
    "ETH": "Ether",
    "SOL": "Solana",
    "XRP": "XRP",
    "ADA": "Cardano",
    "LINK": "Chainlink",
    "LTC": "Litecoin",
    "DOT": "Polkadot",
}

# Stablecoins / fiat that count as cash in a crypto account, not as holdings.
CASH_ASSETS = {"GBP", "USD", "EUR", "USDC", "USDT", "ZGBP", "ZUSD", "ZEUR"}

EXCHANGES: dict[str, dict[str, Any]] = {
    "kraken": {
        "id": "kraken",
        "label": "Kraken",
        "product": "Kraken Pro",
        "ccxt_id": "kraken",
        "quote": "GBP",
        "hosts": ["api.kraken.com"],
        "fees": {"maker": 0.0025, "taker": 0.0040},
        "fee_note": "Kraken Pro's entry tier is 0.25% maker / 0.40% taker; it falls with 30-day volume.",
        "min_slippage": 0.0005,
        "permission_check": "probe",
        "uk": "FCA-registered for cryptoasset activities (Payward Ltd). Supports GBP deposits and GBP pairs.",
        "key_url": "https://pro.kraken.com/app/settings/api",
        # The exact boxes on Kraken Pro → Settings → API → Create API key, per mode.
        # (label, "on" | "off" | "optional"). Shown as a checklist in Settings.
        "permissions": {
            "read": [
                ("Funds · Query Funds", "on"), ("Funds · Deposit Funds", "off"), ("Funds · Withdraw Funds", "off"),
                ("Orders & Trades · Query Open Orders & Trades", "on"),
                ("Orders & Trades · Query Closed Orders & Trades", "on"),
                ("Orders & Trades · Create & Modify Orders", "off"), ("Orders & Trades · Cancel/Close Orders", "off"),
                ("Other · Query Ledger Entries", "optional"), ("Other · Export Data", "off"),
                ("Other · Access WebSockets API", "off"),
            ],
            "trade": [
                ("Funds · Query Funds", "on"), ("Funds · Deposit Funds", "off"), ("Funds · Withdraw Funds", "off"),
                ("Orders & Trades · Query Open Orders & Trades", "on"),
                ("Orders & Trades · Query Closed Orders & Trades", "on"),
                ("Orders & Trades · Create & Modify Orders", "on"), ("Orders & Trades · Cancel/Close Orders", "on"),
                ("Other · Query Ledger Entries", "optional"), ("Other · Export Data", "off"),
                ("Other · Access WebSockets API", "off"),
            ],
        },
        "key_steps": [
            "Kraken Pro → profile icon (top right) → Settings → API → Create API key. Name it \"OpenPF trading\".",
            "Funds: tick Query Funds. Leave Deposit Funds and Withdraw Funds UNTICKED.",
            "Orders & Trades: tick all four — Query Open Orders & Trades, Query Closed Orders & Trades, "
            "Create & Modify Orders, and Cancel/Close Orders.",
            "Other: Query Ledger Entries is optional (lets OpenPF net deposits out of your returns). Leave Export Data "
            "and Access WebSockets API unticked.",
            "Optional: under IP whitelisting, add this machine's public IP address.",
            "Generate key, then copy the API key and the Private key (it's shown once).",
        ],
        "notes": "OpenPF test-calls Kraken's withdrawal-info endpoint on connect: a key that is allowed to reach it is refused.",
        "read_steps": [
            "Kraken Pro → profile icon (top right) → Settings → API → Create API key. Name it \"OpenPF read-only\".",
            "Funds: tick Query Funds only. Leave Deposit Funds and Withdraw Funds UNTICKED.",
            "Orders & Trades: tick Query Open Orders & Trades and Query Closed Orders & Trades. Leave Create & Modify "
            "Orders and Cancel/Close Orders UNTICKED.",
            "Other: tick Query Ledger Entries (lets OpenPF net deposits out of your returns). Leave Export Data and "
            "Access WebSockets API unticked.",
            "Generate key, then copy the API key and the Private key (it's shown once).",
        ],
        "upgrade": "At Kraken, add Create & Modify Orders and Cancel/Close Orders to this key (or create a new "
                   "trading key), then press Enable trading here and paste it. Withdraw Funds stays unticked.",
    },
    "coinbase": {
        "id": "coinbase",
        "label": "Coinbase",
        "product": "Coinbase Advanced Trade",
        "ccxt_id": "coinbase",
        "quote": "GBP",
        "hosts": ["api.coinbase.com"],
        "fees": {"maker": 0.0060, "taker": 0.0120},
        "fee_note": (
            "Advanced Trade's entry tier is 0.60% maker / 1.20% taker. Buying in the simple Coinbase app "
            "adds a spread and a separate Coinbase fee on top, which is why it feels expensive."
        ),
        "min_slippage": 0.0005,
        "permission_check": "api",
        "uk": "Registered with the FCA for cryptoasset activities (CB Payments Ltd). Supports GBP pairs.",
        "key_url": "https://portal.cdp.coinbase.com/access/api",
        "permissions": {
            "read": [("View", "on"), ("Trade", "off"), ("Transfer", "off")],
            "trade": [("View", "on"), ("Trade", "on"), ("Transfer", "off")],
        },
        "key_steps": [
            "Coinbase Developer Platform → API keys → Create API key (an Advanced Trade / CDP key).",
            "Permissions: tick View and Trade. Leave Transfer UNTICKED.",
            "Paste the key name (organizations/…/apiKeys/…) as the API key and the private key as the secret.",
            "Optional: add this machine's IP address to the allowlist.",
        ],
        "notes": "OpenPF reads the key's permissions from Coinbase on connect and refuses any key with Transfer.",
        "read_steps": [
            "Coinbase Developer Platform → API keys → Create API key (a Secret API key for Advanced Trade).",
            "Under Advanced settings choose the ECDSA signature algorithm (CCXT signs Coinbase requests with ECDSA).",
            "Permissions: tick View only. Leave Trade and Transfer UNTICKED.",
            "Paste the key name (organizations/…/apiKeys/…) as the API key and the private key as the secret.",
        ],
    },
    "binance": {
        "id": "binance",
        "label": "Binance",
        "product": "Binance Spot",
        "ccxt_id": "binance",
        "quote": "USDC",
        "hosts": ["api.binance.com"],
        "fees": {"maker": 0.0010, "taker": 0.0010},
        "fee_note": "Binance spot starts at 0.10% maker / 0.10% taker.",
        "min_slippage": 0.0005,
        "permission_check": "api",
        "uk": (
            "UK: Binance stopped onboarding new UK customers in October 2023 and isn't FCA-authorised. Existing UK "
            "accounts keep access; new UK sign-ups aren't possible. Elsewhere, check Binance's own country list."
        ),
        "key_url": "https://www.binance.com/en/my/settings/api-management",
        "permissions": {
            "read": [
                ("Enable Reading", "on"), ("Enable Spot & Margin Trading", "off"), ("Enable Withdrawals", "off"),
                ("Enable Margin Loan, Repay & Transfer", "off"), ("Enable Futures", "off"),
                ("Permits Universal Transfer", "off"), ("Enable Internal Transfer", "off"),
            ],
            "trade": [
                ("Enable Reading", "on"), ("Enable Spot & Margin Trading", "on"), ("Enable Withdrawals", "off"),
                ("Enable Margin Loan, Repay & Transfer", "off"), ("Enable Futures", "off"),
                ("Permits Universal Transfer", "off"), ("Enable Internal Transfer", "off"),
            ],
        },
        "key_steps": [
            "Binance → profile icon → Account → API Management → Create API → System generated. Name it "
            "\"OpenPF trading\" and pass the security check.",
            "On the new key press Edit restrictions.",
            "Tick Enable Reading and Enable Spot & Margin Trading.",
            "NEVER tick Enable Withdrawals. Leave Margin Loan/Repay/Transfer, Futures, Universal Transfer and Internal "
            "Transfer UNTICKED too — OpenPF only places spot orders.",
            "IP access: choose Restrict access to trusted IPs only and add this machine's public IP (recommended; "
            "Binance may also require it for trading keys).",
            "Save, then copy the API key and the Secret key (the secret is shown once).",
        ],
        "notes": "OpenPF reads the key's restrictions from Binance on connect and refuses any key that can withdraw or transfer.",
        "read_steps": [
            "Binance → profile icon → Account → API Management → Create API → System generated. Name it "
            "\"OpenPF read-only\" and pass the security check.",
            "New keys start with only Enable Reading ticked — keep it that way. Don't tick Enable Spot & Margin Trading, "
            "Enable Withdrawals or any transfer, margin or futures box.",
            "IP access: Restrict access to trusted IPs only (add this machine's public IP) is safer; Unrestricted also "
            "works for a read-only key.",
            "Copy the API key and the Secret key (the secret is shown once).",
        ],
        "upgrade": "Binance lets you edit an existing key: API Management → Edit restrictions → tick Enable Spot & "
                   "Margin Trading (never Enable Withdrawals), save, then press Enable trading here and paste the same key.",
    },
}

SANDBOX_EXCHANGE = "kraken"


def crypto_account_kind(exchange: str) -> str:
    """Account key for a crypto connection added in Settings → Connections."""
    return f"{CRYPTO_ACCOUNT}:{exchange.strip().lower()}"


def is_crypto_account(account_kind: str | None) -> bool:
    """``crypto`` (the original connection) or ``crypto:<exchange>``."""
    k = str(account_kind or "").strip().lower()
    return k == CRYPTO_ACCOUNT or k.startswith(CRYPTO_ACCOUNT + ":")


def is_crypto_symbol(symbol: str | None) -> bool:
    base = asset_from_symbol(symbol)
    return bool(base) and base in CRYPTO_ASSETS


def asset_from_symbol(symbol: str | None) -> str:
    """'btc' / 'BTC/GBP' / 'BTC-GBP' / 'XBT' → 'BTC'."""
    raw = str(symbol or "").strip().upper()
    for sep in ("/", "-", ":"):
        raw = raw.split(sep, 1)[0]
    return "BTC" if raw == "XBT" else raw


def market_symbol(asset: str, quote: str) -> str:
    """The CCXT unified market symbol, e.g. ``BTC/GBP``."""
    return f"{asset_from_symbol(asset)}/{quote.upper()}"


def yfinance_symbol(asset: str, quote: str = "GBP") -> str:
    """Yahoo Finance pair for history/volatility, e.g. ``BTC-GBP`` (USDC → USD)."""
    q = "USD" if quote.upper() in {"USDC", "USDT", "USD"} else quote.upper()
    return f"{asset_from_symbol(asset)}-{q}"


def quote_ccy(quote: str) -> str:
    """Fiat currency a quote asset is worth (USDC ≈ USD)."""
    return "USD" if quote.upper() in {"USDC", "USDT", "USD"} else quote.upper()


def public_catalog() -> list[dict[str, Any]]:
    return [
        {k: v for k, v in ex.items() if k not in {"hosts", "ccxt_id", "min_slippage"}}
        for ex in EXCHANGES.values()
    ]
