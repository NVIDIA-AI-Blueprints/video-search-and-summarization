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
"""Materialize a clip-level DSS release into the benchmark's on-disk format.

``physicalAI-event-videos-test`` ships a *raw* layout -- ``clips/`` (393 short
MP4s), ``gt/queries_gt.json`` (6,040 queries -> ``relevant_clip_ids``, no time
bounds), ``manifest.json`` (the ``chunk_id -> clips/<...>.mp4`` map) -- that the
segment-oriented run loop cannot load. This adapter writes the canonical
``dataset.json`` (``schema_version: 3, task: "clip"``) the rest of the flow
expects, plus ``event``/``pas`` subsets and a ``videos/`` entry that shares the
gallery with ingest.

It is idempotent: skipped when ``dataset.json`` is already newer than the raw
``queries_gt.json`` it is built from, so re-runs against a downloaded dataset
do no work.

Design notes traced to the plan review (BENCHMARK_CLIP_RETRIEVAL_PLAN.md):

* relevant clips are stored as **stems** (no ``.mp4``), matching what VST
  ingests and what ``metrics.video_name_matches`` prefix-matches against -- a
  ``.mp4`` GT string never matches a retrieved ``<stem>_<timestamp>_<hash>.mp4``
  and silently zeros every query. [F1]
* the canonical relevance set is ``relevant_clip_ids``; ``_v4_1``/``_oracle``
  are recorded for reference only. ``near_universal`` queries do not
  discriminate and are excluded from scoring. [F4]
* a ``decomposition`` is synthesized per query from ``query_domain`` and used
  only as the dataset-carried fallback when the live decomposer is down (live
  stays the default). [F9]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

#: A query is "near universal" if it is relevant to almost every gallery clip;
#: such queries do not discriminate retrieval and would inflate recall, so
#: they are recorded but not scored.
_SKIP_NEAR_UNIVERSAL = True


def _stem(path: str) -> str:
    """Clip filename -> ingest stem (no extension, no ``clips/`` prefix).

    VST ingests ``clips/CHAD_1_086_1_0.mp4`` and the search core returns that
    filename (possibly renamed with a timestamp/hash suffix). ``video_name_matches``
    strips ``.mp4`` from the *retrieved* name only and prefix-matches the GT
    string, so the GT must be the bare stem or it never matches. [F1]
    """
    return Path(path).stem


def _load_chunk_index(manifest_path: Path) -> dict[str, str]:
    """``chunk_id -> clip stem`` from ``manifest.json``'s ``clips`` list."""
    payload = json.loads(manifest_path.read_text())
    clips = payload.get("clips") or []
    if not isinstance(clips, list) or not clips:
        raise ValueError(
            f"{manifest_path}: expected a non-empty 'clips' list "
            f"(chunk_id -> path); got {type(clips).__name__}"
        )
    index: dict[str, str] = {}
    for entry in clips:
        if not isinstance(entry, dict):
            continue
        chunk_id = entry.get("chunk_id")
        path = entry.get("path")
        if chunk_id and path:
            index[chunk_id] = _stem(path)
    return index


def _decomposition_for(query: dict[str, Any]) -> dict[str, Any]:
    """Synthesize the fallback decomposition from ``query_domain``. [F9]

    Live decomposition is the default; this is used only when the decomposer LLM
    is unreachable, exactly like a dataset that ships its own decompositions.

    * event queries describe an action -> ``has_action: true`` -> fusion
      (the embedding leg carries the action; the attribute leg is empty).
    * pas queries describe a person appearance -> ``attributes: [query]`` ->
      attribute (kNN over per-object visual embeddings).
    """
    text = query.get("query", "")
    domain = query.get("query_domain", "")
    if domain == "pas":
        return {
            "query": text,
            "attributes": [text] if text else [],
            "has_action": False,
            "source_type": "video_file",
        }
    # event (and any unmapped domain) defaults to action -> fusion.
    return {
        "query": text,
        "attributes": [],
        "has_action": True,
        "source_type": "video_file",
    }


def _resolve_relevant(query: dict[str, Any], chunk_index: dict[str, str]) -> tuple[list[str], list[str]]:
    """Map ``relevant_clip_ids`` to stems, returning ``(stems, unresolved)``. [F1]"""
    stems: list[str] = []
    unresolved: list[str] = []
    for chunk_id in query.get("relevant_clip_ids") or []:
        stem = chunk_index.get(chunk_id)
        if stem is None:
            unresolved.append(chunk_id)
        elif stem not in stems:  # de-dup within a query's relevance set
            stems.append(stem)
    return stems, unresolved


