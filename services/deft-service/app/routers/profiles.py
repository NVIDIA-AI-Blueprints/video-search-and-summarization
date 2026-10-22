from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import Principal, get_principal
from app.data.profiles import PROFILES
from app.errors import profile_not_found
from app.schemas.profile_models import ProfileInfo

router = APIRouter(tags=["profiles"], dependencies=[Depends(get_principal)])


@router.get("/v1/profiles", response_model=list[ProfileInfo])
async def list_profiles() -> list[ProfileInfo]:
    return list(PROFILES.values())


@router.get("/v1/profiles/{name}", response_model=ProfileInfo)
async def get_profile(name: str) -> ProfileInfo:
    profile = PROFILES.get(name)
    if profile is None:
        raise profile_not_found(name)
    return profile
