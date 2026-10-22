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
import math
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictInt
from pydantic import model_serializer
from pydantic import model_validator

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
    candidate_limit: int | None = Field(
        default=None,
        ge=1,
        le=100000,
        description="Opt-in exact indexed scan budget; exceeding it fails instead of truncating.",
    )
    top_k: int | None = Field(default=None, ge=1, le=100000)
    search_mode: Literal["embed", "attribute", "fusion", "object", "tag"] = "embed"
    attribute_min_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    attributes: list[str] = Field(default_factory=list)
    object_ids: list[int] | None = None
    # Cosine similarity is in [-1, 1]; the UI sends negative thresholds for
    # low-confidence searches, so don't clamp the lower bound to 0.
    min_cosine_similarity: float = Field(default=0.0, ge=-1.0, le=1.0)

    @model_validator(mode="after")
    def validate_candidate_budget(self):
        if self.top_k is not None and self.top_k > 1000 and self.candidate_limit is None:
            raise ValueError("top_k above 1000 requires explicit candidate_limit")
        return self

    def validate_semantics(self) -> None:
        """Raise :class:`InvalidInputError` for cross-field problems.

        These values each pass their own field constraints but are invalid in
        combination; centralizing them keeps the primitive's ``run()``/``stream()``
        thin and gives callers one place to exercise input semantics.
        """
        if self.search_mode in {"embed", "fusion", "tag"} and not self.query.strip():
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
        if self.video_sources and not all(source.strip() for source in self.video_sources):
            raise InvalidInputError("video_sources must contain only non-empty source names or IDs")
        if self.search_mode == "attribute" and not has_attributes:
            raise InvalidInputError("search_mode='attribute' requires at least one attribute")
        if self.search_mode in {"embed", "tag"} and has_attributes:
            raise InvalidInputError("attributes require search_mode='attribute' or search_mode='fusion'")
        if self.search_mode == "object" and not self.object_ids:
            raise InvalidInputError("search_mode='object' requires at least one object_id")
        if self.search_mode == "object" and has_attributes:
            raise InvalidInputError("search_mode='object' does not accept attributes")
        if self.object_ids and self.search_mode != "object":
            raise InvalidInputError("object_ids require search_mode='object'")


class SearchVerification(BaseModel):
    """Critic verdict attached to one retrieval hit the critic evaluated.

    Retrieval is useful even when no VLM is deployed or verification fails.
    Consequently a hit carries no verdict until the critic evaluates that exact
    interval; a hit without one is unverified. A per-hit media or VLM failure
    is still an evaluated ``unverified`` verdict.
    """

    model_config = ConfigDict(extra="forbid")
    result: Literal["confirmed", "rejected", "unverified"]
    criteria_met: dict[str, bool] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    """A single search result item."""

    model_config = ConfigDict(extra="forbid")
    video_name: str
    description: str
    start_time: str
    end_time: str
    sensor_id: str
    source_id: str | None = None
    screenshot_url: str
    similarity: float
    object_ids: list[str] = Field(default_factory=list)
    # Matches the VSS agent's field, which the UI Search tab reads; None until
    # the critic evaluates this hit.
    attribute_evidence: list[dict] = Field(default_factory=list)
    critic_result: SearchVerification | None = None
    # Indexed sensor identity (the behavior document's sensor.id) carried from
    # the embed adapter for fusion's per-hit attribute lookup when VST is absent.
    # Internal routing hint: excluded from serialization so client-facing result
    # payloads (and exact-equality E2E assertions) are unchanged.
    sensor_id_raw: str = Field(default="", exclude=True)

    @model_serializer(mode="wrap")
    def serialize_source_identity(self, handler):
        payload = handler(self)
        if self.source_id is None:
            payload.pop("source_id", None)
        return payload


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


