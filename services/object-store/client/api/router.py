"""Intake: accept an inference request, admit it, and process in the background.

Validation and admission happen synchronously; the actual crop/infer/publish
work runs in a background task and the verdict returns later on Kafka.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from client.api.schemas import AcceptedResponse, InferenceRequest
from client.core import cropper, pipeline
from client.core.admission import Admission
from client.core.inference_client import InferenceClient
from client.shared.kafka import KafkaPublisher

logger = logging.getLogger(__name__)

router = APIRouter()


def get_kafka(request: Request) -> KafkaPublisher:
    return request.app.state.kafka


def get_inference(request: Request) -> InferenceClient:
    return request.app.state.inference


def get_admission(request: Request) -> Admission:
    return request.app.state.admission


@router.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", tags=["health"])
async def ready(request: Request) -> dict:
    """Readiness: probe AOI #2 inference and Kafka."""
    inference: InferenceClient = request.app.state.inference
    kafka: KafkaPublisher = request.app.state.kafka
    inference_ok = await inference.ready()
    kafka_ok = kafka.is_ready
    body = {"status": "ready", "inference": inference_ok, "kafka": kafka_ok}
    if not (inference_ok and kafka_ok):
        body["status"] = "not ready"
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=body)
    return body


@router.post(
    "/api/inference",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedResponse,
    tags=["inference"],
)
async def infer(
    request: Request,
    body: InferenceRequest,
    inference: InferenceClient = Depends(get_inference),
    kafka: KafkaPublisher = Depends(get_kafka),
    admission: Admission = Depends(get_admission),
) -> AcceptedResponse:
    obj_store_path = body.metadata.objStorePath
    crop_infos = body.metadata.imageInfo.cropInfo

    # Admission: shed load early with 503.
    if not await admission.try_admit():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AOI Client at capacity; retry later",
        )

    try:
        loop = asyncio.get_running_loop()
        try:
            test_img = await loop.run_in_executor(
                None, cropper.decode_board, body.sampleImage
            )
            golden_img = await loop.run_in_executor(
                None, cropper.decode_board, body.goldenRefImage
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid image data: {exc}",
            )

        bound_errors = cropper.validate_bounds(test_img, golden_img, crop_infos)
        if bound_errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="; ".join(bound_errors),
            )
    except HTTPException:
        await admission.release()
        raise

    # Hand the decoded boards to the background pipeline (decode happens once).
    task = asyncio.create_task(
        pipeline.process_board(
            obj_store_path, test_img, golden_img, crop_infos, inference, kafka, admission
        )
    )
    request.app.state.tasks.add(task)
    task.add_done_callback(request.app.state.tasks.discard)

    return AcceptedResponse(objStorePath=obj_store_path, cropCount=len(crop_infos))
