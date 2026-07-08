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

import json
import pytest
from unittest.mock import patch, MagicMock

from spatialai_data_utils.loaders.calibration import fetch_fps_from_calibration
from spatialai_data_utils.datasets.cloud_utils.common import (
    count_the_files_in_storage,
    get_ldrcolor_directories,
)
from spatialai_data_utils.datasets.cloud_utils.validation_utils import (
    check_if_all_ground_truth_files_are_present_in_storage,
    check_if_all_bin_files_are_present_in_storage,
    extract_sensor_name_from_ldrcolor_path,
    validate_bin_sensors_present_in_storage,
)
from spatialai_data_utils.validation.gt_utils import (
    get_unique_types_from_ground_truth,
    get_sensor_bev_group_map,
    ground_truth_data_validation
)


def test_fetch_fps_from_calibration_returns_float(tmp_path):
    calib = {
        "sensors": [
            {
                "id": "Camera",
                "attributes": [
                    {"name": "fps", "value": "30"},
                    {"name": "other", "value": "x"},
                ],
            }
        ]
    }
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(calib))

    assert fetch_fps_from_calibration(str(path)) == 30.0


def test_fetch_fps_from_calibration_raises_when_missing_fps(tmp_path):
    calib = {
        "sensors": [
            {
                "id": "Camera",
                "attributes": [
                    {"name": "direction", "value": "0"},
                ],
            }
        ]
    }
    path = tmp_path / "calibration_no_fps.json"
    path.write_text(json.dumps(calib))

    with pytest.raises(ValueError):
        fetch_fps_from_calibration(str(path))


def test_fetch_fps_from_calibration_raises_on_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ not: valid json }")

    with pytest.raises((json.JSONDecodeError, ValueError)):
        fetch_fps_from_calibration(str(path))


# Test cases for get_unique_types_from_ground_truth
def test_get_unique_types_from_ground_truth(tmp_path):
    gt_content = [
        '{"sensorId": "Camera1", "timestamp": 1000, "objects": [{"type": "Person"}, {"type": "Box"}]}',
        '{"sensorId": "Camera1", "timestamp": 2000, "objects": [{"type": "Person"}, {"type": "Pallet"}]}',
        '{"sensorId": "Camera1", "timestamp": 3000, "objects": [{"type": "Box"}]}'
    ]
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text('\n'.join(gt_content))
    
    result = get_unique_types_from_ground_truth(str(gt_path))
    
    expected = ["Box", "Pallet", "Person"]
    assert result == expected


def test_get_unique_types_from_ground_truth_blank_type(tmp_path):
    gt_content = [
        '{"sensorId": "Camera1", "timestamp": 1000, "objects": [{"type": ""}]}',
        '{"sensorId": "Camera1", "timestamp": 2000, "objects": [{"type": "Person"}]}'
    ]
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text('\n'.join(gt_content))
    
    with pytest.raises(ValueError, match="Found object with blank/empty type in ground truth file"):
        get_unique_types_from_ground_truth(str(gt_path))

def test_get_unique_types_from_ground_truth_invalid_json_lines(tmp_path):
    gt_content = [
        '{"sensorId": "Camera1", "timestamp": 1000, "objects": [{"type": "Person"}]}',
        'invalid json line',
        '{"sensorId": "Camera1", "timestamp": 2000, "objects": [{"type": "Box"}]}'
    ]
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text('\n'.join(gt_content))
    
    result = get_unique_types_from_ground_truth(str(gt_path))
    
    expected = ["Box", "Person"]
    assert result == expected


# Test cases for get_sensor_bev_group_map
def test_get_sensor_bev_group_map():
    calibration_file = {
        "sensors": [
            {"id": "Camera1", "group": {"name": "bev-sensor-1"}},
            {"id": "Camera2", "group": {"name": "bev-sensor-1"}},
            {"id": "Camera3", "group": {"name": "bev-sensor-2"}}
        ]
    }
    
    result = get_sensor_bev_group_map(calibration_file)
    
    expected = {
        "Camera1": "bev-sensor-1",
        "Camera2": "bev-sensor-1", 
        "Camera3": "bev-sensor-2"
    }
    assert result == expected

def test_extract_sensor_name_from_ldrcolor_path():
    path = "/some/path/_Render_MetroCameraRp/LdrColor/"
    result = extract_sensor_name_from_ldrcolor_path(path)
    assert result == "Camera"  # The function removes 'Rp' but leaves the underscore


