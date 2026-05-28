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

from mdx.analytics.core.constants import TripwireDirection
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import (
    Point2D, Line, Tripwire
)
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase
from mdx.analytics.core.transform.event.tripwire_event import TripwireEvent


class TestTripwireEventFunctionality:
    """Test suite for TripwireEvent core functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = Mock(spec=AppConfig)
        self.calibration = Mock(spec=CalibrationBase)
        
        # Setup mock sensor map
        self.mock_sensor = Mock()
        self.mock_sensor.tripwires = {}
        self.calibration.sensor_map = {"sensor1": self.mock_sensor}
        
        # Setup config mock
        self.config.sensor_tripwire_min_points.return_value = 2
        
    def test_initialization_with_valid_parameters(self):
        """Test TripwireEvent initialization with valid parameters."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        assert tripwire_event.config == self.config
        assert tripwire_event.calibration == self.calibration
        assert tripwire_event.direction_enum == TripwireDirection
        assert tripwire_event.event_name == "tripwire"
        assert tripwire_event.event_type == "TripEvent"

    def test_check_point_delegates_to_calibration(self):
        """Test _check_point method delegates to calibration correctly."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        point = Point2D(x=10.0, y=20.0)
        sensor_id = "sensor1"
        obj_id = "tripwire1"
        
        # Mock calibration response
        self.calibration.point_in_tripwire.return_value = True
        
        result = tripwire_event._check_point(point, sensor_id, obj_id)
        
        assert result is True
        self.calibration.point_in_tripwire.assert_called_once_with(point, sensor_id, obj_id)

    def test_check_point_returns_false_when_not_in_tripwire(self):
        """Test _check_point returns False when point is not in tripwire."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        point = Point2D(x=10.0, y=20.0)
        sensor_id = "sensor1"
        obj_id = "tripwire1"
        
        # Mock calibration response
        self.calibration.point_in_tripwire.return_value = False
        
        result = tripwire_event._check_point(point, sensor_id, obj_id)
        
        assert result is False
        self.calibration.point_in_tripwire.assert_called_once_with(point, sensor_id, obj_id)

    def test_get_objects_returns_tripwires_from_sensor_map(self):
        """Test _get_objects returns correct tripwires from sensor map."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        sensor_id = "sensor1"
        
        # Create mock tripwires
        tripwire1 = Mock(spec=Tripwire)
        tripwire1.id = "tripwire1"
        tripwire2 = Mock(spec=Tripwire) 
        tripwire2.id = "tripwire2"
        
        self.mock_sensor.tripwires = {
            "tripwire1": tripwire1,
            "tripwire2": tripwire2
        }
        
        result = tripwire_event._get_objects(sensor_id)
        
        assert len(result) == 2
        assert tripwire1 in result
        assert tripwire2 in result

    def test_get_objects_returns_empty_list_when_no_tripwires(self):
        """Test _get_objects returns empty list when no tripwires exist."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        sensor_id = "sensor1"
        
        self.mock_sensor.tripwires = {}
        
        result = tripwire_event._get_objects(sensor_id)
        
        assert result == []

    @patch('mdx.analytics.core.transform.event.tripwire_event.intersect')
    def test_intersect_with_trajectory_intersection(self, mock_intersect):
        """Test _intersect method when trajectory intersects with tripwire."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        # Create trajectory
        trip = [Point2D(x=0.0, y=0.0), Point2D(x=10.0, y=10.0)]
        sensor_id = "sensor1"
        obj_id = "tripwire1"
        
        # Create mock tripwire with wires
        mock_tripwire = Mock()
        mock_wire = Mock(spec=Line)
        mock_tripwire.wires = [mock_wire]
        
        self.mock_sensor.tripwires = {"tripwire1": mock_tripwire}
        
        # Mock intersect function to return True
        mock_intersect.return_value = True
        
        result = tripwire_event._intersect(trip, sensor_id, obj_id)
        
        assert result is True
        mock_intersect.assert_called_once()
        # Verify the Line was created with correct endpoints
        call_args = mock_intersect.call_args[0]
        assert call_args[0] == [mock_wire]  # First argument is the list of tripwires
        assert call_args[1].p1 == trip[0]  # Second argument is the trajectory line
        assert call_args[1].p2 == trip[-1]

    @patch('mdx.analytics.core.transform.event.tripwire_event.intersect')
    def test_intersect_with_no_trajectory_intersection(self, mock_intersect):
        """Test _intersect method when trajectory does not intersect with tripwire."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        # Create trajectory
        trip = [Point2D(x=0.0, y=0.0), Point2D(x=1.0, y=1.0)]
        sensor_id = "sensor1"
        obj_id = "tripwire1"
        
        # Create mock tripwire with wires
        mock_tripwire = Mock()
        mock_wire = Mock(spec=Line)
        mock_tripwire.wires = [mock_wire]
        
        self.mock_sensor.tripwires = {"tripwire1": mock_tripwire}
        
        # Mock intersect function to return False
        mock_intersect.return_value = False
        
        result = tripwire_event._intersect(trip, sensor_id, obj_id)
        
        assert result is False
        mock_intersect.assert_called_once()

    @patch('mdx.analytics.core.transform.event.tripwire_event.intersect')
    def test_intersect_with_single_point_trajectory(self, mock_intersect):
        """Test _intersect method with single point trajectory (start == end)."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        # Create trajectory with single point (first and last are same)
        trip = [Point2D(x=5.0, y=5.0)]
        sensor_id = "sensor1"
        obj_id = "tripwire1"
        
        # Create mock tripwire with wires
        mock_tripwire = Mock()
        mock_wire = Mock(spec=Line)
        mock_tripwire.wires = [mock_wire]
        
        self.mock_sensor.tripwires = {"tripwire1": mock_tripwire}
        
        # Mock intersect function
        mock_intersect.return_value = False
        
        result = tripwire_event._intersect(trip, sensor_id, obj_id)
        
        assert result is False
        mock_intersect.assert_called_once()
        # Verify the Line was created with same start and end point
        call_args = mock_intersect.call_args[0]
        assert call_args[1].p1 == trip[0]
        assert call_args[1].p2 == trip[-1]  # Same as trip[0]

    def test_intersect_with_multiple_point_trajectory(self):
        """Test _intersect method uses first and last points of multi-point trajectory."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        # Create trajectory with multiple points
        trip = [
            Point2D(x=0.0, y=0.0),
            Point2D(x=5.0, y=5.0),
            Point2D(x=7.0, y=3.0),
            Point2D(x=10.0, y=10.0)
        ]
        sensor_id = "sensor1"
        obj_id = "tripwire1"
        
        # Create mock tripwire with wires
        mock_tripwire = Mock()
        mock_wire = Mock(spec=Line)
        mock_tripwire.wires = [mock_wire]
        
        self.mock_sensor.tripwires = {"tripwire1": mock_tripwire}
        
        with patch('mdx.analytics.core.transform.event.tripwire_event.intersect') as mock_intersect:
            mock_intersect.return_value = True
            
            result = tripwire_event._intersect(trip, sensor_id, obj_id)
            
            assert result is True
            mock_intersect.assert_called_once()
            # Verify the Line was created with first and last points only
            call_args = mock_intersect.call_args[0]
            assert call_args[1].p1 == trip[0]  # First point
            assert call_args[1].p2 == trip[-1]  # Last point (not middle points)


