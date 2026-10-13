"""SEC EDGAR: Form 4 (insider transactions) and 13F-HR (institutional holdings).

Parsing is pure (``parse_form4``, ``parse_info_table``, ``diff_holdings``) so it
is tested from fixtures; the fetchers wrap it with the throttled, cached client
in :mod:`net`.

Form 4 noise filter — only two kinds of rows are *informative*:
* ``P`` open-market purchases (insiders putting their own cash in);
* ``S`` open-market sales that are neither under a Rule 10b5-1 plan nor part
  of an exercise-and-sell.
Everything else (option exercises M/X/C, gifts G, awards A, tax withholding F,
plan sales, dispositions to the issuer…) is stored with ``informative=False``
and a ``noise_reason`` so it never drives a signal.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.smart_money import Disclosure
from app.services.smart_money import net

logger = logging.getLogger(__name__)

SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
OPENFIGI = "https://api.openfigi.com/v3/mapping"

_PLAN_RE = re.compile(r"10b5-?1", re.I)
_CEO_RE = re.compile(r"\b(chief executive|ceo|co-ceo)\b", re.I)
_CFO_RE = re.compile(r"\b(chief financial|cfo)\b", re.I)
_NOISE_CODES = {
    "M": ("option_exercise", "option exercise"),
    "X": ("option_exercise", "option exercise"),
    "C": ("option_exercise", "derivative conversion"),
    "G": ("gift", "gift"),
    "A": ("award", "grant/award from the company"),
    "F": ("tax", "shares withheld for tax"),
    "D": ("other", "disposition to the issuer"),
    "W": ("other", "will/inheritance"),
    "J": ("other", "other (code J)"),
    "I": ("other", "discretionary plan transaction"),
}


# ── XML helpers ─────────────────────────────────────────────────────────────

def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _txt(el: ET.Element | None, path: str) -> str:
    if el is None:
        return ""
    node = el.find(path)
    if node is None:
        return ""
    # Form 4 wraps most values in <value>.
    val = node.find("value")
    raw = (val.text if val is not None else node.text) or ""
    return raw.strip()


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", "")) if s else None
    except ValueError:
        return None


def _bool(s: str) -> bool:
    return s.strip().lower() in {"1", "true", "yes"}


def _date(s: str) -> date | None:
    s = (s or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ── Form 4 ──────────────────────────────────────────────────────────────────

def insider_role(owner: dict[str, Any]) -> tuple[str, bool]:
    """(display role, is CEO/CFO)."""
    title = (owner.get("title") or "").strip()
    is_exec = bool(_CEO_RE.search(title) or _CFO_RE.search(title))
    if title:
        role = title
    elif owner.get("director"):
        role = "Director"
    elif owner.get("ten_pct"):
        role = "10% owner"
    elif owner.get("officer"):
        role = "Officer"
    else:
        role = "Insider"
    return role, is_exec


def parse_form4(xml_text: str, *, accession: str, url: str = "", filed: date | None = None) -> list[dict[str, Any]]:
    """Normalise one Form 4 into disclosure rows (non-derivative table only)."""
    root = _strip_ns(ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text))
    issuer = root.find("issuer")
    ticker = _txt(issuer, "issuerTradingSymbol").upper() or None
    issuer_name = _txt(issuer, "issuerName")
    issuer_cik = _txt(issuer, "issuerCik")

    owners = []
    for ro in root.findall("reportingOwner"):
        rel = ro.find("reportingOwnerRelationship")
        owners.append({
            "name": _txt(ro, "reportingOwnerId/rptOwnerName"),
            "cik": _txt(ro, "reportingOwnerId/rptOwnerCik"),
            "director": _bool(_txt(rel, "isDirector")),
            "officer": _bool(_txt(rel, "isOfficer")),
            "ten_pct": _bool(_txt(rel, "isTenPercentOwner")),
            "title": _txt(rel, "officerTitle"),
        })
    owner = owners[0] if owners else {"name": "Unknown insider"}
    role, is_exec = insider_role(owner)
    actor = owner.get("name") or "Unknown insider"
    if len(owners) > 1:
        actor = f"{actor} (+{len(owners) - 1} joint filer{'s' if len(owners) > 2 else ''})"

    doc_plan = _bool(_txt(root, "aff10b5One"))
    footnotes = {fn.get("id"): (fn.text or "") for fn in root.findall("footnotes/footnote")}

    all_codes: dict[str, set[str]] = {}  # trade date → codes (incl. derivative rows)
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction") + root.findall("derivativeTable/derivativeTransaction"):
        all_codes.setdefault(_txt(tx, "transactionDate"), set()).add(_txt(tx, "transactionCoding/transactionCode").upper())

    rows: list[dict[str, Any]] = []
    for idx, tx in enumerate(root.findall("nonDerivativeTable/nonDerivativeTransaction")):
        code = _txt(tx, "transactionCoding/transactionCode").upper()
        tdate_s = _txt(tx, "transactionDate")
        shares = _num(_txt(tx, "transactionAmounts/transactionShares"))
        price = _num(_txt(tx, "transactionAmounts/transactionPricePerShare"))
        acq = _txt(tx, "transactionAmounts/transactionAcquiredDisposedCode").upper()
        refs = [f.get("id") for f in tx.iter("footnoteId")]
        plan = doc_plan or any(_PLAN_RE.search(footnotes.get(r, "") or "") for r in refs)

        side = "buy" if acq == "A" or code == "P" else "sell"
        informative, noise = True, None
        if code == "P":
            kind, side = "open_market", "buy"
        elif code == "S":
            side = "sell"
            if plan:
                kind, informative, noise = "plan_sale", False, "10b5-1 plan sale"
            elif all_codes.get(tdate_s, set()) & {"M", "X", "C"}:
                kind, informative, noise = "exercise_sale", False, "sale linked to an option exercise"
            else:
                kind = "open_market"
        else:
            kind, noise = _NOISE_CODES.get(code, ("other", f"code {code or '?'}"))
            informative = False
        if informative and (price is None or price <= 0):
            informative, noise = False, "no price (not an open-market trade)"

        value = round(shares * price, 2) if shares and price else None
        tdate = _date(tdate_s)
        rows.append({
            "source": "form4",
            "external_id": f"form4:{accession}:{idx}",
            "actor": actor,
            "actor_type": "insider",
            "actor_role": role,
            "ticker": ticker,
            "issuer": issuer_name,
            "side": side,
            "kind": kind,
            "size_low": value,
            "size_high": value,
            "size_label": _usd(value) if value else "",
            "shares": shares,
            "price": price,
            "trade_date": tdate,
            "disclosed_date": filed,
            "delay_days": (filed - tdate).days if filed and tdate else None,
            "url": url,
            "informative": informative,
            "noise_reason": noise,
            "provider": "sec",
            "meta": {"code": code, "is_exec": is_exec, "owner_cik": owner.get("cik"), "issuer_cik": issuer_cik,
                     "accession": accession, "director": bool(owner.get("director"))},
        })
    return rows


def _usd(v: float | None) -> str:
    if v is None:
        return ""
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:.1f}B"
    if a >= 1e6:
        return f"${v / 1e6:.1f}M"
    if a >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:,.0f}"


# ── 13F ─────────────────────────────────────────────────────────────────────

def parse_info_table(xml_text: str) -> dict[str, dict[str, Any]]:
    """{CUSIP: {name, cusip, shares, value}} — common shares only (no options
    via putCall, no principal-amount rows), summed across sub-managers."""
    root = _strip_ns(ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text))
    out: dict[str, dict[str, Any]] = {}
    for it in root.iter("infoTable"):
        if (it.findtext("putCall") or "").strip():
            continue
        if (it.findtext("shrsOrPrnAmt/sshPrnamtType") or "SH").strip().upper() != "SH":
            continue
        cusip = (it.findtext("cusip") or "").strip().upper()
        if not cusip:
            continue
        shares = _num((it.findtext("shrsOrPrnAmt/sshPrnamt") or "").strip()) or 0.0
        value = _num((it.findtext("value") or "").strip()) or 0.0
        row = out.setdefault(cusip, {"cusip": cusip, "name": (it.findtext("nameOfIssuer") or "").strip(), "shares": 0.0, "value": 0.0})
        row["shares"] += shares
        row["value"] += value
    return out


def diff_holdings(prev: dict[str, dict[str, Any]], cur: dict[str, dict[str, Any]], *, threshold: float = 0.2) -> list[dict[str, Any]]:
    """Quarter-on-quarter changes worth knowing: new, exit, add/trim ≥ threshold
    (by share count, so price moves don't masquerade as buying)."""
    changes: list[dict[str, Any]] = []
    for cusip in set(prev) | set(cur):
        p, c = prev.get(cusip), cur.get(cusip)
        ps, cs = (p or {}).get("shares", 0.0), (c or {}).get("shares", 0.0)
        name = (c or p or {}).get("name", "")
        if ps <= 0 and cs > 0:
            kind, pct = "new", None
        elif ps > 0 and cs <= 0:
            kind, pct = "exit", -1.0
        else:
            pct = (cs - ps) / ps
            if pct >= threshold:
                kind = "add"
            elif pct <= -threshold:
                kind = "trim"
            else:
                continue
        changes.append({
            "cusip": cusip, "name": name, "kind": kind, "change_pct": pct,
            "shares_prev": ps, "shares_cur": cs,
            "value_prev": (p or {}).get("value", 0.0), "value_cur": (c or {}).get("value", 0.0),
        })
    changes.sort(key=lambda r: max(r["value_prev"], r["value_cur"]), reverse=True)
    return changes


def changes_to_rows(changes: list[dict[str, Any]], *, manager: str, cik: int, period: date | None, filed: date | None,
                    url: str, tickers: dict[str, str | None]) -> list[dict[str, Any]]:
    rows = []
    for ch in changes:
        side = "buy" if ch["kind"] in ("new", "add") else "sell"
        value = ch["value_cur"] if side == "buy" else ch["value_prev"]
        pct = ch.get("change_pct")
        label = {"new": "new position", "exit": "sold out"}.get(ch["kind"]) or f"{pct * 100:+.0f}% shares"
        rows.append({
            "source": "13f",
            "external_id": f"13f:{cik}:{period}:{ch['cusip']}",
            "actor": manager,
            "actor_type": "fund",
            "actor_role": "13F manager",
            "ticker": tickers.get(ch["cusip"]),
            "issuer": ch["name"],
            "side": side,
            "kind": ch["kind"],
            "size_low": value,
            "size_high": value,
            "size_label": f"{label} · {_usd(value)}",
            "shares": ch["shares_cur"],
            "price": None,
            "trade_date": period,  # quarter end — the trade happened at some point in that quarter
            "disclosed_date": filed,
            "delay_days": (filed - period).days if filed and period else None,
            "url": url,
            "informative": True,
            "noise_reason": None,
            "provider": "sec",
            "meta": {"cusip": ch["cusip"], "change_pct": pct, "shares_prev": ch["shares_prev"], "cik": cik},
        })
    return rows


# ── SIC → coarse sector ─────────────────────────────────────────────────────

def sic_to_sector(sic: int | str | None) -> str | None:
    try:
        s = int(sic)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    ranges = [
        (100, 999, "Materials"), (1000, 1299, "Materials"), (1300, 1399, "Energy"), (1400, 1499, "Materials"),
        (1500, 1799, "Industrials"), (2000, 2199, "Consumer Staples"), (2200, 2399, "Consumer Discretionary"),
        (2400, 2699, "Materials"), (2700, 2799, "Communication"), (2800, 2829, "Materials"),
        (2830, 2836, "Health Care"), (2837, 2899, "Materials"), (2900, 2999, "Energy"), (3000, 3399, "Materials"),
        (3400, 3569, "Industrials"), (3570, 3579, "Technology"), (3580, 3599, "Industrials"),
        (3600, 3699, "Technology"), (3710, 3716, "Consumer Discretionary"), (3700, 3799, "Industrials"),
        (3812, 3812, "Industrials"), (3820, 3839, "Technology"), (3840, 3851, "Health Care"),
        (3800, 3899, "Technology"), (3900, 3999, "Consumer Discretionary"), (4000, 4799, "Industrials"),
        (4800, 4899, "Communication"), (4900, 4999, "Utilities"), (5000, 5199, "Industrials"),
        (5400, 5499, "Consumer Staples"), (5912, 5912, "Consumer Staples"), (5200, 5999, "Consumer Discretionary"),
        (6798, 6798, "Real Estate"), (6500, 6599, "Real Estate"), (6000, 6799, "Financials"),
        (7370, 7379, "Technology"), (7810, 7819, "Communication"), (7000, 7299, "Consumer Discretionary"),
        (7300, 7399, "Industrials"), (7800, 7999, "Consumer Discretionary"), (8000, 8099, "Health Care"),
        (8700, 8799, "Industrials"),
    ]
    # Narrow ranges first so specific codes win over their enclosing block.
    for lo, hi, sector in sorted(ranges, key=lambda r: r[1] - r[0]):
        if lo <= s <= hi:
            return sector
    return "Other"


# ── Fetchers (network, cached) ──────────────────────────────────────────────

def cik_map(db: Session, ua: str) -> dict[str, list[Any]]:
    """{TICKER: [cik, title]} from SEC's company_tickers.json (cached 7 days)."""
    key = "sec:company_tickers"
    hit = net.cache_get(db, key, timedelta(days=7))
    if hit is not None:
        return hit
    raw = net.sec_get(SEC_TICKERS, ua).json()
    out = {str(v["ticker"]).upper(): [int(v["cik_str"]), v.get("title", "")] for v in raw.values()}
    net.cache_put(db, key, out)
    return out


def submissions(db: Session, cik: int, ua: str, ttl: timedelta = timedelta(hours=6)) -> dict[str, Any]:
    """Compact submissions summary: name, SIC, and recent Form 4 / 13F filings."""
    key = f"sec:subs:{cik}"
    hit = net.cache_get(db, key, ttl)
    if hit is not None:
        return hit
    raw = net.sec_get(SEC_SUBMISSIONS.format(cik=cik), ua).json()
    r = raw.get("filings", {}).get("recent", {})
    keep = []
    for i, form in enumerate(r.get("form", [])):
        if form in ("4", "13F-HR"):
            keep.append({"form": form, "acc": r["accessionNumber"][i], "filed": r["filingDate"][i],
                         "report": (r.get("reportDate") or [""] * (i + 1))[i], "doc": r["primaryDocument"][i]})
    out = {"name": raw.get("name", ""), "sic": raw.get("sic"), "sic_desc": raw.get("sicDescription"),
           "tickers": raw.get("tickers", []), "filings": keep[:400]}
    net.cache_put(db, key, out)
    return out


def sector_for_cik(db: Session, cik: int, ua: str) -> str | None:
    try:
        subs = submissions(db, cik, ua, ttl=timedelta(days=30))
    except net.SourceUnavailable:
        return None
    return sic_to_sector(subs.get("sic"))


def _filing_folder(cik: int, acc: str) -> str:
    return f"{SEC_ARCHIVES}/{cik}/{acc.replace('-', '')}"


def fetch_form4_rows(db: Session, ticker: str, ua: str, *, since_days: int = 90, max_filings: int = 30) -> list[dict[str, Any]]:
    """New Form 4 rows for one issuer ticker (skips accessions already stored)."""
    cmap = cik_map(db, ua)
    entry = cmap.get(ticker.upper()) or cmap.get(ticker.upper().replace(".", "-"))
    if not entry:
        return []
    cik = int(entry[0])
    subs = submissions(db, cik, ua)
    sector = sic_to_sector(subs.get("sic"))
    cutoff = date.today() - timedelta(days=since_days)
    filings = [f for f in subs["filings"] if f["form"] == "4" and (_date(f["filed"]) or date.min) >= cutoff][:max_filings]
    if not filings:
        return []
    known = {
        r.split(":")[1]
        for r in db.execute(select(Disclosure.external_id).where(Disclosure.source == "form4", Disclosure.ticker == ticker.upper())).scalars()
    }
    rows: list[dict[str, Any]] = []
    for f in filings:
        acc = f["acc"]
        if acc in known or net.cache_get(db, f"f4seen:{acc}") is not None:
            continue
        doc = f["doc"].split("/")[-1]
        folder = _filing_folder(cik, acc)
        try:
            xml = net.sec_get(f"{folder}/{doc}", ua).text
            parsed = parse_form4(xml, accession=acc, url=f"{folder}/{f['doc']}", filed=_date(f["filed"]))
        except net.SourceUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — one bad filing mustn't sink the ticker
            logger.info("form4 parse failed %s: %s", acc, exc)
            parsed = []
        for row in parsed:
            row["sector"] = sector
            # Filings sometimes carry an old/blank symbol; trust the ticker we asked for.
            row["ticker"] = ticker.upper()
        net.cache_put(db, f"f4seen:{acc}", 1)
        rows.extend(parsed)
    return rows


def _info_table_url(db: Session, cik: int, acc: str, ua: str) -> str | None:
    folder = _filing_folder(cik, acc)
    idx = net.cached_sec_json(db, f"{folder}/index.json", ua, timedelta(days=3650))
    names = [i["name"] for i in idx.get("directory", {}).get("item", []) if str(i.get("name", "")).lower().endswith(".xml")]
    cands = [n for n in names if n.lower() != "primary_doc.xml"]
    return f"{folder}/{cands[0]}" if cands else None


def fetch_13f_holdings(db: Session, cik: int, acc: str, ua: str) -> dict[str, dict[str, Any]]:
    key = f"13f:holdings:{cik}:{acc}"
    hit = net.cache_get(db, key)
    if hit is not None:
        return hit
    url = _info_table_url(db, cik, acc, ua)
    if not url:
        return {}
    holdings = parse_info_table(net.sec_get(url, ua).text)
    net.cache_put(db, key, holdings)
    return holdings


def map_cusips(db: Session, cusips: list[str], *, budget: int = 300) -> dict[str, str | None]:
    """CUSIP → US ticker via OpenFIGI (free; 10 ids/request keyless), cached
    forever (misses for 30 days). Unmapped CUSIPs return None."""
    import time

    out: dict[str, str | None] = {}
    todo: list[str] = []
    for c in cusips:
        hit = net.cache_get(db, f"cusip:{c}")
        if hit is not None and (hit.get("ticker") or (net.cache_age(db, f"cusip:{c}") or timedelta(0)) < timedelta(days=30)):
            out[c] = hit.get("ticker")
        else:
            todo.append(c)
    todo = todo[:budget]
    for i in range(0, len(todo), 10):
        batch = todo[i:i + 10]
        try:
            res = net.http_post_json(OPENFIGI, [{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in batch])
        except net.SourceUnavailable as exc:
            logger.info("openfigi unavailable: %s", exc)
            break
        except Exception as exc:  # noqa: BLE001
            logger.info("openfigi error: %s", exc)
            break
        for c, item in zip(batch, res):
            data = (item or {}).get("data") or []
            eq = next((d for d in data if d.get("marketSector") == "Equity"), data[0] if data else None)
            ticker = (eq or {}).get("ticker")
            ticker = ticker.replace("/", ".").upper() if ticker else None
            net.cache_put(db, f"cusip:{c}", {"ticker": ticker})
            out[c] = ticker
        if i + 10 < len(todo):
            time.sleep(2.6)  # keyless: 25 requests/minute
    return out


def fetch_13f_rows(db: Session, cik: int, ua: str, *, name_hint: str = "") -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Latest quarter-on-quarter changes for one manager, plus a compact
    current-holdings snapshot ``{period, filed, name, holdings: {TICKER: value}}``."""
    subs = submissions(db, cik, ua)
    filings = [f for f in subs["filings"] if f["form"] == "13F-HR"]
    if not filings:
        return [], None
    filings.sort(key=lambda f: (f.get("report") or "", f["filed"]), reverse=True)
    # One filing per period (the newest original).
    by_period: list[dict[str, Any]] = []
    for f in filings:
        if not by_period or by_period[-1]["report"] != f["report"]:
            by_period.append(f)
    latest = by_period[0]
    manager = name_hint or subs.get("name") or f"CIK {cik}"
    cur = fetch_13f_holdings(db, cik, latest["acc"], ua)
    prev = fetch_13f_holdings(db, cik, by_period[1]["acc"], ua) if len(by_period) > 1 else {}
    changes = diff_holdings(prev, cur) if prev else []
    top_cur = sorted(cur.values(), key=lambda r: r["value"], reverse=True)
    wanted = [c["cusip"] for c in changes] + [r["cusip"] for r in top_cur]
    tickers = map_cusips(db, list(dict.fromkeys(wanted)))
    folder = _filing_folder(cik, latest["acc"])
    rows = changes_to_rows(changes, manager=manager, cik=cik, period=_date(latest["report"]), filed=_date(latest["filed"]),
                           url=f"{folder}/", tickers=tickers)
    total = sum(r["value"] for r in cur.values()) or 1.0
    snapshot = {
        "cik": cik, "name": manager, "period": latest["report"], "filed": latest["filed"],
        "holdings": {tickers[c]: {"value": cur[c]["value"], "weight": cur[c]["value"] / total}
                     for c in cur if tickers.get(c)},
    }
    return rows, snapshot
