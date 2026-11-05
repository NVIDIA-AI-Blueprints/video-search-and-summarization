"""Deterministic object-store keys for a snapshot (design §8.1).

    <prefix>/<snapshot_id>/
    ├── manifest0.jsonl ... manifest<N-1>.jsonl
    ├── _status/slice0.json ... slice<N-1>.json   (durable per-slice markers)
    └── snapshot.json
"""

from snapshot_worker.config import settings


def snapshot_dir(snapshot_id: str) -> str:
    return f"{settings.snapshot_prefix}/{snapshot_id}"


def manifest_key(snapshot_id: str, slice_id: int) -> str:
    return f"{snapshot_dir(snapshot_id)}/manifest{slice_id}.jsonl"


def snapshot_key(snapshot_id: str) -> str:
    return f"{snapshot_dir(snapshot_id)}/snapshot.json"


def latest_snapshot_key(catalog: str = "aoi") -> str:
    # A copy of the most recently finalised snapshot.json; the baseline for the
    # "since last snapshot" deltas and last_snapshot_* pointers.
    #
    # One per catalog: AOI and Smart City snapshots count different documents,
    # so a shared pointer would have whichever finished last set the baseline
    # for both. AOI keeps the original key so an existing baseline survives the
    # upgrade; work items predating the catalog field default to it.
    if catalog == "aoi":
        return f"{settings.snapshot_prefix}/latest_snapshot.json"
    return f"{settings.snapshot_prefix}/latest_snapshot_{catalog}.json"


def status_prefix(snapshot_id: str) -> str:
    return f"{snapshot_dir(snapshot_id)}/_status/"


def status_key(snapshot_id: str, slice_id: int) -> str:
    return f"{status_prefix(snapshot_id)}slice{slice_id}.json"
