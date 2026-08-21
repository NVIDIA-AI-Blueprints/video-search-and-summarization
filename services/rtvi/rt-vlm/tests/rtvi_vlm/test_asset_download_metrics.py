# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for remote asset download metrics."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.asset_manager import AssetManager, _AssetDownloadTracker


def _manager_with_metrics():
    manager = object.__new__(AssetManager)
    manager._download_metrics = MagicMock()
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "source"),
    [
        ("http://example.com/video.mp4", "http"),
        ("https://example.com/video.mp4", "https"),
    ],
)
async def test_http_download_records_success(url, source):
    manager = _manager_with_metrics()
    manager._download_file = AsyncMock(return_value="asset-id")

    result = await manager.download_file(url, "video.mp4", "vision", "video", None, "id")

    assert result == "asset-id"
    manager._download_file.assert_awaited_once()
    assert manager._download_file.await_args.args[7:] == (None, "", None)
    attributes = {"source": source, "status": "success"}
    manager._download_metrics.duration.record.assert_called_once()
    assert manager._download_metrics.duration.record.call_args.args[1] == attributes
    manager._download_metrics.bytes.add.assert_called_once_with(0, attributes)
    manager._download_metrics.requests.add.assert_called_once_with(1, attributes)


@pytest.mark.asyncio
async def test_http_download_forwards_sensor_fields():
    manager = _manager_with_metrics()
    manager._download_file = AsyncMock(return_value="asset-id")

    result = await manager.download_file(
        "https://example.com/video.mp4",
        "video.mp4",
        "vision",
        "video",
        "2026-07-09T14:58:40Z",
        None,
        sensor_name="camera-1",
        camera_id="camera-1",
    )

    assert result == "asset-id"
    assert manager._download_file.await_args.args[7:] == (None, "camera-1", "camera-1")


@pytest.mark.asyncio
async def test_s3_download_records_failure():
    manager = _manager_with_metrics()
    manager._download_file_from_s3 = AsyncMock(side_effect=RuntimeError("download failed"))

    with pytest.raises(RuntimeError, match="download failed"):
        await manager.download_file_from_s3(
            "s3://bucket/video.mp4", "video.mp4", "vision", "video", None, "id"
        )

    attributes = {"source": "s3", "status": "failure"}
    manager._download_metrics.duration.record.assert_called_once()
    assert manager._download_metrics.duration.record.call_args.args[1] == attributes
    manager._download_metrics.bytes.add.assert_called_once_with(0, attributes)
    manager._download_metrics.requests.add.assert_called_once_with(1, attributes)


@pytest.mark.asyncio
async def test_s3_download_forwards_sensor_fields():
    manager = _manager_with_metrics()
    manager._download_file_from_s3 = AsyncMock(return_value="asset-id")

    result = await manager.download_file_from_s3(
        "s3://bucket/video.mp4",
        "video.mp4",
        "vision",
        "video",
        "2026-07-09T14:58:40Z",
        None,
        sensor_name="camera-1",
        camera_id="camera-1",
    )

    assert result == "asset-id"
    assert manager._download_file_from_s3.await_args.args[7:] == ("camera-1", "camera-1")


@pytest.mark.asyncio
async def test_s3_download_records_failure_when_save_file_fails(monkeypatch):
    class FakeS3Body:
        def __init__(self):
            self._chunks = [b"downloaded-bytes", b""]

        def read(self, _chunk_size):
            return self._chunks.pop(0)

    class FakeS3Client:
        class exceptions:
            class NoSuchBucket(Exception):
                pass

            class NoSuchKey(Exception):
                pass

        def get_object(self, Bucket, Key):
            return {"Body": FakeS3Body()}

    manager = _manager_with_metrics()
    manager._get_bucket_and_object_key_from_url = MagicMock(return_value=("bucket", "key"))
    manager.save_file = AsyncMock(side_effect=RuntimeError("save failed"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret-key")
    monkeypatch.setattr("utils.asset_manager.boto3.client", MagicMock(return_value=FakeS3Client()))

    with pytest.raises(RuntimeError, match="save failed"):
        await manager.download_file_from_s3(
            "s3://bucket/video.mp4", "video.mp4", "vision", "video", None, "id"
        )

    attributes = {"source": "s3", "status": "failure"}
    manager._download_metrics.duration.record.assert_called_once()
    assert manager._download_metrics.duration.record.call_args.args[1] == attributes
    manager._download_metrics.bytes.add.assert_called_once_with(
        len(b"downloaded-bytes"), attributes
    )
    manager._download_metrics.requests.add.assert_called_once_with(1, attributes)


def test_tracker_records_partial_bytes_and_finishes_once():
    instruments = MagicMock()
    with patch("utils.asset_manager.time.perf_counter", side_effect=[10.0, 12.5]):
        tracker = _AssetDownloadTracker(instruments, "https")
        tracker.add_bytes(100)
        tracker.add_bytes(23)
        tracker.finish("failure")
        tracker.finish("success")

    attributes = {"source": "https", "status": "failure"}
    instruments.duration.record.assert_called_once_with(2.5, attributes)
    instruments.bytes.add.assert_called_once_with(123, attributes)
    instruments.requests.add.assert_called_once_with(1, attributes)
