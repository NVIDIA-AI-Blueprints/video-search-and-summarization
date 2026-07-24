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
"""Search-specific errors layered on the neutral library foundation.

Primitives never raise framework exceptions (httpx.HTTPError, elasticsearch.ApiError);
the client classes catch and re-raise as BackendUnreachableError with the underlying
exception chained via __cause__. One error surface, one catch-block for callers.
"""

from __future__ import annotations

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError
from vss_core._foundation.errors import LibraryError

__all__ = [
    "BackendUnreachableError",
    "ConfigurationError",
    "IndexNotFoundError",
    "InvalidInputError",
    "NoFinalResultError",
    "SearchError",
]


# Backward-compatible public catch-all.  Keeping this as an alias preserves the
# ``except SearchError`` contract without creating a second error hierarchy
# beside the foundation types re-exported above.
SearchError = LibraryError


class IndexNotFoundError(BackendUnreachableError):
    """A required Elasticsearch index does not exist.

    A specialization of :class:`BackendUnreachableError` so existing
    ``except BackendUnreachableError`` handlers (and the CLI's exit-code-3
    mapping) keep working, while callers that care can distinguish "the backend
    is up but the index is missing — ingest videos first" from a transport
    failure.
    """

    def __init__(
        self,
        index: str | list[str],
        cause: Exception | None = None,
        *,
        available_indices: list[str] | None = None,
    ) -> None:
        shown = ", ".join(index) if isinstance(index, list) else index
        detail = "Please ensure videos have been ingested before searching."
        if available_indices:
            detail += " Available MDX embed indexes: " + ", ".join(available_indices)
        super().__init__(
            "elasticsearch",
            f"Search index '{shown}' does not exist. {detail}",
            cause,
        )
        self.index = index
        self.available_indices = tuple(available_indices or ())


class InvalidInputError(SearchError):
    """Input model passed Pydantic validation but the values are semantically invalid.

    Examples: timestamp_start > timestamp_end, top_k <= 0 after defaults applied,
    contradictory filters. Distinct from Pydantic's own ValidationError.
    """


class NoFinalResultError(SearchError):
    """The internal search generator violated its terminal-result contract."""
