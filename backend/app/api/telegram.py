from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.telegram import (
    TelegramAskRequest,
    TelegramAskResponse,
    TelegramConfigUpdate,
    TelegramConfigView,
    TelegramPollResponse,
    TelegramTestRequest,
    TelegramTestResponse,
)
from app.services.config_store import ConfigStore
from app.services.telegram_service import handle_telegram_text, process_telegram_updates, send_telegram_notification

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.get("/config", response_model=TelegramConfigView)
def get_telegram_config(db: Session = Depends(get_db)) -> TelegramConfigView:
    store = ConfigStore(db)
    return TelegramConfigView(**store.telegram_public())


@router.put("/config", response_model=TelegramConfigView)
def update_telegram_config(payload: TelegramConfigUpdate, db: Session = Depends(get_db)) -> TelegramConfigView:
    store = ConfigStore(db)
    store.set_telegram(payload.model_dump())
    return TelegramConfigView(**store.telegram_public())


@router.post("/test", response_model=TelegramTestResponse)
def test_telegram(payload: TelegramTestRequest, db: Session = Depends(get_db)) -> TelegramTestResponse:
    sent = send_telegram_notification(db, payload.message)
    detail = "message sent" if sent else "message not sent (verify enabled/token/chat_id/network)"
    return TelegramTestResponse(sent=sent, detail=detail)


@router.post("/poll", response_model=TelegramPollResponse)
def poll_telegram(db: Session = Depends(get_db)) -> TelegramPollResponse:
    count = process_telegram_updates(db)
    return TelegramPollResponse(processed_updates=count)


@router.post("/ask", response_model=TelegramAskResponse)
def ask_assistant(payload: TelegramAskRequest, db: Session = Depends(get_db)) -> TelegramAskResponse:
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    reply = handle_telegram_text(db, payload.message)
    return TelegramAskResponse(reply=reply)
