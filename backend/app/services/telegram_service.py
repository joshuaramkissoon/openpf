from __future__ import annotations

import anyio
from datetime import datetime
from typing import Iterable

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import ChatSession
from app.models.entities import AgentRun, Thesis, TradeIntent
from app.services.chat_service import create_session, send_message as send_chat_message
from app.services.config_store import ConfigStore
from app.services.llm_service import maybe_answer_with_openai
from app.services.telegram_client import TelegramError, get_updates, send_message


def _message_for_status(db: Session) -> str:
    from app.services.portfolio_service import get_portfolio_snapshot

    snapshot = get_portfolio_snapshot(db)
    account = snapshot["account"]
    metrics = snapshot["metrics"]

    return (
        "*MyPF Status*\n"
        f"- Equity: `{account['currency']} {account['total']:,.2f}`\n"
        f"- Cash: `{account['currency']} {account['free_cash']:,.2f}` ({metrics['cash_ratio']:.1%})\n"
        f"- P/L: `{account['currency']} {account['ppl']:,.2f}`\n"
        f"- Beta: `{metrics['estimated_beta']:.2f}`\n"
        f"- Concentration HHI: `{metrics['concentration_hhi']:.3f}`\n"
        f"- Time: `{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}`"
    )


def _open_intents(db: Session, limit: int = 8) -> list[TradeIntent]:
    q = (
        select(TradeIntent)
        .where(TradeIntent.status.in_(["proposed", "approved", "executing"]))
        .order_by(desc(TradeIntent.expected_edge), desc(TradeIntent.confidence), desc(TradeIntent.created_at))
        .limit(limit)
    )
    return list(db.execute(q).scalars().all())


def _resolve_intent_by_token(db: Session, token: str) -> TradeIntent | None:
    key = token.strip().lower()
    if not key:
        return None

    intents = list(
        db.execute(
            select(TradeIntent)
            .order_by(desc(TradeIntent.created_at))
            .limit(300)
        )
        .scalars()
        .all()
    )
    for intent in intents:
        if intent.id.lower().startswith(key):
            return intent
    return None


def _help_text() -> str:
    return (
        "*MyPF Telegram Commands*\n"
        "- `/status` portfolio snapshot\n"
        "- `/accounts` account breakdown\n"
        "- `/run` refresh + run agent\n"
        "- `/theses` list latest theses\n"
        "- `/intents` list current queue\n"
        "- `/approve <intent_id_prefix>`\n"
        "- `/reject <intent_id_prefix>`\n"
        "- `/execute <intent_id_prefix>`\n"
        "- `/help` show commands\n"
        "You can also ask a free-form question."
    )


def _fallback_qa(db: Session, question: str) -> str:
    from app.services.portfolio_service import get_portfolio_snapshot

    q = question.lower()
    snapshot = get_portfolio_snapshot(db)
    account = snapshot["account"]
    metrics = snapshot["metrics"]

    if "cash" in q:
        return f"Available cash is {account['currency']} {account['free_cash']:,.2f} ({metrics['cash_ratio']:.1%} of equity)."
    if "risk" in q or "drawdown" in q or "beta" in q:
        return (
            f"Current risk view: beta {metrics['estimated_beta']:.2f}, concentration HHI {metrics['concentration_hhi']:.3f}, "
            f"estimated volatility {metrics['estimated_volatility']:.1%}."
        )
    if "intent" in q or "trade" in q:
        intents = _open_intents(db, limit=5)
        if not intents:
            return "No open intents right now."
        lines = ["Top open intents:"]
        for intent in intents:
            lines.append(
                f"- {intent.id[:8]} {intent.side.upper()} {intent.symbol} edge {intent.expected_edge:.2%} conf {intent.confidence:.0%}"
            )
        return "\n".join(lines)
    if "thesis" in q:
        theses = list(db.execute(select(Thesis).where(Thesis.status == "active").order_by(desc(Thesis.created_at)).limit(5)).scalars().all())
        if not theses:
            return "No active theses available yet."
        lines = ["Latest theses:"]
        for thesis in theses:
            lines.append(f"- {thesis.symbol}: {thesis.title} (conf {thesis.confidence:.0%})")
        return "\n".join(lines)

    return (
        "I can answer portfolio/risk/intent questions. Try: 'what is my risk right now', 'how much cash do I have', or '/intents'."
    )


