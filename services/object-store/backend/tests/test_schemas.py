import base64

import pytest
from pydantic import ValidationError

from backend.api.schemas import ImageItem, ImageMetadata, SampleBatchRequest, REQUIRED_METADATA_FIELDS
from tests.conftest import VALID_B64, VALID_METADATA


def make_image(**kwargs) -> dict:
    return {
        "filename": "test.jpg",
        "content_type": "image/jpeg",
        "data": VALID_B64,
        "metadata": VALID_METADATA,
        **kwargs,
    }


class TestImageItem:
    def test_valid(self):
        img = ImageItem(**make_image())
        assert img.filename == "test.jpg"

    def test_invalid_base64_rejected(self):
        with pytest.raises(ValidationError, match="base64"):
            ImageItem(**make_image(data="not valid base64!!!"))

    def test_empty_data_rejected(self):
        with pytest.raises(ValidationError, match="base64"):
            ImageItem(**make_image(data="!!!"))


class TestMetadataValidation:
    def test_valid_metadata_passes(self):
        img = ImageItem(**make_image())
        assert isinstance(img.metadata, ImageMetadata)

    def test_wrong_type_rejected(self):
        with pytest.raises(ValidationError, match="valid dictionary"):
            ImageItem(**make_image(metadata="not a dict"))

    def test_missing_single_field_rejected(self):
        parsed = {**VALID_METADATA}
        del parsed["version"]
        with pytest.raises(ValidationError, match="version"):
            ImageItem(**make_image(metadata=parsed))

    def test_missing_multiple_fields_rejected(self):
        parsed = {**VALID_METADATA}
        del parsed["version"]
        del parsed["aoiDefects"]
        with pytest.raises(ValidationError, match="Field required"):
            ImageItem(**make_image(metadata=parsed))

    def test_empty_dict_rejected(self):
        with pytest.raises(ValidationError, match="Field required"):
            ImageItem(**make_image(metadata={}))

    def test_extra_fields_allowed(self):
        parsed = {**VALID_METADATA, "customField": "extra"}
        img = ImageItem(**make_image(metadata=parsed))
        assert img.metadata.model_extra.get("customField") == "extra"

    def test_all_required_fields_present_passes(self):
        assert REQUIRED_METADATA_FIELDS <= VALID_METADATA.keys()


class TestSampleBatchRequest:
    def test_valid_single_image(self):
        req = SampleBatchRequest(images=[make_image()])
        assert len(req.images) == 1

    def test_valid_max_images(self):
        images = [make_image(filename=f"img{i}.jpg") for i in range(10)]
        req = SampleBatchRequest(images=images)
        assert len(req.images) == 10

    def test_empty_list_rejected(self):
        with pytest.raises(ValidationError, match="empty"):
            SampleBatchRequest(images=[])

    def test_exceeds_count_limit(self):
        images = [make_image(filename=f"img{i}.jpg") for i in range(11)]
        with pytest.raises(ValidationError, match="Maximum"):
            SampleBatchRequest(images=images)

    def test_exceeds_size_limit(self):
        large_data = base64.b64encode(b"x" * (10 * 1024 * 1024 + 1)).decode()
        with pytest.raises(ValidationError, match="size"):
            SampleBatchRequest(images=[make_image(data=large_data)])

    def test_size_exactly_at_limit_passes(self):
        at_limit = base64.b64encode(b"x" * (10 * 1024 * 1024)).decode()
        req = SampleBatchRequest(images=[make_image(data=at_limit)])
        assert len(req.images) == 1
