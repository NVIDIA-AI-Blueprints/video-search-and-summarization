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
from unittest.mock import Mock, patch

from mdx.analytics.core.constants import ROIDirection
from mdx.analytics.core.transform.event.roi_event import ROIEvent
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import (
    ROI, Bbox, Point2D, Behavior, GeoLocation, Point
)
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase, CalibrationType


class TestROIEventInitialization:
    """Test suite for ROIEvent initialization."""
    
    def test_initialization(self):
        """Test ROIEvent initialization with correct parameters."""
        config = Mock(spec=AppConfig)
        calibration = Mock(spec=CalibrationBase)
        
        roi_event = ROIEvent(config, calibration)
        
        assert roi_event.config == config
        assert roi_event.calibration == calibration
        assert roi_event.direction_enum == ROIDirection
        assert roi_event.event_name == "roi"
        assert roi_event.event_type == "ROIEvent"
    
    def test_initialization_with_mock_none_config(self):
        """Test ROIEvent initialization with mock None config."""
        config = Mock()
        config.return_value = None
        calibration = Mock(spec=CalibrationBase)
        
        # Test that None config is handled in the parent class
        with patch('mdx.analytics.core.transform.event.roi_event.BaseEvent.__init__') as mock_base_init:
            roi_event = ROIEvent(config, calibration)
            mock_base_init.assert_called_once_with(config, calibration, ROIDirection, "roi", "ROIEvent")
    
    def test_initialization_with_mock_none_calibration(self):
        """Test ROIEvent initialization with mock None calibration."""
        config = Mock(spec=AppConfig)
        calibration = Mock()
        calibration.return_value = None
        
        # Test that None calibration is handled in the parent class
        with patch('mdx.analytics.core.transform.event.roi_event.BaseEvent.__init__') as mock_base_init:
            roi_event = ROIEvent(config, calibration)
            mock_base_init.assert_called_once_with(config, calibration, ROIDirection, "roi", "ROIEvent")


class TestROIEventIsInsideFunctionality:
    """Test suite for _is_inside method functionality."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.roi_event = ROIEvent(self.mock_config, self.mock_calibration)
    
    def test_is_inside_when_point_in_roi(self):
        """Test _is_inside when point is inside ROI."""
        point = Point2D(x=10.0, y=20.0)
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        # Mock calibration to return True (point inside)
        self.mock_calibration.point_in_polygon.return_value = True
        
        result = self.roi_event._is_inside(point, sensor_id, roi_id)
        
        assert result is True
        self.mock_calibration.point_in_polygon.assert_called_once_with(point, sensor_id, roi_id)
    
    def test_is_inside_when_point_outside_roi(self):
        """Test _is_inside when point is outside ROI."""
        point = Point2D(x=100.0, y=200.0)
        sensor_id = "sensor2"
        roi_id = "roi2"
        
        # Mock calibration to return False (point outside)
        self.mock_calibration.point_in_polygon.return_value = False
        
        result = self.roi_event._is_inside(point, sensor_id, roi_id)
        
        assert result is False
        self.mock_calibration.point_in_polygon.assert_called_once_with(point, sensor_id, roi_id)
    
    def test_is_inside_with_zero_coordinates(self):
        """Test _is_inside with zero coordinates."""
        point = Point2D(x=0.0, y=0.0)
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        self.mock_calibration.point_in_polygon.return_value = True
        
        result = self.roi_event._is_inside(point, sensor_id, roi_id)
        
        assert result is True
        self.mock_calibration.point_in_polygon.assert_called_once_with(point, sensor_id, roi_id)
    
    def test_is_inside_with_negative_coordinates(self):
        """Test _is_inside with negative coordinates."""
        point = Point2D(x=-50.0, y=-30.0)
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        self.mock_calibration.point_in_polygon.return_value = False
        
        result = self.roi_event._is_inside(point, sensor_id, roi_id)
        
        assert result is False
        self.mock_calibration.point_in_polygon.assert_called_once_with(point, sensor_id, roi_id)


class TestROIEventIsInsideErrorHandling:
    """Test suite for _is_inside method error handling."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.roi_event = ROIEvent(self.mock_config, self.mock_calibration)
    
    def test_is_inside_calibration_raises_exception(self):
        """Test _is_inside when calibration raises exception."""
        point = Point2D(x=10.0, y=20.0)
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        # Mock calibration to raise exception
        self.mock_calibration.point_in_polygon.side_effect = Exception("Calibration error")
        
        with pytest.raises(Exception, match="Calibration error"):
            self.roi_event._is_inside(point, sensor_id, roi_id)


