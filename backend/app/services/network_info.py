"""Best-effort discovery of this machine's public (egress) IP.

The execution key is IP-restricted to the machine the backend runs on, whose
public IP rotates. Surfacing the current egress IP lets the user paste it into
Trading 212's key allowlist (or recognise when it has changed). Best-effort: a
network failure returns ``None`` and the UI degrades gracefully.
"""

from __future__ import annotations

import time

import httpx

# (ip, fetched_at). Short TTL — fresh enough to spot a rotation, cheap enough to
# call on every health poll without hammering the echo service.
_cache: tuple[str | None, float] = (None, 0.0)
_TTL_SECONDS = 30.0

_ECHO_URLS = ("https://api.ipify.org", "https://ifconfig.me/ip")


def get_egress_ip(*, force: bool = False) -> str | None:
    global _cache
    ip, fetched_at = _cache
    if not force and ip and (time.time() - fetched_at) < _TTL_SECONDS:
        return ip

    for url in _ECHO_URLS:
        try:
            with httpx.Client(timeout=4.0) as client:
                resp = client.get(url, headers={"Accept": "text/plain"})
            if resp.status_code == 200:
                value = resp.text.strip()
                # Crude sanity check: looks like an IPv4/IPv6 literal, not HTML.
                if value and len(value) <= 64 and " " not in value and "<" not in value:
                    _cache = (value, time.time())
                    return value
        except httpx.HTTPError:
            continue
    return ip  # stale value (or None) on failure
