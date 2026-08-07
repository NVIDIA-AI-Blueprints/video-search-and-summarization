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

import logging
import pytest
from datetime import datetime, timedelta, timezone

from mdx.analytics.core.schema.models import Bbox, Coordinate, Message, Object, ObjectState, Place, Sensor
from mdx.analytics.core.stream.state.behavior import point_sampling
from mdx.analytics.core.stream.state.behavior.point_sampling import (
    TAIL_CAP,
    append_sampled,
    halve_if_needed,
    insert_tolerance_messages,
)

BASE = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def _message(ts: datetime, x: float) -> Message:
    """A message whose x is echoed into the bbox, so point/bbox alignment is assertable."""
    return Message(
        messageid=f"m{x}",
        timestamp=ts,
        sensor=Sensor(id="s1", type="camera"),
        object=Object(
            id="obj1", type="vehicle", confidence=0.9,
            coordinate=Coordinate(x=x, y=0.0),
            bbox=Bbox(leftX=x, topY=0.0, rightX=x + 1.0, bottomY=1.0),
        ),
        place=Place(id="p1", name="place"),
    )


def _state(**overrides) -> ObjectState:
    state = ObjectState(id="s1 #-# obj1", start=BASE, end=BASE, points=[])
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


class TestAppendSampled:
    """Keeping 1 point in every N, with the stride surviving batch boundaries."""

    def test_stride_one_keeps_everything(self):
        state = _state()
        append_sampled(state, [_message(BASE + timedelta(seconds=i), float(i)) for i in range(4)])

        assert [p.x for p in state.points] == [0.0, 1.0, 2.0, 3.0]
        assert [b.leftX for b in state.bboxes] == [0.0, 1.0, 2.0, 3.0]
        assert len(state.tail_ts) == 4

    def test_phase_persists_across_calls(self):
        """The whole point of carrying sample_phase: one-message batches must not defeat the stride.

        A per-batch slice would keep every message here, since each call sees a single point.
        """
        state = _state(sampling=3, sample_phase=0)
        for i in range(6):
            append_sampled(state, [_message(BASE + timedelta(seconds=i), float(i))])

        # 1 in 3 globally, not 1 per call
        assert [p.x for p in state.points] == [0.0, 3.0]

    def test_timestamps_tracked_only_at_stride_one(self):
        """Above stride 1 the retained points are a sample, so tail_ts would be meaningless."""
        state = _state(sampling=2, sample_phase=0)
        append_sampled(state, [_message(BASE + timedelta(seconds=i), float(i)) for i in range(4)])

        assert state.points and state.tail_ts == []

    def test_tail_is_capped(self):
        state = _state()
        append_sampled(state, [_message(BASE + timedelta(seconds=i), float(i)) for i in range(TAIL_CAP + 5)])

        assert len(state.tail_ts) == TAIL_CAP
        assert len(state.points) == TAIL_CAP + 5  # only the timestamp window is bounded


class TestInsertToleranceMessages:
    """Placing late-but-acceptable messages back in chronological order."""

    def test_inserts_at_the_chronological_position(self):
        state = _state()
        append_sampled(state, [_message(BASE + timedelta(seconds=i), float(i * 10)) for i in (0, 1, 2)])

        insert_tolerance_messages(state, [_message(BASE + timedelta(seconds=1, milliseconds=500), 15.0)], "k")

        assert [p.x for p in state.points] == [0.0, 10.0, 15.0, 20.0]
        assert [b.leftX for b in state.bboxes] == [0.0, 10.0, 15.0, 20.0]  # bboxes follow points

    def test_skipped_above_stride_one(self):
        state = _state(sampling=2)
        append_sampled(state, [_message(BASE, 0.0)])
        before = list(state.points)

        insert_tolerance_messages(state, [_message(BASE + timedelta(seconds=1), 99.0)], "k")

        assert state.points == before

    def test_message_older_than_the_window_is_dropped(self, caplog):
        state = _state()
        append_sampled(state, [_message(BASE + timedelta(seconds=i), float(i)) for i in (5, 6)])

        with caplog.at_level(logging.WARNING, logger=point_sampling.__name__):
            insert_tolerance_messages(state, [_message(BASE, 99.0)], "k")

        assert 99.0 not in [p.x for p in state.points]
        assert any("precedes tracked tail window" in r.message for r in caplog.records)


class TestHalveIfNeeded:
    """Doubling the stride without breaking the pattern already laid down."""

    def test_no_op_below_the_cap(self):
        state = _state()
        append_sampled(state, [_message(BASE + timedelta(seconds=i), float(i)) for i in range(3)])

        halve_if_needed(state, max_points=10)

        assert state.sampling == 1 and len(state.points) == 3

    def test_halves_and_doubles_the_stride(self):
        state = _state()
        append_sampled(state, [_message(BASE + timedelta(seconds=i), float(i)) for i in range(6)])

        halve_if_needed(state, max_points=5)

        assert state.sampling == 2
        assert [p.x for p in state.points] == [0.0, 2.0, 4.0]
        assert [b.leftX for b in state.bboxes] == [0.0, 2.0, 4.0]  # decimated in step
        assert state.tail_ts == []  # bisect-insert no longer valid

    @pytest.mark.parametrize("total,cap", [(30, 8), (30, 5), (17, 4), (64, 10)])
    def test_retained_points_are_a_true_1_in_n_sample(self, total, cap):
        """However often the stride doubles, the survivors must be exactly every Nth message.

        This is the invariant the parity correction exists for, asserted against the original stream
        rather than against the phase the code happens to produce. Drop the correction and the
        pattern slips after the first halving -- 0, 4, 8, 11, 15 instead of 0, 4, 8, 12, 16.
        """
        state = _state()
        for i in range(total):
            append_sampled(state, [_message(BASE + timedelta(seconds=i), float(i))])
            halve_if_needed(state, max_points=cap)

        assert [int(p.x) for p in state.points] == list(range(0, total, state.sampling))
        assert [int(b.leftX) for b in state.bboxes] == list(range(0, total, state.sampling))
