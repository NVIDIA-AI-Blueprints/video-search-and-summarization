"""Manifest line construction for the Eval-set pipeline (design §8.2).

Each ES hit becomes one manifest record. file_path/obj_version/size_byte/sha256
are pulled from content_metadata when present; metadata carries the full
content_metadata for downstream readers.
"""

from typing import Any


def manifest_line(hit: dict[str, Any]) -> dict[str, Any]:
    """Build one manifest record from an ES hit (design §8.2).

    Falls back to the ES doc id for file_path so a line is never path-less.
    """
    source = hit.get("_source", {})
    cm = source.get("metadata", {}).get("content_metadata", {})
    if not isinstance(cm, dict):
        cm = {}
    return {
        "file_path": cm.get("objStorePath") or hit.get("_id"),
        "size_byte": cm.get("size_byte") or cm.get("imageSize"),
        "sha256": cm.get("sha256"),
        "obj_version": cm.get("obj_version") or cm.get("objVersion"),
        "metadata": cm,
    }


def record_class(hit: dict[str, Any]) -> str:
    """Class key for class_counts aggregation — machineId if present, else 'unknown'."""
    cm = hit.get("_source", {}).get("metadata", {}).get("content_metadata", {})
    if isinstance(cm, dict):
        mi = cm.get("machineInfo")
        if isinstance(mi, dict) and mi.get("machineId"):
            return str(mi["machineId"])
        if cm.get("machineId"):
            return str(cm["machineId"])
    return "unknown"
