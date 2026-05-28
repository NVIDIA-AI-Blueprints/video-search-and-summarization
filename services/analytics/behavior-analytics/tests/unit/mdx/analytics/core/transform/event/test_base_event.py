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
from datetime import datetime
from enum import Enum
from unittest.mock import Mock, patch

from mdx.analytics.core.transform.event.base_event import BaseEvent, HasId
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import (
    Behavior, Point2D, Sensor, Object, Place, GeoLocation, Point
)
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase


class DirectionEnum(Enum):
    """Mock direction enum for testing"""
    IN = "in"
    OUT = "out"


class MockObject(HasId):
    """Mock object that implements HasId protocol"""
    def __init__(self, obj_id: str):
        self.id = obj_id


class TestableBaseEvent(BaseEvent[MockObject]):
    """Concrete implementation of BaseEvent for testing"""
    
    def __init__(self, config: AppConfig, calibration: CalibrationBase, 
                 direction_enum=DirectionEnum, event_name="test_event", event_type="test_type"):
        super().__init__(config, calibration, direction_enum, event_name, event_type)
        self._mock_objects = []
        self._check_point_results = {}
        self._intersect_results = {}
    
    def set_mock_objects(self, objects: list[MockObject]):
        """Set mock objects for testing"""
        self._mock_objects = objects
    
    def set_check_point_result(self, point: Point2D, sensor_id: str, obj_id: str, result: bool):
        """Set result for _check_point method"""
        key = (point.x, point.y, sensor_id, obj_id)
        self._check_point_results[key] = result
    
    def set_intersect_result(self, sensor_id: str, obj_id: str, result: bool):
        """Set result for _intersect method"""
        key = (sensor_id, obj_id)
        self._intersect_results[key] = result
    
    def _check_point(self, point: Point2D, sensor_id: str, obj_id: str) -> bool:
        """Mock implementation of _check_point"""
        key = (point.x, point.y, sensor_id, obj_id)
        return self._check_point_results.get(key, False)
    
    def _get_objects(self, sensor_id: str) -> list[MockObject]:
        """Mock implementation of _get_objects"""
        return self._mock_objects
    
    def _intersect(self, trip: list[Point2D], sensor_id: str, obj_id: str) -> bool:
        """Mock implementation of _intersect"""
        key = (sensor_id, obj_id)
        return self._intersect_results.get(key, False)


