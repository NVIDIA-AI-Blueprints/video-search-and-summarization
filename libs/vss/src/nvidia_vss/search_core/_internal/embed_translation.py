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
"""Translate a params-style string dict into a typed ``EmbedSearchInput``.

The search orchestrator builds embed requests as a string-valued ``params``
dict plus a ``source_type``. This module centralizes parsing of that shape —
JSON-or-CSV ``video_sources``, ``top_k`` coercion, and lenient timestamp
handling — so the conversion stays in one place.
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING
from typing import cast

from pydantic import ValidationError

from ..errors import InvalidInputError
from ..models.embed_search import EmbedSearchInput

if TYPE_CHECKING:
    from ..models.common import SourceType


def params_to_embed_input(
    params: dict[str, str],
    source_type: str,
    *,
    exclude_videos: list[dict[str, str]] | None = None,
) -> EmbedSearchInput:
    """Translate a params string-dict into an :class:`EmbedSearchInput`.

    ``exclude_videos`` is passed through from the caller; it lives outside the
    ``params`` sub-dict.
    """
    p = params or {}

    # video_sources may be a JSON array or a comma-separated string:
    # JSON-parse first, fall back to a CSV split.
    vs_raw = p.get("video_sources", "")
    vs: list[str] = []
    if vs_raw:
        vs_stripped = vs_raw.lstrip()
        if vs_stripped.startswith(("[", '"')):
            try:
                parsed = json.loads(vs_raw)
                if isinstance(parsed, list):
                    # Strip and drop blank entries, matching the CSV branch, so a
                    # stray "" never reaches the ES filter as a match-all clause.
                    vs = [str(v).strip() for v in parsed if str(v).strip()]
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

    # None delegates to the shared runtime default.
    # params values are freeform strings; surface a bad numeric string as a typed
    # InvalidInputError rather than a raw ValueError, matching the lenient handling
    # elsewhere in this translator.
    top_k_str = p.get("top_k", "")
    top_k = _coerce_int(top_k_str, "top_k") if top_k_str else None
    min_cosine_similarity = _coerce_float(p.get("min_cosine_similarity", "0.0"), "min_cosine_similarity")

    # params values are freeform strings; treat malformed timestamps as "no
    # filter" instead of raising, so Pydantic's strict datetime coercion never
    # sees an unparseable string.
    ts_start = _safe_iso(p.get("timestamp_start"))
    ts_end = _safe_iso(p.get("timestamp_end"))

    try:
        return EmbedSearchInput(
            query=p.get("query", "") or "",
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
            min_cosine_similarity=min_cosine_similarity,
            exclude_videos=exclude_videos or [],
        )
    except ValidationError as exc:
        # e.g. top_k="0" coerces to int cleanly but violates the ge=1 field bound.
        raise InvalidInputError(f"Invalid embed-search params: {exc}") from exc


def _coerce_int(value: str, field: str) -> int:
    """Parse ``value`` as an int, re-raising failures as :class:`InvalidInputError`."""
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"{field} must be an integer (got {value!r})") from exc


def _coerce_float(value: str, field: str) -> float:
    """Parse ``value`` as a float, re-raising failures as :class:`InvalidInputError`."""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"{field} must be a number (got {value!r})") from exc


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
