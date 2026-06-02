"""Backfill titles for existing chat sessions using the Gemini title model.

Run from the backend/ directory with the project venv:

    python -m scripts.backfill_chat_titles              # only placeholder-titled chats
    python -m scripts.backfill_chat_titles --all        # reconsider every chat
    python -m scripts.backfill_chat_titles --dry-run    # show proposals, change nothing

Existing chats keep their `updated_at` (so the rail isn't reordered); only the
title is rewritten.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.database import SessionLocal, init_db
from app.models.entities import ChatMessage, ChatSession
from app.services.chat_title_service import PLACEHOLDER_TITLES, build_transcript, generate_title

settings = get_settings()


def _has_assistant_reply(db, session_id: str) -> bool:
    return (
        db.execute(
            select(ChatMessage.id)
            .where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
            .limit(1)
        ).first()
        is not None
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="reconsider every chat, not just placeholder-titled ones",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print proposed titles without saving",
    )
    args = parser.parse_args()

    if not settings.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY is not set — cannot backfill titles.")

    init_db()
    with SessionLocal() as db:
        sessions = list(
            db.execute(select(ChatSession).order_by(ChatSession.created_at)).scalars().all()
        )

    considered = renamed = 0
    for session in sessions:
        is_placeholder = (session.title or "").strip().lower() in PLACEHOLDER_TITLES
        if not args.all and not is_placeholder:
            continue

        with SessionLocal() as db:
            if not _has_assistant_reply(db, session.id):
                continue
            transcript = build_transcript(db, session.id)

        considered += 1
        try:
            new_title = await generate_title(session.title or "", transcript)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {session.id}  error: {exc}")
            continue

        if not new_title:
            print(f"  = {session.id}  keep: {session.title!r}")
            continue

        renamed += 1
        print(f"  → {session.id}  {session.title!r} -> {new_title!r}")
        if not args.dry_run:
            with SessionLocal() as db:
                # Preserve updated_at (already loaded on `session`) so the rail
                # isn't reordered by the backfill — only the title changes.
                db.execute(
                    update(ChatSession)
                    .where(ChatSession.id == session.id)
                    .values(title=new_title[:240], updated_at=session.updated_at)
                )
                db.commit()

    print(f"\nDone. considered={considered} renamed={renamed} dry_run={args.dry_run}")


if __name__ == "__main__":
    asyncio.run(main())
