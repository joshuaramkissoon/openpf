"""Company logos, fetched once by this server and cached on disk.

The browser only ever talks to ``/api/logos/{key}``; it never contacts a logo
service, so no third party learns what the owner holds from page loads. The
server asks a logo service once per company and keeps the answer (a logo, or a
"none" marker for 7 days). See ``docs/logos.md`` for the source choice.

Safety, all enforced here:

* requests go only to :data:`ALLOWED_HOSTS` over HTTPS, redirects included
  (each hop is re-checked; no automatic redirect following);
* a short timeout and a :data:`MAX_BYTES` cap, read as a stream so a large body
  is abandoned, not buffered;
* only PNG / JPEG / WebP are accepted, and the bytes must match the claimed
  type. SVG is never stored or served: upstream is asked for a raster (Wikimedia
  renders SVG logos to PNG thumbnails server-side), and anything else is a miss;
* served with ``nosniff`` and a no-script CSP, so even a mislabelled file
  cannot run in the page;
* when the setting is off, nothing is fetched and nothing is served.

The agent has no tool for this; it is an HTTP route the UI uses, nothing more.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import struct
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote, urljoin, urlparse

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset({
    "api.elbstream.com",       # primary: ticker / ISIN logos (formerly Parqet's logo API)
    "query.wikidata.org",      # fallback: ISIN → Commons logo file
    "commons.wikimedia.org",   # Special:FilePath → rendered PNG thumbnail
    "upload.wikimedia.org",
})
MAX_BYTES = 200 * 1024
TIMEOUT_S = 6.0
MAX_REDIRECTS = 4
MISS_TTL_S = 7 * 24 * 3600
LOGO_PX = 128
USER_AGENT = "OpenPF/2 (self-hosted portfolio app; +https://github.com/joshuaramkissoon/openpf)"

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_EXT_TYPES = {v: k for k, v in _IMAGE_TYPES.items()}

LOGOS_DEFAULT = {"enabled": True}


class LogoFetchError(RuntimeError):
    """The upstream response broke a safety rule (host, size, type)."""


# ── Settings ──────────────────────────────────────────────────────────────


def get_logo_settings(db: Session) -> dict[str, Any]:
    from app.services.config_store import ConfigStore

    return {**LOGOS_DEFAULT, **(ConfigStore(db).get("logos", LOGOS_DEFAULT) or {})}


def set_logo_settings(db: Session, enabled: bool) -> dict[str, Any]:
    from app.services.config_store import ConfigStore

    return ConfigStore(db).set("logos", {**get_logo_settings(db), "enabled": bool(enabled)})


# ── Validation ────────────────────────────────────────────────────────────


def valid_key(key: str) -> bool:
    return bool(_KEY_RE.match(key or ""))


def sniff_image(data: bytes) -> str | None:
    """The image type the bytes actually are (``png``/``jpg``/``webp``), or None.

    Checks the format signature and, for PNG, that the header chunk is sane
    (IHDR first, dimensions 1..2048) — enough to refuse anything that isn't a
    plain raster image, SVG/HTML in particular."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 33 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return "png" if 0 < width <= 2048 and 0 < height <= 2048 else None
    if data.startswith(b"\xff\xd8\xff") and len(data) > 4:
        return "jpg"
    if len(data) >= 16 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


# ── Disk cache ────────────────────────────────────────────────────────────


def cache_dir() -> Path:
    raw = Path(get_settings().logos_data_dir).expanduser()
    if not raw.is_absolute():
        raw = Path(__file__).resolve().parents[2] / raw  # relative to backend/
    raw.mkdir(parents=True, exist_ok=True)
    return raw


def _slot(identity: str) -> str:
    return hashlib.sha256(identity.encode()).hexdigest()[:32]


def cached(identity: str) -> tuple[bytes, str] | None:
    base = cache_dir() / _slot(identity)
    for ext, ctype in _EXT_TYPES.items():
        path = base.with_suffix(f".{ext}")
        if path.is_file():
            return path.read_bytes(), ctype
    return None


def _missed_recently(identity: str) -> bool:
    marker = (cache_dir() / _slot(identity)).with_suffix(".miss")
    try:
        return time.time() - marker.stat().st_mtime < MISS_TTL_S
    except OSError:
        return False


def _store(identity: str, data: bytes, ext: str) -> None:
    base = cache_dir() / _slot(identity)
    tmp = base.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(base.with_suffix(f".{ext}"))
    base.with_suffix(".miss").unlink(missing_ok=True)


def _store_miss(identity: str) -> None:
    (cache_dir() / _slot(identity)).with_suffix(".miss").write_text(identity)


# ── Fetch ─────────────────────────────────────────────────────────────────


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS or parsed.port not in (None, 443):
        raise LogoFetchError(f"host not allowed: {parsed.scheme}://{parsed.hostname}")