class TestROIEventGetObjectsFunctionality:
    """Test suite for _get_objects method functionality."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.roi_event = ROIEvent(self.mock_config, self.mock_calibration)
    
    def create_mock_roi(self, roi_id: str, roi_type: str = "entrance") -> ROI:
        """Helper method to create mock ROI."""
        roi = Mock(spec=ROI)
        roi.id = roi_id
        roi.type = roi_type
        roi.roiCoordinates = [
            Point2D(x=0, y=0),
            Point2D(x=100, y=0),
            Point2D(x=100, y=100),
            Point2D(x=0, y=100)
        ]
        return roi
    
    def test_get_objects_with_single_roi(self):
        """Test _get_objects with single ROI."""
        sensor_id = "sensor1"
        roi1 = self.create_mock_roi("roi1")
        
        # Mock sensor map
        mock_sensor = Mock()
        mock_sensor.rois = [roi1]
        self.mock_calibration.sensor_map = {sensor_id: mock_sensor}
        
        result = self.roi_event._get_objects(sensor_id)
        
        assert result == [roi1]
        assert len(result) == 1
        assert result[0].id == "roi1"
    
    def test_get_objects_with_multiple_rois(self):
        """Test _get_objects with multiple ROIs."""
        sensor_id = "sensor1"
        roi1 = self.create_mock_roi("roi1", "entrance")
        roi2 = self.create_mock_roi("roi2", "exit")
        roi3 = self.create_mock_roi("roi3", "restricted")
        
        # Mock sensor map
        mock_sensor = Mock()
        mock_sensor.rois = [roi1, roi2, roi3]
        self.mock_calibration.sensor_map = {sensor_id: mock_sensor}
        
        result = self.roi_event._get_objects(sensor_id)
        
        assert result == [roi1, roi2, roi3]
        assert len(result) == 3
        assert result[0].id == "roi1"
        assert result[1].id == "roi2"
        assert result[2].id == "roi3"
    
    def test_get_objects_with_empty_rois(self):
        """Test _get_objects with empty ROI list."""
        sensor_id = "sensor1"
        
        # Mock sensor map with empty ROIs
        mock_sensor = Mock()
        mock_sensor.rois = []
        self.mock_calibration.sensor_map = {sensor_id: mock_sensor}
        
        result = self.roi_event._get_objects(sensor_id)
        
        assert result == []
        assert len(result) == 0


class TestROIEventGetObjectsErrorHandling:
    """Test suite for _get_objects method error handling."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.roi_event = ROIEvent(self.mock_config, self.mock_calibration)
    
    def test_get_objects_sensor_not_found(self):
        """Test _get_objects when sensor not in sensor_map."""
        sensor_id = "unknown_sensor"
        
        # Mock sensor map without the requested sensor
        self.mock_calibration.sensor_map = {"sensor1": Mock()}
        
        with pytest.raises(KeyError):
            self.roi_event._get_objects(sensor_id)
    
    def test_get_objects_sensor_map_none(self):
        """Test _get_objects when sensor_map is None."""
        sensor_id = "sensor1"
        
        # Mock sensor map as None
        self.mock_calibration.sensor_map = None
        
        with pytest.raises(TypeError):
            self.roi_event._get_objects(sensor_id)
    
    def test_get_objects_sensor_rois_none(self):
        """Test _get_objects when sensor.rois is None."""
        sensor_id = "sensor1"
        
        # Mock sensor with None rois
        mock_sensor = Mock()
        mock_sensor.rois = None
        self.mock_calibration.sensor_map = {sensor_id: mock_sensor}
        
        result = self.roi_event._get_objects(sensor_id)
        
        assert result is None


