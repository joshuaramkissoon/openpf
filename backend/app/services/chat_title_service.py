"""Generate concise, descriptive titles for Archie chat sessions.

A cheap, fast Gemini call (structured output) names a conversation after its
first assistant reply, then re-evaluates every few turns to catch topic drift.
The model receives the current title plus a trimmed slice of recent context and
*decides* whether to rename or keep it — so settled conversations don't churn
their title, and we never feed the whole transcript to the title call.
"""

from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import ChatMessage, ChatSession
from app.services.gemini_client import generate_structured

logger = logging.getLogger(__name__)
settings = get_settings()

# Titles a session may carry before it has been named for real.
PLACEHOLDER_TITLES = {"", "portfolio chat", "new chat"}
# The UI's "New Chat" button stamps a timestamp title, e.g. "Chat Jun 1 10:19"
# (dayjs "MMM D HH:mm"). Treat those as unnamed too, so they get a real title.
_AUTO_TITLE_RE = re.compile(r"^chat [a-z]{3} \d{1,2} \d{2}:\d{2}$")


def is_placeholder_title(title: str) -> bool:
    """True if `title` is a generic/auto-generated default rather than a real name."""
    normalized = (title or "").strip().lower()
    return normalized in PLACEHOLDER_TITLES or bool(_AUTO_TITLE_RE.match(normalized))
# Re-evaluate the title every N assistant turns (after the first) to catch drift.
REEVAL_EVERY_ASSISTANT_TURNS = 5
# Keep the title call cheap: cap how much conversation it sees.
_MAX_MESSAGES = 6
_MAX_CHARS_PER_MESSAGE = 600
# Hard ceiling on how long the live request will wait for a title before
# giving up and keeping the current one. Flash-lite typically answers in ~1-2s.
TITLE_GENERATION_TIMEOUT = 6.0


class TitleDecision(BaseModel):
    """Structured output: the gate (`should_rename`) plus the proposed title."""

    should_rename: bool = Field(
        description="True only if `title` describes the conversation better than the current title.",
    )
    title: str = Field(
        description="A specific 2-6 word title in title case. No surrounding quotes, no trailing punctuation.",
    )


def _assistant_turn_count(db: Session, session_id: str) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
        ).scalar_one()
    )


def _is_due(assistant_turns: int) -> bool:
    if assistant_turns <= 0:
        return False
    if assistant_turns == 1:
        return True  # name the chat as soon as it has a real exchange
    return assistant_turns % REEVAL_EVERY_ASSISTANT_TURNS == 0


def _context_messages(db: Session, session_id: str) -> list[ChatMessage]:
    rows = list(
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id)
        ).scalars().all()
    )
    if len(rows) <= _MAX_MESSAGES:
        return rows
    # The opening message anchors the topic; keep it plus the most recent turns.
    return [rows[0], *rows[-(_MAX_MESSAGES - 1):]]


def build_transcript(db: Session, session_id: str) -> str:
    """A compact, role-tagged transcript for the title model — first message
    plus the most recent turns, each truncated. Empty if nothing usable."""
    parts: list[str] = []
    for row in _context_messages(db, session_id):
        content = (row.content or "").strip()
        if not content:
            continue
        parts.append(f"{row.role}: {content[:_MAX_CHARS_PER_MESSAGE]}")
    return "\n\n".join(parts)


def _build_prompt(current_title: str, transcript: str) -> str:
    if is_placeholder_title(current_title):
        gate = "This conversation has no real title yet — give it one."
    else:
        gate = (
            f'The current title is "{current_title}". Keep it unless the conversation has clearly '
            "shifted to a topic it no longer captures; only then propose a better title."
        )
    return (
        "You title conversations for Archie, a personal investing assistant.\n"
        f"{gate}\n\n"
        "A good title is 2-6 words, specific, in title case, with no surrounding quotes and no "
        "trailing punctuation. Name the concrete subject — a ticker, strategy, or task "
        '(e.g. "NVDA Earnings Outlook", "Rebalancing the ISA", "Leveraged ETP Scan"). '
        'Avoid generic titles like "Portfolio Chat" or "Investment Questions".\n\n'
        f"Conversation:\n{transcript}"
    )


def _clean_title(raw: str) -> str:
    return (raw or "").strip().strip("\"'").strip()[:240]


async def generate_title(current_title: str, transcript: str) -> str | None:
    """Ask the model for a title. Returns a new title to apply, or ``None`` to
    keep the current one. Shared by the live path and the backfill script."""
    if not transcript.strip():
        return None
    decision = await generate_structured(
        model=settings.gemini_title_model,
        contents=_build_prompt(current_title, transcript),
        response_schema=TitleDecision,
        thinking_level="low",
    )
    if decision is None or not decision.should_rename:
        return None
    new_title = _clean_title(decision.title)
    if not new_title or new_title == current_title.strip():
        return None
    return new_title


async def maybe_retitle_session(db: Session, session: ChatSession) -> bool:
    """(Re)title `session` if it is due, persisting and mutating `session.title`
    in place so the caller's response payload reflects the new title. Returns
    True if the title changed."""
    if not settings.gemini_api_key:
        return False
    if not _is_due(_assistant_turn_count(db, session.id)):
        return False
    new_title = await generate_title(session.title or "", build_transcript(db, session.id))
    if not new_title:
        return False
    session.title = new_title
    db.add(session)
    db.commit()
    db.refresh(session)
    return True


async def retitle_in_request(db: Session, session: ChatSession) -> None:
    """Best-effort, time-boxed retitle for the live request path. Never raises:
    on timeout or error it silently keeps the current title."""
    try:
        await asyncio.wait_for(maybe_retitle_session(db, session), timeout=TITLE_GENERATION_TIMEOUT)
    except asyncio.TimeoutError:
        logger.debug("chat retitle timed out for %s", session.id)
    except Exception as exc:  # noqa: BLE001 — titling is best-effort, must not break chat
        # Real errors (bad model name, auth, quota) surface here — keep them
        # visible so a misconfigured key/model isn't silently swallowed.
        logger.warning("chat retitle failed for %s: %s", session.id, exc)
