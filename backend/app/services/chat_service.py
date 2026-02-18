from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import ChatMessage, ChatSession
from app.services.claude_chat_runtime import claude_chat_runtime
from app.services.claude_memory_service import schedule_memory_distillation
from app.services.portfolio_service import get_portfolio_snapshot

settings = get_settings()

AccountKind = Literal["all", "invest", "stocks_isa"]


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def list_sessions(db: Session, limit: int = 20) -> list[ChatSession]:
    return list(
        db.execute(
            select(ChatSession).order_by(desc(ChatSession.updated_at)).limit(max(1, min(limit, 100)))
        ).scalars().all()
    )


def create_session(db: Session, title: str = "Portfolio Chat") -> ChatSession:
    row = ChatSession(title=(title or "Portfolio Chat")[:240])
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_session(db: Session, session_id: str | None) -> ChatSession:
    if session_id:
        existing = db.get(ChatSession, session_id)
        if existing:
            return existing
    existing = db.execute(select(ChatSession).order_by(desc(ChatSession.updated_at)).limit(1)).scalar_one_or_none()
    if existing:
        return existing
    return create_session(db)


def require_session(db: Session, session_id: str) -> ChatSession:
    row = db.get(ChatSession, session_id)
    if not row:
        raise ValueError("chat session not found")
    return row


def delete_session(db: Session, session_id: str) -> bool:
    row = db.get(ChatSession, session_id)
    if not row:
        return False
    db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    db.delete(row)
    db.commit()
    return True


def list_messages(db: Session, session_id: str, limit: int = 120) -> list[ChatMessage]:
    rows = list(
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(desc(ChatMessage.id))
            .limit(max(1, min(limit, 500)))
        ).scalars().all()
    )
    rows.reverse()
    return rows


def _build_prompt(
    snapshot: dict[str, Any],
    history: list[ChatMessage],
    user_message: str,
    account_kind: AccountKind,
    display_currency: Literal["GBP", "USD"],
) -> str:
    now = datetime.utcnow()
    history_payload = [{"role": row.role, "content": row.content} for row in history[-8:]]
    payload = {
        "context": {
            "current_date": now.strftime("%A, %d %B %Y"),
            "current_time_utc": now.strftime("%H:%M UTC"),
            "account_kind": account_kind,
            "display_currency": display_currency,
            "portfolio": snapshot,
            "history": history_payload,
        },
        "task": user_message,
        "instructions": [
            "Act as a pragmatic portfolio copilot.",
            "Use available skills/tools when useful.",
            "Ground recommendations in the provided portfolio and risk context.",
            "Be concise and specific.",
            f"Today is {now.strftime('%A %d %B %Y')}. Use this when referencing dates and days of the week.",
        ],
    }
    return json.dumps(payload, default=_json_default)


def _redact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    quantity_factor = 1.11
    price_factor = 1.23
    money_factor = quantity_factor * price_factor

    def amount(value: Any) -> Any:
        if isinstance(value, (int, float)):
            return float(value) * money_factor
        return value

    def price(value: Any) -> Any:
        if isinstance(value, (int, float)):
            return float(value) * price_factor
        return value

    def qty(value: Any) -> Any:
        if isinstance(value, (int, float)):
            return float(value) * quantity_factor
        return value

    out = json.loads(json.dumps(snapshot, default=_json_default))
    account = out.get("account", {})
    for key in ("free_cash", "invested", "pie_cash", "total", "ppl"):
        account[key] = amount(account.get(key))

    for row in out.get("accounts", []):
        for key in ("free_cash", "invested", "pie_cash", "total", "ppl"):
            row[key] = amount(row.get(key))

    for row in out.get("positions", []):
        row["quantity"] = qty(row.get("quantity"))
        row["average_price"] = price(row.get("average_price"))
        row["current_price"] = price(row.get("current_price"))
        row["total_cost"] = amount(row.get("total_cost"))
        row["value"] = amount(row.get("value"))
        row["ppl"] = amount(row.get("ppl"))

    metrics = out.get("metrics", {})
    metrics["total_value"] = amount(metrics.get("total_value"))
    metrics["free_cash"] = amount(metrics.get("free_cash"))
    return out


def append_user_message(db: Session, session: ChatSession, content: str) -> ChatMessage:
    row = ChatMessage(session_id=session.id, role="user", content=content.strip())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def append_assistant_message(
    db: Session,
    session: ChatSession,
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> ChatMessage:
    row = ChatMessage(
        session_id=session.id,
        role="assistant",
        content=content[:24000],
        tool_calls=tool_calls if tool_calls else None,
    )
    session.updated_at = datetime.utcnow()
    db.add(row)
    db.add(session)
    db.commit()
    db.refresh(row)
    db.refresh(session)
    return row


def build_prompt_for_session(
    db: Session,
    session: ChatSession,
    content: str,
    account_kind: AccountKind,
    display_currency: Literal["GBP", "USD"],
    redact_values: bool = False,
) -> str:
    history = list_messages(db, session.id, limit=80)
    snapshot = get_portfolio_snapshot(db, account_kind=account_kind, display_currency=display_currency)
    if redact_values:
        snapshot = _redact_snapshot(snapshot)
    return _build_prompt(snapshot, history, content, account_kind, display_currency)


async def send_message(
    db: Session,
    session_id: str | None,
    content: str,
    account_kind: AccountKind = "all",
    display_currency: Literal["GBP", "USD"] = "GBP",
    redact_values: bool = False,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    on_status: Callable[[str, str, dict | None], Awaitable[None]] | None = None,
) -> tuple[ChatSession, ChatMessage, ChatMessage]:
    session = ensure_session(db, session_id)
    user_row = append_user_message(db, session, content)
    prompt = build_prompt_for_session(db, session, content, account_kind, display_currency, redact_values=redact_values)

    assistant_text: str
    if settings.agent_provider == "claude":
        try:
            reply = await claude_chat_runtime.stream_reply(
                chat_session_id=session.id,
                prompt=prompt,
                on_delta=on_delta or (lambda _delta: _noop_async()),
                on_status=on_status,
            )
            assistant_text = reply.text
            if not assistant_text:
                assistant_text = "No response generated."
        except Exception as exc:
            assistant_text = f"Claude runtime unavailable: {exc}"
    else:
        assistant_text = "Claude provider is disabled. Set AGENT_PROVIDER=claude to enable chat."

    assistant_row = append_assistant_message(db, session, assistant_text)
    schedule_memory_distillation(user_message=content, assistant_message=assistant_text)
    return session, user_row, assistant_row


async def _noop_async() -> None:
    return None
