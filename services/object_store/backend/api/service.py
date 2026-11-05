import base64
import hashlib
import logging

from backend.api.aoi_trigger import build_inference_request
from backend.api.metadata import AOIMetadata
from backend.api.schemas import FailedImage, ImageItem, UploadedImage
from backend.api.sc_schemas import SCFrame
from backend.api.sc_service import upload_frame
from backend.config import settings
from backend.shared.aoi_client import AoiClientService
from backend.shared.kafka import KafkaService
from backend.shared.storage import StorageService

logger = logging.getLogger(__name__)


async def upload_image(
    storage: StorageService,
    kafka: KafkaService,
    image: ImageItem,
    aoi_client: AoiClientService | None = None,
) -> UploadedImage | FailedImage:
    try:
        data = base64.b64decode(image.data)
        key = f"deft-aoi/{hashlib.sha256(data).hexdigest()[:16]}/{image.filename}"
        md = image.metadata.model_dump()
        md["objStorePath"] = key
        md.setdefault("imageInfo", {})["imageSize"] = len(data)
        aoi_metadata = AOIMetadata.model_validate(md)
        metadata_dict = aoi_metadata.model_dump(by_alias=True, exclude_none=True)

        url = await storage.upload(key, data, image.content_type, metadata_dict)

        try:
            await kafka.send(metadata_dict)
        except Exception as kafka_exc:
            try:
                await storage.delete(key)
            except Exception as delete_exc:
                logger.error(
                    "Orphaned object key=%s: Kafka failed (%s), delete also failed (%s)",
                    key, kafka_exc, delete_exc,
                )
            raise kafka_exc

        if (
            settings.inference_enabled
            and aoi_client is not None
            and aoi_metadata.imageInfo.cropInfo
        ):
            try:
                payload = await build_inference_request(data, aoi_metadata, storage)
                aoi_client.schedule_trigger(payload, key)
            except Exception as exc:
                logger.error(
                    "Failed to schedule AOI Client inference for %s: %s",
                    key,
                    exc,
                )

        return UploadedImage(filename=image.filename, storage_key=key, url=url, size_bytes=len(data))
    except Exception as exc:
        return FailedImage(filename=image.filename, error=str(exc))


async def upload_its_image(
    storage: StorageService,
    kafka: KafkaService,
    image: ImageItem,
) -> UploadedImage | FailedImage:
    try:
        if not isinstance(image.metadata, SCFrame):
            raise ValueError("ITS upload requires Smart City frame metadata")
        data = base64.b64decode(image.data)
        key, url, stored_size = await upload_frame(
            storage,
            kafka,
            metadata=image.metadata,
            data=data,
            content_type=image.content_type,
        )
        return UploadedImage(
            filename=image.filename,
            storage_key=key,
            url=url,
            size_bytes=stored_size,
        )
    except Exception as exc:
        return FailedImage(filename=image.filename, error=str(exc))
