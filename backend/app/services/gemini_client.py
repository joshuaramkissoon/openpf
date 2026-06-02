"""Minimal Gemini client for small structured-output calls.

A thin wrapper around google-genai that returns a parsed Pydantic object. It is
intended for cheap, fast background tasks (e.g. chat-title generation) — there
is deliberately no telemetry here; it just shows the structured-output call.
"""

from __future__ import annotations

from typing import Any, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.core.config import get_settings

settings = get_settings()

T = TypeVar("T", bound=BaseModel)


async def generate_structured(
    *,
    model: str,
    contents: str | list[Any],
    response_schema: type[T],
    thinking_level: str = "low",
) -> T | None:
    """Call Gemini with Pydantic structured output.

    Returns an instance of ``response_schema`` (``response.parsed``), or ``None``
    if no API key is configured. API/network errors propagate so callers can
    decide how to handle them.
    """
    if not settings.gemini_api_key:
        return None

    client = genai.Client(api_key=settings.gemini_api_key)
    config: dict[str, Any] = {
        "response_mime_type": "application/json",
        "response_schema": response_schema,
    }
    if thinking_level:
        config["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)

    response = await client.aio.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )
    return response.parsed
