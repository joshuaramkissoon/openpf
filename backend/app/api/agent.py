import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.database import SessionLocal
from app.core.config import get_settings
from app.schemas.agent import (
    AgentRunItem,
    AgentRunRequest,
    AgentRunResponse,
    ExecutionEventItem,
    IntentActionResponse,
    IntentDecisionRequest,
    IntentExecuteRequest,
    TradeIntentItem,
)
from app.schemas.chat import (
    ChatDeleteResponse,
    ChatMessageItem,
    ChatRuntimeInfo,
    ChatSendRequest,
    ChatSendResponse,
    ChatSessionCreate,
    ChatSessionItem,
)
from app.models.entities import Thesis
from app.services.agent_service import get_run, list_runs, run_agent
from app.services.chat_service import (
    append_assistant_message,
    append_user_message,
    build_prompt_for_session,
    create_session,
    delete_session,
    list_messages,
    list_sessions,
    require_session,
    send_message,
)
from app.services.claude_chat_runtime import claude_chat_runtime
from app.services.claude_memory_service import schedule_memory_distillation
from app.services.execution_service import ExecutionError, approve_intent, execute_intent, list_events, list_intents, reject_intent
from app.schemas.artifacts import ArtifactDetail, ArtifactItem
from app.services.artifact_service import get_artifact, list_artifacts

router = APIRouter(prefix="/agent", tags=["agent"])
settings = get_settings()


def _session_item(row) -> ChatSessionItem:
    return ChatSessionItem(id=row.id, created_at=row.created_at, updated_at=row.updated_at, title=row.title)


def _message_item(row) -> ChatMessageItem:
    return ChatMessageItem(
        id=row.id,
        session_id=row.session_id,
        created_at=row.created_at,
        role=row.role,
        content=row.content,
        tool_calls=row.tool_calls,
    )


@router.post("/run", response_model=AgentRunResponse)
def run(payload: AgentRunRequest, db: Session = Depends(get_db)) -> AgentRunResponse:
    result = run_agent(db, include_watchlist=payload.include_watchlist, execute_auto=payload.execute_auto)
    return AgentRunResponse(**result)


