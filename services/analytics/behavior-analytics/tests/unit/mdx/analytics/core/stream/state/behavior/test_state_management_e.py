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
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone

from mdx.analytics.core.stream.state.behavior.state_management_e import (
    StateMgmtE,
    StateMgmtEWithTripwire,
)
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import (
    Coordinate,
    Message,
    Object,
    Place,
    Sensor,
)
from mdx.analytics.core.schema.trajectory.trajectory_e import TrajectoryE


class TestStateMgmtEWithTripwire:
    """Tests for StateMgmtEWithTripwire class."""

    @pytest.fixture
    def mock_config(self):
        """Create mock AppConfig."""
        config = Mock(spec=AppConfig)
        config.traj_smooth_min_points = 3
        config.traj_smooth_window_size = 3
        config.traj_distance_stride = 1
        config.traj_speed_segment_size = 3
        config.state_expire_seconds = 300
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
        """Create StateMgmtEWithTripwire instance for testing."""
        return StateMgmtEWithTripwire(mock_config, mock_calibration)

    def test_initialization(self, state_mgmt, mock_config, mock_calibration):
        """Test StateMgmtEWithTripwire initialization."""
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

    def test_update_behavior_empty_messages_returns_none_tuple(self, state_mgmt):
        """Test update_behavior with empty messages returns (None, None)."""
        result = state_mgmt.update_behavior("sensor1_obj1", [])
        assert result == (None, None)

    def test_update_behavior_dummy_message_key_returns_none_tuple(self, state_mgmt):
        """Test update_behavior with message_key ending in 'dummy' returns (None, None)."""
        base = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg = Message(
            messageid="m1",
            timestamp=base,
            sensor=Sensor(id="sensor1", type="camera"),
            object=Object(id="obj1", type="vehicle", confidence=0.9, coordinate=Coordinate(x=0.0, y=0.0)),
            place=Place(id="place1", name="test_place"),
        )
        result = state_mgmt.update_behavior("sensor1_obj1_dummy", [msg])
        assert result == (None, None)


class TestStateMgmtE:
    """Tests for StateMgmtE class."""

    @pytest.fixture
    def mock_config(self):
        """Create mock AppConfig."""
        config = Mock(spec=AppConfig)
        config.traj_smooth_min_points = 3
        config.traj_smooth_window_size = 3
        config.traj_distance_stride = 1
        config.traj_speed_segment_size = 3
        config.state_expire_seconds = 300
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

    def test_update_behavior_returns_single_behavior(self, state_mgmt):
        """Test that update_behavior returns only the first element (behavior, not tripwire)."""
        message_key = "sensor1_obj1"
        messages = []
        
        mock_behavior = Mock()
        mock_tripwire = Mock()
        
        with patch.object(
            StateMgmtEWithTripwire, 
            'update_behavior', 
            return_value=(mock_behavior, mock_tripwire)
        ):
            result = state_mgmt.update_behavior(message_key, messages)
        
        # StateMgmtE should return only the first element
        assert result == mock_behavior

    def test_inherits_from_with_tripwire(self, state_mgmt):
        """Test that StateMgmtE inherits from StateMgmtEWithTripwire."""
        assert isinstance(state_mgmt, StateMgmtEWithTripwire)

    def test_update_behavior_empty_messages_returns_none(self, state_mgmt):
        """Test that update_behavior with empty messages returns None (first element of (None, None))."""
        result = state_mgmt.update_behavior("sensor1_obj1", [])
        assert result is None

    def test_create_trajectory_returns_trajectory_e(self, state_mgmt):
        """Test that StateMgmtE uses same _create_trajectory as parent (TrajectoryE)."""
        traj = state_mgmt._create_trajectory(
            "id",
            datetime.now(),
            datetime.now() + timedelta(seconds=1),
            [Coordinate(x=0.0, y=0.0), Coordinate(x=1.0, y=1.0)],
        )
        assert isinstance(traj, TrajectoryE)
