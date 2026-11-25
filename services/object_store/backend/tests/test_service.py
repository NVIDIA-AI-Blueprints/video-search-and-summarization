import base64

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.api.aoi_trigger import build_inference_request, resolve_golden_ref_b64
from backend.api.metadata import AOIMetadata
from backend.api.schemas import FailedImage, ImageItem, ImageMetadata, UploadedImage
from backend.api.service import upload_image
from backend.config import settings
from tests.conftest import VALID_B64, VALID_METADATA, VALID_METADATA_WITH_CROPS

UPLOAD_URL = "http://localhost:8333/test-bucket/samples/uuid/photo.jpg"


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    storage.upload = AsyncMock(return_value=UPLOAD_URL)
    storage.delete = AsyncMock()
    return storage


@pytest.fixture
def mock_kafka():
    kafka = MagicMock()
    kafka.send = AsyncMock()
    return kafka


@pytest.fixture
def mock_aoi_client():
    client = MagicMock()
    client.schedule_trigger = MagicMock()
    return client


def make_image(metadata: dict | None = None, **kwargs) -> ImageItem:
    return ImageItem(
        filename="photo.jpg",
        content_type="image/jpeg",
        data=VALID_B64,
        metadata=ImageMetadata.model_validate(metadata if metadata is not None else VALID_METADATA),
        **kwargs,
    )


def make_metadata(**overrides) -> AOIMetadata:
    base = {**VALID_METADATA_WITH_CROPS, "objStorePath": "deft-aoi/abc/photo.jpg"}
    base.update(overrides)
    return AOIMetadata.model_validate(base)


class TestBuildInferenceRequest:
    @pytest.mark.asyncio
    async def test_maps_crop_info(self, mock_storage):
        metadata = make_metadata()
        payload = await build_inference_request(b"img-bytes", metadata, mock_storage)
        assert payload["sampleImage"] == base64.b64encode(b"img-bytes").decode()
        assert payload["goldenRefImage"] == payload["sampleImage"]
        assert payload["metadata"]["objStorePath"] == metadata.objStorePath
        crops = payload["metadata"]["imageInfo"]["cropInfo"]
        assert len(crops) == 1
        assert crops[0]["id"] == "U7"
        assert crops[0]["crop"]["coordinates"]["width"] == 64

    @pytest.mark.asyncio
    async def test_golden_ref_dev_fallback(self, mock_storage):
        metadata = make_metadata()
        sample_b64 = base64.b64encode(b"x").decode()
        assert await resolve_golden_ref_b64(sample_b64, metadata, mock_storage) == sample_b64

    @pytest.mark.asyncio
    async def test_golden_ref_fetched_from_storage(self, mock_storage):
        golden_bytes = b"golden-image-bytes"
        mock_storage.get_bytes = AsyncMock(return_value=golden_bytes)
        metadata = make_metadata(**{"imageInfo": {**VALID_METADATA_WITH_CROPS["imageInfo"], "golden-ref": "deft-aoi/golden/ref.jpg"}})
        sample_b64 = base64.b64encode(b"sample").decode()
        with patch.object(settings, "dev_use_sample_as_golden_ref", False):
            result = await resolve_golden_ref_b64(sample_b64, metadata, mock_storage)
        mock_storage.get_bytes.assert_awaited_once_with("deft-aoi/golden/ref.jpg")
        assert result == base64.b64encode(golden_bytes).decode()

    @pytest.mark.asyncio
    async def test_golden_ref_storage_error_raises(self, mock_storage):
        mock_storage.get_bytes = AsyncMock(side_effect=Exception("not found"))
        metadata = make_metadata(**{"imageInfo": {**VALID_METADATA_WITH_CROPS["imageInfo"], "golden-ref": "deft-aoi/golden/ref.jpg"}})
        sample_b64 = base64.b64encode(b"sample").decode()
        with patch.object(settings, "dev_use_sample_as_golden_ref", False):
            with pytest.raises(ValueError, match="golden reference unavailable"):
                await resolve_golden_ref_b64(sample_b64, metadata, mock_storage)


@pytest.mark.asyncio
async def test_upload_success(mock_storage, mock_kafka):
    result = await upload_image(mock_storage, mock_kafka, make_image())
    assert isinstance(result, UploadedImage)
    assert result.filename == "photo.jpg"
    assert result.url == UPLOAD_URL
    assert result.size_bytes == len(base64.b64decode(VALID_B64))


@pytest.mark.asyncio
async def test_aoi_images_are_stored_without_tags(mock_storage, mock_kafka):
    """Only ITS frames carry the lifecycle tag; the AOI write is unchanged."""
    await upload_image(mock_storage, mock_kafka, make_image())
    call = mock_storage.upload.await_args
    assert len(call.args) == 4
    assert "tagging" not in call.kwargs


@pytest.mark.asyncio
async def test_storage_key_is_deterministic(mock_storage, mock_kafka):
    image = make_image()
    result1 = await upload_image(mock_storage, mock_kafka, image)
    result2 = await upload_image(mock_storage, mock_kafka, image)
    assert isinstance(result1, UploadedImage)
    assert isinstance(result2, UploadedImage)
    assert result1.storage_key == result2.storage_key