def _build_queries(
    raw_queries: list[dict[str, Any]],
    chunk_index: dict[str, str],
    domain_filter: str | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Build the benchmark ``queries`` map for one subset.

    Returns ``(queries, counts)`` where counts records ``n``, ``excluded_near_universal``,
    and ``unresolved_chunk_ids`` for the run summary. Keys are the query text;
    a duplicate text is disambiguated with `` (<query_id>)`` so no query is lost
    while the CLI still searches a meaningful string. [F1]
    """
    queries: dict[str, dict[str, Any]] = {}
    used_keys: set[str] = set()
    excluded_near_universal = 0
    unresolved: list[str] = []

    for q in raw_queries:
        if domain_filter is not None and q.get("query_domain") != domain_filter:
            continue
        if _SKIP_NEAR_UNIVERSAL and q.get("near_universal"):
            excluded_near_universal += 1
            continue

        stems, missing = _resolve_relevant(q, chunk_index)
        unresolved.extend(missing)
        if not stems:
            # An empty relevance set cannot score recall; keep it out rather
            # than inject a zero-GT query that drags the aggregate down. [F4]
            excluded_near_universal += 1
            continue

        text = q.get("query", "") or q.get("query_id", "")
        key = text
        if key in used_keys:
            key = f"{text} ({q.get('query_id', '?')})"
        used_keys.add(key)

        queries[key] = {
            "relevant_clips": stems,
            "query_domain": q.get("query_domain", ""),
            "query_slice": q.get("slice", ""),
            "query_id": q.get("query_id", ""),
            "n_relevant": len(stems),
            "decomposition": _decomposition_for(q),
        }

    counts = {
        "n": len(queries),
        "excluded_near_universal": excluded_near_universal,
        "unresolved_chunk_ids": len(unresolved),
    }
    return queries, counts


def _write_subset(out_path: Path, queries: dict[str, dict[str, Any]], counts: dict[str, int]) -> None:
    out_path.write_text(json.dumps({"schema_version": 3, "task": "clip", "queries": queries}, indent=2))


def _ensure_videos_symlink(dataset_dir: Path) -> None:
    """Point ``videos/`` at ``clips/`` so ingest reuses the existing path. [F12]

    Materialized copies are the fallback if a caller's filesystem declines to
    follow a symlink for the directory walk.
    """
    videos = dataset_dir / "videos"
    clips = dataset_dir / "clips"
    if videos.exists() or videos.is_symlink():
        return
    if not clips.is_dir():
        raise FileNotFoundError(f"{clips}: clip gallery missing; cannot link videos/")
    try:
        videos.symlink_to(clips, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Fallback: materialize hard copies so walks that do not follow the
        # symlink still see the gallery.
        videos.mkdir(parents=True, exist_ok=True)
        for clip in clips.iterdir():
            target = videos / clip.name
            if not target.exists():
                target.write_bytes(clip.read_bytes())


def make_clip_dataset(dataset_dir: Path, dataset_name: str) -> None:
    """Convert a raw clip DSS layout under ``dataset_dir`` into benchmark JSON.

    Idempotent: no work when ``dataset.json`` is newer than ``queries_gt.json``.
    Writes ``dataset.json`` (all queries) plus ``dataset_event.json`` and
    ``dataset_pas.json`` subsets, and links ``videos/`` -> ``clips/``.
    """
    manifest = dataset_dir / "manifest.json"
    queries_gt = dataset_dir / "gt" / "queries_gt.json"
    out = dataset_dir / "dataset.json"
    if not manifest.is_file() or not queries_gt.is_file():
        print(f"ERROR: {dataset_dir} is not a clip DSS layout (missing manifest.json "
              f"or gt/queries_gt.json).", file=sys.stderr)
        sys.exit(1)

    # Idempotent: nothing to do when already up to date.
    if out.is_file() and out.stat().st_mtime >= queries_gt.stat().st_mtime:
        _ensure_videos_symlink(dataset_dir)
        return

    chunk_index = _load_chunk_index(manifest)
    raw = json.loads(queries_gt.read_text())
    raw_queries = raw.get("queries") or []
    if not isinstance(raw_queries, list):
        raise ValueError(f"{queries_gt}: expected 'queries' to be a list; got {type(raw_queries).__name__}")

    all_q, all_counts = _build_queries(raw_queries, chunk_index, None)
    event_q, event_counts = _build_queries(raw_queries, chunk_index, "event")
    pas_q, pas_counts = _build_queries(raw_queries, chunk_index, "pas")

    _write_subset(out, all_q, all_counts)
    _write_subset(dataset_dir / "dataset_event.json", event_q, event_counts)
    _write_subset(dataset_dir / "dataset_pas.json", pas_q, pas_counts)
    _ensure_videos_symlink(dataset_dir)

    # The run summary wants the exclusions visible: a silent drop of
    # near-universal queries would otherwise read as a smaller dataset. [F4]
    meta = raw.get("meta") or {}
    print(f"  Clip dataset '{dataset_name}': {all_counts['n']} queries scored "
          f"({event_counts['n']} event, {pas_counts['n']} pas), "
          f"{all_counts['excluded_near_universal']} excluded (near_universal/empty), "
          f"{all_counts['unresolved_chunk_ids']} unresolved chunk_id(s). "
          f"Gallery: {len(chunk_index)} clips.")
    _ = meta  # meta retained for future per-relset reporting