@router.get("/runs", response_model=list[AgentRunItem])
def runs(db: Session = Depends(get_db)) -> list[AgentRunItem]:
    rows = list_runs(db)
    return [
        AgentRunItem(
            id=r.id,
            created_at=r.created_at,
            market_regime=r.market_regime,
            portfolio_score=r.portfolio_score,
            status=r.status,
        )
        for r in rows
    ]


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
def run_detail(run_id: str, db: Session = Depends(get_db)) -> AgentRunResponse:
    row = get_run(db, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")

    intents = [i for i in list_intents(db, limit=500) if i.run_id == run_id]
    theses_created = len(
        db.execute(
            select(Thesis.id).where(Thesis.source_run_id == run_id)
        ).all()
    )
    return AgentRunResponse(
        run_id=row.id,
        created_at=row.created_at,
        market_regime=row.market_regime,
        portfolio_score=row.portfolio_score,
        summary_markdown=row.summary_markdown,
        intents_created=len(intents),
        theses_created=theses_created,
    )


@router.get("/intents", response_model=list[TradeIntentItem])
def intents(db: Session = Depends(get_db)) -> list[TradeIntentItem]:
    rows = list_intents(db)
    return [
        TradeIntentItem(
            id=r.id,
            created_at=r.created_at,
            status=r.status,
            symbol=r.symbol,
            instrument_code=r.instrument_code,
            side=r.side,
            order_type=r.order_type,
            quantity=r.quantity,
            estimated_notional=r.estimated_notional,
            expected_edge=r.expected_edge,
            confidence=r.confidence,
            risk_score=r.risk_score,
            rationale=r.rationale,
            broker_mode=r.broker_mode,
            approved_at=r.approved_at,
            executed_at=r.executed_at,
            broker_order_id=r.broker_order_id,
            execution_price=r.execution_price,
            failure_reason=r.failure_reason,
        )
        for r in rows
    ]


@router.post("/intents/{intent_id}/approve", response_model=IntentActionResponse)
def approve(intent_id: str, payload: IntentDecisionRequest, db: Session = Depends(get_db)) -> IntentActionResponse:
    try:
        intent = approve_intent(db, intent_id, note=payload.note)
        return IntentActionResponse(intent_id=intent.id, status=intent.status, message="intent approved")
    except ExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/intents/{intent_id}/reject", response_model=IntentActionResponse)
def reject(intent_id: str, payload: IntentDecisionRequest, db: Session = Depends(get_db)) -> IntentActionResponse:
    try:
        intent = reject_intent(db, intent_id, note=payload.note)
        return IntentActionResponse(intent_id=intent.id, status=intent.status, message="intent rejected")
    except ExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/intents/{intent_id}/execute", response_model=IntentActionResponse)
def execute(intent_id: str, payload: IntentExecuteRequest, db: Session = Depends(get_db)) -> IntentActionResponse:
    try:
        intent = execute_intent(db, intent_id, force_live=payload.force_live)
        return IntentActionResponse(intent_id=intent.id, status=intent.status, message="intent executed")
    except ExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/events", response_model=list[ExecutionEventItem])
def events(db: Session = Depends(get_db)) -> list[ExecutionEventItem]:
    rows = list_events(db)
    return [
        ExecutionEventItem(
            created_at=r.created_at,
            intent_id=r.intent_id,
            level=r.level,
            message=r.message,
            payload=r.payload or {},
        )
        for r in rows
    ]


@router.get("/artifacts", response_model=list[ArtifactItem])
def artifacts() -> list[ArtifactItem]:
    items = list_artifacts()
    return [ArtifactItem(**item) for item in items]


@router.get("/artifacts/{path:path}", response_model=ArtifactDetail)
def artifact_detail(path: str) -> ArtifactDetail:
    result = get_artifact(path)
    if result is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return ArtifactDetail(**result)


@router.get("/chat/sessions", response_model=list[ChatSessionItem])
def chat_sessions(db: Session = Depends(get_db)) -> list[ChatSessionItem]:
    rows = list_sessions(db, limit=50)
    return [
        ChatSessionItem(
            id=row.id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            title=row.title,
        )
        for row in rows
    ]


@router.post("/chat/sessions", response_model=ChatSessionItem)
def create_chat_session(payload: ChatSessionCreate, db: Session = Depends(get_db)) -> ChatSessionItem:
    row = create_session(db, title=payload.title)
    return _session_item(row)


@router.get("/chat/sessions/{session_id}/messages", response_model=list[ChatMessageItem])
def chat_messages(session_id: str, db: Session = Depends(get_db)) -> list[ChatMessageItem]:
    rows = list_messages(db, session_id, limit=200)
    return [_message_item(row) for row in rows]


@router.get("/chat/runtime", response_model=ChatRuntimeInfo)
def chat_runtime() -> ChatRuntimeInfo:
    return ChatRuntimeInfo(**claude_chat_runtime.runtime_info())


@router.get("/chat/runtime/mcp-health")
async def mcp_health() -> dict[str, dict[str, str]]:
    return await claude_chat_runtime.check_mcp_health()


@router.delete("/chat/sessions/{session_id}", response_model=ChatDeleteResponse)
async def chat_delete_session(session_id: str, db: Session = Depends(get_db)) -> ChatDeleteResponse:
    deleted = delete_session(db, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="chat session not found")
    await claude_chat_runtime.drop_session(session_id)
    return ChatDeleteResponse(id=session_id, deleted=True)


@router.post("/chat/sessions/{session_id}/messages", response_model=ChatSendResponse)
async def chat_send(session_id: str, payload: ChatSendRequest, db: Session = Depends(get_db)) -> ChatSendResponse:
    session, user_row, assistant_row = await send_message(
        db,
        session_id=session_id,
        content=payload.content,
        account_kind=payload.account_kind,
        display_currency=payload.display_currency,
        redact_values=payload.redact_values,
    )
    return ChatSendResponse(
        session=_session_item(session),
        user_message=_message_item(user_row),
        assistant_message=_message_item(assistant_row),
    )


@router.websocket("/chat/sessions/{session_id}/stream")
async def chat_stream(session_id: str, websocket: WebSocket):
    await websocket.accept()
    db = SessionLocal()
    try:
        incoming = await websocket.receive_json()
        payload = ChatSendRequest.model_validate(incoming)
        session = require_session(db, session_id)
        user_row = append_user_message(db, session, payload.content)

        await websocket.send_json(
            {
                "type": "ack",
                "session": _session_item(session).model_dump(mode="json"),
                "user_message": _message_item(user_row).model_dump(mode="json"),
            }
        )

        prompt = build_prompt_for_session(
            db=db,
            session=session,
            content=payload.content,
            account_kind=payload.account_kind,
            display_currency=payload.display_currency,
            redact_values=payload.redact_values,
        )

        streamed_chunks: list[str] = []
        collected_tool_calls: list[dict] = []

        async def _on_delta(chunk: str) -> None:
            streamed_chunks.append(chunk)
            await websocket.send_json({"type": "delta", "delta": chunk})

        async def _on_status(phase: str, message: str, tool_input: dict | None = None) -> None:
            msg: dict = {"type": "status", "phase": phase, "message": message}
            if tool_input:
                msg["tool_input"] = tool_input
            await websocket.send_json(msg)
            if phase in ("tool_start", "tool_result"):
                entry: dict = {"phase": phase, "message": message}
                if tool_input:
                    entry["tool_input"] = tool_input
                collected_tool_calls.append(entry)

        assistant_text: str
        stop_reason: str | None = None
        result_subtype: str | None = None

        if not payload.content.strip():
            assistant_text = "No message provided."
        elif settings.agent_provider != "claude":
            assistant_text = "Claude provider is disabled. Set AGENT_PROVIDER=claude to enable chat."
        else:
            timeout = getattr(settings, "chat_stream_timeout", 300)
            try:
                reply = await asyncio.wait_for(
                    claude_chat_runtime.stream_reply(
                        chat_session_id=session.id,
                        prompt=prompt,
                        on_delta=_on_delta,
                        on_status=_on_status,
                    ),
                    timeout=timeout,
                )
                assistant_text = reply.text
                stop_reason = reply.stop_reason
                result_subtype = reply.result_subtype
            except asyncio.TimeoutError:
                assistant_text = "".join(streamed_chunks).strip() or "Response timed out."
                result_subtype = "error_timeout"

        if not assistant_text:
            assistant_text = "".join(streamed_chunks).strip() or "No response generated."

        assistant_row = append_assistant_message(
            db, session, assistant_text,
            tool_calls=collected_tool_calls if collected_tool_calls else None,
        )
        schedule_memory_distillation(user_message=payload.content, assistant_message=assistant_text)
        await websocket.send_json({
            "type": "done",
            "session": _session_item(session).model_dump(mode="json"),
            "assistant_message": _message_item(assistant_row).model_dump(mode="json"),
            "stop_reason": stop_reason,
            "result_subtype": result_subtype,
        })
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "error": str(exc)})
        except Exception:
            pass
    finally:
        db.close()
        try:
            await websocket.close()
        except Exception:
            pass
