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
"""Tests for shared ranking data models and utilities."""

from datetime import UTC
from datetime import datetime
from datetime import timedelta
import math
from zoneinfo import ZoneInfo

from pydantic import ValidationError
import pytest

from vss_agents.data_models.ranking import ChunkKey
from vss_agents.data_models.ranking import RankedChunk
from vss_agents.data_models.ranking import snap


def _ts(seconds: int) -> datetime:
    """Build a UTC datetime offset by ``seconds`` from a fixed epoch.

    Mirrors the helper in test_fusion.py for cross-test consistency.
    """
    return datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# snap()
# ---------------------------------------------------------------------------


class TestSnap:
    """Snap an arbitrary timestamp down to the chunk-grid floor."""

    def test_snaps_below_chunk_boundary(self):
        # 00:01:27.500 with chunk=5 -> 00:01:25
        ts = _ts(85) + timedelta(seconds=2.5)
        assert snap(ts, 5) == _ts(85)

    def test_already_snapped_is_noop(self):
        assert snap(_ts(90), 5) == _ts(90)

    def test_preserves_tzinfo_for_aware_input(self):
        # Non-UTC tz-aware input -> tzinfo preserved on the snapped output.
        paris = ZoneInfo("Europe/Paris")
        ts = datetime(2025, 1, 1, 0, 1, 27, 500_000, tzinfo=paris)
        snapped = snap(ts, 5)
        assert snapped.tzinfo is paris
        assert snapped == datetime(2025, 1, 1, 0, 1, 25, tzinfo=paris)

    def test_naive_datetime_rejected(self):
        # Tightened contract: naive datetime in -> ValueError out.
        naive = datetime(2025, 1, 1, 0, 1, 27, 500_000)
        with pytest.raises(ValueError):
            snap(naive, 5)

    def test_negative_offsets_floor_correctly(self):
        # 1969 (pre-epoch) edge: start = -10s, chunk=5 -> -10s (already on grid).
        ts = datetime(1969, 12, 31, 23, 59, 50, tzinfo=UTC)
        assert snap(ts, 5) == ts


# ---------------------------------------------------------------------------
# Other models
# ---------------------------------------------------------------------------


class TestRankedChunkContract:
    """Lock in important contracts for :class:`RankedChunk`."""

    @pytest.mark.parametrize("bad_score", [math.nan, math.inf, -math.inf])
    def test_non_finite_score_rejected(self, bad_score):
        """Loud failure: NaN/Inf score in -> ValidationError out.

        Reject at the model boundary instead so every downstream call site can trust the input.
        """
        with pytest.raises(ValidationError):
            RankedChunk(
                key=ChunkKey(sensor_id="s", start=_ts(0)),
                score=bad_score,
                rank=1,
            )


class TestChunkKeyTimezoneContract:
    """Pin the tz-awareness contract on ChunkKey.start"""

    def test_naive_datetime_rejected_at_construction(self):
        """Loud failure: naive datetime in -> ValidationError out."""
        naive = datetime(2025, 1, 1, 12, 0, 0)
        assert naive.tzinfo is None  # sanity: this really is naive

        with pytest.raises(ValidationError):
            ChunkKey(sensor_id="cam-1", start=naive)

    def test_same_moment_two_timezones_produce_identical_keys(self):
        """Two ChunkKeys built from the same wall moment in different tz must
        be ``==`` AND hash-equal (so they collide in fuse()'s dict join)."""
        ts_utc = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        ts_paris = datetime(2025, 1, 1, 13, 0, 0, tzinfo=ZoneInfo("Europe/Paris"))

        key_utc = ChunkKey(sensor_id="cam-1", start=ts_utc)
        key_paris = ChunkKey(sensor_id="cam-1", start=ts_paris)

        assert key_utc == key_paris
        assert hash(key_utc) == hash(key_paris)
