"""US Congress trades (STOCK Act) behind a provider interface.

There's no official API. Options we evaluated:

* **House Clerk** (official): the yearly ``{YEAR}FD.zip`` index lists every
  Periodic Transaction Report (member, date, DocID) but the trades themselves
  are only in PDFs — parsing those is what Capitol API does.
* **Senate eFD** (official): a session/CSRF-gated search UI; returns 403 to
  scripted clients. Not usable without scraping.
* **Capitol API** (open source, self-hosted Node service): parses House PTR
  PDFs into JSON. House only, needs you to run it. Supported here as
  ``capitol_api`` with a configurable base URL.
* **Bargo** free API (hosted): House + Senate, already parsed, keyless
  (30 req / 100 rows a day per IP; a free key lifts it to 100 / 1,000), last
  3 months only, attribution required. The default — most reliable free route.

Every row carries its own delay (``disclosed − traded``); members have up to
45 days to report, so these are always old news.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.services.smart_money import net

logger = logging.getLogger(__name__)

BARGO_BASE = "https://www.bargo.ai/free-apis/congress/v1"
BARGO_ATTRIBUTION = {"label": "Congress data: Bargo", "url": "https://www.bargo.ai/free-apis/congress"}


def _d(s: Any) -> date | None:
    s = str(s or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _side_kind(raw: str) -> tuple[str, str, bool]:
    """(side, kind, informative) from a provider's transaction type."""
    t = (raw or "").strip().lower()
    if t.startswith("p") or "purchase" in t or t == "buy":
        return "buy", "purchase", True
    if "partial" in t:
        return "sell", "partial_sale", True
    if t.startswith("s") or "sale" in t or t == "sell":
        return "sell", "sale", True
    if t.startswith("e") or "exchange" in t:
        return "", "exchange", False
    return "", t or "other", False


def _amount_label(lo: float | None, hi: float | None) -> str:
    def f(v: float) -> str:
        return f"${v / 1e6:.0f}M" if v >= 1e6 else f"${v / 1e3:.0f}K" if v >= 1e3 else f"${v:,.0f}"

    if lo and hi:
        return f"{f(lo)}–{f(hi)}"
    if lo:
        return f"{f(lo)}+"
    return ""


def _parse_range(text: str) -> tuple[float | None, float | None]:
    nums = []
    for part in (text or "").replace("$", "").replace(",", "").split("-"):
        part = part.strip().split(" ")[0]
        try:
            nums.append(float(part))
        except ValueError:
            continue
    if not nums:
        return None, None
    return nums[0], (nums[1] if len(nums) > 1 else None)


def make_row(*, provider: str, member: str, chamber: str, state: str, ticker: str | None, asset: str, ttype: str,
             lo: float | None, hi: float | None, traded: date | None, disclosed: date | None, url: str,
             ext: str | None = None, party: str | None = None) -> dict[str, Any]:
    side, kind, informative = _side_kind(ttype)
    if not ticker:
        informative = False
    ext = ext or hashlib.sha1(f"{member}|{ticker}|{traded}|{ttype}|{lo}|{hi}|{disclosed}".encode()).hexdigest()[:20]
    role = " ".join(x for x in [chamber.title() if chamber else "", f"({party})" if party else "", state] if x)
    return {
        "source": "congress",
        "external_id": f"congress:{provider}:{ext}",
        "actor": member,
        "actor_type": "congress",
        "actor_role": role,
        "ticker": (ticker or "").upper() or None,
        "issuer": asset or "",
        "side": side,
        "kind": kind,
        "size_low": lo,
        "size_high": hi,
        "size_label": _amount_label(lo, hi),
        "shares": None,
        "price": None,
        "trade_date": traded,
        "disclosed_date": disclosed,
        "delay_days": (disclosed - traded).days if traded and disclosed else None,
        "url": url,
        "informative": informative,
        "noise_reason": None if informative else ("no listed ticker" if not ticker else f"{kind} (not a buy/sell)"),
        "provider": provider,
        "meta": {"chamber": chamber, "state": state, "party": party},
    }


class CongressProvider(Protocol):
    name: str
    label: str
    attribution: dict[str, str] | None

    def fetch(self, db: Session, *, tickers: list[str]) -> list[dict[str, Any]]: ...


