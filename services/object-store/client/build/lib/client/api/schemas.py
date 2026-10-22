import base64
import binascii

from pydantic import BaseModel, ConfigDict, field_validator


class Coordinates(BaseModel):
    model_config = ConfigDict(extra="allow")

    left: float
    top: float
    width: float
    height: float

    @field_validator("left", "top")
    @classmethod
    def non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("left/top must be non-negative")
        return v

    @field_validator("width", "height")
    @classmethod
    def positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("width/height must be positive")
        return v


class Crop(BaseModel):
    model_config = ConfigDict(extra="allow")

    coordinates: Coordinates


class CropInfoItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    crop: Crop


class ImageInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    cropInfo: list[CropInfoItem] = []


class Metadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    objStorePath: str
    imageInfo: ImageInfo


class InferenceRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    sampleImage: str
    goldenRefImage: str
    metadata: Metadata

    @field_validator("sampleImage", "goldenRefImage")
    @classmethod
    def valid_base64(cls, v: str) -> str:
        try:
            decoded = base64.b64decode(v, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("image must be valid base64")
        if not decoded:
            raise ValueError("image must not be empty")
        return v


class AcceptedResponse(BaseModel):
    objStorePath: str
    status: str = "accepted"
    cropCount: int


class ModelResult(BaseModel):
    version: str
    score: float
    verdict: str  # "PASS" | "DEFECT"


class CropResult(BaseModel):
    cropId: str
    status: str  # "completed" | "failed"
    score: float | None = None
    verdict: str | None = None
    threshold: float | None = None
    old_model: ModelResult | None = None
    new_model: ModelResult | None = None


class InferenceResultRecord(BaseModel):
    objStorePath: str
    status: str  # "completed" | "partial" | "failed"
    cropCount: int
    completedCount: int
    failedCount: int
    deployment_type: str | None = None
    cropResults: list[CropResult]
