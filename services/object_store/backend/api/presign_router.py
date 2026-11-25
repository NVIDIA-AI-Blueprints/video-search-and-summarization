from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.api.presign_schemas import (
    PresignPostRequest,
    PresignPostResponse,
    PresignRequest,
    PresignResponse,
)
from backend.api.presign_service import (
    PresignConflictError,
    PresignError,
    create_presigned_post,
    create_presigned_urls,
)
from backend.shared.storage import StorageService

router = APIRouter(prefix="/v1", tags=["presign"])


def get_storage(request: Request) -> StorageService:
    return request.app.state.storage


@router.post("/get_presigned_urls", response_model=PresignResponse)
async def get_presigned_urls(
    body: PresignRequest,
    storage: StorageService = Depends(get_storage),
) -> PresignResponse:
    """Return short-lived presigned GET URLs for object-store keys or URLs.

    Callers pass objStorePath values (or full ``{endpoint}/{bucket}/{key}`` URLs);
    the response contains SigV4-signed URLs suitable for browser or tool download
    without sharing SeaweedFS credentials.
    """
    try:
        return await create_presigned_urls(storage, body)
    except PresignError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/get_presigned_post_url", response_model=PresignPostResponse)
async def get_presigned_post_url(
    body: PresignPostRequest,
    storage: StorageService = Depends(get_storage),
) -> PresignPostResponse:
    """Return a presigned POST so the client can upload one file directly to
    ``<folder>/<file_name>`` (size-capped; rejected if it already exists).
    """
    try:
        return await create_presigned_post(storage, body)
    except PresignConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
