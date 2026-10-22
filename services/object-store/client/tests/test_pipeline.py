from unittest.mock import AsyncMock, MagicMock

from client.api.schemas import CropInfoItem
from client.core import batcher, cropper, pipeline
from client.core.inference_client import BatchOutcome, CropScore, ModelScore
from tests.helpers import BOARD_B64


def _infos(n):
    return [
        CropInfoItem.model_validate(
            {"id": f"U{i}", "crop": {"coordinates": {"left": 0, "top": 0, "width": 32, "height": 32}}}
        )
        for i in range(n)
    ]


def _imgs():
    return cropper.decode_board(BOARD_B64), cropper.decode_board(BOARD_B64)


def _deps():
    kafka = MagicMock()
    kafka.publish = AsyncMock()
    admission = MagicMock()
    admission.release = AsyncMock()
    return kafka, admission


def _score() -> CropScore:
    return CropScore(
        old_model=ModelScore(version="1", score=0.9, verdict="DEFECT"),
        new_model=ModelScore(version="2", score=0.8, verdict="DEFECT"),
        score=0.9,
        verdict="DEFECT",
        threshold=0.33,
    )


async def test_completed_board_publishes_record():
    infos = _infos(2)

    async def fake_infer(batch):
        return BatchOutcome(batch, [_score() for _ in batch.crop_ids], deployment_type="Shadow")

    inference = MagicMock()
    inference.infer_batch = AsyncMock(side_effect=fake_infer)
    kafka, admission = _deps()
    test_img, golden_img = _imgs()

    await pipeline.process_board(
        "obj", test_img, golden_img, infos, inference, kafka, admission
    )

    kafka.publish.assert_awaited_once()
    key, payload = kafka.publish.await_args.args
    assert key == "obj"
    assert payload["status"] == "completed"
    assert payload["cropCount"] == 2 and payload["completedCount"] == 2
    assert payload["deployment_type"] == "Shadow"
    admission.release.assert_awaited_once()


async def test_empty_board_publishes_completed_zero():
    inference = MagicMock()
    inference.infer_batch = AsyncMock()
    kafka, admission = _deps()
    test_img, golden_img = _imgs()

    await pipeline.process_board("obj", test_img, golden_img, [], inference, kafka, admission)

    inference.infer_batch.assert_not_awaited()
    _key, payload = kafka.publish.await_args.args
    assert payload["status"] == "completed" and payload["cropCount"] == 0


async def test_failed_batch_yields_partial_or_failed():
    infos = _infos(1)
    inference = MagicMock()
    inference.infer_batch = AsyncMock(
        side_effect=lambda batch: BatchOutcome(batch, None, error="boom")
    )
    kafka, admission = _deps()
    test_img, golden_img = _imgs()

    await pipeline.process_board("obj", test_img, golden_img, infos, inference, kafka, admission)

    _key, payload = kafka.publish.await_args.args
    assert payload["status"] == "failed"
    assert payload["failedCount"] == 1
    assert payload["cropResults"][0]["status"] == "failed"


async def test_full_boards_freed_before_inference():
    """The decoded boards must be collectable once cropping is done, so a board
    waiting on permits does not pin two large images through the inference wait."""
    import gc
    import weakref

    infos = _infos(1)
    holder = list(_imgs())
    refs = [weakref.ref(holder[0]), weakref.ref(holder[1])]

    alive_during_infer = {}

    async def check_infer(batch):
        gc.collect()
        alive_during_infer["count"] = sum(r() is not None for r in refs)
        return BatchOutcome(batch, [_score()])

    inference = MagicMock()
    inference.infer_batch = AsyncMock(side_effect=check_infer)
    kafka, admission = _deps()

    await pipeline.process_board(
        "obj", holder.pop(0), holder.pop(0), infos, inference, kafka, admission
    )

    assert alive_during_infer["count"] == 0, "decoded boards still alive during inference"


async def test_deadline_publishes_failed_record(monkeypatch):
    from client.config import settings

    monkeypatch.setattr(settings, "board_deadline_s", 0.05)
    infos = _infos(1)

    async def slow_infer(batch):
        import asyncio
        await asyncio.sleep(1.0)
        return BatchOutcome(batch, [_score()])

    inference = MagicMock()
    inference.infer_batch = AsyncMock(side_effect=slow_infer)
    kafka, admission = _deps()
    test_img, golden_img = _imgs()

    await pipeline.process_board("obj", test_img, golden_img, infos, inference, kafka, admission)

    _key, payload = kafka.publish.await_args.args
    assert payload["status"] == "failed"
    admission.release.assert_awaited_once()


async def test_unexpected_exception_publishes_failed_record():
    infos = _infos(1)
    inference = MagicMock()
    inference.infer_batch = AsyncMock(side_effect=RuntimeError("kaboom"))
    kafka, admission = _deps()
    test_img, golden_img = _imgs()

    await pipeline.process_board("obj", test_img, golden_img, infos, inference, kafka, admission)

    _key, payload = kafka.publish.await_args.args
    assert payload["status"] == "failed"
    admission.release.assert_awaited_once()


def test_crop_and_batch_produces_correct_batches():
    test_img, golden_img = _imgs()
    batches = batcher.crop_and_batch(test_img, golden_img, _infos(3), batch_size=2)
    assert len(batches) == 2
    assert len(batches[0].crop_ids) == 2
    assert len(batches[1].crop_ids) == 1
    assert batches[0].batch_number == 0
    assert batches[1].batch_number == 1
