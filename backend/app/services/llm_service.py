from __future__ import annotations

import json

import httpx

from app.core.config import get_settings

settings = get_settings()


def maybe_answer_with_openai(question: str, context: str) -> str | None:
    if not settings.openai_api_key:
        return None

    payload = {
        "model": settings.openai_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are MyPF, a concise portfolio operator assistant. "
                    "Use the provided portfolio context. Do not fabricate prices. "
                    "Be precise, practical, and include risk caveats if asked about execution."
                ),
            },
            {
                "role": "user",
                "content": f"Context:\n{context}\n\nQuestion:\n{question}",
            },
        ],
        "temperature": 0.2,
    }

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
        if response.status_code >= 400:
            return None

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return None

        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = [part.get("text", "") for part in content if isinstance(part, dict)]
            text = "\n".join([p for p in parts if p])
            return text.strip() or None
        return None
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError):
        return None
