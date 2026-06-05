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


from mdx.analytics.core.constants import SENSOR_TYPE_CAMERA
from mdx.analytics.core.schema.models import Bbox, Coordinate, Location, Message, Sensor, SensorInfo
from mdx.analytics.core.transform.calibration.calibration_i import CalibrationI
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.utils.io_utils import load_json_from_file


class TestCalibrationI(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.config = AppConfig(**load_json_from_file("tests/unit/resources/test_config.json"))
        calibration_path = "tests/unit/resources/calibration_building_k.json"
        self.calibration = CalibrationI(self.config, calibration_path)

    def test_initialization(self):
        """Test CalibrationI initialization."""
        self.assertIsInstance(self.calibration, CalibrationI)
        self.assertEqual(self.calibration.config, self.config)

    def test_transform_bbox_bottom_center_mode(self):
        """Test transform_bbox with bottom_center mode (default)."""
        # Create test bbox and sensor
        bbox = Bbox(leftX=100, topY=50, rightX=200, bottomY=150)
        sensor_id = "test_sensor"

        # Mock sensor with origin
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)

        # Set config to bottom_center mode (default)
        self.config.set_app_config("imageLocationMode", "bottom_center")

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            with patch.object(self.calibration, 'contains', return_value=True):
                coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)

                # Verify coordinate calculation
                expected_x = (bbox.rightX + bbox.leftX) / 2.0  # Center X
                expected_y = bbox.bottomY  # Bottom Y (bottom_center mode)
                self.assertEqual(coordinate.x, expected_x, "X should be center of bbox")
                self.assertEqual(coordinate.y, expected_y, "Y should be bottom Y in bottom_center mode")
                self.assertEqual(coordinate.y, 150, "Y should be bottomY value")
                self.assertEqual(coordinate.z, 0)

    def test_transform_bbox_center_mode(self):
        """Test transform_bbox with center mode."""
        # Create test bbox and sensor
        bbox = Bbox(leftX=100, topY=50, rightX=200, bottomY=150)
        sensor_id = "test_sensor"

        # Mock sensor with origin
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)

        # Set config to center mode
        self.config.set_app_config("imageLocationMode", "center")

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            with patch.object(self.calibration, 'contains', return_value=True):
                coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)

                # Verify coordinate calculation
                expected_x = (bbox.rightX + bbox.leftX) / 2.0  # Center X
                expected_y = (bbox.topY + bbox.bottomY) / 2.0  # Center Y (center mode)
                self.assertEqual(coordinate.x, expected_x, "X should be center of bbox")
                self.assertEqual(coordinate.y, expected_y, "Y should be center Y in center mode")
                self.assertEqual(coordinate.y, 100.0, "Y should be average of topY and bottomY")
                self.assertEqual(coordinate.z, 0)

    def test_transform_bbox_default_mode(self):
        """Test transform_bbox with default mode (should be bottom_center)."""
        # Create test bbox and sensor
        bbox = Bbox(leftX=100, topY=50, rightX=200, bottomY=150)
        sensor_id = "test_sensor"

        # Mock sensor with origin
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)

        # Don't set imageLocationMode - should use default
        # Default is "bottom_center" per IMAGE_LOCATION_MODE constant

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            with patch.object(self.calibration, 'contains', return_value=True):
                coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)

                # Verify coordinate calculation uses bottom_center (default)
                expected_x = (bbox.rightX + bbox.leftX) / 2.0
                expected_y = bbox.bottomY  # Default is bottom_center
                self.assertEqual(coordinate.x, expected_x)
                self.assertEqual(coordinate.y, expected_y)
                self.assertEqual(coordinate.y, 150, "Y should be bottomY with default mode")

    def test_transform_bbox_invalid_mode_falls_back_to_bottom_center(self):
        """Test transform_bbox with invalid mode falls back to bottom_center."""
        # Create test bbox and sensor
        bbox = Bbox(leftX=100, topY=50, rightX=200, bottomY=150)
        sensor_id = "test_sensor"

        # Mock sensor with origin
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)

        # Set config to invalid mode
        self.config.set_app_config("imageLocationMode", "invalid_mode")

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            with patch.object(self.calibration, 'contains', return_value=True):
                coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)

                # Should fall back to bottom_center (default behavior)
                expected_x = (bbox.rightX + bbox.leftX) / 2.0
                expected_y = bbox.bottomY  # Falls back to bottom_center
                self.assertEqual(coordinate.x, expected_x)
                self.assertEqual(coordinate.y, expected_y)
                self.assertEqual(coordinate.y, 150, "Should use bottomY for invalid mode")

    def test_transform_bbox_center_mode_different_bbox_values(self):
        """Test transform_bbox center mode with different bbox values."""
        # Test with bbox where topY < bottomY (typical image coordinates)
        bbox = Bbox(leftX=50, topY=20, rightX=150, bottomY=120)
        sensor_id = "test_sensor"

        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)

        self.config.set_app_config("imageLocationMode", "center")

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            with patch.object(self.calibration, 'contains', return_value=True):
                coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)

                expected_x = (150 + 50) / 2.0  # 100
                expected_y = (20 + 120) / 2.0  # 70
                self.assertEqual(coordinate.x, expected_x)
                self.assertEqual(coordinate.y, expected_y)
                self.assertEqual(coordinate.y, 70.0)

    def test_transform_bbox_sensor_not_found_uses_default_origin(self):
        """Test transform_bbox when sensor is not found uses default origin."""
        bbox = Bbox(leftX=100, topY=50, rightX=200, bottomY=150)
        sensor_id = "non_existent_sensor"

        self.config.set_app_config("imageLocationMode", "center")

        with patch.object(self.calibration, 'contains', return_value=False):
            coordinate, location = self.calibration.transform_bbox(bbox, sensor_id)

            self.assertAlmostEqual(location.lat, 0.0, delta=0.002)
            self.assertAlmostEqual(location.lon, 0.0, delta=0.002)
            # Coordinate should still be calculated correctly
            expected_x = (bbox.rightX + bbox.leftX) / 2.0
            expected_y = (bbox.topY + bbox.bottomY) / 2.0
            self.assertEqual(coordinate.x, expected_x)
            self.assertEqual(coordinate.y, expected_y)

    def test_transform_bbox_bbox3d_raises_error(self):
        """Test transform_bbox with Bbox3d raises ValueError."""
        from mdx.analytics.core.schema.models import Bbox3d
        
        bbox3d = Bbox3d(coordinates=[100, 200, 50, 30, 40, 20, 0, 0, 0, 0, 0, 0])
        sensor_id = "test_sensor"

        with self.assertRaises(ValueError) as context:
            self.calibration.transform_bbox(bbox3d, sensor_id)
        
        self.assertIn("CalibrationI only supports 2D bounding boxes", str(context.exception))

    def test_transform_message(self):
        """Test transform method with Message."""
        # Create test message
        sensor = Sensor(id="test_sensor")
        message = Message(messageid="test_message", timestamp=datetime.now(), sensor=sensor)

        # Mock transform_bbox method
        with patch.object(self.calibration, "transform_bbox") as mock_transform:
            mock_transform.return_value = (Coordinate(x=1, y=2), Location(lat=3, lon=4))
            transformed_message = self.calibration.transform(message)

            self.assertEqual(transformed_message.sensor.id, "test_sensor")
            mock_transform.assert_not_called()  # Should not be called since no bbox in message

    def test_image_location_mode_config_property_default(self):
        """Test image_location_mode config property default value."""
        # Create new config instance to test default
        from mdx.analytics.core.utils.io_utils import load_json_from_file
        config = AppConfig(**load_json_from_file("tests/unit/resources/test_config.json"))
        default_mode = config.image_location_mode
        self.assertEqual(default_mode, "bottom_center", "Default should be bottom_center")

    def test_image_location_mode_config_property_center(self):
        """Test image_location_mode config property with center mode."""
        # Create new config instance and set to center
        from mdx.analytics.core.utils.io_utils import load_json_from_file
        config = AppConfig(**load_json_from_file("tests/unit/resources/test_config.json"))
        config.set_app_config("imageLocationMode", "center")
        # Create new instance to avoid cache issues
        config2 = AppConfig(**load_json_from_file("tests/unit/resources/test_config.json"))
        config2.set_app_config("imageLocationMode", "center")
        center_mode = config2.image_location_mode
        self.assertEqual(center_mode, "center", "Should return center when set")

    def test_transform_bbox_coordinate_always_center_x(self):
        """Test that px (X coordinate) is always center regardless of mode."""
        bbox = Bbox(leftX=100, topY=50, rightX=200, bottomY=150)
        sensor_id = "test_sensor"

        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.origin = Location(lat=37.7749, lon=-122.4194)

        # Create separate calibration instances to avoid cache issues
        config_center = AppConfig(**load_json_from_file("tests/unit/resources/test_config.json"))
        config_center.set_app_config("imageLocationMode", "center")
        calibration_center = CalibrationI(config_center, "tests/unit/resources/calibration_building_k.json")

        config_bottom = AppConfig(**load_json_from_file("tests/unit/resources/test_config.json"))
        config_bottom.set_app_config("imageLocationMode", "bottom_center")
        calibration_bottom = CalibrationI(config_bottom, "tests/unit/resources/calibration_building_k.json")

        # Test center mode
        with patch.object(calibration_center, 'sensor_map', {sensor_id: mock_sensor}):
            with patch.object(calibration_center, 'contains', return_value=True):
                coord_center, _ = calibration_center.transform_bbox(bbox, sensor_id)
                x_center = coord_center.x
                y_center = coord_center.y

        # Test bottom_center mode
        with patch.object(calibration_bottom, 'sensor_map', {sensor_id: mock_sensor}):
            with patch.object(calibration_bottom, 'contains', return_value=True):
                coord_bottom, _ = calibration_bottom.transform_bbox(bbox, sensor_id)
                x_bottom = coord_bottom.x
                y_bottom = coord_bottom.y

        # X coordinate should be the same in both modes
        self.assertEqual(x_center, x_bottom, "X coordinate should always be center regardless of mode")
        self.assertEqual(x_center, 150.0, "X should be (100 + 200) / 2 = 150")
        # But Y should be different
        self.assertNotEqual(y_center, y_bottom, "Y coordinate should differ between modes")
        self.assertEqual(y_center, 100.0, "Center mode Y should be average: (50 + 150) / 2 = 100")
        self.assertEqual(y_bottom, 150.0, "Bottom center mode Y should be bottomY: 150")


if __name__ == "__main__":
    unittest.main()