def _context_for_llm(db: Session) -> str:
    from app.services.portfolio_service import get_portfolio_snapshot

    snapshot = get_portfolio_snapshot(db)
    open_intents = _open_intents(db, limit=8)
    recent_theses = list(db.execute(select(Thesis).where(Thesis.status == "active").order_by(desc(Thesis.created_at)).limit(8)).scalars().all())
    run = db.execute(select(AgentRun).order_by(desc(AgentRun.created_at)).limit(1)).scalar_one_or_none()

    lines = [
        f"account={snapshot['account']}",
        f"metrics={snapshot['metrics']}",
        f"positions_count={len(snapshot['positions'])}",
    ]
    if run:
        lines.append(f"latest_run={{'market_regime': '{run.market_regime}', 'portfolio_score': {run.portfolio_score:.3f}}}")
    if open_intents:
        compact = [
            {
                "id": x.id,
                "status": x.status,
                "symbol": x.symbol,
                "side": x.side,
                "expected_edge": x.expected_edge,
                "confidence": x.confidence,
                "risk_score": x.risk_score,
            }
            for x in open_intents
        ]
        lines.append(f"open_intents={compact}")
    if recent_theses:
        lines.append(
            f"recent_theses={[{'symbol': t.symbol, 'title': t.title, 'confidence': t.confidence, 'invalidation': t.invalidation} for t in recent_theses]}"
        )
    return "\n".join(lines)


def _answer_question(db: Session, question: str) -> str:
    context = _context_for_llm(db)
    llm = maybe_answer_with_openai(question, context)
    if llm:
        return llm
    return _fallback_qa(db, question)


def _is_authorized(user_id: int | None, cfg: dict) -> bool:
    allowed = [int(x) for x in cfg.get("allowed_user_ids", [])]
    if not allowed:
        return True
    if user_id is None:
        return False
    return int(user_id) in allowed


