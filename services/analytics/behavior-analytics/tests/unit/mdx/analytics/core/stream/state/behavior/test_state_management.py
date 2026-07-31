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
from unittest.mock import Mock
from datetime import datetime, timedelta, timezone

from mdx.analytics.core.stream.state.behavior.state_management import StateMgmt
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import (
    Bbox,
    Behavior,
    Coordinate,
    Message,
    Object,
    ObjectState,
    Place,
    Sensor,
)


class TestStateMgmtLogic:
    """
    Tests for StateMgmt logic (sensor timestamp, expiry, valid state, get_behavior, _process_key).
    Uses StateMgmt directly.
    Includes _process_key tests: same message_key across calls, messages <= previous state end dropped.
    """

    @pytest.fixture
    def full_config(self):
        """Config with all attributes required by StateMgmt."""
        config = Mock(spec=AppConfig)
        config.in_simulation_mode = True
        config.traj_smooth_min_points = 3
        config.traj_smooth_window_size = 3
        config.traj_distance_stride = 1
        config.traj_speed_segment_size = 3
        config.behavior_water_mark = 60
        config.behavior_time_threshold = datetime(2000, 1, 1, tzinfo=timezone.utc)
        config.behavior_state_valid_interval = 30
        config.behavior_max_points = 10000
        config.behavior_state_end_tolerance_sec = 0.0
        config.behavior_emit_once = False
        config.cluster_threshold = 0.5
        config.object_confidence_threshold = 0.0
        config.sensor_tripwire_min_points = Mock(return_value=1)
        return config

    @pytest.fixture
    def mock_calibration(self):
        from mdx.analytics.core.transform.calibration.calibration_base import CalibrationType
        cal = Mock()
        cal.calibration_type = CalibrationType.CARTESIAN
        return cal

    @pytest.fixture
    def state_mgmt(self, full_config, mock_calibration):
        return StateMgmt(full_config, mock_calibration)

    def _make_message(self, message_id: str, sensor_id: str, ts: datetime, x: float, y: float) -> Message:
        return Message(
            messageid=message_id,
            timestamp=ts,
            sensor=Sensor(id=sensor_id, type="camera"),
            object=Object(
                id="obj1",
                type="vehicle",
                confidence=0.9,
                coordinate=Coordinate(x=x, y=y),
                # Encode x/y into the bbox so per-frame bbox alignment with points is assertable.
                bbox=Bbox(leftX=x, topY=y, rightX=x + 1.0, bottomY=y + 1.0),
            ),
            place=Place(id="place1", name="test_place"),
        )

    # --- _get_current_timestamp ---
    def test_get_current_timestamp_simulation_mode_returns_sensor_latest(self, state_mgmt, full_config):
        """_get_current_timestamp in simulation mode returns sensor_latest_timestamp[sensor_id] or None."""
        full_config.in_simulation_mode = True
        state_mgmt.sensor_latest_timestamp["s1"] = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert state_mgmt._get_current_timestamp("s1") == datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert state_mgmt._get_current_timestamp("unknown_sensor") is None

    def test_get_current_timestamp_non_simulation_returns_now(self, state_mgmt, full_config):
        """_get_current_timestamp when not in simulation returns datetime.now(utc)."""
        full_config.in_simulation_mode = False
        before = datetime.now(timezone.utc)
        result = state_mgmt._get_current_timestamp("s1")
        after = datetime.now(timezone.utc)
        assert before <= result <= after

    # --- _update_sensor_latest_timestamp ---
    def test_update_sensor_latest_timestamp_updates_on_new_or_newer(self, state_mgmt):
        """_update_sensor_latest_timestamp sets/updates sensor_latest_timestamp from messages."""
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg1 = Message(
            messageid="m1", timestamp=base,
            sensor=Sensor(id="s1", type="camera"),
            place=Place(id="p1", name="place"),
        )
        msg2 = Message(
            messageid="m2", timestamp=base + timedelta(seconds=5),
            sensor=Sensor(id="s1", type="camera"),
            place=Place(id="p1", name="place"),
        )
        state_mgmt._update_sensor_latest_timestamp([msg1])
        assert state_mgmt.sensor_latest_timestamp["s1"] == base
        state_mgmt._update_sensor_latest_timestamp([msg2])
        assert state_mgmt.sensor_latest_timestamp["s1"] == base + timedelta(seconds=5)

    def test_update_sensor_latest_timestamp_does_not_downgrade(self, state_mgmt):
        """_update_sensor_latest_timestamp does not replace with an older timestamp."""
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        state_mgmt.sensor_latest_timestamp["s1"] = base + timedelta(seconds=10)
        msg_older = Message(
            messageid="m1", timestamp=base,
            sensor=Sensor(id="s1", type="camera"),
            place=Place(id="p1", name="place"),
        )
        state_mgmt._update_sensor_latest_timestamp([msg_older])
        assert state_mgmt.sensor_latest_timestamp["s1"] == base + timedelta(seconds=10)

    # --- _is_valid_state ---
    def test_is_valid_state_true_when_continuous_and_within_interval(self, state_mgmt):
        """_is_valid_state True when new_state.start >= old_state.end and gap < interval."""
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        old = ObjectState(id="k", start=base, end=base + timedelta(seconds=5), points=[])
        new = ObjectState(
            id="k",
            start=base + timedelta(seconds=6),
            end=base + timedelta(seconds=10),
            points=[],
        )
        assert state_mgmt._is_valid_state(old, new, interval=30) is True

    def test_is_valid_state_false_when_new_start_before_old_end(self, state_mgmt):
        """_is_valid_state False when new_state.start < old_state.end."""
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        old = ObjectState(id="k", start=base, end=base + timedelta(seconds=10), points=[])
        new = ObjectState(
            id="k",
            start=base + timedelta(seconds=5),
            end=base + timedelta(seconds=15),
            points=[],
        )
        assert state_mgmt._is_valid_state(old, new, interval=30) is False

    def test_is_valid_state_false_when_gap_exceeds_interval(self, state_mgmt):
        """_is_valid_state False when (new_state.start - old_state.end) >= interval."""
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        old = ObjectState(id="k", start=base, end=base + timedelta(seconds=5), points=[])
        new = ObjectState(
            id="k",
            start=base + timedelta(seconds=40),
            end=base + timedelta(seconds=50),
            points=[],
        )
        assert state_mgmt._is_valid_state(old, new, interval=30) is False

    # --- _get_behavior ---
    def test_get_behavior_builds_behavior_from_state_trajectory_message(self, state_mgmt):
        """_get_behavior returns Behavior with id, timestamp, end, place, sensor from state and message."""
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        state = ObjectState(
            id="s1_o1",
            start=base,
            end=base + timedelta(seconds=2),
            points=[Coordinate(x=0.0, y=0.0), Coordinate(x=1.0, y=1.0)],
        )
        tr = state_mgmt._create_trajectory(state.id, state.start, state.end, state.points)
        msg = Message(
            messageid="m1",
            timestamp=base + timedelta(seconds=2),
            sensor=Sensor(id="s1", type="camera"),
            place=Place(id="p1", name="place1"),
            object=Object(id="o1", type="vehicle", confidence=0.9, coordinate=Coordinate(x=1.0, y=1.0)),
        )
        behavior = state_mgmt._get_behavior(state, tr, msg)
        assert isinstance(behavior, Behavior)
        assert behavior.id == state.id
        assert behavior.timestamp == state.start
        assert behavior.end == state.end
        assert behavior.place == msg.place
        assert behavior.sensor == msg.sensor
        assert behavior.distance == tr.distance
        assert behavior.speed == tr.speed

    # --- _process_key: dummy key (messages without object, e.g. from messages_to_map) ---
    def test_process_key_message_key_dummy_returns_none_tuple(self, state_mgmt):
        """When message_key is 'dummy' (messages without object, e.g. from messages_to_map), _process_key returns (None, None)."""
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        messages_no_object = [
            Message(
                messageid="m1",
                timestamp=base,
                sensor=Sensor(id="sensor1", type="camera"),
                place=Place(id="place1", name="test_place"),
            ),
        ]
        result = state_mgmt._process_key("dummy", messages_no_object)
        assert result == (None, None)
        assert "dummy" not in state_mgmt.state

    # --- _process_key: same message_key across calls, drop messages <= previous state end ---
    def test_process_key_second_call_drops_older_messages_extends_state(self, state_mgmt):
        """Second call: message with ts <= first batch end is dropped; state extends to 12:00:03."""
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        messages_first = [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=1), 1.0, 1.0),
            self._make_message("m3", "sensor1", base + timedelta(seconds=2), 2.0, 2.0),
        ]
        behavior_first, trip_first = state_mgmt._process_key(message_key, messages_first)

        assert behavior_first is not None
        assert trip_first is not None
        assert behavior_first.timestamp == base
        assert behavior_first.end == base + timedelta(seconds=2)

        ts_older = base + timedelta(seconds=1)
        ts_new = datetime(2025, 3, 1, 12, 0, 3, tzinfo=timezone.utc)
        messages_second = [
            self._make_message("m4", "sensor1", ts_older, 0.5, 0.5),
            self._make_message("m5", "sensor1", ts_new, 1.5, 1.5),
        ]
        behavior_second, trip_second = state_mgmt._process_key(message_key, messages_second)

        assert behavior_second is not None
        assert trip_second is not None
        assert behavior_second.timestamp == base
        assert behavior_second.end == ts_new

    def test_process_key_second_call_all_before_previous_end_returns_none_tuple(self, state_mgmt):
        """When second call has only messages with timestamp <= first batch end, all dropped → (None, None)."""
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        messages_first = [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=1), 1.0, 1.0),
            self._make_message("m3", "sensor1", base + timedelta(seconds=2), 2.0, 2.0),
        ]
        behavior_first, _ = state_mgmt._process_key(message_key, messages_first)
        assert behavior_first is not None
        assert behavior_first.end == base + timedelta(seconds=2)

        messages_second = [
            self._make_message("m4", "sensor1", base, 0.0, 0.0),
            self._make_message("m5", "sensor1", base + timedelta(seconds=1), 0.5, 0.5),
        ]
        behavior_second, trip_second = state_mgmt._process_key(message_key, messages_second)

        assert behavior_second is None
        assert trip_second is None

    def test_process_key_single_call_returns_behavior_and_trip_behavior(self, state_mgmt):
        """Single _process_key call with valid messages returns (behavior, trip_behavior) with expected shape."""
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        messages = [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=1), 1.0, 1.0),
        ]
        behavior, trip_behavior = state_mgmt._process_key(message_key, messages)

        assert behavior is not None
        assert trip_behavior is not None
        assert behavior.id == message_key
        assert trip_behavior.id == message_key
        assert behavior.timestamp == base
        assert behavior.end == base + timedelta(seconds=1)
        assert len(behavior.locations.coordinates) >= 1
        assert len(trip_behavior.locations.coordinates) >= 1

    # --- Per-frame bbox storage (aligned 1:1 with points) ---
    def test_bboxes_align_with_points_and_trip_carries_them(self, state_mgmt):
        """New state stores a bbox per point; the trip behavior carries them as locationsBboxes."""
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        messages = [
            self._make_message(f"m{i}", "sensor1", base + timedelta(seconds=i), float(i), float(i))
            for i in range(4)
        ]
        _, trip_behavior = state_mgmt._process_key(message_key, messages)

        state = state_mgmt.state[message_key]
        # bboxes stay 1:1 with points (leftX encodes each point's x).
        assert len(state.bboxes) == len(state.points)
        assert [b.leftX for b in state.bboxes] == [p.x for p in state.points]
        assert len(state.lastXbboxes) == len(state.lastXpoints)
        # The trip behavior carries per-frame bboxes aligned with its locations (internal-only field).
        assert len(trip_behavior.locationsBboxes) == len(trip_behavior.locations.coordinates)
        assert [b.leftX for b in trip_behavior.locationsBboxes] == [
            c.point[0] for c in trip_behavior.locations.coordinates
        ]

    def test_bboxes_stay_aligned_through_halving(self, state_mgmt, full_config):
        """Halving decimates bboxes with the same [::2] stride as points, preserving alignment."""
        full_config.behavior_max_points = 5
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        state_mgmt._process_key(message_key, [
            self._make_message(f"m{i}", "sensor1", base + timedelta(seconds=i), float(i), 0.0)
            for i in range(6)
        ])
        state = state_mgmt.state[message_key]
        # One more in-order msg → points=7, triggers halving → sampling=2.
        state_mgmt._process_key(message_key, [
            self._make_message("m6", "sensor1", base + timedelta(seconds=6), 6.0, 0.0)
        ])
        assert state.sampling == 2
        assert len(state.bboxes) == len(state.points)
        assert [b.leftX for b in state.bboxes] == [p.x for p in state.points]

    def test_bboxes_stay_aligned_through_tolerance_insert(self, state_mgmt, full_config):
        """A tolerance-window bbox is inserted at the same index as its point, preserving order."""
        full_config.behavior_state_end_tolerance_sec = 2.0
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        state_mgmt._process_key(message_key, [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=1), 10.0, 0.0),
            self._make_message("m3", "sensor1", base + timedelta(seconds=2), 20.0, 0.0),
        ])
        state = state_mgmt.state[message_key]
        state_mgmt._process_key(message_key, [
            self._make_message("m_late", "sensor1", base + timedelta(seconds=1, milliseconds=500), 15.0, 0.0),
            self._make_message("m_new", "sensor1", base + timedelta(seconds=3), 30.0, 0.0),
        ])
        # Points are chronologically ordered; bboxes follow the same order (leftX encodes x).
        assert [p.x for p in state.points] == [0.0, 10.0, 15.0, 20.0, 30.0]
        assert [b.leftX for b in state.bboxes] == [0.0, 10.0, 15.0, 20.0, 30.0]

    # --- Cross-batch 1-in-N sampling (sample_phase persists across batches) ---
    def test_sample_phase_persists_across_batches(self, state_mgmt):
        """With sampling=3, phase carries across batches — stride stays 1-in-3 globally, not per-batch."""
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Seed with 3 in-order msgs (fresh init ⇒ sampling=1, all kept)
        initial = [
            self._make_message(f"m{i}", "sensor1", base + timedelta(seconds=i), float(i), 0.0)
            for i in range(3)
        ]
        state_mgmt._process_key(message_key, initial)
        state = state_mgmt.state[message_key]
        state.sampling = 3
        state.sample_phase = 0
        before_len = len(state.points)

        # Batch 1 (2 msgs): phase 0→keep, 1→skip. After: phase=2, +1 point.
        batch1 = [
            self._make_message("m3", "sensor1", base + timedelta(seconds=3), 3.0, 0.0),
            self._make_message("m4", "sensor1", base + timedelta(seconds=4), 4.0, 0.0),
        ]
        state_mgmt._process_key(message_key, batch1)
        assert state.sample_phase == 2
        assert len(state.points) == before_len + 1

        # Batch 2 (2 msgs): phase 2→skip, 0→keep. After: phase=1, +1 point. 1-in-3 stride holds globally.
        batch2 = [
            self._make_message("m5", "sensor1", base + timedelta(seconds=5), 5.0, 0.0),
            self._make_message("m6", "sensor1", base + timedelta(seconds=6), 6.0, 0.0),
        ]
        state_mgmt._process_key(message_key, batch2)
        assert state.sample_phase == 1
        assert len(state.points) == before_len + 2

    # --- Halving preserves exact 1-in-N continuity across stride doubling ---
    def test_halving_preserves_sampling_continuity(self, state_mgmt, full_config):
        """Through the full path, the retained points stay a true 1-in-N sample of the stream.

        Asserted against the original message stream rather than against the phase the code just
        produced, so the parity correction in point_sampling is genuinely exercised end to end and
        not merely checked for self-consistency.
        """
        full_config.behavior_max_points = 5
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        total = 24

        for i in range(total):
            state_mgmt._process_key(
                message_key, [self._make_message(f"m{i}", "sensor1", base + timedelta(seconds=i), float(i), 0.0)]
            )

        state = state_mgmt.state[message_key]
        assert state.sampling > 1  # the cap was crossed, so halving ran at least once
        assert [p.x for p in state.points] == [float(x) for x in range(0, total, state.sampling)]

    # --- Tolerance feature ---
    def test_tolerance_insert_at_sampling_one_preserves_monotonicity(self, state_mgmt, full_config):
        """With sampling=1 and tolerance>0, a late message is bisect-inserted chronologically."""
        full_config.behavior_state_end_tolerance_sec = 2.0
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        first = [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=1), 10.0, 0.0),
            self._make_message("m3", "sensor1", base + timedelta(seconds=2), 20.0, 0.0),
        ]
        state_mgmt._process_key(message_key, first)
        state = state_mgmt.state[message_key]
        before_len = len(state.points)
        assert state.sampling == 1

        # In-tolerance (1.5s, between m2 and m3) + in-order (3s)
        second = [
            self._make_message("m_late", "sensor1", base + timedelta(seconds=1, milliseconds=500), 15.0, 0.0),
            self._make_message("m_new", "sensor1", base + timedelta(seconds=3), 30.0, 0.0),
        ]
        state_mgmt._process_key(message_key, second)
        assert state.end == base + timedelta(seconds=3)
        assert len(state.points) == before_len + 2
        # x is chronologically monotonic — 15.0 is inserted between 10.0 and 20.0
        assert [p.x for p in state.points] == [0.0, 10.0, 15.0, 20.0, 30.0]

    def test_tolerance_message_skipped_when_sampling_above_one(self, state_mgmt, full_config):
        """At sampling>1, tolerance-window messages are NOT inserted into state.points."""
        full_config.behavior_state_end_tolerance_sec = 2.0
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        state_mgmt._process_key(message_key, [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=1), 10.0, 0.0),
            self._make_message("m3", "sensor1", base + timedelta(seconds=2), 20.0, 0.0),
        ])
        state = state_mgmt.state[message_key]
        # Simulate post-halving state manually
        state.sampling = 2
        state.tail_ts = []
        before_len = len(state.points)

        # In-tolerance (1.5s) + in-order (3s). Tolerance coord should be dropped at sampling>1.
        state_mgmt._process_key(message_key, [
            self._make_message("m_late", "sensor1", base + timedelta(seconds=1, milliseconds=500), 99.0, 0.0),
            self._make_message("m_new", "sensor1", base + timedelta(seconds=3), 30.0, 0.0),
        ])
        assert 99.0 not in [p.x for p in state.points]
        # Only the in-order point is considered by the sampler (phase==0 → kept)
        assert len(state.points) == before_len + 1

    def test_tolerance_message_beyond_tolerance_dropped(self, state_mgmt, full_config):
        """A message older than (state.end - tolerance) is dropped at the cutoff filter."""
        full_config.behavior_state_end_tolerance_sec = 0.5
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        state_mgmt._process_key(message_key, [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=1), 10.0, 0.0),
            self._make_message("m3", "sensor1", base + timedelta(seconds=2), 20.0, 0.0),
        ])
        state = state_mgmt.state[message_key]
        before_len = len(state.points)

        # 1s before state.end=2s, tolerance=0.5s — beyond tolerance, dropped
        state_mgmt._process_key(message_key, [
            self._make_message("m_stale", "sensor1", base + timedelta(seconds=1), 99.0, 0.0),
            self._make_message("m_new", "sensor1", base + timedelta(seconds=3), 30.0, 0.0),
        ])
        assert 99.0 not in [p.x for p in state.points]
        assert len(state.points) == before_len + 1

    def test_tolerance_message_before_tail_window_dropped(self, state_mgmt, full_config):
        """A tolerance message older than the tracked tail_ts window is dropped with a warning."""
        from mdx.analytics.core.stream.state.behavior.point_sampling import TAIL_CAP
        full_config.behavior_state_end_tolerance_sec = 100.0
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Saturate tail_ts by feeding more than TAIL_CAP in-order messages
        state_mgmt._process_key(message_key, [
            self._make_message(f"m{i}", "sensor1", base + timedelta(seconds=i), float(i), 0.0)
            for i in range(TAIL_CAP + 5)
        ])
        state = state_mgmt.state[message_key]
        assert len(state.tail_ts) == TAIL_CAP
        tail_start = state.tail_ts[0]
        before_len = len(state.points)

        # Tolerance window accepts old message via cutoff (tolerance=100s), but tail_ts[0] is
        # later than the stale timestamp, so the bisect step drops it.
        stale_ts = tail_start - timedelta(seconds=1)
        new_ts = base + timedelta(seconds=TAIL_CAP + 10)
        state_mgmt._process_key(message_key, [
            self._make_message("m_stale", "sensor1", stale_ts, 99.0, 0.0),
            self._make_message("m_new", "sensor1", new_ts, float(TAIL_CAP + 10), 0.0),
        ])
        assert 99.0 not in [p.x for p in state.points]
        assert len(state.points) == before_len + 1

    def test_tolerance_only_batch_logs_debug_and_drops(self, state_mgmt, full_config, caplog):
        """A batch with only tolerance-window messages (no in-order) is dropped with a debug log."""
        import logging
        full_config.behavior_state_end_tolerance_sec = 5.0
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        state_mgmt._process_key(message_key, [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=2), 10.0, 0.0),
        ])
        state = state_mgmt.state[message_key]
        before_len = len(state.points)

        # Both messages are within tolerance (state.end=2s, tolerance=5s → cutoff=-3s) but
        # neither is strictly in-order (ts > state.end=2s).
        with caplog.at_level(logging.DEBUG, logger="mdx.analytics.core.stream.state.behavior.state_management"):
            behavior, trip_behavior = state_mgmt._process_key(message_key, [
                self._make_message("m_late1", "sensor1", base + timedelta(seconds=1), 50.0, 0.0),
                self._make_message("m_late2", "sensor1", base + timedelta(seconds=1, milliseconds=500), 60.0, 0.0),
            ])
        assert behavior is None
        assert trip_behavior is None
        assert len(state.points) == before_len  # no changes applied
        assert any("Tolerance-only batch" in r.message for r in caplog.records)


