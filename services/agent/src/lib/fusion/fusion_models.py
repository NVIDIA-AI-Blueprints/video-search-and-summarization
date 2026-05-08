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
"""Pure fusion data contracts."""

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Annotated
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from lib.fusion.ranking_models import DEFAULT_CHUNK_SECONDS
from lib.fusion.ranking_models import ChunkKey
from lib.fusion.ranking_models import EmbeddingSpaceName
from lib.fusion.ranking_models import RankedList

# TODO: remove "rrf_with_attribute_rank" (only for backward compatibility) when generalized fusion is fully adopted
FusionMethod = Literal["rrf", "weighted_linear", "rrf_with_attribute_rank"]
Aggregation = Literal["max", "mean"]
DEFAULT_RRF_K = 60

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
FiniteNonNegFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]

# ---------------------------------------------------------------------------
# Fusion data contract
# ---------------------------------------------------------------------------


class _SharedFusionParams(BaseModel):
    """Knobs shared between request body (:class:`FusionInput`) and deployment config (:class:`FusionConfig`).

    Single source of truth for every field that exists on both models.
    Overridable fields via user request, they get overlayed on top of the deployment config.
    """

    chunk_seconds: int = Field(
        default=DEFAULT_CHUNK_SECONDS,
        gt=0,
        description=(
            "Chunk grid in seconds. Drives snap/dedup and merge gap math. "
            "E.g. =5 -> 00:03 and 00:04 collapse to one bucket."
        ),
    )
    method: FusionMethod = Field(
        default="rrf",
        description=(
            "Fusion math. 'rrf' uses ranks (unit-free, robust). 'weighted_linear' uses min-max normalized raw scores."
        ),
    )
    rrf_k: int = Field(
        default=DEFAULT_RRF_K,
        gt=0,
        description=("RRF damping. Larger k flattens, smaller k amplifies top ranks. 60 is the TREC standard."),
    )

    # Pre-fuse filter
    per_space_min_score: dict[EmbeddingSpaceName, FiniteFloat] = Field(
        default_factory=dict,
        description=(
            "Drop per-space chunks early below a raw-unit threshold. "
            'E.g. {"embed": 0.7} -> cosine < 0.7 chunks dropped.'
        ),
    )

    # Post-fuse filters
    min_contributing_spaces: int = Field(
        default=1,
        ge=0,
        description="Chunk must appear in >=N spaces to survive. E.g. =2 -> at least 2 spaces voted for it.",
    )
    keep_if_top_n_in_any_space: int | None = Field(
        default=None,
        gt=0,
        description=(
            "OR-exemption: top-N in any space bypasses post-fuse gates. E.g. =3 -> rank <=3 anywhere survives."
        ),
    )
    required_spaces: list[EmbeddingSpaceName] = Field(
        default_factory=list,
        description=(
            "Hard gate: every listed space must appear in a chunk's contributing_spaces for it to survive. "
            'e.g. =["embed"] enforces an embed-anchor invariant - any chunk with no embed contribution is dropped, '
            "even if it would qualify via keep_if_top_n_in_any_space (no exemption - required means required). "
            "Empty list (default) disables this gate."
        ),
    )
    min_fused_score_ratio: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description=(
            "Drop chunks below ratio x theoretical ceiling. E.g. 0.3 -> keep only >=30% of the best possible fused_score. "
            "Absolute comparison, independent of what is returned. Therefore, weak queries may return zero segments."
        ),
    )
    # TODO: add a relative min filter similar to "top_percent_filter" where the filter is relative to what is returned, so weak queries can still return some segments

    # Merge knobs (applied after filtering and fusion)
    merge_adjacent: bool = Field(
        default=True,
        description="Collapse touching chunks into one segment. E.g. [0-5]+[5-10] -> [0-10].",
    )
    merge_gap_chunks: int = Field(
        default=0,
        ge=0,
        description="Tolerate up to N missing chunks between merges. E.g. =1 keeps [0-5]+[10-15] as one.",
    )
    segment_score_aggregation: Aggregation = Field(
        default="mean",
        description=(
            "How per-chunk fused scores collapse into one segment score. "
            "'mean' matches legacy search and merge behavior - sustained events outrank single-chunk spikes. "
            "'max' opts in to surfacing peak moments instead."
        ),
    )

    # End of pipeline knobs
    top_k_segments: int | None = Field(
        default=None,
        gt=0,
        description=("Cap the final segments by fused_score. None -> no cap (return every survivor)."),
    )


class FusionInput(_SharedFusionParams):
    """Request body for fusion. Carries only what the math needs.

    Inherits all shared knobs that can be overridden by the user in the request.
    """

    model_config = ConfigDict(frozen=True)

    lists: list[RankedList] = Field(
        default_factory=list,
        description="N per-space ranked lists from upstream search tools, e.g. [embed, attribute, caption]",
    )

    space_weights: dict[EmbeddingSpaceName, FiniteNonNegFloat] = Field(
        default_factory=dict,
        description=(
            "Per-space trust weight used when fusing results. Higher values give "
            "more influence to that space. Optional - omit, pass None, or pass "
            "{} to defer entirely to the config space_weights_default. "
            "Missing per-space keys are also filled from that default."
        ),
    )

    @field_validator("space_weights", mode="before")
    @classmethod
    def _none_to_empty(cls, v: dict | None) -> dict:
        return {} if v is None else v


class FusedSegment(BaseModel):
    """One merged segment in the fusion output.

             INPUT                               OUTPUT
             ─────                               ───────

    RankedChunk.key  ────────►  fuse() ────►  FusedSegment.member_keys[i]
         │                         ▲                       │
         │                         │                       │
         └──────────► ChunkKey ◄───┴───► ChunkKey ◄────────┘
                      (frozen)              (frozen)
                         ▲                     ▲
                         └─── same value ──────┘
                         identity preserved end-to-end
                         -> enables payload re-join in search.py
    """

    sensor_id: str
    start: datetime  # earliest start of merged members
    end: datetime  # latest end of merged members

    # Rank-derived voting score (not a similarity)
    # Gotcha: Unitless and varies by method (e.g. RRF, rrf_k=60, 3 spaces, weights=1, ceiling ≈ 0.0492)
    # Always interpret and compare fused_score as a *ratio* of this ceiling (see ``_theoretical_ceiling``), not as an absolute value.
    # TODO: maybe revisit model to also have a more meaningful ratio in the ranked lists for easy interpretation
    fused_score: float

    # Union across member chunks
    # Reflects the breadth of evidence for this segment (i.e. more contributing spaces means more trustworthy)
    contributing_spaces: list[EmbeddingSpaceName]

    # Original chunk keys that fed this segment
    member_keys: list[ChunkKey]

    # Convenience field (=len(member_keys))
    # How many 5-second chunks were merged into this segment
    member_chunk_count: int


class FusionOutput(BaseModel):
    """Response body. Already filtered, merged, sorted desc by ``fused_score``."""

    segments: list[FusedSegment] = Field(default_factory=list)

    # Theoretical maximum fused_score possible for this query
    # Used for normalizing the fused score to 0-1
    theoretical_max_score: float = 0.0


# ---------------------------------------------------------------------------
# Internal structures
# ---------------------------------------------------------------------------


@dataclass
class FusedRow:
    """Outer-join accumulator used inside :func:`fuse` and the global filter pass."""

    key: ChunkKey
    score: float = 0.0
    contributing_spaces: list[EmbeddingSpaceName] = field(default_factory=list)
    per_space_ranks: dict[EmbeddingSpaceName, int] = field(default_factory=dict)
