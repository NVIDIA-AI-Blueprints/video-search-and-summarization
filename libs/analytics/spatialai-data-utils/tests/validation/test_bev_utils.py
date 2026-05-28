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
import logging
import pytest
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime, timezone

from spatialai_data_utils.datasets.cloud_utils.s3_utils.common import list_files
from spatialai_data_utils.datasets.cloud_utils.s3_utils.validation_utils import (
    count_lines_in_s3_object,
    count_the_bev_records_in_s3,
    check_if_bev_files_are_present_in_s3,
)
from spatialai_data_utils.validation.bev_utils import (
    bev_data_validation
)


# Test cases for count_lines_in_s3_object (mocked)
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.common.boto3.client')
def test_count_lines_in_s3_object(mock_boto3_client):
    # Mock S3 client and response
    mock_s3_client = MagicMock()
    mock_response = MagicMock()
    mock_boto3_client.return_value = mock_s3_client
    mock_s3_client.get_object.return_value = mock_response
    
    # Mock response body with 3 lines
    mock_response['Body'].iter_chunks.return_value = [
        b'{"id": 1, "sensorId": "Camera_01"}\n',
        b'{"id": 2, "sensorId": "Camera_02"}\n',
        b'{"id": 3, "sensorId": "Camera_03"}\n'
    ]
    
    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket"
    }
    
    result = count_lines_in_s3_object(env_variables, "test/file.json")
    
    assert result == 3
    mock_s3_client.get_object.assert_called_once_with(Bucket="test-bucket", Key="test/file.json")


# Test cases for list_files (mocked)
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.common.boto3.client')
def test_list_files(mock_boto3_client):
    # Mock S3 client and paginator
    mock_s3_client = MagicMock()
    mock_paginator = MagicMock()
    mock_page_iterator = MagicMock()
    
    mock_boto3_client.return_value = mock_s3_client
    mock_s3_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = mock_page_iterator
    
    # Mock paginated results
    mock_page_iterator.__iter__.return_value = [
        {
            'Contents': [
                {'Key': 'test/file1.json'},
                {'Key': 'test/file2.json'},
                {'Key': 'test/directory/'}  # Directory should be included
            ]
        },
        {
            'Contents': [
                {'Key': 'test/file3.json'}
            ]
        }
    ]
    
    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket"
    }
    
    result = list(list_files(env_variables, "test/"))
    
    expected = ['test/file1.json', 'test/file2.json', 'test/directory/', 'test/file3.json']
    assert result == expected
    mock_s3_client.get_paginator.assert_called_once_with('list_objects_v2')


# Test cases for count_the_bev_records_in_s3 (mocked)
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.validation_utils.list_files')
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.validation_utils.get_s3_client')
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.validation_utils._count_lines_in_s3_object')
def test_count_the_bev_records_in_s3(mock_count_lines, mock_get_s3_client, mock_list_files):
    mock_list_files.return_value = [
        'test/file1.json',
        'test/file2.json',
        'test/directory/',  # Directory should be skipped
        'test/file3.json'
    ]
    mock_s3_client = MagicMock()
    mock_get_s3_client.return_value = mock_s3_client
    mock_count_lines.side_effect = [5, 3, 2]  # Skip directory, so only 3 calls
    
    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket"
    }
    
    result = count_the_bev_records_in_s3(env_variables, "test/")
    
    assert result == 10  # 5 + 3 + 2
    mock_get_s3_client.assert_called_once_with(
        "test_key",
        "test_secret",
        "us-east-1",
        max_pool_connections=8,
    )
    assert mock_count_lines.call_count == 3  # Directory skipped
    assert all(call.args[0] is mock_s3_client for call in mock_count_lines.call_args_list)


