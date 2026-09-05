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

"""Turning a decomposed query into a CLI invocation.

The agent path (``POST /api/v1/search`` with ``agent_mode: true``) runs an LLM
query-decomposition step before retrieval. The CLI has no such step: it takes
an already-structured request. So an eval that feeds raw natural language to
``vss search run`` is not comparing like with like -- it is comparing
"decomposed then retrieved" against "retrieved raw".

This module closes that gap. Given the decomposition the agent would have
produced, it derives the retrieval path and the CLI arguments.

The decomposition contract is ``QUERY_DECOMPOSITION_PROMPT`` in
``vss_agents/tools/search.py``::

    query            rewritten description, actions AND attributes
    attributes       person-appearance attributes ONLY; never bare "person"
    has_action       true when an action/event is described
    video_sources    named sources, empty when none mentioned
    source_type      "video_file" | "rtsp"
    timestamp_start  ISO-8601, base date 2025-01-01
    timestamp_end    ISO-8601
    object_ids       explicit tracked-object ids
    top_k            only when the query says so

Where decompositions come from is deliberately not decided here. A sidecar
file keyed by query works today; folding them into the dataset is the same
shape. Capturing what the agent actually emitted is preferable to
hand-annotation, because it isolates retrieval differences from routing
differences.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Retrieval paths, mirroring vss_cli.search_group.SearchGroup.actions.
EMBED = "embed"
ATTRIBUTE = "attribute"
FUSION = "fusion"
OBJECT = "object"


def route(decomposition: dict[str, Any]) -> str:
    """Pick the retrieval path a decomposition implies.

    Derived from the four paths' own input models rather than invented here:

    ============================  ===========  ==========  =========
    attributes                    has_action   object_ids  path
    ============================  ===========  ==========  =========
    --                            --           present     object
    present                       false        --          attribute
    present                       true         --          fusion
    empty                         --           --          embed
    ============================  ===========  ==========  =========

    ``attribute`` is the no-action case because that path cannot express an
    action at all -- it matches detected object properties. ``fusion`` is the
    both case: the embedding leg carries the action, the attribute leg
    re-ranks by appearance.
    """
    if decomposition.get("object_ids"):
        return OBJECT

    attributes = decomposition.get("attributes") or []
    if not attributes:
        return EMBED

    # `has_action` is REQUIRED in the prompt, but a hand-written decomposition
    # may omit it. Absent means "unknown", and fusion is the safer default: it
    # still returns the embedding leg's candidates, whereas `attribute` would
    # silently drop every action-based match.
    if decomposition.get("has_action", True):
        return FUSION
    return ATTRIBUTE


def plan_for(
    query: str,
    decomposition: dict[str, Any] | None,
    default_path: str = EMBED,
    default_attributes: list[str] | None = None,
    default_source_type: str = "video_file",
) -> dict[str, Any]:
    """Build the per-query retrieval plan the CLI backend executes.

    With no decomposition this reproduces the previous fixed-flag behaviour,
    so an un-annotated dataset keeps working exactly as before.
    """
    if not decomposition:
        return {
            "path": default_path,
            "query": query,
            "attributes": list(default_attributes or []),
            "source_type": default_source_type,
            "video_sources": [],
            "timestamp_start": None,
            "timestamp_end": None,
            "object_ids": [],
            "top_k": None,
            "routed": False,
        }

    return {
        "path": route(decomposition),
        # The decomposition rewrites the query ("Find a man pushing a cart
        # wearing a beige shirt between 1pm and 2pm" -> "man pushing cart
        # wearing beige shirt"). Feeding the raw text instead would embed the
        # time range and source name as if they were visual content.
        "query": decomposition.get("query") or query,
        "attributes": list(decomposition.get("attributes") or []),
        "source_type": decomposition.get("source_type") or default_source_type,
        "video_sources": list(decomposition.get("video_sources") or []),
        "timestamp_start": decomposition.get("timestamp_start"),
        "timestamp_end": decomposition.get("timestamp_end"),
        "object_ids": list(decomposition.get("object_ids") or []),
        "top_k": decomposition.get("top_k"),
        "routed": True,
    }


def load_decompositions(path: str | Path) -> dict[str, dict[str, Any]]:
    """Read a sidecar file mapping query text to its decomposition.

    Accepts either shape, so a dataset that grows a ``decomposition`` key per
    query can be passed directly once that lands::

        {"<query>": {"query": ..., "attributes": [...], "has_action": true}}
        {"queries": {"<query>": {"decomposition": {...}}}}
    """
    data = json.loads(Path(path).read_text())
    if "queries" in data and isinstance(data["queries"], dict):
        data = data["queries"]

    decompositions: dict[str, dict[str, Any]] = {}
    for query, value in data.items():
        if not isinstance(value, dict):
            continue
        # A dataset entry nests the decomposition; a sidecar file is already one.
        inner = value.get("decomposition") if "decomposition" in value else value
        if isinstance(inner, dict):
            decompositions[query] = inner
    return decompositions


def unpack_dataset(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Split a dataset into ``(annotations, decompositions)``.

    Decompositions belong with the query they decompose, so the dataset is
    their natural home -- one file, no separate flag, and adding a query adds
    both halves at once.

    Two shapes are accepted, so old datasets keep working unchanged::

        # legacy: the value IS the ground-truth segment list
        {"queries": {"<query>": [ {...}, {...} ]}}

        # extended: segments plus the decomposition
        {"queries": {"<query>": {"segments": [ {...} ],
                                 "decomposition": {"query": ..., "attributes": [...]}}}}

    Returns annotations in the legacy shape either way, so the scoring code
    never learns which form it came from.
    """
    queries = data.get("queries", {}) or {}
    annotations: dict[str, Any] = {}
    decompositions: dict[str, dict[str, Any]] = {}

    for query, value in queries.items():
        if isinstance(value, dict):
            annotations[query] = value.get("segments") or value.get("annotations") or []
            decomposition = value.get("decomposition")
            if isinstance(decomposition, dict):
                decompositions[query] = decomposition
        else:
            # Legacy: a bare list of ground-truth segments.
            annotations[query] = value or []

    return annotations, decompositions


def path_distribution(plans: list[dict[str, Any]]) -> dict[str, int]:
    """Count plans per retrieval path, for the results summary.

    A mixed-path run's headline metric averages over different retrieval
    mechanisms, so the split has to be visible next to it.
    """
    counts: dict[str, int] = {}
    for plan in plans:
        counts[plan["path"]] = counts.get(plan["path"], 0) + 1
    return dict(sorted(counts.items()))
