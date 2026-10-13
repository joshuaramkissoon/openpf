"""Chat input edge cases and runtime failures yield a clear message, never a
hang or a raw stack/exception dump."""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.api.agent as agent
from app.main import app
from app.schemas.chat import ChatSendRequest
from app.services.chat_errors import clean_chat_input, friendly_chat_error
from tests.test_chat_stream_question_ws import _patch_seams


@pytest.mark.parametrize("content", ["", "   ", "\n\t ", "\x00\x01\x02", "x" * 12001])
def test_send_request_rejects_empty_binary_and_huge(content):
    with pytest.raises(ValidationError):
        ChatSendRequest(content=content)


def test_send_request_keeps_unicode_and_strips_control_chars():
    req = ChatSendRequest(content="  buy 🍎 for café 日本\x00\x07 ")
    assert req.content == "buy 🍎 for café 日本"
    assert clean_chat_input("a\tb\nc") == "a\tb\nc"


class CLINotFoundError(Exception):
    pass


class ProcessError(Exception):
    pass


@pytest.mark.parametrize(
    "exc, needle",
    [
        (CLINotFoundError("Claude Code not found"), "isn't installed"),
        (ProcessError("Invalid API key · Please run /login"), "isn't authenticated"),
        (RuntimeError("Error 429 rate limit"), "rate limited"),
        (ProcessError("Command failed with exit code 1"), "couldn't start"),
    ],
)
def test_friendly_chat_errors(exc, needle):
    msg = friendly_chat_error(exc)
    assert needle in msg
    assert "Traceback" not in msg


def _ws_turn(content):
    client = TestClient(app)
    frames = []
    with client.websocket_connect("/api/agent/chat/sessions/sess-1/stream") as ws:
        ws.send_json({"content": content, "account_kind": "all", "display_currency": "GBP"})
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in {"done", "error"}:
                return frames


def test_ws_runtime_failure_finishes_with_clear_reply(monkeypatch):
    _patch_seams(monkeypatch)

    async def broken(**kwargs):
        raise CLINotFoundError("Claude Code not found at /usr/bin/claude")

    monkeypatch.setattr(agent.claude_chat_runtime, "stream_reply", broken)
    frames = _ws_turn("hello")
    done = frames[-1]
    assert done["type"] == "done"
    assert done["result_subtype"] == "error_runtime"
    assert "isn't installed" in done["assistant_message"]["content"]


def test_ws_partial_answer_is_kept_on_failure(monkeypatch):
    _patch_seams(monkeypatch)

    async def half(*, on_delta, **kwargs):
        await on_delta("Here is half an answer")
        raise RuntimeError("stream dropped")

    monkeypatch.setattr(agent.claude_chat_runtime, "stream_reply", half)
    done = _ws_turn("hello")[-1]
    assert done["assistant_message"]["content"].startswith("Here is half an answer")


@pytest.mark.parametrize("content, needle", [("   ", "empty"), ("x" * 20000, "too long")])
def test_ws_rejects_bad_input_without_starting_a_turn(monkeypatch, content, needle):
    _patch_seams(monkeypatch)
    appended = []
    monkeypatch.setattr(agent, "append_user_message", lambda *a, **k: appended.append(a))
    frames = _ws_turn(content)
    assert frames[-1]["type"] == "error" and needle in frames[-1]["error"]
    assert appended == []
