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

from spatialai_data_utils.datasets.cloud_utils.upload_utils import (
    combine_and_upload_detection_metrics_csv_to_storage,
    upload_csv_to_storage
)


# Test cases for combine_and_upload_detection_metrics_csv_to_storage
@patch('spatialai_data_utils.datasets.cloud_utils.upload_utils.upload_csv_to_storage')
@patch('spatialai_data_utils.datasets.cloud_utils.upload_utils.pd.read_csv')
@patch('spatialai_data_utils.datasets.cloud_utils.upload_utils.os.walk')
@patch('pandas.DataFrame.to_csv')
def test_combine_and_upload_detection_metrics_csv_to_storage_success(mock_to_csv, mock_walk, mock_read_csv, mock_upload_csv):
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
    
    combine_and_upload_detection_metrics_csv_to_storage(
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
    
    # Verify that upload_csv_to_storage was called with the combined DataFrame
    mock_upload_csv.assert_called_once()
    call_args = mock_upload_csv.call_args
    assert call_args[0][0].equals(pd.concat([df1, df2], ignore_index=True))  # Combined DataFrame
    assert call_args[0][1] == env_variables
    assert call_args[0][2] == "s3/path/combined.csv"


@patch('spatialai_data_utils.datasets.cloud_utils.upload_utils.os.walk')
def test_combine_and_upload_detection_metrics_csv_to_storage_no_files(mock_walk):
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
    combine_and_upload_detection_metrics_csv_to_storage(
        env_variables, 
        "/input/path", 
        "/output/path/combined.csv", 
        "s3/path/combined.csv"
    )
    
    # Verify os.walk was called
    mock_walk.assert_called_once_with("/input/path")

# Test cases for upload_csv_to_storage
@patch('spatialai_data_utils.datasets.cloud_utils.upload_utils.get_storage_client')
def test_upload_csv_to_storage_success(mock_get_storage_client):
    # Mock S3 client
    mock_s3_client = MagicMock()
    mock_get_storage_client.return_value = mock_s3_client
    
    # Create test DataFrame
    df = pd.DataFrame({'col1': [1, 2, 3], 'col2': ['a', 'b', 'c']})
    
    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket",
    }

    upload_csv_to_storage(df, env_variables, "s3/path/file.csv")
    
    # Verify storage client was created with environment configuration
    mock_get_storage_client.assert_called_once_with(env_variables)
    
    # Verify put_object was called with correct parameters
    mock_s3_client.put_object.assert_called_once()
    call_args = mock_s3_client.put_object.call_args
    assert call_args[1]['Bucket'] == "test-bucket"
    assert call_args[1]['Key'] == "s3/path/file.csv"
    
    # Verify the CSV content is correct
    csv_content = call_args[1]['Body']
    expected_csv = df.to_csv(index=False)
    assert csv_content == expected_csv


@patch('spatialai_data_utils.datasets.cloud_utils.upload_utils.get_storage_client')
def test_upload_csv_to_storage_empty_dataframe(mock_get_storage_client):
    # Mock S3 client
    mock_s3_client = MagicMock()
    mock_get_storage_client.return_value = mock_s3_client
    
    # Create empty DataFrame
    df = pd.DataFrame()
    
    env_variables = {
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "AWS_REGION": "us-east-1",
        "AWS_BUCKET": "test-bucket",
    }

    upload_csv_to_storage(df, env_variables, "s3/path/empty.csv")
    
    # Verify put_object was called
    mock_s3_client.put_object.assert_called_once()
    call_args = mock_s3_client.put_object.call_args
    assert call_args[1]['Bucket'] == "test-bucket"
    assert call_args[1]['Key'] == "s3/path/empty.csv"
    
    # Verify the CSV content is just headers (empty DataFrame)
    csv_content = call_args[1]['Body']
    expected_csv = df.to_csv(index=False)
    assert csv_content == expected_csv


@patch('spatialai_data_utils.datasets.cloud_utils.upload_utils.get_storage_client')
def test_upload_csv_to_storage_uses_gcs_bucket(mock_get_storage_client):
    mock_storage_client = MagicMock()
    mock_get_storage_client.return_value = mock_storage_client

    df = pd.DataFrame({'col1': [1], 'col2': ['gcs']})

    env_variables = {
        "STORAGE_PROVIDER": "gcs",
        "GCS_HMAC_ACCESS_KEY_ID": "gcs_key",
        "GCS_HMAC_SECRET_ACCESS_KEY": "gcs_secret",
        "GCS_BUCKET": "gcs-bucket",
        "GCS_ENDPOINT_URL": "https://storage.googleapis.com",
    }

    upload_csv_to_storage(df, env_variables, "gcs/path/file.csv")

    mock_get_storage_client.assert_called_once_with(env_variables)
    mock_storage_client.put_object.assert_called_once()
    call_args = mock_storage_client.put_object.call_args
    assert call_args.kwargs["Bucket"] == "gcs-bucket"
    assert call_args.kwargs["Key"] == "gcs/path/file.csv"
    assert call_args.kwargs["Body"] == df.to_csv(index=False)
