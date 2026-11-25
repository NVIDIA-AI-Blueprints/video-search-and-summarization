from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.api.eval_schemas import EvalRequest, EvalResponse, EvalStatusResponse
from backend.api.eval_service import (
    EvalFanoutError,
    create_eval,
    get_eval_status,
)
from backend.shared.elasticsearch import ElasticsearchService
from backend.shared.kafka import KafkaService
from backend.shared.storage import StorageService

router = APIRouter(prefix="/v1", tags=["eval"])


def get_kafka(request: Request) -> KafkaService:
    return request.app.state.kafka


def get_es(request: Request) -> ElasticsearchService:
    return request.app.state.elasticsearch


def get_storage(request: Request) -> StorageService:
    return request.app.state.storage


@router.post("/results", response_model=EvalResponse, status_code=status.HTTP_201_CREATED)
async def create_eval_endpoint(
    body: EvalRequest,
    es: ElasticsearchService = Depends(get_es),
    kafka: KafkaService = Depends(get_kafka),
    storage: StorageService = Depends(get_storage),
) -> EvalResponse:
    # Returns promptly once slices are fanned out; the export completes
    # asynchronously in the consumer groups. Progress is observed via
    # GET /v1/eval/<eval_id>.
    try:
        eval_id = await create_eval(es, kafka, storage, body)
    except EvalFanoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        )
    return EvalResponse(eval_id=eval_id)


@router.get("/eval/{eval_id}", response_model=EvalStatusResponse)
async def get_eval_endpoint(
    eval_id: str,
    storage: StorageService = Depends(get_storage),
) -> EvalStatusResponse:
    result = await get_eval_status(storage, eval_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown eval_id {eval_id}"
        )
    return EvalStatusResponse(**result)