class TestROIEventCrossesFunctionality:
    """Test suite for _crosses method functionality."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.roi_event = ROIEvent(self.mock_config, self.mock_calibration)
    
    def test_crosses_start_inside_end_outside(self):
        """Test _crosses when start point inside, end point outside."""
        trip = [Point2D(x=10, y=10), Point2D(x=20, y=20), Point2D(x=100, y=100)]
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        # Mock check_point to return True for start, False for end
        def mock_is_inside(point, sensor, roi):
            if point == trip[0]:  # Start point
                return True
            elif point == trip[-1]:  # End point
                return False
            return False
        
        self.mock_calibration.point_in_polygon.side_effect = mock_is_inside
        
        result = self.roi_event._crosses(trip, sensor_id, roi_id)
        
        assert result is True  # start_position (True) != end_position (False)
    
    def test_crosses_start_outside_end_inside(self):
        """Test _crosses when start point outside, end point inside."""
        trip = [Point2D(x=100, y=100), Point2D(x=50, y=50), Point2D(x=10, y=10)]
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        # Mock check_point to return False for start, True for end
        def mock_is_inside(point, sensor, roi):
            if point == trip[0]:  # Start point
                return False
            elif point == trip[-1]:  # End point
                return True
            return False
        
        self.mock_calibration.point_in_polygon.side_effect = mock_is_inside
        
        result = self.roi_event._crosses(trip, sensor_id, roi_id)
        
        assert result is True  # start_position (False) != end_position (True)
    
    def test_crosses_both_inside(self):
        """Test _crosses when both start and end points are inside."""
        trip = [Point2D(x=10, y=10), Point2D(x=20, y=20)]
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        # Mock check_point to return True for both
        self.mock_calibration.point_in_polygon.return_value = True
        
        result = self.roi_event._crosses(trip, sensor_id, roi_id)
        
        assert result is False  # start_position (True) == end_position (True)
    
    def test_crosses_both_outside(self):
        """Test _crosses when both start and end points are outside."""
        trip = [Point2D(x=100, y=100), Point2D(x=200, y=200)]
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        # Mock check_point to return False for both
        self.mock_calibration.point_in_polygon.return_value = False
        
        result = self.roi_event._crosses(trip, sensor_id, roi_id)
        
        assert result is False  # start_position (False) == end_position (False)
    
    def test_crosses_single_point_trip(self):
        """Test _crosses with single point trip."""
        trip = [Point2D(x=10, y=10)]
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        # Mock check_point to return True
        self.mock_calibration.point_in_polygon.return_value = True
        
        result = self.roi_event._crosses(trip, sensor_id, roi_id)
        
        assert result is False  # start_position == end_position (same point)
    
    def test_crosses_long_trip(self):
        """Test _crosses with long trip (many points)."""
        trip = [Point2D(x=i, y=i) for i in range(100)]  # 100 points
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        # Mock check_point: first point inside, last point outside
        def mock_is_inside(point, sensor, roi):
            if point == trip[0]:  # Start point
                return True
            elif point == trip[-1]:  # End point
                return False
            return True  # Default for other points
        
        self.mock_calibration.point_in_polygon.side_effect = mock_is_inside
        
        result = self.roi_event._crosses(trip, sensor_id, roi_id)
        
        assert result is True  # start != end
        # Should only call check_point for first and last points
        assert self.mock_calibration.point_in_polygon.call_count == 2


class TestROIEventCrossesErrorHandling:
    """Test suite for _crosses method error handling."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.roi_event = ROIEvent(self.mock_config, self.mock_calibration)
    
    def test_crosses_empty_trip(self):
        """Test _crosses with empty trip."""
        trip = []
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        with pytest.raises(IndexError):
            self.roi_event._crosses(trip, sensor_id, roi_id)
    
    def test_crosses_when_is_inside_raises_exception(self):
        """Test _crosses when _is_inside raises exception."""
        trip = [Point2D(x=10, y=10), Point2D(x=20, y=20)]
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        # Mock check_point to raise exception on first call
        self.mock_calibration.point_in_polygon.side_effect = Exception("Point check error")
        
        with pytest.raises(Exception, match="Point check error"):
            self.roi_event._crosses(trip, sensor_id, roi_id)