def _get(client: httpx.Client, url: str, *, accept: str, max_bytes: int = MAX_BYTES) -> tuple[int, str, bytes]:
    """GET with every hop allow-listed, a byte cap, and no auto-redirects.
    Returns ``(status, content_type, body)``; ``body`` is empty unless 200."""
    for _ in range(MAX_REDIRECTS + 1):
        _check_url(url)
        with client.stream("GET", url, headers={"Accept": accept, "User-Agent": USER_AGENT}) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    return resp.status_code, "", b""
                url = urljoin(url, location)
                continue
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if resp.status_code != 200:
                return resp.status_code, ctype, b""
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise LogoFetchError("too large")
            buf = bytearray()
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise LogoFetchError("too large")
            return 200, ctype, bytes(buf)
    raise LogoFetchError("too many redirects")


def _image(client: httpx.Client, url: str) -> tuple[bytes, str] | None:
    status, ctype, body = _get(client, url, accept="image/png,image/webp,image/jpeg")
    if status != 200:
        return None
    if ctype not in _IMAGE_TYPES:
        raise LogoFetchError(f"not an allowed image type: {ctype or 'none'}")
    kind = sniff_image(body)
    if kind is None or kind != _IMAGE_TYPES[ctype]:
        raise LogoFetchError("body is not the image it claims to be")
    return body, kind


def _from_elbstream(client: httpx.Client, id_type: str, ident: str) -> tuple[bytes, str] | None:
    return _image(client, f"https://api.elbstream.com/logos/{id_type}/{quote(ident, safe='')}?format=png&size={LOGO_PX}")


def _from_wikidata(client: httpx.Client, isin: str) -> tuple[bytes, str] | None:
    """ISIN → Wikidata item → its ``logo image`` (P154) on Commons, rendered to
    a PNG thumbnail by Wikimedia (never the raw SVG)."""
    query = f'SELECT ?logo WHERE {{ ?item wdt:P946 "{isin}"; wdt:P154 ?logo. }} LIMIT 1'
    status, _, body = _get(
        client,
        f"https://query.wikidata.org/sparql?format=json&query={quote(query)}",
        accept="application/sparql-results+json",
        max_bytes=64 * 1024,
    )
    if status != 200:
        return None
    try:
        rows = json.loads(body)["results"]["bindings"]
        logo = rows[0]["logo"]["value"] if rows else ""
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    marker = "/Special:FilePath/"
    if marker not in logo:
        return None
    filename = logo.split(marker, 1)[1]
    return _image(client, f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename}?width={LOGO_PX}")


def fetch_logo(identity: dict[str, str | None], client: httpx.Client | None = None) -> tuple[bytes, str] | None:
    """Try each source in order; the first image wins. A source that breaks a
    safety rule is skipped (logged), not trusted."""
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S, follow_redirects=False)
    isin, symbol = identity.get("isin"), identity.get("symbol")
    attempts: list[Callable[[], tuple[bytes, str] | None]] = []
    if isin:
        attempts.append(lambda: _from_elbstream(client, "isin", isin))
    if symbol:
        attempts.append(lambda: _from_elbstream(client, "symbol", symbol))
    if isin:
        attempts.append(lambda: _from_wikidata(client, isin))
    try:
        for attempt in attempts:
            try:
                got = attempt()
            except (LogoFetchError, httpx.HTTPError) as exc:
                logger.info("logo source refused for %s: %s", identity.get("label"), exc)
                continue
            if got:
                return got
        return None
    finally:
        if own:
            client.close()


# ── Key → identity ────────────────────────────────────────────────────────

_identity_cache: dict[str, tuple[float, dict[str, str | None]]] = {}
_IDENTITY_TTL_S = 3600.0


def _held_codes(db: Session) -> dict[str, str]:
    """``{ticker the UI shows: T212 instrument code}`` for the latest holdings,
    so a bare ``BA`` the owner holds in London resolves to BAE, not Boeing."""
    from sqlalchemy import desc, select

    from app.models.entities import PositionSnapshot
    from app.services.portfolio_service import _instrument_meta_map, display_ticker

    latest = db.execute(select(PositionSnapshot.fetched_at).order_by(desc(PositionSnapshot.fetched_at)).limit(1)).scalar_one_or_none()
    if latest is None:
        return {}
    meta = _instrument_meta_map(db)
    out: dict[str, str] = {}
    for p in db.execute(select(PositionSnapshot).where(PositionSnapshot.fetched_at == latest)).scalars():
        code = (p.instrument_code or "").upper()
        if not code.endswith("_EQ"):
            continue
        shown = display_ticker(p.ticker, (meta.get(code) or {}).get("short_name"))
        for k in {shown, (p.ticker or "").upper()} - {""}:
            out.setdefault(k, code)
    return out


def _from_t212_code(db: Session, code: str) -> dict[str, str | None]:
    from app.services.portfolio_service import _instrument_meta_map, market_yfinance_ticker

    meta = _instrument_meta_map(db).get(code.upper()) or {}
    original = meta.get("ticker") or code
    symbol = market_yfinance_ticker(original, meta.get("short_name"), meta.get("currency"))
    return {"isin": (meta.get("isin") or "").upper() or None, "symbol": symbol}