class TestTripwireEventEdgeCases:
    """Test suite for TripwireEvent edge cases and boundary conditions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = Mock(spec=AppConfig)
        self.calibration = Mock(spec=CalibrationBase)
        
        # Setup mock sensor map
        self.mock_sensor = Mock()
        self.mock_sensor.tripwires = {}
        self.calibration.sensor_map = {"sensor1": self.mock_sensor}
        
        # Setup config mock
        self.config.sensor_tripwire_min_points.return_value = 2

    def test_check_point_with_zero_coordinates(self):
        """Test _check_point with zero coordinates."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        point = Point2D(x=0.0, y=0.0)
        
        self.calibration.point_in_tripwire.return_value = True
        
        result = tripwire_event._check_point(point, "sensor1", "tripwire1")
        
        assert result is True
        self.calibration.point_in_tripwire.assert_called_once_with(point, "sensor1", "tripwire1")

    def test_check_point_with_negative_coordinates(self):
        """Test _check_point with negative coordinates."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        point = Point2D(x=-10.5, y=-20.3)
        
        self.calibration.point_in_tripwire.return_value = False
        
        result = tripwire_event._check_point(point, "sensor1", "tripwire1")
        
        assert result is False
        self.calibration.point_in_tripwire.assert_called_once_with(point, "sensor1", "tripwire1")

    def test_check_point_with_very_large_coordinates(self):
        """Test _check_point with very large coordinates."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        point = Point2D(x=999999.999, y=888888.888)
        
        self.calibration.point_in_tripwire.return_value = True
        
        result = tripwire_event._check_point(point, "sensor1", "tripwire1")
        
        assert result is True
        self.calibration.point_in_tripwire.assert_called_once_with(point, "sensor1", "tripwire1")

    def test_check_point_with_very_small_coordinates(self):
        """Test _check_point with very small floating point coordinates."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        point = Point2D(x=0.000001, y=0.000002)
        
        self.calibration.point_in_tripwire.return_value = False
        
        result = tripwire_event._check_point(point, "sensor1", "tripwire1")
        
        assert result is False
        self.calibration.point_in_tripwire.assert_called_once_with(point, "sensor1", "tripwire1")

    def test_get_objects_with_empty_sensor_id(self):
        """Test _get_objects with empty sensor ID."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        # Add empty sensor to sensor map
        empty_sensor = Mock()
        empty_sensor.tripwires = {}
        self.calibration.sensor_map[""] = empty_sensor
        
        result = tripwire_event._get_objects("")
        
        assert result == []

    def test_get_objects_with_single_tripwire(self):
        """Test _get_objects with exactly one tripwire."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        sensor_id = "sensor1"
        
        # Create single tripwire
        tripwire1 = Mock(spec=Tripwire)
        tripwire1.id = "tripwire1"
        
        self.mock_sensor.tripwires = {"tripwire1": tripwire1}
        
        result = tripwire_event._get_objects(sensor_id)
        
        assert len(result) == 1
        assert result[0] == tripwire1

    def test_get_objects_with_many_tripwires(self):
        """Test _get_objects with many tripwires."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        sensor_id = "sensor1"
        
        # Create multiple tripwires
        tripwires = {}
        for i in range(10):
            tripwire = Mock(spec=Tripwire)
            tripwire.id = f"tripwire{i}"
            tripwires[f"tripwire{i}"] = tripwire
        
        self.mock_sensor.tripwires = tripwires
        
        result = tripwire_event._get_objects(sensor_id)
        
        assert len(result) == 10
        for i in range(10):
            assert any(tw.id == f"tripwire{i}" for tw in result)

    @patch('mdx.analytics.core.transform.event.tripwire_event.intersect')
    def test_intersect_with_identical_start_end_points(self, mock_intersect):
        """Test _intersect when trajectory has identical start and end points."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        # Create trajectory with identical start and end
        same_point = Point2D(x=5.0, y=5.0)
        trip = [same_point, Point2D(x=7.0, y=8.0), same_point]
        sensor_id = "sensor1"
        obj_id = "tripwire1"
        
        # Create mock tripwire
        mock_tripwire = Mock()
        mock_wire = Mock(spec=Line)
        mock_tripwire.wires = [mock_wire]
        self.mock_sensor.tripwires = {"tripwire1": mock_tripwire}
        
        mock_intersect.return_value = False
        
        result = tripwire_event._intersect(trip, sensor_id, obj_id)
        
        assert result is False
        # Verify line created with same start and end points
        call_args = mock_intersect.call_args[0]
        assert call_args[1].p1 == same_point
        assert call_args[1].p2 == same_point

    @patch('mdx.analytics.core.transform.event.tripwire_event.intersect')
    def test_intersect_with_trajectory_at_origin(self, mock_intersect):
        """Test _intersect with trajectory starting and ending at origin."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        trip = [Point2D(x=0.0, y=0.0), Point2D(x=0.0, y=0.0)]
        sensor_id = "sensor1"
        obj_id = "tripwire1"
        
        mock_tripwire = Mock()
        mock_wire = Mock(spec=Line)
        mock_tripwire.wires = [mock_wire]
        self.mock_sensor.tripwires = {"tripwire1": mock_tripwire}
        
        mock_intersect.return_value = False
        
        result = tripwire_event._intersect(trip, sensor_id, obj_id)
        
        assert result is False
        mock_intersect.assert_called_once()


