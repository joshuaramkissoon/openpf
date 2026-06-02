"""Tests for chat-title generation: cadence gating, transcript trimming, and
the rename/keep decision. The Gemini call is monkeypatched — no network."""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import entities  # noqa: F401 — register all tables
from app.models.entities import ChatMessage, ChatSession
from app.services import chat_title_service as cts
from app.services.chat_title_service import TitleDecision


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _seed(db: Session, *, turns: int) -> ChatSession:
    """A session with `turns` user/assistant exchanges (default placeholder title)."""
    session = ChatSession(title="Portfolio Chat")
    db.add(session)
    db.commit()
    for i in range(turns):
        db.add(ChatMessage(session_id=session.id, role="user", content=f"user msg {i}"))
        db.add(ChatMessage(session_id=session.id, role="assistant", content=f"assistant msg {i}"))
    db.commit()
    return session


def _patch_model(monkeypatch, *, should_rename: bool, title: str) -> dict:
    calls = {"n": 0}

    async def fake_generate_structured(**kwargs):
        calls["n"] += 1
        return TitleDecision(should_rename=should_rename, title=title)

    monkeypatch.setattr(cts.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(cts, "generate_structured", fake_generate_structured)
    return calls


def test_is_due_cadence():
    assert cts._is_due(0) is False
    assert cts._is_due(1) is True  # first assistant reply → name it
    assert cts._is_due(2) is False
    assert cts._is_due(cts.REEVAL_EVERY_ASSISTANT_TURNS) is True  # re-eval cadence
    assert cts._is_due(cts.REEVAL_EVERY_ASSISTANT_TURNS + 1) is False


def test_is_placeholder_title():
    # generic defaults + the UI's "New Chat" timestamp titles count as unnamed
    for t in ["", "Portfolio Chat", "portfolio chat", "New Chat", "Chat Jun 1 10:19", "Chat Feb 16 18:29"]:
        assert cts.is_placeholder_title(t) is True, t
    # real titles must not be treated as placeholders
    for t in ["NVDA Earnings Outlook", "Rebalancing the ISA", "Chat about NVDA", "Chatting Strategy"]:
        assert cts.is_placeholder_title(t) is False, t


def test_build_transcript_caps_messages_and_chars(db):
    session = _seed(db, turns=10)  # 20 messages
    db.add(ChatMessage(session_id=session.id, role="user", content="x" * 5000))
    db.commit()
    lines = [l for l in cts.build_transcript(db, session.id).split("\n\n") if l]
    assert len(lines) <= cts._MAX_MESSAGES
    assert all(len(l) <= cts._MAX_CHARS_PER_MESSAGE + len("assistant: ") for l in lines)


def test_retitle_names_chat_on_first_assistant_reply(db, monkeypatch):
    session = _seed(db, turns=1)
    _patch_model(monkeypatch, should_rename=True, title="NVDA Earnings Outlook")
    changed = asyncio.run(cts.maybe_retitle_session(db, session))
    assert changed is True
    assert session.title == "NVDA Earnings Outlook"
    assert db.get(ChatSession, session.id).title == "NVDA Earnings Outlook"  # persisted


def test_retitle_keeps_when_model_declines(db, monkeypatch):
    session = _seed(db, turns=1)
    session.title = "Existing Good Title"
    db.commit()
    _patch_model(monkeypatch, should_rename=False, title="whatever")
    changed = asyncio.run(cts.maybe_retitle_session(db, session))
    assert changed is False
    assert session.title == "Existing Good Title"


def test_retitle_noop_when_not_due_skips_model(db, monkeypatch):
    session = _seed(db, turns=2)  # 2 assistant turns → not due
    calls = _patch_model(monkeypatch, should_rename=True, title="Should Not Be Used")
    changed = asyncio.run(cts.maybe_retitle_session(db, session))
    assert changed is False
    assert calls["n"] == 0  # gated out before calling the model


def test_retitle_noop_without_api_key(db, monkeypatch):
    session = _seed(db, turns=1)
    monkeypatch.setattr(cts.settings, "gemini_api_key", "")
    changed = asyncio.run(cts.maybe_retitle_session(db, session))
    assert changed is False
