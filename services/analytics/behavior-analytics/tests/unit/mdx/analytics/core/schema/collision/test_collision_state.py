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
from datetime import datetime, timedelta
from unittest.mock import patch

from mdx.analytics.core.schema.collision.collision_state import CollisionState
from mdx.analytics.core.schema.models import Place, Bbox, Behavior, Sensor, Object, Location
from mdx.analytics.core.transform.detection.collision_detection import ObjectData


class TestCollisionState:
    """Comprehensive test suite for CollisionState functionality."""

    @pytest.fixture
    def collision_state(self):
        """Fixture providing a basic collision state for testing."""
        return CollisionState(
            ["obj1", "obj2"], 
            "sensor1", 
            Place(name="test_place"), 
            Location(lat=1.0, lon=2.0, alt=3.0),
            datetime.now(), 
            trigger_modules=[]
        )

    @pytest.fixture
    def sample_behavior(self):
        """Fixture providing a sample behavior object for testing."""
        # Create required sensor and object
        sensor = Sensor(id="test_sensor")
        obj = Object(id="obj1", type="person")
        start_time = datetime.now()
        end_time = start_time + timedelta(seconds=1)
        return Behavior(
            id="obj1",
            timestamp=start_time,
            end=end_time,
            sensor=sensor,
            object=obj,
            timeInterval=1.0
        )

    # Initialization Tests
    def test_initialization_with_valid_parameters_creates_correct_state(self):
        """Test that CollisionState initializes correctly with valid parameters."""
        object_ids = ["obj1", "obj2"]
        sensor_id = "sensor1"
        timeout = 15
        place = Place(name="test_place")
        location = Location(lat=1.0, lon=2.0, alt=3.0)
        start_time = datetime.now()
        trigger_modules = ["module1", "module2"]
        
        state = CollisionState(object_ids, sensor_id, place, location, start_time, trigger_modules, timeout)
        
        assert state.object_ids == set(object_ids)
        assert state.sensor_id == sensor_id
        assert state.timeout == timeout
        assert state.start_time == start_time
        assert state.primary_object_id == object_ids[0]
        assert state.place == place
        assert state.location == location
        assert state.trigger_modules == trigger_modules
        assert len(state.trajectories) == len(object_ids)
        assert all(obj_id in state.trajectories for obj_id in object_ids)
        assert all(len(traj) == 0 for traj in state.trajectories.values())

    def test_initialization_with_default_timeout_sets_ten_seconds(self):
        """Test that CollisionState uses default timeout of 10 seconds when not specified."""
        object_ids = ["obj1"]
        sensor_id = "sensor1"
        place = Place(name="test_place")
        location = Location(lat=1.0, lon=2.0, alt=3.0)
        start_time = datetime.now()
        trigger_modules = []
        
        state = CollisionState(object_ids, sensor_id, place, location, start_time, trigger_modules)
        
        assert state.timeout == 10

    def test_initialization_with_single_object_sets_primary_correctly(self):
        """Test that CollisionState sets primary object ID correctly for single object."""
        object_ids = ["single_obj"]
        sensor_id = "sensor1"
        place = Place(name="test_place")
        location = Location(lat=1.0, lon=2.0, alt=3.0)
        start_time = datetime.now()
        trigger_modules = []
        
        state = CollisionState(object_ids, sensor_id, place, location, start_time, trigger_modules)
        
        assert state.primary_object_id == "single_obj"
        assert len(state.object_ids) == 1
        assert "single_obj" in state.trajectories

    # Object Management Tests
    def test_add_object_id_with_new_object_adds_to_state(self, collision_state):
        """Test that adding a new object ID updates the collision state correctly."""
        new_object_id = "obj3"
        initial_count = len(collision_state.object_ids)
        
        collision_state.add_object_id(new_object_id)
        
        assert new_object_id in collision_state.object_ids
        assert new_object_id in collision_state.trajectories
        assert len(collision_state.trajectories[new_object_id]) == 0
        assert len(collision_state.object_ids) == initial_count + 1

    def test_add_object_id_with_existing_object_remains_unchanged(self, collision_state):
        """Test that adding an existing object ID doesn't create duplicates."""
        existing_object_id = "obj1"
        initial_count = len(collision_state.object_ids)
        
        collision_state.add_object_id(existing_object_id)
        
        assert len(collision_state.object_ids) == initial_count
        assert existing_object_id in collision_state.object_ids

    def test_get_object_ids_returns_correct_set(self, collision_state):
        """Test that get_object_ids returns the correct set of object IDs."""
        object_ids = collision_state.get_object_ids()
        
        assert isinstance(object_ids, set)
        assert object_ids == {"obj1", "obj2"}

    def test_get_place_returns_correct_place_object(self, collision_state):
        """Test that get_place returns the correct Place object."""
        place = collision_state.get_place()
        
        assert isinstance(place, Place)
        assert place.name == "test_place"

    def test_get_location_returns_correct_location_object(self, collision_state):
        """Test that get_location returns the correct Location object."""
        location = collision_state.get_location()
        
        assert isinstance(location, Location)
        assert location.lat == 1.0
        assert location.lon == 2.0
        assert location.alt == 3.0

    def test_update_location_updates_location_object(self, collision_state):
        """Test that update_location updates the location object."""
        location = Location(lat=4.0, lon=5.0, alt=6.0)
        collision_state.update_location(location)
        
        assert collision_state.location.lat == 4.0
        assert collision_state.location.lon == 5.0
        assert collision_state.location.alt == 6.0

    # Trajectory Management Tests
    def test_update_trajectory_by_frame_with_valid_data_adds_trajectory_point(self, collision_state):
        """Test that updating trajectory by frame adds a trajectory point correctly."""
        frame = {
            "obj1": ObjectData(
                x=1.0, y=2.0, 
                bbox=Bbox(leftX=1.0, topY=2.0, rightX=3.0, bottomY=4.0), 
                object_type="vehicle", 
                is_geo_coordinate=True, 
                bearing=0.0
            ),
            "timestamp": 1234567890
        }
        
        collision_state.update_trajectory_by_frame("obj1", frame)
        
        trajectory = collision_state.get_trajectory("obj1")
        assert len(trajectory) == 1
        assert trajectory[0]["x"] == 1.0
        assert trajectory[0]["y"] == 2.0
        assert trajectory[0]["timestamp"] == 1234567890

    def test_update_trajectory_by_frame_with_new_object_creates_trajectory(self, collision_state):
        """Test that updating trajectory for a new object creates the trajectory list."""
        frame = {
            "new_obj": ObjectData(
                x=5.0, y=6.0,
                bbox=Bbox(leftX=5.0, topY=6.0, rightX=7.0, bottomY=8.0),
                object_type="pedestrian",
                is_geo_coordinate=True,
                bearing=90.0
            ),
            "timestamp": 1234567890
        }
        
        collision_state.update_trajectory_by_frame("new_obj", frame)
        
        trajectory = collision_state.get_trajectory("new_obj")
        assert len(trajectory) == 1
        assert trajectory[0]["x"] == 5.0
        assert trajectory[0]["y"] == 6.0

    def test_update_trajectory_by_trajectory_with_valid_data_replaces_trajectory(self, collision_state):
        """Test that updating trajectory by trajectory list replaces existing trajectory."""
        trajectory_data = [
            {"x": 1.0, "y": 2.0, "timestamp": 1234567890},
            {"x": 2.0, "y": 3.0, "timestamp": 1234567891}
        ]
        
        collision_state.update_trajectory_by_trajectory("obj1", trajectory_data)
        
        stored_trajectory = collision_state.get_trajectory("obj1")
        assert len(stored_trajectory) == 2
        assert stored_trajectory == trajectory_data

    def test_get_trajectory_with_existing_object_returns_trajectory(self, collision_state):
        """Test that getting trajectory for existing object returns correct trajectory."""
        # Add some trajectory data first
        trajectory_data = [{"x": 1.0, "y": 2.0, "timestamp": 1234567890}]
        collision_state.update_trajectory_by_trajectory("obj1", trajectory_data)
        
        result = collision_state.get_trajectory("obj1")
        
        assert result == trajectory_data

    def test_get_trajectories_returns_all_trajectory_data(self, collision_state):
        """Test that get_trajectories returns all trajectory data for all objects."""
        # Add trajectory data for multiple objects
        collision_state.update_trajectory_by_trajectory("obj1", [{"x": 1.0, "y": 2.0, "timestamp": 123}])
        collision_state.update_trajectory_by_trajectory("obj2", [{"x": 3.0, "y": 4.0, "timestamp": 456}])
        
        trajectories = collision_state.get_trajectories()
        
        assert isinstance(trajectories, dict)
        assert "obj1" in trajectories
        assert "obj2" in trajectories
        assert len(trajectories["obj1"]) == 1
        assert len(trajectories["obj2"]) == 1

    # Behavior Management Tests
    def test_update_behavior_with_valid_data_stores_behavior(self, collision_state, sample_behavior):
        """Test that updating behavior stores the behavior correctly."""
        collision_state.update_behavior("obj1", sample_behavior)
        
        stored_behavior = collision_state.get_behavior("obj1")
        assert stored_behavior is sample_behavior
        assert stored_behavior.id == "obj1"

    def test_update_behavior_with_new_object_stores_behavior(self, collision_state, sample_behavior):
        """Test that updating behavior for a new object stores the behavior."""
        # Create a separate behavior object to avoid modifying the fixture
        sensor = Sensor(id="test_sensor")
        obj = Object(id="new_obj", type="person")
        
        new_behavior = Behavior(
            id="new_obj",
            timestamp=datetime.now(),
            end=datetime.now() + timedelta(seconds=1),
            sensor=sensor,
            object=obj,
            timeInterval=1.0
        )
        
        collision_state.update_behavior("new_obj", new_behavior)
        
        stored_behavior = collision_state.get_behavior("new_obj")
        assert stored_behavior is new_behavior

    def test_get_behavior_with_existing_object_returns_behavior(self, collision_state, sample_behavior):
        """Test that getting behavior for existing object returns correct behavior."""
        collision_state.update_behavior("obj1", sample_behavior)
        
        result = collision_state.get_behavior("obj1")
        
        assert result is sample_behavior

    def test_get_behavior_with_nonexistent_object_returns_none(self, collision_state):
        """Test that getting behavior for non-existent object returns None."""
        result = collision_state.get_behavior("nonexistent_obj")
        
        assert result is None

    def test_get_behavior_with_empty_behaviors_returns_none(self, collision_state):
        """Test that getting behavior when no behaviors are stored returns None."""
        result = collision_state.get_behavior("obj1")
        
        assert result is None

    # Frame Management Tests
    def test_add_frame_id_with_valid_data_stores_frame(self, collision_state):
        """Test that adding frame ID with valid data stores the frame correctly."""
        frame_id = "frame1"
        timestamp = 1234567890
        
        collision_state.add_frame_id(frame_id, timestamp)
        
        assert (frame_id, timestamp) in collision_state.frame_ids

    def test_add_frame_id_with_multiple_frames_stores_all(self, collision_state):
        """Test that adding multiple frame IDs stores all frames correctly."""
        frames = [("frame1", 100), ("frame2", 200), ("frame3", 150)]
        
        for frame_id, timestamp in frames:
            collision_state.add_frame_id(frame_id, timestamp)
        
        assert len(collision_state.frame_ids) == 3
        for frame_tuple in frames:
            assert frame_tuple in collision_state.frame_ids

    def test_get_frame_ids_with_multiple_frames_returns_sorted_by_timestamp(self, collision_state):
        """Test that getting frame IDs returns them sorted by timestamp."""
        frames = [("frame1", 200), ("frame2", 100), ("frame3", 300)]
        
        for frame_id, timestamp in frames:
            collision_state.add_frame_id(frame_id, timestamp)
        
        sorted_frames = collision_state.get_frame_ids()
        assert sorted_frames == ["frame2", "frame1", "frame3"]

    def test_get_frame_ids_with_no_frames_returns_empty_list(self, collision_state):
        """Test that getting frame IDs when no frames exist returns empty list."""
        result = collision_state.get_frame_ids()
        
        assert result == []

    def test_get_frame_ids_with_same_timestamps_maintains_order(self, collision_state):
        """Test that getting frame IDs with same timestamps maintains consistent order."""
        frames = [("frame1", 100), ("frame2", 100), ("frame3", 100)]
        
        for frame_id, timestamp in frames:
            collision_state.add_frame_id(frame_id, timestamp)
        
        sorted_frames = collision_state.get_frame_ids()
        assert len(sorted_frames) == 3
        assert all(frame in sorted_frames for frame in ["frame1", "frame2", "frame3"])

    # Time Management Tests
    def test_reset_time_updates_start_time_to_current(self):
        """Test that reset_time updates start_time to current time."""
        initial_time = datetime.now() - timedelta(seconds=5)
        collision_state = CollisionState(
            ["obj1"], "sensor1", Place(name="test_place"), Location(lat=1.0, lon=2.0, alt=3.0),
            initial_time, trigger_modules=[]
        )
        
        # Mock datetime.now to return a time later than initial_time
        later_time = initial_time + timedelta(seconds=1)
        with patch('mdx.analytics.core.schema.collision.collision_state.datetime') as mock_datetime:
            mock_datetime.now.return_value = later_time
            collision_state.reset_time()
        
        assert collision_state.start_time == later_time
        assert collision_state.start_time > initial_time

    def test_is_timeout_with_recent_start_time_returns_false(self):
        """Test that is_timeout returns False for recently started collision state."""
        collision_state = CollisionState(
            ["obj1"], "sensor1", Place(name="test_place"), 
            Location(lat=1.0, lon=2.0, alt=3.0),
            datetime.now(), trigger_modules=[], timeout=10
        )
        
        result = collision_state.is_timeout()
        
        assert result is False

    def test_is_timeout_with_expired_start_time_returns_true(self):
        """Test that is_timeout returns True for expired collision state."""
        expired_start_time = datetime.now() - timedelta(seconds=20)
        collision_state = CollisionState(
            ["obj1"], "sensor1", Place(name="test_place"), 
            Location(lat=1.0, lon=2.0, alt=3.0),
            expired_start_time, trigger_modules=[], timeout=10
        )
        
        result = collision_state.is_timeout()
        
        assert result is True

    def test_is_timeout_with_exactly_timeout_duration_returns_true(self):
        """Test that is_timeout returns True when exactly at timeout duration."""
        timeout_duration = 5
        exact_timeout_time = datetime.now() - timedelta(seconds=timeout_duration + 0.1)
        collision_state = CollisionState(
            ["obj1"], "sensor1", Place(name="test_place"), 
            Location(lat=1.0, lon=2.0, alt=3.0),
            exact_timeout_time, trigger_modules=[], timeout=timeout_duration
        )
        
        result = collision_state.is_timeout()
        
        assert result is True

    def test_is_timeout_with_custom_timeout_respects_setting(self):
        """Test that is_timeout respects custom timeout setting."""
        custom_timeout = 2
        expired_time = datetime.now() - timedelta(seconds=custom_timeout + 1)
        collision_state = CollisionState(
            ["obj1"], "sensor1", Place(name="test_place"), 
            Location(lat=1.0, lon=2.0, alt=3.0),
            expired_time, trigger_modules=[], timeout=custom_timeout
        )
        
        result = collision_state.is_timeout()
        
        assert result is True