def resolve_identity(db: Session, key: str) -> dict[str, str | None]:
    """What to ask the logo service for: ``{isin, symbol, label}``.

    Accepts an ISIN, a T212 code (``BAl_EQ``), a Yahoo-style symbol (``BA.L``)
    or a bare ticker. ISIN comes from the broker's instrument list when it's
    known; the symbol is Yahoo-style so the venue is never lost."""
    now = time.time()
    hit = _identity_cache.get(key)
    if hit and now - hit[0] < _IDENTITY_TTL_S:
        return hit[1]

    from app.services.instrument_names import find_listing, split_symbol

    upper = key.strip().upper()
    ident: dict[str, str | None] = {"isin": None, "symbol": None}
    try:
        if _ISIN_RE.match(upper):
            ident = {"isin": upper, "symbol": None}
        elif upper.endswith("_EQ"):
            ident = _from_t212_code(db, key.strip())
        else:
            held = _held_codes(db).get(upper) if not split_symbol(upper)[1] else None
            if held:
                ident = _from_t212_code(db, held)
            else:
                listing = find_listing(db, upper)
                ident = {"isin": ((listing or {}).get("isin") or "").upper() or None, "symbol": upper}
    except Exception:  # noqa: BLE001 — resolution is best-effort; the symbol alone still works
        logger.debug("logo identity lookup failed for %s", key, exc_info=True)
        ident = {"isin": None, "symbol": upper}
    ident["label"] = key
    _identity_cache[key] = (now, ident)
    return ident


def _identity_key(ident: dict[str, str | None]) -> str:
    return f"isin:{ident['isin']}" if ident.get("isin") else f"symbol:{ident.get('symbol') or ''}"


# ── Public entry points ───────────────────────────────────────────────────

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(slot: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(slot, threading.Lock())


def get_logo(
    db: Session,
    key: str,
    *,
    fetcher: Callable[[dict[str, str | None]], tuple[bytes, str] | None] | None = None,
) -> tuple[bytes, str] | None:
    """``(bytes, content_type)`` for a key, or None (→ the UI's monogram).

    Off → None, without touching cache or network (the UI shows monograms).
    On → cache first; the network only when the key hasn't missed in the last
    7 days and resolves to something to ask for."""
    if not valid_key(key) or not get_logo_settings(db).get("enabled", True):
        return None
    ident = resolve_identity(db, key)
    if not ident.get("isin") and not ident.get("symbol"):
        return None
    identity = _identity_key(ident)
    hit = cached(identity)
    if hit:
        return hit
    if _missed_recently(identity):
        return None
    with _lock_for(identity):
        hit = cached(identity)  # another request may have just fetched it
        if hit:
            return hit
        if _missed_recently(identity):
            return None
        try:
            got = (fetcher or fetch_logo)(ident)
        except Exception:  # noqa: BLE001 — a network blip is not a 7-day miss
            logger.info("logo fetch failed for %s", key, exc_info=True)
            return None
        if not got:
            _store_miss(identity)
            return None
        data, ext = got
        _store(identity, data, ext)
        return data, _EXT_TYPES[ext]


# ── Pre-warm ──────────────────────────────────────────────────────────────

_prewarm_running = threading.Lock()
PREWARM_INTERVAL_S = 0.5
PREWARM_MAX = 80


def holding_keys(db: Session) -> list[str]:
    """The keys the holdings list will ask for (``display_ticker``), equities only."""
    return sorted(_held_codes(db))


def prewarm(keys: Iterable[str], *, interval_s: float = PREWARM_INTERVAL_S, fetcher=None) -> int:
    """Fetch logos for ``keys`` one at a time, ``interval_s`` apart. Returns
    how many were fetched from the network. Skips silently when off."""
    from app.core.database import SessionLocal

    fetched = 0
    with SessionLocal() as db:
        if not get_logo_settings(db).get("enabled", True):
            return 0
        for key in list(keys)[:PREWARM_MAX]:
            if not valid_key(key):
                continue
            ident = resolve_identity(db, key)
            identity = _identity_key(ident)
            if (not ident.get("isin") and not ident.get("symbol")) or cached(identity) or _missed_recently(identity):
                continue
            get_logo(db, key, fetcher=fetcher)
            fetched += 1
            time.sleep(interval_s)
    return fetched


def prewarm_holdings_async() -> None:
    """After a portfolio sync: warm the holdings' logos in a background thread.
    One run at a time; never raises into the sync."""
    if not get_settings().logos_prewarm or not _prewarm_running.acquire(blocking=False):
        return

    def run() -> None:
        try:
            from app.core.database import SessionLocal

            with SessionLocal() as db:
                keys = holding_keys(db)
            n = prewarm(keys)
            if n:
                logger.info("pre-warmed %d logo(s)", n)
        except Exception:  # noqa: BLE001
            logger.debug("logo pre-warm failed", exc_info=True)
        finally:
            _prewarm_running.release()

    threading.Thread(target=run, name="logo-prewarm", daemon=True).start()
