# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from google.protobuf.timestamp_pb2 import Timestamp

from mdx.analytics.core.constants import SENSOR_TYPE_CAMERA
from mdx.analytics.core.schema.models import Bbox, Coordinate, Location, SensorInfo
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.transform.calibration.calibration import Calibration
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationType
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.utils.io_utils import load_json_from_file


class TestCalibration(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.config = AppConfig(**load_json_from_file("tests/unit/resources/test_config.json"))
        calibration_path = "tests/unit/resources/calibration_building_k.json"
        self.calibration = Calibration(self.config, calibration_path)

    def test_initialization(self):
        """Test Calibration initialization."""
        self.assertIsInstance(self.calibration, Calibration)
        self.assertEqual(self.calibration.config, self.config)

    def test_transform_bbox_latlon_to_latlon(self):
        """Test transform_bbox method with latlon input and output."""
        # Create test bbox and sensor
        bbox = Bbox(leftX=100, topY=100, rightX=130, bottomY=140)
        sensor_id = "test_sensor"

        # Mock sensor with origin and homography
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)
        mock_sensor.homography = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

        # Configure calibration for latlon input/output
        self.calibration.input_data_in_cartesian = False

        self.config.set_app_config("trajGeoCoordEnable", "true")

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)

            # Verify coordinate calculation
            expected_x = (bbox.rightX + bbox.leftX) / 2.0
            expected_y = max(bbox.topY, bbox.bottomY)
            self.assertEqual(coordinate.x, expected_x)
            self.assertEqual(coordinate.y, expected_y)

            # Verify location matches coordinate (for latlon output)
            self.assertEqual(location.lat, expected_y)
            self.assertEqual(location.lon, expected_x)

    def test_transform_bbox_cartesian_to_cartesian(self):
        """Test transform_bbox method with cartesian input and output."""
        # Create test bbox and sensor
        bbox = Bbox(leftX=100, topY=100, rightX=130, bottomY=140)
        sensor_id = "test_sensor"

        # Mock sensor with origin and homography
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)
        mock_sensor.homography = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

        # Configure calibration for cartesian input and output
        self.calibration.input_data_in_cartesian = True
        self.config.set_app_config("trajGeoCoordEnable", "false")

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)

            # Verify coordinate calculation
            expected_x = (bbox.rightX + bbox.leftX) / 2.0
            expected_y = max(bbox.topY, bbox.bottomY)
            self.assertEqual(coordinate.x, expected_x)
            self.assertEqual(coordinate.y, expected_y)

            # Verify location matches coordinate (for cartesian output)
            self.assertEqual(location.lat, expected_y)
            self.assertEqual(location.lon, expected_x)

    def test_transform_frame(self):
        """Test transform_frame method."""
        # Create test frame
        timestamp = Timestamp()
        timestamp.FromDatetime(datetime.now())
        frame = nvSchema.Frame(version="1.0", id="test_frame", timestamp=timestamp, sensorId="test_sensor", objects=[])

        # Mock transform_bbox method
        with patch.object(self.calibration, "transform_bbox") as mock_transform:
            mock_transform.return_value = (Coordinate(x=1, y=2), Location(lat=3, lon=4))

            # Mock get_roi_metrics and get_fov
            with patch.object(self.calibration, "get_roi_metrics") as mock_roi:
                with patch.object(self.calibration, "get_fov") as mock_fov:
                    mock_roi.return_value = []
                    mock_fov.return_value = []

                    transformed_frame = self.calibration.transform_frame(frame)

                    self.assertEqual(transformed_frame.sensorId, "test_sensor")
                    mock_transform.assert_not_called()  # No objects to transform
                    mock_roi.assert_called_once()
                    mock_fov.assert_called_once()

    def test_initialization_with_custom_origin(self):
        """Test Calibration initialization with custom origin enabled."""
        # Create config with custom origin enabled
        config = AppConfig(**load_json_from_file("tests/unit/resources/test_config.json"))
        config.coordinateReferenceSystem.crsCartesianCustomOrigin.enable = True
        config.coordinateReferenceSystem.crsCartesianCustomOrigin.lat = 37.7749
        config.coordinateReferenceSystem.crsCartesianCustomOrigin.lon = -122.4194
        config.coordinateReferenceSystem.crsCartesianEnablePerSensorOrigin = False
        
        with patch('mdx.analytics.core.transform.calibration.calibration.lonlat_to_xy') as mock_lonlat_to_xy:
            mock_lonlat_to_xy.return_value = (100.0, 200.0)
            calibration = Calibration(config, "tests/unit/resources/calibration_building_k.json")
            
            # Verify custom origin was set
            self.assertIsNotNone(calibration.crs_cartesian_custom_origin_cartesian)
            self.assertEqual(calibration.crs_cartesian_custom_origin_cartesian, (100.0, 200.0))
            mock_lonlat_to_xy.assert_called_once()

    def test_initialization_without_custom_origin(self):
        """Test Calibration initialization without custom origin."""
        # Use default config where custom origin is disabled
        config = AppConfig(**load_json_from_file("tests/unit/resources/test_config.json"))
        config.coordinateReferenceSystem.crsCartesianCustomOrigin.enable = False
        
        calibration = Calibration(config, "tests/unit/resources/calibration_building_k.json")
        self.assertIsNone(calibration.crs_cartesian_custom_origin_cartesian)

    def test_transform_bbox_latlon_to_cartesian_with_calibration_origin(self):
        """Test transform_bbox with latlon input, cartesian output using calibration origin."""
        bbox = Bbox(leftX=100, topY=100, rightX=130, bottomY=140)
        sensor_id = "test_sensor"

        # Mock sensor with origin and homography
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)
        mock_sensor.homography = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

        # Configure calibration for latlon input, cartesian output
        self.calibration.input_data_in_cartesian = False
        self.calibration.use_calibration_origin = True
        self.config.set_app_config("trajGeoCoordEnable", "false")

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            with patch('mdx.analytics.core.transform.calibration.calibration.lonlat_to_xy') as mock_lonlat_to_xy:
                # Mock coordinate transformations
                mock_lonlat_to_xy.side_effect = [(500.0, 600.0), (100.0, 200.0)]  # global coords, then origin
                
                coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)
                
                # Verify coordinate calculation with origin offset
                expected_x = 500.0 - 100.0  # global_x - origin_x
                expected_y = 600.0 - 200.0  # global_y - origin_y
                self.assertEqual(coordinate.x, expected_x)
                self.assertEqual(coordinate.y, expected_y)
                self.assertEqual(location.lat, expected_y)
                self.assertEqual(location.lon, expected_x)

    def test_transform_bbox_latlon_to_cartesian_with_custom_origin(self):
        """Test transform_bbox with latlon input, cartesian output using custom origin."""
        bbox = Bbox(leftX=100, topY=100, rightX=130, bottomY=140)
        sensor_id = "test_sensor"

        # Mock sensor with origin and homography
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)
        mock_sensor.homography = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

        # Configure calibration for latlon input, cartesian output with custom origin
        self.calibration.input_data_in_cartesian = False
        self.calibration.use_calibration_origin = False
        self.calibration.crs_cartesian_custom_origin_cartesian = (50.0, 75.0)
        self.config.set_app_config("trajGeoCoordEnable", "false")

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            with patch('mdx.analytics.core.transform.calibration.calibration.lonlat_to_xy') as mock_lonlat_to_xy:
                mock_lonlat_to_xy.return_value = (500.0, 600.0)
                
                coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)
                
                # Verify coordinate calculation with custom origin offset
                expected_x = 500.0 - 50.0  # global_x - custom_origin_x
                expected_y = 600.0 - 75.0  # global_y - custom_origin_y
                self.assertEqual(coordinate.x, expected_x)
                self.assertEqual(coordinate.y, expected_y)

    def test_transform_bbox_cartesian_to_latlon_with_calibration_origin(self):
        """Test transform_bbox with cartesian input, latlon output using calibration origin."""
        bbox = Bbox(leftX=100, topY=100, rightX=130, bottomY=140)
        sensor_id = "test_sensor"

        # Mock sensor with origin and homography
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)
        mock_sensor.homography = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

        # Configure calibration for cartesian input, latlon output
        self.calibration.input_data_in_cartesian = True
        self.calibration.use_calibration_origin = True
        self.config.set_app_config("trajGeoCoordEnable", "true")

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            with patch('mdx.analytics.core.transform.calibration.calibration.lonlat_to_xy') as mock_lonlat_to_xy:
                with patch('mdx.analytics.core.transform.calibration.calibration.xy_to_lonlat') as mock_xy_to_lonlat:
                    mock_lonlat_to_xy.return_value = (100.0, 200.0)  # origin in cartesian
                    mock_xy_to_lonlat.return_value = (-122.4194, 37.7749)  # final latlon
                    
                    coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)
                    
                    # Verify coordinate and location match converted latlon
                    self.assertEqual(coordinate.x, -122.4194)
                    self.assertEqual(coordinate.y, 37.7749)
                    self.assertEqual(location.lat, 37.7749)
                    self.assertEqual(location.lon, -122.4194)

    def test_transform_bbox_cartesian_to_latlon_with_custom_origin(self):
        """Test transform_bbox with cartesian input, latlon output using custom origin."""
        bbox = Bbox(leftX=100, topY=100, rightX=130, bottomY=140)
        sensor_id = "test_sensor"

        # Mock sensor with origin and homography
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)
        mock_sensor.homography = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

        # Configure calibration for cartesian input, latlon output with custom origin
        self.calibration.input_data_in_cartesian = True
        self.calibration.use_calibration_origin = False
        self.calibration.crs_cartesian_custom_origin_cartesian = (50.0, 75.0)
        self.config.set_app_config("trajGeoCoordEnable", "true")

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            with patch('mdx.analytics.core.transform.calibration.calibration.xy_to_lonlat') as mock_xy_to_lonlat:
                mock_xy_to_lonlat.return_value = (-122.4194, 37.7749)
                
                coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)
                
                # Verify coordinate and location match converted latlon
                self.assertEqual(coordinate.x, -122.4194)
                self.assertEqual(coordinate.y, 37.7749)
                self.assertEqual(location.lat, 37.7749)
                self.assertEqual(location.lon, -122.4194)

    def test_transform_bbox_sensor_not_found(self):
        """Test transform_bbox when sensor is not in sensor_map."""
        bbox = Bbox(leftX=100, topY=100, rightX=130, bottomY=140)
        sensor_id = "nonexistent_sensor"

        # Ensure sensor_map is empty
        self.calibration.sensor_map = {}
        
        coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)
        
        # Should return default values when sensor not found
        expected_px = (bbox.rightX + bbox.leftX) / 2.0
        expected_py = max(bbox.topY, bbox.bottomY)
        self.assertEqual(coordinate.x, expected_px)
        self.assertEqual(coordinate.y, expected_py)
        self.assertEqual(coordinate.z, 0)
        self.assertEqual(location.lat, 0)
        self.assertEqual(location.lon, 0)

    def test_transform_bbox_none_global_coords(self):
        """Test transform_bbox when perspective_transform returns None."""
        bbox = Bbox(leftX=100, topY=100, rightX=130, bottomY=140)
        sensor_id = "test_sensor"

        # Mock sensor with origin and homography
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)
        mock_sensor.homography = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

        # Configure calibration
        self.calibration.input_data_in_cartesian = False
        self.config.set_app_config("trajGeoCoordEnable", "true")

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            with patch.object(self.calibration, 'perspective_transform') as mock_perspective:
                mock_perspective.return_value = None  # Simulate transformation failure
                
                coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)
                
                # Should fall back to pixel coordinates
                expected_px = (bbox.rightX + bbox.leftX) / 2.0
                expected_py = max(bbox.topY, bbox.bottomY)
                self.assertEqual(coordinate.x, expected_px)
                self.assertEqual(coordinate.y, expected_py)
                self.assertEqual(location.lat, expected_py)
                self.assertEqual(location.lon, expected_px)

    def test_transform_frame_with_objects(self):
        """Test transform_frame with actual objects."""
        # Create test frame with objects
        timestamp = Timestamp()
        timestamp.FromDatetime(datetime.now())
        
        # Create test objects
        obj1 = nvSchema.Object()
        obj1.id = "obj1"
        obj1.type = "person"
        obj1.bbox.leftX = 100
        obj1.bbox.rightX = 130
        obj1.bbox.topY = 100
        obj1.bbox.bottomY = 140
        
        obj2 = nvSchema.Object()
        obj2.id = "obj2"
        obj2.type = "vehicle"
        obj2.bbox.leftX = 200
        obj2.bbox.rightX = 250
        obj2.bbox.topY = 150
        obj2.bbox.bottomY = 200
        
        frame = nvSchema.Frame(
            version="1.0",
            id="test_frame",
            timestamp=timestamp,
            sensorId="test_sensor",
            objects=[obj1, obj2]
        )

        # Mock sensor with place information
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = "test_sensor"
        mock_sensor.place = [{"name": "building", "value": "test-building"}]

        with patch.object(self.calibration, 'sensor_map', {"test_sensor": mock_sensor}):
            with patch.object(self.calibration, "transform_bbox") as mock_transform:
                with patch.object(self.calibration, "get_roi_metrics") as mock_roi:
                    with patch.object(self.calibration, "get_fov") as mock_fov:
                        # Mock return values
                        mock_transform.side_effect = [
                            (Coordinate(x=1, y=2), Location(lat=3, lon=4)),
                            (Coordinate(x=5, y=6), Location(lat=7, lon=8))
                        ]
                        mock_roi.return_value = []
                        mock_fov.return_value = []

                        transformed_frame = self.calibration.transform_frame(frame)

                        # Verify frame properties
                        self.assertEqual(transformed_frame.sensorId, "test_sensor")
                        self.assertEqual(len(transformed_frame.objects), 2)
                        self.assertEqual(transformed_frame.info["place"], "building=test-building")
                        
                        # Verify transform_bbox was called for each object
                        self.assertEqual(mock_transform.call_count, 2)

    def test_transform_frame_with_compact_objects(self):
        """Test transform_frame with compact frame enabled."""
        # Enable compact frame via app config
        self.config.set_app_config("compactFrame", "true")
        
        timestamp = Timestamp()
        timestamp.FromDatetime(datetime.now())
        
        # Create test objects
        obj1 = nvSchema.Object()
        obj1.id = "obj1"
        obj1.type = "person"
        obj1.bbox.leftX = 100
        obj1.bbox.rightX = 130
        obj1.bbox.topY = 100
        obj1.bbox.bottomY = 140
        
        obj2 = nvSchema.Object()
        obj2.id = "obj2"
        obj2.type = "vehicle"
        obj2.bbox.leftX = 200
        obj2.bbox.rightX = 250
        obj2.bbox.topY = 150
        obj2.bbox.bottomY = 200
        
        frame = nvSchema.Frame(
            version="1.0",
            id="test_frame",
            timestamp=timestamp,
            sensorId="test_sensor",
            objects=[obj1, obj2]
        )

        # Mock sensor
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = "test_sensor"
        mock_sensor.place = []

        # Create proper ROI that contains only obj1
        roi = nvSchema.TypeMetrics()
        roi.objectIds.extend(["obj1"])

        with patch.object(self.calibration, 'sensor_map', {"test_sensor": mock_sensor}):
            with patch.object(self.calibration, "transform_bbox") as mock_transform:
                with patch.object(self.calibration, "get_roi_metrics") as mock_roi_metrics:
                    with patch.object(self.calibration, "get_fov") as mock_fov:
                        # Mock return values
                        mock_transform.side_effect = [
                            (Coordinate(x=1, y=2), Location(lat=3, lon=4)),
                            (Coordinate(x=5, y=6), Location(lat=7, lon=8))
                        ]
                        mock_roi_metrics.return_value = [roi]
                        mock_fov.return_value = []

                        transformed_frame = self.calibration.transform_frame(frame)

                        # Should only contain objects that are in ROIs
                        self.assertEqual(len(transformed_frame.objects), 1)
                        self.assertEqual(transformed_frame.objects[0].id, "obj1")

    def test_transform_frame_sensor_not_found(self):
        """Test transform_frame when sensor is not in sensor_map."""
        timestamp = Timestamp()
        timestamp.FromDatetime(datetime.now())
        
        frame = nvSchema.Frame(
            version="1.0",
            id="test_frame",
            timestamp=timestamp,
            sensorId="nonexistent_sensor",
            objects=[]
        )

        # Ensure sensor_map is empty
        self.calibration.sensor_map = {}

        with patch.object(self.calibration, "get_roi_metrics") as mock_roi:
            with patch.object(self.calibration, "get_fov") as mock_fov:
                mock_roi.return_value = []
                mock_fov.return_value = []

                transformed_frame = self.calibration.transform_frame(frame)

                # Should still return a frame with empty place info
                self.assertEqual(transformed_frame.sensorId, "nonexistent_sensor")
                self.assertEqual(transformed_frame.info["place"], "")

    def test_calibration_type_returns_geo(self):
        """Test that Calibration returns GEO calibration type."""
        self.assertEqual(self.calibration.calibration_type, CalibrationType.GEO)


if __name__ == "__main__":
    unittest.main()
