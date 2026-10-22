from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import Principal, get_principal
from app.config import BACKEND_TYPE, SERVICE_VERSION, STORAGE_ROOT
from app.schemas.service_models import HealthResponse, InfoResponse

router = APIRouter(tags=["service"])


@router.get("/v1/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    return HealthResponse()


@router.get("/v1/info", response_model=InfoResponse)
async def get_info(_principal: Principal = Depends(get_principal)) -> InfoResponse:
    return InfoResponse(version=SERVICE_VERSION, backend=BACKEND_TYPE, storage_root=STORAGE_ROOT)
