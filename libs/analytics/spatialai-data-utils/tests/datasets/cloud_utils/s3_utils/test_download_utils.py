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
import os
import pytest
from unittest.mock import patch, MagicMock, mock_open

from spatialai_data_utils.datasets.cloud_utils.s3_utils.common import (
    convert_https_to_s3_url,
    format_aws_s3_base_prefix_path,
)
from spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils import (
    sort_file_by_timestamp, 
    sort_files_in_folders,
    download_and_merge_data_from_s3,
    get_calibration_from_s3
)


def test_sort_file_by_timestamp_sorts_and_filters(tmp_path):
    input_file = tmp_path / "input.json"
    output_file = tmp_path / "output.json"

    lines = [
        {"timestamp": "2025-01-01T00:00:00.300Z", "sensorId": "Camera", "value": 3},
        "{'timestamp': '2025-01-01T00:00:00.200Z', 'sensorId': 'Camera', 'value': 2}",
        {"timestamp": "2025-01-01T00:00:00.100Z", "sensorId": "Camera_01", "value": 1},
        {"timestamp": "2025-01-01T00:00:00.100Z", "sensorId": "0", "value": 1},

    ]

    with open(input_file, "w") as f:
        for entry in lines:
            if isinstance(entry, str):
                f.write(entry + "\n")
            else:
                f.write(json.dumps(entry) + "\n")

    sort_file_by_timestamp(str(input_file), str(output_file))

    # Verify output is sorted ascending by timestamp and filtered
    with open(output_file) as f:
        out = [json.loads(line) for line in f if line.strip()]

    assert [o["timestamp"] for o in out] == [
        "2025-01-01T00:00:00.100Z",
        "2025-01-01T00:00:00.200Z",
        "2025-01-01T00:00:00.300Z",
    ]
    # Ensure filtered entry not present (sensorId == "0")
    assert all(o["sensorId"] != "0" for o in out)


def test_sort_files_in_folders_creates_sorted_outputs(tmp_path):
    base_dir = tmp_path / "dataset"
    base_dir.mkdir()

    # Create only a subset of expected datasets; missing ones are skipped
    gt_dir = base_dir / "ground-truth"
    bev_dir = base_dir / "mdx-bev"
    gt_dir.mkdir()
    bev_dir.mkdir()

    # ground-truth.json (unsorted)
    gt_input = gt_dir / "ground-truth.json"
    gt_lines = [
        {"timestamp": "2025-01-01T00:00:00.300Z", "sensorId": "Camera_01"},
        {"timestamp": "2025-01-01T00:00:00.100Z", "sensorId": "Camera_02"},
    ]
    with open(gt_input, "w") as f:
        for obj in gt_lines:
            f.write(json.dumps(obj) + "\n")

    # mdx-bev.json (unsorted)
    bev_input = bev_dir / "mdx-bev.json"
    bev_lines = [
        {"timestamp": "2025-01-01T00:00:00.400Z", "sensorId": "bev-sensor-1"},
        {"timestamp": "2025-01-01T00:00:00.200Z", "sensorId": "bev-sensor-1"},
    ]
    with open(bev_input, "w") as f:
        for obj in bev_lines:
            f.write(json.dumps(obj) + "\n")

    sort_files_in_folders(str(base_dir))

    # Verify sorted outputs exist and are sorted
    gt_sorted = gt_dir / "ground-truth-sorted.json"
    bev_sorted = bev_dir / "mdx-bev-sorted.json"
    assert gt_sorted.is_file()
    assert bev_sorted.is_file()

    with open(gt_sorted) as f:
        gt_out = [json.loads(line) for line in f if line.strip()]
    assert [o["timestamp"] for o in gt_out] == [
        "2025-01-01T00:00:00.100Z",
        "2025-01-01T00:00:00.300Z",
    ]

    with open(bev_sorted) as f:
        bev_out = [json.loads(line) for line in f if line.strip()]
    assert [o["timestamp"] for o in bev_out] == [
        "2025-01-01T00:00:00.200Z",
        "2025-01-01T00:00:00.400Z",
    ]


