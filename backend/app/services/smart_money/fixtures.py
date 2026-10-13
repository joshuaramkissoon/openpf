"""Offline demo data for sandbox mode (no network).

Tickers are the sandbox book's; every person and fund here is fictional
("Demo …") so the demo never attributes made-up trades to real people.
Dates are relative to today so the demo always looks current.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.services.smart_money import net

SECTORS = {
    "CCJ": "Energy", "OKLO": "Utilities", "VST": "Utilities", "CEG": "Utilities", "NEE": "Utilities", "XOM": "Energy",
    "NVDA": "Technology", "MSFT": "Technology", "PLTR": "Technology", "AMD": "Technology", "LLY": "Health Care",
    "SMR": "Industrials", "LEU": "Energy", "TLN": "Utilities",
}

DEMO_FUNDS = [
    {"cik": 900001, "name": "Demo Value Partners"},
    {"cik": 900002, "name": "Demo Macro Fund"},
    {"cik": 900003, "name": "Demo Growth Capital"},
]


def _f4(i: int, ticker: str, actor: str, role: str, code: str, shares: float, price: float, ago: int, *,
        exec_: bool = False, plan: bool = False, filed_lag: int = 2) -> dict[str, Any]:
    today = date.today()
    traded = today - timedelta(days=ago)
    filed = traded + timedelta(days=filed_lag)
    side = "buy" if code == "P" else "sell"
    kind, informative, noise = ("open_market", True, None)
    if plan:
        kind, informative, noise = "plan_sale", False, "10b5-1 plan sale"
    elif code == "M":
        kind, informative, noise, side = "option_exercise", False, "option exercise", "buy"
    elif code == "G":
        kind, informative, noise, side = "gift", False, "gift", "sell"
    value = round(shares * price, 2)
    return {
        "source": "form4", "external_id": f"fixture:form4:{i}", "actor": actor, "actor_type": "insider", "actor_role": role,
        "ticker": ticker, "issuer": ticker, "sector": SECTORS.get(ticker), "side": side, "kind": kind,
        "size_low": value, "size_high": value, "size_label": f"${value / 1e3:,.0f}K", "shares": shares, "price": price,
        "trade_date": traded, "disclosed_date": filed, "delay_days": filed_lag, "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4",
        "informative": informative, "noise_reason": noise, "provider": "fixture",
        "meta": {"code": code, "is_exec": exec_, "owner_cik": f"demo-{actor}", "demo": True},
    }


def _cg(i: int, ticker: str, member: str, role: str, ttype: str, lo: float, hi: float, ago: int, lag: int) -> dict[str, Any]:
    today = date.today()
    traded = today - timedelta(days=ago)
    side = "buy" if ttype == "purchase" else "sell"
    return {
        "source": "congress", "external_id": f"fixture:congress:{i}", "actor": member, "actor_type": "congress", "actor_role": role,
        "ticker": ticker, "issuer": ticker, "sector": SECTORS.get(ticker), "side": side, "kind": ttype,
        "size_low": lo, "size_high": hi, "size_label": f"${lo / 1e3:,.0f}K–${hi / 1e3:,.0f}K",
        "trade_date": traded, "disclosed_date": traded + timedelta(days=lag), "delay_days": lag,
        "url": "https://disclosures-clerk.house.gov/FinancialDisclosure", "informative": True, "noise_reason": None,
        "provider": "fixture", "meta": {"demo": True},
    }


def _13f(i: int, fund: dict[str, Any], ticker: str, kind: str, value: float, pct: float | None) -> dict[str, Any]:
    today = date.today()
    q_end = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)  # last quarter end
    filed = q_end + timedelta(days=44)
    if filed > today:
        q_end = date(q_end.year, ((q_end.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
        filed = q_end + timedelta(days=44)
    label = {"new": "new position", "exit": "sold out"}.get(kind) or f"{(pct or 0) * 100:+.0f}% shares"
    return {
        "source": "13f", "external_id": f"fixture:13f:{i}", "actor": fund["name"], "actor_type": "fund", "actor_role": "13F manager",
        "ticker": ticker, "issuer": ticker, "sector": SECTORS.get(ticker), "side": "buy" if kind in ("new", "add") else "sell",
        "kind": kind, "size_low": value, "size_high": value, "size_label": f"{label} · ${value / 1e6:,.0f}M",
        "trade_date": q_end, "disclosed_date": filed, "delay_days": (filed - q_end).days,
        "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=13F-HR", "informative": True, "noise_reason": None,
        "provider": "fixture", "meta": {"cik": fund["cik"], "change_pct": pct, "demo": True},
    }


def rows() -> list[dict[str, Any]]:
    v, m, g = DEMO_FUNDS
    out = [
        # CCJ: a real cluster — three insiders incl. the CFO buying with their own money.
        _f4(1, "CCJ", "Demo Insider Ana Ruiz", "Chief Financial Officer", "P", 4000, 84.1, 6, exec_=True),
        _f4(2, "CCJ", "Demo Insider Tom Park", "Director", "P", 2500, 82.7, 11),
        _f4(3, "CCJ", "Demo Insider Lee Chen", "SVP Operations", "P", 1800, 81.9, 19),
        # NVDA: lots of selling, almost all of it pre-scheduled plan sales (noise).
        _f4(4, "NVDA", "Demo Insider Sam Hale", "Director", "S", 20000, 182.0, 4, plan=True),
        _f4(5, "NVDA", "Demo Insider Kim Osei", "EVP", "S", 12000, 179.5, 9, plan=True),
        _f4(6, "NVDA", "Demo Insider Rob Lund", "Director", "S", 3000, 176.4, 15),
        _f4(7, "NVDA", "Demo Insider Kim Osei", "EVP", "M", 12000, 12.0, 9),
        # PLTR: several discretionary sales — shown, but sales are weak evidence.
        _f4(8, "PLTR", "Demo Insider Ava Moss", "Director", "S", 50000, 181.0, 3),
        _f4(9, "PLTR", "Demo Insider Ian Holt", "Chief Technology Officer", "S", 40000, 176.0, 8),
        _f4(10, "PLTR", "Demo Insider Joy Kerr", "Chief Legal Officer", "S", 25000, 174.2, 14),
        _f4(11, "PLTR", "Demo Insider Max Bell", "Director", "S", 10000, 170.8, 22),
        # VST: a director buy alongside fund adds.
        _f4(12, "VST", "Demo Insider Nia Ford", "Director", "P", 1500, 196.0, 12),
        # AMD: one gift (noise).
        _f4(13, "AMD", "Demo Insider Ola Grant", "Director", "G", 2000, 0.0, 10),
        # Congress (fictional members), each disclosed weeks after the trade.
        _cg(1, "OKLO", "Rep. Demo Alvarez", "House (D) CA-12", "purchase", 15001, 50000, 52, 38),
        _cg(2, "OKLO", "Sen. Demo Whitaker", "Senate (R) TX", "purchase", 1001, 15000, 41, 29),
        _cg(3, "NVDA", "Rep. Demo Alvarez", "House (D) CA-12", "purchase", 1001, 15000, 60, 38),
        _cg(4, "NVDA", "Rep. Demo Brooks", "House (R) FL-07", "sale", 15001, 50000, 47, 44),
        _cg(5, "CEG", "Rep. Demo Nakamura", "House (D) WA-09", "purchase", 15001, 50000, 58, 31),
        _cg(6, "VST", "Rep. Demo Brooks", "House (R) FL-07", "purchase", 1001, 15000, 66, 40),
        _cg(7, "NEE", "Sen. Demo Whitaker", "Senate (R) TX", "purchase", 1001, 15000, 70, 33),
        _cg(8, "TLN", "Rep. Demo Okafor", "House (D) NY-15", "purchase", 15001, 50000, 45, 36),
        _cg(9, "MSFT", "Rep. Demo Okafor", "House (D) NY-15", "sale", 1001, 15000, 39, 21),
        # 13F (fictional funds), latest quarter vs the one before.
        _13f(1, v, "VST", "new", 210e6, None),
        _13f(2, m, "VST", "add", 95e6, 0.42),
        _13f(3, g, "OKLO", "new", 48e6, None),
        _13f(4, v, "MSFT", "trim", 380e6, -0.25),
        _13f(5, m, "NVDA", "add", 520e6, 0.31),
        _13f(6, g, "NVDA", "add", 260e6, 0.22),
        _13f(7, m, "XOM", "exit", 140e6, -1.0),
    ]
    return out


def snapshots() -> list[dict[str, Any]]:
    v, m, g = DEMO_FUNDS
    period = rows()[-1]["trade_date"].isoformat()
    filed = rows()[-1]["disclosed_date"].isoformat()
    hold = {
        v["cik"]: {"MSFT": 0.18, "VST": 0.09, "KO": 0.12, "V": 0.08, "AAPL": 0.21},
        m["cik"]: {"NVDA": 0.14, "VST": 0.07, "MSFT": 0.11, "CCJ": 0.04},
        g["cik"]: {"NVDA": 0.22, "OKLO": 0.03, "PLTR": 0.05, "AMD": 0.06},
    }
    return [
        {"cik": f["cik"], "name": f["name"], "period": period, "filed": filed,
         "holdings": {t: {"weight": w, "value": w * 2e9} for t, w in hold[f["cik"]].items()}}
        for f in DEMO_FUNDS
    ]


def load(db: Session) -> dict[str, int]:
    from app.services.smart_money.service import upsert_rows

    data = rows()
    added = upsert_rows(db, data)
    for snap in snapshots():
        net.cache_put(db, f"13f:snapshot:{snap['cik']}", snap)
    counts: dict[str, int] = {}
    for r in data:
        counts[r["source"]] = counts.get(r["source"], 0) + 1
    return {**counts, "new": added}
