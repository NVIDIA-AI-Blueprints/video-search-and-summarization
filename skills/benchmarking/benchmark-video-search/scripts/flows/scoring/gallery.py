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
from collections import Counter
from pathlib import Path
from typing import Any

from .pas_ground_truth import VERSION as PAS_VERSION
from .pas_ground_truth import frame_supported_queries


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
    result = json.loads(path.read_text())
    result["gallery"] = [
        {
            **row,
            "video_path": str(
                (path.parent / row.get("video_path", row["video"])).resolve()
            ),
        }
        for row in result["gallery"]
    ]
    return result


def validate_gallery_queries(gt):
    """Check identities before ingestion or inference; repeated text is valid."""
    gallery = [row["chunk_id"] for row in gt["gallery"]]
    if not gallery or len(set(gallery)) != len(gallery):
        raise SystemExit("ERROR: gallery has no clips, or duplicate clip ids")
    queries = gt["queries"]
    ids = [q.get("query_id") for q in queries]
    if any(not isinstance(qid, str) or not qid.strip() for qid in ids) or len(
        set(ids)
    ) != len(ids):
        raise SystemExit("ERROR: every gallery query needs a unique nonempty query_id")
    if any(
        not isinstance(q.get("query"), str) or not q["query"].strip() for q in queries
    ):
        raise SystemExit("ERROR: every gallery query needs a non-empty 'query' text")


def resolve_query(record, context):
    """Resolve stable identity, accepting legacy text only when unambiguous."""
    query_id = getattr(record, "query_id", None)
    if query_id is not None:
        row = context["all_by_id"].get(query_id)
        if row is None or row["query"] != record.query:
            raise SystemExit(
                f"ERROR: unknown or conflicting artifact query_id {query_id!r}"
            )
        return context["by_id"].get(query_id)
    if record.query in context["ambiguous_query_texts"]:
        raise SystemExit(
            f"ERROR: legacy artifact query {record.query!r} is ambiguous; query_id is required"
        )
    row = context["all_by_text"].get(record.query)
    if row is None:
        raise SystemExit(
            f"ERROR: artifact query {record.query!r} is not in the gallery GT"
        )
    return context["by_id"].get(row["query_id"])


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


def build_context(
    gt_path: str | Path,
    manifest_path: str | Path,
    *,
    pas_frame_supported: bool | None = None,
) -> dict[str, Any]:
    """Load + cross-validate the gallery and its ingestion manifest.

    Returns the structures the clip-level scorers consume: the GT, the gallery
    clip-id list, the ``clip_id -> manifest row`` map (for clip-relevance's
    ClipScorer), the ``sensor_id -> clip_id`` map (for drive), and a
    ``query text -> query row`` map (to align artifact records with GT rows).

    By default, when the release includes track annotations, PAS queries are
    rewritten so their
    ``relevant_clip_ids`` come from the released positive tracks whose
    representative frame supports the query's resolved attributes (PhysicalAI
    v1.1.2), read from ``additional_gt/tracks_pas_attributes.json`` in the
    release root. PAS queries left with no supported positive are excluded and
    listed; the returned context then also carries ``cohort`` and ``excluded``.
    ``pas_frame_supported=True`` requires this protocol; ``False`` disables it.
    This derivation needs the release-directory layout -- a merged GT json has
    nowhere to put the tracks file. Missing annotations in automatic mode are
    explicitly recorded. Event queries follow Drive eligibility.
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

    validate_gallery_queries(gt)
    texts = Counter(q["query"] for q in queries)
    ambiguous_query_texts = {text for text, count in texts.items() if count > 1}
    all_by_id = {q["query_id"]: q for q in queries}
    all_by_text = {
        q["query"]: q for q in queries if q["query"] not in ambiguous_query_texts
    }

    excluded: list[dict[str, Any]] = []
    excluded_query_texts: set[str] = set()
    root = Path(gt_path)
    tracks_file = root / "additional_gt" / "tracks_pas_attributes.json"
    apply_pas = pas_frame_supported is True or (
        pas_frame_supported is None and root.is_dir() and tracks_file.exists()
    )
    if apply_pas:
        root = Path(gt_path)
        if not root.is_dir():
            raise SystemExit(
                "ERROR: --pas-frame-supported needs the release-directory layout "
                "(additional_gt/tracks_pas_attributes.json); a merged GT json has "
                "no track annotations."
            )
        tracks_file = root / "additional_gt" / "tracks_pas_attributes.json"
        if not tracks_file.exists():
            raise SystemExit(
                f"ERROR: --pas-frame-supported requires {tracks_file} in the release"
            )
        try:
            tracks = json.loads(tracks_file.read_text())["tracks"]
            queries, excluded = frame_supported_queries(queries, tracks, set(gallery))
        except (ValueError, KeyError) as e:
            raise SystemExit(f"ERROR: invalid PAS ground truth: {e}") from e
        # Map excluded query_ids back to their texts so the scorer can skip
        # retrieval records for PAS queries the derivation dropped, without
        # mistaking them for a genuine artifact/GT mismatch.
        excluded_ids = {e["query_id"] for e in excluded}
        excluded_query_texts = {
            q["query"] for q in gt["queries"] if q.get("query_id") in excluded_ids
        }

    # Match Drive event eligibility. Missing home clips are diagnostic only:
    # the reference still scores a query with positives elsewhere in the gallery.
    eligible = []
    gallery_set = set(gallery)
    missing_home = 0
    for q in queries:
        if q.get("query_domain") == "event":
            if q.get("chunk_id") not in gallery_set:
                missing_home += 1
            reason = (
                "near_universal"
                if q.get("near_universal")
                else "no_gallery_positives"
                if not gallery_set.intersection(q["relevant_clip_ids"])
                else None
            )
            if reason:
                excluded.append(
                    {
                        "query_id": q["query_id"],
                        "query_domain": "event",
                        "reason": reason,
                    }
                )
                excluded_query_texts.add(q["query"])
                continue
            q = {
                **q,
                "relevant_clip_ids": [
                    cid for cid in q["relevant_clip_ids"] if cid in gallery_set
                ],
            }
        eligible.append(q)
    queries = eligible
    by_text = {
        q["query"]: q for q in queries if q["query"] not in ambiguous_query_texts
    }
    cohort = {
        "protocol": PAS_VERSION if apply_pas else "released-clip-relevance",
        "pas_frame_supported": "applied"
        if apply_pas
        else (
            "disabled"
            if pas_frame_supported is False
            else "unknown: released track annotations unavailable"
        ),
        "input": dict(Counter(q.get("query_domain", "unknown") for q in gt["queries"])),
        "eligible": dict(Counter(q.get("query_domain", "unknown") for q in queries)),
        "input_queries": len(gt["queries"]),
        "eligible_queries": len(queries),
        "event_missing_home_clip": missing_home,
        "excluded": excluded,
    }

    return {
        "gt": gt,
        "gallery": gallery,
        "queries": queries,
        "mapping": mapping,
        "sensor_to_clip": sensor_to_clip,
        "by_text": by_text,
        "by_id": {q["query_id"]: q for q in queries},
        "all_by_id": all_by_id,
        "all_by_text": all_by_text,
        "ambiguous_query_texts": ambiguous_query_texts,
        "excluded_query_ids": {q["query_id"] for q in excluded},
        "sources": [Path(mapping[cid]["filename"]).stem for cid in gallery],
        "cohort": cohort,
        "excluded": excluded,
        "excluded_query_texts": excluded_query_texts,
    }