def test_sort_files_in_folders_exits_when_mdx_bev_empty(tmp_path):
    base_dir = tmp_path / "dataset2"
    base_dir.mkdir()

    # Create empty mdx-bev file
    bev_dir = base_dir / "mdx-bev"
    bev_dir.mkdir()
    open(bev_dir / "mdx-bev.json", "w").close()

    import pytest
    with pytest.raises(SystemExit):
        sort_files_in_folders(str(base_dir))


# Test cases for format_aws_s3_base_prefix_path
def test_format_aws_s3_base_prefix_path_with_slash():
    result = format_aws_s3_base_prefix_path("test/path/")
    assert result == "test/path/"


def test_format_aws_s3_base_prefix_path_without_slash():
    result = format_aws_s3_base_prefix_path("test/path")
    assert result == "test/path/"


def test_format_aws_s3_base_prefix_path_empty():
    result = format_aws_s3_base_prefix_path("")
    assert result == ""


# Test cases for convert_https_to_s3_url
def test_convert_https_to_s3_url_standard_format():
    url = "https://s3.amazonaws.com/bucket-name/path/to/file.json"
    result = convert_https_to_s3_url(url)
    assert result == "s3://bucket-name/path/to/file.json"


def test_convert_https_to_s3_url_regional_format():
    url = "https://bucket-name.s3.us-east-1.amazonaws.com/path/to/file.json"
    result = convert_https_to_s3_url(url)
    assert result == "s3://bucket-name/path/to/file.json"


def test_convert_https_to_s3_url_with_query_params():
    url = "https://s3.amazonaws.com/bucket-name/path/to/file.json?s3.param=value"
    result = convert_https_to_s3_url(url)
    assert result == "s3://bucket-name/path/to/file.json?s3.param=value"


def test_convert_https_to_s3_url_invalid_format():
    url = "https://example.com/file.json"
    result = convert_https_to_s3_url(url)
    assert result == "https://example.com/file.json"  # Returns original URL unchanged


# Test cases for download_and_merge_data_from_s3 (mocked)
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.multiprocessing.Pool')
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.get_s3_client')
def test_download_and_merge_data_from_s3_success(mock_get_s3_client, mock_pool):
    # Mock S3 client and paginator
    mock_s3_client = MagicMock()
    mock_paginator = MagicMock()
    mock_page_iterator = MagicMock()
    
    mock_get_s3_client.return_value = mock_s3_client
    mock_s3_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = mock_page_iterator
    
    # Mock paginated results with file contents
    mock_page_iterator.__iter__.return_value = [
        {
            'Contents': [
                {'Key': 'test/sim1/ground-truth/file1.json', 'Size': 10},
                {'Key': 'test/sim1/ground-truth/file2.json', 'Size': 20}
            ]
        }
    ]

    mock_s3_client.download_file.return_value = None
    mock_pool.return_value.__enter__.return_value.apply_async.return_value.get.return_value = True

    args = MagicMock()
    args.only_mdx_bev_validation = False

    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket",
        "AWS_S3_BASE_PREFIX_PATH": "test/",
        "SIMULATION_ID": "sim1",
    }
    
    # Mock os.makedirs to avoid actual directory creation
    with patch('os.makedirs'), patch('builtins.open', mock_open()) as mock_file:
        download_and_merge_data_from_s3(
            args,
            env_variables,
            "/tmp"
        )
    
    # Verify S3 client was called correctly
    mock_s3_client.get_paginator.assert_called_with('list_objects_v2')
    mock_s3_client.download_file.assert_called()


@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.get_s3_client')
def test_download_and_merge_data_from_s3_no_files(mock_get_s3_client):
    # Mock S3 client and paginator
    mock_s3_client = MagicMock()
    mock_paginator = MagicMock()
    mock_page_iterator = MagicMock()
    
    mock_get_s3_client.return_value = mock_s3_client
    mock_s3_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = mock_page_iterator
    
    # Mock empty paginated results
    mock_page_iterator.__iter__.return_value = [
        {'Contents': []}  # No files found
    ]
    
    args = MagicMock()
    args.only_mdx_bev_validation = False

    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket",
        "AWS_S3_BASE_PREFIX_PATH": "test/",
        "SIMULATION_ID": "sim1",
    }

    # Mock os.makedirs to avoid actual directory creation
    with patch('os.makedirs'), patch('builtins.open', mock_open()) as mock_file:
        download_and_merge_data_from_s3(
            args,
            env_variables,
            "/tmp"
        )


