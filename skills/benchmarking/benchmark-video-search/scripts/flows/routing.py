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
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

#: Retrieval paths, mirroring vss_cli.search_group.SearchGroup.actions.
EMBED = "embed"
ATTRIBUTE = "attribute"
FUSION = "fusion"
OBJECT = "object"


class InvalidDecomposition(ValueError):
    """Supplied decomposition fields violate the retrieval schema."""


def validate_decomposition(value: Any) -> dict[str, Any]:
    """Validate supplied fields without inventing omitted legacy fields.

    Legacy hand-written decompositions may omit has_action (the documented
    fusion default still applies). Present fields must obey the product types.
    """
    if not isinstance(value, dict):
        raise InvalidDecomposition("decomposition must be a JSON object")
    if value.get("_decomposition_error"):
        raise InvalidDecomposition(str(value["_decomposition_error"]))
    for name in ("attributes", "video_sources", "object_ids"):
        if name not in value:
            continue
        items = value[name]
        if not isinstance(items, list):
            raise InvalidDecomposition(f"{name} must be a list")
        for item in items:
            if name == "object_ids" and type(item) is int:
                continue
            if not isinstance(item, str) or not item.strip():
                raise InvalidDecomposition(
                    f"{name} must contain nonempty strings"
                    + (" or integer IDs" if name == "object_ids" else "")
                )
    if "has_action" in value and type(value["has_action"]) is not bool:
        raise InvalidDecomposition("has_action must be a boolean")
    if "query" in value and (
        not isinstance(value["query"], str) or not value["query"].strip()
    ):
        raise InvalidDecomposition("query must be a nonempty string")
    if "source_type" in value and value["source_type"] not in ("video_file", "rtsp"):
        raise InvalidDecomposition("source_type must be video_file or rtsp")
    if (
        "top_k" in value
        and value["top_k"] is not None
        and (type(value["top_k"]) is not int or value["top_k"] < 1)
    ):
        raise InvalidDecomposition("top_k must be a positive integer or null")
    bounds = []
    for name in ("timestamp_start", "timestamp_end"):
        stamp = value.get(name)
        if stamp is None:
            bounds.append(None)
            continue
        if not isinstance(stamp, str):
            raise InvalidDecomposition(f"{name} must be an ISO-8601 string or null")
        try:
            bounds.append(datetime.fromisoformat(stamp.replace("Z", "+00:00")))
        except ValueError as exc:
            raise InvalidDecomposition(f"{name} must be an ISO-8601 timestamp") from exc
    if all(bound is not None for bound in bounds):
        try:
            if bounds[0] >= bounds[1]:
                raise InvalidDecomposition("timestamp_start must precede timestamp_end")
        except TypeError as exc:
            raise InvalidDecomposition(
                "timestamp bounds must use consistent timezones"
            ) from exc
    return value


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
    validate_decomposition(decomposition)
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
    if decomposition is not None:
        validate_decomposition(decomposition)
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
        {"expected_decomposition": {"<query>": {...}}}   # dataset provenance

    The third is a dataset's own answer key. It is kept out of the dataset files
    on purpose -- a stored decomposition would be a second, silently diverging
    opinion about routing -- but it is exactly what a run needs when the LLM is
    unreachable and the alternative is measuring one fixed path.
    """
    path = Path(path)
    decompositions: dict[str, dict[str, Any]] = {}

    def add(key, value, origin):
        if not isinstance(key, str) or not key:
            warnings.warn(
                f"Ignoring decomposition without query identity: {origin}", stacklevel=2
            )
            return
        try:
            validate_decomposition(value)
        except ValueError as exc:
            value = {"_decomposition_error": str(exc)}
            warnings.warn(
                f"Invalid decomposition {key!r} in {origin}: {exc}", stacklevel=2
            )
        if key in decompositions:
            # Never let directory ordering choose between conflicting evidence.
            if decompositions[key] != value:
                value = {
                    "_decomposition_error": "conflicting saved decompositions for the same query identity"
                }
            else:
                return
        decompositions[key] = value

    def collect(data, origin):
        if not isinstance(data, dict):
            warnings.warn(
                f"Ignoring malformed decomposition container: {origin}", stacklevel=2
            )
            return
        if "original_query" in data:
            key = data.get("query_id") or data["original_query"]
            add(key, data.get("parsed"), origin)
            return
        if isinstance(data.get("queries"), list):
            for row in data["queries"]:
                if isinstance(row, dict):
                    add(
                        row.get("query_id") or row.get("query"),
                        row.get("decomposition"),
                        origin,
                    )
            return
        for name in ("queries", "expected_decomposition"):
            if isinstance(data.get(name), dict):
                data = data[name]
                break
        for key, value in data.items():
            inner = (
                value.get("decomposition")
                if isinstance(value, dict) and "decomposition" in value
                else value
            )
            add(key, inner, origin)

    if path.is_dir():
        for file in sorted(path.glob("*.json")):
            try:
                collect(json.loads(file.read_text()), file)
            except (OSError, ValueError) as exc:
                warnings.warn(
                    f"Skipping unreadable decomposition evidence {file}: {exc}",
                    stacklevel=2,
                )
    else:
        collect(json.loads(path.read_text()), path)
    return decompositions


def unpack_dataset(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
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
