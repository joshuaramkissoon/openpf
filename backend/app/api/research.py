from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import Thesis
from app.schemas.research import ResearchRunRequest, ResearchRunResponse
from app.services.claude_agent_runtime import run_research_request

router = APIRouter(prefix="/research", tags=["research"])


@router.post("/run", response_model=ResearchRunResponse)
def run(payload: ResearchRunRequest, db: Session = Depends(get_db)) -> ResearchRunResponse:
    """Run an agent-driven analysis request and return a structured verdict.

    Synchronous: Archie orchestrates the researcher + quant subagents over the
    market-data, Kronos forecast, and read-only T212 tools. Runs can take a
    minute or two; the agent also writes a persistent artifact to disk.
    """
    result = run_research_request(
        objective=payload.objective,
        subject=payload.subject,
        hypothesis=payload.hypothesis,
        horizon_days=payload.horizon_days,
        portfolio_context={"account_kind": payload.account_kind},
    )
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "Research run failed.")

    thesis_id: str | None = None
    verdict = (result.get("verdict") or "").lower()
    if payload.create_thesis and payload.subject.strip() and verdict in ("support", "mixed"):
        row = Thesis(
            symbol=payload.subject.upper().strip()[:32],
            account_kind=payload.account_kind or "all",
            title=(result.get("summary") or payload.objective)[:240],
            thesis=result.get("summary") or result.get("suggested_action") or "",
            invalidation=result.get("invalidation") or "",
            confidence=float(result.get("confidence") or 0.0),
            status="active",
            meta={
                "source": "research_desk",
                "objective": payload.objective,
                "verdict": verdict,
                "artifact_path": result.get("artifact_path"),
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        thesis_id = row.id

    return ResearchRunResponse(
        ok=True,
        markdown=result.get("markdown") or "",
        verdict=result.get("verdict"),
        confidence=result.get("confidence"),
        summary=result.get("summary"),
        suggested_action=result.get("suggested_action"),
        invalidation=result.get("invalidation"),
        artifact_path=result.get("artifact_path"),
        thesis_id=thesis_id,
    )
