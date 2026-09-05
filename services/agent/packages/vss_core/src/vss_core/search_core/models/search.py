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
"""Search-orchestrator input/output models.

``SearchInput`` is the user-facing request; ``SearchOutput`` wraps the ranked
``SearchResult`` items. ``use_attribute_search`` is deliberately absent — it is an
orchestrator config-time flag, not a user-routable field on the request.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  Pydantic field annotation; resolved at runtime
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from ..errors import InvalidInputError

# ``SourceType`` and ``datetime`` are used in Pydantic field annotations
# below; Pydantic v2 resolves the stringified annotations at model_build
# time and needs them importable at runtime.
from .common import SourceType  # noqa: TC001  Pydantic-resolved at runtime


class SearchInput(BaseModel):
    """User-facing input for the Search orchestrator."""

    model_config = ConfigDict(extra="forbid")

    # Only the modes that embed text need a query. Attribute- and object-mode
    # searches match structured evidence (attribute strings, tracked object
    # ids) and never read this field, so requiring it forced callers to invent
    # one. ``validate_semantics`` still requires it where it is used.
    query: str = ""
    original_query: str | None = None
    source_type: SourceType = "video_file"
    video_sources: list[str] | None = None
    description: str | None = None
    timestamp_start: datetime | None = None
    timestamp_end: datetime | None = None
    # None means use the primitive's configured default; all standalone
    # primitives receive SearchRuntime.default_max_results.
    top_k: int | None = Field(default=None, ge=1, le=1000)
    search_mode: Literal["embed", "attribute", "fusion", "object"] = "embed"
    attributes: list[str] = Field(default_factory=list)
    object_ids: list[int] | None = None
    # Cosine similarity is in [-1, 1]; the UI sends negative thresholds for
    # low-confidence searches, so don't clamp the lower bound to 0.
    min_cosine_similarity: float = Field(default=0.0, ge=-1.0, le=1.0)

    def validate_semantics(self) -> None:
        """Raise :class:`InvalidInputError` for cross-field problems.

        These values each pass their own field constraints but are invalid in
        combination; centralizing them keeps the primitive's ``run()``/``stream()``
        thin and gives callers one place to exercise input semantics.
        """
        if self.search_mode in {"embed", "fusion"} and not self.query.strip():
            raise InvalidInputError(f"SearchInput.query must be non-empty for search_mode={self.search_mode!r}")
        if self.timestamp_start and self.timestamp_end and self.timestamp_start > self.timestamp_end:
            raise InvalidInputError(
                f"timestamp_start ({self.timestamp_start.isoformat()}) must not be after "
                f"timestamp_end ({self.timestamp_end.isoformat()})"
            )
        # Defensive: the top_k field constraint (ge=1) already rejects < 1 at
        # construction, so this branch is unreachable for a validated model. Kept
        # so the semantic guarantee survives any future loosening of the field.
        if self.top_k is not None and self.top_k < 1:
            raise InvalidInputError(f"top_k must be >= 1 when provided (got {self.top_k})")
        has_attributes = any(attribute.strip() for attribute in self.attributes)
        if self.search_mode in {"attribute", "fusion"} and not has_attributes:
            raise InvalidInputError(f"search_mode={self.search_mode!r} requires at least one attribute")
        if self.search_mode == "embed" and has_attributes:
            raise InvalidInputError("attributes require search_mode='attribute' or search_mode='fusion'")
        if self.search_mode == "object" and not self.object_ids:
            raise InvalidInputError("search_mode='object' requires at least one object_id")
        if self.search_mode == "object" and has_attributes:
            raise InvalidInputError("search_mode='object' does not accept attributes")
        if self.object_ids and self.search_mode != "object":
            raise InvalidInputError("object_ids require search_mode='object'")


class SearchVerification(BaseModel):
    """Visual verification attached to one retrieval hit.

    Retrieval is useful even when no VLM is deployed or verification fails.
    Consequently every hit starts as ``unverified`` and is upgraded only after
    the critic successfully evaluates that exact interval.
    """

    model_config = ConfigDict(extra="forbid")
    result: Literal["confirmed", "rejected", "unverified"] = "unverified"
    criteria_met: dict[str, bool] | None = None


class SearchResult(BaseModel):
    """A single search result item."""

    model_config = ConfigDict(extra="forbid")
    video_name: str
    description: str
    start_time: str
    end_time: str
    sensor_id: str
    screenshot_url: str
    similarity: float
    object_ids: list[str] = Field(default_factory=list)
    verification: SearchVerification = Field(default_factory=SearchVerification)


class SearchTimings(BaseModel):
    """Per-stage wall-clock for one search, in seconds.

    The stages are the ``TimeMeasure`` blocks already present in the retrieval
    primitives -- embedding generation, ES query build and execution, hit
    processing, attribute lookups, fusion rerank. They were previously only
    logged at a custom PERF level, which the CLI never configured a handler
    for, so callers had no way to see where a search spent its time.

    ``stages`` keys are the measured block labels, which are namespaced by
    convention ("embed_search: ES search execution"). Each maps to:

    * ``total_s`` -- inclusive: the block and everything measured inside it.
    * ``self_s`` -- exclusive: nested measured blocks subtracted. A "where did
      the time go" table wants this; ``total_s`` alone double counts a parent
      against its children. Sums to real elapsed work only where blocks did
      not overlap.
    * ``calls`` -- entries, since some blocks run per hit rather than per search.
    * ``concurrent_children`` -- 1.0 when nested blocks overlapped (they ran
      under ``asyncio.gather``, as fusion's two legs do). Self time is not
      meaningful for such a block and is reported as 0.0 rather than negative.
    """

    model_config = ConfigDict(extra="forbid")
    stages: dict[str, dict[str, float]] = Field(default_factory=dict)
    total_s: float = 0.0


class SearchOutput(BaseModel):
    """Output envelope from Search.run()."""

    model_config = ConfigDict(extra="forbid")
    data: list[SearchResult] = Field(default_factory=list)
    search_messages: list[str] = Field(default_factory=list)
    #: Optional so every existing producer and consumer is unaffected: absent
    #: means nobody collected, not "the search took no time".
    timings: SearchTimings | None = None

    @property
    def results(self) -> list[SearchResult]:
        """Unified envelope accessor retained alongside the serialized ``data`` key."""
        return self.data
