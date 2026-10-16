# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Gallery ground truth for the clip-level scorers (clip-relevance and drive).

Both clip-level models score a retrieval against a fixed gallery of clips with
per-query ``relevant_clip_ids`` -- the Drive release format (``gt/queries_gt.json``
+ ``additional_gt/clips_gt.json``, or a single merged ``{gallery, queries}`` json).
Neither reads the segment-overlap eval dataset.

The clip identity mapping -- which ``clip_id`` a returned ``sensor_id`` /
``video_name`` refers to -- comes from the ingestion manifest produced when the
gallery was uploaded. That manifest is the whole contract: without it a returned
hit cannot be attributed to a gallery clip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_gallery_gt(gt_path: str | Path) -> dict[str, Any]:
    """Read a Drive release root or a merged GT json into ``{meta, gallery, queries}``.

    Same two layouts the Drive ``evaluate_domain_test.py`` accepts. Gallery rows
    carry ``chunk_id``; query rows carry ``query`` + ``relevant_clip_ids``.
    """
    path = Path(gt_path)
    if not path.exists():
        raise SystemExit(f"ERROR: gallery ground truth not found: {path}")
    if path.is_dir():
        try:
            q = json.loads((path / "gt" / "queries_gt.json").read_text())
            c = json.loads((path / "additional_gt" / "clips_gt.json").read_text())
        except FileNotFoundError as e:
            raise SystemExit(
                f"ERROR: gallery release root {path} is missing {e.filename}. "
                "Expected gt/queries_gt.json and additional_gt/clips_gt.json."
            ) from e
        gallery = [
            {**row, "video_path": str((path / row["video"]).resolve())}
            for row in c["gallery"]
        ]
        return {"meta": q.get("meta", {}), "gallery": gallery, "queries": q["queries"]}
    return json.loads(path.read_text())


def load_ingestion_manifest(manifest_path: str | Path) -> list[dict[str, Any]]:
    """The clip -> sensor/source mapping recorded at ingest time."""
    path = Path(manifest_path)
    if not path.exists():
        raise SystemExit(f"ERROR: ingestion manifest not found: {path}")
    payload = json.loads(path.read_text())
    clips = payload.get("clips")
    if not isinstance(clips, list):
        raise SystemExit(f"ERROR: ingestion manifest {path} has no 'clips' list")
    return clips


def build_context(gt_path: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    """Load + cross-validate the gallery and its ingestion manifest.

    Returns the structures the clip-level scorers consume: the GT, the gallery
    clip-id list, the ``clip_id -> manifest row`` map (for clip-relevance's
    ClipScorer), the ``sensor_id -> clip_id`` map (for drive), and a
    ``query text -> query row`` map (to align artifact records with GT rows).
    """
    gt = load_gallery_gt(gt_path)
    gallery = [row["chunk_id"] for row in gt["gallery"]]
    queries = gt["queries"]
    clips = load_ingestion_manifest(manifest_path)

    if not gallery or len(set(gallery)) != len(gallery):
        raise SystemExit("ERROR: gallery has no clips, or duplicate clip ids")
    mapping = {c.get("dataset_id"): c for c in clips}
    if None in mapping or len(mapping) != len(clips) or set(mapping) != set(gallery):
        raise SystemExit(
            "ERROR: the ingestion manifest's clips must map the gallery exactly "
            "(one entry per clip_id, no extras, no missing)."
        )

    sensor_to_clip: dict[str, str] = {}
    for cid in gallery:
        sensor = mapping[cid].get("sensor_id")
        if not sensor:
            raise SystemExit(
                f"ERROR: gallery clip {cid!r} has no sensor_id in the ingestion manifest"
            )
        if sensor in sensor_to_clip:
            raise SystemExit(
                f"ERROR: duplicate sensor_id {sensor!r} in the ingestion manifest"
            )
        sensor_to_clip[sensor] = cid

    by_text: dict[str, dict[str, Any]] = {}
    for q in queries:
        text = q.get("query")
        if not isinstance(text, str) or not text.strip():
            raise SystemExit(
                "ERROR: every gallery query needs a non-empty 'query' text"
            )
        if text in by_text:
            raise SystemExit(f"ERROR: duplicate gallery query text {text!r}")
        by_text[text] = q

    return {
        "gt": gt,
        "gallery": gallery,
        "queries": queries,
        "mapping": mapping,
        "sensor_to_clip": sensor_to_clip,
        "by_text": by_text,
        "sources": [Path(mapping[cid]["filename"]).stem for cid in gallery],
    }
