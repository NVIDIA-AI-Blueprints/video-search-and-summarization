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
import json
import tempfile
import os
from unittest.mock import Mock, patch

from mdx.analytics.core.transform.calibration.calibration_dynamic import DynamicCalibration
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationType
from mdx.analytics.core.schema.config import AppConfig


class TestCalibrationType:
    """Tests for CalibrationType enum."""

    def test_image_type(self):
        """Test IMAGE calibration type."""
        assert CalibrationType.IMAGE.value == "image"

    def test_cartesian_type(self):
        """Test CARTESIAN calibration type."""
        assert CalibrationType.CARTESIAN.value == "cartesian"

    def test_geo_type(self):
        """Test GEO calibration type."""
        assert CalibrationType.GEO.value == "geo"


class TestDynamicCalibration:
    """Tests for DynamicCalibration class."""

    @pytest.fixture
    def mock_config(self):
        """Create mock AppConfig."""
        config = Mock(spec=AppConfig)
        return config

    @pytest.fixture
    def calibration_file_image(self):
        """Create a temporary image calibration file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                "calibrationType": "image",
                "sensors": []
            }, f)
            return f.name

    @pytest.fixture
    def calibration_file_cartesian(self):
        """Create a temporary cartesian calibration file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                "calibrationType": "cartesian",
                "sensors": []
            }, f)
            return f.name

    @pytest.fixture
    def calibration_file_geo(self):
        """Create a temporary geo calibration file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                "calibrationType": "geo",
                "sensors": []
            }, f)
            return f.name

    def test_initialization_without_file(self, mock_config):
        """Test initialization without calibration file."""
        with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
            MockCalibrationI.return_value = Mock()
            
            calibration = DynamicCalibration(mock_config, None)
            
            assert calibration.config == mock_config
            assert calibration._started_with_file is False
            MockCalibrationI.assert_called_once_with(mock_config, None)

    def test_initialization_with_image_file(self, mock_config, calibration_file_image):
        """Test initialization with image calibration file."""
        try:
            with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
                MockCalibrationI.return_value = Mock()
                
                calibration = DynamicCalibration(mock_config, calibration_file_image)
                
                assert calibration._started_with_file is True
        finally:
            os.unlink(calibration_file_image)

    def test_initialization_with_cartesian_file(self, mock_config, calibration_file_cartesian):
        """Test initialization with cartesian calibration file."""
        try:
            with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationE') as MockCalibrationE:
                MockCalibrationE.return_value = Mock()
                
                calibration = DynamicCalibration(mock_config, calibration_file_cartesian)
                
                assert calibration._started_with_file is True
        finally:
            os.unlink(calibration_file_cartesian)

    def test_initialization_with_geo_file(self, mock_config, calibration_file_geo):
        """Test initialization with geo calibration file."""
        try:
            with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.Calibration') as MockCalibration:
                MockCalibration.return_value = Mock()
                
                calibration = DynamicCalibration(mock_config, calibration_file_geo)
                
                assert calibration._started_with_file is True
        finally:
            os.unlink(calibration_file_geo)

    def test_get_calibration_type_image(self, mock_config, calibration_file_image):
        """Test getting IMAGE calibration type from file."""
        try:
            with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
                MockCalibrationI.return_value = Mock()
                calibration = DynamicCalibration(mock_config, None)
                
                result = calibration.get_calibration_type(calibration_file_image)
                
                assert result == CalibrationType.IMAGE
        finally:
            os.unlink(calibration_file_image)

    def test_get_calibration_type_cartesian(self, mock_config, calibration_file_cartesian):
        """Test getting CARTESIAN calibration type from file."""
        try:
            with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
                MockCalibrationI.return_value = Mock()
                calibration = DynamicCalibration(mock_config, None)
                
                result = calibration.get_calibration_type(calibration_file_cartesian)
                
                assert result == CalibrationType.CARTESIAN
        finally:
            os.unlink(calibration_file_cartesian)

    def test_get_calibration_type_geo(self, mock_config, calibration_file_geo):
        """Test getting GEO calibration type from file."""
        try:
            with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
                MockCalibrationI.return_value = Mock()
                calibration = DynamicCalibration(mock_config, None)
                
                result = calibration.get_calibration_type(calibration_file_geo)
                
                assert result == CalibrationType.GEO
        finally:
            os.unlink(calibration_file_geo)

    def test_get_calibration_type_unknown_defaults_to_image(self, mock_config):
        """Test that unknown calibration type defaults to IMAGE."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"calibrationType": "unknown"}, f)
            temp_file = f.name
        
        try:
            with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
                MockCalibrationI.return_value = Mock()
                calibration = DynamicCalibration(mock_config, None)
                
                result = calibration.get_calibration_type(temp_file)
                
                assert result == CalibrationType.IMAGE
        finally:
            os.unlink(temp_file)

    def test_get_calibration_type_missing_key_defaults_to_image(self, mock_config):
        """Test that missing calibrationType defaults to IMAGE."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"sensors": []}, f)
            temp_file = f.name
        
        try:
            with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
                MockCalibrationI.return_value = Mock()
                calibration = DynamicCalibration(mock_config, None)
                
                result = calibration.get_calibration_type(temp_file)
                
                assert result == CalibrationType.IMAGE
        finally:
            os.unlink(temp_file)

    def test_transform_delegates_to_calibrator(self, mock_config):
        """Test that transform delegates to calibrator."""
        with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
            mock_calibrator = Mock()
            mock_calibrator.transform.return_value = Mock()
            MockCalibrationI.return_value = mock_calibrator
            
            calibration = DynamicCalibration(mock_config, None)
            msg = Mock()
            
            result = calibration.transform(msg)
            
            mock_calibrator.transform.assert_called_once_with(msg)

    def test_transform_frame_delegates_to_calibrator(self, mock_config):
        """Test that transform_frame delegates to calibrator."""
        with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
            mock_calibrator = Mock()
            mock_calibrator.transform_frame.return_value = Mock()
            MockCalibrationI.return_value = mock_calibrator
            
            calibration = DynamicCalibration(mock_config, None)
            frame = Mock()
            
            result = calibration.transform_frame(frame)
            
            mock_calibrator.transform_frame.assert_called_once_with(frame)

    def test_sensors_property(self, mock_config):
        """Test sensors property delegates to calibrator."""
        with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
            mock_calibrator = Mock()
            mock_calibrator.sensors = [Mock(), Mock()]
            MockCalibrationI.return_value = mock_calibrator
            
            calibration = DynamicCalibration(mock_config, None)
            
            assert len(calibration.sensors) == 2

    def test_sensor_map_property(self, mock_config):
        """Test sensor_map property delegates to calibrator."""
        with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
            mock_calibrator = Mock()
            mock_calibrator.sensor_map = {"sensor1": Mock()}
            MockCalibrationI.return_value = mock_calibrator
            
            calibration = DynamicCalibration(mock_config, None)
            
            assert "sensor1" in calibration.sensor_map

    def test_calibration_type_delegates_to_calibrator(self, mock_config):
        """Test calibration_type property delegates to calibrator."""
        with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
            mock_calibrator = Mock()
            mock_calibrator.calibration_type = CalibrationType.IMAGE
            MockCalibrationI.return_value = mock_calibrator
            
            calibration = DynamicCalibration(mock_config, None)
            
            assert calibration.calibration_type == CalibrationType.IMAGE

    def test_calibration_type_image(self, mock_config, calibration_file_image):
        """Test calibration_type returns IMAGE for image calibration."""
        try:
            with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
                mock_calibrator = Mock()
                mock_calibrator.calibration_type = CalibrationType.IMAGE
                MockCalibrationI.return_value = mock_calibrator
                
                calibration = DynamicCalibration(mock_config, calibration_file_image)
                
                assert calibration.calibration_type == CalibrationType.IMAGE
        finally:
            os.unlink(calibration_file_image)

    def test_calibration_type_cartesian(self, mock_config, calibration_file_cartesian):
        """Test calibration_type returns CARTESIAN for cartesian calibration."""
        try:
            with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationE') as MockCalibrationE:
                mock_calibrator = Mock()
                mock_calibrator.calibration_type = CalibrationType.CARTESIAN
                MockCalibrationE.return_value = mock_calibrator
                
                calibration = DynamicCalibration(mock_config, calibration_file_cartesian)
                
                assert calibration.calibration_type == CalibrationType.CARTESIAN
        finally:
            os.unlink(calibration_file_cartesian)

    def test_calibration_type_geo(self, mock_config, calibration_file_geo):
        """Test calibration_type returns GEO for geo calibration."""
        try:
            with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.Calibration') as MockCalibration:
                mock_calibrator = Mock()
                mock_calibrator.calibration_type = CalibrationType.GEO
                MockCalibration.return_value = mock_calibrator
                
                calibration = DynamicCalibration(mock_config, calibration_file_geo)
                
                assert calibration.calibration_type == CalibrationType.GEO
        finally:
            os.unlink(calibration_file_geo)


class TestDynamicCalibrationReloadData:
    """
    Tests for the :meth:`DynamicCalibration.reload_data` override.

    The override is the single place that hosts the one-time-switch semantic
    (no-file -> typed calibration). Everything else (the watcher itself,
    path filtering, dotfile/``.json`` checks) is inherited from
    :class:`CalibrationBase`, so we focus on the switch logic only.
    """

    @pytest.fixture
    def mock_config(self):
        return Mock(spec=AppConfig)

    def test_reload_data_when_no_file_triggers_one_time_switch(self, mock_config):
        """First reload after starting with ``calibration_path=None`` drives the switch."""
        with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
            MockCalibrationI.return_value = Mock()
            calibration = DynamicCalibration(mock_config, None)

        assert calibration._started_with_file is False

        with patch.object(calibration, '_handle_calibration_change') as mock_handle:
            calibration.reload_data("/tmp/checkpoint/calibration/upsert-all-calibration-x.json")

        mock_handle.assert_called_once_with(
            "/tmp/checkpoint/calibration/upsert-all-calibration-x.json"
        )
        # The flag flip is what guarantees the next event goes through the delegate branch.
        assert calibration._started_with_file is True

    def test_reload_data_after_switch_delegates_to_inner_calibrator(self, mock_config):
        """After the one-time switch, subsequent events delegate to ``_calibrator.reload_data``."""
        with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
            MockCalibrationI.return_value = Mock()
            calibration = DynamicCalibration(mock_config, None)
        calibration._calibrator = Mock()
        calibration._started_with_file = True  # simulate the post-switch state

        calibration.reload_data("/tmp/checkpoint/calibration/upsert-calibration-y.json")

        calibration._calibrator.reload_data.assert_called_once_with(
            "/tmp/checkpoint/calibration/upsert-calibration-y.json"
        )

    def test_reload_data_when_started_with_file_always_delegates(self, mock_config):
        """Started with a file: every event delegates straight to ``_calibrator.reload_data``."""
        with patch('mdx.analytics.core.transform.calibration.calibration_dynamic.CalibrationI') as MockCalibrationI:
            MockCalibrationI.return_value = Mock()
            with patch.object(
                DynamicCalibration, 'get_calibration_type', return_value=CalibrationType.IMAGE
            ):
                calibration = DynamicCalibration(mock_config, "/tmp/some-initial-calibration.json")
        calibration._calibrator = Mock()
        assert calibration._started_with_file is True

        # Two reloads -- both delegate, never switch.
        calibration.reload_data("/tmp/checkpoint/calibration/a.json")
        calibration.reload_data("/tmp/checkpoint/calibration/b.json")

        assert calibration._calibrator.reload_data.call_count == 2
        calibration._calibrator.reload_data.assert_any_call(
            "/tmp/checkpoint/calibration/a.json"
        )
        calibration._calibrator.reload_data.assert_any_call(
            "/tmp/checkpoint/calibration/b.json"
        )