# Test cases for validate_bin_sensors_present_in_storage
def test_validate_bin_sensors_present_in_storage_success():
    ldrcolor_directories = [
        "/path/to/_Render_MetroCamera_01Rp/LdrColor/",
        "/path/to/_Render_MetroCamera_02Rp/LdrColor/"
    ]
    
    unique_bev_groups = {"bev-sensor-1"}
    bev_to_sensor_map = {
        "bev-sensor-1": ["Camera_01", "Camera_02"]
    }
    
    result = validate_bin_sensors_present_in_storage(ldrcolor_directories, unique_bev_groups, bev_to_sensor_map)
    
    assert result["status"] is True
    assert "All sensors are present" in result["message"]


def test_validate_bin_sensors_present_in_storage_missing_sensor():
    ldrcolor_directories = [
        "/path/to/_Render_MetroCamera_01Rp/LdrColor/",
        "/path/to/_Render_MetroCamera_02Rp/LdrColor/"
        # Camera3 is missing
    ]
    
    unique_bev_groups = {"bev-sensor-1"}
    bev_to_sensor_map = {
        "bev-sensor-1": ["Camera_01", "Camera_02", "Camera_03"]
    }
    
    result = validate_bin_sensors_present_in_storage(ldrcolor_directories, unique_bev_groups, bev_to_sensor_map)
    
    assert result["status"] is False
    assert "Sensors missing" in result["message"]
    assert "Camera_03" in result["message"]


# Test cases for count_the_files_in_storage (mocked)
@patch('spatialai_data_utils.datasets.cloud_utils.common.boto3.client')
def test_count_the_files_in_storage(mock_boto3_client):
    # Mock S3 client and paginator
    mock_s3_client = MagicMock()
    mock_paginator = MagicMock()
    mock_page_iterator = MagicMock()
    
    mock_boto3_client.return_value = mock_s3_client
    mock_s3_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = mock_page_iterator
    
    # Mock paginated results
    mock_page_iterator.__iter__.return_value = [
        {'KeyCount': 5},
        {'KeyCount': 3},
        {'KeyCount': 2}
    ]
    
    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret", 
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket"
    }
    
    result = count_the_files_in_storage(env_variables, "test/prefix/")
    
    assert result == 10  # 5 + 3 + 2
    mock_s3_client.get_paginator.assert_called_once_with('list_objects_v2')


# Test cases for check_if_all_ground_truth_files_are_present_in_storage (mocked)
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.count_the_files_in_storage')
def test_check_if_all_ground_truth_files_are_present_in_storage(mock_count_files):
    mock_count_files.return_value = 100
    
    args = MagicMock()
    args.simulation_seconds = 10
    args.ground_truth_record_count_warning_threshold_ratio = 0.9
    args.ground_truth_record_count_error_threshold_ratio = 0.8
    
    env_variables = {
        "BASE_PREFIX_PATH": "test/",
        "SIMULATION_ID": "test_sim"
    }
    
    result = check_if_all_ground_truth_files_are_present_in_storage(args, env_variables, 30)
    
    assert result["actual_count"] == 100
    assert result["warning_threshold_record_count"] == 270  # 10 * 30 * 0.9
    assert result["error_threshold_record_count"] == 240   # 10 * 30 * 0.8


# Test cases for ground_truth_data_validation
def test_ground_truth_data_validation_success(tmp_path):
    """Test successful validation with proper sensor sync and record count"""
    gt_content = [
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera2", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.033Z", "objects": []}',
        '{"sensorId": "Camera2", "timestamp": "2025-01-01T12:00:00.033Z", "objects": []}',
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.066Z", "objects": []}',
        '{"sensorId": "Camera2", "timestamp": "2025-01-01T12:00:00.066Z", "objects": []}'
    ]
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text('\n'.join(gt_content))
    
    calibration_content = {
        "sensors": [
            {"id": "Camera1", "group": {"name": "bev-sensor-1"}},
            {"id": "Camera2", "group": {"name": "bev-sensor-1"}}
        ]
    }
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 33
    args.max_tolerance_ms_for_bev_record = 34
    args.simulation_seconds = 3
    args.ground_truth_record_count_warning_threshold_ratio = 0.9
    args.ground_truth_record_count_error_threshold_ratio = 0.8
    
    result = ground_truth_data_validation(args, str(gt_path), str(calibration_path), 1)
    
    assert "actual_count" in result
    assert "warning_threshold_record_count" in result
    assert "error_threshold_record_count" in result
    assert result["actual_count"] == 3  # 3 unique timestamps


