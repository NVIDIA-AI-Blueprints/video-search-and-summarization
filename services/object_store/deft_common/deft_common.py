"""Shared types for the DEFT AOI services (backend, snapshot_worker, eval_set_worker).

str-based enums so members compare equal to their wire value and serialise to
that value via json.dumps. Status covers the snapshot lifecycle
(creating/ready/failed/deleted), the eval-set job lifecycle
(creating/ready/failed), and the per-slice outcome (completed/failed). Tag holds
known values of the catalog `tag` metadata field.
"""

from enum import StrEnum


class Status(StrEnum):
    CREATING = "creating"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"
    COMPLETED = "completed"


class ConfirmedLabel(StrEnum):
    PASS = "PASS"
    DEFECT = "DEFECT"


class Tag(StrEnum):
    """Known values of the catalog `metadata.content_metadata.tag` field."""

    KPI = "kpi"
    KPI_GOLDEN = "kpi-golden"
    AOI = "aoi"
    AOI1 = "aoi1"
    AOI1_TRAIN = "aoi1-train"
    AOI1_EVAL = "aoi1-eval"
    AOI1_GOLDEN_TRAIN = "aoi1-golden-train"
    AOI1_GOLDEN_EVAL = "aoi1-golden-eval"
    NORMAL = "normal"
