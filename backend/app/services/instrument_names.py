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


# How a Yahoo suffix shows up in T212's metadata: the quote currencies a listing
# there can carry, the venue letter T212 puts in the code (``RHMd_EQ``, ``HOp_EQ``,
# ``BAl_EQ``) and the ISIN country it usually has. Matching is on the symbol +
# currency; the letter and ISIN only break ties, so an unexpected letter can't
# hide a listing.
_VENUE: dict[str, tuple[set[str], set[str], set[str]]] = {
    ".L": ({"GBX", "GBP", "USD", "EUR"}, {"l"}, {"GB", "IE", "JE", "GG", "IM", "LU"}),
    ".IL": ({"GBX", "GBP", "USD", "EUR"}, {"l"}, set()),
    ".DE": ({"EUR"}, {"d"}, {"DE"}),
    ".F": ({"EUR"}, {"d"}, {"DE"}),
    ".PA": ({"EUR"}, {"p"}, {"FR"}),
    ".AS": ({"EUR"}, {"a"}, {"NL"}),
    ".BR": ({"EUR"}, {"b"}, {"BE"}),
    ".MI": ({"EUR"}, {"m"}, {"IT"}),
    ".MC": ({"EUR"}, {"e"}, {"ES"}),
    ".LS": ({"EUR"}, set(), {"PT"}),
    ".VI": ({"EUR"}, set(), {"AT"}),
    ".IR": ({"EUR"}, set(), {"IE"}),
    ".HE": ({"EUR"}, set(), {"FI"}),
    ".SW": ({"CHF"}, set(), {"CH"}),
    ".ST": ({"SEK"}, set(), {"SE"}),
    ".CO": ({"DKK"}, set(), {"DK"}),
    ".OL": ({"NOK"}, set(), {"NO"}),
    ".TO": ({"CAD"}, set(), {"CA"}),
    ".V": ({"CAD"}, set(), {"CA"}),
}
_ALNUM = re.compile(r"[^A-Z0-9]")


def _key(text: str | None) -> str:
    return _ALNUM.sub("", (text or "").upper())


def _name_key(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (tidy_name(name) or "").lower().replace("&", "and"))


def _t212_index(db: Session) -> dict[str, Any]:
    """The cached instrument list, indexed by symbol (from the original-case code
    and the shortName) and by tidy name."""
    global _index_cache
    from app.services.portfolio_service import _LEVERAGE_NAME_RE, _instrument_meta_map, t212_symbol

    meta = _instrument_meta_map(db)
    if not meta:
        return {}
    if _index_cache and _index_cache[0] == id(meta):
        return _index_cache[1]
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for code, info in meta.items():
        original = str(info.get("ticker") or code)
        m = re.match(r"^[0-9A-Z.]+?([a-z]+)\d*_EQ$", original)
        entry = {
            "instrument_code": code,
            "original": original,
            "us": code.upper().endswith("_US_EQ"),
            "letter": m.group(1) if m else "",
            "name": info.get("name") or "",
            "currency": (info.get("currency") or "").upper() or None,
            "type": info.get("type") or "",
            "isin": info.get("isin") or "",
            "wrapper": bool(_LEVERAGE_NAME_RE.search(info.get("name") or "")),
        }
        for k in {_key(t212_symbol(original)), _key(info.get("short_name"))} - {""}:
            by_symbol.setdefault(k, []).append(entry)
        nk = _name_key(entry["name"])
        if nk:
            by_name.setdefault(nk, []).append(entry)
    index = {"by_symbol": by_symbol, "by_name": by_name}
    _index_cache = (id(meta), index)
    return index


def instruments_known(db: Session) -> bool:
    from app.services.portfolio_service import _instrument_meta_map

    try:
        return bool(_instrument_meta_map(db))
    except Exception:  # noqa: BLE001
        return False


def _fits_venue(e: dict[str, Any], suffix: str) -> bool:
    if suffix == "":
        return e["us"]
    venue = _VENUE.get(suffix)
    if venue is None:  # Tokyo, Korea, Hong Kong…: T212 has no such listing
        return False
    if e["us"]:
        return False
    return e["currency"] is None or e["currency"] in venue[0]


