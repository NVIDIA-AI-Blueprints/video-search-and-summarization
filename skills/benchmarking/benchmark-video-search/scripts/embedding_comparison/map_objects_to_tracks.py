#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Map live ``mdx-behavior-*`` objects to release PAS track IDs.

The live pipeline indexes *runtime-detected objects* keyed by
``(sensor_id, object_id)``; the PAS repo scores *frozen release tracks*
keyed by ``track_uid``. Per-query AP and the track-level metric are only
comparable once these two galleries are aligned. This helper performs the
alignment by matching each object to the release track in the same clip
(``chunk_id``) whose representative-frame bbox has the highest IoU with the
object's bbox, above a minimum.

The core mapping is pure and side-effect-free, so it is unit-testable without
Elasticsearch. The CLI only loads two local files: a behavior-index snapshot
(one JSON object per line with ``sensor_id``, ``object_id``, ``chunk_id``,
``bbox``) and the release ``tracks_pas_attributes.json``; both bboxes are
``[x1, y1, x2, y2]`` in pixel space.
"""
import argparse
import json
import sys
from pathlib import Path

from comparison_common import read_json


def bbox_iou(a: list[float], b: list[float]) -> float:
    """IoU of two [x1, y1, x2, y2] boxes; 0.0 when they do not overlap."""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    area_a = max(0.0, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(0.0, (b[2] - b[0]) * (b[3] - b[1]))
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _track_bbox(track: dict) -> list[float]:
    rep = track.get("representative_frame") or {}
    return list(rep.get("bbox_xyxy_pixels") or [])


def load_tracks(path: Path) -> list[dict]:
    """Release tracks Pas attributes -> [{track_uid, chunk_id, bbox}]."""
    data = read_json(path)
    rows = data["tracks"] if isinstance(data, dict) else data
    tracks = []
    for t in rows:
        bbox = _track_bbox(t)
        if not bbox:
            continue
        tracks.append({"track_uid": t["track_uid"], "chunk_id": t["chunk_id"], "bbox": bbox})
    return tracks


def load_objects(path: Path) -> list[dict]:
    """Behavior-index snapshot JSONL -> [{sensor_id, object_id, chunk_id, bbox}]."""
    objects = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if not record.get("bbox") or not record.get("chunk_id"):
            continue
        objects.append({k: record[k] for k in ("sensor_id", "object_id", "chunk_id", "bbox")})
    return objects


def map_objects_to_tracks(objects: list[dict], tracks: list[dict], min_iou: float = 0.0) -> list[dict]:
    """Best release track per object (same chunk_id, highest IoU >= min_iou)."""
    by_chunk: dict[str, list[dict]] = {}
    for t in tracks:
        by_chunk.setdefault(t["chunk_id"], []).append(t)
    mapping = []
    for obj in objects:
        best, best_iou = None, min_iou
        for t in by_chunk.get(obj["chunk_id"], []):
            iou = bbox_iou(obj["bbox"], t["bbox"])
            if iou > best_iou:
                best, best_iou = t["track_uid"], iou
        mapping.append({
            "sensor_id": obj["sensor_id"],
            "object_id": obj["object_id"],
            "chunk_id": obj["chunk_id"],
            "track_uid": best,
            "iou": round(best_iou, 6) if best is not None else None,
        })
    return mapping


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--objects", type=Path, required=True,
                    help="Behavior-index snapshot JSONL: {sensor_id, object_id, chunk_id, bbox:[x1,y1,x2,y2]} per line.")
    p.add_argument("--tracks", type=Path, required=True,
                    help="Release tracks_pas_attributes.json (or a JSON list of tracks).")
    p.add_argument("--min-iou", type=float, default=0.0, help="Minimum IoU to assign a track (default 0.0).")
    p.add_argument("--out", type=Path, required=True, help="Write the mapping JSONL here.")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    mapping = map_objects_to_tracks(load_objects(args.objects), load_tracks(args.tracks), args.min_iou)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as stream:
        for record in mapping:
            stream.write(json.dumps(record) + "\n")
    assigned = sum(1 for r in mapping if r["track_uid"] is not None)
    print(f"mapped {assigned}/{len(mapping)} objects to a release track", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