class SourceSelection(BaseModel):
    """Selected positions in an unchanged, complete attribute ranking."""

    model_config = ConfigDict(extra="forbid")
    original_pool: list[SearchResult]
    selected_indices: list[StrictInt]
    policy_version: Literal["source-cap-v1"] = "source-cap-v1"
    max_hits_per_source: StrictInt = Field(ge=1, le=100000)
    requested_top_k: StrictInt = Field(ge=1, le=100000)
    candidate_limit: StrictInt = Field(ge=1, le=100000)
    counts: dict[str, StrictInt]
    stages: list[dict[str, Any]]
    completeness: dict[str, Any]

    @model_validator(mode="after")
    def validate_selection(self):
        if self.selected_indices != sorted(set(self.selected_indices)) or any(
            index < 0 or index >= len(self.original_pool) for index in self.selected_indices
        ):
            raise ValueError("selected_indices must be unique, increasing positions in original_pool")
        if len(self.selected_indices) > self.requested_top_k or len(self.original_pool) > self.candidate_limit:
            raise ValueError("source selection exceeds its requested bounds")
        if self.completeness.get("complete") is not True:
            raise ValueError("source selection requires complete ranking evidence")
        from .._internal.source_selection import selection_positions

        expected_indices, expected_counts = selection_positions(
            self.original_pool, cap=self.max_hits_per_source, requested_top_k=self.requested_top_k
        )
        if self.selected_indices != expected_indices or self.counts != expected_counts:
            raise ValueError("source selection positions or counts do not match the declared policy")
        if any(not math.isfinite(row.similarity) for row in self.original_pool):
            raise ValueError("source selection requires finite ranking scores")
        scans = self.completeness.get("scans")
        expected_scans = self.completeness.get("expected_scans")
        if (
            not isinstance(scans, list)
            or not scans
            or type(expected_scans) is not int
            or any(not isinstance(scan, dict) for scan in scans)
            or len(scans) != expected_scans
        ):
            raise ValueError("source selection lacks scan evidence for every attribute")
        absent = self.completeness.get("absent_uploads_anchor") is True
        if absent:
            from ..runtime import BEHAVIOR_INDEX_ANCHOR

            if (
                self.original_pool
                or self.completeness.get("snapshot_verified") is not False
                or any(
                    scan.get("absent_uploads_anchor") is not True
                    or scan.get("index") != BEHAVIOR_INDEX_ANCHOR
                    or scan.get("eligible_documents") != 0
                    for scan in scans
                )
            ):
                raise ValueError("source selection has invalid absent-uploads evidence")
        else:
            if self.completeness.get("snapshot_verified") is not True:
                raise ValueError("source selection requires verified indexed snapshots")
            identities = set()
            for scan in scans:
                if (
                    scan.get("exhaustive") is not True
                    or scan.get("snapshot_verified") is not True
                    or type(scan.get("budget")) is not int
                    or scan.get("budget") != self.candidate_limit
                    or type(scan.get("eligible_documents")) is not int
                    or not 0 <= scan["eligible_documents"] <= self.candidate_limit
                    or type(scan.get("scored_documents")) is not int
                    or scan.get("scored_documents") != scan["eligible_documents"]
                    or not isinstance(scan.get("snapshot_digest"), str)
                    or len(scan["snapshot_digest"]) != 64
                    or any(character not in "0123456789abcdef" for character in scan["snapshot_digest"])
                    or not isinstance(scan.get("snapshot_scope_digest"), str)
                    or len(scan["snapshot_scope_digest"]) != 64
                    or any(character not in "0123456789abcdef" for character in scan["snapshot_scope_digest"])
                    or not scan.get("snapshot_indices")
                    or scan.get("absent_uploads_anchor")
                ):
                    raise ValueError("source selection scan evidence is incomplete")
                indices = scan["snapshot_indices"]
                if (
                    not isinstance(indices, list)
                    or any(
                        not isinstance(index, dict)
                        or not isinstance(index.get("name"), str)
                        or not index["name"]
                        or not isinstance(index.get("uuid"), str)
                        or not index["uuid"]
                        for index in indices
                    )
                    or len({index["name"] for index in indices}) != len(indices)
                ):
                    raise ValueError("source selection lacks concrete index identities")
                identities.add(
                    (
                        scan["snapshot_digest"],
                        scan["snapshot_scope_digest"],
                        tuple(sorted((index["name"], index["uuid"]) for index in indices)),
                    )
                )
            if len(identities) != 1:
                raise ValueError("source selection combines inconsistent attribute snapshots")
        for stage in self.stages:
            before, after = stage.get("before"), stage.get("after")
            if (
                not isinstance(stage.get("stage"), str)
                or not stage["stage"]
                or stage.get("kind") not in {"truncation", "conversion", "filter", "aggregation"}
                or type(stage.get("removed")) is not int
                or type(before) is not int
                or type(after) is not int
                or not 0 <= after <= before
                or stage.get("removed") != before - after
                or (stage.get("kind") in {"truncation", "conversion"} and after != before)
            ):
                raise ValueError("source selection stage evidence reports unexpected candidate loss")
        return self


class SearchOutput(BaseModel):
    """Output envelope from Search.run()."""

    model_config = ConfigDict(extra="forbid")
    data: list[SearchResult] = Field(default_factory=list)
    search_messages: list[str] = Field(default_factory=list)
    candidate_coverage: list[dict] = Field(default_factory=list)
    source_selection: SourceSelection | None = None
    frame_refinement: list[dict] = Field(default_factory=list)
    #: Optional so every existing producer and consumer is unaffected: absent
    #: means nobody collected, not "the search took no time".
    timings: SearchTimings | None = None

    @model_validator(mode="after")
    def validate_source_selected_data(self):
        if self.source_selection is not None:
            selected = self.source_selection
            expected = [selected.original_pool[index] for index in selected.selected_indices]
            if len(self.data) != len(expected) or any(
                actual.model_dump(exclude={"critic_result"}) != original.model_dump(exclude={"critic_result"})
                for actual, original in zip(self.data, expected, strict=True)
            ):
                raise ValueError("search data does not match source-selected pool positions")
        return self

    @model_serializer(mode="wrap")
    def serialize_selection(self, handler):
        payload = handler(self)
        if self.source_selection is None:
            payload.pop("source_selection", None)
        return payload

    @property
    def results(self) -> list[SearchResult]:
        """Unified envelope accessor retained alongside the serialized ``data`` key."""
        return self.data
