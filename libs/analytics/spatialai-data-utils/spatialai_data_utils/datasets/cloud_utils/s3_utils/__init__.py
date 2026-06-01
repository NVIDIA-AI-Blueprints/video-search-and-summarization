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

"""
S3 utility functions shared by dataset workflows.
"""

from .common import (
    convert_https_to_s3_url,
    count_the_files_in_s3,
    format_aws_s3_base_prefix_path,
    get_ldrcolor_directories,
    get_s3_client,
    list_files,
)
from .download_utils import (
    download_and_merge_data_from_s3,
    download_single_file_from_s3_streaming,
    fetch_s3_keys_for_dataset,
    get_calibration_from_s3,
    sort_dataset,
    sort_file_by_timestamp,
    sort_files_in_folders,
)
from .upload_utils import (
    combine_and_upload_detection_metrics_csv_to_s3,
    upload_csv_to_s3,
)
from .validation_utils import (
    check_if_all_bin_files_are_present_in_s3,
    check_if_all_ground_truth_files_are_present_in_s3,
    check_if_bev_files_are_present_in_s3,
    count_lines_in_s3_object,
    count_the_bev_records_in_s3,
    extract_sensor_name_from_ldrcolor_path,
    validate_bin_sensors_present_in_s3,
)

__all__ = [
    "check_if_all_bin_files_are_present_in_s3",
    "check_if_all_ground_truth_files_are_present_in_s3",
    "check_if_bev_files_are_present_in_s3",
    "combine_and_upload_detection_metrics_csv_to_s3",
    "convert_https_to_s3_url",
    "count_lines_in_s3_object",
    "count_the_bev_records_in_s3",
    "count_the_files_in_s3",
    "download_and_merge_data_from_s3",
    "download_single_file_from_s3_streaming",
    "extract_sensor_name_from_ldrcolor_path",
    "fetch_s3_keys_for_dataset",
    "format_aws_s3_base_prefix_path",
    "get_calibration_from_s3",
    "get_ldrcolor_directories",
    "get_s3_client",
    "list_files",
    "sort_dataset",
    "sort_file_by_timestamp",
    "sort_files_in_folders",
    "upload_csv_to_s3",
    "validate_bin_sensors_present_in_s3",
]