def handle_telegram_text(db: Session, text: str) -> str:
    from app.services.agent_service import run_agent
    from app.services.execution_service import approve_intent, execute_intent, reject_intent
    from app.services.portfolio_service import refresh_portfolio

    raw = text.strip()
    if not raw:
        return "Empty message. Send /help for commands."

    parts = raw.split()
    cmd = parts[0].lower()

    if cmd in {"/start", "/help"}:
        return _help_text()

    if cmd == "/status":
        return _message_for_status(db)

    if cmd == "/accounts":
        from app.services.portfolio_service import get_portfolio_snapshot

        snap = get_portfolio_snapshot(db, account_kind="all")
        rows = snap.get("accounts", [])
        if not rows:
            return "No account snapshots available."
        lines = ["*Account Breakdown*"]
        for row in rows:
            lines.append(
                f"- {str(row.get('account_kind', '')).upper()}: {row.get('currency')} {float(row.get('total', 0.0)):,.2f} "
                f"(cash {row.get('currency')} {float(row.get('free_cash', 0.0)):,.2f})"
            )
        return "\n".join(lines)

    if cmd == "/run":
        refresh_portfolio(db)
        result = run_agent(db, include_watchlist=True, execute_auto=False)
        return (
            "Agent run completed.\n"
            f"- Regime: {result['market_regime']}\n"
            f"- Score: {result['portfolio_score']*100:.1f}/100\n"
            f"- Intents: {result['intents_created']}"
        )

    if cmd in {"/scheduler", "/tasks"}:
        from app.services.task_scheduler_service import list_tasks

        tasks = list_tasks(db)
        if not tasks:
            return "No scheduled tasks configured."
        lines = ["*Scheduler Tasks*"]
        for task in tasks:
            lines.append(
                f"- `{task['name']}` | {'on' if task['enabled'] else 'off'} | "
                f"next `{task['next_run_at']}` | status `{task['last_status']}`"
            )
        return "\n".join(lines)

    if cmd.startswith("/lev"):
        from app.services.leveraged_service import (
            close_trade,
            get_policy as get_leveraged_policy,
            leveraged_snapshot,
            run_leveraged_cycle,
            scan_signals,
            update_policy as update_leveraged_policy,
        )

        if len(parts) == 1 or parts[1].lower() in {"help", "-h", "--help"}:
            return (
                "*Leveraged Commands*\n"
                "- `/lev status`\n"
                "- `/lev scan`\n"
                "- `/lev cycle`\n"
                "- `/lev policy`\n"
                "- `/lev auto on|off`\n"
                "- `/lev close <trade_id_prefix>`"
            )

        sub = parts[1].lower()
        if sub == "status":
            snap = leveraged_snapshot(db)
            summary = snap.get("summary", {})
            policy = snap.get("policy", {})
            return (
                "*Leveraged Status*\n"
                f"- Open positions: `{summary.get('open_positions', 0)}`\n"
                f"- Open exposure: `{summary.get('open_exposure', 0):.2f}` / `{summary.get('max_total_exposure', 0):.2f}`\n"
                f"- Unrealized P&L: `{summary.get('open_unrealized_pnl', 0):.2f}`\n"
                f"- Realized P&L: `{summary.get('closed_realized_pnl', 0):.2f}`\n"
                f"- Auto execute: `{'ON' if policy.get('auto_execute_enabled') else 'OFF'}`\n"
                f"- Rails: per-position `{policy.get('per_position_notional', 0):.2f}`, "
                f"max total `{policy.get('max_total_exposure', 0):.2f}`, max open `{policy.get('max_open_positions', 0)}`"
            )

        if sub == "scan":
            result = scan_signals(db)
            return (
                "*Leveraged Scan*\n"
                f"- Signals created: `{result.get('created', 0)}`\n"
                f"- Auto executed: `{result.get('executed', 0)}`\n"
                f"- Failures: `{len(result.get('failures', []))}`"
            )

        if sub == "cycle":
            result = run_leveraged_cycle(db)
            monitor = result.get("monitor", {})
            scan = result.get("scan", {})
            return (
                "*Leveraged Cycle*\n"
                f"- Monitored: `{monitor.get('checked', 0)}`\n"
                f"- Closed: `{monitor.get('closed', 0)}`\n"
                f"- Signals created: `{scan.get('created', 0)}`\n"
                f"- Auto executed: `{scan.get('executed', 0)}`"
            )

        if sub == "policy":
            policy = get_leveraged_policy(db)
            return (
                "*Leveraged Policy*\n"
                f"- Enabled: `{'ON' if policy.get('enabled') else 'OFF'}`\n"
                f"- Auto execute: `{'ON' if policy.get('auto_execute_enabled') else 'OFF'}`\n"
                f"- Per position: `{policy.get('per_position_notional', 0):.2f}`\n"
                f"- Max exposure: `{policy.get('max_total_exposure', 0):.2f}`\n"
                f"- Max open positions: `{policy.get('max_open_positions', 0)}`\n"
                f"- TP/SL: `+{float(policy.get('take_profit_pct', 0))*100:.1f}% / -{float(policy.get('stop_loss_pct', 0))*100:.1f}%`\n"
                f"- Close time UK: `{policy.get('close_time_uk', '15:30')}`\n"
                f"- Overnight: `{'yes' if policy.get('allow_overnight') else 'no'}`"
            )

        if sub == "auto":
            if len(parts) < 3:
                return "Usage: `/lev auto on|off`"
            desired = parts[2].strip().lower()
            if desired not in {"on", "off"}:
                return "Usage: `/lev auto on|off`"
            updated = update_leveraged_policy(db, {"auto_execute_enabled": desired == "on"}, actor="telegram")
            return f"Auto execute is now {'ON' if updated.get('auto_execute_enabled') else 'OFF'}."

        if sub == "close":
            if len(parts) < 3:
                return "Usage: `/lev close <trade_id_prefix>`"
            token = parts[2].strip().lower()
            trade_rows = leveraged_snapshot(db).get("open_trades", [])
            target = next((row for row in trade_rows if str(row.get("id", "")).lower().startswith(token)), None)
            if not target:
                return "Open trade not found."
            closed = close_trade(db, str(target["id"]), reason="telegram-close")
            return (
                f"Closed `{closed.symbol}` trade `{closed.id[:8]}` at `{closed.exit_price or 0:.4f}`. "
                f"P&L `{closed.pnl_value:.2f}` ({closed.pnl_pct*100:.2f}%)."
            )

        return "Unknown `/lev` subcommand. Use `/lev help`."

    if cmd == "/intents":
        intents = _open_intents(db, limit=8)
        if not intents:
            return "No open intents right now."
        lines = ["*Open Intents*"]
        for intent in intents:
            lines.append(
                f"- `{intent.id[:8]}` {intent.side.upper()} {intent.symbol} | {intent.status} | "
                f"edge {intent.expected_edge:.2%} conf {intent.confidence:.0%}"
            )
        return "\n".join(lines)

    if cmd == "/theses":
        theses = list(db.execute(select(Thesis).where(Thesis.status == "active").order_by(desc(Thesis.created_at)).limit(10)).scalars().all())
        if not theses:
            return "No active theses yet."
        lines = ["*Active Theses*"]
        for thesis in theses:
            lines.append(
                f"- `{thesis.id[:8]}` {thesis.symbol} | {thesis.title} | conf {thesis.confidence:.0%}"
            )
        return "\n".join(lines)

    if cmd in {"/approve", "/reject", "/execute"}:
        if len(parts) < 2:
            return f"Usage: `{cmd} <intent_id_prefix>`"
        target = _resolve_intent_by_token(db, parts[1])
        if not target:
            return "Intent not found. Use /intents to list IDs."
        try:
            if cmd == "/approve":
                approve_intent(db, target.id, note="telegram approval")
                return f"Approved `{target.id[:8]}` {target.side.upper()} {target.symbol}."
            if cmd == "/reject":
                reject_intent(db, target.id, note="telegram rejection")
                return f"Rejected `{target.id[:8]}` {target.side.upper()} {target.symbol}."

            execute_intent(db, target.id, force_live=False)
            refreshed = db.get(TradeIntent, target.id)
            status = refreshed.status if refreshed else "executed"
            return f"Execute requested for `{target.id[:8]}`. Current status: {status}."
        except Exception as exc:
            return f"Action failed: {exc}"

    session = db.execute(select(ChatSession).where(ChatSession.title == "Telegram").limit(1)).scalar_one_or_none()
    if not session:
        session = create_session(db, title="Telegram")

    async def _ask_archie() -> str:
        _, _, assistant = await send_chat_message(
            db,
            session_id=session.id,
            content=raw,
            account_kind="all",
            display_currency="GBP",
            redact_values=False,
        )
        return assistant.content

    try:
        return anyio.run(_ask_archie)
    except Exception:
        return _answer_question(db, raw)


