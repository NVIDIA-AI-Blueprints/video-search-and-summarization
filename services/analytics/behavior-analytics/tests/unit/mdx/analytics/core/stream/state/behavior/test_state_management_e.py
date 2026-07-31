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

from mdx.analytics.core.stream.state.behavior.state_management_e import StateMgmtE
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import (
    Coordinate,
    Message,
    Object,
    Place,
    Sensor,
)
from mdx.analytics.core.schema.trajectory.trajectory_e import TrajectoryE


class TestStateMgmtE:
    """Tests for StateMgmtE: trajectory type and batch processing in Euclidean coordinates."""

    @pytest.fixture
    def mock_config(self):
        """Create mock AppConfig."""
        config = Mock(spec=AppConfig)
        config.traj_smooth_min_points = 3
        config.traj_smooth_window_size = 3
        config.traj_distance_stride = 1
        config.traj_speed_segment_size = 3
        config.state_expire_seconds = 300
        config.behavior_emit_once = False
        # Full pipeline attributes, needed once a test drives process_batch end to end.
        config.in_simulation_mode = True
        config.behavior_water_mark = 60
        config.behavior_time_threshold = datetime(2000, 1, 1, tzinfo=timezone.utc)
        config.behavior_state_valid_interval = 30
        config.behavior_state_end_tolerance_sec = 0.0
        config.behavior_max_points = 10000
        config.cluster_threshold = 0.5
        config.object_confidence_threshold = 0.0
        config.sensor_tripwire_min_points = Mock(return_value=1)
        return config

    @pytest.fixture
    def mock_calibration(self):
        """Create mock Calibration."""
        from mdx.analytics.core.transform.calibration.calibration_base import CalibrationType
        calibration = Mock()
        calibration.calibration_type = CalibrationType.CARTESIAN
        return calibration

    @pytest.fixture
    def state_mgmt(self, mock_config, mock_calibration):
        """Create StateMgmtE instance for testing."""
        return StateMgmtE(mock_config, mock_calibration)

    def test_initialization(self, state_mgmt, mock_config, mock_calibration):
        """Test StateMgmtE initialization."""
        assert state_mgmt.config == mock_config
        assert state_mgmt.calibration == mock_calibration

    def test_create_trajectory(self, state_mgmt):
        """Test _create_trajectory returns TrajectoryE."""
        id = "test_trajectory"
        start = datetime.now()
        end = start + timedelta(seconds=10)
        points = [
            Coordinate(x=0.0, y=0.0),
            Coordinate(x=1.0, y=1.0),
            Coordinate(x=2.0, y=2.0),
        ]

        result = state_mgmt._create_trajectory(id, start, end, points)

        assert isinstance(result, TrajectoryE)
        assert result.id == id
        assert result.start == start
        assert result.end == end

    def test_process_key_empty_messages_returns_none_tuple(self, state_mgmt):
        """_process_key with empty messages yields no behavior and no trip."""
        assert state_mgmt._process_key("sensor1_obj1", []) == (None, None)

    def test_process_key_dummy_message_key_returns_none_tuple(self, state_mgmt):
        """_process_key with a message_key ending in 'dummy' yields no behavior and no trip."""
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg = Message(
            messageid="m1",
            timestamp=base,
            sensor=Sensor(id="sensor1", type="camera"),
            object=Object(id="obj1", type="vehicle", confidence=0.9, coordinate=Coordinate(x=0.0, y=0.0)),
            place=Place(id="place1", name="test_place"),
        )
        assert state_mgmt._process_key("sensor1_obj1_dummy", [msg]) == (None, None)

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
