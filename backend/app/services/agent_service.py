from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import AgentRun, Thesis, TradeIntent
from app.services.analytics import signal_for_symbol
from app.services.claude_agent_runtime import run_claude_analyst_cycle
from app.services.config_store import ConfigStore
from app.services.execution_service import approve_intent, execute_intent, supersede_open_proposals
from app.services.market_data import fetch_history
from app.services.portfolio_service import get_portfolio_snapshot
from app.services.regime_service import compute_regime
from app.services.t212_client import normalize_instrument_code


@dataclass
class ProposedIntent:
    symbol: str
    instrument_code: str
    side: str
    order_type: str
    quantity: float
    estimated_notional: float
    expected_edge: float
    confidence: float
    risk_score: float
    rationale: str
    metadata: dict[str, Any]


def _dedupe_and_route(ideas: list[ProposedIntent], snapshot: dict[str, Any]) -> list[ProposedIntent]:
    """Collapse per-account duplicates and route each intent to one account.

    A name held in both Invest and ISA otherwise yields one near-identical
    proposal per account. Keep a single representative per (symbol, side) — the
    highest-confidence one — then route it:
      • BUY  → prefer the ISA while it has free cash for the order (consumed
               greedily across buys), else Invest. Buys can only use cash that's
               actually in the account, so free cash is the real constraint —
               not the £20k allowance (a *deposit* cap, tracked elsewhere).
      • SELL → the account that holds the most of the name (so it's executable).
    """
    isa_cash = next(
        (float(a.get("free_cash") or 0.0) for a in snapshot.get("accounts", [])
         if a.get("account_kind") == "stocks_isa"),
        0.0,
    )
    held: dict[tuple[str, str], float] = {}
    for p in snapshot.get("positions", []):
        key = (str(p.get("ticker", "")).upper(), p.get("account_kind"))
        held[key] = held.get(key, 0.0) + float(p.get("value") or 0.0)

    best: dict[tuple[str, str], ProposedIntent] = {}
    order: list[tuple[str, str]] = []
    for idea in ideas:
        k = (idea.symbol.upper(), idea.side)
        if k not in best:
            order.append(k)
            best[k] = idea
        elif idea.confidence > best[k].confidence:
            best[k] = idea

    routed: list[ProposedIntent] = []
    isa_remaining = isa_cash
    for symbol, side in order:
        idea = best[(symbol, side)]
        if side == "buy":
            if isa_remaining >= idea.estimated_notional:
                acct = "stocks_isa"
                isa_remaining -= idea.estimated_notional
            else:
                acct = "invest"
        else:
            isa_h = held.get((symbol, "stocks_isa"), 0.0)
            inv_h = held.get((symbol, "invest"), 0.0)
            acct = "stocks_isa" if isa_h >= inv_h else "invest"
        idea.metadata = {**(idea.metadata or {}), "account_kind": acct}
        routed.append(idea)
    return routed


def _market_regime() -> str:
    """Human-readable regime line for the agent run summary.

    Delegates to the shared `regime_service` (SPY/QQQ vs SMA50/200 + VIX) so the
    agent, the leveraged engine, and the dashboard all read the SAME regime,
    instead of this function's old SPY-only heuristic.
    """
    try:
        r = compute_regime()
        bits = [r.label, f"score {r.score:+.2f}"]
        if r.vix is not None:
            bits.append(f"VIX {r.vix:.1f} ({r.vix_state})")
        return " · ".join(bits)
    except Exception:  # noqa: BLE001 — never block a run on regime
        return "neutral"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _size_add(
    *,
    total_book: float,
    current_weight: float,
    max_weight: float,
    momentum: float,
    trend: float,
    rsi: float,
    volatility: float,
    max_single: float,
    free_cash: float,
    min_trade: float = 25.0,
) -> float:
    """Conviction-, position-, and risk-aware size for a 'buy' add.

    Sizes a fraction of the per-order limit by a 0–1 score, so each add reflects
    the specific name and holding rather than a flat % of book:
      • conviction — momentum, trend, and RSI headroom (the stock's signal),
      • room       — how far the current weight sits below the cap (your position),
      • vol_scale  — risk-parity-ish: higher volatility → smaller (the stock's risk).
    Never exceeds the configured single-order cap or available cash; returns 0
    when there's no room or the result is too small to be worth proposing."""
    gap = max_weight - current_weight
    if gap <= 0 or total_book <= 0 or max_weight <= 0:
        return 0.0

    mom = _clamp(momentum / 0.30, 0.0, 1.0)            # 30% 3m momentum ≈ full
    tr = _clamp(trend, 0.0, 1.0)
    rsi_room = _clamp((75.0 - rsi) / 25.0, 0.0, 1.0)   # fades as RSI → overbought
    conviction = 0.5 * mom + 0.4 * tr + 0.1 * rsi_room

    vol_scale = _clamp(0.25 / max(volatility, 0.05), 0.3, 1.0)
    room_factor = _clamp(gap / max_weight, 0.0, 1.0)

    score = conviction * vol_scale * room_factor
    notional = min(max_single * score, free_cash)
    return round(notional, 2) if notional >= min_trade else 0.0