def send_telegram_notification(db: Session, text: str) -> bool:
    cfg_store = ConfigStore(db)
    cfg = cfg_store.get_telegram()
    if not cfg.get("enabled"):
        return False

    token = str(cfg.get("bot_token", "")).strip()
    chat_id = str(cfg.get("chat_id", "")).strip()
    if not token or not chat_id:
        return False

    try:
        send_message(token, chat_id, text)
        return True
    except TelegramError:
        return False


def notify_agent_run(db: Session, run_id: str) -> None:
    cfg_store = ConfigStore(db)
    cfg = cfg_store.get_telegram()

    if not cfg.get("enabled"):
        return

    run = db.get(AgentRun, run_id)
    if run is None:
        return

    threshold = float(cfg.get("high_conviction_threshold", 0.68))
    intents = list(
        db.execute(
            select(TradeIntent)
            .where(TradeIntent.run_id == run_id)
            .order_by(desc(TradeIntent.expected_edge), desc(TradeIntent.confidence))
        )
        .scalars()
        .all()
    )

    high_conv = [i for i in intents if i.confidence >= threshold and i.expected_edge > 0]

    lines: list[str] = []
    if high_conv:
        lines.append("*High Conviction Intents*")
        for intent in high_conv[:5]:
            lines.append(
                f"- `{intent.id[:8]}` {intent.side.upper()} {intent.symbol} | edge {intent.expected_edge:.2%} | "
                f"conf {intent.confidence:.0%} | risk {intent.risk_score:.0%}"
            )
    elif cfg.get("notify_general_updates", True):
        lines.append("*Agent Update*")
        lines.append(f"- Regime: {run.market_regime}")
        lines.append(f"- Portfolio score: {run.portfolio_score*100:.1f}/100")
        lines.append(f"- New intents: {len(intents)}")

    if not lines:
        return

    send_telegram_notification(db, "\n".join(lines))


def process_telegram_updates(db: Session) -> int:
    cfg_store = ConfigStore(db)
    cfg = cfg_store.get_telegram()

    if not cfg.get("enabled") or not cfg.get("poll_enabled", True):
        return 0

    token = str(cfg.get("bot_token", "")).strip()
    if not token:
        return 0

    state = cfg_store.get("telegram_state", {"last_update_id": 0})
    offset = int(state.get("last_update_id", 0))

    try:
        updates = get_updates(token, offset=offset, timeout=0)
    except TelegramError:
        return 0

    processed = 0
    new_offset = offset

    for update in updates:
        update_id = int(update.get("update_id", 0))
        new_offset = max(new_offset, update_id + 1)

        message = update.get("message") or update.get("edited_message")
        if not message:
            continue

        text = str(message.get("text", "")).strip()
        if not text:
            continue

        chat = message.get("chat", {})
        chat_id = str(chat.get("id", "")).strip()
        from_user = message.get("from", {})
        user_id = from_user.get("id")

        if not cfg.get("chat_id") and chat_id:
            cfg = cfg_store.set_telegram({"chat_id": chat_id})

        if not _is_authorized(user_id if isinstance(user_id, int) else None, cfg):
            if chat_id:
                try:
                    send_message(token, chat_id, "Unauthorized user for this bot.")
                except TelegramError:
                    pass
            processed += 1
            continue

        reply = handle_telegram_text(db, text)
        if chat_id:
            try:
                send_message(token, chat_id, reply)
            except TelegramError:
                pass

        processed += 1

    if new_offset != offset:
        cfg_store.set("telegram_state", {"last_update_id": new_offset})

    return processed
