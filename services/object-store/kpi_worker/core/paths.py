"""Deterministic object-store keys for a KPI set (mirrors snapshot §8.1).

    <prefix>/<kpi_id>/
    ├── manifest0.jsonl ... manifest<N-1>.jsonl
    ├── _status/slice0.json ... slice<N-1>.json   (durable per-slice markers)
    └── kpi.json
"""

from kpi_worker.config import settings


def kpi_dir(kpi_id: str) -> str:
    return f"{settings.kpi_prefix}/{kpi_id}"


def manifest_key(kpi_id: str, slice_id: int) -> str:
    return f"{kpi_dir(kpi_id)}/manifest{slice_id}.jsonl"


def kpi_key(kpi_id: str) -> str:
    return f"{kpi_dir(kpi_id)}/kpi.json"


def status_prefix(kpi_id: str) -> str:
    return f"{kpi_dir(kpi_id)}/_status/"


def status_key(kpi_id: str, slice_id: int) -> str:
    return f"{status_prefix(kpi_id)}slice{slice_id}.json"