class TestTripwireEventErrorHandling:
    """Test suite for TripwireEvent error handling and dirty input scenarios."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = Mock(spec=AppConfig)
        self.calibration = Mock(spec=CalibrationBase)
        
        # Setup mock sensor map
        self.mock_sensor = Mock()
        self.mock_sensor.tripwires = {}
        self.calibration.sensor_map = {"sensor1": self.mock_sensor}

    def test_check_point_when_calibration_raises_exception(self):
        """Test _check_point when calibration.point_in_tripwire raises exception."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        point = Point2D(x=10.0, y=20.0)
        
        # Mock calibration to raise exception
        self.calibration.point_in_tripwire.side_effect = KeyError("Tripwire not found")
        
        with pytest.raises(KeyError, match="Tripwire not found"):
            tripwire_event._check_point(point, "sensor1", "tripwire1")

    def test_check_point_when_calibration_raises_attribute_error(self):
        """Test _check_point when calibration raises AttributeError."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        point = Point2D(x=10.0, y=20.0)
        
        # Mock calibration to raise AttributeError
        self.calibration.point_in_tripwire.side_effect = AttributeError("Method not available")
        
        with pytest.raises(AttributeError, match="Method not available"):
            tripwire_event._check_point(point, "sensor1", "tripwire1")

    def test_get_objects_when_sensor_not_in_map(self):
        """Test _get_objects when sensor ID is not in sensor map."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        with pytest.raises(KeyError):
            tripwire_event._get_objects("nonexistent_sensor")

    def test_get_objects_when_sensor_map_is_none(self):
        """Test _get_objects when sensor_map is None."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        self.calibration.sensor_map = None
        
        with pytest.raises(TypeError):
            tripwire_event._get_objects("sensor1")

    def test_get_objects_when_tripwires_attribute_missing(self):
        """Test _get_objects when sensor has no tripwires attribute."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        # Create sensor without tripwires attribute
        broken_sensor = Mock()
        del broken_sensor.tripwires  # Remove the tripwires attribute
        self.calibration.sensor_map = {"sensor1": broken_sensor}
        
        with pytest.raises(AttributeError):
            tripwire_event._get_objects("sensor1")

    def test_intersect_when_tripwire_not_found(self):
        """Test _intersect when tripwire ID is not found."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        trip = [Point2D(x=0.0, y=0.0), Point2D(x=10.0, y=10.0)]
        
        # Empty tripwires dictionary
        self.mock_sensor.tripwires = {}
        
        with pytest.raises(KeyError):
            tripwire_event._intersect(trip, "sensor1", "nonexistent_tripwire")

    def test_intersect_when_tripwire_has_no_wire(self):
        """Test _intersect when tripwire has no wires attribute."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        trip = [Point2D(x=0.0, y=0.0), Point2D(x=10.0, y=10.0)]
        
        # Create tripwire without wires attribute
        broken_tripwire = Mock()
        del broken_tripwire.wires
        self.mock_sensor.tripwires = {"tripwire1": broken_tripwire}
        
        with pytest.raises(AttributeError):
            tripwire_event._intersect(trip, "sensor1", "tripwire1")

    def test_intersect_when_trip_is_empty(self):
        """Test _intersect when trip list is empty."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        trip = []  # Empty trajectory
        
        mock_tripwire = Mock()
        mock_wire = Mock(spec=Line)
        mock_tripwire.wires = [mock_wire]
        self.mock_sensor.tripwires = {"tripwire1": mock_tripwire}
        
        with pytest.raises(IndexError):
            tripwire_event._intersect(trip, "sensor1", "tripwire1")

    @patch('mdx.analytics.core.transform.event.tripwire_event.intersect')
    def test_intersect_when_intersect_function_raises_exception(self, mock_intersect):
        """Test _intersect when intersect utility function raises exception."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        trip = [Point2D(x=0.0, y=0.0), Point2D(x=10.0, y=10.0)]
        
        mock_tripwire = Mock()
        mock_wire = Mock(spec=Line)
        mock_tripwire.wires = [mock_wire]
        self.mock_sensor.tripwires = {"tripwire1": mock_tripwire}
        
        # Mock intersect to raise exception
        mock_intersect.side_effect = ValueError("Invalid line geometry")
        
        with pytest.raises(ValueError, match="Invalid line geometry"):
            tripwire_event._intersect(trip, "sensor1", "tripwire1")

    def test_initialization_with_none_config(self):
        """Test initialization with None config should not raise during init."""
        # This tests the constructor behavior with None values
        tripwire_event = TripwireEvent(None, self.calibration)
        
        assert tripwire_event.config is None
        assert tripwire_event.calibration == self.calibration

    def test_initialization_with_none_calibration(self):
        """Test initialization with None calibration should not raise during init."""
        tripwire_event = TripwireEvent(self.config, None)
        
        assert tripwire_event.config == self.config
        assert tripwire_event.calibration is None