def _safe_qty(notional: float, price: float) -> float:
    if price <= 0:
        return 0.0
    return round(notional / price, 4)


def _position_intents(snapshot: dict[str, Any], risk: dict[str, Any]) -> list[ProposedIntent]:
    positions: list[dict[str, Any]] = snapshot["positions"]
    total = float(snapshot["account"]["total"] or 0.0)
    max_weight = float(risk.get("max_position_weight", 0.25))
    max_single = float(risk.get("max_single_order_notional", 500.0))
    free_cash = float(snapshot.get("metrics", {}).get("free_cash", 0.0) or 0.0)
    ideas: list[ProposedIntent] = []

    for p in positions:
        account_kind = str(p.get("account_kind", "invest"))
        symbol = str(p["ticker"]).upper()
        code = str(p["instrument_code"]).upper()
        weight = float(p["weight"])
        price = float(p["current_price"] or 0.0)
        momentum = float(p["momentum_63d"] or 0.0)
        trend = float(p["trend_score"] or 0.0)
        rsi = float(p["rsi_14"] or 50.0)
        vol = float(p.get("volatility_30d") or 0.25)

        if weight > max_weight + 0.02 and price > 0:
            excess = (weight - max_weight) * total
            notional = max(excess, total * 0.015)
            qty = _safe_qty(notional, price)
            if qty > 0:
                ideas.append(
                    ProposedIntent(
                        symbol=symbol,
                        instrument_code=code,
                        side="sell",
                        order_type="market",
                        quantity=qty,
                        estimated_notional=qty * price,
                        expected_edge=0.012,
                        confidence=0.79,
                        risk_score=min(1.0, 0.55 + weight),
                        rationale=f"Trim concentration: weight {weight:.1%} exceeds cap {max_weight:.1%}.",
                        metadata={"trigger": "concentration-trim", "weight": weight, "account_kind": account_kind},
                    )
                )

        if momentum < -0.12 and trend < 0.4 and price > 0:
            notional = max(total * 0.01, float(p["value"]) * 0.2)
            qty = min(float(p["quantity"]), _safe_qty(notional, price))
            if qty > 0:
                confidence = min(0.92, 0.58 + abs(momentum))
                ideas.append(
                    ProposedIntent(
                        symbol=symbol,
                        instrument_code=code,
                        side="sell",
                        order_type="market",
                        quantity=qty,
                        estimated_notional=qty * price,
                        expected_edge=0.009,
                        confidence=confidence,
                        risk_score=min(1.0, vol + 0.35),
                        rationale=f"Downtrend defense: 3m momentum {momentum:.1%}, trend score {trend:.2f}.",
                        metadata={"trigger": "trend-breakdown", "momentum_63d": momentum, "trend_score": trend, "account_kind": account_kind},
                    )
                )

        if momentum > 0.1 and trend > 0.7 and rsi < 72 and weight < max_weight * 0.75 and price > 0:
            notional = _size_add(
                total_book=total, current_weight=weight, max_weight=max_weight,
                momentum=momentum, trend=trend, rsi=rsi, volatility=vol,
                max_single=max_single, free_cash=free_cash,
            )
            qty = _safe_qty(notional, price)
            if qty > 0:
                confidence = min(0.92, 0.55 + momentum + trend / 4)
                ideas.append(
                    ProposedIntent(
                        symbol=symbol,
                        instrument_code=code,
                        side="buy",
                        order_type="market",
                        quantity=qty,
                        estimated_notional=qty * price,
                        expected_edge=0.014,
                        confidence=confidence,
                        risk_score=min(1.0, vol),
                        rationale=(
                            f"Add winner: trend score {trend:.2f}, momentum {momentum:.1%}, "
                            f"current weight {weight:.1%} below cap."
                        ),
                        metadata={"trigger": "trend-follow-add", "momentum_63d": momentum, "trend_score": trend, "account_kind": account_kind},
                    )
                )

        if rsi > 80 and momentum > 0.14 and price > 0:
            notional = min(float(p["value"]) * 0.12, total * 0.01)
            qty = min(float(p["quantity"]), _safe_qty(notional, price))
            if qty > 0:
                ideas.append(
                    ProposedIntent(
                        symbol=symbol,
                        instrument_code=code,
                        side="sell",
                        order_type="market",
                        quantity=qty,
                        estimated_notional=qty * price,
                        expected_edge=0.006,
                        confidence=0.67,
                        risk_score=min(1.0, vol + 0.2),
                        rationale=f"Take-profit rebalance: RSI {rsi:.1f} indicates stretched move.",
                        metadata={"trigger": "rsi-take-profit", "rsi": rsi, "account_kind": account_kind},
                )
                )

    return ideas


