"""Human names for market symbols, and whether Trading 212 lists them.

Archie researches with Yahoo-style symbols (``BA.L``, ``RHM.DE``, ``SAAB-B.ST``):
the suffix is the exchange, which reads as gibberish in the UI. ``describe``
turns each into ``{name, ticker: "BA", exchange: {code: "UK", venue: "London"},
sector, price, tradable, accounts}`` so the app can show "BAE Systems · BA".

Names come from the broker's instrument list first (cached ~6h by
``portfolio_service``), then from market data (``.info``, cached here for a
day). ``tradable`` is checked against the same instrument list: True when T212
lists the listing, False when the list is available and it isn't there, None
when the list can't be read (offline / no key) — never a guess.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Yahoo suffix → (country code, venue). "" is a bare US symbol.
EXCHANGES: dict[str, tuple[str, str]] = {
    "": ("US", "US"),
    ".L": ("UK", "London"),
    ".IL": ("UK", "London IOB"),
    ".DE": ("DE", "Xetra"),
    ".F": ("DE", "Frankfurt"),
    ".PA": ("FR", "Paris"),
    ".AS": ("NL", "Amsterdam"),
    ".BR": ("BE", "Brussels"),
    ".MI": ("IT", "Milan"),
    ".MC": ("ES", "Madrid"),
    ".LS": ("PT", "Lisbon"),
    ".SW": ("CH", "SIX Swiss"),
    ".VI": ("AT", "Vienna"),
    ".ST": ("SE", "Stockholm"),
    ".CO": ("DK", "Copenhagen"),
    ".OL": ("NO", "Oslo"),
    ".HE": ("FI", "Helsinki"),
    ".IR": ("IE", "Dublin"),
    ".TO": ("CA", "Toronto"),
    ".V": ("CA", "TSX Venture"),
    ".AX": ("AU", "ASX"),
    ".HK": ("HK", "Hong Kong"),
    ".T": ("JP", "Tokyo"),
    ".KS": ("KR", "Korea"),
    ".TW": ("TW", "Taiwan"),
}

_PROFILE_TTL_S = 24 * 3600
_PROFILE_WAIT_S = 6.0
_profiles: dict[str, tuple[float, dict[str, Any]]] = {}
_profiles_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="instrument-names")
_inflight: dict[str, Any] = {}

_index_cache: tuple[int, dict[str, dict[str, Any]]] | None = None


def split_symbol(symbol: str) -> tuple[str, str]:
    """``"BA.L"`` → ``("BA", ".L")``; ``"BRK.B"`` → ``("BRK.B", "")`` (a share class, not a venue)."""
    sym = (symbol or "").strip().upper()
    if "." in sym:
        base, _, tail = sym.rpartition(".")
        if base and f".{tail}" in EXCHANGES:
            return base, f".{tail}"
    return sym, ""


def clean_ticker(symbol: str) -> str:
    """The ticker a person would recognise: no exchange suffix, ``SAAB-B`` → ``SAAB B``."""
    base, suffix = split_symbol(symbol)
    return base.replace("-", " ") if suffix else base


def exchange_of(symbol: str) -> dict[str, str] | None:
    base, suffix = split_symbol(symbol)
    if not base:
        return None
    code, venue = EXCHANGES[suffix]
    return {"code": code, "venue": venue}


def _t212_index(db: Session) -> dict[str, dict[str, Any]]:
    """{Yahoo symbol: T212 instrument} built from the cached instrument list."""
    global _index_cache
    from app.services.portfolio_service import _LEVERAGE_NAME_RE, _instrument_meta_map, market_yfinance_ticker

    meta = _instrument_meta_map(db)
    if not meta:
        return {}
    if _index_cache and _index_cache[0] == id(meta):
        return _index_cache[1]
    index: dict[str, dict[str, Any]] = {}
    for code, info in meta.items():
        original = info.get("ticker") or code
        yf = market_yfinance_ticker(original, info.get("short_name"), info.get("currency"))
        if not yf:
            continue
        entry = {
            "instrument_code": code,
            "name": info.get("name") or "",
            "currency": info.get("currency") or None,
            "type": info.get("type") or "",
            "wrapper": bool(_LEVERAGE_NAME_RE.search(info.get("name") or "")),
        }
        key = yf.upper()
        # A same-symbol leveraged/ETP wrapper never shadows the plain listing.
        if key not in index or (index[key]["wrapper"] and not entry["wrapper"]):
            index[key] = entry
    _index_cache = (id(meta), index)
    return index


def instruments_known(db: Session) -> bool:
    from app.services.portfolio_service import _instrument_meta_map

    try:
        return bool(_instrument_meta_map(db))
    except Exception:  # noqa: BLE001
        return False


def find_listing(db: Session, symbol: str) -> dict[str, Any] | None:
    """The T212 instrument for a Yahoo-style symbol, or None."""
    try:
        index = _t212_index(db)
    except Exception:  # noqa: BLE001
        logger.debug("instrument index unavailable", exc_info=True)
        return None
    sym = (symbol or "").strip().upper()
    return index.get(sym) or index.get(sym.replace("-", "."))


def accounts_for(listing: dict[str, Any]) -> list[str]:
    """Which T212 accounts can hold it. US-domiciled ETFs are Invest-only (PRIIPs);
    shares and UCITS ETFs work in both."""
    code = str(listing.get("instrument_code") or "").upper()
    if str(listing.get("type") or "").upper() == "ETF" and code.endswith("_US_EQ"):
        return ["invest"]
    return ["invest", "stocks_isa"]


def _fetch_profile(symbol: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        from app.services.fundamentals_service import get_fundamentals_snapshot

        snap = get_fundamentals_snapshot(symbol)
        out.update({"name": snap.get("long_name"), "sector": snap.get("sector"), "industry": snap.get("industry"),
                    "currency": snap.get("currency")})
    except Exception:  # noqa: BLE001 — names are best-effort
        pass
    try:
        from app.services.market_data import fetch_history

        frame = fetch_history(symbol, lookback_days=10)
        close = float(frame["close"].iloc[-1])
        if close > 0:
            out["price"] = round(close, 4)
    except Exception:  # noqa: BLE001
        pass
    with _profiles_lock:
        _profiles[symbol] = (time.time(), out)
        _inflight.pop(symbol, None)
    return out


def _cached_profile(symbol: str) -> dict[str, Any] | None:
    with _profiles_lock:
        hit = _profiles.get(symbol)
    if hit and time.time() - hit[0] < _PROFILE_TTL_S:
        return hit[1]
    return None


def _profiles_for(symbols: list[str], wait_s: float) -> dict[str, dict[str, Any]]:
    """Market-data profiles, cached; slow lookups keep running and land in the
    cache for the next call instead of holding this one up."""
    out: dict[str, dict[str, Any]] = {}
    futures = {}
    for s in symbols:
        cached = _cached_profile(s)
        if cached is not None:
            out[s] = cached
            continue
        with _profiles_lock:
            fut = _inflight.get(s)
            if fut is None:
                fut = _inflight[s] = _pool.submit(_fetch_profile, s)
        futures[s] = fut
    if futures and wait_s > 0:
        wait(list(futures.values()), timeout=wait_s)
    for s, fut in futures.items():
        if fut.done():
            try:
                out[s] = fut.result()
            except Exception:  # noqa: BLE001
                out[s] = {}
    return out


_LEGAL_SUFFIX = re.compile(
    r"(?:,?\s+(?:inc\.?|incorporated|corp\.?|corporation|plc|p\.l\.c\.|ag|se|sa|s\.a\.|s\.p\.a\.|spa|n\.v\.|nv|"
    r"ltd\.?|limited|co\.?|l\.p\.|ab(?:\s+\(publ\))?|asa|oyj|a/s|kgaa|& co\.? kgaa|holding ag))+$",
    re.IGNORECASE,
)


def tidy_name(name: str | None) -> str | None:
    """"BAE Systems plc" → "BAE Systems"; "Saab AB (publ)" → "Saab". Keeps the name if stripping empties it."""
    if not name:
        return None
    out = _LEGAL_SUFFIX.sub("", name.strip()).strip(" ,")
    return out or name.strip()


_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-_^=]{0,19}$")


def describe(db: Session, symbols: list[str], *, account_kind: str | None = None, profile: bool = True,
             wait_s: float = _PROFILE_WAIT_S) -> list[dict[str, Any]]:
    syms: list[str] = []
    for raw in symbols:
        s = str(raw or "").strip().upper()
        if s and _SYMBOL_RE.match(s) and s not in syms:
            syms.append(s)
    known = instruments_known(db)
    listings = {s: find_listing(db, s) for s in syms}
    # Skip the network for names the broker list already gives us, unless sector/price are wanted.
    profiles = _profiles_for(syms, wait_s) if profile else {}
    items = []
    for s in syms:
        listing = listings[s]
        prof = profiles.get(s) or {}
        accounts = accounts_for(listing) if listing else []
        tradable = True if listing else (False if known else None)
        items.append({
            "symbol": s,
            "ticker": clean_ticker(s),
            "exchange": exchange_of(s),
            "name": tidy_name((listing or {}).get("name") or prof.get("name")),
            "legal_name": (listing or {}).get("name") or prof.get("name") or None,
            "sector": prof.get("sector"),
            "industry": prof.get("industry"),
            "price": prof.get("price"),
            "currency": (listing or {}).get("currency") or prof.get("currency"),
            "tradable": tradable,
            "accounts": accounts,
            "in_account": (account_kind in accounts) if (listing and account_kind) else None,
            "instrument_code": (listing or {}).get("instrument_code"),
            "pending": profile and s not in profiles,
        })
    return items
