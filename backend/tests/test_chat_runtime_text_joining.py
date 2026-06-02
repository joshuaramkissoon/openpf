"""Regression: assistant text runs separated by tool calls must keep a paragraph
break in the persisted reply.

During streaming the frontend splits text on tool boundaries (groupSegments) and
renders each run in its own block, so the live view looks fine. But the persisted
`message.content` is `"".join(chunks)` over every streamed delta — so two text runs
emitted around a tool call ("...placing the order." then "I'll get the FX rate...")
joined with no separator and rendered as one mashed-together block once the tool
calls collapsed to a summary. Insert "\n\n" when text resumes after tool activity so
the completed message reads as separate paragraphs, matching the streaming layout.
"""

from pathlib import Path
from types import SimpleNamespace
import asyncio
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.claude_chat_runtime import ClaudeChatRuntime, _RuntimeSession


def _text_delta(text: str):
    """A streaming text delta (Anthropic content_block_delta shape)."""
    return SimpleNamespace(event={"type": "content_block_delta", "delta": {"text": text}})


def _tool_use(tool_id: str, name: str, tool_input: dict | None = None):
    return SimpleNamespace(content=[{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input or {}}])


def _tool_result(tool_id: str, is_error: bool = False):
    return SimpleNamespace(content=[{"type": "tool_result", "tool_use_id": tool_id, "is_error": is_error}])


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


def _run_stream(messages):
    """Drive stream_reply with a fake SDK client; return (reply, deltas_seen)."""
    runtime = object.__new__(ClaudeChatRuntime)
    runtime._sessions = {}
    runtime._sessions_lock = asyncio.Lock()
    runtime._info = {}
    runtime._options = None

    deltas: list[str] = []

    async def on_delta(d: str):
        deltas.append(d)

    async def go():
        runtime._sessions["sess-1"] = _RuntimeSession(client=_FakeClient(messages), connected=True)
        reply = await runtime.stream_reply("sess-1", "buy KEYS", on_delta=on_delta)
        return reply, deltas

    return asyncio.run(go())


def test_text_runs_around_tool_calls_get_paragraph_breaks():
    messages = [
        _text_delta("I'll resolve KEYS and pull a live price before placing the order."),
        _tool_use("t1", "mcp__marketdata__price", {"symbol": "KEYS"}),
        _tool_result("t1"),
        _text_delta("£100 ≈ $134.78 at 1.3478, so ≈0.389 shares. Placing the market buy now."),
        _tool_use("t2", "mcp__trading212__place_order", {"ticker": "KEYS_US_EQ"}),
        _tool_result("t2"),
        _text_delta("Order placed ✅"),
    ]

    reply, _ = _run_stream(messages)

    # The three text runs are now separated by blank lines (markdown paragraph breaks).
    assert (
        reply.text
        == "I'll resolve KEYS and pull a live price before placing the order.\n\n"
        "£100 ≈ $134.78 at 1.3478, so ≈0.389 shares. Placing the market buy now.\n\n"
        "Order placed ✅"
    )
    # The bug signature — text mashed directly together — must be gone.
    assert "order.£100" not in reply.text
    assert "now.Order placed" not in reply.text


def test_consecutive_deltas_within_one_run_are_not_split():
    """Deltas with no intervening tool call concatenate verbatim (the model owns
    its own spacing/newlines); we must not inject breaks mid-sentence."""
    messages = [
        _text_delta("Your portfolio is "),
        _text_delta("up 2.3% today.\n\n"),
        _text_delta("Biggest mover: NVDA."),
    ]

    reply, _ = _run_stream(messages)

    assert reply.text == "Your portfolio is up 2.3% today.\n\nBiggest mover: NVDA."


def test_break_not_inserted_before_first_text_run():
    """Tool calls that precede any text (assistant acts first, then writes) must
    not produce a leading blank line."""
    messages = [
        _tool_use("t1", "mcp__marketdata__price", {"symbol": "KEYS"}),
        _tool_result("t1"),
        _text_delta("KEYS is trading at $346.61."),
    ]

    reply, _ = _run_stream(messages)

    assert reply.text == "KEYS is trading at $346.61."


def test_streaming_deltas_are_emitted_without_injected_breaks():
    """The injected break lives only in the persisted text. on_delta (which feeds
    the streaming view) still receives the raw deltas — streaming relies on tool
    grouping for separation, so it must stay untouched."""
    messages = [
        _text_delta("Before the tool."),
        _tool_use("t1", "mcp__marketdata__price", {"symbol": "KEYS"}),
        _tool_result("t1"),
        _text_delta("After the tool."),
    ]

    _, deltas = _run_stream(messages)

    assert deltas == ["Before the tool.", "After the tool."]