def _watchlist_intents(snapshot: dict[str, Any], watchlist: list[str], risk: dict[str, Any]) -> list[ProposedIntent]:
    held = {str(row["ticker"]).upper() for row in snapshot["positions"]}
    total = float(snapshot["account"]["total"] or 0.0)
    max_weight = float(risk.get("max_position_weight", 0.25))
    max_single = float(risk.get("max_single_order_notional", 500.0))
    free_cash = float(snapshot.get("metrics", {}).get("free_cash", 0.0) or 0.0)
    ideas: list[ProposedIntent] = []
    default_account = "invest"
    if snapshot.get("accounts"):
        ranked = sorted(snapshot["accounts"], key=lambda x: float(x.get("free_cash", 0.0)), reverse=True)
        if ranked:
            default_account = str(ranked[0].get("account_kind", "invest"))

    for symbol in watchlist:
        clean = symbol.strip().upper()
        if not clean or clean in held:
            continue

        signal = signal_for_symbol(clean)
        momentum = signal.momentum_63d
        trend = signal.trend_score
        rsi = signal.rsi_14
        if momentum is None or trend is None or rsi is None:
            continue

        if momentum > 0.12 and trend > 0.75 and rsi < 70:
            try:
                from app.services.market_data import fetch_history

                history = fetch_history(clean, lookback_days=60)
                price = float(history["close"].iloc[-1])
            except Exception:
                continue

            # New position (current weight 0); size by conviction/vol against the
            # per-order cap, same model as adds, but start at half the room to cap.
            vol = float(getattr(signal, "volatility_30d", None) or 0.30)
            notional = 0.5 * _size_add(
                total_book=total, current_weight=0.0, max_weight=max_weight,
                momentum=momentum, trend=trend, rsi=rsi, volatility=vol,
                max_single=max_single, free_cash=free_cash,
            )
            qty = _safe_qty(notional, price) if notional > 0 else 0.0
            if qty <= 0:
                continue

            edge = min(0.03, 0.01 + (momentum - 0.12) * 0.6 + (trend - 0.75) * 0.02)
            confidence = min(0.9, 0.57 + momentum + (trend - 0.5) * 0.2)
            ideas.append(
                ProposedIntent(
                    symbol=clean,
                    instrument_code=normalize_instrument_code(clean),
                    side="buy",
                    order_type="market",
                    quantity=qty,
                    estimated_notional=qty * price,
                    expected_edge=edge,
                    confidence=confidence,
                    risk_score=0.48,
                    rationale=(
                        f"Breakout candidate from watchlist: momentum {momentum:.1%}, trend {trend:.2f}, "
                        f"RSI {rsi:.1f}."
                    ),
                    metadata={
                        "trigger": "watchlist-breakout",
                        "momentum_63d": momentum,
                        "trend_score": trend,
                        "rsi_14": rsi,
                        "account_kind": default_account,
                    },
                )
            )

    return ideas


