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

import pytest
from datetime import datetime, timedelta, timezone

from mdx.analytics.core.stream.state.behavior.behavior_holdback import BehaviorHoldback
from mdx.analytics.core.schema.models import Behavior, Coordinate, Object, Place, Sensor


class TestBehaviorHoldback:
    """
    Tests for BehaviorHoldback in isolation from state management.

    The holdback decides nothing about when a track ends; it only holds the latest behavior per track
    and releases it on request. These tests pin that container behaviour directly, so the state
    management tests can focus on the timing rules.
    """

    @pytest.fixture
    def holdback(self):
        return BehaviorHoldback()

    def _behavior(self, behavior_id: str, seconds: int = 0) -> Behavior:
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        return Behavior(
            id=behavior_id,
            timestamp=base,
            end=base + timedelta(seconds=seconds),
            timeInterval=float(seconds),
            place=Place(id="p1", name="place"),
            sensor=Sensor(id=behavior_id.split(" #-# ")[0], type="camera"),
            object=Object(
                id=behavior_id.split(" #-# ")[-1], type="vehicle", confidence=0.9,
                coordinate=Coordinate(x=0.0, y=0.0),
            ),
        )

    def test_starts_empty(self, holdback):
        """A new holdback holds nothing and releases nothing."""
        assert holdback.pending == {}
        assert holdback.take_ended() == []
        assert holdback.flush() == []

    def test_retain_keeps_only_the_latest_per_track(self, holdback):
        """Retaining the same track twice replaces the snapshot rather than accumulating."""
        holdback.retain([self._behavior("s1 #-# obj1", seconds=1)])
        holdback.retain([self._behavior("s1 #-# obj1", seconds=5)])

        assert list(holdback.pending) == ["s1 #-# obj1"]
        assert holdback.pending["s1 #-# obj1"].end.second == 5

    def test_end_track_releases_the_retained_behavior(self, holdback):
        """Ending a track moves its snapshot out of pending and into the next take_ended."""
        holdback.retain([self._behavior("s1 #-# obj1"), self._behavior("s1 #-# obj2")])

        holdback.end_track("s1 #-# obj1", reason="track inactive")

        assert list(holdback.pending) == ["s1 #-# obj2"]
        assert [b.id for b in holdback.take_ended()] == ["s1 #-# obj1"]

    def test_end_track_is_a_no_op_for_unknown_keys(self, holdback):
        """Ending a track that is not held does nothing, so callers need not check first."""
        holdback.end_track("s1 #-# never_seen", reason="state expired")

        assert holdback.take_ended() == []

    def test_end_track_twice_releases_once(self, holdback):
        """A track cannot be released twice, which is what makes writing exactly-once."""
        holdback.retain([self._behavior("s1 #-# obj1")])

        holdback.end_track("s1 #-# obj1", reason="track inactive")
        holdback.end_track("s1 #-# obj1", reason="state expired")

        assert len(holdback.take_ended()) == 1

    def test_take_ended_drains(self, holdback):
        """take_ended hands over the ended behaviors and forgets them."""
        holdback.retain([self._behavior("s1 #-# obj1")])
        holdback.end_track("s1 #-# obj1", reason="track inactive")

        assert len(holdback.take_ended()) == 1
        assert holdback.take_ended() == []

    def test_flush_returns_ended_then_live_and_clears(self, holdback):
        """Flush hands over everything held -- ended first, then still-live tracks -- and empties."""
        holdback.retain([self._behavior("s1 #-# obj1"), self._behavior("s1 #-# obj2")])
        holdback.end_track("s1 #-# obj1", reason="track inactive")

        flushed = holdback.flush()

        assert [b.id for b in flushed] == ["s1 #-# obj1", "s1 #-# obj2"]
        assert holdback.pending == {}
        assert holdback.flush() == []
