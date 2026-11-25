"""Manifest line construction (design §8.2).

Each ES hit becomes one JSONL record: object file path, size, version, and the
record metadata. obj_version / sha256 are not tracked by the ingest pipeline
yet (open question #5) and are emitted as null for now.
"""

import json
from typing import Any


def _content_metadata(hit: dict[str, Any]) -> dict[str, Any]:
    source = hit.get("_source") or {}
    meta = source.get("metadata") or {}
    return meta.get("content_metadata") or {}


def to_manifest_line(hit: dict[str, Any]) -> dict[str, Any]:
    source = hit.get("_source") or {}
    meta = source.get("metadata") or {}
    cm = meta.get("content_metadata") or {}
    return {
        "id": hit.get("_id"),
        "file_path": cm.get("objStorePath") or meta.get("source"),
        "size_byte": cm.get("imageSize"),
        "obj_version": cm.get("obj_version"),
        "sha256": cm.get("sha256"),
        "metadata": cm,
    }


def manifest_line_bytes(hit: dict[str, Any]) -> bytes:
    line = json.dumps(to_manifest_line(hit), separators=(",", ":"))
    return (line + "\n").encode()


def hit_size_bytes(hit: dict[str, Any]) -> int:
    size = _content_metadata(hit).get("imageSize")
    return size if isinstance(size, int) else 0
