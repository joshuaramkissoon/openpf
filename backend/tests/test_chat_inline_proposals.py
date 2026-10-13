"""Proposals Archie creates in chat are referenced inline in the transcript.

When propose_trade / stage_play_tranche returns created intents, the web chat
stream splices a ``[proposal](proposal:<id>)`` reference into the reply at that
point (and persists it with the message) so the UI can render a live approval
card in place. Only the id is stored. Plain-text consumers (Telegram) don't opt
in and see no markers.
"""

from pathlib import Path
from types import SimpleNamespace
import asyncio
import json
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.claude_chat_runtime import (  # noqa: E402
    ClaudeChatRuntime,
    _RuntimeSession,
    proposal_ids_from_tool_result,
)


def _text_delta(text: str):
    return SimpleNamespace(event={"type": "content_block_delta", "delta": {"text": text}})


def _tool_use(tool_id: str, name: str):
    return SimpleNamespace(content=[{"type": "tool_use", "id": tool_id, "name": name, "input": {}}])


def _tool_result(tool_id: str, payload: dict, is_error: bool = False):
    return SimpleNamespace(
        content=[
            {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "is_error": is_error,
                "content": [{"type": "text", "text": json.dumps(payload)}],
            }
        ]
    )


class _FakeClient:
    def __init__(self, messages):
        self._messages = messages

    async def connect(self):
        return None

    async def query(self, prompt, session_id=None):
        return None

    async def receive_response(self):
        for m in self._messages:
            yield m


def _run(messages, inline: bool):
    runtime = object.__new__(ClaudeChatRuntime)
    runtime._sessions = {}
    runtime._sessions_lock = asyncio.Lock()
    runtime._info = {}
    runtime._options = None
    deltas: list[str] = []

    async def on_delta(d: str):
        deltas.append(d)

    async def go():
        runtime._sessions["s"] = _RuntimeSession(client=_FakeClient(messages), connected=True)
        return await runtime.stream_reply("s", "x", on_delta=on_delta, inline_proposals=inline)

    return asyncio.run(go()), deltas


MESSAGES = [
    _text_delta("Proposing a starter position."),
    _tool_use("t1", "mcp__proposals__propose_trade"),
    _tool_result("t1", {"ok": True, "intent": {"id": "int-1", "symbol": "NVDA"}}),
    _tool_use("t2", "mcp__proposals__propose_trade"),
    _tool_result("t2", {"ok": False, "violations": ["over cap"]}),
    _text_delta("Approve it in the card above."),
]


def test_markers_spliced_in_place_when_enabled():
    reply, deltas = _run(MESSAGES, inline=True)
    assert "[proposal](proposal:int-1)" in reply.text
    # Placed between the two text runs, where the tool was called.
    assert reply.text.index("starter position") < reply.text.index("proposal:int-1") < reply.text.index("Approve it")
    # Streamed live too, so the card appears mid-reply.
    assert any("proposal:int-1" in d for d in deltas)
    # A rejected proposal (violations) creates nothing to reference.
    assert reply.text.count("proposal:") == 1


def test_no_markers_for_plain_text_consumers():
    reply, deltas = _run(MESSAGES, inline=False)
    assert "proposal:" not in reply.text
    assert not any("proposal:" in d for d in deltas)


def test_ids_from_stage_play_tranche():
    text = json.dumps({"play_id": "p", "created": [{"intent_id": "a"}, {"intent_id": "b"}]})
    assert proposal_ids_from_tool_result("mcp__plays__stage_play_tranche", text) == ["a", "b"]
    assert proposal_ids_from_tool_result("mcp__plays__get_play", text) == []
    assert proposal_ids_from_tool_result("mcp__proposals__propose_trade", "not json") == []


def test_scheduled_reports_share_the_report_frame():
    """Scheduled reports open with TL;DR + Key numbers so the report reader can
    show a TL;DR card and key-numbers strip."""
    from app.services.task_scheduler_service import REPORT_FORMAT, _DEFAULT_TASKS

    assert "## TL;DR" in REPORT_FORMAT and "## Key numbers" in REPORT_FORMAT
    prompts = {t["name"]: t["prompt"] for t in _DEFAULT_TASKS}
    for name in ("morning_brief", "weekly_review"):
        assert "TL;DR" in prompts[name] and "Key numbers" in prompts[name]
