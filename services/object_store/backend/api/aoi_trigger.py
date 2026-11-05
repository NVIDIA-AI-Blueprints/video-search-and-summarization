"""Map AOIMetadata + image bytes to AOI Client POST /api/inference payload."""

import base64
import logging

from backend.api.metadata import AOIMetadata
from backend.config import settings
from backend.shared.storage import StorageService

logger = logging.getLogger(__name__)


def _crop_info_for_client(metadata: AOIMetadata) -> list[dict]:
    items = []
    for crop_item in metadata.imageInfo.cropInfo or []:
        coords = crop_item.crop.coordinates
        items.append(
            {
                "id": crop_item.id,
                "crop": {
                    "coordinates": {
                        "left": coords.left,
                        "top": coords.top,
                        "width": coords.width,
                        "height": coords.height,
                    }
                },
            }
        )
    return items


async def resolve_golden_ref_b64(
    sample_b64: str, metadata: AOIMetadata, storage: StorageService
) -> str:
    golden_ref = metadata.imageInfo.goldenRef
    if settings.dev_use_sample_as_golden_ref or not golden_ref:
        if golden_ref:
            logger.warning(
                "golden-ref path %r not fetched; using sample as dev fallback for %s",
                golden_ref,
                metadata.objStorePath,
            )
        return sample_b64
    try:
        golden_bytes = await storage.get_bytes(golden_ref)
    except Exception as exc:
        raise ValueError(
            f"golden reference unavailable for {metadata.objStorePath}: {exc}"
        ) from exc
    return base64.b64encode(golden_bytes).decode("ascii")


async def build_inference_request(
    image_bytes: bytes,
    metadata: AOIMetadata,
    storage: StorageService,
) -> dict:
    sample_b64 = base64.b64encode(image_bytes).decode("ascii")
    golden_b64 = await resolve_golden_ref_b64(sample_b64, metadata, storage)
    return {
        "sampleImage": sample_b64,
        "goldenRefImage": golden_b64,
        "metadata": {
            "objStorePath": metadata.objStorePath,
            "imageInfo": {"cropInfo": _crop_info_for_client(metadata)},
        },
    }
