from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.claude_sdk_config import parse_setting_sources, resolve_sdk_cwd

settings = get_settings()
logger = logging.getLogger(__name__)

_MEMORY_START = "<!-- MYPF_MEMORY_START -->"
_MEMORY_END = "<!-- MYPF_MEMORY_END -->"
_MAX_FACTS_PER_UPDATE = 3
_memory_lock = asyncio.Lock()


def _extract_text(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, str):
        return message

    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)

    text = getattr(message, "text", None)
    if isinstance(text, str):
        return text
    return ""


def _extract_json_block(text: str) -> dict[str, Any] | None:
    if not text.strip():
        return None

    fenced = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text)
    if fenced:
        candidate = fenced.group(1)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    for match in re.finditer(r"\{[\s\S]*\}", text):
        candidate = match.group(0)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    return None


def _looks_sensitive(text: str) -> bool:
    lower = text.lower()
    return any(
        token in lower
        for token in (
            "api key",
            "apikey",
            "secret",
            "token",
            "password",
            "private key",
            "seed phrase",
        )
    )


def _has_memory_cue(text: str) -> bool:
    lower = text.lower()
    explicit = (
        "remember this",
        "remember that",
        "please remember",
        "for future",
        "from now on",
        "my name is",
        "call me ",
    )
    if any(token in lower for token in explicit):
        return True

    durable = (
        "i prefer",
        "i want",
        "always",
        "never",
        "my goal",
        "risk tolerance",
        "my style",
        "my tone",
    )
    return any(token in lower for token in durable)


def _memory_file() -> Path:
    cwd = resolve_sdk_cwd()
    return cwd / ".claude" / "CLAUDE.md"


def _default_memory_doc() -> str:
    return (
        "# MyPF Claude Runtime Memory\n\n"
        "## Guidance\n"
        "- Persist only durable user preferences, constraints, goals, and workflow decisions.\n"
        "- Never store secrets, credentials, exact balances, or transient market noise.\n\n"
        "## Learned Memory\n"
        f"{_MEMORY_START}\n"
        "- (none yet)\n"
        f"{_MEMORY_END}\n"
    )


def _read_memory_lines(doc: str) -> list[str]:
    start = doc.find(_MEMORY_START)
    end = doc.find(_MEMORY_END)
    if start == -1 or end == -1 or end <= start:
        return []

    block = doc[start + len(_MEMORY_START) : end]
    lines: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line.startswith("- "):
            continue
        item = line[2:].strip()
        if item and item.lower() != "(none yet)":
            lines.append(item)
    return lines


def _normalize_fact_key(line: str) -> str:
    core = re.sub(r"^\[[^\]]+\]\s*", "", line.strip().lower())
    core = re.sub(r"[^a-z0-9%/.\- ]+", "", core)
    core = re.sub(r"\s+", " ", core).strip()
    return core


def _write_memory_lines(path: Path, lines: list[str]) -> None:
    if path.exists():
        doc = path.read_text(encoding="utf-8")
    else:
        doc = _default_memory_doc()

    if _MEMORY_START not in doc or _MEMORY_END not in doc:
        if not doc.endswith("\n"):
            doc += "\n"
        doc += (
            "\n## Learned Memory\n"
            f"{_MEMORY_START}\n"
            "- (none yet)\n"
            f"{_MEMORY_END}\n"
        )

    start = doc.find(_MEMORY_START)
    end = doc.find(_MEMORY_END)

    memory_block_lines = ["- (none yet)"] if not lines else [f"- {line}" for line in lines]
    memory_block = "\n".join(memory_block_lines)

    updated = (
        doc[: start + len(_MEMORY_START)]
        + "\n"
        + memory_block
        + "\n"
        + doc[end:]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")


async def _distill(user_message: str, assistant_message: str, current_memory: list[str]) -> list[str]:
    from claude_agent_sdk import ClaudeAgentOptions, query

    env_key = (settings.anthropic_api_key or "").strip()
    if env_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", env_key)

    prompt_payload = {
        "purpose": "Extract durable memory for a long-running portfolio copilot.",
        "current_memory": current_memory[-40:],
        "latest_exchange": {
            "user": user_message[:8000],
            "assistant": assistant_message[:12000],
        },
        "rules": [
            "Keep only durable user preferences, constraints, goals, and workflow decisions.",
            "Never store secrets, credentials, exact balances, or one-off market commentary.",
            f"Return at most {_MAX_FACTS_PER_UPDATE} short facts.",
            "If nothing durable exists, return store=false.",
        ],
        "response_schema": {
            "store": "boolean",
            "facts": ["string"],
        },
        "output_mode": "json_only",
    }

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a strict memory distiller. "
            "Output only valid JSON matching the provided schema."
        ),
        model=settings.claude_memory_model,
        cwd=str(resolve_sdk_cwd()),
        setting_sources=parse_setting_sources(settings.claude_setting_sources, require_project=True),
        max_turns=2,
        allowed_tools=[],
    )

    chunks: list[str] = []
    async for message in query(prompt=json.dumps(prompt_payload), options=options):
        text = _extract_text(message)
        if text:
            chunks.append(text)

    raw = "\n".join(chunks)
    parsed = _extract_json_block(raw)
    if not parsed or not bool(parsed.get("store")):
        return []

    facts_raw = parsed.get("facts", [])
    if not isinstance(facts_raw, list):
        return []

    facts: list[str] = []
    for item in facts_raw:
        if not isinstance(item, str):
            continue
        clean = " ".join(item.split()).strip(" -")
        if not clean:
            continue
        if _looks_sensitive(clean):
            continue
        facts.append(clean[:220])

    # Preserve order while capping size.
    uniq: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        key = _normalize_fact_key(fact)
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(fact)
        if len(uniq) >= _MAX_FACTS_PER_UPDATE:
            break
    return uniq


async def _run_memory_update(user_message: str, assistant_message: str) -> None:
    if not settings.claude_memory_enabled:
        return
    if settings.claude_memory_strategy != "distill":
        return
    if not user_message.strip():
        return
    if "runtime unavailable" in assistant_message.lower():
        return
    if len(user_message.strip()) < 12:
        return
    if not _has_memory_cue(user_message):
        return

    path = _memory_file()
    async with _memory_lock:
        if path.exists():
            doc = path.read_text(encoding="utf-8")
        else:
            doc = _default_memory_doc()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(doc, encoding="utf-8")

        current = _read_memory_lines(doc)
        new_facts = await _distill(user_message, assistant_message, current)
        if not new_facts:
            return

        today = datetime.utcnow().strftime("%Y-%m-%d")
        merged = list(current)
        known = {_normalize_fact_key(x) for x in merged}

        for fact in new_facts:
            stamped = f"[{today}] {fact}"
            key = _normalize_fact_key(stamped)
            if key in known:
                continue
            known.add(key)
            merged.append(stamped)

        max_facts = max(20, min(int(settings.claude_memory_max_facts), 200))
        merged = merged[-max_facts:]
        _write_memory_lines(path, merged)


def schedule_memory_distillation(user_message: str, assistant_message: str) -> None:
    if not settings.claude_memory_enabled:
        return
    # When strategy is "self_managed", Archie manages its own memory via
    # Read/Write/Edit tools — no external distillation needed.
    if settings.claude_memory_strategy == "self_managed":
        return
    if settings.claude_memory_strategy != "distill":
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _safe() -> None:
        try:
            await asyncio.wait_for(_run_memory_update(user_message, assistant_message), timeout=45)
        except Exception as exc:  # noqa: BLE001
            logger.debug("memory distillation skipped: %s", exc)

    loop.create_task(_safe())
