"""Per-board pipeline: crop -> batch -> infer -> aggregate -> publish.

Runs in the background after the intake returns 202. Each board is processed on
its own and its record is built only from its own batches. A
per-board deadline guarantees a record is always published, even if the board
hangs.
"""

import asyncio
import logging

from PIL import Image

from client.api.schemas import CropInfoItem, CropResult, InferenceResultRecord
from client.config import settings
from client.core import aggregator, batcher
from client.core.admission import Admission
from client.core.inference_client import InferenceClient
from client.shared.kafka import KafkaPublisher

logger = logging.getLogger(__name__)


def _all_failed_record(
    obj_store_path: str, crop_infos: list[CropInfoItem]
) -> InferenceResultRecord:
    n = len(crop_infos)
    return InferenceResultRecord(
        objStorePath=obj_store_path,
        status="failed" if n else "completed",
        cropCount=n,
        completedCount=0,
        failedCount=n,
        cropResults=[CropResult(cropId=c.id, status="failed") for c in crop_infos],
    )


async def _run(
    obj_store_path: str,
    test_img: Image.Image,
    golden_img: Image.Image,
    crop_infos: list[CropInfoItem],
    inference: InferenceClient,
) -> InferenceResultRecord:
    """Run the full pipeline and always return a record — never raises."""
    batches = await asyncio.to_thread(
        batcher.crop_and_batch, test_img, golden_img, crop_infos, settings.batch_size
    )
    del test_img, golden_img

    if not batches:
        return InferenceResultRecord(
            objStorePath=obj_store_path,
            status="completed",
            cropCount=0,
            completedCount=0,
            failedCount=0,
            cropResults=[],
        )

    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(*(inference.infer_batch(b) for b in batches)),
            timeout=settings.board_deadline_s,
        )
        return aggregator.build_record(obj_store_path, outcomes)
    except asyncio.TimeoutError:
        logger.warning("board %s exceeded deadline", obj_store_path)
    except Exception:
        logger.exception("board %s failed unexpectedly", obj_store_path)
    return _all_failed_record(obj_store_path, crop_infos)


async def process_board(
    obj_store_path: str,
    test_img: Image.Image,
    golden_img: Image.Image,
    crop_infos: list[CropInfoItem],
    inference: InferenceClient,
    kafka: KafkaPublisher,
    admission: Admission,
) -> None:
    try:
        # Build the coroutine before dropping refs so this frame doesn't pin
        # the images through the inference wait.
        coro = _run(obj_store_path, test_img, golden_img, crop_infos, inference)
        del test_img, golden_img
        record = await coro
        await kafka.publish(obj_store_path, record.model_dump(exclude_none=True))
    finally:
        await admission.release()
