"""Integration: the chat WebSocket surfaces AskUserQuestion as a `question` frame
and feeds the client's selection back into the runtime's on_question callback.

Drives the REAL chat_stream handler (ack/question/answer/done framing + the
receive loop) with stream_reply stubbed to invoke on_question — so the SDK
control-request plumbing is faked but the handler's round-trip is exercised end
to end. DB/title/memory/serialisation are patched at the agent-module seams.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import sys
import threading

sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.api.agent as agent
from app.main import app
from app.services.claude_chat_runtime import ReplyResult


class _Item:
    def __init__(self, d):
        self._d = d

    def model_dump(self, mode=None):
        return self._d


def _patch_seams(monkeypatch):
    monkeypatch.setattr(agent.settings, "agent_provider", "claude")
    monkeypatch.setattr(agent, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(agent, "require_session", lambda *a, **k: SimpleNamespace(id="sess-1"))
    monkeypatch.setattr(
        agent, "append_user_message", lambda db, session, content, *a, **k: SimpleNamespace(id=1, content=content)
    )
    monkeypatch.setattr(
        agent,
        "append_assistant_message",
        lambda db, session, text, *a, **k: SimpleNamespace(id=2, content=text),
    )
    monkeypatch.setattr(agent, "build_prompt_for_session", lambda **k: "PROMPT")
    monkeypatch.setattr(agent, "schedule_memory_distillation", lambda **k: None)

    async def _no_retitle(*a, **k):
        return None

    monkeypatch.setattr(agent, "retitle_in_request", _no_retitle)
    monkeypatch.setattr(agent, "_session_item", lambda row: _Item({"id": getattr(row, "id", "sess-1")}))
    monkeypatch.setattr(agent, "_message_item", lambda row: _Item({"id": row.id, "content": row.content}))


_QUESTION = {
    "question": "Which KEYS did you mean?",
    "header": "Ticker",
    "options": [{"label": "Keysight"}, {"label": "Keystone Law"}],
    "multiSelect": False,
}


def test_question_frame_round_trips_answer_into_runtime(monkeypatch):
    _patch_seams(monkeypatch)

    seen_answers = {}

    async def fake_stream_reply(*, chat_session_id, prompt, on_delta, on_status, on_question):
        answers = await on_question({"questions": [_QUESTION]})
        seen_answers.update(answers or {})
        text = f"Great — going with {answers['Which KEYS did you mean?']}."
        await on_delta(text)
        return ReplyResult(text=text)

    monkeypatch.setattr(agent.claude_chat_runtime, "stream_reply", fake_stream_reply)

    client = TestClient(app)
    with client.websocket_connect("/api/agent/chat/sessions/sess-1/stream") as ws:
        ws.send_json({"content": "buy KEYS", "account_kind": "all", "display_currency": "GBP"})

        assert ws.receive_json()["type"] == "ack"

        q = ws.receive_json()
        assert q["type"] == "question"
        assert q["questions"][0]["question"] == "Which KEYS did you mean?"
        qid = q["question_id"]

        ws.send_json({"type": "answer", "question_id": qid, "answers": {"Which KEYS did you mean?": "Keysight"}})

        # Drain delta(s) until the done frame.
        frame = ws.receive_json()
        while frame["type"] == "delta":
            frame = ws.receive_json()

        assert frame["type"] == "done"
        assert "Keysight" in frame["assistant_message"]["content"]

    # The runtime callback received exactly what the user selected.
    assert seen_answers == {"Which KEYS did you mean?": "Keysight"}


def test_cancel_frame_resolves_question_as_dismissed(monkeypatch):
    _patch_seams(monkeypatch)

    captured = {}

    async def fake_stream_reply(*, chat_session_id, prompt, on_delta, on_status, on_question):
        answers = await on_question({"questions": [_QUESTION]})
        captured["answers"] = answers
        return ReplyResult(text="No worries, I'll use my best judgement.")

    monkeypatch.setattr(agent.claude_chat_runtime, "stream_reply", fake_stream_reply)

    client = TestClient(app)
    with client.websocket_connect("/api/agent/chat/sessions/sess-1/stream") as ws:
        ws.send_json({"content": "buy KEYS", "account_kind": "all", "display_currency": "GBP"})
        assert ws.receive_json()["type"] == "ack"
        q = ws.receive_json()
        assert q["type"] == "question"
        ws.send_json({"type": "cancel_question", "question_id": q["question_id"]})

        frame = ws.receive_json()
        while frame["type"] == "delta":
            frame = ws.receive_json()
        assert frame["type"] == "done"

    # Dismissed → on_question resolves to None (the runtime then denies gracefully).
    assert captured["answers"] is None


def test_disconnect_mid_question_resolves_without_hanging(monkeypatch):
    """If the client drops while a question is pending, on_question must resolve
    (to None) rather than block until the turn timeout — no dangling session lock."""
    _patch_seams(monkeypatch)

    resolved = threading.Event()
    captured = {}

    async def fake_stream_reply(*, chat_session_id, prompt, on_delta, on_status, on_question):
        captured["answers"] = await on_question({"questions": [_QUESTION]})
        resolved.set()
        return ReplyResult(text="done")

    monkeypatch.setattr(agent.claude_chat_runtime, "stream_reply", fake_stream_reply)

    client = TestClient(app)
    with client.websocket_connect("/api/agent/chat/sessions/sess-1/stream") as ws:
        ws.send_json({"content": "buy KEYS", "account_kind": "all", "display_currency": "GBP"})
        assert ws.receive_json()["type"] == "ack"
        assert ws.receive_json()["type"] == "question"
        # Leave the context → client disconnects without answering.

    assert resolved.wait(timeout=5), "on_question did not resolve after disconnect"
    assert captured["answers"] is None
