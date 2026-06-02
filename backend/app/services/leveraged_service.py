from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import LeveragedSignal, LeveragedTrade, ScheduledTaskLog, TradeIntent
from app.services.claude_sdk_config import project_root
from app.services.config_store import ConfigStore
from app.services.instrument_cache_service import refresh_instrument_cache
from app.services.leveraged_market import LeveragedMarketError, get_price, get_technicals
from app.services.regime_service import RegimeState, compute_regime
from app.services.signal_attribution import compute_attribution, data_driven_edge
from app.services.telegram_service import send_telegram_notification
from app.services.t212_client import T212Error, build_t212_client, normalize_instrument_code


UK_TZ = ZoneInfo("Europe/London")
logger = logging.getLogger(__name__)


class LeveragedError(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _trade_log_dir() -> Path:
    root = project_root()
    out = root / ".claude" / "runtime" / "memory" / "trades"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _audit_log(entry: dict[str, Any]) -> str:
    now = datetime.now(tz=UK_TZ)
    path = _trade_log_dir() / f"{now.date().isoformat()}.md"
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        content = f"# Leveraged Trades — {now.date().isoformat()}\n\n"

    lines = [
        f"## {entry.get('action', 'event').title()} — {now.strftime('%H:%M:%S %Z')}",
        f"- **Symbol**: {entry.get('symbol', '')}",
        f"- **Direction**: {entry.get('direction', '')}",
        f"- **Quantity**: {entry.get('quantity', 0):.6f}",
        f"- **Price**: {entry.get('price', 0):.4f}",
        f"- **Notional**: {entry.get('notional', 0):.2f}",
    ]
    if entry.get("pnl_value") is not None:
        lines.append(f"- **P&L**: {entry.get('pnl_value', 0):.2f} ({entry.get('pnl_pct', 0)*100:.2f}%)")
    if entry.get("reason"):
        lines.append(f"- **Reason**: {entry.get('reason')}")
    if entry.get("meta"):
        lines.append(f"- **Meta**: `{json.dumps(entry.get('meta'), ensure_ascii=False)[:800]}`")
    lines.append("")

    content += "\n".join(lines)
    path.write_text(content, encoding="utf-8")
    return str(path)


# ── ETP → underlying map ──────────────────────────────────────────────────
# Maps each leveraged/inverse ETP ticker to the UNDERLYING asset's tradable
# proxy symbol (used for technicals) and a stable underlying KEY (used for
# concentration accounting — long and inverse ETPs on the same name share a
# key so we cap total exposure to that underlying regardless of direction).
# NOTE: this static map is a stopgap. The authoritative source is now the live
# T212 instrument metadata (see app.services.leveraged_registry, which derives
# factor/direction/underlying from the instrument name); the regime/universe
# engine will replace this hardcoded map with the derived registry.
#
# IMPORTANT: inverse ("short") ETPs provide DOWNSIDE exposure to the
# underlying and are ISA-only. T212 does NOT support short selling; buying an
# inverse ETP is the sanctioned way to express a bearish view. The leverage
# direction is reflected by ``_is_short_product``; the underlying KEY is the
# same for the long and inverse product on a given name.
@dataclass(frozen=True)
class _Underlying:
    key: str          # stable underlying identity (for concentration accounting)
    proxy: str        # symbol to fetch the underlying's technicals from
    inverse: bool     # True if this ETP is an inverse/short product


_ETP_UNDERLYING: dict[str, _Underlying] = {
    # Index — long
    "3USL": _Underlying("SP500", "SPY", False),
    "3LUS": _Underlying("SP500", "SPY", False),
    "QQQ3": _Underlying("NASDAQ100", "QQQ", False),
    "LQQ3": _Underlying("NASDAQ100", "QQQ", False),
    "3UKL": _Underlying("FTSE100", "^FTSE", False),
    "3GOL": _Underlying("GOLD", "GLD", False),
    "3LGO": _Underlying("GOLD", "GLD", False),
    "3SLV": _Underlying("SILVER", "SLV", False),
    "3BRL": _Underlying("BRENT", "BZ=F", False),
    "3BLR": _Underlying("BRENT", "BZ=F", False),
    "3NGL": _Underlying("NATGAS", "NG=F", False),
    "AI3": _Underlying("AI_INDEX", "AI3.L", False),
    "GPT3": _Underlying("AI_INDEX", "GPT3.L", False),
    # Index — inverse / short
    "3ULS": _Underlying("SP500", "SPY", True),
    "3USS": _Underlying("SP500", "SPY", True),
    "QQQS": _Underlying("NASDAQ100", "QQQ", True),
    "3SGO": _Underlying("GOLD", "GLD", True),
    "3GOS": _Underlying("GOLD", "GLD", True),
    "3BSR": _Underlying("BRENT", "BZ=F", True),
    "3BRS": _Underlying("BRENT", "BZ=F", True),
    "3LGS": _Underlying("NATGAS", "NG=F", True),
    "3NGS": _Underlying("NATGAS", "NG=F", True),
    "3SDE": _Underlying("DAX", "^GDAXI", True),
    "MG3S": _Underlying("MAG7", "QQQ", True),
    "3M7S": _Underlying("MAG7", "QQQ", True),
    "3SSM": _Underlying("SEMIS", "SOXX", True),
    "SC3S": _Underlying("SEMIS", "SOXX", True),
    "UL3S": _Underlying("US30Y", "^TYX", True),
    "3TYS": _Underlying("US10Y", "^TNX", True),
    # Single stock — long
    "3PLT": _Underlying("PLTR", "PLTR", False),
    "3TSM": _Underlying("TSM", "TSM", False),
    "3MSF": _Underlying("MSFT", "MSFT", False),
    "AMD3": _Underlying("AMD", "AMD", False),
    "3NVD": _Underlying("NVDA", "NVDA", False),
    "3CON": _Underlying("COIN", "COIN", False),
    "3BAB": _Underlying("BABA", "BABA", False),
    "3AVG": _Underlying("AVGO", "AVGO", False),
    "3ASM": _Underlying("ASML", "ASML", False),
    "NFL3": _Underlying("NFLX", "NFLX", False),
    # Single stock — inverse / short
    "3STS": _Underlying("TSLA", "TSLA", True),
    "3SNV": _Underlying("NVDA", "NVDA", True),
    "3SMS": _Underlying("MSFT", "MSFT", True),
    "3SAM": _Underlying("AMD", "AMD", True),
    "3SPL": _Underlying("PLTR", "PLTR", True),
    "3SNP": _Underlying("NFLX", "NFLX", True),
    "3SNPL": _Underlying("NFLX", "NFLX", True),
}


def _underlying_for(symbol: str) -> _Underlying | None:
    return _ETP_UNDERLYING.get(str(symbol or "").strip().upper())


# ── Concentration caps (configurable; sane defaults) ────────────────────────
# Fraction of total leveraged book that a single UNDERLYING may represent.
# Mirrors the prose caps in memory/constraints.md (PLTR≈29% / NVDA≈25%).
# Overridable via policy["concentration_caps"] = {"PLTR": 0.29, ...}.
DEFAULT_CONCENTRATION_CAP = 0.40
CONCENTRATION_CAPS: dict[str, float] = {
    "PLTR": 0.29,
    "NVDA": 0.25,
}


def _concentration_cap_for(underlying_key: str, policy: dict[str, Any]) -> float:
    overrides = policy.get("concentration_caps") or {}
    if isinstance(overrides, dict) and underlying_key in overrides:
        try:
            return _clamp(float(overrides[underlying_key]), 0.01, 1.0)
        except (TypeError, ValueError):
            pass
    return CONCENTRATION_CAPS.get(underlying_key, DEFAULT_CONCENTRATION_CAP)


def _concentration_check(
    db: Session,
    *,
    symbol: str,
    add_notional: float,
    policy: dict[str, Any],
) -> str | None:
    """Return a block reason if opening would push an underlying past its cap.

    Computes per-underlying leveraged exposure from OPEN ``LeveragedTrade``
    rows (long + inverse on the same name both count toward that underlying's
    concentration) plus the proposed ``add_notional``. Uses entry notional as
    the exposure proxy — consistent with ``_exposure``/``max_total_exposure``.
    Returns ``None`` if within cap or the ETP's underlying is unknown.
    """
    under = _underlying_for(symbol)
    if under is None:
        # Unknown product → cannot attribute to an underlying; don't block here.
        return None

    open_trades = _open_trades(db)
    existing = float(sum(max(float(t.entry_notional or 0.0), 0.0) for t in open_trades))

    by_underlying = 0.0
    for t in open_trades:
        t_under = _underlying_for(t.symbol)
        if t_under is not None and t_under.key == under.key:
            by_underlying += max(float(t.entry_notional or 0.0), 0.0)

    projected_underlying = by_underlying + max(add_notional, 0.0)
    projected_total = existing + max(add_notional, 0.0)
    if projected_total <= 0:
        return None

    weight = projected_underlying / projected_total
    cap = _concentration_cap_for(under.key, policy)
    if weight > cap:
        return (
            f"concentration cap exceeded for {under.key}: would be "
            f"{weight * 100:.1f}% of leveraged book (cap {cap * 100:.0f}%)"
        )
    return None


def _is_short_product(symbol: str) -> bool:
    value = symbol.strip().upper()
    short_markers = (
        "QQQS",
        "3ULS",
        "3USS",
        "MG3S",
        "3M7S",
        "3SSM",
        "SC3S",
        "UL3S",
        "3TYS",
        "3STS",
        "3SNV",
        "3SMS",
        "3SAM",
        "3SPL",
        "3SGO",
        "3GOS",
        "3BSR",
        "3BRS",
        "3LGS",
        "3NGS",
        "3SDE",
    )
    return value in short_markers or value.startswith("3S")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _dedupe_symbols(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        sym = str(raw or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def _sanitize_close_time(raw: str) -> str:
    value = str(raw or "").strip()
    if not re.match(r"^\d{2}:\d{2}$", value):
        return "15:30"
    hh, mm = value.split(":", 1)
    try:
        h = int(hh)
        m = int(mm)
    except ValueError:
        return "15:30"
    if h < 0 or h > 23 or m < 0 or m > 59:
        return "15:30"
    return f"{h:02d}:{m:02d}"


def _normalize_policy(value: dict[str, Any]) -> dict[str, Any]:
    policy = dict(value or {})
    policy["enabled"] = bool(policy.get("enabled", True))
    policy["auto_execute_enabled"] = bool(policy.get("auto_execute_enabled", False))

    # Leveraged execution is ISA-only by product decision.
    policy["account_kind"] = "stocks_isa"

    policy["per_position_notional"] = _clamp(float(policy.get("per_position_notional", 200.0)), 50.0, 2000.0)
    policy["max_total_exposure"] = _clamp(float(policy.get("max_total_exposure", 600.0)), 100.0, 8000.0)
    policy["max_open_positions"] = int(_clamp(float(policy.get("max_open_positions", 3)), 1, 20))
    policy["take_profit_pct"] = _clamp(float(policy.get("take_profit_pct", 0.08)), 0.01, 0.4)
    policy["stop_loss_pct"] = _clamp(float(policy.get("stop_loss_pct", 0.05)), 0.005, 0.3)
    # Daily session rails (0 = disabled/unlimited). Previously dropped here, so a
    # policy round-trip silently erased them; carry them through and clamp ≥ 0.
    policy["daily_profit_target_gbp"] = max(0.0, float(policy.get("daily_profit_target_gbp", 0.0)))
    policy["daily_loss_limit_gbp"] = max(0.0, float(policy.get("daily_loss_limit_gbp", 0.0)))
    policy["max_daily_trades"] = max(0, int(float(policy.get("max_daily_trades", 0))))
    policy["allow_overnight"] = bool(policy.get("allow_overnight", False))
    policy["close_time_uk"] = _sanitize_close_time(str(policy.get("close_time_uk", "15:30")))
    policy["scan_symbols"] = _dedupe_symbols(list(policy.get("scan_symbols", [])))
    policy["instrument_priority"] = _dedupe_symbols(list(policy.get("instrument_priority", [])))
    if not policy["scan_symbols"]:
        policy["scan_symbols"] = ["3USL", "3ULS", "LQQ3", "QQQS", "3NVD", "3PLT", "SPY", "QQQ"]
    return policy


def get_policy(db: Session) -> dict[str, Any]:
    store = ConfigStore(db)
    policy = _normalize_policy(store.get_leveraged())
    if policy != store.get_leveraged():
        store.set_leveraged(policy)
    return policy


def update_policy(db: Session, patch: dict[str, Any], actor: str = "user") -> dict[str, Any]:
    store = ConfigStore(db)
    current = get_policy(db)
    merged = _normalize_policy({**current, **patch})
    store.set_leveraged(merged)

    _audit_log(
        {
            "action": "policy update",
            "symbol": "RAILS",
            "direction": actor,
            "quantity": 0.0,
            "price": 0.0,
            "notional": 0.0,
            "reason": f"leveraged policy updated by {actor}",
            "meta": merged,
        }
    )
    return merged


def _latest_broker_mode(db: Session) -> Literal["paper", "live"]:
    store = ConfigStore(db)
    broker = store.get_broker()
    mode = str(broker.get("broker_mode", "paper")).lower()
    return "live" if mode == "live" else "paper"


def _open_trades(db: Session) -> list[LeveragedTrade]:
    q = select(LeveragedTrade).where(LeveragedTrade.status == "open").order_by(desc(LeveragedTrade.created_at))
    return list(db.execute(q).scalars().all())


def _exposure(open_trades: list[LeveragedTrade]) -> float:
    return float(sum(max(float(tr.entry_notional or 0.0), 0.0) for tr in open_trades))


def _current_return_pct(trade: LeveragedTrade, current_price: float) -> float:
    entry = float(trade.entry_price or 0.0)
    if entry <= 0:
        return 0.0
    return (current_price / entry) - 1.0


def _uk_day_bounds_utc(now_uk: datetime | None = None) -> tuple[datetime, datetime]:
    """Return [start, end) of *today's* UK trading day expressed as naive UTC.

    Trade timestamps are stored as naive UTC (see ``_utcnow``), so we convert
    the UK midnight boundaries to UTC for comparison.
    """
    now_uk = now_uk or datetime.now(tz=UK_TZ)
    start_uk = now_uk.replace(hour=0, minute=0, second=0, microsecond=0)
    end_uk = start_uk + timedelta(days=1)
    start_utc = start_uk.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_uk.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def _daily_realized_pnl(db: Session) -> float:
    """Sum of realized P&L (£) for leveraged trades CLOSED today (UK session)."""
    start_utc, end_utc = _uk_day_bounds_utc()
    rows = db.execute(
        select(LeveragedTrade).where(
            LeveragedTrade.status == "closed",
            LeveragedTrade.exited_at.is_not(None),
            LeveragedTrade.exited_at >= start_utc,
            LeveragedTrade.exited_at < end_utc,
        )
    ).scalars().all()
    return float(sum(float(r.pnl_value or 0.0) for r in rows))


def _daily_entry_count(db: Session) -> int:
    """Number of leveraged positions ENTERED today (UK session)."""
    start_utc, end_utc = _uk_day_bounds_utc()
    rows = db.execute(
        select(LeveragedTrade).where(
            LeveragedTrade.entered_at >= start_utc,
            LeveragedTrade.entered_at < end_utc,
        )
    ).scalars().all()
    return int(len(rows))


def _daily_rail_block_reason(db: Session, policy: dict[str, Any]) -> str | None:
    """Return a human-readable reason if a daily session rail blocks NEW entries.

    Rails (0 = disabled):
    - realized P&L ≥ daily_profit_target_gbp  → stop opening for the day (target hit)
    - realized P&L ≤ -daily_loss_limit_gbp    → stop opening for the day (loss limit)
    - entries today ≥ max_daily_trades        → stop opening (trade cap)

    These gate ENTRIES only. Exits/monitoring are never blocked (we must always
    be able to cut a losing position).
    """
    target = float(policy.get("daily_profit_target_gbp", 0.0) or 0.0)
    loss_limit = float(policy.get("daily_loss_limit_gbp", 0.0) or 0.0)
    max_trades = int(policy.get("max_daily_trades", 0) or 0)

    if max_trades > 0:
        entries = _daily_entry_count(db)
        if entries >= max_trades:
            return f"daily trade cap reached ({entries}/{max_trades})"

    if target > 0 or loss_limit > 0:
        realized = _daily_realized_pnl(db)
        if target > 0 and realized >= target:
            return f"daily profit target reached (realized £{realized:.2f} ≥ £{target:.2f})"
        if loss_limit > 0 and realized <= -abs(loss_limit):
            return f"daily loss limit hit (realized £{realized:.2f} ≤ -£{loss_limit:.2f})"

    return None


def _signal_risk_flag(confidence: float, expected_edge: float) -> str:
    if confidence >= 0.8 and expected_edge >= 0.01:
        return "high"
    if confidence >= 0.65:
        return "medium"
    return "low"


# How hard a STRONG regime suppresses counter-regime entries. At |score| >=
# _REGIME_GATE_AT a misaligned signal is dropped outright (don't fight a strong
# tape); below that it survives but with a confidence penalty + a conflict flag.
_REGIME_GATE_AT = 0.6
_REGIME_BOOST = 0.06     # max confidence boost for an in-regime signal
_REGIME_PENALTY = 0.15   # max confidence penalty for a counter-regime signal


def _apply_regime(
    confidence: float, direction: str, regime: RegimeState | None
) -> tuple[float, bool, str | None]:
    """Adjust confidence by regime alignment; signal whether to drop the entry.

    Returns (adjusted_confidence, drop, note). A risk-on regime favours LONG
    ETPs and penalises INVERSE; risk-off is the mirror. A neutral or degraded
    regime is a no-op. In a STRONG aligned-against regime the entry is dropped
    so the engine never proposes fighting a decisive tape.
    """
    if regime is None or regime.stale or regime.regime == "neutral":
        return confidence, False, None

    strength = abs(regime.score)
    aligned = regime.favours(direction)
    if aligned:
        return _clamp(confidence + _REGIME_BOOST * strength, 0.35, 0.95), False, (
            f"regime {regime.label} supports {direction}"
        )

    # Counter-regime.
    if strength >= _REGIME_GATE_AT:
        return confidence, True, (
            f"dropped: {direction} fights a strong {regime.label} regime (score {regime.score:+.2f})"
        )
    return _clamp(confidence - _REGIME_PENALTY * strength, 0.35, 0.95), False, (
        f"regime {regime.label} counter to {direction} — confidence trimmed"
    )


def _build_signal(
    symbol: str, policy: dict[str, Any], regime: RegimeState | None = None
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Build a per-product entry signal.

    Direction logic is driven by the UNDERLYING'S technicals, NOT the ETP's own
    price action:

      * A LONG-leveraged ETP is bought when the UNDERLYING is in an uptrend.
      * An INVERSE ("short") ETP is bought when the UNDERLYING is in a
        DOWNTREND — i.e. the inverse ETP is downside exposure to that
        underlying. T212 does not support short selling; buying an inverse ETP
        (ISA-only) is the sanctioned way to express a bearish view. This is NOT
        shorting.

    The previous logic read the *inverse ETP's own chart* and bought it when
    that chart was falling — which is backwards (an inverse ETP falls when its
    underlying rises). We now read the underlying's chart instead.

    For unmapped symbols (e.g. raw SPY/QQQ in a scan list that are not ETPs),
    we fall back to the symbol's own chart and treat it as a plain long.
    """
    is_short = _is_short_product(symbol)
    under = _underlying_for(symbol)

    # The ETP's own price is what we actually trade — always needed for sizing.
    etp_tech = get_technicals(symbol, period="6mo")
    price = float(etp_tech.get("price") or 0.0)

    if under is not None:
        # Drive direction off the UNDERLYING's chart.
        signal_tech = get_technicals(under.proxy, period="6mo")
        underlying_label = under.key
    else:
        # Unmapped: best-effort using the symbol's own chart, plain long only.
        signal_tech = etp_tech
        underlying_label = normalize_instrument_code(symbol)

    rsi = float(signal_tech.get("rsi_14") or 50.0)
    macd = float(signal_tech.get("macd") or 0.0)
    macd_signal = float(signal_tech.get("macd_signal") or 0.0)
    sma20 = float(signal_tech.get("sma_20") or 0.0)
    sma50 = float(signal_tech.get("sma_50") or 0.0)
    u_price = float(signal_tech.get("price") or 0.0)
    trend = str(signal_tech.get("trend_direction") or "mixed")

    # Setups evaluate the UNDERLYING's trend/momentum.
    underlying_uptrend = (
        trend == "uptrend"
        and macd >= macd_signal
        and 44 <= rsi <= 72
        and (sma20 == 0 or u_price >= sma20)
    )
    underlying_downtrend = (
        trend == "downtrend"
        and macd <= macd_signal
        and 28 <= rsi <= 60
        and (sma20 == 0 or u_price <= sma20)
    )

    if is_short:
        # Inverse ETP: buy it only when the UNDERLYING is falling (downside).
        use_signal = underlying_downtrend
        direction = "short"  # downside exposure to the underlying (not a short sale)
    else:
        # Long ETP (or unmapped plain long): buy when the UNDERLYING is rising.
        use_signal = underlying_uptrend
        direction = "long"

    if not use_signal or price <= 0:
        return None, etp_tech

    confidence = 0.55
    confidence += 0.08 if trend in {"uptrend", "downtrend"} else 0.0
    confidence += 0.07 if (underlying_uptrend and 48 <= rsi <= 66) or (underlying_downtrend and 34 <= rsi <= 56) else 0.0
    confidence += 0.07 if abs(macd - macd_signal) > 0 else 0.0
    confidence = _clamp(confidence, 0.35, 0.92)

    # Regime gate: tilt toward in-regime entries, suppress counter-regime ones,
    # and drop outright when fighting a strong tape. `direction` here is
    # 'short' for inverse ETPs / 'long' for long ETPs.
    confidence, regime_drop, regime_note = _apply_regime(confidence, direction, regime)
    if regime_drop:
        return None, etp_tech

    expected_edge = 0.006 + max(0.0, confidence - 0.5) * 0.02
    expected_edge = _clamp(expected_edge, 0.004, 0.03)

    side = "downside" if is_short else "upside"
    rationale = (
        f"underlying={underlying_label} {side}: trend={trend}, rsi={rsi:.1f}, "
        f"macd={macd:.4f} vs signal={macd_signal:.4f}, sma20={sma20:.4f}, sma50={sma50:.4f}"
    )
    if regime_note:
        rationale += f" | {regime_note}"

    # Return the ETP's own technicals as `tech` (so meta reflects the traded
    # instrument); the underlying read lives in the rationale + meta below.
    out_tech = dict(etp_tech)
    out_tech["underlying_key"] = underlying_label
    out_tech["underlying_trend"] = trend
    out_tech["driven_by_underlying"] = under is not None
    if regime is not None:
        out_tech["regime"] = regime.regime
        out_tech["regime_score"] = regime.score
        out_tech["regime_aligned"] = regime.favours(direction)

    return {
        "symbol": symbol,
        "direction": direction,
        "reference_price": price,
        "confidence": confidence,
        "expected_edge": expected_edge,
        "rationale": rationale,
        "risk_flag": _signal_risk_flag(confidence, expected_edge),
    }, out_tech


def scan_signals(db: Session, source_task_id: str | None = None) -> dict[str, Any]:
    policy = get_policy(db)
    if not policy.get("enabled", True):
        return {"created": 0, "signals": [], "policy": policy, "reason": "leveraged disabled"}

    # Daily session rails: once today's realized P&L hits the profit target or
    # loss limit, or the daily trade cap is reached, stop opening new positions.
    # We still build no signals to avoid surfacing actionable proposals the
    # rails forbid. Monitoring/exits are handled elsewhere and never blocked.
    daily_block = _daily_rail_block_reason(db, policy)
    if daily_block:
        return {
            "created": 0,
            "signals": [],
            "policy": policy,
            "reason": f"daily session rail: {daily_block}",
            "daily_rail_blocked": True,
        }

    open_trades = _open_trades(db)
    open_symbols = {row.symbol.upper() for row in open_trades}
    current_exposure = _exposure(open_trades)

    slots_left = max(0, int(policy["max_open_positions"]) - len(open_trades))
    capacity_left = max(0.0, float(policy["max_total_exposure"]) - current_exposure)

    if slots_left <= 0 or capacity_left < 10:
        return {
            "created": 0,
            "signals": [],
            "policy": policy,
            "reason": "no available slot or exposure capacity",
            "open_positions": len(open_trades),
            "open_exposure": current_exposure,
        }

    universe = _dedupe_symbols(list(policy.get("instrument_priority", [])) + list(policy.get("scan_symbols", [])))
    created_rows: list[LeveragedSignal] = []
    failures: list[str] = []

    # One regime read per scan — gates long-vs-inverse for every candidate.
    regime = compute_regime()
    # Attribution from closed trades makes expected_edge data-driven (falls back
    # to the rule constant when there's not enough history yet).
    attribution: dict[str, Any] = {}
    attribution_error: str | None = None
    try:
        attribution = compute_attribution(db)
    except Exception as exc:  # noqa: BLE001 — attribution is advisory, never block a scan
        attribution_error = str(exc)
        logger.warning("leveraged scan: attribution failed, using rule edge: %s", exc)

    notional_per_trade = min(float(policy["per_position_notional"]), capacity_left)

    for symbol in universe:
        if slots_left <= 0:
            break
        if symbol in open_symbols:
            continue

        try:
            signal_data, tech = _build_signal(symbol, policy, regime=regime)
        except LeveragedMarketError as exc:
            failures.append(f"{symbol}: {exc}")
            continue

        if signal_data is None:
            continue

        # Blend the rule-based edge with realized history for this direction.
        signal_data["expected_edge"] = data_driven_edge(
            attribution, signal_data["direction"], float(signal_data["expected_edge"])
        )

        target_notional = min(notional_per_trade, capacity_left)
        if target_notional < 10:
            break

        # Hard-flag (not block) concentration at scan time so a breaching
        # proposal is visible; execute_signal enforces the hard stop.
        concentration_flag = _concentration_check(
            db, symbol=signal_data["symbol"], add_notional=target_notional, policy=policy
        )

        signal_meta: dict[str, Any] = {
            "risk_flag": signal_data["risk_flag"],
            "tech": tech,
        }
        if concentration_flag:
            signal_meta["concentration_flag"] = concentration_flag
            signal_meta["risk_flag"] = "blocked"

        row = LeveragedSignal(
            status="proposed",
            symbol=signal_data["symbol"],
            instrument_code=normalize_instrument_code(signal_data["symbol"]),
            account_kind="stocks_isa",
            direction=signal_data["direction"],
            entry_side="buy",
            target_notional=target_notional,
            reference_price=float(signal_data["reference_price"]),
            stop_loss_pct=float(policy["stop_loss_pct"]),
            take_profit_pct=float(policy["take_profit_pct"]),
            confidence=float(signal_data["confidence"]),
            expected_edge=float(signal_data["expected_edge"]),
            rationale=str(signal_data["rationale"]),
            strategy_tag="leveraged-momentum",
            source_task_id=source_task_id,
            meta=signal_meta,
        )
        db.add(row)
        created_rows.append(row)

        slots_left -= 1
        capacity_left -= target_notional

    db.commit()
    for row in created_rows:
        db.refresh(row)

    executed: list[dict[str, Any]] = []
    if policy.get("auto_execute_enabled", False):
        for row in created_rows:
            try:
                trade = execute_signal(db, row.id, source="auto")
                executed.append({"signal_id": row.id, "trade_id": trade.id, "symbol": trade.symbol})
            except Exception as exc:  # noqa: BLE001
                failures.append(f"auto-exec {row.symbol}: {exc}")

    return {
        "created": len(created_rows),
        "executed": len(executed),
        "signals": [serialize_signal(row) for row in created_rows],
        "executed_items": executed,
        "policy": policy,
        "open_positions": len(open_trades),
        "open_exposure": current_exposure,
        "regime": regime.to_dict(),
        "attribution_error": attribution_error,
        "failures": failures,
    }


def _create_trade_intent(
    db: Session,
    *,
    symbol: str,
    instrument_code: str,
    side: Literal["buy", "sell"],
    quantity: float,
    notional: float,
    confidence: float,
    rationale: str,
    signal_id: str | None,
) -> TradeIntent:
    intent = TradeIntent(
        status="executing",
        broker_mode=_latest_broker_mode(db),
        symbol=symbol,
        instrument_code=instrument_code,
        side=side,
        order_type="market",
        quantity=quantity,
        estimated_notional=notional,
        expected_edge=0.0,
        confidence=confidence,
        risk_score=0.4,
        rationale=rationale,
        meta={
            "account_kind": "stocks_isa",
            "leveraged": True,
            "signal_id": signal_id,
        },
    )
    db.add(intent)
    db.commit()
    db.refresh(intent)
    return intent


def execute_signal(db: Session, signal_id: str, source: str = "manual") -> LeveragedTrade:
    signal = db.get(LeveragedSignal, signal_id)
    if not signal:
        raise LeveragedError(f"signal {signal_id} not found")
    if signal.status not in {"proposed", "approved"}:
        raise LeveragedError(f"signal {signal_id} cannot execute from status {signal.status}")

    policy = get_policy(db)
    if not policy.get("enabled", True):
        raise LeveragedError("leveraged trading is disabled")

    # Hard-stop on daily session rails before opening any new position. This is
    # the single source of truth for the rails — scan/auto-exec both flow here.
    daily_block = _daily_rail_block_reason(db, policy)
    if daily_block:
        raise LeveragedError(f"daily session rail: {daily_block}")

    open_trades = _open_trades(db)
    if len(open_trades) >= int(policy["max_open_positions"]):
        raise LeveragedError("max open leveraged positions reached")

    open_exposure = _exposure(open_trades)
    notional = min(float(signal.target_notional), float(policy["per_position_notional"]))
    if open_exposure + notional > float(policy["max_total_exposure"]):
        raise LeveragedError("max leveraged exposure exceeded")

    # Concentration guardrail: block entries that would push a single
    # underlying above its cap (long + inverse ETPs share the underlying key).
    concentration_block = _concentration_check(
        db, symbol=signal.symbol, add_notional=notional, policy=policy
    )
    if concentration_block:
        raise LeveragedError(concentration_block)

    price_info = get_price(signal.symbol)
    entry_price = float(price_info.get("price") or signal.reference_price or 0.0)
    if entry_price <= 0:
        raise LeveragedError("invalid entry price")

    qty = round(notional / entry_price, 6)
    if qty <= 0:
        raise LeveragedError("quantity resolved to zero")

    broker_mode = _latest_broker_mode(db)
    intent = _create_trade_intent(
        db,
        symbol=signal.symbol,
        instrument_code=signal.instrument_code,
        side="buy",
        quantity=qty,
        notional=notional,
        confidence=float(signal.confidence),
        rationale=f"leveraged entry ({source}): {signal.rationale}",
        signal_id=signal.id,
    )

    broker_order_id = f"paper-{intent.id[:8]}"
    execution_price = entry_price

    if broker_mode == "live":
        store = ConfigStore(db)
        exec_creds = store.get_account_exec_credentials("stocks_isa")
        if not exec_creds.get("exec_enabled", True):
            raise LeveragedError("live execution is disabled for stocks_isa (enable it in Settings)")
        if not exec_creds.get("t212_api_key") or not exec_creds.get("t212_api_secret"):
            raise LeveragedError("no execution key configured for stocks_isa (add it in Settings → Credentials)")
        client = build_t212_client(store, account_kind="stocks_isa", purpose="execute")
        try:
            resp = client.place_market_order(signal.instrument_code, qty)
        except T212Error as exc:
            intent.status = "failed"
            intent.failure_reason = str(exc)
            signal.status = "failed"
            signal.meta = {**(signal.meta or {}), "error": str(exc)}
            db.add_all([intent, signal])
            db.commit()
            raise LeveragedError(f"broker execution failed: {exc}") from exc

        broker_order_id = str(resp.get("id") or resp.get("orderId") or broker_order_id)
        execution_price = float(resp.get("price") or execution_price)

    intent.status = "executed"
    intent.executed_at = _utcnow()
    intent.execution_price = execution_price
    intent.broker_order_id = broker_order_id

    trade = LeveragedTrade(
        signal_id=signal.id,
        status="open",
        symbol=signal.symbol,
        instrument_code=signal.instrument_code,
        account_kind="stocks_isa",
        direction=signal.direction,
        quantity=qty,
        entry_price=execution_price,
        entry_notional=notional,
        entered_at=_utcnow(),
        stop_loss_pct=float(signal.stop_loss_pct),
        take_profit_pct=float(signal.take_profit_pct),
        entry_intent_id=intent.id,
        meta={
            "source": source,
            "risk_rails": {
                "per_position_notional": policy["per_position_notional"],
                "max_total_exposure": policy["max_total_exposure"],
                "max_open_positions": policy["max_open_positions"],
            },
        },
    )
    db.add(trade)
    db.flush()

    signal.status = "executed"
    signal.linked_trade_id = trade.id
    signal.linked_intent_id = intent.id

    db.add_all([signal, intent, trade])
    db.commit()
    db.refresh(trade)

    log_path = _audit_log(
        {
            "action": "entry",
            "symbol": trade.symbol,
            "direction": trade.direction,
            "quantity": trade.quantity,
            "price": trade.entry_price,
            "notional": trade.entry_notional,
            "reason": signal.rationale,
            "meta": {"signal_id": signal.id, "trade_id": trade.id, "mode": broker_mode},
        }
    )

    send_telegram_notification(
        db,
        (
            f"*Archie leveraged entry*\n"
            f"- Symbol: `{trade.symbol}` ({trade.direction})\n"
            f"- Qty: `{trade.quantity:.4f}` @ `{trade.entry_price:.4f}`\n"
            f"- Notional: `{trade.entry_notional:.2f}`\n"
            f"- Mode: `{broker_mode}`\n"
            f"- Log: `{log_path}`"
        ),
    )

    return trade


def close_trade(db: Session, trade_id: str, reason: str = "manual") -> LeveragedTrade:
    trade = db.get(LeveragedTrade, trade_id)
    if not trade:
        raise LeveragedError(f"trade {trade_id} not found")
    if trade.status != "open":
        raise LeveragedError(f"trade {trade_id} is not open")

    broker_mode = _latest_broker_mode(db)
    intent = _create_trade_intent(
        db,
        symbol=trade.symbol,
        instrument_code=trade.instrument_code,
        side="sell",
        quantity=trade.quantity,
        notional=trade.quantity * max(trade.entry_price, 0.0),
        confidence=0.5,
        rationale=f"leveraged exit ({reason})",
        signal_id=trade.signal_id,
    )

    price_info = get_price(trade.symbol)
    exit_price = float(price_info.get("price") or trade.entry_price or 0.0)
    broker_order_id = f"paper-{intent.id[:8]}"

    if broker_mode == "live":
        store = ConfigStore(db)
        client = build_t212_client(store, account_kind="stocks_isa", purpose="execute")
        try:
            resp = client.place_market_order(trade.instrument_code, -abs(float(trade.quantity)))
        except T212Error as exc:
            intent.status = "failed"
            intent.failure_reason = str(exc)
            db.add(intent)
            db.commit()
            raise LeveragedError(f"exit execution failed: {exc}") from exc

        broker_order_id = str(resp.get("id") or resp.get("orderId") or broker_order_id)
        exit_price = float(resp.get("price") or exit_price)

    exit_notional = float(trade.quantity) * max(exit_price, 0.0)
    pnl_value = exit_notional - float(trade.entry_notional)
    pnl_pct = (pnl_value / float(trade.entry_notional)) if float(trade.entry_notional) > 0 else 0.0

    intent.status = "executed"
    intent.executed_at = _utcnow()
    intent.execution_price = exit_price
    intent.broker_order_id = broker_order_id

    trade.status = "closed"
    trade.exit_intent_id = intent.id
    trade.exit_price = exit_price
    trade.exit_notional = exit_notional
    trade.exited_at = _utcnow()
    trade.close_reason = reason
    trade.pnl_value = pnl_value
    trade.pnl_pct = pnl_pct

    db.add_all([trade, intent])
    db.commit()
    db.refresh(trade)

    log_path = _audit_log(
        {
            "action": "exit",
            "symbol": trade.symbol,
            "direction": trade.direction,
            "quantity": trade.quantity,
            "price": exit_price,
            "notional": exit_notional,
            "pnl_value": pnl_value,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "meta": {"trade_id": trade.id, "mode": broker_mode},
        }
    )

    send_telegram_notification(
        db,
        (
            f"*Archie leveraged exit*\n"
            f"- Symbol: `{trade.symbol}`\n"
            f"- Qty: `{trade.quantity:.4f}` @ `{exit_price:.4f}`\n"
            f"- P&L: `{pnl_value:.2f}` ({pnl_pct*100:.2f}%)\n"
            f"- Reason: `{reason}`\n"
            f"- Log: `{log_path}`"
        ),
    )

    return trade


def _should_force_close_for_time(policy: dict[str, Any], now_uk: datetime) -> bool:
    if bool(policy.get("allow_overnight", False)):
        return False
    hh, mm = str(policy.get("close_time_uk", "15:30")).split(":", 1)
    close_t = time(int(hh), int(mm))
    return now_uk.time() >= close_t


def monitor_open_trades(db: Session) -> dict[str, Any]:
    policy = get_policy(db)
    open_trades = _open_trades(db)
    if not open_trades:
        return {"checked": 0, "closed": 0, "items": []}

    now_uk = datetime.now(tz=UK_TZ)
    close_for_time = _should_force_close_for_time(policy, now_uk)

    items: list[dict[str, Any]] = []
    closed = 0
    for trade in open_trades:
        price = float(get_price(trade.symbol).get("price") or trade.entry_price or 0.0)
        ret = _current_return_pct(trade, price)

        reason: str | None = None
        if ret >= float(trade.take_profit_pct or policy["take_profit_pct"]):
            reason = "take-profit"
        elif ret <= -abs(float(trade.stop_loss_pct or policy["stop_loss_pct"])):
            reason = "stop-loss"
        elif close_for_time:
            reason = "time-stop"

        if reason:
            close_trade(db, trade.id, reason=reason)
            closed += 1

        items.append(
            {
                "trade_id": trade.id,
                "symbol": trade.symbol,
                "current_price": price,
                "return_pct": ret,
                "close_reason": reason,
            }
        )

    return {"checked": len(open_trades), "closed": closed, "items": items}


def run_leveraged_cycle(db: Session, source_task_id: str | None = None) -> dict[str, Any]:
    policy = get_policy(db)
    monitor = monitor_open_trades(db)
    scan = scan_signals(db, source_task_id=source_task_id)
    return {
        "ran_at": _utcnow().isoformat(),
        "policy": policy,
        "monitor": monitor,
        "scan": scan,
    }


def serialize_signal(row: LeveragedSignal) -> dict[str, Any]:
    return {
        "id": row.id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "status": row.status,
        "symbol": row.symbol,
        "instrument_code": row.instrument_code,
        "account_kind": row.account_kind,
        "direction": row.direction,
        "entry_side": row.entry_side,
        "target_notional": row.target_notional,
        "reference_price": row.reference_price,
        "stop_loss_pct": row.stop_loss_pct,
        "take_profit_pct": row.take_profit_pct,
        "confidence": row.confidence,
        "expected_edge": row.expected_edge,
        "rationale": row.rationale,
        "strategy_tag": row.strategy_tag,
        "linked_intent_id": row.linked_intent_id,
        "linked_trade_id": row.linked_trade_id,
        "source_task_id": row.source_task_id,
        "meta": row.meta or {},
    }


def serialize_trade(row: LeveragedTrade) -> dict[str, Any]:
    current_price = None
    current_value = None
    current_pnl_value = None
    current_pnl_pct = None
    if row.status == "open":
        try:
            current_price = float(get_price(row.symbol).get("price") or 0.0)
            current_value = float(row.quantity) * current_price
            current_pnl_value = current_value - float(row.entry_notional)
            if float(row.entry_notional) > 0:
                current_pnl_pct = current_pnl_value / float(row.entry_notional)
        except Exception:  # noqa: BLE001
            current_price = None

    return {
        "id": row.id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "signal_id": row.signal_id,
        "status": row.status,
        "symbol": row.symbol,
        "instrument_code": row.instrument_code,
        "account_kind": row.account_kind,
        "direction": row.direction,
        "quantity": row.quantity,
        "entry_price": row.entry_price,
        "entry_notional": row.entry_notional,
        "entered_at": row.entered_at,
        "stop_loss_pct": row.stop_loss_pct,
        "take_profit_pct": row.take_profit_pct,
        "entry_intent_id": row.entry_intent_id,
        "exit_intent_id": row.exit_intent_id,
        "exit_price": row.exit_price,
        "exit_notional": row.exit_notional,
        "exited_at": row.exited_at,
        "close_reason": row.close_reason,
        "pnl_value": row.pnl_value,
        "pnl_pct": row.pnl_pct,
        "meta": row.meta or {},
        "current_price": current_price,
        "current_value": current_value,
        "current_pnl_value": current_pnl_value,
        "current_pnl_pct": current_pnl_pct,
    }


def leveraged_snapshot(db: Session) -> dict[str, Any]:
    policy = get_policy(db)

    open_rows = list(
        db.execute(
            select(LeveragedTrade)
            .where(LeveragedTrade.status == "open")
            .order_by(desc(LeveragedTrade.entered_at))
            .limit(60)
        ).scalars().all()
    )
    closed_rows = list(
        db.execute(
            select(LeveragedTrade)
            .where(LeveragedTrade.status == "closed")
            .order_by(desc(LeveragedTrade.exited_at))
            .limit(120)
        ).scalars().all()
    )
    signal_rows = list(
        db.execute(select(LeveragedSignal).order_by(desc(LeveragedSignal.created_at)).limit(120)).scalars().all()
    )

    open_trades = [serialize_trade(row) for row in open_rows]
    closed_trades = [serialize_trade(row) for row in closed_rows]
    signals = [serialize_signal(row) for row in signal_rows]

    open_exposure = sum(float(row.get("entry_notional") or 0.0) for row in open_trades)
    open_unrealized = sum(float(row.get("current_pnl_value") or 0.0) for row in open_trades)
    closed_realized = sum(float(row.get("pnl_value") or 0.0) for row in closed_trades)

    wins = sum(1 for row in closed_trades if float(row.get("pnl_value") or 0.0) > 0)
    losses = sum(1 for row in closed_trades if float(row.get("pnl_value") or 0.0) < 0)
    total_closed = len(closed_trades)
    win_rate = (wins / total_closed) if total_closed else 0.0

    task_logs = list(
        db.execute(
            select(ScheduledTaskLog)
            .order_by(desc(ScheduledTaskLog.created_at))
            .limit(30)
        ).scalars().all()
    )

    return {
        "policy": policy,
        "summary": {
            "open_positions": len(open_trades),
            "open_exposure": open_exposure,
            "max_total_exposure": float(policy["max_total_exposure"]),
            "open_unrealized_pnl": open_unrealized,
            "closed_realized_pnl": closed_realized,
            "win_rate": win_rate,
            "wins": wins,
            "losses": losses,
            "closed_trades": total_closed,
        },
        "open_trades": open_trades,
        "closed_trades": closed_trades[:50],
        "signals": signals,
        "recent_task_logs": [
            {
                "id": row.id,
                "created_at": row.created_at,
                "task_id": row.task_id,
                "status": row.status,
                "message": row.message,
                "output_path": row.output_path,
                "payload": row.payload or {},
            }
            for row in task_logs
        ],
    }


def refresh_instrument_cache_now(db: Session) -> dict[str, Any]:
    return refresh_instrument_cache(db)
