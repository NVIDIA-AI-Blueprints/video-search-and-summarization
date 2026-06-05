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

import os
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock, mock_open
from io import StringIO

from spatialai_data_utils.datasets.cloud_utils.s3_utils.upload_utils import (
    combine_and_upload_detection_metrics_csv_to_s3,
    upload_csv_to_s3
)


# Test cases for combine_and_upload_detection_metrics_csv_to_s3
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.upload_utils.upload_csv_to_s3')
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.upload_utils.pd.read_csv')
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.upload_utils.os.walk')
@patch('pandas.DataFrame.to_csv')
def test_combine_and_upload_detection_metrics_csv_to_s3_success(mock_to_csv, mock_walk, mock_read_csv, mock_upload_csv):
    # Mock os.walk to return directories with detection_metrics.csv files
    mock_walk.return_value = [
        ('/path/to/results1', [], ['detection_metrics.csv', 'other_file.txt']),
        ('/path/to/results2', [], ['detection_metrics.csv']),
        ('/path/to/results3', [], ['other_file.csv'])  # No detection_metrics.csv
    ]
    
    # Mock pandas read_csv to return different DataFrames
    df1 = pd.DataFrame({'metric1': [1, 2], 'metric2': [3, 4]})
    df2 = pd.DataFrame({'metric1': [5, 6], 'metric2': [7, 8]})
    mock_read_csv.side_effect = [df1, df2]
    
    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket"
    }
    
    combine_and_upload_detection_metrics_csv_to_s3(
        env_variables, 
        "/input/path", 
        "/output/path/combined.csv", 
        "s3/path/combined.csv"
    )
    
    # Verify that read_csv was called for each detection_metrics.csv file
    assert mock_read_csv.call_count == 2
    mock_read_csv.assert_any_call('/path/to/results1/detection_metrics.csv')
    mock_read_csv.assert_any_call('/path/to/results2/detection_metrics.csv')
    
    # Verify that to_csv was called to save the combined DataFrame locally
    mock_to_csv.assert_called_once_with("/output/path/combined.csv", index=False)
    
    # Verify that upload_csv_to_s3 was called with the combined DataFrame
    mock_upload_csv.assert_called_once()
    call_args = mock_upload_csv.call_args
    assert call_args[0][0].equals(pd.concat([df1, df2], ignore_index=True))  # Combined DataFrame
    assert call_args[0][1] == "test_key"
    assert call_args[0][2] == "test_secret"
    assert call_args[0][3] == "us-east-1"
    assert call_args[0][4] == "test-bucket"
    assert call_args[0][5] == "s3/path/combined.csv"


@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.upload_utils.os.walk')
def test_combine_and_upload_detection_metrics_csv_to_s3_no_files(mock_walk):
    # Mock os.walk to return directories without detection_metrics.csv files
    mock_walk.return_value = [
        ('/path/to/results1', [], ['other_file.txt']),
        ('/path/to/results2', [], ['other_file.csv'])
    ]
    
    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket"
    }
    
    # Should not raise an exception, just log and exit
    combine_and_upload_detection_metrics_csv_to_s3(
        env_variables, 
        "/input/path", 
        "/output/path/combined.csv", 
        "s3/path/combined.csv"
    )
    
    # Verify os.walk was called
    mock_walk.assert_called_once_with("/input/path")

# Test cases for upload_csv_to_s3
@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.upload_utils.get_s3_client')
def test_upload_csv_to_s3_success(mock_get_s3_client):
    # Mock S3 client
    mock_s3_client = MagicMock()
    mock_get_s3_client.return_value = mock_s3_client
    
    # Create test DataFrame
    df = pd.DataFrame({'col1': [1, 2, 3], 'col2': ['a', 'b', 'c']})
    
    upload_csv_to_s3(
        df, 
        "test_key", 
        "test_secret", 
        "us-east-1", 
        "test-bucket", 
        "s3/path/file.csv"
    )
    
    # Verify S3 client was created with correct parameters
    mock_get_s3_client.assert_called_once_with("test_key", "test_secret", "us-east-1")
    
    # Verify put_object was called with correct parameters
    mock_s3_client.put_object.assert_called_once()
    call_args = mock_s3_client.put_object.call_args
    assert call_args[1]['Bucket'] == "test-bucket"
    assert call_args[1]['Key'] == "s3/path/file.csv"
    
    # Verify the CSV content is correct
    csv_content = call_args[1]['Body']
    expected_csv = df.to_csv(index=False)
    assert csv_content == expected_csv


@patch('spatialai_data_utils.datasets.cloud_utils.s3_utils.upload_utils.get_s3_client')
def test_upload_csv_to_s3_empty_dataframe(mock_get_s3_client):
    # Mock S3 client
    mock_s3_client = MagicMock()
    mock_get_s3_client.return_value = mock_s3_client
    
    # Create empty DataFrame
    df = pd.DataFrame()
    
    upload_csv_to_s3(
        df, 
        "test_key", 
        "test_secret", 
        "us-east-1", 
        "test-bucket", 
        "s3/path/empty.csv"
    )
    
    # Verify put_object was called
    mock_s3_client.put_object.assert_called_once()
    call_args = mock_s3_client.put_object.call_args
    assert call_args[1]['Bucket'] == "test-bucket"
    assert call_args[1]['Key'] == "s3/path/empty.csv"
    
    # Verify the CSV content is just headers (empty DataFrame)
    csv_content = call_args[1]['Body']
    expected_csv = df.to_csv(index=False)
    assert csv_content == expected_csv