class TestStateMgmtEmitOnce:
    """
    Tests for emit-once output (``behaviorEmitOnce``).

    A behavior is held back while its track is live and written exactly once when the track ends:
    after ``behaviorStateValidInterval`` seconds of silence, or immediately when a gap that long is
    proven by a new observation reusing the object ID. Ending a track is also when its state is
    reclaimed. Uses StateMgmt directly.
    """

    @pytest.fixture
    def emit_once_config(self):
        """Config with emit-once enabled and all attributes required by StateMgmt."""
        config = Mock(spec=AppConfig)
        config.in_simulation_mode = True
        config.traj_smooth_min_points = 3
        config.traj_smooth_window_size = 3
        config.traj_distance_stride = 1
        config.traj_speed_segment_size = 3
        config.behavior_water_mark = 600
        config.behavior_time_threshold = datetime(2000, 1, 1, tzinfo=timezone.utc)
        config.behavior_state_valid_interval = 6
        config.behavior_max_points = 10000
        config.behavior_state_end_tolerance_sec = 0.0
        config.behavior_emit_once = True
        config.cluster_threshold = 0.5
        config.object_confidence_threshold = 0.0
        config.sensor_tripwire_min_points = Mock(return_value=1)
        return config

    @pytest.fixture
    def mock_calibration(self):
        from mdx.analytics.core.transform.calibration.calibration_base import CalibrationType
        cal = Mock()
        cal.calibration_type = CalibrationType.CARTESIAN
        return cal

    @pytest.fixture
    def state_mgmt(self, emit_once_config, mock_calibration):
        return StateMgmt(emit_once_config, mock_calibration)

    def _make_message(self, message_id: str, sensor_id: str, object_id: str, ts: datetime, x: float) -> Message:
        return Message(
            messageid=message_id,
            timestamp=ts,
            sensor=Sensor(id=sensor_id, type="camera"),
            object=Object(
                id=object_id,
                type="vehicle",
                confidence=0.9,
                coordinate=Coordinate(x=x, y=0.0),
                bbox=Bbox(leftX=x, topY=0.0, rightX=x + 1.0, bottomY=1.0),
            ),
            place=Place(id="place1", name="test_place"),
        )

    def _messages_map(self, observations: list[tuple[str, str, datetime, float]]) -> dict[str, list[Message]]:
        """Group ``(message_key, object_id, timestamp, x)`` tuples the way ``messages_to_map`` would.

        Insertion order is preserved, so a test can control which key process_batch reaches first.
        """
        by_key: dict[str, list[Message]] = {}
        for message_key, object_id, ts, x in observations:
            sensor_id = message_key.split(" #-# ")[0]
            by_key.setdefault(message_key, []).append(
                self._make_message(f"m{object_id}{x}", sensor_id, object_id, ts, x)
            )
        return by_key

    def _run_batch(self, state_mgmt, observations: list[tuple[str, str, datetime, float]]) -> list[Behavior]:
        """Process one batch and return what it would write."""
        return state_mgmt.process_batch(self._messages_map(observations)).behaviors_to_write

    def _feed(self, state_mgmt, message_key: str, object_id: str, ts: datetime, x: float) -> list[Behavior]:
        """Process a single-observation batch and return what it would write."""
        return self._run_batch(state_mgmt, [(message_key, object_id, ts, x)])

    # --- emit-once disabled: unchanged per-batch output ---
    def test_writes_every_batch_when_disabled(self, state_mgmt, emit_once_config):
        """With behavior_emit_once False, the batch writes what it produced and retains nothing."""
        emit_once_config.behavior_emit_once = False
        key = "sensor1 #-# obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        batch = state_mgmt.process_batch(self._messages_map([(key, "obj1", base, 0.0)]))

        assert [b.id for b in batch.behaviors_to_write] == [key]
        assert batch.behaviors_to_write == batch.active_behaviors
        assert state_mgmt.behavior_holdback.pending == {}

    # --- emit-once enabled: exactly one behavior per track, at the valid interval ---
    def test_behavior_held_back_while_track_is_live(self, state_mgmt):
        """No behavior is written while the track keeps receiving messages."""
        key = "sensor1 #-# obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        for i in range(4):
            assert self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=i), float(i)) == []

        # The latest snapshot is retained, carrying the full accumulated trajectory.
        assert list(state_mgmt.behavior_holdback.pending) == [key]
        assert state_mgmt.behavior_holdback.pending[key].end == base + timedelta(seconds=3)

    def test_behavior_written_once_after_valid_interval(self, state_mgmt):
        """The track is written one valid interval after it goes quiet."""
        key = "sensor1 #-# obj1"
        other_key = "sensor1 #-# obj2"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        for i in range(3):
            self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=i), float(i))

        # A second object on the same sensor keeps the sensor clock moving while obj1 goes silent.
        assert self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=4), 100.0) == []

        # 6s past obj1's last message == behavior_state_valid_interval → obj1 is written.
        written = self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=8), 101.0)

        assert [b.id for b in written] == [key]
        assert written[0].timestamp == base
        assert written[0].end == base + timedelta(seconds=2)
        # Full trajectory of the track, not just the last batch.
        assert len(written[0].locations.coordinates) == 3
        assert key not in state_mgmt.behavior_holdback.pending

        # No repeat on later batches.
        assert self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=12), 102.0) == []

    def test_ending_a_track_reclaims_its_state(self, state_mgmt):
        """Ending a track is what frees its state, so there is no separate memory TTL to wait for."""
        key = "sensor1 #-# obj1"
        other_key = "sensor1 #-# obj2"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        self._feed(state_mgmt, key, "obj1", base, 0.0)
        assert key in state_mgmt.live_object_ids()

        self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=4), 100.0)
        written = self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=8), 101.0)

        assert [b.id for b in written] == [key]
        # Gone in the same batch that wrote it -- no longer live, no state held.
        assert key not in state_mgmt.live_object_ids()
        assert key not in state_mgmt.state

    def test_valid_interval_boundary(self, state_mgmt):
        """Silence just under the valid interval keeps the track; reaching it writes."""
        key = "sensor1 #-# obj1"
        other_key = "sensor1 #-# obj2"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        self._feed(state_mgmt, key, "obj1", base, 0.0)

        # 5.999s of silence — a new observation would still extend the track, so it is not over.
        assert self._feed(
            state_mgmt, other_key, "obj2", base + timedelta(seconds=5, milliseconds=999), 100.0
        ) == []
        assert key in state_mgmt.behavior_holdback.pending

        # Exactly 6s — _is_valid_state would reject a continuation, so the track is provably ended.
        written = self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=6), 101.0)
        assert [b.id for b in written] == [key]

    # --- the sweep must see a whole batch before judging silence ---
    def test_object_updated_later_in_the_same_batch_is_not_ended(self, state_mgmt):
        """A key processed first must not age out a key later in the same batch.

        obj_a jumps 10s (its own track really does end) while obj_b advances only 5s, which is still
        a valid continuation. Judging obj_b the moment obj_a advanced the sensor clock would end it
        on a 10s gap it never had, and the exactly-once guard would then discard the rest of its track.
        """
        key_a = "sensor1 #-# objA"
        key_b = "sensor1 #-# objB"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        self._run_batch(state_mgmt, [(key_a, "objA", base, 0.0), (key_b, "objB", base, 50.0)])

        # objA is processed first and pushes the sensor clock to +10; objB only reaches +5.
        written = self._run_batch(state_mgmt, [
            (key_a, "objA", base + timedelta(seconds=10), 10.0),
            (key_b, "objB", base + timedelta(seconds=5), 55.0),
        ])

        # Only objA ended, and by discontinuity — its new observation is 10s past its old track.
        assert [b.id for b in written] == [key_a]
        assert key_b in state_mgmt.state
        assert state_mgmt.state[key_b].end == base + timedelta(seconds=5)
        assert state_mgmt.behavior_holdback.pending[key_b].end == base + timedelta(seconds=5)

    def test_busy_sensor_does_not_end_another_sensors_tracks(self, state_mgmt):
        """Each sensor's silence is measured on its own clock, so a busy sensor cannot age out another."""
        key = "sensor1 #-# obj1"
        other_sensor_key = "sensor2 #-# obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        self._run_batch(state_mgmt, [(key, "obj1", base, 0.0), (other_sensor_key, "obj1", base, 50.0)])

        # sensor1 races ahead; sensor2 contributes nothing to these batches.
        for i in (4, 8, 12):
            assert self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=i), float(i)) == []

        assert other_sensor_key in state_mgmt.state
        assert other_sensor_key in state_mgmt.behavior_holdback.pending

    def test_ingestion_lag_does_not_end_live_tracks(self, state_mgmt, emit_once_config):
        """In live mode the sweep uses event time, so a lagging pipeline is not mistaken for silence."""
        emit_once_config.in_simulation_mode = False  # expiry would otherwise fall back to wall clock
        key = "sensor1 #-# obj1"
        # Messages arrive 30s behind the wall clock — far past the 6s valid interval, well inside
        # the 300s memory TTL, so the state survives and only the emission rule is under test.
        base = datetime.now(timezone.utc) - timedelta(seconds=30)

        assert self._feed(state_mgmt, key, "obj1", base, 0.0) == []
        assert self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=1), 1.0) == []

        assert key in state_mgmt.state
        assert state_mgmt.behavior_holdback.pending[key].end == base + timedelta(seconds=1)

    def test_discontinuous_track_writes_previous_behavior_immediately(self, state_mgmt):
        """A returning object ID ends the old track even when no sweep ran in between.

        The sensor clock is frozen while its only object is silent, so the inactivity sweep never
        fires; the replacement itself is what proves the previous track ended.
        """
        key = "sensor1 #-# obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        self._feed(state_mgmt, key, "obj1", base, 0.0)
        self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=1), 1.0)

        # 8s gap > behavior_state_valid_interval (6s) → the object ID is reused by a new track.
        written = self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=9), 50.0)

        assert [b.id for b in written] == [key]
        assert written[0].end == base + timedelta(seconds=1)  # the old track, not the new one
        # The replacement track is now the retained snapshot for that key, and can be written again.
        assert state_mgmt.behavior_holdback.pending[key].timestamp == base + timedelta(seconds=9)
        assert key in state_mgmt.state

    def test_replacement_track_written_on_its_own_inactivity(self, state_mgmt):
        """After a replacement, the new track is still written once when it goes quiet."""
        key = "sensor1 #-# obj1"
        other_key = "sensor1 #-# obj2"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        self._feed(state_mgmt, key, "obj1", base, 0.0)
        self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=9), 50.0)  # replaces the first track

        written = self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=15), 100.0)

        assert [b.id for b in written] == [key]
        assert written[0].timestamp == base + timedelta(seconds=9)
        assert key not in state_mgmt.behavior_holdback.pending

    def test_late_observation_starts_a_new_track_rather_than_rewriting(self, state_mgmt):
        """A message arriving after its track ended begins a fresh track, never a second copy.

        Reclaiming state at end is what makes this safe: there is nothing left for a late message to
        extend, so it cannot resurrect and re-write a track that was already written.
        """
        key = "sensor1 #-# obj1"
        other_key = "sensor1 #-# obj2"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        self._feed(state_mgmt, key, "obj1", base, 0.0)
        self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=1), 1.0)

        # obj2 pushes the sensor clock 6s past obj1's last message → obj1 ends and is written.
        first = self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=7), 100.0)
        assert [b.id for b in first] == [key]
        assert first[0].timestamp == base and first[0].end == base + timedelta(seconds=1)
        assert key not in state_mgmt.state

        # A late obj1 message finds no state, so it opens a new track instead of extending the old.
        assert self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=5), 5.0) == []
        assert state_mgmt.state[key].start == base + timedelta(seconds=5)

        second = self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=11), 101.0)

        # Written again under the same ID, but as a distinct track -- not a repeat of the first.
        assert [b.id for b in second] == [key]
        assert second[0].timestamp == base + timedelta(seconds=5)
        assert second[0].timestamp != first[0].timestamp

    def test_written_behavior_keeps_enrichment_of_last_batch(self, state_mgmt):
        """Enrichment applied in place to active_behaviors survives to writing."""
        key = "sensor1 #-# obj1"
        other_key = "sensor1 #-# obj2"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        for i in range(2):
            batch = state_mgmt.process_batch(self._messages_map([(key, "obj1", base + timedelta(seconds=i), float(i))]))
            assert batch.behaviors_to_write == []
            for behavior in batch.active_behaviors:
                behavior.info["current_action"] = f"action_{i}"

        written = self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=7), 100.0)

        assert len(written) == 1
        assert written[0].info["current_action"] == "action_1"

    def test_trip_behaviors_still_produced_every_batch(self, state_mgmt):
        """Emit-once only defers the behavior topic; trip states for events stay per-batch."""
        key = "sensor1 #-# obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        for i in range(3):
            batch = state_mgmt.process_batch(self._messages_map([(key, "obj1", base + timedelta(seconds=i), float(i))]))
            assert [b.id for b in batch.trip_behaviors] == [key]
            assert batch.behaviors_to_write == []

    # --- shutdown flush ---
    def test_flush_behaviors_returns_live_tracks_and_clears(self, state_mgmt):
        """Shutdown flush writes tracks that were still live, then holds nothing."""
        key = "sensor1 #-# obj1"
        other_key = "sensor1 #-# obj2"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        self._run_batch(state_mgmt, [(key, "obj1", base, 0.0), (other_key, "obj2", base, 100.0)])

        flushed = state_mgmt.flush_behaviors()

        assert sorted(b.id for b in flushed) == [key, other_key]
        assert state_mgmt.behavior_holdback.pending == {}
        assert state_mgmt.flush_behaviors() == []

    def test_track_ending_in_its_own_batch_is_still_written(self, state_mgmt):
        """A track that ends in the very batch that produced its behavior must not be dropped.

        Two trackers on one sensor report out of sync within a single batch: objA at +100s, objB at
        +50s, so objB is already past the valid interval by the time the sweep runs. Reclaiming its
        state before retaining its behavior would leave nothing to release, and it would vanish from
        the stream instead of being written.
        """
        key_a = "sensor1 #-# objA"
        key_b = "sensor1 #-# objB"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        batch = state_mgmt.process_batch(self._messages_map([
            (key_a, "objA", base + timedelta(seconds=100), 1.0),
            (key_b, "objB", base + timedelta(seconds=50), 2.0),
        ]))

        assert sorted(b.id for b in batch.active_behaviors) == [key_a, key_b]
        # objB is 50s behind the sensor clock, so its track ended and was written in this same batch.
        assert [b.id for b in batch.behaviors_to_write] == [key_b]
        assert key_b not in state_mgmt.state
        assert key_a in state_mgmt.state

    def test_state_reclaimed_when_emit_once_is_disabled(self, state_mgmt, emit_once_config):
        """Reclamation does not depend on emit-once: per-batch mode ends tracks and frees state too."""
        emit_once_config.behavior_emit_once = False
        key = "sensor1 #-# obj1"
        other_key = "sensor1 #-# obj2"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        self._feed(state_mgmt, key, "obj1", base, 0.0)
        self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=4), 100.0)
        assert key in state_mgmt.state

        # obj1 goes 6s silent; nothing is held back in this mode, but its state is still released.
        written = self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=8), 101.0)

        assert [b.id for b in written] == [other_key]  # per-batch output, unaffected
        assert key not in state_mgmt.state

    def test_switching_emit_once_off_still_writes_held_back_tracks(self, state_mgmt, emit_once_config):
        """behaviorEmitOnce is runtime-updatable, so turning it off must not strand what was held.

        A track retained under emit-once may only end after the setting flips. Nothing else collects
        the holdback, so without handing it over on switch-off that behavior would never be written
        and the holdback would grow without bound.
        """
        key = "sensor1 #-# obj1"
        other_key = "sensor1 #-# obj2"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Held back while emit-once is on.
        assert self._feed(state_mgmt, key, "obj1", base, 0.0) == []
        assert key in state_mgmt.behavior_holdback.pending

        # Operator switches it off before obj1's track ends.
        emit_once_config.behavior_emit_once = False

        written = self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=6), 100.0)

        # obj1's held-back behavior is written alongside the now per-batch output for obj2, and
        # exactly once each -- no track appears twice in the same batch.
        assert sorted(b.id for b in written) == [key, other_key]
        assert state_mgmt.behavior_holdback.pending == {}
        assert state_mgmt.behavior_holdback.ended == []

        # Settled in one go: later batches carry nothing over.
        assert [b.id for b in self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=8), 101.0)] == [
            other_key
        ]

    def test_switching_emit_once_off_does_not_duplicate_a_live_track(self, state_mgmt, emit_once_config):
        """A track still producing output must not also get its stale held snapshot carried over.

        Per-batch output resumes with the fuller behavior, so releasing the frozen snapshot too would
        write the same track twice in one batch, the second copy with an older end.
        """
        key = "sensor1 #-# obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        assert self._feed(state_mgmt, key, "obj1", base, 0.0) == []
        assert key in state_mgmt.behavior_holdback.pending

        emit_once_config.behavior_emit_once = False

        # obj1 is still live, so this batch produces a fresher behavior for it.
        written = self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=1), 1.0)

        assert [b.id for b in written] == [key]
        assert written[0].end == base + timedelta(seconds=1)  # the fresh one, not the frozen snapshot
        assert state_mgmt.behavior_holdback.pending == {}

    def test_switching_emit_once_on_holds_back_from_that_point(self, state_mgmt, emit_once_config):
        """Turning emit-once on mid-run starts holding back, keeping the track's history so far.

        The object state is untouched by the flip, so the single behavior written when the track ends
        still covers everything from before the switch, not just what arrived after it.
        """
        emit_once_config.behavior_emit_once = False
        key = "sensor1 #-# obj1"
        other_key = "sensor1 #-# obj2"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Per-batch output while off.
        assert [b.id for b in self._feed(state_mgmt, key, "obj1", base, 0.0)] == [key]

        emit_once_config.behavior_emit_once = True

        # Now held back rather than written.
        assert self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=1), 1.0) == []
        assert key in state_mgmt.behavior_holdback.pending

        # obj2 pushes the sensor clock a valid interval past obj1's last message.
        written = self._feed(state_mgmt, other_key, "obj2", base + timedelta(seconds=7), 100.0)

        assert [b.id for b in written] == [key]
        # Spans the whole track, including the point that arrived before the switch.
        assert written[0].timestamp == base
        assert written[0].end == base + timedelta(seconds=1)
        assert len(written[0].locations.coordinates) == 2

    def test_emit_once_round_trip_leaves_nothing_held(self, state_mgmt, emit_once_config):
        """Toggling on, off and on again settles cleanly, holding nothing stale from earlier phases."""
        key = "sensor1 #-# obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        assert self._feed(state_mgmt, key, "obj1", base, 0.0) == []           # on: held
        emit_once_config.behavior_emit_once = False
        assert [b.id for b in self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=1), 1.0)] == [key]
        assert state_mgmt.behavior_holdback.pending == {}                      # handed over on switch-off

        emit_once_config.behavior_emit_once = True
        assert self._feed(state_mgmt, key, "obj1", base + timedelta(seconds=2), 2.0) == []
        assert list(state_mgmt.behavior_holdback.pending) == [key]             # holding only the current track
        assert state_mgmt.behavior_holdback.ended == []