def test_ground_truth_data_validation_empty_file(tmp_path):
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text("")

    calibration_content = {
        "sensors": [
            {"id": "Camera1", "group": {"name": "bev-sensor-1"}},
        ]
    }
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration_content))

    result = ground_truth_data_validation(MagicMock(), str(gt_path), str(calibration_path), 1)

    assert result["status"] is False
    assert result["message"] == "Empty or invalid ground truth file"


def test_ground_truth_data_validation_single_timestamp_bucket(tmp_path):
    gt_content = [
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera2", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
    ]
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text('\n'.join(gt_content))

    calibration_content = {
        "sensors": [
            {"id": "Camera1", "group": {"name": "bev-sensor-1"}},
            {"id": "Camera2", "group": {"name": "bev-sensor-1"}},
        ]
    }
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration_content))

    args = MagicMock()
    args.simulation_seconds = 1
    args.ground_truth_record_count_warning_threshold_ratio = 0.9
    args.ground_truth_record_count_error_threshold_ratio = 0.8

    result = ground_truth_data_validation(args, str(gt_path), str(calibration_path), 1)

    assert result["status"] is True
    assert result["actual_count"] == 1
    assert "unique_bev_groups" in result


def test_ground_truth_data_validation_unknown_sensor_id(tmp_path):
    gt_content = [
        '{"sensorId": "Camera3", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.033Z", "objects": []}',
    ]
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text('\n'.join(gt_content))

    calibration_content = {
        "sensors": [
            {"id": "Camera1", "group": {"name": "bev-sensor-1"}},
            {"id": "Camera2", "group": {"name": "bev-sensor-1"}},
        ]
    }
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration_content))

    result = ground_truth_data_validation(MagicMock(), str(gt_path), str(calibration_path), 1)

    assert result["status"] is False
    assert "Unknown sensorId(s) in ground truth" in result["message"]
    assert "Camera3" in result["message"]


def test_ground_truth_data_validation_unsynced_sensors(tmp_path):
    """Test validation failure when sensors are not synced"""
    gt_content = [
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera2", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.033Z", "objects": []}',
        # Camera2 missing for this timestamp
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.066Z", "objects": []}',
        '{"sensorId": "Camera2", "timestamp": "2025-01-01T12:00:00.066Z", "objects": []}'
    ]
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text('\n'.join(gt_content))
    
    calibration_content = {
        "sensors": [
            {"id": "Camera1", "group": {"name": "bev-sensor-1"}},
            {"id": "Camera2", "group": {"name": "bev-sensor-1"}}
        ]
    }
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 33
    args.max_tolerance_ms_for_bev_record = 34
    args.simulation_seconds = 3
    args.ground_truth_record_count_warning_threshold_ratio = 0.9
    args.ground_truth_record_count_error_threshold_ratio = 0.8
    
    result = ground_truth_data_validation(args, str(gt_path), str(calibration_path), 2)
    
    assert result["status"] is False
    assert "Timestamps of sensors are not synced" in result["message"]
    assert "Camera2" in result["message"]


def test_ground_truth_data_validation_wrong_record_count(tmp_path):
    """Test validation failure when record count doesn't match expected"""
    gt_content = [
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera2", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.033Z", "objects": []}',
        '{"sensorId": "Camera2", "timestamp": "2025-01-01T12:00:00.033Z", "objects": []}'
        # Missing 1 more timestamp (expected 3 unique timestamps: 3 seconds * 1 fps)
    ]
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text('\n'.join(gt_content))
    
    calibration_content = {
        "sensors": [
            {"id": "Camera1", "group": {"name": "bev-sensor-1"}},
            {"id": "Camera2", "group": {"name": "bev-sensor-1"}}
        ]
    }
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 33
    args.max_tolerance_ms_for_bev_record = 34
    args.simulation_seconds = 3
    args.ground_truth_record_count_warning_threshold_ratio = 0.9
    args.ground_truth_record_count_error_threshold_ratio = 0.8
    
    result = ground_truth_data_validation(args, str(gt_path), str(calibration_path), 1)
    
    assert "actual_count" in result
    assert "warning_threshold_record_count" in result
    assert "error_threshold_record_count" in result
    assert result["actual_count"] == 2  # 2 unique timestamps


