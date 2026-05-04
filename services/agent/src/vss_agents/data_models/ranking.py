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
"""Shared data contract for ranked-list fusion.

Pure data-model module. Consumed by fusion tool and search embedding space tools adapters.
"""

from datetime import UTC
from datetime import datetime
from datetime import timedelta

from pydantic import AwareDatetime
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

# Single source of truth for the chunk grid. Keep all grid-aware code aligned
# to this one value (fusion, search embedding space tools adapters, etc.)
DEFAULT_CHUNK_SECONDS: int = 5


class ChunkKey(BaseModel):
    """Unique identifier for a video chunk on the snapped grid.

    Note: End of the chunk is intentionally not a field of the key. End is fully derived
    as ``start + chunk_seconds`` post-bucketize.
    """

    # Makes model hashable so it can be used as a ``dict`` key during the outer-join
    model_config = ConfigDict(frozen=True)

    sensor_id: str
    start: AwareDatetime = Field(
        description=(
            "Raw upstream timestamp; bucketize snaps to chunk_seconds grid. "
            "Must be tz-aware (naive rejected; non-UTC coerced to UTC)."
        ),
    )

    @field_validator("start")
    @classmethod
    def _coerce_to_utc(cls, v: datetime) -> datetime:
        """Normalize any tz-aware datetime to UTC.

        Note: ``AwareDatetime`` has already rejected naive non-tz inputs at this point.
        """
        return v.astimezone(UTC)


class RankedChunk(BaseModel):
    """One ranked entry from a single embedding space.

    Pure data model. Fusion is blind to payloads.
    Hence no VST URLs, screenshots, descriptions, object_ids - those live on
    the original search-tool outputs and are recombined post-fusion via the
    payload sidecar in ``search.py``.
    """

    model_config = ConfigDict(frozen=True)

    key: ChunkKey

    # Raw score in space-native units (cosine, frame_score, ...)
    score: float = Field(allow_inf_nan=False)

    # 1-based position inside its source list
    # Note: Fusion does not compute initial ranks - it receives them
    # (the ranks come baked into the input from upstream search tools)
    # e.g. each embedding space tool -> emits ``RankedList(rank=1..K)``
    rank: int


class RankedList(BaseModel):
    """A ranked list of chunks from one embedding space."""

    model_config = ConfigDict(frozen=True)

    # Kept dynamic (not a literal) for ease of extensibility when consumers add new spaces
    # "embed", "attribute", "caption", "face", ...
    space: str
    chunks: list[RankedChunk] = Field(default_factory=list)


def _validate_chunk_seconds(chunk_seconds: int) -> None:
    """Invariant for callers that bypass the Pydantic boundary."""
    if chunk_seconds <= 0:
        raise ValueError(f"chunk_seconds must be > 0, got {chunk_seconds!r}")


def snap(ts: AwareDatetime, chunk_seconds: int = DEFAULT_CHUNK_SECONDS) -> datetime:
    """Snap an arbitrary timestamp down to the chunk-grid floor.

    Different search tools may return chunks at slightly different timestamps
    even when describing the same moment of video. Snapping lines them up onto a deterministic grid
    so the outer-join across spaces (consumed by the fusion tool) matches the same chunk.

    Example:
    - `embed_search` (Cosmos clip embeddings) -> 00:01:25.300 (sliding window started)
    - `attribute_search` (CV per-frame) -> 00:01:27.100 (bounding box landed)
    -> both snap to 00:01:25
    """
    _validate_chunk_seconds(chunk_seconds)
    if ts.tzinfo is None:
        raise ValueError(f"snap requires a tz-aware datetime, got naive {ts!r}")
    epoch = datetime(1970, 1, 1, tzinfo=ts.tzinfo)
    seconds_since_epoch = (ts - epoch).total_seconds()
    snapped = (seconds_since_epoch // chunk_seconds) * chunk_seconds
    return epoch + timedelta(seconds=snapped)


__all__ = [
    "DEFAULT_CHUNK_SECONDS",
    "ChunkKey",
    "RankedChunk",
    "RankedList",
    "snap",
]
