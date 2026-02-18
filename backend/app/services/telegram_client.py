from __future__ import annotations

from typing import Any

import httpx


class TelegramError(RuntimeError):
    pass


def _endpoint(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def call_telegram(token: str, method: str, *, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
    if not token:
        raise TelegramError("Telegram bot token is not configured")

    with httpx.Client(timeout=20.0) as client:
        response = client.post(_endpoint(token, method), json=payload, params=params)

    if response.status_code >= 400:
        raise TelegramError(f"Telegram API error {response.status_code}: {response.text[:400]}")

    body = response.json()
    if not body.get("ok"):
        raise TelegramError(f"Telegram API returned error: {body}")

    return body.get("result")


def send_message(token: str, chat_id: str, text: str) -> Any:
    return call_telegram(
        token,
        "sendMessage",
        payload={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
    )


def get_updates(token: str, *, offset: int | None = None, timeout: int = 0) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    result = call_telegram(token, "getUpdates", params=params)
    if isinstance(result, list):
        return result
    return []
