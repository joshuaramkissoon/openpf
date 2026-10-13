"""User-facing wording for chat failures and chat input clean-up.

Raw SDK/CLI exceptions ("Command failed with exit code 1", a pydantic dump)
mean nothing to a user. These map them to one actionable sentence that the UI
shows (and that is stored as Archie's reply so the history makes sense).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

MAX_CHAT_CHARS = 12000

_SETUP_HINT = (
    "Log in with the Claude Code CLI (`claude login`), or set ANTHROPIC_API_KEY / "
    "CLAUDE_CODE_OAUTH_TOKEN in backend/.env, then restart OpenPF."
)
_AUTH_HINTS = ("login", "log in", "api key", "apikey", "authentication", "unauthorized", "401",
               "oauth", "credit balance", "not logged")
# C0 control chars except tab/newline/carriage return.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def friendly_chat_error(exc: BaseException) -> str:
    name = exc.__class__.__name__
    text = str(exc) or name
    low = text.lower()
    logger.warning("Chat turn failed: %s: %s", name, text[:500])
    if name == "CLINotFoundError" or "claude code not found" in low or "cli not found" in low:
        return f"Archie is unavailable: the Claude Code CLI isn't installed. Install it (`npm i -g @anthropic-ai/claude-code`). {_SETUP_HINT}"
    if any(h in low for h in _AUTH_HINTS):
        return f"Archie is unavailable: Claude isn't authenticated. {_SETUP_HINT}"
    if "rate limit" in low or "429" in low or "overloaded" in low or "529" in low:
        return "Claude is rate limited or overloaded right now. Try again in a minute."
    if name in {"CLIConnectionError", "ProcessError"}:
        return f"Archie couldn't start the Claude runtime ({name}). Check the backend logs. {_SETUP_HINT}"
    return f"Archie hit an error and stopped: {text[:300]}"


def clean_chat_input(raw: str) -> str:
    """Strip control/binary characters and surrounding whitespace. Unicode
    (emoji, accents, CJK) is kept as-is."""
    return _CONTROL.sub("", raw or "").strip()