# Test cases for check_if_bev_files_are_present_in_s3 (mocked)
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.validation_utils.count_the_bev_records_in_s3')
def test_check_if_bev_files_are_present_in_s3(mock_count_records):
    mock_count_records.return_value = 100
    
    args = MagicMock()
    args.simulation_seconds = 10
    args.fps = 30
    args.bev_record_count_warning_threshold_ratio = 0.8
    args.bev_record_count_error_threshold_ratio = 0.5
    
    env_variables = {
        "AWS_S3_BASE_PREFIX_PATH": "test/",
        "SIMULATION_ID": "test_sim"
    }
    
    result = check_if_bev_files_are_present_in_s3(args, env_variables, 30)
    
    assert result["actual_count"] == 100
    assert result["warning_threshold_record_count"] == 240  # 10 * 30 * 0.8
    assert result["error_threshold_record_count"] == 150   # 10 * 30 * 0.5


# Test cases for bev_data_validation
def test_bev_data_validation_success(tmp_path):
    bev_content = [
        '{"id": 1, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.000Z", "info": {"Camera_01": "2025-01-01T12:00:00.000Z", "Camera_02": "2025-01-01T12:00:00.000Z"}}',
        '{"id": 2, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.033Z", "info": {"Camera_01": "2025-01-01T12:00:00.033Z", "Camera_02": "2025-01-01T12:00:00.033Z"}}',
        '{"id": 3, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.066Z", "info": {"Camera_01": "2025-01-01T12:00:00.066Z", "Camera_02": "2025-01-01T12:00:00.066Z"}}'
    ]
    bev_path = tmp_path / "bev.json"
    bev_path.write_text('\n'.join(bev_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 30
    args.max_tolerance_ms_for_bev_record = 40
    args.bev_intra_record_timestamp_tolerance_ms = 34
    
    result = bev_data_validation(args, str(bev_path), 30)
    
    assert result["status"] is True
    assert "All bev records are within tolerance" in result["message"]


def test_bev_data_validation_missing_info_key(tmp_path):
    """Test validation when info section is missing - should handle gracefully"""
    bev_content = [
        '{"id": 1, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}'
    ]
    bev_path = tmp_path / "bev.json"
    bev_path.write_text('\n'.join(bev_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 30
    args.max_tolerance_ms_for_bev_record = 40
    args.bev_intra_record_timestamp_tolerance_ms = 34
    
    # The function should now handle missing 'info' key gracefully
    result = bev_data_validation(args, str(bev_path), 30)
    
    # Should still pass validation since it's just one record
    assert result["status"] is True
    assert "All bev records are within tolerance" in result["message"]
    assert "Total number of records with no objects: 1 out of total 1 records" in result["message"]


def test_bev_data_validation_missing_info_key_in_faulty_record(tmp_path, caplog):
    bev_content = [
        '{"id": 1, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.000Z", "info": {"Camera_01": "2025-01-01T12:00:00.000Z"}}',
        '{"id": 2, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.100Z", "objects": []}'
    ]
    bev_path = tmp_path / "bev_missing_info_fault.json"
    bev_path.write_text('\n'.join(bev_content))

    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 30
    args.max_tolerance_ms_for_bev_record = 40
    args.bev_intra_record_timestamp_tolerance_ms = 34

    with caplog.at_level(logging.WARNING):
        result = bev_data_validation(args, str(bev_path), 30)

    assert result["status"] is True
    assert "not within tolerance" in caplog.text
    assert "'info': {}" in caplog.text
    assert "1 out of 2 records with inter-record timestamp spacing outside" in result["message"]


def test_bev_data_validation_uses_fps_for_expected_record_count(tmp_path):
    bev_content = []
    for idx in range(10):
        timestamp = f"2025-01-01T12:00:00.{idx * 33:03d}Z"
        bev_content.append(json.dumps({
            "id": idx + 1,
            "sensorId": "Camera_01",
            "timestamp": timestamp,
            "info": {"Camera_01": timestamp},
        }))

    ground_truth_content = [
        '{"sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}',
        '{"sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:02.000Z", "objects": []}',
    ]

    bev_path = tmp_path / "bev_fps.json"
    ground_truth_path = tmp_path / "ground_truth.json"
    bev_path.write_text('\n'.join(bev_content))
    ground_truth_path.write_text('\n'.join(ground_truth_content))

    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 30
    args.max_tolerance_ms_for_bev_record = 40
    args.bev_intra_record_timestamp_tolerance_ms = 34
    args.bev_delay = 0
    args.bev_record_count_error_threshold_ratio = 0.75
    args.bev_record_count_warning_threshold_ratio = 0.9

    result = bev_data_validation(args, str(bev_path), 10, str(ground_truth_path))

    assert result["status"] is False
    assert "Total number of records expected in BEV is 60" in result["message"]


def test_bev_delay_does_not_warn_when_bev_after_ground_truth_within_threshold(tmp_path, caplog):
    bev_content = [
        json.dumps({
            "id": 1,
            "sensorId": "Camera_01",
            "timestamp": "2025-01-01T12:00:00.050Z",
            "info": {"Camera_01": "2025-01-01T12:00:00.050Z"},
        })
    ]
    ground_truth_content = [
        '{"sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}'
    ]

    bev_path = tmp_path / "bev_delay_ok.json"
    ground_truth_path = tmp_path / "ground_truth_delay_ok.json"
    bev_path.write_text('\n'.join(bev_content))
    ground_truth_path.write_text('\n'.join(ground_truth_content))

    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 30
    args.max_tolerance_ms_for_bev_record = 40
    args.bev_intra_record_timestamp_tolerance_ms = 34
    args.bev_delay = 100
    args.bev_record_count_error_threshold_ratio = 0.75
    args.bev_record_count_warning_threshold_ratio = 0.9

    with caplog.at_level(logging.WARNING):
        result = bev_data_validation(args, str(bev_path), 30, str(ground_truth_path))

    assert result["status"] is True
    assert "greater than bev_delay" not in caplog.text


def test_bev_delay_warns_when_threshold_is_exceeded(tmp_path, caplog):
    bev_content = [
        json.dumps({
            "id": 1,
            "sensorId": "Camera_01",
            "timestamp": "2025-01-01T12:00:00.150Z",
            "info": {"Camera_01": "2025-01-01T12:00:00.150Z"},
        })
    ]
    ground_truth_content = [
        '{"sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.000Z", "objects": []}'
    ]

    bev_path = tmp_path / "bev_delay_late.json"
    ground_truth_path = tmp_path / "ground_truth_delay_late.json"
    bev_path.write_text('\n'.join(bev_content))
    ground_truth_path.write_text('\n'.join(ground_truth_content))

    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 30
    args.max_tolerance_ms_for_bev_record = 40
    args.bev_intra_record_timestamp_tolerance_ms = 34
    args.bev_delay = 100
    args.bev_record_count_error_threshold_ratio = 0.75
    args.bev_record_count_warning_threshold_ratio = 0.9

    with caplog.at_level(logging.WARNING):
        result = bev_data_validation(args, str(bev_path), 30, str(ground_truth_path))

    assert result["status"] is True
    assert "150 ms after the first ground truth record" in caplog.text
    assert "greater than bev_delay=100 ms" in caplog.text


def test_bev_data_validation_different_timestamps_in_record(tmp_path):
    bev_content = [
        '{"id": 1, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.000Z", "info": {"Camera_01": "2025-01-01T12:00:00.000Z", "Camera_02": "2025-01-01T12:00:00.040Z"}}'
    ]
    bev_path = tmp_path / "bev.json"
    bev_path.write_text('\n'.join(bev_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 30
    args.max_tolerance_ms_for_bev_record = 40
    args.bev_intra_record_timestamp_tolerance_ms = 34
    
    result = bev_data_validation(args, str(bev_path), 30)
    
    assert result["status"] is True
    assert "There are 1 out of 1 records have unsynchronized timestamps" in result["message"]


def test_bev_data_validation_timestamp_out_of_tolerance(tmp_path, caplog):
    bev_content = [
        '{"id": 1, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.000Z", "info": {"Camera_01": "2025-01-01T12:00:00.000Z", "Camera_02": "2025-01-01T12:00:00.000Z"}}',
        '{"id": 2, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.100Z", "info": {"Camera_01": "2025-01-01T12:00:00.100Z", "Camera_02": "2025-01-01T12:00:00.100Z"}}'  # Too far from previous
    ]
    bev_path = tmp_path / "bev.json"
    bev_path.write_text('\n'.join(bev_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 30
    args.max_tolerance_ms_for_bev_record = 40
    args.bev_intra_record_timestamp_tolerance_ms = 34
    
    with caplog.at_level(logging.WARNING):
        result = bev_data_validation(args, str(bev_path), 30)
    
    assert result["status"] is True
    assert "not within tolerance" in caplog.text
    assert "1 out of 2 records with inter-record timestamp spacing outside" in result["message"]


def test_bev_data_validation_custom_tolerance(tmp_path):
    bev_content = [
        '{"id": 1, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.000Z", "info": {"Camera_01": "2025-01-01T12:00:00.000Z", "Camera_02": "2025-01-01T12:00:00.000Z"}}',
        '{"id": 2, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.040Z", "info": {"Camera_01": "2025-01-01T12:00:00.040Z", "Camera_02": "2025-01-01T12:00:00.040Z"}}'  # Exactly 40ms difference
    ]
    bev_path = tmp_path / "bev.json"
    bev_path.write_text('\n'.join(bev_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 40
    args.max_tolerance_ms_for_bev_record = 60
    args.bev_intra_record_timestamp_tolerance_ms = 34
    
    result = bev_data_validation(args, str(bev_path), 30)
    
    assert result["status"] is True
    assert "All bev records are within tolerance" in result["message"]


def test_bev_data_validation_single_record(tmp_path):
    bev_content = [
        '{"id": 1, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.000Z", "info": {"Camera_01": "2025-01-01T12:00:00.000Z", "Camera_02": "2025-01-01T12:00:00.000Z"}}'
    ]
    bev_path = tmp_path / "bev.json"
    bev_path.write_text('\n'.join(bev_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 30
    args.max_tolerance_ms_for_bev_record = 40
    args.bev_intra_record_timestamp_tolerance_ms = 34
    
    result = bev_data_validation(args, str(bev_path), 30)
    
    assert result["status"] is True
    assert "All bev records are within tolerance" in result["message"]


def test_bev_data_validation_empty_file(tmp_path):
    bev_path = tmp_path / "bev.json"
    bev_path.write_text('')
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 30
    args.max_tolerance_ms_for_bev_record = 40
    args.bev_intra_record_timestamp_tolerance_ms = 34
    
    result = bev_data_validation(args, str(bev_path), 30)
    
    assert result["status"] is False
    assert "Empty BEV file provided" in result["message"]

def test_bev_data_validation_mixed_records_with_empty_objects(tmp_path):
    """Test validation with mix of records with and without objects - covers both lines 65 and 84"""
    bev_content = [
        '{"id": 1, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.000Z", "objects": [], "info": {}}',
        '{"id": 2, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.033Z", "objects": [{"id": "obj1"}], "info": {"Camera_01": "2025-01-01T12:00:00.033Z"}}',
        '{"id": 3, "sensorId": "Camera_01", "timestamp": "2025-01-01T12:00:00.066Z", "objects": [], "info": {}}'
    ]
    bev_path = tmp_path / "bev.json"
    bev_path.write_text('\n'.join(bev_content))
    
    args = MagicMock()
    args.min_tolerance_ms_for_bev_record = 30
    args.max_tolerance_ms_for_bev_record = 40
    args.bev_intra_record_timestamp_tolerance_ms = 34
    
    result = bev_data_validation(args, str(bev_path), 30)
    
    # Should pass validation and mention records with no objects - covers line 84
    assert result["status"] is True
    assert "Total number of records with no objects: 2 out of total 3 records" in result["message"]
