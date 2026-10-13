from datetime import datetime

from fastapi import APIRouter

from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
@router.get("/api/health", response_model=HealthResponse, include_in_schema=False)  # reachable via the /api proxy
def health() -> HealthResponse:
    return HealthResponse(status="ok", timestamp=datetime.utcnow())
