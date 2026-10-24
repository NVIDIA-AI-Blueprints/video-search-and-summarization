import base64
import binascii
from typing import Any, Optional

from deft_common import Tag
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from backend.api.sc_schemas import SCFrame
from backend.config import settings

REQUIRED_METADATA_FIELDS = {
    "version",
    "description",
    "timestamp",
    "machineInfo",
    "aoiDefects",
    "imageInfo",
}


class ImageMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: str
    description: str
    timestamp: str
    tag: Optional[Tag] = None
    machineInfo: dict[str, Any]
    aoiDefects: dict[str, Any]
    imageInfo: dict[str, Any]


class ImageItem(BaseModel):
    filename: str
    content_type: str
    data: str
    metadata: ImageMetadata | SCFrame

    @field_validator("data")
    @classmethod
    def validate_base64(cls, v: str) -> str:
        try:
            base64.b64decode(v, validate=True)
        except binascii.Error:
            raise ValueError("data must be valid base64")
        return v


class SampleBatchRequest(BaseModel):
    images: list[ImageItem]

    @field_validator("images")
    @classmethod
    def validate_count(cls, v: list[ImageItem]) -> list[ImageItem]:
        if not v:
            raise ValueError("images list must not be empty")
        if len(v) > settings.max_images_per_request:
            raise ValueError(f"Maximum {settings.max_images_per_request} images per request")
        return v

    @model_validator(mode="after")
    def validate_total_size(self) -> "SampleBatchRequest":
        total = sum(len(base64.b64decode(img.data)) for img in self.images)
        if total > settings.max_request_bytes:
            raise ValueError(
                f"Total decoded size {total} bytes exceeds the {settings.max_request_bytes // (1024 * 1024)} MB limit"
            )
        return self


class UploadedImage(BaseModel):
    filename: str
    storage_key: str
    url: str
    size_bytes: int


class FailedImage(BaseModel):
    filename: str
    error: str


class SampleBatchResponse(BaseModel):
    uploaded: list[UploadedImage]
    failed: list[FailedImage]