def _rank(e: dict[str, Any], suffix: str) -> tuple[int, ...]:
    letters, isins = (_VENUE.get(suffix) or (set(), set(), set()))[1:]
    return (
        0 if not e["wrapper"] else 1,
        0 if e["letter"] in letters else 1,
        0 if e["isin"][:2] in isins else 1,
        0 if e["type"].upper() in ("STOCK", "ETF", "") else 1,
    )


def find_listing(db: Session, symbol: str, name: str | None = None) -> dict[str, Any] | None:
    """The T212 instrument for a Yahoo-style symbol, or None.

    Matches the symbol (``BA`` of ``BA.L``) against each instrument's symbol and
    shortName, keeping listings on a venue that quotes in the right currency
    (``RHM.DE`` → a EUR line, never the US ADR). When the symbol finds nothing
    and the caller knows the company ``name``, a tidy-name match on the same
    venue is tried before giving up."""
    try:
        index = _t212_index(db)
    except Exception:  # noqa: BLE001
        logger.debug("instrument index unavailable", exc_info=True)
        return None
    if not index:
        return None
    base, suffix = split_symbol(symbol)
    cands = [e for e in index["by_symbol"].get(_key(base), []) if _fits_venue(e, suffix)]
    matched_by = "symbol"
    if not cands and name:
        nk = _name_key(name)
        pool = [e for e in index["by_name"].get(nk, []) if not suffix or _fits_venue(e, suffix)]
        if not pool and len(nk) >= 5:
            pool = [e for k, rows in index["by_name"].items() if (k.startswith(nk) or nk.startswith(k)) and len(k) >= 5
                    for e in rows if not suffix or _fits_venue(e, suffix)]
        cands = pool
        matched_by = "name"
    if not cands:
        return None
    best = sorted(cands, key=lambda e: _rank(e, suffix))[0]
    return {**best, "matched_by": matched_by}


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
             wait_s: float = _PROFILE_WAIT_S, names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    syms: list[str] = []
    for raw in symbols:
        s = str(raw or "").strip().upper()
        if s and _SYMBOL_RE.match(s) and s not in syms:
            syms.append(s)
    known = instruments_known(db)
    listings = {s: find_listing(db, s, (names or {}).get(s)) for s in syms}
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


# ── Typeahead + names written in an idea ─────────────────────────────────────

def _row_for(db: Session, code: str, info: dict[str, Any]) -> dict[str, Any]:
    from app.services.portfolio_service import market_yfinance_ticker, t212_symbol

    original = info.get("ticker") or code
    yf = market_yfinance_ticker(original, info.get("short_name"), info.get("currency")) or t212_symbol(original)
    short = str(info.get("short_name") or "").strip().upper()
    base, suffix = split_symbol(yf)
    if suffix and " " in short and _key(short) == _key(base):
        yf = short.replace(" ", "-") + suffix  # share classes: SAAB B → SAAB-B.ST, as market data spells it
    listing = {"instrument_code": code, "type": info.get("type") or ""}
    return {
        "symbol": yf.upper(),
        "ticker": clean_ticker(yf),
        "exchange": exchange_of(yf),
        "name": tidy_name(info.get("name")) or clean_ticker(yf),
        "legal_name": info.get("name") or None,
        "currency": info.get("currency") or None,
        "instrument_code": code,
        "accounts": accounts_for(listing),
        "tradable": True,
    }


def search(db: Session, q: str, limit: int = 8) -> list[dict[str, Any]]:
    """Typeahead over the broker's instrument list, as market symbols with names."""
    from app.services.portfolio_service import _instrument_meta_map, search_instruments

    meta = _instrument_meta_map(db)
    out = []
    for r in search_instruments(q, db, limit=limit):
        info = meta.get(str(r["instrument_code"]).upper())
        if info:
            out.append(_row_for(db, str(r["instrument_code"]).upper(), info))
    return out


