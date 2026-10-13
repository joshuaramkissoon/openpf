"""Smart-money MCP server — disclosed trades by insiders (SEC Form 4), followed
funds (SEC 13F) and members of Congress (STOCK Act), plus a deterministic
trend detector. Read-only over the local disclosures table (refreshed daily by
the ``smart_money_refresh`` system job); it never places or proposes trades.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app.core.database import SessionLocal, init_db
from app.services.smart_money import service

_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR") or (Path(__file__).resolve().parent.parent / "logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile

    _LOG_DIR = Path(tempfile.gettempdir()) / "openpf-mcp-logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("smart-money-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "smart_money.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "smart_money",
    instructions=(
        "Disclosed trades by company insiders (Form 4, ~2 days late), followed funds (13F, quarterly, up to "
        "45 days late) and members of Congress (up to 45 days late). Use them as leads to research, never as "
        "something to copy. Always state the delay and how strong the evidence is; sales are weak evidence."
    ),
)


def _fmt(payload: object) -> str:
    return json.dumps(payload, indent=1, ensure_ascii=False, default=str)


@mcp.tool()
def get_smart_money(ticker: str = "", sector: str = "", actor: str = "", days: int = 90, include_noise: bool = False) -> str:
    """Disclosed trades filtered by ticker, sector or actor (any combination).

    With a ticker you also get a summary: insider buyers/sellers in 30 days
    (10b5-1 plan sales excluded), followed funds that hold it, Congress trades
    in 90 days with the median disclosure delay, and any detections.

    Args:
        ticker: US symbol, e.g. "NVDA".
        sector: coarse sector, e.g. "Technology", "Energy", "Utilities".
        actor: person or fund name substring, e.g. "Berkshire" or "Pelosi".
        days: look-back by trade/disclosure date (default 90).
        include_noise: also return option exercises, plan sales, gifts etc. (default false).
    """
    try:
        db = SessionLocal()
        try:
            init_db()
            out = service.query(db, ticker=ticker or None, sector=sector or None, actor=actor or None,
                                days=days, include_noise=include_noise, limit=40)
            for r in out["rows"]:
                for k in ("id", "external_id", "meta", "size_high", "shares", "provider"):
                    r.pop(k, None)
            return _fmt({"ok": True, **out})
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_smart_money error: %s", exc)
        return _fmt({"ok": False, "error": str(exc)})


@mcp.tool()
def get_disclosure_trends(window: int = 90) -> str:
    """Deterministic detections across all disclosures, strongest first:
    insider cluster buys, CEO/CFO open-market buys, several Congress members or
    funds moving into one name, 13F consensus adds, sector flows, and anything
    touching the user's holdings, plays or watchlist. Each has a 0-100 evidence
    score, strength, caveats and the evidence rows.

    Args:
        window: days to look back, 30 or 90 typically (13F uses the latest quarter on file).
    """
    try:
        db = SessionLocal()
        try:
            init_db()
            rep = service.trends_report(db, window=window)
            for d in rep["detections"]:
                d["evidence"] = d["evidence"][:4]
            return _fmt({"ok": True, **rep})
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_disclosure_trends error: %s", exc)
        return _fmt({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    mcp.run()
