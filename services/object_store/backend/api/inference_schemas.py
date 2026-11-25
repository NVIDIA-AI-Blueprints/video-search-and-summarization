"""Schemas for Workflow 2 — AOI Client contract and AL Archiver inbound records."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DeploymentType(StrEnum):
    """How a model result was produced. StrEnum so it compares/serialises to its
    plain string value (e.g. "normal") over the wire and into Elasticsearch."""

    NORMAL = "normal"
    SHADOW = "shadow"
    CANARY = "canary"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            return cls(value.lower())

class ModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str | None = None
    version: str
    score: float
    verdict: str


class CropResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cropId: str
    status: str
    score: float | None = None
    verdict: str | None = None
    threshold: float | None = None
    old_model: ModelResult | None = None
    new_model: ModelResult | None = None


class InferenceResultRecord(BaseModel):
    """Verbatim record forwarded by AL Archiver from Kafka aoi-inference-results."""

    model_config = ConfigDict(extra="forbid")

    objStorePath: str
    status: str
    cropCount: int
    completedCount: int
    failedCount: int
    deployment_type: DeploymentType | None = None
    model_name: str | None = None
    cropResults: list[CropResult]


class InferenceResultResponse(BaseModel):
    objStorePath: str
    updated: bool = True