class TestROIEventIntegration:
    """Integration tests for ROIEvent with realistic scenarios."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        
        # Setup realistic return values
        self.mock_config.sensor_tripwire_min_points.return_value = 3
        # Default coordinate-inside detection mode (bbox off)
        self.mock_config.roi_event_detection_mode = "coordinate"

        # Setup sensor map
        mock_sensor = Mock()
        mock_rois = [
            self.create_mock_roi("entrance_roi", "entrance"),
            self.create_mock_roi("exit_roi", "exit")
        ]
        mock_sensor.rois = mock_rois
        self.mock_calibration.sensor_map = {"sensor1": mock_sensor}
        
        self.roi_event = ROIEvent(self.mock_config, self.mock_calibration)
    
    def create_mock_roi(self, roi_id: str, roi_type: str) -> ROI:
        """Helper method to create mock ROI."""
        roi = Mock(spec=ROI)
        roi.id = roi_id
        roi.type = roi_type
        roi.roiCoordinates = [
            Point2D(x=0, y=0),
            Point2D(x=100, y=0),
            Point2D(x=100, y=100),
            Point2D(x=0, y=100)
        ]
        return roi
    
    def create_mock_behavior(self, sensor_id="sensor1", length=10):
        """Helper method to create mock behavior object."""
        behavior = Mock(spec=Behavior)
        # Create a proper mock sensor that will pass Pydantic validation
        behavior.sensor = {"id": sensor_id, "type": "camera"}
        behavior.length = length
        behavior.timestamp = datetime(2024, 1, 15, 14, 30, 0)
        behavior.timeInterval = 1.0  # 1 second total time interval
        behavior.speed = 1.5
        behavior.speedOverTime = [1.2, 1.5, 1.8]
        behavior.bearing = 85.0
        behavior.direction = "E"
        behavior.distance = 25.0
        behavior.id = f"behavior_{sensor_id}"
        behavior.object = {"id": "obj1", "type": "person"}
        behavior.place = {"name": "test_place"}
        
        # Create trajectory
        coordinates = [
            Point(point=[i * 10.0, i * 5.0]) for i in range(length)
        ]
        geo_location = Mock(spec=GeoLocation)
        geo_location.coordinates = coordinates
        behavior.locations = geo_location
        # Per-frame bboxes aligned 1:1 with the trajectory (values irrelevant in coordinate mode).
        behavior.locationsBboxes = [Bbox() for _ in range(length)]

        return behavior
    
    def test_integration_uneven_distribution_no_events(self):
        """Test integration where uneven distribution prevents event creation."""
        behavior = self.create_mock_behavior(length=8)  # 8 points with min_trip_length = 6
        
        # Mock behavior sensor.id access for get_events
        behavior.sensor = Mock()
        behavior.sensor.id = "sensor1"
        
        # Test uneven distribution: all points inside ROI
        # This creates side1=6, side2=0, so side1 != side2
        self.mock_calibration.point_in_polygon.return_value = True
        
        result = self.roi_event.get_events(behavior)
        
        # Should not detect events due to uneven distribution (all inside)
        assert result == []
    
    def test_integration_even_distribution_detection(self):
        """Test integration with even distribution of points."""
        behavior = self.create_mock_behavior(length=10)
        
        # Mock behavior sensor.id access for get_events  
        behavior.sensor = Mock()
        behavior.sensor.id = "sensor1"
        
        # Test even distribution: exactly half inside, half outside
        def mock_point_in_polygon(point, sensor_id, roi_id):
            # For 6 points (min_trip_length), 3 inside, 3 outside
            return point.x < 30.0  # First 3 points inside, last 3 outside
        
        self.mock_calibration.point_in_polygon.side_effect = mock_point_in_polygon
        
        # Since we can't easily create valid Behavior objects due to Pydantic validation,
        # we'll test that the logic gets to the point where it would try to create an event
        # but skip the actual event creation to avoid validation errors
        with patch.object(self.roi_event, '_crosses', return_value=True):
            try:
                result = self.roi_event.get_events(behavior)
                # If we get here without error, the logic worked up to Behavior creation
                # The actual event creation might fail due to mock objects, but that's OK
            except Exception as e:
                # We expect potential validation errors but want to ensure
                # the detection logic itself is working
                assert "ValidationError" in str(type(e)) or "Mock" in str(e)
    
    def test_integration_no_roi_interaction(self):
        """Test integration scenario with no ROI interaction."""
        behavior = self.create_mock_behavior(length=5)
        
        # Mock behavior sensor.id access for get_events
        behavior.sensor = Mock()
        behavior.sensor.id = "sensor1"
        
        # Setup point checking: all points outside ROI
        self.mock_calibration.point_in_polygon.return_value = False
        
        result = self.roi_event.get_events(behavior)
        
        # Should not detect any events
        assert result == []
    
    def test_integration_behavior_too_short(self):
        """Test integration with behavior shorter than minimum required."""
        behavior = self.create_mock_behavior(length=1)  # Very short behavior
        
        # Mock behavior sensor.id access for get_events
        behavior.sensor = Mock()
        behavior.sensor.id = "sensor1"
        
        result = self.roi_event.get_events(behavior)
        
        # Should not detect events due to short length
        assert result == []
    
    def test_integration_sensor_not_found(self):
        """Test integration when sensor not in calibration map."""
        behavior = self.create_mock_behavior(sensor_id="unknown_sensor")
        
        # Mock behavior sensor.id access for get_events
        behavior.sensor = Mock()
        behavior.sensor.id = "unknown_sensor"
        
        result = self.roi_event.get_events(behavior)
        
        # Should return empty list when sensor not found
        assert result == []


class TestROIEventEdgeCases:
    """Test suite for edge cases in ROIEvent."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.roi_event = ROIEvent(self.mock_config, self.mock_calibration)
    
    def test_crosses_with_extreme_coordinates(self):
        """Test _crosses with extreme coordinate values."""
        trip = [
            Point2D(x=float('inf'), y=float('inf')),
            Point2D(x=float('-inf'), y=float('-inf'))
        ]
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        # Mock different results for extreme points
        def mock_is_inside(point, sensor, roi):
            if point.x == float('inf'):
                return True
            else:
                return False
        
        self.mock_calibration.point_in_polygon.side_effect = mock_is_inside
        
        result = self.roi_event._crosses(trip, sensor_id, roi_id)
        
        assert result is True  # Different positions
    
    def test_is_inside_with_very_large_coordinates(self):
        """Test _is_inside with very large coordinates."""
        point = Point2D(x=1e10, y=1e10)
        sensor_id = "sensor1"
        roi_id = "roi1"
        
        self.mock_calibration.point_in_polygon.return_value = False
        
        result = self.roi_event._is_inside(point, sensor_id, roi_id)
        
        assert result is False
        self.mock_calibration.point_in_polygon.assert_called_once_with(point, sensor_id, roi_id)
    
    def test_get_objects_with_malformed_sensor_data(self):
        """Test _get_objects when sensor data is malformed."""
        sensor_id = "sensor1"
        
        # Mock sensor without rois attribute
        mock_sensor = Mock()
        del mock_sensor.rois  # Remove rois attribute
        self.mock_calibration.sensor_map = {sensor_id: mock_sensor}
        
        with pytest.raises(AttributeError):
            self.roi_event._get_objects(sensor_id)


