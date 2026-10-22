from datetime import datetime, timedelta
from typing import Any

from deft_common import Status
from pydantic import BaseModel, ConfigDict, Field, field_validator


class KpiSelection(BaseModel):
    # tag is required; any other keys (including optional created_after) are
    # passed through into the ES query (see kpi_service.compile_kpi_selection).
    model_config = ConfigDict(extra="allow")

    tag: str = Field(min_length=1)
    created_after: str | None = None

    @field_validator("created_after")
    @classmethod
    def validate_created_after(cls, v: str | None) -> str | None:
        # Optional; when present it must be an ISO 8601 datetime that explicitly
        # denotes UTC (trailing 'Z' or '+00:00'). Naive / non-UTC values rejected.
        if v is None:
            return v
        try:
            dt = datetime.fromisoformat(v)
        except (TypeError, ValueError):
            raise ValueError("created_after must be an ISO 8601 datetime string in UTC")
        if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
            raise ValueError("created_after must be an ISO 8601 datetime string in UTC")
        return v


class KpiRequest(BaseModel):
    selection: KpiSelection
    reason: str | None = None
    metadata: dict[str, Any] | None = None


class KpiResponse(BaseModel):
    kpi_id: str


class KpiCountResponse(BaseModel):
    count: int


class Manifest(BaseModel):
    format: str
    uri: str
    size_bytes: int | None = None
    sha256: str | None = None
    record_count: int | None = None


class KpiStatusResponse(BaseModel):
    # Mirrors the KPI worker's kpi.json (superset); extra worker-side fields are
    # tolerated rather than breaking the contract.
    model_config = ConfigDict(extra="ignore")

    kpi_id: str
    status: Status
    created_at: str | None = None
    requested_at: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] | None = None
    query: dict[str, Any] | None = None
    slice_count: int | None = None
    total_records: int | None = None
    manifests: list[Manifest] = []
    errors: list[dict[str, Any]] = []
