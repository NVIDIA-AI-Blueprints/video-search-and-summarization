from datetime import datetime, timedelta
from typing import Any

from deft_common import Status
from pydantic import BaseModel, ConfigDict, field_validator


class SnapshotSelection(BaseModel):
    # created_after is required; any other keys are passed through into the ES
    # query as term/terms filters (see snapshot_service.compile_selection).
    model_config = ConfigDict(extra="allow")

    created_after: str

    @field_validator("created_after")
    @classmethod
    def validate_created_after(cls, v: str) -> str:
        # Must be an ISO 8601 datetime that explicitly denotes UTC (trailing 'Z'
        # or '+00:00'); naive (no timezone) and non-UTC offsets are rejected.
        try:
            dt = datetime.fromisoformat(v)
        except (TypeError, ValueError):
            raise ValueError("created_after must be an ISO 8601 datetime string in UTC")
        if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
            raise ValueError("created_after must be an ISO 8601 datetime string in UTC")
        return v


class SnapshotRequest(BaseModel):
    selection: SnapshotSelection
    snapshot_id: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] | None = None


class SnapshotResponse(BaseModel):
    snapshot_id: str


class Manifest(BaseModel):
    format: str
    uri: str
    size_bytes: int | None = None
    sha256: str | None = None
    record_count: int | None = None


class SnapshotStatusResponse(BaseModel):
    # Mirrors the finalizer's snapshot.json (superset of API §9.1); extra
    # worker-side fields are tolerated rather than breaking the contract.
    model_config = ConfigDict(extra="ignore")

    snapshot_id: str
    status: Status
    # Which catalog the snapshot was taken over; absent on snapshots created
    # before the catalog segment existed.
    catalog: str | None = None
    created_at: str | None = None
    requested_at: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] | None = None
    query: dict[str, Any] | None = None
    slice_count: int | None = None
    total_records: int | None = None
    total_bytes: int | None = None
    records_since_last_snapshot: int | None = None
    bytes_since_last_snapshot: int | None = None
    last_snapshot_id: str | None = None
    last_snapshot_created_at: str | None = None
    manifests: list[Manifest] = []
    errors: list[dict[str, Any]] = []


class CatalogStatsResponse(BaseModel):
    status: str = "live"
    total_records: int
    total_bytes: int
    records_since_last_snapshot: int
    bytes_since_last_snapshot: int
    last_snapshot_id: str | None = None
    last_snapshot_created_at: str | None = None
