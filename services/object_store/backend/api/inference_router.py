from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.inference_schemas import InferenceResultRecord, InferenceResultResponse
from backend.api.inference_service import apply_inference_result
from backend.api.router import get_kafka, get_storage
from backend.shared.kafka import KafkaService
from backend.shared.storage import StorageService

router = APIRouter(prefix="/api", tags=["inference-results"])


@router.post(
    "/inference-results",
    response_model=InferenceResultResponse,
    status_code=status.HTTP_200_OK,
)
async def receive_inference_result(
    body: InferenceResultRecord,
    kafka: KafkaService = Depends(get_kafka),
    storage: StorageService = Depends(get_storage),
) -> InferenceResultResponse:
    """Receive inference result forwarded by AL Archiver (Workflow 2 inbound).

    Republishes the inference-enriched metadata to Kafka so it flows through the
    same Logstash -> Elasticsearch ingest path as the original upload.
    """
    try:
        updated = await apply_inference_result(storage, kafka, body)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"failed to publish inference result: {exc}",
        ) from exc

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"could not publish inference for objStorePath={body.objStorePath} "
                "(missing object metadata)"
            ),
        )

    return InferenceResultResponse(objStorePath=body.objStorePath, updated=True)
