# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Pydantic request/response models for the ReID HTTP API.

Imported by ``main.py`` after ``config`` so :class:`SearchInput` can use
the resolved :data:`config.DIMENSION` in its vector-length validators.
"""

from __future__ import annotations

from typing import Dict, List, Literal

from pydantic import BaseModel, Field

from config import DIMENSION
from tracking_store import TrackedObject


class HealthLiveResponse(BaseModel):
    """Body of ``GET /health/live`` (HTTP 200)."""

    status: Literal["alive"] = Field(
        description="Process liveness indicator. Always ``alive`` when this endpoint returns 200.",
        examples=["alive"],
    )


class HealthReadyResponse(BaseModel):
    """Body of ``GET /health/ready`` when the service is ready (HTTP 200)."""

    status: Literal["ready"] = Field(
        description="Readiness indicator. ``ready`` when every check below passed.",
        examples=["ready"],
    )
    checks: Dict[str, str] = Field(
        description=(
            "Named dependency checks (Milvus collections, child processes, in-process "
            "threads, tracking store, cluster-stats caches). Each value is a short status string."
        ),
        examples=[{"milvus": "ok"}],
    )


class HealthNotReadyPayload(BaseModel):
    """Readiness payload nested under ``detail`` when ``GET /health/ready`` returns 503."""

    status: Literal["not_ready"] = Field(
        description="Readiness indicator. ``not_ready`` when at least one check failed.",
        examples=["not_ready"],
    )
    checks: Dict[str, str] = Field(
        description=("Named dependency checks. Failed entries describe why the probe is not ready."),
        examples=[{"milvus": "collection 'embeddings' missing"}],
    )


class HealthNotReadyResponse(BaseModel):
    """Body of ``GET /health/ready`` when the service is not ready (HTTP 503).

    FastAPI wraps the handler payload in ``detail`` because the handler raises
    ``HTTPException``.
    """

    detail: HealthNotReadyPayload


class EmbeddingInput(BaseModel):
    object_id: str
    vector: List[float]
    sensor_id: str
    frame_id: int


class EmbeddingOutput(BaseModel):
    object_id: str
    entry_id: int
    sensor_id: str
    frame_id: int
    vector: List[float]


class OutlierOutput(BaseModel):
    object_id: str
    outliers: Dict[str, List[int]]  # sensor_id -> [frame_ids]


class InsertResponse(BaseModel):
    success: bool
    message: str
    object_id: str


class SearchInput(BaseModel):
    """Request body for ``POST /search``: a single ReID embedding to query.

    The handler matches ``vector`` against the active embeddings collection and
    returns up to ``top_k`` similar object_ids ranked by L2 distance.
    """

    vector: List[float] = Field(
        description=(
            "ReID embedding vector to search for. Length MUST be equal to the "
            "configured embedding dimension (currently {dim}) "
            "(Can be configured using the DIMENSION environment variable). Floats are "
            "expected to be in the same numeric range as embeddings produced "
            "by the upstream feature extractor (typically L2-normalized)."
        ).format(dim=DIMENSION),
        min_length=DIMENSION,
        max_length=DIMENSION,
    )
    top_k: int = Field(
        default=5,
        description=("Maximum number of distinct object_ids to return. "),
        ge=1,
        le=20,
        examples=[5],
    )
    max_group_count: int = Field(
        default=8,
        description=(
            "The maximum number of vectors to be analyzed per object when formulating the final result. "
            "Used to cap the vector-level processing for each of the top "
            "retrieved objects."
        ),
        ge=1,
        le=50,
        examples=[8],
    )


class SearchResult(BaseModel):
    """Single match returned by ``POST /search``, one per matched object_id."""

    object_id: str = Field(
        description=("Identifier of the matched object. String form of an unsigned " "64-bit integer."),
        min_length=1,
        max_length=20,
        examples=["12345"],
        pattern=r"^[0-9]+$",
    )
    confidence: float = Field(
        description=(
            "Similarity confidence for this object_id, in (0, 1]. "
            "Computed as ``1 / (1 + distance)`` where the "
            "underlying distance is returned in the distance field."
        ),
        examples=[0.87],
    )
    exp_confidence: float = Field(
        description=(
            "Exponentially-weighted variant of ``confidence``, in (0, 1]. "
            "Computed as ``exp(-4 * distance)`` where the "
            "underlying distance is returned in the distance field."
        ),
        examples=[0.81],
    )
    distance: float = Field(
        description=(
            "Weighted mean **squared** L2 distance between the query vector "
            "and the best-matching ``group_count`` stored vectors for this "
            "object_id."
        ),
        ge=0.0,
        examples=[0.42],
    )
    group_count: int = Field(
        description=(
            "Number of stored vectors for this object_id that contributed "
            "to the weighted mean used to compute ``distance``, "
            "``confidence``, and ``exp_confidence``."
        ),
        ge=0,
        examples=[5],
    )
    distance_to_centroid: float = Field(
        description=(
            "**Squared** L2 distance from the query vector to the cached "
            "centroid of this object_id's cluster."
            "\n\n**Sentinel:** ``-1`` means cluster statistics are not yet "
            "available for this object_id. Otherwise the value is ``>= 0``."
        ),
        ge=-1.0,
        examples=[0.55],
        default=-1,
    )
    cluster_variance: float = Field(
        description=(
            "Cached intra-cluster variance for this object_id. Reflects how "
            "tightly the stored embeddings for this object cluster together. "
            "\n\n**Sentinel:** ``-1`` means cluster statistics are not yet "
            "available for this object_id. Otherwise the value is ``>= 0``."
        ),
        ge=-1.0,
        examples=[0.018],
        default=-1,
    )
    cluster_count: int = Field(
        description=(
            "Total number of stored embeddings currently associated with "
            "this object_id. "
            "\n\n**Sentinel:** ``-1`` means cluster statistics are not yet "
            "available for this object_id. Otherwise the value is ``>= 0``."
        ),
        ge=-1,
        examples=[27],
        default=-1,
    )


class ObjectHistoryResponse(BaseModel):
    """Response for object history query"""

    object_id: str
    stream_id: str
    frames: List[TrackedObject]


class CompressedResponse(BaseModel):
    message: str
    total_original: int
    total_compressed: int
    objects_processed: int
    per_object: List[Dict[str, int]]
    entries_processed: int
    entries_remaining: int


__all__ = [
    "HealthLiveResponse",
    "HealthReadyResponse",
    "HealthNotReadyPayload",
    "HealthNotReadyResponse",
    "EmbeddingInput",
    "EmbeddingOutput",
    "OutlierOutput",
    "InsertResponse",
    "SearchInput",
    "SearchResult",
    "ObjectHistoryResponse",
    "CompressedResponse",
]
