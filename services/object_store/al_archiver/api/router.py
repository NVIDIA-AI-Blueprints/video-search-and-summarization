from fastapi import APIRouter, HTTPException, Request, status

from al_archiver.core.consumer import ArchiverConsumer

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict:
    consumer: ArchiverConsumer = request.app.state.consumer
    if consumer.is_ready:
        return {"status": "ready"}
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"status": "not ready"},
    )
