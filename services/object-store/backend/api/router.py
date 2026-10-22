import asyncio

from fastapi import APIRouter, Depends, Request

from backend.api.schemas import FailedImage, SampleBatchRequest, SampleBatchResponse, UploadedImage
from backend.api.sc_schemas import SCFrame
from backend.api.service import upload_image, upload_its_image
from backend.shared.aoi_client import AoiClientService
from backend.shared.kafka import KafkaService
from backend.shared.storage import StorageService

router = APIRouter(prefix="/api", tags=["samples"])


def get_storage(request: Request) -> StorageService:
    return request.app.state.storage


def get_kafka(request: Request) -> KafkaService:
    return request.app.state.kafka


def get_aoi_client(request: Request) -> AoiClientService:
    return request.app.state.aoi_client


@router.post("/samples", response_model=SampleBatchResponse, status_code=207)
async def upload_samples(
    body: SampleBatchRequest,
    storage: StorageService = Depends(get_storage),
    kafka: KafkaService = Depends(get_kafka),
    aoi_client: AoiClientService = Depends(get_aoi_client),
) -> SampleBatchResponse:
    calls = [
        upload_its_image(storage, kafka, image)
        if isinstance(image.metadata, SCFrame)
        else upload_image(storage, kafka, image, aoi_client)
        for image in body.images
    ]
    results = await asyncio.gather(*calls)
    return SampleBatchResponse(
        uploaded=[r for r in results if isinstance(r, UploadedImage)],
        failed=[r for r in results if isinstance(r, FailedImage)],
    )
