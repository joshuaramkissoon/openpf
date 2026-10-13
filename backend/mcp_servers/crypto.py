"""Crypto MCP server for Archie — read-only and keyless.

Balances and positions come from the snapshots the backend stores on each
portfolio refresh; prices come from public market data; fees from the
connected exchange's schedule. This process is never given an exchange key
(``crypto_service`` blanks the stored one inside any MCP process and refuses to
build a keyed client), so nothing here can trade or withdraw. To suggest a
crypto trade, use ``propose_trade`` with ``account_kind='crypto'``.

The one write is ``draft_starter_crypto_play``: it creates a DRAFT play — no
orders. Each tranche later becomes a proposal the user approves.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.core.database import SessionLocal, init_db
from app.services import crypto_service
from app.services.config_store import ConfigStore
from app.services.crypto_exchanges import CRYPTO_ASSETS, EXCHANGES, asset_from_symbol

_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR") or (Path(__file__).resolve().parent.parent / "logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile

    _LOG_DIR = Path(tempfile.gettempdir()) / "openpf-mcp-logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("crypto-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "crypto.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "crypto",
    instructions=(
        "Read-only view of the user's crypto exchange account (balances, positions, prices, fees, risk). "
        "You cannot trade or withdraw. To suggest a crypto trade call proposals.propose_trade with "
        "account_kind='crypto' and symbol 'BTC'/'ETH'/…; it shows fee and slippage estimates and checks the "
        "crypto cap. For a nervous first-timer, draft the Starter crypto play instead of a one-off buy."
    ),
)


def _fmt(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _with_db(fn):  # noqa: ANN001
    init_db()
    db = SessionLocal()
    try:
        return _fmt(fn(db))
    except Exception as exc:  # noqa: BLE001 — surface as a tool error payload
        logger.exception("crypto tool failed")
        return _fmt({"ok": False, "error": str(exc)})
    finally:
        db.close()


@mcp.tool()
def get_crypto_account() -> str:
    """The connected exchange (or none), its verified key permissions, and the latest crypto
    balances and positions from the app's last portfolio refresh (quote currency + GBP)."""
    return _with_db(crypto_service.overview)


@mcp.tool()
def get_crypto_prices(assets: str = "BTC,ETH") -> str:
    """Latest prices in GBP plus the estimated cost of a £100 market buy on the connected exchange.

    Args:
        assets: Comma-separated symbols, e.g. "BTC,ETH,SOL".
    """
    def run(db):  # noqa: ANN001
        store = ConfigStore(db)
        cfg = crypto_service.get_config(store)
        exchange = cfg.get("exchange") if cfg.get("exchange") in EXCHANGES else "kraken"
        out = []
        for raw in [a for a in assets.split(",") if a.strip()][:10]:
            asset = asset_from_symbol(raw)
            if asset not in CRYPTO_ASSETS:
                out.append({"asset": asset, "error": "not a supported crypto asset"})
                continue
            try:
                est = crypto_service.quote_estimate(None, exchange, asset, "buy", 100.0, fees=cfg.get("fees"))
                out.append({"asset": asset, "name": CRYPTO_ASSETS[asset], "price_gbp": round(est["price_gbp"], 2),
                            "market": est["market"], "cost_of_100gbp_buy": {"fee_gbp": est["fee_gbp"],
                                                                            "slippage_gbp": est["slippage_gbp"]}})
            except Exception as exc:  # noqa: BLE001
                out.append({"asset": asset, "error": str(exc)})
        return {"exchange": EXCHANGES[exchange]["label"], "connected": crypto_service.is_connected(store), "prices": out}
    return _with_db(run)


@mcp.tool()
def get_crypto_fees() -> str:
    """Fee schedule for the connected exchange (and the alternatives), as fractions (0.004 = 0.40%)."""
    def run(db):  # noqa: ANN001
        pub = crypto_service.public(ConfigStore(db))
        return {"connected": pub, "exchanges": [{"exchange": e["label"], "product": e["product"], "quote": e["quote"],
                                                 "maker": e["fees"]["maker"], "taker": e["fees"]["taker"],
                                                 "note": e["fee_note"], "uk": e["uk"]} for e in EXCHANGES.values()]}
    return _with_db(run)


@mcp.tool()
def get_crypto_risk() -> str:
    """Crypto sleeve risk: per-asset annualised volatility, 1-year max drawdown, correlation with the rest
    of the book, crypto's share of the book vs the cap, and what a 50% crypto fall would cost."""
    return _with_db(lambda db: crypto_service.crypto_risk(db))


@mcp.tool()
def draft_starter_crypto_play(conviction: str = "low", include_eth: bool = True, weeks: int = 10,
                              budget_gbp: float = 0.0) -> str:
    """Create the built-in Starter crypto play as a DRAFT (no orders): a small staged weekly DCA into
    Bitcoin (+ optional Ether), sized from conviction under the crypto cap, with kill rules and a
    plain-English explanation of volatility, fees, custody and UK tax.

    Args:
        conviction: low (1% of book) | medium (2%) | high (3%) — never above the crypto cap.
        include_eth: Add Ether as a 20% slice (Bitcoin 80%).
        weeks: Number of equal weekly buys (4–16, default 10).
        budget_gbp: Optional fixed budget in GBP; 0 lets the plan size it.
    """
    def run(db):  # noqa: ANN001
        from app.services import crypto_play, plays_service

        play = crypto_play.create_starter_play(db, conviction=conviction, include_eth=include_eth, weeks=weeks,
                                               budget_gbp=budget_gbp or None)
        p = plays_service.serialize_play(play)
        return {"ok": True, "play_id": p["id"], "title": p["title"], "status": p["status"],
                "budget_gbp": p["budget_gbp"], "sizing": {k: p["sizing"].get(k) for k in
                                                          ("dca", "steps", "warnings", "tranches", "max_loss_gbp")},
                "note": "Draft only. The user reviews it on the Plays page; each weekly tranche becomes a proposal to approve."}
    return _with_db(run)


if __name__ == "__main__":
    mcp.run()