def test_ground_truth_data_validation_tolerance_out_of_range(tmp_path):
    """Test validation failure when timestamp tolerance is out of range"""
    gt_content = [
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera2", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.100Z", "objects": []}',  # 100ms difference, not 33 or 34
        '{"sensorId": "Camera2", "timestamp": "2025-01-01T12:00:00.100Z", "objects": []}'
    ]
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text('\n'.join(gt_content))
    
    calibration_content = {
        "sensors": [
            {"id": "Camera1", "group": {"name": "bev-sensor-1"}},
            {"id": "Camera2", "group": {"name": "bev-sensor-1"}}
        ]
    }
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 33
    args.max_tolerance_ms_for_bev_record = 34
    args.simulation_seconds = 2
    args.ground_truth_record_count_warning_threshold_ratio = 0.9
    args.ground_truth_record_count_error_threshold_ratio = 0.8
    
    result = ground_truth_data_validation(args, str(gt_path), str(calibration_path), 1)

    assert result["status"] is False
    assert "Timestamp gap 100 ms is outside the allowed range [33, 34]" in result["message"]


def test_ground_truth_data_validation_invalid_json_lines(tmp_path):
    """Test validation with invalid JSON lines (should be skipped)"""
    gt_content = [
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera2", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        'invalid json line',
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.033Z", "objects": []}',
        '{"sensorId": "Camera2", "timestamp": "2025-01-01T12:00:00.033Z", "objects": []}'
    ]
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text('\n'.join(gt_content))
    
    calibration_content = {
        "sensors": [
            {"id": "Camera1", "group": {"name": "bev-sensor-1"}},
            {"id": "Camera2", "group": {"name": "bev-sensor-1"}}
        ]
    }
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 33
    args.max_tolerance_ms_for_bev_record = 34
    args.simulation_seconds = 2
    args.ground_truth_record_count_warning_threshold_ratio = 0.9
    args.ground_truth_record_count_error_threshold_ratio = 0.8
    
    result = ground_truth_data_validation(args, str(gt_path), str(calibration_path), 1)
    
    assert "actual_count" in result
    assert "warning_threshold_record_count" in result
    assert "error_threshold_record_count" in result
    assert result["actual_count"] == 2  # 2 unique timestamps


def test_ground_truth_data_validation_single_sensor(tmp_path):
    """Test validation with single sensor"""
    gt_content = [
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.033Z", "objects": []}',
        '{"sensorId": "Camera1", "timestamp": "2025-01-01T12:00:00.066Z", "objects": []}'
    ]
    gt_path = tmp_path / "ground_truth.json"
    gt_path.write_text('\n'.join(gt_content))
    
    calibration_content = {
        "sensors": [
            {"id": "Camera1", "group": {"name": "bev-sensor-1"}}
        ]
    }
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 33
    args.max_tolerance_ms_for_bev_record = 34
    args.simulation_seconds = 3
    args.ground_truth_record_count_warning_threshold_ratio = 0.9
    args.ground_truth_record_count_error_threshold_ratio = 0.8
    
    result = ground_truth_data_validation(args, str(gt_path), str(calibration_path), 1)
    
    assert "actual_count" in result
    assert "warning_threshold_record_count" in result
    assert "error_threshold_record_count" in result
    assert result["actual_count"] == 3  # 3 unique timestamps


# Test cases for get_ldrcolor_directories
@patch('spatialai_data_utils.datasets.cloud_utils.common.boto3.client')
def test_get_ldrcolor_directories_success(mock_boto3_client):
    """Test successful retrieval of LdrColor directories"""
    # Mock S3 client and paginator
    mock_s3_client = MagicMock()
    mock_paginator = MagicMock()
    mock_page_iterator = MagicMock()
    
    mock_boto3_client.return_value = mock_s3_client
    mock_s3_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = mock_page_iterator
    
    # Mock paginated results with LdrColor directories
    mock_page_iterator.__iter__.return_value = [
        {
            'Contents': [
                {'Key': 'simulation1/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCameraRp/LdrColor/file1.bin'},
                {'Key': 'simulation1/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCamera_01Rp/LdrColor/file2.bin'},
                {'Key': 'simulation1/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCamera_02Rp/LdrColor/file3.bin'},
                {'Key': 'simulation1/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCameraRp/SemanticBoundingBox3D/file.json'}  # Should be ignored
            ]
        }
    ]
    
    result = get_ldrcolor_directories(mock_s3_client, "test-bucket", "simulation1/ground-truth/")
    
    expected = [
        'simulation1/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCameraRp/LdrColor/',
        'simulation1/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCamera_01Rp/LdrColor/',
        'simulation1/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCamera_02Rp/LdrColor/'
    ]
    assert result == expected
    mock_s3_client.get_paginator.assert_called_once_with('list_objects_v2')