class TestTripwireEventIntegration:
    """Integration tests to verify TripwireEvent works with realistic data."""

    def setup_method(self):
        """Set up realistic test fixtures."""
        self.config = Mock(spec=AppConfig)
        self.calibration = Mock(spec=CalibrationBase)
        
        # Create realistic tripwire
        self.tripwire = Tripwire(
            id="entry_gate",
            wires=[Line(
                p1=Point2D(x=100.0, y=200.0),
                p2=Point2D(x=300.0, y=200.0)
            )],
            direction=Line(
                p1=Point2D(x=200.0, y=200.0),
                p2=Point2D(x=200.0, y=100.0)
            ),
            in_orientation=1,
            out_orientation=2,
            sensors=["camera1"]
        )
        
        # Setup sensor map
        self.mock_sensor = Mock()
        self.mock_sensor.tripwires = {"entry_gate": self.tripwire}
        self.calibration.sensor_map = {"camera1": self.mock_sensor}
        
        # Setup calibration methods
        self.calibration.point_in_tripwire.return_value = True
        
        # Setup config
        self.config.sensor_tripwire_min_points.return_value = 2

    def test_realistic_tripwire_crossing_scenario(self):
        """Test realistic scenario of object crossing a tripwire."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        # Test point inside tripwire area
        point_inside = Point2D(x=200.0, y=150.0)  # Above the tripwire line
        result = tripwire_event._check_point(point_inside, "camera1", "entry_gate")
        assert result is True
        
        # Test getting tripwires for the sensor
        tripwires = tripwire_event._get_objects("camera1")
        assert len(tripwires) == 1
        assert tripwires[0].id == "entry_gate"

    @patch('mdx.analytics.core.transform.event.tripwire_event.intersect')
    def test_realistic_trajectory_intersection(self, mock_intersect):
        """Test realistic trajectory that crosses a tripwire."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        # Create trajectory that crosses the tripwire
        trajectory = [
            Point2D(x=200.0, y=100.0),  # Start above tripwire
            Point2D(x=200.0, y=250.0),  # End below tripwire
        ]
        
        mock_intersect.return_value = True
        
        result = tripwire_event._intersect(trajectory, "camera1", "entry_gate")
        
        assert result is True
        mock_intersect.assert_called_once()
        
        # Verify correct line creation
        call_args = mock_intersect.call_args[0]
        trajectory_line = call_args[1]
        assert trajectory_line.p1.x == 200.0
        assert trajectory_line.p1.y == 100.0
        assert trajectory_line.p2.x == 200.0
        assert trajectory_line.p2.y == 250.0

    @patch('mdx.analytics.core.transform.event.tripwire_event.intersect')
    def test_realistic_trajectory_no_intersection(self, mock_intersect):
        """Test realistic trajectory that does not cross a tripwire."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        # Create trajectory that runs parallel to tripwire
        trajectory = [
            Point2D(x=50.0, y=150.0),   # Start left of tripwire
            Point2D(x=350.0, y=150.0),  # End right of tripwire, same height
        ]
        
        mock_intersect.return_value = False
        
        result = tripwire_event._intersect(trajectory, "camera1", "entry_gate")
        
        assert result is False
        mock_intersect.assert_called_once()

    def test_multiple_sensors_multiple_tripwires(self):
        """Test scenario with multiple sensors and tripwires."""
        # Add second sensor with different tripwires
        tripwire2 = Tripwire(
            id="exit_gate",
            wires=[Line(
                p1=Point2D(x=400.0, y=300.0),
                p2=Point2D(x=600.0, y=300.0)
            )],
            direction=Line(
                p1=Point2D(x=500.0, y=300.0),
                p2=Point2D(x=500.0, y=200.0)
            ),
            in_orientation=2,
            out_orientation=1,
            sensors=["camera2"]
        )
        
        mock_sensor2 = Mock()
        mock_sensor2.tripwires = {"exit_gate": tripwire2}
        self.calibration.sensor_map["camera2"] = mock_sensor2
        
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        # Test camera1 tripwires
        camera1_tripwires = tripwire_event._get_objects("camera1")
        assert len(camera1_tripwires) == 1
        assert camera1_tripwires[0].id == "entry_gate"
        
        # Test camera2 tripwires
        camera2_tripwires = tripwire_event._get_objects("camera2")
        assert len(camera2_tripwires) == 1
        assert camera2_tripwires[0].id == "exit_gate"

    def test_boundary_point_on_tripwire_line(self):
        """Test point exactly on the tripwire line."""
        tripwire_event = TripwireEvent(self.config, self.calibration)
        
        # Point exactly on the tripwire line
        point_on_line = Point2D(x=200.0, y=200.0)  # Midpoint of tripwire
        
        # Mock calibration to return False for point on line (as per spec)
        self.calibration.point_in_tripwire.return_value = False
        
        result = tripwire_event._check_point(point_on_line, "camera1", "entry_gate")
        
        assert result is False
        self.calibration.point_in_tripwire.assert_called_once_with(
            point_on_line, "camera1", "entry_gate"
        )