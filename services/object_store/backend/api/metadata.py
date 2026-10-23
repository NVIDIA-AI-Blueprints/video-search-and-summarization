"""
Pydantic v2 models for the AOI metadata JSON schema.

- Required fields (those in the schema's `required` list) have no default,
  so Pydantic raises a ValidationError if they're missing.
- Optional fields default to None.
- JSON keys that aren't valid Python identifiers (e.g. "ref-points",
  "diameter-in-mm", "golden-ref") are exposed as snake_case attributes
  via `Field(alias=...)`. `populate_by_name=True` lets you construct
  models with either the alias or the Python name.

Usage:
    obj = AOIMetadata.model_validate(json_dict)        # parse + validate
    obj.model_dump(by_alias=True, exclude_none=True)   # back to schema-shaped dict
    obj.model_dump_json(by_alias=True, exclude_none=True)
"""

from __future__ import annotations

from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from backend.api.inference_schemas import DeploymentType
from deft_common import ConfirmedLabel

Number = Union[int, float]


class _Base(BaseModel):
    """Shared config: allow alias OR python name on input; forbid unknown keys."""
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )


# ---------------------------------------------------------------------------
# Shared / leaf objects
# ---------------------------------------------------------------------------

class CustomInfo(_Base):
    key: str
    value: str


class Point(_Base):
    top: Number
    left: Number
    width: Number
    height: Number


# ---------------------------------------------------------------------------
# machineInfo
# ---------------------------------------------------------------------------

class MachineInfo(_Base):
    machineId: str
    location: str
    jobType: str
    batchId: Optional[str] = None
    customInfo: Optional[CustomInfo] = None
    serialNumber: Optional[str] = None


# ---------------------------------------------------------------------------
# aoiDefects
# ---------------------------------------------------------------------------

class DefectDetail(_Base):
    """
    NOTE: the schema's `required` list says ["type", "count"], but the only
    property defined is `defectType`. Treated as a schema typo —
    `defectType` is required here.
    """
    defectType: str
    count: Number


class AoiDefects(_Base):
    count: Number
    details: List[DefectDetail]
    customInfo: Optional[CustomInfo] = None


# ---------------------------------------------------------------------------
# imageInfo subcomponents
# ---------------------------------------------------------------------------

class Crop(_Base):
    cropType: str
    coordinates: Point
    image: Optional[str] = None
    crop_ratio: Optional[Number] = None


class AoiDefectInfo(_Base):
    defectType: str


# ---------------------------------------------------------------------------
# Workflow 2 — inference fields stored in ES (inferenceSummary + cropInfo.inference)
# ---------------------------------------------------------------------------

class ModelInference(_Base):
    model_name: Optional[str] = None
    version: str
    score: float
    verdict: str


class CropInference(_Base):
    inspectionId: str
    status: str
    score: Optional[float] = None
    verdict: Optional[str] = None
    threshold: Optional[float] = None
    old_model: Optional[ModelInference] = None
    new_model: Optional[ModelInference] = None


class InferenceSummary(_Base):
    status: str
    cropCount: int
    completedCount: int
    failedCount: int
    updatedAt: str
    deployment_type: Optional[DeploymentType] = None
    model_name: Optional[str] = None


class CropItem(_Base):
    id: str
    label: str
    confirmed_label: Optional[ConfirmedLabel] = None
    crop: Crop
    aoiDefectInfo: AoiDefectInfo
    packageType: Optional[str] = None
    partNumber: Optional[str] = None
    customInfo: Optional[CustomInfo] = None
    rotationAngle: Optional[Number] = Field(default=None, validation_alias="rotation_angle")
    referenceLabels: Optional[List[str]]
    inference: Optional[CropInference] = None


class ImageInfo(_Base):
    model_config = ConfigDict(extra="allow")
    # imageInfo has no required sub-fields per the schema.
    # JSON: "golden-ref"
    goldenRef: Optional[str] = Field(default=None, alias="golden-ref")
    imageType: Optional[str] = None
    path: Optional[str] = None
    layer: Optional[str] = None
    imageSize: Optional[int] = None  # size of the uploaded image in bytes, set by the backend
    cropInfo: Optional[List[CropItem]] = None


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

class AOIMetadata(_Base):
    """
    Root model. Required: version, description, timestamp, objStorePath, machineInfo,
    aoiDefects, imageInfo. customInfo is optional.
    """
    version: str
    description: str
    timestamp: str
    tag: Optional[str] = None
    objStorePath: str
    machineInfo: MachineInfo
    aoiDefects: AoiDefects
    imageInfo: ImageInfo
    customInfo: Optional[CustomInfo] = None
    inferenceSummary: Optional[InferenceSummary] = None
