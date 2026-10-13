"""HTTP + cache plumbing for smart-money sources.

* SEC EDGAR: a declared User-Agent with a contact email is mandatory (SEC
  returns 403 without one) and the fair-access limit is 10 req/s — we stay at
  ~5 req/s with a process-wide throttle.
* Every response we may want again goes through the DB-backed
  :class:`DisclosureCache` with a TTL, so refreshes are incremental.
* Sandbox mode never touches the network (fixtures only).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.core.sandbox import is_sandbox
from app.models.smart_money import DisclosureCache

logger = logging.getLogger(__name__)

SEC_MIN_INTERVAL = 0.2  # seconds between SEC requests (≈5 req/s; SEC allows 10)
TIMEOUT = 20


class SourceUnavailable(RuntimeError):
    """A source can't be reached or isn't configured (never fatal)."""


_sec_lock = threading.Lock()
_sec_last = 0.0


def network_allowed() -> bool:
    return not is_sandbox() and os.environ.get("OPENPF_SMART_MONEY_OFFLINE", "").strip() not in {"1", "true", "yes"}


def sec_user_agent(contact: str | None) -> str:
    """SEC wants ``Company/Name contact@email``. Env ``SEC_USER_AGENT`` wins."""
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if ua:
        return ua
    contact = (contact or os.environ.get("SEC_CONTACT_EMAIL", "")).strip()
    if not contact or "@" not in contact:
        raise SourceUnavailable(
            "SEC needs a contact email in the User-Agent — set SEC_CONTACT_EMAIL (or the smart-money "
            "config's sec_contact) to enable Form 4 / 13F."
        )
    return f"OpenPF self-hosted portfolio tool {contact}"


def sec_get(url: str, user_agent: str) -> requests.Response:
    if not network_allowed():
        raise SourceUnavailable("network disabled (sandbox/offline)")
    global _sec_last
    with _sec_lock:
        wait = SEC_MIN_INTERVAL - (time.monotonic() - _sec_last)
        if wait > 0:
            time.sleep(wait)
        _sec_last = time.monotonic()
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    for attempt in range(3):
        resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code == 403:
            raise SourceUnavailable(f"SEC refused the request (403) — check the User-Agent contact: {url}")
        resp.raise_for_status()
        return resp
    raise SourceUnavailable(f"SEC rate-limited or unavailable: {url}")


def http_get_json(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[Any, requests.Response]:
    if not network_allowed():
        raise SourceUnavailable("network disabled (sandbox/offline)")
    try:
        resp = requests.get(url, params=params, headers=headers or {}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SourceUnavailable(f"unreachable: {exc}") from exc
    if resp.status_code == 429:
        raise SourceUnavailable("rate limited (429)")
    if resp.status_code >= 400:
        raise SourceUnavailable(f"HTTP {resp.status_code} from {url}")
    return resp.json(), resp


def http_post_json(url: str, body: Any, headers: dict[str, str] | None = None) -> Any:
    if not network_allowed():
        raise SourceUnavailable("network disabled (sandbox/offline)")
    try:
        resp = requests.post(url, json=body, headers={"Content-Type": "application/json", **(headers or {})}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SourceUnavailable(f"unreachable: {exc}") from exc
    if resp.status_code == 429:
        raise SourceUnavailable("rate limited (429)")
    resp.raise_for_status()
    return resp.json()


# ── DB cache ────────────────────────────────────────────────────────────────

def cache_get(db: Session, key: str, max_age: timedelta | None = None) -> Any | None:
    row = db.get(DisclosureCache, key)
    if row is None:
        return None
    if max_age is not None and row.fetched_at and datetime.utcnow() - row.fetched_at > max_age:
        return None
    try:
        return json.loads(row.payload)
    except (TypeError, ValueError):
        return None


def cache_age(db: Session, key: str) -> timedelta | None:
    row = db.get(DisclosureCache, key)
    if row is None or not row.fetched_at:
        return None
    return datetime.utcnow() - row.fetched_at


def cache_put(db: Session, key: str, value: Any) -> None:
    payload = json.dumps(value, default=str, separators=(",", ":"))
    row = db.get(DisclosureCache, key)
    if row is None:
        db.add(DisclosureCache(key=key, payload=payload, fetched_at=datetime.utcnow()))
    else:
        row.payload = payload
        row.fetched_at = datetime.utcnow()
    db.flush()


def cached_sec_json(db: Session, url: str, ua: str, ttl: timedelta) -> Any:
    hit = cache_get(db, f"sec:{url}", ttl)
    if hit is not None:
        return hit
    data = sec_get(url, ua).json()
    cache_put(db, f"sec:{url}", data)
    return data
