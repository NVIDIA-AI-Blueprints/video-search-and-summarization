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

from mdx.analytics.core.stream.state.behavior.state_management_g import StateMgmtG
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import (
    Coordinate, 
    Message, 
    ObjectState,
    Sensor, 
    Object, 
    Place,
)
from mdx.analytics.core.schema.trajectory.trajectory_g import TrajectoryG


class TestStateMgmtG:
    """Tests for StateMgmt class."""

    @pytest.fixture
    def mock_config(self):
        """Create mock AppConfig."""
        config = Mock(spec=AppConfig)
        config.traj_geo_coord_enable = True
        config.traj_smooth_min_points = 3
        config.traj_smooth_window_size = 3
        config.traj_distance_stride = 1
        config.traj_speed_segment_size = 3
        config.traj_direction_mode = 0
        config.traj_direction_cluster_mode = 0
        config.map_matching_classes = ["vehicle"]
        config.map_matching_max_points = 10
        config.inference = Mock()
        config.inference.enable = False
        config.state_expire_seconds = 300
        
        crs_config = Mock()
        crs_config.inputDataInCRSCartesian = False
        config.coordinateReferenceSystem = crs_config
        
        config.get_sensor_anomaly_config = Mock(return_value={})
        return config

    @pytest.fixture
    def mock_calibration(self):
        """Create mock Calibration.

        Typed geographic, since StateMgmtG is only selected for geographic calibration -- and since
        ``_create_trajectory`` now carries the type through to the trajectory rather than letting it
        fall back to Trajectory's cartesian default.
        """
        from mdx.analytics.core.transform.calibration.calibration_base import CalibrationType
        calibration = Mock()
        calibration.calibration_type = CalibrationType.GEO
        return calibration

    @pytest.fixture
    def state_mgmt(self, mock_config, mock_calibration):
        """Create StateMgmt instance for testing."""
        return StateMgmtG(mock_config, mock_calibration, crs=None)

    def test_initialization(self, state_mgmt, mock_config):
        """Test StateMgmt initialization."""
        assert state_mgmt.config == mock_config
        assert state_mgmt.mapmatching_success_cnt == 0
        assert state_mgmt.mapmatching_total_cnt == 0

    def test_create_trajectory(self, state_mgmt):
        """Test _create_trajectory returns proper Trajectory."""
        id = "test_trajectory"
        start = datetime.now()
        end = start + timedelta(seconds=10)
        points = [
            Coordinate(x=0.0, y=0.0),
            Coordinate(x=1.0, y=1.0),
            Coordinate(x=2.0, y=2.0),
        ]
        
        result = state_mgmt._create_trajectory(id, start, end, points)
        
        assert isinstance(result, TrajectoryG)
        assert result.id == id
        assert result.start == start
        assert result.end == end

    def test_update_object_state_model_noop(self, state_mgmt):
        """Test _update_object_state_model is a no-op."""
        state = Mock()
        embeddings = [[1.0, 2.0, 3.0]]
        # Should not raise and return None
        result = state_mgmt._update_object_state_model(state, embeddings)
        assert result is None

    def test_subsample_for_mapmatching_small_list(self, state_mgmt):
        """Test subsampling when points are fewer than target."""
        points = [1, 2, 3]
        result = state_mgmt._subsample_for_mapmatching(points, 10)
        assert result == points

    def test_subsample_for_mapmatching_exact_target(self, state_mgmt):
        """Test subsampling to exact target count."""
        points = list(range(20))
        result = state_mgmt._subsample_for_mapmatching(points, 5)
        assert len(result) == 5
        # Should include first and last
        assert result[0] == 0
        assert result[-1] == 19

    def test_subsample_for_mapmatching_zero_target(self, state_mgmt):
        """Test subsampling with zero target returns original."""
        points = [1, 2, 3]
        result = state_mgmt._subsample_for_mapmatching(points, 0)
        assert result == points

    def test_process_key_no_messages(self, state_mgmt):
        """_process_key with empty messages yields no behavior, exercising the real path."""
        assert state_mgmt._process_key("sensor1_obj1", []) == (None, None)

    def test_build_trip_behavior_is_always_none(self, state_mgmt):
        """Geographic tracks have no trip behavior, even when a trip state exists upstream.

        This is what lets StateMgmtG share _process_key with the base rather than duplicating it.
        """
        base = datetime(2025, 3, 1, 12, 0, 0)
        trip_state = ObjectState(
            id="s1 #-# obj1", start=base, end=base + timedelta(seconds=1),
            points=[Coordinate(x=0.0, y=0.0), Coordinate(x=1.0, y=1.0)],
        )
        message = Message(
            messageid="m1", timestamp=base,
            sensor=Sensor(id="s1", type="camera"),
            place=Place(id="p1", name="place"),
            object=Object(id="obj1", type="vehicle", confidence=0.9, coordinate=Coordinate(x=1.0, y=1.0)),
        )

        assert state_mgmt._build_trip_behavior(trip_state, message) is None


class TestStateMgmtGWithMessages:
    """Tests for StateMgmt with actual messages."""

    @pytest.fixture
    def config(self):
        """Create real AppConfig."""
        config = AppConfig()
        config.set_app_config("trajGeoCoordEnable", "true")
        return config

    @pytest.fixture
    def mock_calibration(self):
        """Create mock Calibration.

        Typed geographic, since StateMgmtG is only selected for geographic calibration -- and since
        ``_create_trajectory`` now carries the type through to the trajectory rather than letting it
        fall back to Trajectory's cartesian default.
        """
        from mdx.analytics.core.transform.calibration.calibration_base import CalibrationType
        calibration = Mock()
        calibration.calibration_type = CalibrationType.GEO
        return calibration

    @pytest.fixture
    def sample_message(self):
        """Create sample message."""
        return Message(
            sensor=Sensor(id="sensor1", type="camera"),
            object=Object(id="obj1", type="vehicle"),
            timestamp=datetime.now(),
            place=Place(id="place1", name="test_place"),
            videoPath="test_video_path"
        )

    def test_speed_over_edge_empty_lattice(self):
        """Test _speed_over_edge with empty lattice."""
        config = AppConfig()
        config.set_app_config("trajGeoCoordEnable", "true")
        calibration = Mock()
        state_mgmt = StateMgmtG(config, calibration, crs=None)
        
        tr = Mock()
        tr.smooth_trajectory = [
            Coordinate(x=0.0, y=0.0),
            Coordinate(x=1.0, y=1.0),
        ]
        tr.start = datetime.now()
        tr.end = datetime.now() + timedelta(seconds=10)
        
        matched_lattice = []
        
        edges, speeds = state_mgmt._speed_over_edge(tr, matched_lattice)
        
        assert edges == []
        assert speeds == []