class TestBaseEventFunctionality:
    """Test suite for BaseEvent core functionality."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.mock_calibration.sensor_map = {"sensor1": Mock()}
        
        # Setup default return values
        self.mock_config.sensor_tripwire_min_points.return_value = 5
        
        self.base_event = TestableBaseEvent(
            self.mock_config, 
            self.mock_calibration,
            DirectionEnum,
            "crossing",
            "tripwire"
        )
    
    def create_mock_behavior(self, sensor_id="sensor1", length=10, locations=None):
        """Helper method to create mock behavior object"""
        behavior = Mock(spec=Behavior)
        behavior.sensor = Mock(spec=Sensor)
        behavior.sensor.id = sensor_id
        behavior.length = length
        behavior.timestamp = datetime.now()
        behavior.timeInterval = 1.0  # 1 second total time interval
        behavior.speed = 2.5
        behavior.speedOverTime = [2.0, 2.5, 3.0]
        behavior.bearing = 45.0
        behavior.direction = "NE"
        behavior.distance = 15.0
        behavior.id = "behavior1"
        behavior.object = Mock(spec=Object)
        behavior.place = Mock(spec=Place)
        
        if locations is None:
            # Create default locations
            coordinates = [
                Point(point=[i * 10.0, i * 5.0]) for i in range(length)
            ]
            geo_location = Mock(spec=GeoLocation)
            geo_location.coordinates = coordinates
            behavior.locations = geo_location
        else:
            behavior.locations = locations
            
        return behavior
    
    def test_initialization(self):
        """Test BaseEvent initialization."""
        config = Mock(spec=AppConfig)
        calibration = Mock(spec=CalibrationBase)
        direction_enum = DirectionEnum
        event_name = "test_event"
        event_type = "test_type"
        
        base_event = TestableBaseEvent(config, calibration, direction_enum, event_name, event_type)
        
        assert base_event.config == config
        assert base_event.calibration == calibration
        assert base_event.direction_enum == direction_enum
        assert base_event.event_name == event_name
        assert base_event.event_type == event_type
    
    def test_abstract_methods_raise_not_implemented(self):
        """Test that abstract methods raise NotImplementedError in base class."""
        config = Mock(spec=AppConfig)
        calibration = Mock(spec=CalibrationBase)
        
        # Create base class instance (not the testable subclass)
        base_event = BaseEvent(config, calibration, DirectionEnum, "event", "type")
        
        point = Point2D(x=10, y=20)
        
        with pytest.raises(NotImplementedError, match="_check_point is not implemented"):
            base_event._check_point(point, "sensor1", "obj1")
            
        with pytest.raises(NotImplementedError, match="_get_objects is not implemented"):
            base_event._get_objects("sensor1")
            
        with pytest.raises(NotImplementedError, match="_intersect is not implemented"):
            base_event._intersect([point], "sensor1", "obj1")


class TestGetEventsFunctionality:
    """Test suite for get_events method functionality."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.mock_calibration.sensor_map = {"sensor1": Mock()}
        
        # Setup default return values
        self.mock_config.sensor_tripwire_min_points.return_value = 5
        
        self.base_event = TestableBaseEvent(
            self.mock_config, 
            self.mock_calibration,
            DirectionEnum,
            "crossing",
            "tripwire"
        )
    
    def create_mock_behavior(self, sensor_id="sensor1", length=10, locations=None):
        """Helper method to create mock behavior object"""
        behavior = Mock(spec=Behavior)
        behavior.sensor = Mock(spec=Sensor)
        behavior.sensor.id = sensor_id
        behavior.length = length
        behavior.timestamp = datetime.now()
        behavior.timeInterval = 1.0  # 1 second total time interval
        behavior.speed = 2.5
        behavior.speedOverTime = [2.0, 2.5, 3.0]
        behavior.bearing = 45.0
        behavior.direction = "NE"
        behavior.distance = 15.0
        behavior.id = "behavior1"
        behavior.object = Mock(spec=Object)
        behavior.place = Mock(spec=Place)
        
        if locations is None:
            # Create default locations
            coordinates = [
                Point(point=[i * 10.0, i * 5.0]) for i in range(length)
            ]
            geo_location = Mock(spec=GeoLocation)
            geo_location.coordinates = coordinates
            behavior.locations = geo_location
        else:
            behavior.locations = locations
            
        return behavior
    
    def test_get_events_with_none_behavior(self):
        """Test get_events returns empty list when behavior is None."""
        result = self.base_event.get_events(None)
        assert result == []
    
    def test_get_events_sensor_not_in_calibration_map(self):
        """Test get_events returns empty list when sensor not in calibration map."""
        behavior = self.create_mock_behavior(sensor_id="unknown_sensor")
        result = self.base_event.get_events(behavior)
        assert result == []
    
    def test_get_events_no_objects_for_sensor(self):
        """Test get_events returns empty list when no objects for sensor."""
        behavior = self.create_mock_behavior()
        # Don't set any mock objects
        result = self.base_event.get_events(behavior)
        assert result == []
    
    def test_get_events_behavior_too_short(self):
        """Test get_events returns empty list when behavior length < 2."""
        behavior = self.create_mock_behavior(length=1)
        self.base_event.set_mock_objects([MockObject("obj1")])
        result = self.base_event.get_events(behavior)
        assert result == []
    
    @patch('mdx.analytics.core.utils.schema_util.point_list_to_geo_location')
    def test_get_events_successful_detection(self, mock_point_list_to_geo_location):
        """Test successful event detection."""
        # Setup
        behavior = self.create_mock_behavior(length=10)
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        # Mock return value
        mock_geo_location = Mock(spec=GeoLocation)
        mock_point_list_to_geo_location.return_value = mock_geo_location
        
        # Set up point checking to create even distribution
        min_trip_length = 10  # 5 * 2
        for i in range(min_trip_length):
            point = Point2D(x=i * 10.0, y=i * 5.0)
            # Half points inside, half outside for even distribution
            inside = i < min_trip_length // 2
            self.base_event.set_check_point_result(point, "sensor1", "obj1", inside)
        
        # Set intersection to true
        self.base_event.set_intersect_result("sensor1", "obj1", True)
        
        # Execute
        result = self.base_event.get_events(behavior)
        
        # Verify
        assert len(result) == 1
        event_behavior = result[0]
        assert event_behavior.id == behavior.id
        assert event_behavior.event.id == "obj1"
        assert event_behavior.event.type == DirectionEnum.OUT.value  # First point is inside
        assert event_behavior.event.info == {"class": "crossing"}
        assert event_behavior.analyticsModule.id == "tripwire"
        assert "index = 0" in event_behavior.analyticsModule.description
    
    @patch('mdx.analytics.core.utils.schema_util.point_list_to_geo_location')
    def test_get_events_direction_in(self, mock_point_list_to_geo_location):
        """Test event detection with IN direction."""
        # Setup
        behavior = self.create_mock_behavior(length=10)
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        # Mock return value
        mock_geo_location = Mock(spec=GeoLocation)
        mock_point_list_to_geo_location.return_value = mock_geo_location
        
        # Set up point checking - first point outside for IN direction
        min_trip_length = 10  # 5 * 2
        for i in range(min_trip_length):
            point = Point2D(x=i * 10.0, y=i * 5.0)
            # First point outside, then half inside for even distribution
            inside = i >= min_trip_length // 2
            self.base_event.set_check_point_result(point, "sensor1", "obj1", inside)
        
        # Set intersection to true
        self.base_event.set_intersect_result("sensor1", "obj1", True)
        
        # Execute
        result = self.base_event.get_events(behavior)
        
        # Verify
        assert len(result) == 1
        event_behavior = result[0]
        assert event_behavior.event.type == DirectionEnum.IN.value  # First point is outside
    
    def test_get_events_no_intersection(self):
        """Test get_events when trip doesn't intersect with object."""
        behavior = self.create_mock_behavior(length=10)
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        # Set up even distribution but no intersection
        min_trip_length = 10
        for i in range(min_trip_length):
            point = Point2D(x=i * 10.0, y=i * 5.0)
            inside = i < min_trip_length // 2
            self.base_event.set_check_point_result(point, "sensor1", "obj1", inside)
        
        # Set intersection to false
        self.base_event.set_intersect_result("sensor1", "obj1", False)
        
        result = self.base_event.get_events(behavior)
        assert result == []
    
    def test_get_events_uneven_distribution(self):
        """Test get_events when points are not evenly distributed."""
        behavior = self.create_mock_behavior(length=10)
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        # Set up uneven distribution (more points inside than outside)
        min_trip_length = 10
        for i in range(min_trip_length):
            point = Point2D(x=i * 10.0, y=i * 5.0)
            inside = i < 7  # 7 inside, 3 outside - uneven
            self.base_event.set_check_point_result(point, "sensor1", "obj1", inside)
        
        # Set intersection to true
        self.base_event.set_intersect_result("sensor1", "obj1", True)
        
        result = self.base_event.get_events(behavior)
        assert result == []  # Should be empty due to uneven distribution
    
    @patch('mdx.analytics.core.utils.schema_util.point_list_to_geo_location')
    def test_get_events_multiple_objects(self, mock_point_list_to_geo_location):
        """Test get_events with multiple objects."""
        behavior = self.create_mock_behavior(length=10)
        mock_obj1 = MockObject("obj1")
        mock_obj2 = MockObject("obj2")
        self.base_event.set_mock_objects([mock_obj1, mock_obj2])
        
        # Mock return value
        mock_geo_location = Mock(spec=GeoLocation)
        mock_point_list_to_geo_location.return_value = mock_geo_location
        
        # Set up even distribution for both objects
        min_trip_length = 10
        for i in range(min_trip_length):
            point = Point2D(x=i * 10.0, y=i * 5.0)
            inside = i < min_trip_length // 2
            self.base_event.set_check_point_result(point, "sensor1", "obj1", inside)
            self.base_event.set_check_point_result(point, "sensor1", "obj2", inside)
        
        # Set intersection to true for both
        self.base_event.set_intersect_result("sensor1", "obj1", True)
        self.base_event.set_intersect_result("sensor1", "obj2", True)
        
        result = self.base_event.get_events(behavior)
        assert len(result) == 2
        assert result[0].event.id == "obj1"
        assert result[1].event.id == "obj2"
    
    @patch('mdx.analytics.core.utils.schema_util.point_list_to_geo_location')
    def test_get_events_multiple_tracklets(self, mock_point_list_to_geo_location):
        """Test get_events with behavior longer than min_trip_length."""
        behavior = self.create_mock_behavior(length=15)  # Longer than min_trip_length
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        # Mock return value
        mock_geo_location = Mock(spec=GeoLocation)
        mock_point_list_to_geo_location.return_value = mock_geo_location
        
        # Set up even distribution for all points
        for i in range(15):
            point = Point2D(x=i * 10.0, y=i * 5.0)
            inside = i % 2 == 0  # Alternating pattern
            self.base_event.set_check_point_result(point, "sensor1", "obj1", inside)
        
        # Set intersection to true
        self.base_event.set_intersect_result("sensor1", "obj1", True)
        
        result = self.base_event.get_events(behavior)
        # Should have multiple events from different tracklets
        assert len(result) >= 1
    
    def test_get_events_timestamp_calculation(self):
        """Test timestamp calculation in events."""
        behavior = self.create_mock_behavior(length=10)
        behavior.timestamp = datetime(2024, 1, 1, 12, 0, 0)
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        # Set up even distribution
        min_trip_length = 10
        for i in range(min_trip_length):
            point = Point2D(x=i * 10.0, y=i * 5.0)
            inside = i < min_trip_length // 2
            self.base_event.set_check_point_result(point, "sensor1", "obj1", inside)
        
        # Set intersection to true
        self.base_event.set_intersect_result("sensor1", "obj1", True)
        
        with patch('mdx.analytics.core.utils.schema_util.point_list_to_geo_location'):
            result = self.base_event.get_events(behavior)
        
        assert len(result) == 1
        event_behavior = result[0]
        # Check that timestamp is calculated correctly (should be same as behavior timestamp for first tracklet)
        assert event_behavior.timestamp == behavior.timestamp