def _claude_payload_to_intents(snapshot: dict[str, Any], payload_intents: list[dict[str, Any]], risk: dict[str, Any]) -> list[ProposedIntent]:
    if not payload_intents:
        return []

    max_single = float(risk.get("max_single_order_notional", 500.0))
    if max_single <= 0:
        max_single = 500.0

    price_by_symbol: dict[str, tuple[float, str]] = {}
    for row in snapshot.get("positions", []):
        symbol = str(row.get("ticker", "")).upper()
        if symbol and float(row.get("current_price", 0.0) or 0.0) > 0:
            account_kind = str(row.get("account_kind", "invest"))
            price_by_symbol[symbol] = (float(row.get("current_price", 0.0) or 0.0), account_kind)

    ideas: list[ProposedIntent] = []

    def _to_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    for raw in payload_intents:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol", "")).strip().upper()
        if not symbol:
            continue

        side = str(raw.get("side", "")).strip().lower()
        if side not in {"buy", "sell"}:
            continue

        account_kind = str(raw.get("account_kind", "invest")).strip().lower()
        if account_kind not in {"invest", "stocks_isa"}:
            account_kind = "invest"

        target_notional = _to_float(raw.get("target_notional"), max_single * 0.35)
        target_notional = max(25.0, min(target_notional, max_single))

        market_price = 0.0
        if symbol in price_by_symbol:
            market_price, inferred_kind = price_by_symbol[symbol]
            if raw.get("account_kind") is None:
                account_kind = inferred_kind
        else:
            try:
                history = fetch_history(symbol, lookback_days=60)
                market_price = float(history["close"].iloc[-1])
            except Exception:
                market_price = 0.0

        qty = _safe_qty(target_notional, market_price)
        if qty <= 0:
            continue

        confidence = max(0.0, min(1.0, _to_float(raw.get("confidence"), 0.6)))
        expected_edge = max(0.0, min(1.0, _to_float(raw.get("expected_edge"), 0.01)))
        risk_score = max(0.0, min(1.0, _to_float(raw.get("risk_score"), 0.45)))
        order_type = str(raw.get("order_type", "market")).strip().lower() or "market"
        rationale = str(raw.get("rationale") or raw.get("thesis") or f"Claude signal for {symbol}").strip()

        ideas.append(
            ProposedIntent(
                symbol=symbol,
                instrument_code=normalize_instrument_code(symbol),
                side=side,
                order_type=order_type,
                quantity=qty,
                estimated_notional=qty * market_price,
                expected_edge=expected_edge,
                confidence=confidence,
                risk_score=risk_score,
                rationale=rationale,
                metadata={
                    "trigger": "claude-runtime",
                    "account_kind": account_kind,
                    "source_payload": raw,
                },
            )
        )

    return ideas


def _format_summary(
    snapshot: dict[str, Any],
    market_regime: str,
    ideas: list[ProposedIntent],
) -> tuple[str, float]:
    metrics = snapshot["metrics"]
    account = snapshot["account"]

    concentration_penalty = min(0.35, metrics["concentration_hhi"])
    volatility_penalty = min(0.25, metrics["estimated_volatility"] / 2)
    cash_bonus = min(0.2, metrics["cash_ratio"] / 2)

    score = max(0.0, min(1.0, 0.6 + cash_bonus - concentration_penalty - volatility_penalty))

    lines = [
        f"## Agent Brief - {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"- Market regime: **{market_regime}**",
        f"- Portfolio score: **{score * 100:.1f}/100**",
        f"- Total equity: **{account['currency']} {account['total']:,.2f}**",
        f"- Cash ratio: **{metrics['cash_ratio']:.1%}**",
        f"- Concentration (HHI): **{metrics['concentration_hhi']:.3f}**",
        f"- Estimated beta: **{metrics['estimated_beta']:.2f}**",
        "",
    ]

    if not ideas:
        lines.append("No high-conviction trade intents right now. Hold and monitor risk drift.")
    else:
        lines.append("### Top actionable intents")
        for idea in ideas[:5]:
            lines.append(
                f"- **{idea.side.upper()} {idea.symbol}** | edge {idea.expected_edge:.2%} | "
                f"confidence {idea.confidence:.0%} | {idea.rationale}"
            )

    return "\n".join(lines), score


