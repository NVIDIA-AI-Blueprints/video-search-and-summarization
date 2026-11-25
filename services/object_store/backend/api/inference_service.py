"""Handle inbound inference results from AL Archiver and upsert ES."""

import hashlib
import logging
from datetime import datetime, timezone

from pydantic import ValidationError

from backend.api.inference_schemas import CropResult, InferenceResultRecord, ModelResult
from backend.api.metadata import (
    AOIMetadata,
    CropInference,
    InferenceSummary,
    ModelInference,
)
from backend.shared.kafka import KafkaService
from backend.shared.storage import StorageService

logger = logging.getLogger(__name__)


def _model_inference(model: ModelResult) -> ModelInference:
    return ModelInference(
        model_name=model.model_name,
        version=model.version,
        score=model.score,
        verdict=model.verdict,
    )


def _board_uuid(obj_store_path: str) -> str:
    return hashlib.sha256(obj_store_path.encode()).hexdigest()[:32]


def _crop_inference(crop: CropResult, obj_store_path: str) -> CropInference:
    return CropInference(
        inspectionId=f"{_board_uuid(obj_store_path)}_{crop.cropId}",
        status=crop.status,
        score=crop.score,
        verdict=crop.verdict,
        threshold=crop.threshold,
        old_model=_model_inference(crop.old_model) if crop.old_model else None,
        new_model=_model_inference(crop.new_model) if crop.new_model else None,
    )


def _inference_summary(record: InferenceResultRecord) -> InferenceSummary:
    return InferenceSummary(
        status=record.status,
        cropCount=record.cropCount,
        completedCount=record.completedCount,
        failedCount=record.failedCount,
        updatedAt=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        deployment_type=record.deployment_type,
        model_name=record.model_name,
    )


def merge_inference_into_metadata(
    metadata: AOIMetadata,
    record: InferenceResultRecord,
) -> AOIMetadata:
    """Attach board-level and per-crop inference onto a validated AOIMetadata."""
    summary = _inference_summary(record)
    crop_inferences = {
        crop.cropId: _crop_inference(crop, record.objStorePath)
        for crop in record.cropResults
    }

    image_info = metadata.imageInfo
    crop_info = image_info.cropInfo
    if crop_info and crop_inferences:
        updated_crops = []
        for item in crop_info:
            if item.id in crop_inferences:
                updated_crops.append(
                    item.model_copy(update={"inference": crop_inferences[item.id]})
                )
            else:
                updated_crops.append(item)
        unmatched = set(crop_inferences) - {c.id for c in crop_info}
        if unmatched:
            logger.warning(
                "cropIds %s not found in cropInfo for %s",
                sorted(unmatched),
                metadata.objStorePath,
            )
        image_info = image_info.model_copy(update={"cropInfo": updated_crops})
    elif crop_inferences and not crop_info:
        logger.warning(
            "No cropInfo on metadata for %s; writing inferenceSummary only",
            metadata.objStorePath,
        )

    return metadata.model_copy(
        update={"inferenceSummary": summary, "imageInfo": image_info}
    )


def _metadata_from_storage(raw: dict) -> AOIMetadata:
    """Rehydrate AOIMetadata from S3 x-image-metadata.

    Upload persists metadata with ``exclude_none=True``, so optional fields like
    ``cropInfo[].referenceLabels`` are omitted when null. Restore them before
    strict validation.
    """
    md = dict(raw)
    image_info = dict(md.get("imageInfo") or {})
    crop_info = image_info.get("cropInfo")
    if crop_info:
        image_info["cropInfo"] = [
            {**crop, "referenceLabels": crop.get("referenceLabels")}
            if "referenceLabels" not in crop
            else crop
            for crop in crop_info
        ]
        md["imageInfo"] = image_info
    return AOIMetadata.model_validate(md)


async def apply_inference_result(
    storage: StorageService,
    kafka: KafkaService,
    record: InferenceResultRecord,
) -> bool:
    try:
        raw = await storage.get_metadata(record.objStorePath)
    except Exception as exc:
        logger.warning(
            "No object metadata for objStorePath=%s: %s",
            record.objStorePath,
            exc,
        )
        return False

    try:
        metadata = _metadata_from_storage(raw)
    except ValidationError as exc:
        logger.warning(
            "Invalid stored metadata for objStorePath=%s: %s",
            record.objStorePath,
            exc,
        )
        return False

    metadata = merge_inference_into_metadata(metadata, record)
    # Re-ingest the inference-enriched metadata through the same Kafka -> Logstash
    # -> ES path as upload. Logstash re-indexes the deterministic document id, so
    # this upserts inferenceSummary + cropInfo[].inference onto the existing doc.
    await kafka.send(metadata.model_dump(by_alias=True, exclude_none=True))
    logger.info("Published inference result for %s", record.objStorePath)
    return True
