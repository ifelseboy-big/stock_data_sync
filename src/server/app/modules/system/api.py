from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.system.schemas import HealthResponse
from app.modules.system.service import is_database_ready

router = APIRouter()
DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/live", response_model=HealthResponse, response_model_exclude_none=True)
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse)
async def readiness(db: DbSession) -> HealthResponse:
    if not await is_database_ready(db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL is unavailable",
        )
    return HealthResponse(status="ok", database="postgresql")
