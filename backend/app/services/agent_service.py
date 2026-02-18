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
from app.services.execution_service import approve_intent, execute_intent
from app.services.market_data import fetch_history
from app.services.portfolio_service import get_portfolio_snapshot
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


def _market_regime() -> str:
    spy = signal_for_symbol("SPY")
    if spy.trend_score is None or spy.momentum_63d is None:
        return "neutral"
    if spy.trend_score >= 0.75 and spy.momentum_63d > 0:
        return "risk-on"
    if spy.trend_score <= 0.25 and spy.momentum_63d < 0:
        return "risk-off"
    return "mixed"


def _safe_qty(notional: float, price: float) -> float:
    if price <= 0:
        return 0.0
    return round(notional / price, 4)


def _position_intents(snapshot: dict[str, Any], max_weight: float) -> list[ProposedIntent]:
    positions: list[dict[str, Any]] = snapshot["positions"]
    total = float(snapshot["account"]["total"] or 0.0)
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
            notional = total * 0.015
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


def _watchlist_intents(snapshot: dict[str, Any], watchlist: list[str], max_weight: float) -> list[ProposedIntent]:
    held = {str(row["ticker"]).upper() for row in snapshot["positions"]}
    total = float(snapshot["account"]["total"] or 0.0)
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

            starter_weight = min(max_weight * 0.4, 0.04)
            notional = max(total * starter_weight, 75.0)
            qty = _safe_qty(notional, price)
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

    snapshot = get_portfolio_snapshot(db)
    market_regime = _market_regime()

    ideas: list[ProposedIntent] = []
    summary_override: str | None = None
    claude_result = run_claude_analyst_cycle(snapshot, watchlist, risk)

    if claude_result and claude_result.get("ok"):
        summary_override = str(claude_result.get("summary_markdown") or "").strip() or None
        ideas.extend(_claude_payload_to_intents(snapshot, claude_result.get("intents", []), risk))

    if not ideas:
        ideas = _position_intents(snapshot, max_weight=float(risk["max_position_weight"]))
        if include_watchlist:
            ideas.extend(_watchlist_intents(snapshot, watchlist, max_weight=float(risk["max_position_weight"])))

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
