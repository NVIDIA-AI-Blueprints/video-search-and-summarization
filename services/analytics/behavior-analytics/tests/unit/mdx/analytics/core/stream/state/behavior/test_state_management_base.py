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

from mdx.analytics.core.stream.state.behavior.state_management_e import StateMgmtEWithTripwire
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import (
    Behavior,
    Coordinate,
    Message,
    Object,
    ObjectState,
    Place,
    Sensor,
)


class TestStateMgmtBaseLogic:
    """
    Tests for StateMgmtBase logic (sensor timestamp, expiry, valid state, get_behavior, update_behavior).
    Uses StateMgmtEWithTripwire as concrete implementation.
    Includes update_behavior tests: same message_key across calls, messages <= previous state end dropped.
    """

    @pytest.fixture
    def full_config(self):
        """Config with all attributes required by StateMgmtBase."""
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
        config.behavior_state_timeout = 300
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
        return StateMgmtEWithTripwire(full_config, mock_calibration)

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

    # --- _delete_expired_object_state ---
    def test_delete_expired_object_state_removes_old_state(self, state_mgmt, full_config):
        """_delete_expired_object_state deletes state when (now - state.end) > behavior_state_timeout."""
        behavior_id = "s1 #-# obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        state_mgmt.state[behavior_id] = ObjectState(
            id=behavior_id,
            start=base,
            end=base + timedelta(seconds=1),
            points=[Coordinate(x=0.0, y=0.0)],
        )
        full_config.behavior_state_timeout = 10
        state_mgmt.sensor_latest_timestamp["s1"] = base + timedelta(seconds=100)
        state_mgmt._delete_expired_object_state()
        assert behavior_id not in state_mgmt.state

    def test_delete_expired_object_state_keeps_recent_state(self, state_mgmt, full_config):
        """_delete_expired_object_state keeps state when within timeout."""
        behavior_id = "s1 #-# obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        state_mgmt.state[behavior_id] = ObjectState(
            id=behavior_id,
            start=base,
            end=base + timedelta(seconds=1),
            points=[Coordinate(x=0.0, y=0.0)],
        )
        full_config.behavior_state_timeout = 300
        state_mgmt.sensor_latest_timestamp["s1"] = base + timedelta(seconds=5)
        state_mgmt._delete_expired_object_state()
        assert behavior_id in state_mgmt.state

    def test_delete_expired_object_state_skips_when_no_current_timestamp(self, state_mgmt, full_config):
        """_delete_expired_object_state skips state when sensor has no timestamp (simulation)."""
        behavior_id = "s1 #-# obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        state_mgmt.state[behavior_id] = ObjectState(
            id=behavior_id,
            start=base,
            end=base + timedelta(seconds=1),
            points=[Coordinate(x=0.0, y=0.0)],
        )
        full_config.behavior_state_timeout = 1
        state_mgmt._delete_expired_object_state()
        assert behavior_id in state_mgmt.state

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

    # --- _get_object_state_and_message ---
    def test_get_object_state_and_message_delegates_to_trip(self, state_mgmt):
        """_get_object_state_and_message returns (state, last_message) from trip method."""
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        messages = [
            Message(
                messageid="m1",
                timestamp=base,
                sensor=Sensor(id="sensor1", type="camera"),
                object=Object(id="o1", type="v", confidence=0.9, coordinate=Coordinate(x=0.0, y=0.0)),
                place=Place(id="p1", name="place"),
            ),
        ]
        state, last_msg = state_mgmt._get_object_state_and_message("sensor1_obj1", messages)
        assert state is not None
        assert last_msg is not None
        assert state.id == "sensor1_obj1"
        assert last_msg.messageid == "m1"

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

    # --- update_behavior: dummy key (messages without object, e.g. from messages_to_map) ---
    def test_update_behavior_message_key_dummy_returns_none_tuple(self, state_mgmt):
        """When message_key is 'dummy' (messages without object, e.g. from messages_to_map), update_behavior returns (None, None)."""
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        messages_no_object = [
            Message(
                messageid="m1",
                timestamp=base,
                sensor=Sensor(id="sensor1", type="camera"),
                place=Place(id="place1", name="test_place"),
            ),
        ]
        result = state_mgmt.update_behavior("dummy", messages_no_object)
        assert result == (None, None)
        assert "dummy" not in state_mgmt.state

    # --- update_behavior: same message_key across calls, drop messages <= previous state end ---
    def test_update_behavior_second_call_drops_older_messages_extends_state(self, state_mgmt):
        """Second call: message with ts <= first batch end is dropped; state extends to 12:00:03."""
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        messages_first = [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=1), 1.0, 1.0),
            self._make_message("m3", "sensor1", base + timedelta(seconds=2), 2.0, 2.0),
        ]
        behavior_first, trip_first = state_mgmt.update_behavior(message_key, messages_first)

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
        behavior_second, trip_second = state_mgmt.update_behavior(message_key, messages_second)

        assert behavior_second is not None
        assert trip_second is not None
        assert behavior_second.timestamp == base
        assert behavior_second.end == ts_new

    def test_update_behavior_second_call_all_before_previous_end_returns_none_tuple(self, state_mgmt):
        """When second call has only messages with timestamp <= first batch end, all dropped → (None, None)."""
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        messages_first = [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=1), 1.0, 1.0),
            self._make_message("m3", "sensor1", base + timedelta(seconds=2), 2.0, 2.0),
        ]
        behavior_first, _ = state_mgmt.update_behavior(message_key, messages_first)
        assert behavior_first is not None
        assert behavior_first.end == base + timedelta(seconds=2)

        messages_second = [
            self._make_message("m4", "sensor1", base, 0.0, 0.0),
            self._make_message("m5", "sensor1", base + timedelta(seconds=1), 0.5, 0.5),
        ]
        behavior_second, trip_second = state_mgmt.update_behavior(message_key, messages_second)

        assert behavior_second is None
        assert trip_second is None

    def test_update_behavior_single_call_returns_behavior_and_trip_behavior(self, state_mgmt):
        """Single update_behavior call with valid messages returns (behavior, trip_behavior) with expected shape."""
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        messages = [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=1), 1.0, 1.0),
        ]
        behavior, trip_behavior = state_mgmt.update_behavior(message_key, messages)

        assert behavior is not None
        assert trip_behavior is not None
        assert behavior.id == message_key
        assert trip_behavior.id == message_key
        assert behavior.timestamp == base
        assert behavior.end == base + timedelta(seconds=1)
        assert len(behavior.locations.coordinates) >= 1
        assert len(trip_behavior.locations.coordinates) >= 1

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
        state_mgmt.update_behavior(message_key, initial)
        state = state_mgmt.state[message_key]
        state.sampling = 3
        state.sample_phase = 0
        before_len = len(state.points)

        # Batch 1 (2 msgs): phase 0→keep, 1→skip. After: phase=2, +1 point.
        batch1 = [
            self._make_message("m3", "sensor1", base + timedelta(seconds=3), 3.0, 0.0),
            self._make_message("m4", "sensor1", base + timedelta(seconds=4), 4.0, 0.0),
        ]
        state_mgmt.update_behavior(message_key, batch1)
        assert state.sample_phase == 2
        assert len(state.points) == before_len + 1

        # Batch 2 (2 msgs): phase 2→skip, 0→keep. After: phase=1, +1 point. 1-in-3 stride holds globally.
        batch2 = [
            self._make_message("m5", "sensor1", base + timedelta(seconds=5), 5.0, 0.0),
            self._make_message("m6", "sensor1", base + timedelta(seconds=6), 6.0, 0.0),
        ]
        state_mgmt.update_behavior(message_key, batch2)
        assert state.sample_phase == 1
        assert len(state.points) == before_len + 2

    # --- Halving preserves exact 1-in-N continuity across stride doubling ---
    def test_halving_preserves_sampling_continuity(self, state_mgmt, full_config):
        """After state.points > behavior_max_points, halving + phase parity shift keeps the 1-in-2N stride continuous."""
        full_config.behavior_max_points = 5
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Seed 6 msgs → fresh init, sampling=1, points has 6 entries, halving should trigger on next update
        initial = [
            self._make_message(f"m{i}", "sensor1", base + timedelta(seconds=i), float(i), 0.0)
            for i in range(6)
        ]
        state_mgmt.update_behavior(message_key, initial)
        state = state_mgmt.state[message_key]
        assert state.sampling == 1
        assert len(state.points) == 6

        # One more in-order msg → points=7, triggers halving → sampling=2
        more = [self._make_message("m6", "sensor1", base + timedelta(seconds=6), 6.0, 0.0)]
        state_mgmt.update_behavior(message_key, more)
        assert state.sampling == 2
        assert state.tail_ts == []  # cleared on halving

        # Next raw msg. Under the new stride, whether this msg is kept depends on the phase
        # parity formula. Verify phase math matches: advancing sample_phase from its current
        # value and checking kept-count is consistent with 1-in-2 of the now-doubled pattern.
        points_before = len(state.points)
        phase_before = state.sample_phase
        nxt = [self._make_message("m7", "sensor1", base + timedelta(seconds=7), 7.0, 0.0)]
        state_mgmt.update_behavior(message_key, nxt)
        expected_delta = 1 if phase_before == 0 else 0
        assert len(state.points) == points_before + expected_delta

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
        state_mgmt.update_behavior(message_key, first)
        state = state_mgmt.state[message_key]
        before_len = len(state.points)
        assert state.sampling == 1

        # In-tolerance (1.5s, between m2 and m3) + in-order (3s)
        second = [
            self._make_message("m_late", "sensor1", base + timedelta(seconds=1, milliseconds=500), 15.0, 0.0),
            self._make_message("m_new", "sensor1", base + timedelta(seconds=3), 30.0, 0.0),
        ]
        state_mgmt.update_behavior(message_key, second)
        assert state.end == base + timedelta(seconds=3)
        assert len(state.points) == before_len + 2
        # x is chronologically monotonic — 15.0 is inserted between 10.0 and 20.0
        assert [p.x for p in state.points] == [0.0, 10.0, 15.0, 20.0, 30.0]

    def test_tolerance_message_skipped_when_sampling_above_one(self, state_mgmt, full_config):
        """At sampling>1, tolerance-window messages are NOT inserted into state.points."""
        full_config.behavior_state_end_tolerance_sec = 2.0
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        state_mgmt.update_behavior(message_key, [
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
        state_mgmt.update_behavior(message_key, [
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

        state_mgmt.update_behavior(message_key, [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=1), 10.0, 0.0),
            self._make_message("m3", "sensor1", base + timedelta(seconds=2), 20.0, 0.0),
        ])
        state = state_mgmt.state[message_key]
        before_len = len(state.points)

        # 1s before state.end=2s, tolerance=0.5s — beyond tolerance, dropped
        state_mgmt.update_behavior(message_key, [
            self._make_message("m_stale", "sensor1", base + timedelta(seconds=1), 99.0, 0.0),
            self._make_message("m_new", "sensor1", base + timedelta(seconds=3), 30.0, 0.0),
        ])
        assert 99.0 not in [p.x for p in state.points]
        assert len(state.points) == before_len + 1

    def test_tolerance_message_before_tail_window_dropped(self, state_mgmt, full_config):
        """A tolerance message older than the tracked tail_ts window is dropped with a warning."""
        from mdx.analytics.core.stream.state.behavior.state_management_base import TAIL_CAP
        full_config.behavior_state_end_tolerance_sec = 100.0
        message_key = "sensor1_obj1"
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Saturate tail_ts by feeding more than TAIL_CAP in-order messages
        state_mgmt.update_behavior(message_key, [
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
        state_mgmt.update_behavior(message_key, [
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

        state_mgmt.update_behavior(message_key, [
            self._make_message("m1", "sensor1", base, 0.0, 0.0),
            self._make_message("m2", "sensor1", base + timedelta(seconds=2), 10.0, 0.0),
        ])
        state = state_mgmt.state[message_key]
        before_len = len(state.points)

        # Both messages are within tolerance (state.end=2s, tolerance=5s → cutoff=-3s) but
        # neither is strictly in-order (ts > state.end=2s).
        with caplog.at_level(logging.DEBUG, logger="mdx.analytics.core.stream.state.behavior.state_management_base"):
            behavior, trip_behavior = state_mgmt.update_behavior(message_key, [
                self._make_message("m_late1", "sensor1", base + timedelta(seconds=1), 50.0, 0.0),
                self._make_message("m_late2", "sensor1", base + timedelta(seconds=1, milliseconds=500), 60.0, 0.0),
            ])
        assert behavior is None
        assert trip_behavior is None
        assert len(state.points) == before_len  # no changes applied
        assert any("Tolerance-only batch" in r.message for r in caplog.records)
