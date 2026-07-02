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
"""Shared Elasticsearch filter builders.

Pure, dependency-free helpers used by more than one primitive (embed_search and
attribute_search) to restrict results to a set of ``video_sources``. Keeping
this in one place means the sensor/name matching rules — and their escaping —
stay consistent across primitives.
"""

from __future__ import annotations

from typing import Any

from .uuid_string import is_standard_uuid_string


def escape_wildcard(value: str) -> str:
    """Escape ES wildcard metacharacters (``\\``, ``*``, ``?``) in ``value``."""
    return value.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")


def should_clauses_for_source(name: str) -> list[dict[str, Any]]:
    """Build the ``should`` clauses that match a single non-UUID source name.

    Covers exact id plus ``*name*`` substring wildcards on the id, url, and path
    keyword fields. A blank/whitespace name yields no clauses so it can never
    become a match-all ``**`` filter.

    The former ``regexp`` clauses were dropped: they escaped ``name`` with Python
    ``re.escape`` (which emits ``\\`` sequences that are invalid in Lucene regexp
    syntax and can make Elasticsearch reject the query) and anchored only as a
    suffix (``.*name``), which is asymmetric with the ``*name*`` wildcards. The
    keyword-field substring wildcards already cover the intended matches without
    that Lucene-escaping hazard.
    """
    if not name.strip():
        return []
    escaped = escape_wildcard(name)
    return [
        {"term": {"sensor.id.keyword": name}},
        {"wildcard": {"sensor.id.keyword": f"*{escaped}*"}},
        {"wildcard": {"sensor.info.url.keyword": f"*{escaped}*"}},
        {"wildcard": {"sensor.info.path.keyword": f"*{escaped}*"}},
    ]


def build_video_sources_filter(
    video_sources: list[str] | None,
    source_type: str,
) -> dict[str, Any] | None:
    """Build the ES filter clause restricting results to ``video_sources``.

    For ``rtsp`` every source is treated as a name (UUIDs live in the path, not
    ``sensor.id``); for ``video_file`` UUID sources get a fast ``terms`` clause
    and names fall back to substring-wildcard matching. Blank/whitespace entries
    are skipped so they cannot widen the filter to match everything. Returns None
    when there are no usable sources to filter on.
    """
    if not video_sources:
        return None

    non_blank = [v for v in video_sources if v.strip()]
    if not non_blank:
        return None

    if source_type == "rtsp":
        uuid_sources: list[str] = []
        non_uuid_sources = list(non_blank)
    else:
        uuid_sources = [v for v in non_blank if is_standard_uuid_string(v)]
        non_uuid_sources = [v for v in non_blank if not is_standard_uuid_string(v)]

    if uuid_sources and not non_uuid_sources:
        return {"terms": {"sensor.id.keyword": uuid_sources}}

    should_clauses: list[dict[str, Any]] = []
    if uuid_sources:
        should_clauses.append({"terms": {"sensor.id.keyword": uuid_sources}})
    for vname in non_uuid_sources:
        should_clauses.extend(should_clauses_for_source(vname))
    return {"bool": {"should": should_clauses, "minimum_should_match": 1}}