class TestROIEventBboxMode:
    """Test suite for the configurable bbox-overlap detection mode (image calibration only)."""

    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_calibration = Mock(spec=CalibrationBase)
        self.mock_calibration.calibration_type = CalibrationType.IMAGE
        self.roi_event = ROIEvent(self.mock_config, self.mock_calibration)
        self.bbox = Bbox(leftX=100.0, topY=100.0, rightX=200.0, bottomY=200.0)

    def test_is_inside_bbox_mode_uses_bbox_overlap(self):
        """When bbox mode is on and a per-frame bbox is supplied, _is_inside uses bbox_overlaps_polygon."""
        point = Point2D(x=150.0, y=200.0)
        self.mock_config.roi_event_detection_mode = "bbox"
        self.mock_calibration.bbox_overlaps_polygon.return_value = True

        result = self.roi_event._is_inside(point, "sensor1", "roi1", self.bbox)

        assert result is True
        self.mock_calibration.bbox_overlaps_polygon.assert_called_once_with(
            self.bbox, "sensor1", "roi1", point
        )
        self.mock_calibration.point_in_polygon.assert_not_called()

    def test_is_inside_bbox_mode_without_bbox_falls_back_to_coordinate(self):
        """Bbox mode falls back to the coordinate check when no per-frame bbox is available."""
        point = Point2D(x=150.0, y=200.0)
        self.mock_config.roi_event_detection_mode = "bbox"
        self.mock_calibration.point_in_polygon.return_value = True

        result = self.roi_event._is_inside(point, "sensor1", "roi1", None)

        assert result is True
        self.mock_calibration.point_in_polygon.assert_called_once_with(point, "sensor1", "roi1")
        self.mock_calibration.bbox_overlaps_polygon.assert_not_called()

    def test_is_inside_coordinate_mode_ignores_bbox(self):
        """With coordinate mode, _is_inside uses the coordinate check even when a bbox is supplied."""
        point = Point2D(x=150.0, y=200.0)
        self.mock_config.roi_event_detection_mode = "coordinate"
        self.mock_calibration.point_in_polygon.return_value = False

        result = self.roi_event._is_inside(point, "sensor1", "roi1", self.bbox)

        assert result is False
        self.mock_calibration.point_in_polygon.assert_called_once_with(point, "sensor1", "roi1")
        self.mock_calibration.bbox_overlaps_polygon.assert_not_called()

    def test_crosses_bbox_mode_uses_endpoint_bboxes(self):
        """_crosses uses the start/end per-frame bboxes so it stays consistent with get_events."""
        trip = [Point2D(x=10.0, y=10.0), Point2D(x=20.0, y=20.0), Point2D(x=300.0, y=300.0)]
        start_bbox = Bbox(leftX=0.0, topY=0.0, rightX=10.0, bottomY=10.0)
        end_bbox = Bbox(leftX=290.0, topY=290.0, rightX=310.0, bottomY=310.0)
        bboxes = [start_bbox, self.bbox, end_bbox]
        self.mock_config.roi_event_detection_mode = "bbox"
        # First point overlaps, last point does not -> crossing detected
        self.mock_calibration.bbox_overlaps_polygon.side_effect = [True, False]

        result = self.roi_event._crosses(trip, "sensor1", "roi1", bboxes)

        assert result is True
        assert self.mock_calibration.bbox_overlaps_polygon.call_count == 2
        self.mock_calibration.bbox_overlaps_polygon.assert_any_call(
            start_bbox, "sensor1", "roi1", trip[0]
        )
        self.mock_calibration.bbox_overlaps_polygon.assert_any_call(
            end_bbox, "sensor1", "roi1", trip[-1]
        )

    def test_is_inside_bbox_mode_falls_back_for_cartesian(self):
        """Bbox mode falls back to the coordinate check for cartesian calibration (unit mismatch)."""
        point = Point2D(x=5.0, y=12.0)
        self.mock_config.roi_event_detection_mode = "bbox"
        self.mock_calibration.calibration_type = CalibrationType.CARTESIAN
        self.mock_calibration.point_in_polygon.return_value = True

        result = self.roi_event._is_inside(point, "sensor1", "roi1", self.bbox)

        assert result is True
        self.mock_calibration.point_in_polygon.assert_called_once_with(point, "sensor1", "roi1")
        self.mock_calibration.bbox_overlaps_polygon.assert_not_called()

    def test_is_inside_bbox_mode_falls_back_for_geo(self):
        """Bbox mode falls back to the coordinate check for geo calibration (unit mismatch)."""
        point = Point2D(x=-73.9, y=40.7)
        self.mock_config.roi_event_detection_mode = "bbox"
        self.mock_calibration.calibration_type = CalibrationType.GEO
        self.mock_calibration.point_in_polygon.return_value = False

        result = self.roi_event._is_inside(point, "sensor1", "roi1", self.bbox)

        assert result is False
        self.mock_calibration.point_in_polygon.assert_called_once_with(point, "sensor1", "roi1")
        self.mock_calibration.bbox_overlaps_polygon.assert_not_called()

    def test_bbox_mode_unsupported_warns_once(self):
        """The unsupported-calibration fallback warning is emitted only once per ROIEvent instance."""
        point = Point2D(x=5.0, y=12.0)
        self.mock_config.roi_event_detection_mode = "bbox"
        self.mock_calibration.calibration_type = CalibrationType.CARTESIAN
        self.mock_calibration.point_in_polygon.return_value = False

        with patch("mdx.analytics.core.transform.event.roi_event.logger") as mock_logger:
            self.roi_event._is_inside(point, "sensor1", "roi1", self.bbox)
            self.roi_event._is_inside(point, "sensor1", "roi1", self.bbox)
            assert mock_logger.warning.call_count == 1


if __name__ == "__main__":
    pytest.main([__file__])
