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
        "key_steps": [
            "Kraken Pro → Settings → API → Create API key.",
            "Funds permissions: tick Query. Leave Deposit and Withdraw UNTICKED.",
            "Orders and trades: tick Query open orders & trades, Query closed orders & trades, Create & modify orders.",
            "Leave Cancel & close orders, Export data, Earn and WebSocket permissions unticked.",
            "Data: Query ledger entries is optional (lets OpenPF read your deposit history).",
            "Optional: restrict the key to this machine's IP address.",
        ],
        "notes": "OpenPF test-calls Kraken's withdrawal-info endpoint on connect: a key that is allowed to reach it is refused.",
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
        "key_steps": [
            "Coinbase Developer Platform → API keys → Create API key (an Advanced Trade / CDP key).",
            "Permissions: tick View and Trade. Leave Transfer UNTICKED.",
            "Paste the key name (organizations/…/apiKeys/…) as the API key and the private key as the secret.",
            "Optional: add this machine's IP address to the allowlist.",
        ],
        "notes": "OpenPF reads the key's permissions from Coinbase on connect and refuses any key with Transfer.",
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
            "Binance has not onboarded new UK customers since October 2023 and is not FCA-authorised. "
            "Listed for users outside the UK."
        ),
        "key_url": "https://www.binance.com/en/my/settings/api-management",
        "key_steps": [
            "Binance → API Management → Create API (system-generated).",
            "Tick Enable Reading and Enable Spot & Margin Trading.",
            "Leave Enable Withdrawals, Enable Internal Transfer, Permits Universal Transfer, Margin and Futures UNTICKED.",
            "Restrict access to trusted IPs (this machine).",
        ],
        "notes": "OpenPF reads the key's restrictions from Binance on connect and refuses any key that can withdraw or transfer.",
    },
}

SANDBOX_EXCHANGE = "kraken"


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
