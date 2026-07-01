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

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from spatialai_data_utils.datasets.cloud_utils.validation_utils import (
    check_if_all_bin_files_are_present_in_storage,
    check_if_all_ground_truth_files_are_present_in_storage,
    check_if_bev_files_are_present_in_storage,
)


def _gcs_env(base_prefix_path="generated"):
    return {
        "STORAGE_PROVIDER": "gcs",
        "GCS_HMAC_ACCESS_KEY_ID": "gcs_key",
        "GCS_HMAC_SECRET_ACCESS_KEY": "gcs_secret",
        "GCS_BUCKET": "gcs-bucket",
        "GCS_ENDPOINT_URL": "https://storage.googleapis.com",
        "BASE_PREFIX_PATH": base_prefix_path,
        "SIMULATION_ID": "sim1",
    }


def _validation_args():
    return SimpleNamespace(
        simulation_seconds=10,
        bev_record_count_warning_threshold_ratio=0.8,
        bev_record_count_error_threshold_ratio=0.5,
        ground_truth_record_count_warning_threshold_ratio=0.8,
        ground_truth_record_count_error_threshold_ratio=0.5,
    )


@patch(
    "spatialai_data_utils.datasets.cloud_utils.validation_utils."
    "count_the_bev_records_in_storage",
    return_value=9,
)
def test_check_if_bev_files_are_present_in_storage_formats_gcs_prefix(mock_count):
    env_variables = _gcs_env(base_prefix_path="generated")
    args = _validation_args()

    result = check_if_bev_files_are_present_in_storage(args, env_variables, fps=2)

    mock_count.assert_called_once_with(
        env_variables,
        "generated/sim1/mdx-bev/",
    )
    assert result == {
        "actual_count": 9,
        "warning_threshold_record_count": 16,
        "error_threshold_record_count": 10,
    }


@patch(
    "spatialai_data_utils.datasets.cloud_utils.validation_utils."
    "count_the_files_in_storage",
    return_value=20,
)
def test_check_if_ground_truth_files_are_present_in_storage_formats_gcs_prefix(mock_count):
    env_variables = _gcs_env(base_prefix_path="generated")
    args = _validation_args()

    result = check_if_all_ground_truth_files_are_present_in_storage(
        args,
        env_variables,
        fps=2,
    )

    mock_count.assert_called_once_with(
        env_variables,
        "generated/sim1/ground-truth/mega_gt",
    )
    assert result == {
        "actual_count": 20,
        "warning_threshold_record_count": 16,
        "error_threshold_record_count": 10,
    }


@patch(
    "spatialai_data_utils.datasets.cloud_utils.validation_utils."
    "count_the_files_in_storage",
    return_value=20,
)
@patch(
    "spatialai_data_utils.datasets.cloud_utils.validation_utils."
    "validate_bin_sensors_present_in_storage",
    return_value={"status": True, "message": "ok"},
)
@patch(
    "spatialai_data_utils.datasets.cloud_utils.validation_utils."
    "get_ldrcolor_directories",
    return_value=["sim1/ground-truth/SensorA/LdrColor/"],
)
@patch("spatialai_data_utils.datasets.cloud_utils.validation_utils.get_storage_client")
def test_check_if_all_bin_files_are_present_in_storage_allows_blank_gcs_prefix(
    mock_get_storage_client,
    mock_get_ldrcolor_directories,
    mock_validate_bin_sensors_present,
    mock_count_files,
):
    storage_client = MagicMock()
    mock_get_storage_client.return_value = storage_client
    env_variables = _gcs_env(base_prefix_path="")
    args = _validation_args()

    result = check_if_all_bin_files_are_present_in_storage(
        args,
        env_variables,
        fps=2,
        bev_to_sensor_map={"bev1": ["SensorA"]},
        unique_bev_groups={"bev1"},
    )

    mock_get_ldrcolor_directories.assert_called_once_with(
        storage_client,
        "gcs-bucket",
        "sim1/ground-truth/",
    )
    mock_validate_bin_sensors_present.assert_called_once_with(
        ["sim1/ground-truth/SensorA/LdrColor/"],
        {"bev1"},
        {"bev1": ["SensorA"]},
    )
    mock_count_files.assert_called_once_with(
        env_variables,
        "sim1/ground-truth/SensorA/LdrColor/",
    )
    assert result == {
        "status": True,
        "message": "All bin files are present in object storage.",
    }