def run_agent(db: Session, *, include_watchlist: bool = True, execute_auto: bool = False) -> dict[str, Any]:
    config = ConfigStore(db)
    risk = config.get_risk()
    broker = config.get_broker()
    watchlist = config.get_watchlist().get("symbols", [])

    # Deterministic trims + the summary need real prices/weights; the LLM cycle
    # gets a price-stripped view (it must pull live prices via the marketdata MCP
    # rather than trust cached snapshot prices). So build both.
    snapshot = get_portfolio_snapshot(db)
    llm_snapshot = get_portfolio_snapshot(db, strip_prices=True)
    market_regime = _market_regime()

    ideas: list[ProposedIntent] = []
    summary_override: str | None = None
    claude_result = run_claude_analyst_cycle(llm_snapshot, watchlist, risk)

    if claude_result and claude_result.get("ok"):
        summary_override = str(claude_result.get("summary_markdown") or "").strip() or None
        ideas.extend(_claude_payload_to_intents(snapshot, claude_result.get("intents", []), risk))

    if not ideas:
        ideas = _position_intents(snapshot, risk)
        if include_watchlist:
            ideas.extend(_watchlist_intents(snapshot, watchlist, risk))

    # Collapse per-account duplicates (a name held in both accounts) into one
    # proposal and route it (ISA-first by free cash; sells to the holding account).
    ideas = _dedupe_and_route(ideas, snapshot)

    ideas = sorted(
        ideas,
        key=lambda i: (i.expected_edge * i.confidence) - (i.risk_score * 0.2),
        reverse=True,
    )[:12]

    summary, score = _format_summary(snapshot, market_regime, ideas)
    if summary_override:
        summary = summary_override

    run = AgentRun(
        status="completed",
        summary_markdown=summary,
        market_regime=market_regime,
        portfolio_score=score,
        meta={
            "positions": len(snapshot["positions"]),
            "ideas": len(ideas),
            "cash_ratio": snapshot["metrics"]["cash_ratio"],
            "provider": "claude" if claude_result and claude_result.get("ok") else "rules",
            "theses": (claude_result or {}).get("theses", []),
            "research": (claude_result or {}).get("research", {}),
            "claude_error": None if not claude_result else claude_result.get("error"),
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # A fresh batch of proposals supersedes the previous run's un-acted ones, so
    # the queue reflects the latest run instead of stacking duplicate batches.
    # Guarded: an empty run doesn't wipe still-relevant proposals.
    if ideas:
        supersede_open_proposals(db)

    created = 0
    for idea in ideas:
        intent = TradeIntent(
            run_id=run.id,
            status="proposed",
            broker_mode=broker.get("broker_mode", "paper"),
            symbol=idea.symbol,
            instrument_code=idea.instrument_code,
            side=idea.side,
            order_type=idea.order_type,
            quantity=idea.quantity,
            estimated_notional=idea.estimated_notional,
            expected_edge=idea.expected_edge,
            confidence=idea.confidence,
            risk_score=idea.risk_score,
            rationale=idea.rationale,
            meta=idea.metadata,
        )
        db.add(intent)
        created += 1

    theses_created = 0
    if claude_result and isinstance(claude_result.get("theses"), list):
        for raw in claude_result.get("theses", []):
            if not isinstance(raw, dict):
                continue
            symbol = str(raw.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            account_kind = str(raw.get("account_kind", "all")).strip().lower() or "all"
            if account_kind not in {"all", "invest", "stocks_isa"}:
                account_kind = "all"

            confidence_raw = raw.get("confidence", 0.55)
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = 0.55
            confidence = max(0.0, min(1.0, confidence))

            catalysts_raw = raw.get("catalysts", [])
            catalysts = [str(item).strip() for item in catalysts_raw if str(item).strip()] if isinstance(catalysts_raw, list) else []

            thesis_row = Thesis(
                source_run_id=run.id,
                symbol=symbol,
                account_kind=account_kind,
                title=str(raw.get("title", f"{symbol} thesis")).strip()[:240],
                thesis=str(raw.get("thesis", "")).strip(),
                catalysts=catalysts,
                invalidation=str(raw.get("invalidation", "")).strip(),
                confidence=confidence,
                status="active",
                meta={"provider": "claude", "raw": raw},
            )
            db.add(thesis_row)
            theses_created += 1

    db.commit()

    if execute_auto and broker.get("autopilot_enabled"):
        intents = db.execute(
            select(TradeIntent).where(TradeIntent.run_id == run.id).order_by(desc(TradeIntent.expected_edge))
        ).scalars().all()
        for intent in intents[:3]:
            try:
                approve_intent(db, intent.id, note="autopilot approved")
                execute_intent(db, intent.id)
            except Exception:
                continue

    try:
        from app.services.telegram_service import notify_agent_run

        notify_agent_run(db, run.id)
    except Exception:
        pass

    return {
        "run_id": run.id,
        "created_at": run.created_at,
        "market_regime": run.market_regime,
        "portfolio_score": run.portfolio_score,
        "summary_markdown": run.summary_markdown,
        "intents_created": created,
        "theses_created": theses_created,
    }


def list_runs(db: Session, limit: int = 50) -> list[AgentRun]:
    q = select(AgentRun).order_by(desc(AgentRun.created_at)).limit(limit)
    return list(db.execute(q).scalars().all())


def get_run(db: Session, run_id: str) -> AgentRun | None:
    return db.get(AgentRun, run_id)