class TestGetEventsEdgeCases:
    """Test suite for edge cases in get_events method."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.mock_calibration.sensor_map = {"sensor1": Mock()}
        
        # Setup default return values
        self.mock_config.sensor_tripwire_min_points.return_value = 5
        
        self.base_event = TestableBaseEvent(
            self.mock_config, 
            self.mock_calibration,
            DirectionEnum,
            "crossing",
            "tripwire"
        )
    
    def create_mock_behavior(self, sensor_id="sensor1", length=10, locations=None):
        """Helper method to create mock behavior object"""
        behavior = Mock(spec=Behavior)
        behavior.sensor = Mock(spec=Sensor)
        behavior.sensor.id = sensor_id
        behavior.length = length
        behavior.timestamp = datetime.now()
        behavior.timeInterval = 1.0  # 1 second total time interval
        behavior.speed = 2.5
        behavior.speedOverTime = [2.0, 2.5, 3.0]
        behavior.bearing = 45.0
        behavior.direction = "NE"
        behavior.distance = 15.0
        behavior.id = "behavior1"
        behavior.object = Mock(spec=Object)
        behavior.place = Mock(spec=Place)
        
        if locations is None:
            # Create default locations
            coordinates = [
                Point(point=[i * 10.0, i * 5.0]) for i in range(length)
            ]
            geo_location = Mock(spec=GeoLocation)
            geo_location.coordinates = coordinates
            behavior.locations = geo_location
        else:
            behavior.locations = locations
            
        return behavior
    
    def test_get_events_behavior_length_equals_min_required(self):
        """Test get_events when behavior length equals minimum required."""
        behavior = self.create_mock_behavior(length=2)  # Minimum length
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        # Set up check for exact minimum length
        for i in range(2):
            point = Point2D(x=i * 10.0, y=i * 5.0)
            inside = i == 0  # First inside, second outside
            self.base_event.set_check_point_result(point, "sensor1", "obj1", inside)
        
        result = self.base_event.get_events(behavior)
        # Should return empty because min_trip_length (10) > behavior.length (2)
        assert result == []
    
    def test_get_events_with_none_locations(self):
        """Test get_events when behavior.locations is None."""
        behavior = self.create_mock_behavior(locations=None)
        behavior.locations = None
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        # This should raise an AttributeError when trying to access coordinates
        with pytest.raises(AttributeError):
            self.base_event.get_events(behavior)
    
    def test_get_events_empty_coordinates(self):
        """Test get_events when behavior has empty coordinates."""
        behavior = self.create_mock_behavior()
        geo_location = Mock(spec=GeoLocation)
        geo_location.coordinates = []  # Empty coordinates
        behavior.locations = geo_location
        behavior.length = 0
        
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        result = self.base_event.get_events(behavior)
        assert result == []
    
    def test_get_events_with_very_small_min_trip_length(self):
        """Test get_events with very small min_trip_length."""
        self.mock_config.sensor_tripwire_min_points.return_value = 1  # Minimum possible
        behavior = self.create_mock_behavior(length=5)
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        min_trip_length = 2  # 1 * 2
        
        # Set up even distribution
        for i in range(5):
            point = Point2D(x=i * 10.0, y=i * 5.0)
            inside = i < 1  # Only first point inside
            self.base_event.set_check_point_result(point, "sensor1", "obj1", inside)
        
        self.base_event.set_intersect_result("sensor1", "obj1", True)
        
        with patch('mdx.analytics.core.utils.schema_util.point_list_to_geo_location'):
            result = self.base_event.get_events(behavior)
            # Should have multiple tracklets due to small min_trip_length
            assert len(result) >= 1
    
    def test_get_events_all_points_inside(self):
        """Test get_events when all points are inside the object."""
        behavior = self.create_mock_behavior(length=10)
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        # All points inside
        min_trip_length = 10
        for i in range(min_trip_length):
            point = Point2D(x=i * 10.0, y=i * 5.0)
            self.base_event.set_check_point_result(point, "sensor1", "obj1", True)
        
        self.base_event.set_intersect_result("sensor1", "obj1", True)
        
        result = self.base_event.get_events(behavior)
        # Should be empty because side2 (outside points) = 0, side1 (inside points) = 10
        # Not evenly distributed
        assert result == []
    
    def test_get_events_all_points_outside(self):
        """Test get_events when all points are outside the object."""
        behavior = self.create_mock_behavior(length=10)
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        # All points outside
        min_trip_length = 10
        for i in range(min_trip_length):
            point = Point2D(x=i * 10.0, y=i * 5.0)
            self.base_event.set_check_point_result(point, "sensor1", "obj1", False)
        
        self.base_event.set_intersect_result("sensor1", "obj1", True)
        
        result = self.base_event.get_events(behavior)
        # Should be empty because side1 (inside points) = 0, side2 (outside points) = 10
        # Not evenly distributed
        assert result == []


class TestGetEventsErrorHandling:
    """Test suite for error handling in get_events method."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.mock_calibration.sensor_map = {"sensor1": Mock()}
        
        # Setup default return values
        self.mock_config.sensor_tripwire_min_points.return_value = 5
        
        self.base_event = TestableBaseEvent(
            self.mock_config, 
            self.mock_calibration,
            DirectionEnum,
            "crossing",
            "tripwire"
        )
    
    def create_mock_behavior(self, sensor_id="sensor1", length=10):
        """Helper method to create mock behavior object"""
        behavior = Mock(spec=Behavior)
        behavior.sensor = Mock(spec=Sensor)
        behavior.sensor.id = sensor_id
        behavior.length = length
        behavior.timestamp = datetime.now()
        behavior.timeInterval = 1.0  # 1 second total time interval
        behavior.speed = 2.5
        behavior.speedOverTime = [2.0, 2.5, 3.0]
        behavior.bearing = 45.0
        behavior.direction = "NE"
        behavior.distance = 15.0
        behavior.id = "behavior1"
        behavior.object = Mock(spec=Object)
        behavior.place = Mock(spec=Place)
        
        # Create default locations
        coordinates = [
            Point(point=[i * 10.0, i * 5.0]) for i in range(length)
        ]
        geo_location = Mock(spec=GeoLocation)
        geo_location.coordinates = coordinates
        behavior.locations = geo_location
            
        return behavior
    
    def test_get_events_config_method_raises_exception(self):
        """Test get_events when config method raises exception."""
        behavior = self.create_mock_behavior()
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        # Make config method raise exception
        self.mock_config.sensor_tripwire_min_points.side_effect = Exception("Config error")
        
        with pytest.raises(Exception, match="Config error"):
            self.base_event.get_events(behavior)
    
    def test_get_events_abstract_method_not_implemented(self):
        """Test get_events when abstract methods are not properly implemented."""
        # Create a base event instance that doesn't implement abstract methods
        config = Mock(spec=AppConfig)
        calibration = Mock(spec=CalibrationBase) 
        calibration.sensor_map = {"sensor1": Mock()}
        config.sensor_tripwire_min_points.return_value = 5
        
        # Use the base class directly (not the testable subclass)
        base_event = BaseEvent(config, calibration, DirectionEnum, "event", "type")
        
        behavior = self.create_mock_behavior()
        
        # This should raise NotImplementedError when trying to call _get_objects
        with pytest.raises(NotImplementedError, match="_get_objects is not implemented"):
            base_event.get_events(behavior)
    
    def test_get_events_malformed_point_coordinates(self):
        """Test get_events with malformed point coordinates."""
        behavior = self.create_mock_behavior()
        
        # Create malformed coordinates (missing or invalid data)
        malformed_coordinates = [
            Point(point=[10.0]),  # Missing y coordinate
            Point(point=[]),       # Empty coordinates
            Point(point=[20.0, 30.0, 40.0])  # Extra coordinate
        ]
        geo_location = Mock(spec=GeoLocation)
        geo_location.coordinates = malformed_coordinates
        behavior.locations = geo_location
        behavior.length = 3
        
        mock_obj = MockObject("obj1")
        self.base_event.set_mock_objects([mock_obj])
        
        # This should raise an IndexError when trying to access point[1]
        with pytest.raises(IndexError):
            self.base_event.get_events(behavior)


