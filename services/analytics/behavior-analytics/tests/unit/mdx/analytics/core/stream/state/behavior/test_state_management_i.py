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
from datetime import datetime, timedelta

from mdx.analytics.core.stream.state.behavior.state_management_i import (
    StateMgmtI,
    StateMgmtIWithTripwire,
)
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import (
    Coordinate,
)
from mdx.analytics.core.schema.trajectory.trajectory_i import TrajectoryI


class TestStateMgmtIWithTripwire:
    """Tests for StateMgmtIWithTripwire class."""

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
        calibration = Mock()
        return calibration

    @pytest.fixture
    def state_mgmt(self, mock_config, mock_calibration):
        """Create StateMgmtIWithTripwire instance for testing."""
        return StateMgmtIWithTripwire(mock_config, mock_calibration)

    def test_initialization(self, state_mgmt, mock_config, mock_calibration):
        """Test StateMgmtIWithTripwire initialization."""
        assert state_mgmt.config == mock_config
        assert state_mgmt.calibration == mock_calibration

    def test_create_trajectory(self, state_mgmt):
        """Test _create_trajectory returns TrajectoryI."""
        id = "test_trajectory"
        start = datetime.now()
        end = start + timedelta(seconds=10)
        points = [
            Coordinate(x=100.0, y=100.0),
            Coordinate(x=200.0, y=200.0),
            Coordinate(x=300.0, y=300.0),
        ]
        
        result = state_mgmt._create_trajectory(id, start, end, points)
        
        assert isinstance(result, TrajectoryI)
        assert result.id == id
        assert result.start == start
        assert result.end == end


class TestStateMgmtI:
    """Tests for StateMgmtI class."""

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
        calibration = Mock()
        return calibration

    @pytest.fixture
    def state_mgmt(self, mock_config, mock_calibration):
        """Create StateMgmtI instance for testing."""
        return StateMgmtI(mock_config, mock_calibration)

    def test_initialization(self, state_mgmt, mock_config, mock_calibration):
        """Test StateMgmtI initialization."""
        assert state_mgmt.config == mock_config
        assert state_mgmt.calibration == mock_calibration

    def test_update_behavior_returns_single_behavior(self, state_mgmt):
        """Test that update_behavior returns only the first element (behavior, not tripwire)."""
        message_key = "sensor1_obj1"
        messages = []
        
        mock_behavior = Mock()
        mock_tripwire = Mock()
        
        with patch.object(
            StateMgmtIWithTripwire, 
            'update_behavior', 
            return_value=(mock_behavior, mock_tripwire)
        ):
            result = state_mgmt.update_behavior(message_key, messages)
        
        # StateMgmtI should return only the first element
        assert result == mock_behavior

    def test_inherits_from_with_tripwire(self, state_mgmt):
        """Test that StateMgmtI inherits from StateMgmtIWithTripwire."""
        assert isinstance(state_mgmt, StateMgmtIWithTripwire)

