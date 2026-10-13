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

"""Which retrieval path a decomposed query takes, and with which arguments.

The agent picks a path from the LLM's decomposition of the query. This module
reproduces that choice so the benchmark can call ``vss search run <path>``
directly: the path is then *known* for every request rather than inferred
afterwards from the agent's logs.

That is a real gain in measurement quality, not just a port. The previous
design read the path out of ``vss-agent`` log lines, which carry no request id,
so attribution only worked at concurrency 1 and every higher load level
inherited a path measured under different conditions.

The five CLI paths and how they are reached
-------------------------------------------
``vss search run`` exposes ``embed``, ``attribute``, ``tag``, ``fusion`` and
``object``. Only four are reachable from a decomposition -- :func:`route`
mirrors the agent and never selects ``tag``, because nothing in the
decomposition contract asks for keyword retrieval over VLM tag documents. It
remains callable by pinning ``--path tag`` explicitly, which is the only way to
benchmark it.
"""

from __future__ import annotations

from typing import Any

#: Named ``event`` in config, run ids and results; the CLI subcommand is
#: ``embed`` (see :data:`CLI_SUBCOMMANDS`).
EVENT = "event"
ATTRIBUTE = "attribute"
TAG = "tag"
FUSION = "fusion"
OBJECT = "object"

#: Every path the CLI accepts, in the order the sweep reports them.
SEARCH_PATHS = (EVENT, ATTRIBUTE, TAG, FUSION, OBJECT)

#: Benchmark path name -> ``vss search run`` subcommand, where they differ.
CLI_SUBCOMMANDS = {EVENT: "embed"}


def cli_subcommand(path: str) -> str:
    return CLI_SUBCOMMANDS.get(path, path)

#: The paths :func:`route` can return. ``tag`` is absent by design.
ROUTABLE_PATHS = (EVENT, ATTRIBUTE, FUSION, OBJECT)


def route(decomposition: dict[str, Any]) -> str:
    """The path the agent would take for this decomposition.

    Four rules, in this order, because they are not independent:

    1. ``object_ids`` -- an identity lookup, which ignores the query text
       entirely, so it wins over anything else present.
    2. no ``attributes`` -- nothing to filter on, so semantic similarity.
    3. ``attributes`` and ``has_action`` -- the action has to come from the
       embedding leg and the attributes from the detection leg, so both.
    4. ``attributes`` without an action -- a pure detection filter.

    ``has_action`` defaults to true when the key is absent: the decomposition
    prompt always emits it, and treating a missing one as false would quietly
    move a query from fusion to attribute and change what is being measured.
    """
    if decomposition.get("object_ids"):
        return OBJECT
    attributes = decomposition.get("attributes") or []
    if not attributes:
        return EVENT
    if decomposition.get("has_action", True):
        return FUSION
    return ATTRIBUTE


def plan_for(
    query: str,
    decomposition: dict[str, Any],
    default_source_type: str = "video_file",
) -> dict[str, Any]:
    """Everything one request needs: the path and the arguments for it.

    No ``top_k``: it is a sweep axis, and :func:`build_argv` takes it from the
    run rather than from anything the decomposition emitted.

    Raises ``ValueError`` when the LLM returned object ids that are not integers.
    """
    attributes = [str(a) for a in (decomposition.get("attributes") or []) if str(a)]
    raw_ids = decomposition.get("object_ids") or []
    try:
        object_ids = [int(o) for o in raw_ids]
    except (TypeError, ValueError):
        raise ValueError(f"decomposition returned non-integer object_ids {raw_ids!r}") from None
    path = route(decomposition)

    return {
        "path": path,
        # The decomposition rewrites the query for the embedding leg; the
        # original is sent separately, for the critic.
        "query": str(decomposition.get("query") or query),
        "attributes": attributes,
        "object_ids": object_ids,
        "source_type": str(decomposition.get("source_type") or default_source_type),
        "video_sources": [
            str(s) for s in (decomposition.get("video_sources") or []) if str(s)
        ],
        "timestamp_start": str(decomposition.get("timestamp_start") or ""),
        "timestamp_end": str(decomposition.get("timestamp_end") or ""),
    }


def pinned_plan(
    query: str,
    path: str,
    default_source_type: str = "video_file",
) -> dict[str, Any]:
    """A plan for a path chosen by the operator rather than by the LLM.

    Used by ``--path``, and the only way to reach ``tag``. The attribute and
    object paths cannot be synthesised this way -- there is nothing to put in
    ``--attribute`` or ``--object-id`` without a decomposition -- so those
    still need one, and the caller is expected to have run it.
    """
    if path not in SEARCH_PATHS:
        raise ValueError(
            f"unknown search path {path!r}; expected one of {', '.join(SEARCH_PATHS)}"
        )
    return {
        "path": path,
        "query": query,
        "attributes": [],
        "object_ids": [],
        "source_type": default_source_type,
        "video_sources": [],
        "timestamp_start": "",
        "timestamp_end": "",
    }


def path_distribution(plans: dict[str, dict[str, Any]]) -> dict[str, int]:
    """How many queries landed on each path. Zero-valued paths are kept.

    Keeping the zeros is the point: a sweep whose ``object`` count is 0 has no
    object-path measurement at all, and a dict that simply omits the key reads
    as if the question was never asked.
    """
    counts = dict.fromkeys(SEARCH_PATHS, 0)
    for plan in plans.values():
        path = str(plan.get("path") or "")
        if path in counts:
            counts[path] += 1
    return counts