class TestBaseEventIntegration:
    """Integration tests for BaseEvent with realistic scenarios."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.mock_calibration.sensor_map = {"sensor1": Mock(), "sensor2": Mock()}
        
        # Setup realistic return values
        self.mock_config.sensor_tripwire_min_points.return_value = 3
        
        self.base_event = TestableBaseEvent(
            self.mock_config, 
            self.mock_calibration,
            DirectionEnum,
            "person_crossing",
            "tripwire_detection"
        )
    
    def create_realistic_behavior(self, sensor_id="sensor1", num_points=20):
        """Create a realistic behavior with actual trajectory."""
        behavior = Mock(spec=Behavior)
        behavior.sensor = Mock(spec=Sensor)
        behavior.sensor.id = sensor_id
        behavior.length = num_points
        behavior.timestamp = datetime(2024, 1, 15, 14, 30, 0)
        behavior.timeInterval = 2.0  # 2 seconds total time interval
        behavior.speed = 1.5  # m/s
        behavior.speedOverTime = [1.2, 1.5, 1.8, 1.5, 1.3]
        behavior.bearing = 85.0  # Nearly east
        behavior.direction = "E"
        behavior.distance = 25.0
        behavior.id = f"behavior_{sensor_id}_{int(behavior.timestamp.timestamp())}"
        behavior.object = Mock(spec=Object)
        behavior.object.id = "person_123"
        behavior.object.type = "person"
        behavior.place = Mock(spec=Place)
        behavior.place.name = "Main Entrance"
        
        # Create realistic trajectory (person walking east, crossing a north-south tripwire)
        coordinates = []
        for i in range(num_points):
            x = 100.0 + i * 2.0  # Moving east
            y = 200.0 + (i % 3 - 1) * 0.5  # Slight north-south variation
            coordinates.append(Point(point=[x, y]))
        
        geo_location = Mock(spec=GeoLocation)
        geo_location.coordinates = coordinates
        behavior.locations = geo_location
        
        return behavior
    
    @patch('mdx.analytics.core.utils.schema_util.point_list_to_geo_location')
    def test_realistic_person_crossing_tripwire(self, mock_point_list_to_geo_location):
        """Test realistic scenario of person crossing a tripwire."""
        behavior = self.create_realistic_behavior(num_points=20)
        tripwire = MockObject("tripwire_main_entrance")
        self.base_event.set_mock_objects([tripwire])
        
        # Mock return value
        mock_geo_location = Mock(spec=GeoLocation)
        mock_point_list_to_geo_location.return_value = mock_geo_location
        
        # Simulate tripwire crossing (person starts outside, crosses into tripwire zone)
        tripwire_x_center = 110.0  # Tripwire at x=110
        min_trip_length = 6  # 3 * 2
        
        for i in range(20):
            point = Point2D(x=100.0 + i * 2.0, y=200.0 + (i % 3 - 1) * 0.5)
            # Person crosses tripwire when x >= 110
            inside_tripwire = point.x >= tripwire_x_center
            self.base_event.set_check_point_result(point, "sensor1", "tripwire_main_entrance", inside_tripwire)
        
        # Set intersection to true (trajectory crosses tripwire)
        self.base_event.set_intersect_result("sensor1", "tripwire_main_entrance", True)
        
        result = self.base_event.get_events(behavior)
        
        # Should detect crossing event
        assert len(result) >= 1
        crossing_event = result[0]
        assert crossing_event.event.id == "tripwire_main_entrance"
        assert crossing_event.event.type in [DirectionEnum.IN.value, DirectionEnum.OUT.value]
        assert crossing_event.event.info["class"] == "person_crossing"
        assert crossing_event.analyticsModule.id == "tripwire_detection"
        
        # Verify timing
        assert crossing_event.timestamp >= behavior.timestamp
        assert crossing_event.end > crossing_event.timestamp
    
    def test_multiple_sensors_different_configurations(self):
        """Test behavior with multiple sensors having different configurations."""
        # Configure different sensors
        def sensor_config_side_effect(sensor_id):
            if sensor_id == "sensor1":
                return 2  # Lower threshold
            elif sensor_id == "sensor2":
                return 5  # Higher threshold
            return 3  # Default
        
        self.mock_config.sensor_tripwire_min_points.side_effect = sensor_config_side_effect
        
        # Test with sensor1 (low threshold)
        behavior1 = self.create_realistic_behavior(sensor_id="sensor1", num_points=8)
        tripwire1 = MockObject("tripwire_sensor1")
        
        base_event1 = TestableBaseEvent(
            self.mock_config, self.mock_calibration, DirectionEnum, "crossing", "tripwire"
        )
        base_event1.set_mock_objects([tripwire1])
        
        # Should work with shorter behavior due to lower threshold
        result1 = base_event1.get_events(behavior1)
        # Result depends on the specific setup, but should not crash
        
        # Test with sensor2 (high threshold)
        behavior2 = self.create_realistic_behavior(sensor_id="sensor2", num_points=8)
        tripwire2 = MockObject("tripwire_sensor2")
        
        base_event2 = TestableBaseEvent(
            self.mock_config, self.mock_calibration, DirectionEnum, "crossing", "tripwire"
        )
        base_event2.set_mock_objects([tripwire2])
        
        # Might not work with same length behavior due to higher threshold
        result2 = base_event2.get_events(behavior2)
        # Should be empty due to higher threshold (min_trip_length = 10 > behavior.length = 8)
        assert result2 == []


if __name__ == "__main__":
    pytest.main([__file__])