@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.download_single_file_from_s3_streaming')
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.get_s3_client')
def test_download_and_merge_data_from_s3_raises_on_failed_download(mock_get_s3_client, mock_download_file, tmp_path):
    mock_s3_client = MagicMock()
    mock_paginator = MagicMock()
    mock_page_iterator = MagicMock()

    mock_get_s3_client.return_value = mock_s3_client
    mock_s3_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = mock_page_iterator
    mock_page_iterator.__iter__.return_value = [
        {
            'Contents': [
                {'Key': 'test/sim1/mdx-bev/file1.json', 'Size': 10},
            ]
        }
    ]
    mock_download_file.return_value = None

    args = MagicMock()
    args.only_mdx_bev_validation = True

    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket",
        "AWS_S3_BASE_PREFIX_PATH": "test/",
        "SIMULATION_ID": "sim1",
    }

    with pytest.raises(RuntimeError, match="Failed to download 1 S3 objects"):
        download_and_merge_data_from_s3(args, env_variables, str(tmp_path))


@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.download_single_file_from_s3_streaming')
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.get_s3_client')
def test_download_and_merge_data_from_s3_raises_on_merge_failure(mock_get_s3_client, mock_download_file, tmp_path):
    mock_s3_client = MagicMock()
    mock_paginator = MagicMock()
    mock_page_iterator = MagicMock()

    mock_get_s3_client.return_value = mock_s3_client
    mock_s3_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = mock_page_iterator
    mock_page_iterator.__iter__.return_value = [
        {
            'Contents': [
                {'Key': 'test/sim1/mdx-bev/file1.json', 'Size': 10},
            ]
        }
    ]
    mock_download_file.return_value = str(tmp_path / "missing-temp-file.json")

    args = MagicMock()
    args.only_mdx_bev_validation = True

    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket",
        "AWS_S3_BASE_PREFIX_PATH": "test/",
        "SIMULATION_ID": "sim1",
    }

    with pytest.raises(RuntimeError, match="Failed to merge 1 downloaded files"):
        download_and_merge_data_from_s3(args, env_variables, str(tmp_path))


# Test cases for get_calibration_from_s3 (mocked)
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.get_s3_client')
def test_get_calibration_from_s3_s3_url(mock_get_s3_client):
    mock_s3_client = MagicMock()
    mock_get_s3_client.return_value = mock_s3_client
    
    with patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.os.path.exists', return_value=False), \
         patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.os.makedirs') as mock_makedirs, \
         patch('builtins.open', mock_open()) as mock_file:
        get_calibration_from_s3(
            access_key_id="test_key",
            secret_access_key="test_secret",
            region="us-east-1", 
            calibration_url="s3://test-bucket/path/calibration.json",
            simulation_id="sim1",
            output_root_dir="/tmp"
        )
    
    mock_makedirs.assert_called_once_with("/tmp/sim1", exist_ok=True)
    mock_s3_client.download_file.assert_called_once_with(
        "test-bucket", 
        "path/calibration.json", 
        "/tmp/sim1/calibration.json"
    )


@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.requests.get')
def test_get_calibration_from_s3_http_url(mock_requests_get):
    mock_response = MagicMock()
    mock_response.content = b'{"calibration": "data"}'
    mock_requests_get.return_value = mock_response
    
    with patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.os.path.exists', return_value=False), \
         patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.os.makedirs'), \
         patch('builtins.open', mock_open()) as mock_file:
        get_calibration_from_s3(
            access_key_id="test_key",
            secret_access_key="test_secret",
            region="us-east-1", 
            calibration_url="https://example.com/calibration.json",
            simulation_id="sim1",
            output_root_dir="/tmp"
        )
    
    mock_requests_get.assert_called_once_with("https://example.com/calibration.json")


def test_get_calibration_from_s3_file_exists_skip():
    with patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.os.path.exists', return_value=True), \
         patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.os.makedirs'):
        # Should not raise any exception and should skip download
        get_calibration_from_s3(
            access_key_id="test_key",
            secret_access_key="test_secret",
            region="us-east-1", 
            calibration_url="s3://test-bucket/calibration.json",
            simulation_id="sim1",
            output_root_dir="/tmp",
            overwrite_file=False
        )