@patch('spatialai_data_utils.datasets.cloud_utils.common.boto3.client')
def test_get_ldrcolor_directories_skips_shallow_keys(mock_boto3_client):
    """Test shallow keys are skipped while finding LdrColor directories."""
    mock_s3_client = MagicMock()
    mock_paginator = MagicMock()
    mock_page_iterator = MagicMock()

    mock_boto3_client.return_value = mock_s3_client
    mock_s3_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = mock_page_iterator

    mock_page_iterator.__iter__.return_value = [
        {
            'Contents': [
                {'Key': 'root-level-object.bin'},
                {'Key': 'LdrColor'},
                {'Key': 'simulation1/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCameraRp/LdrColor/file1.bin'},
            ]
        }
    ]

    result = get_ldrcolor_directories(mock_s3_client, "test-bucket", "simulation1/ground-truth/")

    assert result == [
        'simulation1/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCameraRp/LdrColor/'
    ]


@patch('spatialai_data_utils.datasets.cloud_utils.common.boto3.client')
def test_get_ldrcolor_directories_no_directories_found(mock_boto3_client):
    """Test when no LdrColor directories are found."""
    mock_s3_client = MagicMock()
    mock_paginator = MagicMock()
    mock_page_iterator = MagicMock()

    mock_boto3_client.return_value = mock_s3_client
    mock_s3_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = mock_page_iterator

    mock_page_iterator.__iter__.return_value = [
        {
            'Contents': [
                {'Key': 'simulation1/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCameraRp/SemanticBoundingBox3D/0.json'},
                {'Key': 'simulation1/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCamera_01Rp/SemanticIdMap/0.json'},
            ]
        }
    ]

    with pytest.raises(RuntimeError, match="No directories containing 'LdrColor/' found"):
        get_ldrcolor_directories(mock_s3_client, "test-bucket", "simulation1/ground-truth/")


@patch('spatialai_data_utils.datasets.cloud_utils.common.boto3.client')
def test_get_ldrcolor_directories_exception_handling(mock_boto3_client):
    """Test storage client errors propagate from get_ldrcolor_directories."""
    mock_s3_client = MagicMock()
    mock_s3_client.get_paginator.side_effect = Exception("S3 connection failed")

    mock_boto3_client.return_value = mock_s3_client

    with pytest.raises(Exception, match="S3 connection failed"):
        get_ldrcolor_directories(mock_s3_client, "test-bucket", "simulation1/ground-truth/")


# Test cases for check_if_all_bin_files_are_present_in_storage
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.get_ldrcolor_directories')
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.validate_bin_sensors_present_in_storage')
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.count_the_files_in_storage')
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.get_storage_client')
def test_check_if_all_bin_files_are_present_in_storage_success(mock_get_storage_client, mock_count_files, mock_validate_sensors, mock_get_directories):
    """Test successful validation of bin files"""
    # Mock arguments
    args = MagicMock()
    args.simulation_seconds = 10
    args.ground_truth_record_count_warning_threshold_ratio = 0.8
    args.ground_truth_record_count_error_threshold_ratio = 0.5
    
    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket",
        "BASE_PREFIX_PATH": "test/",
        "SIMULATION_ID": "test_sim"
    }
    
    fps = 30
    unique_bev_groups = {"group1"}
    bev_to_sensor_map = {"group1": ["Camera", "Camera_01"]}
    
    # Mock return values
    mock_get_directories.return_value = [
        "test/test_sim/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCameraRp/LdrColor/",
        "test/test_sim/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCamera_01Rp/LdrColor/"
    ]
    
    mock_validate_sensors.return_value = {
        "status": True,
        "message": "All sensors found"
    }
    
    mock_count_files.side_effect = [300, 300]  # Above warning threshold
    
    result = check_if_all_bin_files_are_present_in_storage(args, env_variables, fps, bev_to_sensor_map, unique_bev_groups)
    
    assert result["status"] is True
    assert "All bin files are present in object storage." in result["message"]