class TestStateMgmtBatchApi:
    """
    Tests for the batch entry point and trajectory construction.

    Carried over from the old per-coordinate-system subclasses, which existed only to pick a
    trajectory type. StateMgmt now does that itself, reading the calibration type at construction
    time so a calibration switch is picked up on the next batch.
    """

    @pytest.fixture
    def mock_config(self):
        config = Mock(spec=AppConfig)
        config.in_simulation_mode = True
        config.traj_smooth_min_points = 3
        config.traj_smooth_window_size = 3
        config.traj_distance_stride = 1
        config.traj_speed_segment_size = 3
        config.behavior_water_mark = 60
        config.behavior_time_threshold = datetime(2000, 1, 1, tzinfo=timezone.utc)
        config.behavior_state_valid_interval = 30
        config.behavior_max_points = 10000
        config.behavior_state_end_tolerance_sec = 0.0
        config.behavior_emit_once = False
        config.cluster_threshold = 0.5
        config.object_confidence_threshold = 0.0
        config.sensor_tripwire_min_points = Mock(return_value=1)
        return config

    @pytest.fixture
    def mock_calibration(self):
        from mdx.analytics.core.transform.calibration.calibration_base import CalibrationType
        calibration = Mock()
        calibration.calibration_type = CalibrationType.CARTESIAN
        return calibration

    @pytest.fixture
    def state_mgmt(self, mock_config, mock_calibration):
        return StateMgmt(mock_config, mock_calibration)

    def test_initialization(self, state_mgmt, mock_config, mock_calibration):
        """StateMgmt holds the config and calibration it was given."""
        assert state_mgmt.config == mock_config
        assert state_mgmt.calibration == mock_calibration

    def test_create_trajectory_carries_the_calibration_type(self, state_mgmt, mock_calibration):
        """The trajectory is built with the calibration's current type, not a captured one."""
        from mdx.analytics.core.schema.trajectory.trajectory import Trajectory

        start = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        points = [Coordinate(x=0.0, y=0.0), Coordinate(x=1.0, y=1.0), Coordinate(x=2.0, y=2.0)]

        result = state_mgmt._create_trajectory("t", start, start + timedelta(seconds=10), points)

        assert isinstance(result, Trajectory)
        assert result.calibration_type == mock_calibration.calibration_type
        assert result.id == "t"

    def test_process_batch_returns_behaviors_and_trips(self, state_mgmt):
        """A batch yields one active behavior and one trip behavior per updated track."""
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        messages_map = {
            "sensor1 #-# obj1": [
                Message(
                    messageid=f"m{i}",
                    timestamp=base + timedelta(seconds=i),
                    sensor=Sensor(id="sensor1", type="camera"),
                    object=Object(
                        id="obj1", type="vehicle", confidence=0.9,
                        coordinate=Coordinate(x=float(i), y=0.0),
                    ),
                    place=Place(id="place1", name="test_place"),
                )
                for i in range(3)
            ]
        }

        batch = state_mgmt.process_batch(messages_map)

        assert [b.id for b in batch.active_behaviors] == ["sensor1 #-# obj1"]
        assert [b.id for b in batch.trip_behaviors] == ["sensor1 #-# obj1"]
        # Per-batch mode writes exactly what the batch produced.
        assert batch.behaviors_to_write == batch.active_behaviors

    def test_process_batch_empty_map(self, state_mgmt):
        """An empty batch produces an empty result rather than raising."""
        batch = state_mgmt.process_batch({})

        assert batch.active_behaviors == []
        assert batch.trip_behaviors == []
        assert batch.behaviors_to_write == []