@pytest.mark.asyncio
async def test_storage_key_uses_sha256_prefix(mock_storage, mock_kafka):
    result = await upload_image(mock_storage, mock_kafka, make_image())
    assert isinstance(result, UploadedImage)
    assert result.storage_key.startswith("deft-aoi/")
    assert result.storage_key.endswith("/photo.jpg")
    segment = result.storage_key.split("/")[1]
    assert len(segment) == 16
    assert all(c in "0123456789abcdef" for c in segment)


@pytest.mark.asyncio
async def test_obj_store_path_is_set_to_storage_key(mock_storage, mock_kafka):
    result = await upload_image(mock_storage, mock_kafka, make_image())
    assert isinstance(result, UploadedImage)
    args, _ = mock_storage.upload.call_args
    assert args[3]["objStorePath"] == result.storage_key


@pytest.mark.asyncio
async def test_image_size_is_set_to_decoded_byte_length(mock_storage, mock_kafka):
    result = await upload_image(mock_storage, mock_kafka, make_image())
    assert isinstance(result, UploadedImage)
    args, _ = mock_storage.upload.call_args
    expected = len(base64.b64decode(VALID_B64))
    assert args[3]["imageInfo"]["imageSize"] == expected
    assert result.size_bytes == expected


@pytest.mark.asyncio
async def test_kafka_receives_metadata_dict(mock_storage, mock_kafka):
    result = await upload_image(mock_storage, mock_kafka, make_image())
    assert isinstance(result, UploadedImage)
    mock_kafka.send.assert_awaited_once()
    sent_payload = mock_kafka.send.call_args.args[0]
    assert isinstance(sent_payload, dict)
    assert sent_payload["objStorePath"] == result.storage_key


@pytest.mark.asyncio
async def test_kafka_failure_triggers_compensating_delete(mock_storage, mock_kafka):
    mock_kafka.send = AsyncMock(side_effect=Exception("broker unavailable"))
    result = await upload_image(mock_storage, mock_kafka, make_image())
    assert isinstance(result, FailedImage)
    assert "broker unavailable" in result.error
    mock_storage.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_kafka_failure_delete_key_matches_upload_key(mock_storage, mock_kafka):
    mock_kafka.send = AsyncMock(side_effect=Exception("broker unavailable"))
    await upload_image(mock_storage, mock_kafka, make_image())
    upload_key = mock_storage.upload.call_args.args[0]
    delete_key = mock_storage.delete.call_args.args[0]
    assert upload_key == delete_key


@pytest.mark.asyncio
async def test_kafka_and_delete_both_fail_returns_failed_image(mock_storage, mock_kafka):
    mock_kafka.send = AsyncMock(side_effect=Exception("broker unavailable"))
    mock_storage.delete = AsyncMock(side_effect=Exception("storage unreachable"))
    result = await upload_image(mock_storage, mock_kafka, make_image())
    assert isinstance(result, FailedImage)
    assert "broker unavailable" in result.error


@pytest.mark.asyncio
async def test_upload_storage_failure_returns_failed_image(mock_storage, mock_kafka):
    mock_storage.upload = AsyncMock(side_effect=Exception("connection refused"))
    result = await upload_image(mock_storage, mock_kafka, make_image())
    assert isinstance(result, FailedImage)
    assert "connection refused" in result.error
    mock_kafka.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_aoi_metadata_returns_failed_image(mock_storage, mock_kafka):
    bad_metadata = {
        "version": "1.0",
        "description": "test",
        "timestamp": "2026-06-12T00:00:00Z",
        "machineInfo": {},
        "aoiDefects": {"count": 0, "details": []},
        "imageInfo": {},
    }
    result = await upload_image(mock_storage, mock_kafka, make_image(metadata=bad_metadata))
    assert isinstance(result, FailedImage)


@pytest.mark.asyncio
async def test_inference_trigger_scheduled_when_enabled(mock_storage, mock_kafka, mock_aoi_client):
    with patch.object(settings, "inference_enabled", True):
        result = await upload_image(
            mock_storage,
            mock_kafka,
            make_image(metadata=VALID_METADATA_WITH_CROPS),
            mock_aoi_client,
        )
    assert isinstance(result, UploadedImage)
    mock_aoi_client.schedule_trigger.assert_called_once()
    payload, key = mock_aoi_client.schedule_trigger.call_args.args
    assert key == result.storage_key
    assert payload["metadata"]["objStorePath"] == result.storage_key


@pytest.mark.asyncio
async def test_inference_not_scheduled_when_disabled(mock_storage, mock_kafka, mock_aoi_client):
    with patch.object(settings, "inference_enabled", False):
        result = await upload_image(
            mock_storage, mock_kafka, make_image(), mock_aoi_client
        )
    assert isinstance(result, UploadedImage)
    mock_aoi_client.schedule_trigger.assert_not_called()


@pytest.mark.asyncio
async def test_inference_not_scheduled_without_crop_info(mock_storage, mock_kafka, mock_aoi_client):
    metadata = {**VALID_METADATA, "imageInfo": {}}
    with patch.object(settings, "inference_enabled", True):
        result = await upload_image(
            mock_storage, mock_kafka, make_image(metadata=metadata), mock_aoi_client
        )
    assert isinstance(result, UploadedImage)
    mock_aoi_client.schedule_trigger.assert_not_called()
