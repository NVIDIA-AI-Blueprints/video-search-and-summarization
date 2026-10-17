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
"""Streaming event protocol for Search.stream().

A typed v1 contract: callers consume ``SearchEvent`` instances instead of
loosely-typed streaming chunks. Adapters that bridge to an older chunk-based
streaming path translate those chunks into these events at the boundary.

Guarantees for Search.stream():
  - Exactly one FinalResultEvent OR exactly one ErrorEvent terminates the stream.
  - Never both. Never neither.
  - StatusEvent and PartialResultEvent may appear zero or more times before
    the terminator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict

if TYPE_CHECKING:
    from .models.search import SearchOutput
    from .models.search import SearchResult


class StatusEvent(BaseModel):
    """Lifecycle progress signal for a search stage."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["status"] = "status"
    stage: str  # e.g. "embed_search" or "attribute_search"
    message: str


class PartialResultEvent(BaseModel):
    """A batch of results emitted before the final ranking is complete."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["partial"] = "partial"
    results: list[SearchResult]


class FinalResultEvent(BaseModel):
    """Stream terminator on success."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["final"] = "final"
    output: SearchOutput


class ErrorEvent(BaseModel):
    """Stream terminator on failure.

    ``error_code`` is ``type(exc).__name__`` for the raised ``LibraryError``
    subclass (e.g. ``BackendUnreachableError``, ``IndexNotFoundError``,
    ``ConfigurationError``, ``InvalidInputError``), or one of the stream's own
    sentinels — ``"UnexpectedError"`` for a non-``LibraryError`` escape and
    ``"NoFinalResult"`` when the stream ends without a terminator. ``message`` is
    the human-readable text from ``str(exc)``.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["error"] = "error"
    error_code: str
    message: str


SearchEvent = StatusEvent | PartialResultEvent | FinalResultEvent | ErrorEvent


def _rebuild_event_models() -> None:
    """Resolve Pydantic forward refs used by streaming JSON serialization."""
    from .models.search import SearchOutput
    from .models.search import SearchResult

    namespace = {"SearchOutput": SearchOutput, "SearchResult": SearchResult}
    PartialResultEvent.model_rebuild(_types_namespace=namespace)
    FinalResultEvent.model_rebuild(_types_namespace=namespace)


_rebuild_event_models()
