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
"""Shared QueryInput-style params → EmbedSearchInput translator.

Used by two call sites that previously duplicated this logic:
  - ``tools/embed_search.py`` — translates wire-shape ``QueryInput`` from
    HTTP callers
  - ``search_core/primitives/search.py`` — translates the orchestrator-built
    QueryInput-shaped dict that ``execute_core_search`` hands to
    ``embed_search.ainvoke``

The two call sites supply the wrapper-specific extras (``embeddings`` →
``precomputed_embedding`` and ``exclude_videos`` are present on the wire
QueryInput, absent in the orchestrator path); the shared helper handles the
common ``params`` extraction so the JSON-or-CSV video_sources parsing,
top_k coercion, and field extraction stay in lockstep.
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from ..models.embed_search import EmbedSearchInput

if TYPE_CHECKING:
    from ..models.common import SourceType


def params_to_embed_input(
    params: dict[str, str],
    source_type: str,
    *,
    precomputed_embedding: list[float] | None = None,
    exclude_videos: list[dict[str, str]] | None = None,
) -> EmbedSearchInput:
    """Translate a QueryInput.params dict into a library EmbedSearchInput.

    Caller-supplied ``precomputed_embedding`` / ``exclude_videos`` are simply
    forwarded — they originate from the wire ``QueryInput`` envelope, not the
    ``params`` sub-dict. The orchestrator passes None for both.
    """
    p = params or {}

    # video_sources can be a JSON array or comma-separated string. The shape
    # is the wire convention; JSON-parse first, fall back to CSV split.
    vs_raw = p.get("video_sources", "")
    vs: list[str] = []
    if vs_raw:
        vs_stripped = vs_raw.lstrip()
        if vs_stripped.startswith(("[", '"')):
            try:
                parsed = json.loads(vs_raw)
                if isinstance(parsed, list):
                    vs = [str(v) for v in parsed]
                elif isinstance(parsed, str):
                    # A JSON scalar string (e.g. '"cam1"') decodes to a single
                    # name — use it directly. CSV-splitting vs_raw here would
                    # leave the literal quote chars in the value and break the
                    # downstream ES filter.
                    vs = [parsed.strip()] if parsed.strip() else []
                else:
                    vs = [v.strip() for v in vs_raw.split(",") if v.strip()]
            except (json.JSONDecodeError, TypeError):
                vs = [v.strip() for v in vs_raw.split(",") if v.strip()]
        else:
            vs = [v.strip() for v in vs_raw.split(",") if v.strip()]

    # top_k = None when unspecified so the primitive's default_max_results wins.
    top_k_str = p.get("top_k", "")
    top_k = int(top_k_str) if top_k_str else None

    # Wire-shape ``params`` is a freeform string dict — malformed timestamps
    # should not crash the request. The legacy embed_search path treated bad
    # values as "no filter" rather than a 4xx. The shared helper now does the
    # same pre-check so Pydantic's strict datetime coercion (which would raise
    # ValidationError) never sees an unparseable string.
    ts_start = _safe_iso(p.get("timestamp_start"))
    ts_end = _safe_iso(p.get("timestamp_end"))

    return EmbedSearchInput(
        query=p.get("query", "") or "",
        image_url=p.get("image_url") or None,
        video_url=p.get("video_url") or None,
        description=p.get("description") or None,
        # Pydantic narrows the runtime value to ``SourceType``; cast satisfies
        # the static type checker without re-validating here.
        source_type=cast("SourceType", source_type),
        video_sources=vs or None,
        # Pydantic v2 coerces ISO-8601 strings to ``datetime`` automatically.
        # ``cast`` tells mypy we're aware of the runtime coercion.
        timestamp_start=cast("datetime | None", ts_start),
        timestamp_end=cast("datetime | None", ts_end),
        top_k=top_k,
        min_cosine_similarity=float(p.get("min_cosine_similarity", "0.0")),
        exclude_videos=exclude_videos or [],
        precomputed_embedding=precomputed_embedding,
    )


def _safe_iso(value: str | None) -> str | None:
    """Return ``value`` if it parses as ISO 8601, else None.

    Pydantic v2 accepts ISO 8601 strings for ``datetime`` fields, so this only
    needs to reject obviously-malformed inputs. Empty strings also map to None.
    """
    if not value:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return value


def query_input_to_embed_input(query_input: Any) -> EmbedSearchInput:
    """Translate a wire ``QueryInput`` BaseModel instance into EmbedSearchInput.

    Extracts the two wrapper-only fields (precomputed embedding from
    ``embeddings[0].vector``; ``exclude_videos`` straight through) and
    delegates the shared ``params`` extraction to :func:`params_to_embed_input`.
    """
    pre: list[float] | None = None
    embeddings = getattr(query_input, "embeddings", None) or []
    if embeddings:
        first = embeddings[0]
        vec = first.get("vector", []) if isinstance(first, dict) else []
        if isinstance(vec, list) and vec:
            pre = [float(x) for x in vec]

    return params_to_embed_input(
        getattr(query_input, "params", {}) or {},
        getattr(query_input, "source_type", "video_file"),
        precomputed_embedding=pre,
        exclude_videos=getattr(query_input, "exclude_videos", None),
    )