_STOP_WORDS = {
    "AI", "HBM", "ETF", "ETFS", "US", "USA", "UK", "EU", "GDP", "CEO", "IPO", "IPOS", "NATO", "ESG", "EV", "EVS", "AND",
    "THE", "FOR", "NOT", "ALL", "BUY", "SELL", "ISA", "SIPP", "GPU", "GPUS", "CPU", "LLM", "API", "SAAS", "FX", "OR", "IN",
    "ON", "OF", "TO", "A", "I", "ME", "MY", "BIG", "NEW", "TOP", "LOW", "HIGH", "DC", "RAM", "SSD", "NAND", "DRAM", "CAPEX",
    "FTSE", "S&P", "SPX", "QE", "FED", "BOE", "ECB", "YOY", "Q1", "Q2", "Q3", "Q4", "UCITS", "ADR", "GDR", "LNG", "OIL",
}


def detect_in_text(db: Session, text: str, limit: int = 8) -> list[dict[str, Any]]:
    """Company names and tickers written in an idea ("no SNDK?", "include SanDisk"),
    resolved against the instrument list. Conservative: a ticker must be written
    in capitals (or $-prefixed) and a name capitalised, and both must match."""
    from app.services.portfolio_service import _instrument_meta_map

    try:
        index = _t212_index(db)
        meta = _instrument_meta_map(db)
    except Exception:  # noqa: BLE001
        return []
    if not index or not text:
        return []
    found: dict[str, dict[str, Any]] = {}

    def add(code: str, query: str) -> None:
        if code not in found and code in meta and len(found) < limit:
            found[code] = {**_row_for(db, code, meta[code]), "query": query}

    for m in re.finditer(r"(?<![\w$])\$?([A-Z][A-Z0-9]{1,5}(?:[.\-][A-Z]{1,2})?)(?![\w])", text):
        token = m.group(1)
        if token in _STOP_WORDS and not m.group(0).startswith("$"):
            continue
        listing = find_listing(db, token)
        if not listing and "." not in token:  # a bare symbol listed outside the US (BESI, RHM)
            rows = [e for e in index["by_symbol"].get(_key(token), []) if not e["wrapper"]]
            listing = sorted(rows, key=lambda e: _rank(e, ""))[0] if rows else None
        if listing and not listing["wrapper"]:
            add(listing["instrument_code"], token)

    words = re.findall(r"[A-Za-z][A-Za-z0-9&'.\-]*", text)
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            chunk = words[i:i + size]
            if not chunk[0][0].isupper():
                continue  # names are written as proper nouns
            if size == 1 and (len(chunk[0]) < 4 or chunk[0].upper() in _STOP_WORDS):
                continue
            rows = index["by_name"].get(_name_key(" ".join(chunk)))
            if rows:
                best = sorted(rows, key=lambda e: _rank(e, "" if e["us"] else ".L"))[0]
                if not best["wrapper"]:
                    add(best["instrument_code"], " ".join(chunk))
    return list(found.values())


def resolve_names(db: Session, items: list[str]) -> list[dict[str, Any]]:
    """Must-have / excluded names as typed (symbol or company) → instrument rows.
    Unresolved ones come back with ``tradable: False`` so the draft can say so."""
    from app.services.portfolio_service import _instrument_meta_map

    meta = _instrument_meta_map(db) if items else {}
    out = []
    for raw in items:
        q = raw.strip()
        sym = q.upper().lstrip("$")
        listing = find_listing(db, sym) if re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,15}", sym) else None
        if not listing:
            listing = find_listing(db, "", q)  # by name
        if listing and listing["instrument_code"] in meta:
            out.append({**_row_for(db, listing["instrument_code"], meta[listing["instrument_code"]]), "query": q})
        else:
            hits = search(db, q, limit=1) if len(q) >= 3 else []
            out.append({**hits[0], "query": q} if hits else
                       {"query": q, "symbol": sym, "ticker": clean_ticker(sym), "name": None, "tradable": False if meta else None})
    return out
