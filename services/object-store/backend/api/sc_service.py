"""Archive a Smart City frame: image to the object store, metadata to Kafka.

This is the ITS counterpart of Workflow 1. A VSS Selected Image Schema record
arrives on ``selected-frames`` with the frame inline as base64; the image is
written to the object store and the metadata is republished to ``sc-metadata``
without it, so the image never travels any further and Logstash indexes a
metadata-only document.

The republished record stays FLAT. sc-ingest.conf is what nests it under
metadata.content_metadata; sending the stored shape from here would put the
same knowledge in two places.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import re
from typing import Any

from backend.api.sc_compress import compress_frame
from backend.api.sc_lifecycle import frame_tagging
from backend.api.sc_schemas import SCFrame
from backend.config import settings
from backend.shared.kafka import KafkaService
from backend.shared.storage import StorageService

logger = logging.getLogger(__name__)

_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/bmp": "bmp"}
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(part: str, fallback: str) -> str:
    cleaned = _UNSAFE.sub("_", part.strip())
    return cleaned or fallback


def object_key(data: bytes, frame_id: str, content_type: str) -> str:
    """Content-addressed key, mirroring the AOI formula in service.py.

    Addressing by the digest of the bytes means re-archiving the same frame
    lands on the same key, so a redelivery overwrites rather than accumulating a
    second copy. The prefix is configurable because it has not been settled with
    the ITS team yet.
    """
    ext = _EXTENSIONS.get(content_type, "bin")
    name = _safe(frame_id, "frame")
    digest = hashlib.sha256(data).hexdigest()[:16]
    return f"{settings.its_object_prefix}/{digest}/{name}.{ext}"


def build_metadata(frame: SCFrame, key: str, data: bytes) -> dict[str, Any]:
    """Strip the image out and record where it was written.

    Dumped by alias and without unset fields, so the record stays as flat and as
    small as what arrived. sc-ingest.conf is what nests it under
    metadata.content_metadata; building the stored shape here would put that
    knowledge in two places.
    """
    metadata = frame.model_dump(by_alias=True, exclude_none=True, exclude={"imageData"})

    image = frame.imageData
    metadata["objStorePath"] = key
    metadata["imageType"] = image.content_type if image else "application/octet-stream"
    metadata["imageSize"] = len(data)

    if image is not None:
        if image.metadata.width:
            metadata["imageWidth"] = image.metadata.width
        if image.metadata.height:
            metadata["imageHeight"] = image.metadata.height
    return metadata


async def _store_and_publish(
    storage: StorageService,
    kafka: KafkaService,
    *,
    key: str,
    data: bytes,
    content_type: str,
    metadata: dict[str, Any],
    sensor_id: str,
    tag: str | None,
) -> str:
    """Persist one ITS image and publish its flat catalog metadata.

    Every frame but a KPI-set one is tagged for the bucket lifecycle rule
    (sc_lifecycle).
    """
    url = await storage.upload(key, data, content_type, metadata, tagging=frame_tagging(tag))
    try:
        # Keyed by sensorId so one sensor's frames retain their order.
        await kafka.send(
            metadata,
            topic=settings.sc_metadata_topic,
            key=sensor_id,
        )
    except Exception as kafka_exc:
        try:
            await storage.delete(key)
        except Exception as delete_exc:
            logger.error(
                "orphaned object key=%s: Kafka failed (%s), delete also failed (%s)",
                key,
                kafka_exc,
                delete_exc,
            )
        raise
    return url


async def archive_frame(
    storage: StorageService,
    kafka: KafkaService,
    raw: dict[str, Any],
) -> str | None:
    """Store the frame and publish its metadata. Returns the object key.

    Returns None when the record carries nothing to archive, which is not an
    error: a metadata-only frame is valid on this topic.

    Validation happens here rather than deeper in, so a malformed frame is
    rejected before anything is written to the object store.
    """
    frame = SCFrame.model_validate(raw)

    image = frame.imageData
    frame_id = frame.id
    sensor_id = frame.sensorId

    if image is None or not image.data:
        logger.warning(
            "frame sensorId=%s id=%s carries no imageData — nothing to archive",
            sensor_id, frame_id,
        )
        return None

    try:
        data = base64.b64decode(image.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"imageData is not valid base64: {exc}") from exc
    if not data:
        raise ValueError("imageData decoded to zero bytes")

    content_type = image.content_type
    data = await compress_frame(data, content_type, frame.tag)
    key = object_key(data, frame_id, content_type)
    metadata = build_metadata(frame, key, data)

    await _store_and_publish(
        storage,
        kafka,
        key=key,
        data=data,
        content_type=content_type,
        metadata=metadata,
        sensor_id=sensor_id,
        tag=frame.tag,
    )

    logger.info("archived frame sensorId=%s id=%s -> %s", sensor_id, frame_id, key)
    return key


async def upload_frame(
    storage: StorageService,
    kafka: KafkaService,
    *,
    metadata: SCFrame,
    data: bytes,
    content_type: str,
) -> tuple[str, str, int]:
    """Archive one ITS image supplied through ``POST /api/samples``.

    This is the synchronous API counterpart of :func:`archive_frame`. It uses
    the same object-key, metadata, Kafka topic, and orphan cleanup behavior,
    while keeping S3 and Kafka credentials behind the backend boundary.
    Returns the object key, its URL and the stored size, which differs from
    the uploaded size when the frame was re-encoded.
    """
    data = await compress_frame(data, content_type, metadata.tag)
    key = object_key(data, metadata.id, content_type)
    metadata_dict = metadata.model_dump(
        by_alias=True,
        exclude_none=True,
        exclude={"imageData"},
    )
    metadata_dict["objStorePath"] = key
    metadata_dict["imageType"] = content_type
    metadata_dict["imageSize"] = len(data)
    url = await _store_and_publish(
        storage,
        kafka,
        key=key,
        data=data,
        content_type=content_type,
        metadata=metadata_dict,
        sensor_id=metadata.sensorId,
        tag=metadata.tag,
    )
    logger.info(
        "uploaded ITS sample sensorId=%s id=%s -> %s",
        metadata.sensorId,
        metadata.id,
        key,
    )
    return key, url, len(data)
