"""Leveraged-ETP registry derived live from Trading 212 instrument metadata.

T212 instrument names are self-describing — e.g. "Leverage Shares 3x Long
SanDisk SNDK (Acc)" encodes the issuer, leverage factor, direction, underlying
name, and (often) the underlying ticker. So we never need a hand-curated
products file or ticker-letter guessing: parse the authoritative name.

`classify_leveraged(name)` returns the structured classification for a single
instrument; `build_leveraged_registry()` classifies the whole T212 metadata set
(used by the dashboard + the regime/universe engine). Pure parsing — the only
I/O is the cached metadata fetch in build_leveraged_registry.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# A leverage factor like "3x" / "2 x" — its presence is what marks a leveraged ETP.
_FACTOR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*x\b", re.IGNORECASE)
# Words indicating an inverse/short product (downside exposure).
_INVERSE_RE = re.compile(r"\b(short|inverse|bear|-1x)\b", re.IGNORECASE)
# Known issuer prefixes to strip when deriving the underlying name.
_ISSUER_RE = re.compile(
    r"^(leverage\s+shares|graniteshares|granite\s+shares|wisdomtree|wisdom\s+tree|"
    r"global\s+x|direxion|proshares)\b",
    re.IGNORECASE,
)
# Structural words to strip from the underlying name.
_NOISE_RE = re.compile(r"\b(long|short|daily|leveraged|inverse|bull|bear|etp|etf)\b", re.IGNORECASE)
# Trailing share-class / currency annotations like "(Acc)", "(Dist)", "(GBP)".
_PAREN_RE = re.compile(r"\((?:acc|dist|inc|gbp|usd|eur|1d|1c)\)", re.IGNORECASE)


def classify_leveraged(name: str) -> Optional[dict[str, Any]]:
    """Classify a T212 instrument name as a leveraged ETP, or return None.

    Returns {factor, direction ('long'|'inverse'), underlying_name,
    underlying_ticker} when `name` looks like a leveraged product.
    """
    if not name:
        return None
    fmatch = _FACTOR_RE.search(name)
    if not fmatch:
        return None  # no leverage factor → not a leveraged ETP

    factor = float(fmatch.group(1))
    factor_out: float | int = int(factor) if factor.is_integer() else factor
    direction = "inverse" if _INVERSE_RE.search(name) else "long"

    # Derive the underlying: strip issuer, parens, factor, and structural words.
    rest = _ISSUER_RE.sub("", name).strip()
    rest = _PAREN_RE.sub("", rest)
    rest = _FACTOR_RE.sub("", rest)
    rest = _NOISE_RE.sub("", rest)
    rest = re.sub(r"\s+", " ", rest).strip(" -·")

    # A trailing ALL-CAPS token is usually the underlying ticker (SNDK, CRWV, TSM).
    underlying_ticker: str | None = None
    tokens = rest.split()
    if tokens and re.fullmatch(r"[A-Z]{1,5}", tokens[-1]):
        underlying_ticker = tokens[-1]
        tokens = tokens[:-1]
    underlying_name = " ".join(tokens).strip() or None

    return {
        "factor": factor_out,
        "direction": direction,
        "underlying_name": underlying_name,
        "underlying_ticker": underlying_ticker,
    }


def build_leveraged_registry(instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build {ticker: {name, type, **classification}} for every leveraged ETP
    in a T212 instrument-metadata list. Non-leveraged instruments are skipped.
    """
    registry: dict[str, dict[str, Any]] = {}
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = str(inst.get("name", ""))
        cls = classify_leveraged(name)
        if not cls:
            continue
        ticker = str(inst.get("ticker", "")).strip()
        if not ticker:
            continue
        registry[ticker] = {
            "ticker": ticker,
            "name": name,
            "type": inst.get("type"),
            "currency": inst.get("currencyCode") or inst.get("currency"),
            **cls,
        }
    return registry