@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.get_ldrcolor_directories')
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.validate_bin_sensors_present_in_storage')
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.count_the_files_in_storage')
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.get_storage_client')
def test_check_if_all_bin_files_are_present_in_storage_error_threshold(mock_get_storage_client, mock_count_files, mock_validate_sensors, mock_get_directories):
    """Test when bin file count is below error threshold"""
    # Mock arguments
    args = MagicMock()
    args.simulation_seconds = 10
    args.ground_truth_record_count_warning_threshold_ratio = 0.8
    args.ground_truth_record_count_error_threshold_ratio = 0.5
    
    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket",
        "BASE_PREFIX_PATH": "test/",
        "SIMULATION_ID": "test_sim"
    }
    
    fps = 30
    unique_bev_groups = {"group1"}
    bev_to_sensor_map = {"group1": ["Camera_01"]}
    
    # Mock return values
    mock_get_directories.return_value = [
        "test/test_sim/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCamera_01Rp/LdrColor/"
    ]
    
    mock_validate_sensors.return_value = {
        "status": True,
        "message": "All sensors found"
    }
    
    mock_count_files.return_value = 100  # Below error threshold (150)
    
    result = check_if_all_bin_files_are_present_in_storage(args, env_variables, fps, bev_to_sensor_map, unique_bev_groups)
    
    assert result["status"] is False
    assert "Number of bin files in object storage for" in result["message"]
    assert "which is less than expected error threshold count" in result["message"]


@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.get_ldrcolor_directories')
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.validate_bin_sensors_present_in_storage')
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.count_the_files_in_storage')
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.get_storage_client')
def test_check_if_all_bin_files_are_present_in_storage_warning_threshold(mock_get_storage_client, mock_count_files, mock_validate_sensors, mock_get_directories):
    """Test when bin file count is below warning threshold"""
    # Mock arguments
    args = MagicMock()
    args.simulation_seconds = 10
    args.ground_truth_record_count_warning_threshold_ratio = 0.8
    args.ground_truth_record_count_error_threshold_ratio = 0.5
    
    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket",
        "BASE_PREFIX_PATH": "test/",
        "SIMULATION_ID": "test_sim"
    }
    
    fps = 30
    unique_bev_groups = {"group1"}
    bev_to_sensor_map = {"group1": ["Camera_01"]}
    
    # Mock return values
    mock_get_directories.return_value = [
        "test/test_sim/ground-truth/Camera_01/LdrColor/"
    ]
    
    mock_validate_sensors.return_value = {
        "status": True,
        "message": "All sensors found"
    }
    
    mock_count_files.return_value = 200  # Below warning threshold (240) but above error threshold (150)
    
    result = check_if_all_bin_files_are_present_in_storage(args, env_variables, fps, bev_to_sensor_map, unique_bev_groups)
    
    assert result["status"] is True
    assert "Number of bin files in object storage for" in result["message"]
    assert "which is less than expected warning threshold count" in result["message"]
    assert "Total number of bin files expected for each sensor" in result["message"]


@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.get_ldrcolor_directories')
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.validate_bin_sensors_present_in_storage')
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.get_storage_client')
@patch('spatialai_data_utils.datasets.cloud_utils.validation_utils.sys.exit')
def test_check_if_all_bin_files_are_present_in_storage_sensor_validation_fails(mock_exit, mock_get_storage_client, mock_validate_sensors, mock_get_directories):
    """Test when sensor validation fails"""
    # Mock arguments
    args = MagicMock()
    args.simulation_seconds = 10
    args.ground_truth_record_count_warning_threshold_ratio = 0.8
    args.ground_truth_record_count_error_threshold_ratio = 0.5
    
    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket",
        "BASE_PREFIX_PATH": "test/",
        "SIMULATION_ID": "test_sim"
    }
    
    fps = 30
    unique_bev_groups = {"group1"}
    bev_to_sensor_map = {"group1": ["Camera_01"]}
    
    # Mock return values
    mock_get_directories.return_value = [
        "test/test_sim/ground-truth/MetroSensor_Bridge_0_50060/_Render_MetroCamera_01Rp/LdrColor/"
    ]
    
    mock_validate_sensors.return_value = {
        "status": False,
        "message": "Some sensors missing"
    }
    
    mock_exit.side_effect = SystemExit

    with pytest.raises(SystemExit):
        check_if_all_bin_files_are_present_in_storage(args, env_variables, fps, bev_to_sensor_map, unique_bev_groups)
    
    # Should call exit(1) when sensor validation fails
    mock_exit.assert_called_once_with(1)