def test_get_calibration_from_s3_invalid_url():
    with patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils.os.makedirs'), \
         pytest.raises(ValueError, match="Unsupported URL scheme"):
        get_calibration_from_s3(
            access_key_id="test_key",
            secret_access_key="test_secret",
            region="us-east-1", 
            calibration_url="ftp://example.com/calibration.json",
            simulation_id="sim1",
            output_root_dir="/tmp"
        )


# Additional test cases for sort_file_by_timestamp edge cases
def test_sort_file_by_timestamp_missing_timestamp_field(tmp_path):
    input_file = tmp_path / "input.json"
    output_file = tmp_path / "output.json"
    
    lines = [
        '{"sensorId": "Camera1", "value": 1}',  # Missing timestamp
        '{"timestamp": "2025-01-01T00:00:00.100Z", "sensorId": "Camera2", "value": 2}'
    ]
    
    with open(input_file, "w") as f:
        for line in lines:
            f.write(line + "\n")
    
    sort_file_by_timestamp(str(input_file), str(output_file))
    
    # Should only contain the entry with timestamp
    with open(output_file) as f:
        out = [json.loads(line) for line in f if line.strip()]
    
    assert len(out) == 1
    assert out[0]["sensorId"] == "Camera2"


def test_sort_file_by_timestamp_invalid_json_line(tmp_path):
    input_file = tmp_path / "input.json"
    output_file = tmp_path / "output.json"
    
    lines = [
        '{"timestamp": "2025-01-01T00:00:00.100Z", "sensorId": "Camera1", "value": 1}',
        'invalid json line',
        '{"timestamp": "2025-01-01T00:00:00.200Z", "sensorId": "Camera2", "value": 2}'
    ]
    
    with open(input_file, "w") as f:
        for line in lines:
            f.write(line + "\n")
    
    # Should raise SystemExit due to invalid JSON
    with pytest.raises(SystemExit):
        sort_file_by_timestamp(str(input_file), str(output_file))


def test_sort_file_by_timestamp_custom_timestamp_field(tmp_path):
    input_file = tmp_path / "input.json"
    output_file = tmp_path / "output.json"
    
    lines = [
        '{"time": "2025-01-01T00:00:00.200Z", "sensorId": "Camera1", "value": 1}',
        '{"time": "2025-01-01T00:00:00.100Z", "sensorId": "Camera2", "value": 2}'
    ]
    
    with open(input_file, "w") as f:
        for line in lines:
            f.write(line + "\n")
    
    sort_file_by_timestamp(str(input_file), str(output_file), timestamp_field="time")
    
    with open(output_file) as f:
        out = [json.loads(line) for line in f if line.strip()]
    
    assert [o["time"] for o in out] == [
        "2025-01-01T00:00:00.100Z",
        "2025-01-01T00:00:00.200Z"
    ]


# Additional test cases for sort_files_in_folders edge cases
def test_sort_files_in_folders_missing_dataset_directories(tmp_path):
    base_dir = tmp_path / "dataset"
    base_dir.mkdir()
    
    # Only create ground-truth directory, others are missing
    gt_dir = base_dir / "ground-truth"
    gt_dir.mkdir()
    
    gt_input = gt_dir / "ground-truth.json"
    gt_lines = [
        {"timestamp": "2025-01-01T00:00:00.200Z", "sensorId": "Camera1"},
        {"timestamp": "2025-01-01T00:00:00.100Z", "sensorId": "Camera2"}
    ]
    with open(gt_input, "w") as f:
        for obj in gt_lines:
            f.write(json.dumps(obj) + "\n")
    
    # Should not raise any exception
    sort_files_in_folders(str(base_dir))
    
    # Verify sorted output exists
    gt_sorted = gt_dir / "ground-truth-sorted.json"
    assert gt_sorted.is_file()


def test_sort_files_in_folders_empty_file(tmp_path):
    base_dir = tmp_path / "dataset"
    base_dir.mkdir()
    
    # Create empty file
    gt_dir = base_dir / "ground-truth"
    gt_dir.mkdir()
    open(gt_dir / "ground-truth.json", "w").close()  # Empty file
    
    # Should not raise any exception
    sort_files_in_folders(str(base_dir))
    
    # Verify no sorted output was created
    gt_sorted = gt_dir / "ground-truth-sorted.json"
    assert not gt_sorted.exists()
