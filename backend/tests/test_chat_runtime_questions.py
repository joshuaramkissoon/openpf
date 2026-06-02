"""AskUserQuestion (clarifying questions) wiring in the chat runtime.

The agent's built-in AskUserQuestion tool reaches the SDK as a `can_use_tool`
permission request. With no callback it hard-errors (`<error>Answer questions?</error>`).
We provide a per-session callback that routes the questions to the live turn's UI
channel and feeds the user's selections back as the tool's `updated_input.answers`.
"""

from pathlib import Path
import asyncio
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from app.services.claude_chat_runtime import ClaudeChatRuntime, _RuntimeSession


def _runtime() -> ClaudeChatRuntime:
    rt = object.__new__(ClaudeChatRuntime)
    rt._sessions = {}
    rt._sessions_lock = asyncio.Lock()
    rt._info = {}
    rt._options = None
    return rt


_QUESTIONS = {
    "questions": [
        {
            "question": "Which KEYS did you mean?",
            "header": "Ticker",
            "options": [
                {"label": "Keysight (KEYS_US_EQ)", "description": "NYSE test & measurement"},
                {"label": "Keystone Law", "description": "UK law firm"},
            ],
            "multiSelect": False,
        }
    ]
}


def test_answers_flow_back_as_updated_input():
    rt = _runtime()

    async def asker(input_data):
        # The UI would render input_data["questions"] and collect a selection.
        return {"Which KEYS did you mean?": "Keysight (KEYS_US_EQ)"}

    rt._sessions["s1"] = _RuntimeSession(client=object(), connected=True, question_asker=asker)
    cb = rt._build_can_use_tool("s1")

    result = asyncio.run(cb("AskUserQuestion", _QUESTIONS, None))

    assert isinstance(result, PermissionResultAllow)
    assert result.updated_input == {
        "questions": _QUESTIONS["questions"],
        "answers": {"Which KEYS did you mean?": "Keysight (KEYS_US_EQ)"},
    }


def test_no_channel_falls_back_to_conversational_deny():
    """No active UI channel → deny with guidance so Claude asks in plain text
    instead of the tool hard-erroring."""
    rt = _runtime()
    rt._sessions["s1"] = _RuntimeSession(client=object(), connected=True, question_asker=None)
    cb = rt._build_can_use_tool("s1")

    result = asyncio.run(cb("AskUserQuestion", _QUESTIONS, None))

    assert isinstance(result, PermissionResultDeny)
    assert "text reply" in result.message


def test_dismissed_question_denies_gracefully():
    rt = _runtime()

    async def asker(input_data):
        return None  # user dismissed

    rt._sessions["s1"] = _RuntimeSession(client=object(), connected=True, question_asker=asker)
    cb = rt._build_can_use_tool("s1")

    result = asyncio.run(cb("AskUserQuestion", _QUESTIONS, None))

    assert isinstance(result, PermissionResultDeny)


def test_asker_exception_denies_gracefully():
    rt = _runtime()

    async def asker(input_data):
        raise RuntimeError("socket closed")

    rt._sessions["s1"] = _RuntimeSession(client=object(), connected=True, question_asker=asker)
    cb = rt._build_can_use_tool("s1")

    result = asyncio.run(cb("AskUserQuestion", _QUESTIONS, None))

    assert isinstance(result, PermissionResultDeny)


def test_other_tools_are_denied_not_blanket_allowed():
    """The callback only fires for non-allowlisted tools; it must not become a
    blanket allow that neuters the permission system on a live trading app."""
    rt = _runtime()
    rt._sessions["s1"] = _RuntimeSession(client=object(), connected=True, question_asker=None)
    cb = rt._build_can_use_tool("s1")

    for tool in ("Bash", "Write", "mcp__trading212__place_order"):
        result = asyncio.run(cb(tool, {"any": "input"}, None))
        assert isinstance(result, PermissionResultDeny), f"{tool} should be denied"


def test_stream_reply_exposes_question_asker_during_turn_and_clears_after():
    """The callback reads state.question_asker; stream_reply must set it for the
    turn and reset it afterward so it never leaks to a later (or idle) turn."""
    rt = _runtime()
    observed: dict = {}

    async def asker(_input):
        return {}

    async def on_delta(_d):
        return None

    class _FakeClient:
        async def connect(self):
            return None

        async def query(self, *a, **k):
            return None

        async def receive_response(self):
            observed["during"] = session.question_asker
            return
            yield  # pragma: no cover — marks this an async generator

    session = _RuntimeSession(client=_FakeClient(), connected=True)
    rt._sessions["s1"] = session

    asyncio.run(rt.stream_reply("s1", "hi", on_delta=on_delta, on_status=None, on_question=asker))

    assert observed["during"] is asker
    assert session.question_asker is None


def test_multiselect_answer_passes_through_as_list():
    rt = _runtime()

    async def asker(input_data):
        return {"Pick sectors": ["Tech", "Energy"]}

    rt._sessions["s1"] = _RuntimeSession(client=object(), connected=True, question_asker=asker)
    cb = rt._build_can_use_tool("s1")

    payload = {"questions": [{"question": "Pick sectors", "header": "Sectors", "options": [], "multiSelect": True}]}
    result = asyncio.run(cb("AskUserQuestion", payload, None))

    assert isinstance(result, PermissionResultAllow)
    assert result.updated_input["answers"] == {"Pick sectors": ["Tech", "Energy"]}
