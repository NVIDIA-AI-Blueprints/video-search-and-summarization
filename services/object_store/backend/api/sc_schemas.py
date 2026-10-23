"""Validation model for Smart City frames arriving on the archival topic.

The wire format is the NVIDIA VSS Selected Image Schema:

    {"version", "id", "timestamp", "sensorId", "imageData": {...}, "objects": [...]}

plus ``tag``, which the envelope has no field for but the catalog selects on
(see the KPI / mining dataset flow in the Smart City design, and
``_EXCLUDED_TAGS`` in snapshot_service).

Unknown keys are rejected, mirroring AOIMetadata. That is a deliberate trade:
NvSchema defines more of a frame than this envelope carries, so a producer that
starts sending one of those fields will have its frames REJECTED -- image and
all -- until the field is declared here. Rejecting is not how the ingest
pipeline behaves, which filters unknown keys instead of dropping the document,
so the two differ on purpose: the pipeline cannot ask anyone to fix a producer,
and this can. The fields NvSchema already defines and the sc_* template already
maps are declared up front so the common growth path costs nothing.
"""

from __future__ import annotations

from typing import Any, Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class _Base(BaseModel):
    """Accept the alias or the python name; reject anything undeclared."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ImageMetadata(_Base):
    # The one place NvSchema is most likely to grow per-image detail, and an
    # unknown key here is not worth losing the frame over.
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    width: Optional[int] = None
    height: Optional[int] = None


class ImageData(_Base):
    content_type: str
    data: str
    metadata: ImageMetadata = Field(default_factory=ImageMetadata)


class GeoPoint(_Base):
    lat: Optional[float] = None
    lon: Optional[float] = None
    # NvSchema carries altitude inside the geo object; the ingest pipeline
    # splits it out, because an Elasticsearch geo_point rejects the extra key.
    alt: Optional[float] = None


class Coordinate(_Base):
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None


class SensorInfo(_Base):
    id: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    location: Optional[GeoPoint] = None
    altitude: Optional[float] = None
    coordinate: Optional[Coordinate] = None


class PlaceInfo(_Base):
    id: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    location: Optional[GeoPoint] = None
    altitude: Optional[float] = None
    coordinate: Optional[Coordinate] = None
    # NvSchema shows name/lane/level here but the keys are open-ended, which is
    # why the sc_* template maps this object dynamic:false.
    info: Optional[dict[str, Any]] = None


class AnalyticsModule(_Base):
    id: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None
    version: Optional[str] = None


class SCFrame(_Base):
    """One selected frame, as published to the archival topic."""

    version: str
    # NvSchema names the frame id "id"; the catalog stores it as frameId.
    id: str = Field(validation_alias=AliasChoices("id", "frameId"))
    # Selected Image Schema sends "timestamp"; the minimal VSS variant sends
    # "@timestamp". The ingest pipeline accepts both, so this does too rather
    # than rejecting frames the rest of the path handles.
    timestamp: str = Field(validation_alias=AliasChoices("timestamp", "@timestamp"))
    sensorId: str

    # Optional for selected-frames messages. POST /api/samples provides the
    # image separately through ImageItem.data and ImageItem.content_type.
    imageData: Optional[ImageData] = None

    # Left untyped: VSS sends either pipe-delimited strings or full objects, and
    # turning those into typed objects is the parser's job, not this envelope's.
    objects: list[Union[dict[str, Any], str]] = Field(default_factory=list)

    # Not part of Selected Image Schema. Only the producer knows which dataset a
    # frame belongs to, and snapshot selection filters on it.
    tag: Optional[str] = None

    # Declared ahead of use: NvSchema defines these and the sc_* template maps
    # them, so a producer that starts sending them is not rejected.
    videoPath: Optional[str] = None
    sensor: Optional[SensorInfo] = None
    place: Optional[PlaceInfo] = None
    analyticsModule: Optional[AnalyticsModule] = None