class BargoProvider:
    """Bargo's free Congress API. Budget-aware: keyless is 30 requests and 100
    rows per day, so a refresh pulls the global feed once a day and only spends
    leftover budget on per-ticker lookups for the user's own names."""

    name = "bargo"
    label = "Bargo free API (House + Senate, last 3 months)"
    attribution = BARGO_ATTRIBUTION

    def __init__(self, api_key: str = "", base_url: str = BARGO_BASE):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key, "Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _get(self, db: Session, path: str, params: dict[str, Any], ttl: timedelta) -> list[dict[str, Any]]:
        key = f"bargo:{path}:{sorted(params.items())}"
        hit = net.cache_get(db, key, ttl)
        if hit is not None:
            return hit
        budget = net.cache_get(db, "bargo:budget", timedelta(hours=20)) or {}
        if budget.get("remaining") is not None and int(budget["remaining"]) <= 2:
            raise net.SourceUnavailable("Bargo daily request budget used up")
        data, resp = net.http_get_json(f"{self.base_url}{path}", params=params, headers=self._headers())
        rem = resp.headers.get("x-ratelimit-remaining")
        rows_rem = resp.headers.get("x-ratelimit-rows-remaining")
        if rem is not None:
            net.cache_put(db, "bargo:budget", {"remaining": rem, "rows_remaining": rows_rem})
        trades = data.get("trades", []) if isinstance(data, dict) else []
        net.cache_put(db, key, trades)
        return trades

    def _rows(self, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for t in trades:
            lo, hi = t.get("amount_low"), t.get("amount_high")
            if lo is None and hi is None:
                lo, hi = _parse_range(t.get("amount_range", ""))
            row = make_row(
                provider="bargo", member=t.get("member", ""), chamber=t.get("chamber", ""), state=t.get("state", ""),
                ticker=t.get("ticker"), asset=t.get("asset", ""), ttype=t.get("type", ""),
                lo=float(lo) if lo is not None else None, hi=float(hi) if hi is not None else None,
                traded=_d(t.get("transaction_date")), disclosed=_d(t.get("disclosure_date")),
                url=t.get("filing_portal") or "", party=t.get("party"),
            )
            row["meta"]["est_price"] = t.get("est_price")
            out.append(row)
        return out

    def fetch(self, db: Session, *, tickers: list[str]) -> list[dict[str, Any]]:
        limit = 250 if self.api_key else 100
        rows = self._rows(self._get(db, "/trades", {"limit": limit}, timedelta(hours=12)))
        per_ticker = 20 if self.api_key else 0  # keyless rows budget is all spent on the feed
        for tk in tickers[:per_ticker]:
            try:
                rows += self._rows(self._get(db, f"/trades/{tk}", {"limit": 25}, timedelta(hours=24)))
            except net.SourceUnavailable as exc:
                logger.info("bargo ticker fetch stopped: %s", exc)
                break
        return rows


class CapitolApiProvider:
    """A self-hosted Capitol API (github.com/crnicholson/capitol-api): House PTRs
    parsed from the Clerk's PDFs."""

    name = "capitol_api"
    label = "Capitol API (self-hosted, House only)"
    attribution = None

    def __init__(self, base_url: str):
        if not base_url:
            raise net.SourceUnavailable("capitol_api needs a base URL (e.g. http://localhost:3000)")
        self.base_url = base_url.rstrip("/")

    def rows_from(self, payload: Any) -> list[dict[str, Any]]:
        items = payload if isinstance(payload, list) else (payload.get("trades") or payload.get("data") or [])
        out = []
        for t in items:
            asset, tx, filing, person = t.get("asset") or {}, t.get("transaction") or {}, t.get("filing") or {}, t.get("person") or {}
            lo, hi = tx.get("amountMin"), tx.get("amountMax")
            if lo is None and hi is None:
                lo, hi = _parse_range(str(tx.get("amount") or ""))
            out.append(make_row(
                provider="capitol_api", member=person.get("name") or f"{person.get('firstName', '')} {person.get('lastName', '')}".strip(),
                chamber="house", state=f"{person.get('state', '')}{person.get('district', '') or ''}", party=person.get("party"),
                ticker=asset.get("ticker"), asset=asset.get("name", ""), ttype=str(tx.get("type") or tx.get("description") or ""),
                lo=float(lo) if lo is not None else None, hi=float(hi) if hi is not None else None,
                traded=_d(tx.get("tradeDate")), disclosed=_d(tx.get("filingDate") or tx.get("notificationDate")),
                url=filing.get("pdfUrl") or "", ext=str(t.get("id") or "") or None,
            ))
        return out

    def fetch(self, db: Session, *, tickers: list[str]) -> list[dict[str, Any]]:
        since = (date.today() - timedelta(days=120)).isoformat()
        data, _ = net.http_get_json(f"{self.base_url}/api/trades", params={"from": since, "limit": 1000, "sort": "filingdate"})
        return self.rows_from(data)


def provider_from_config(cfg: dict[str, Any]) -> CongressProvider | None:
    name = (cfg.get("congress_provider") or "bargo").strip().lower()
    if name in ("", "none", "off", "disabled"):
        return None
    if name == "capitol_api":
        return CapitolApiProvider(cfg.get("capitol_api_url", ""))
    return BargoProvider(api_key=cfg.get("bargo_api_key", ""))
