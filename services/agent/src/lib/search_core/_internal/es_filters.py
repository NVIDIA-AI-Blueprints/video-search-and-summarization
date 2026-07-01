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

import re
from typing import Any

from .uuid_string import is_standard_uuid_string


def escape_wildcard(value: str) -> str:
    """Escape ES wildcard metacharacters (``\\``, ``*``, ``?``) in ``value``."""
    return value.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")


def should_clauses_for_source(name: str) -> list[dict[str, Any]]:
    """Build the ``should`` clauses that match a single non-UUID source name.

    Covers exact id, wildcard id, url/path wildcards, and url/path regexp. A
    single ``*name*`` clause is used per field; the broad wildcard subsumes any
    suffix-only variant.
    """
    escaped = escape_wildcard(name)
    regex_escaped = re.escape(name)
    return [
        {"term": {"sensor.id.keyword": name}},
        {"wildcard": {"sensor.id.keyword": f"*{escaped}*"}},
        {"wildcard": {"sensor.info.url.keyword": f"*{escaped}*"}},
        {"wildcard": {"sensor.info.path.keyword": f"*{escaped}*"}},
        {"regexp": {"sensor.info.url": f".*{regex_escaped}"}},
        {"regexp": {"sensor.info.path": f".*{regex_escaped}"}},
    ]


def build_video_sources_filter(
    video_sources: list[str] | None,
    source_type: str,
) -> dict[str, Any] | None:
    """Build the ES filter clause restricting results to ``video_sources``.

    For ``rtsp`` every source is treated as a name (UUIDs live in the path, not
    ``sensor.id``); for ``video_file`` UUID sources get a fast ``terms`` clause
    and names fall back to wildcard/regexp matching. Returns None when there are
    no sources to filter on.
    """
    if not video_sources:
        return None

    if source_type == "rtsp":
        uuid_sources: list[str] = []
        non_uuid_sources = list(video_sources)
    else:
        uuid_sources = [v for v in video_sources if is_standard_uuid_string(v)]
        non_uuid_sources = [v for v in video_sources if not is_standard_uuid_string(v)]

    if uuid_sources and not non_uuid_sources:
        return {"terms": {"sensor.id.keyword": uuid_sources}}

    should_clauses: list[dict[str, Any]] = []
    if uuid_sources:
        should_clauses.append({"terms": {"sensor.id.keyword": uuid_sources}})
    for vname in non_uuid_sources:
        should_clauses.extend(should_clauses_for_source(vname))
    return {"bool": {"should": should_clauses, "minimum_should_match": 1}}
