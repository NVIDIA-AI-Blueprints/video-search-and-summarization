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

from lib._foundation.errors import BackendUnreachableError
from lib._foundation.errors import LibraryError

SearchError = LibraryError


class ConfigurationError(SearchError):
    """SearchRuntime is missing or malformed.

    Raised by SearchRuntime builders (from_env, from_config_file, from_remote),
    by CriticAgent constructors when vlm_analyzer is required and absent, and by
    VSSSearch facade methods that exercise an unconfigured primitive.

    Never raised by primitive .run() / .stream() bodies — those paths assume
    a fully-built runtime.
    """


class IndexNotFoundError(BackendUnreachableError):
    """A required Elasticsearch index does not exist.

    A specialization of :class:`BackendUnreachableError` so existing
    ``except BackendUnreachableError`` handlers (and the CLI's exit-code-3
    mapping) keep working, while callers that care can distinguish "the backend
    is up but the index is missing — ingest videos first" from a transport
    failure.
    """

    def __init__(self, index: str | list[str], cause: Exception | None = None) -> None:
        shown = ", ".join(index) if isinstance(index, list) else index
        super().__init__(
            "elasticsearch",
            f"Search index '{shown}' does not exist. Please ensure videos have been ingested before searching.",
            cause,
        )
        self.index = index


class InvalidInputError(SearchError):
    """Input model passed Pydantic validation but the values are semantically invalid.

    Examples: timestamp_start > timestamp_end, top_k <= 0 after defaults applied,
    contradictory filters. Distinct from Pydantic's own ValidationError.
    """


class NoResultsError(SearchError):
    """Recoverable: search ran successfully but produced zero results.

    Callers may choose to relax filters and retry. Note: the library does not
    currently emit this — normal "no matches" cases return an empty list inside
    the output model. It is retained as public API for callers/subclasses that
    want to signal the condition explicitly.
    """
