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

import concurrent.futures
import logging

from spatialai_data_utils.datasets.cloud_utils.s3_utils.common import (
    count_the_files_in_s3,
    get_ldrcolor_directories,
    get_s3_client,
    list_files,
)


def _count_lines_in_s3_object(s3_client, bucket, key):
    """Optimized line counting using streaming and chunked reading."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        line_count = 0
        chunk_size = 8192

        for chunk in response["Body"].iter_chunks(chunk_size=chunk_size):
            line_count += chunk.count(b"\n")

        return line_count
    except Exception as exc:
        logging.error(f"Error counting lines in {key}: {exc}")
        return 0


def count_lines_in_s3_object(env_variables, key):
    """Count lines in a single S3 object using environment configuration."""
    s3_client = get_s3_client(
        env_variables["AWS_ACCESS_KEY_ID"],
        env_variables["AWS_SECRET_ACCESS_KEY"],
        env_variables["AWS_REGION"],
    )
    return _count_lines_in_s3_object(s3_client, env_variables["AWS_BUCKET"], key)


def count_the_bev_records_in_s3(env_variables, aws_s3_base_prefix_path, max_workers=8):
    """Count BEV records in S3 with parallel processing and progress tracking."""
    logging.info("Counting lines in each file in mdx-bev directory")

    file_keys = [
        key for key in list_files(env_variables, aws_s3_base_prefix_path)
        if not key.endswith("/")
    ]

    if not file_keys:
        logging.info("No files found in the specified S3 prefix")
        return 0

    s3_client = get_s3_client(
        env_variables["AWS_ACCESS_KEY_ID"],
        env_variables["AWS_SECRET_ACCESS_KEY"],
        env_variables["AWS_REGION"],
        max_pool_connections=max_workers,
    )
    bucket = env_variables["AWS_BUCKET"]

    total_lines = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_key = {
            executor.submit(_count_lines_in_s3_object, s3_client, bucket, key): key
            for key in file_keys
        }

        for future in concurrent.futures.as_completed(future_to_key):
            key = future_to_key[future]
            try:
                total_lines += future.result()
            except Exception as exc:
                logging.error(f"Error processing {key}: {exc}")

    logging.info(f"Total lines in directory: {total_lines}")
    return total_lines


def check_if_bev_files_are_present_in_s3(args, env_variables, fps):
    """
    Count BEV prediction records in S3 and compute validation thresholds.

    :param args: Parsed validation arguments containing simulation duration and
        BEV record-count threshold ratios.
    :type args: argparse.Namespace
    :param env_variables: Environment/configuration values containing S3 bucket,
        base prefix, and simulation ID.
    :type env_variables: dict
    :param fps: Frames per second used to compute expected record thresholds.
    :type fps: int | float
    :return: Count result with ``actual_count``, warning threshold, and error
        threshold.
    :rtype: dict
    """
    aws_s3_base_prefix_path = (
        f"{env_variables['AWS_S3_BASE_PREFIX_PATH']}{env_variables['SIMULATION_ID']}/mdx-bev/"
    )
    logging.info(f"Counting the records in bev files in s3 path: {aws_s3_base_prefix_path}")

    actual_count = count_the_bev_records_in_s3(env_variables, aws_s3_base_prefix_path)
    logging.info(f"Number of files in s3: {actual_count}")

    warning_threshold_record_count = int(
        args.simulation_seconds * fps * args.bev_record_count_warning_threshold_ratio
    )
    error_threshold_record_count = int(
        args.simulation_seconds * fps * args.bev_record_count_error_threshold_ratio
    )

    return {
        "actual_count": actual_count,
        "warning_threshold_record_count": warning_threshold_record_count,
        "error_threshold_record_count": error_threshold_record_count,
    }


def check_if_all_ground_truth_files_are_present_in_s3(args, env_variables, fps):
    """
    Count ground-truth files in S3 and compute validation thresholds.

    :param args: Parsed validation arguments containing simulation duration and
        ground-truth record-count threshold ratios.
    :type args: argparse.Namespace
    :param env_variables: Environment/configuration values containing S3 bucket,
        base prefix, and simulation ID.
    :type env_variables: dict
    :param fps: Frames per second used to compute expected file thresholds.
    :type fps: int | float
    :return: Count result with ``actual_count``, warning threshold, and error
        threshold.
    :rtype: dict
    """
    aws_s3_base_prefix_path = (
        f"{env_variables['AWS_S3_BASE_PREFIX_PATH']}"
        f"{env_variables['SIMULATION_ID']}/ground-truth/mega_gt"
    )
    logging.info(f"Counting the files in s3 with prefix: {aws_s3_base_prefix_path}")

    actual_count = count_the_files_in_s3(env_variables, aws_s3_base_prefix_path)
    logging.info(f"Number of files in s3: {actual_count}")

    warning_threshold_record_count = int(
        args.simulation_seconds
        * fps
        * args.ground_truth_record_count_warning_threshold_ratio
    )
    error_threshold_record_count = int(
        args.simulation_seconds
        * fps
        * args.ground_truth_record_count_error_threshold_ratio
    )

    return {
        "actual_count": actual_count,
        "warning_threshold_record_count": warning_threshold_record_count,
        "error_threshold_record_count": error_threshold_record_count,
    }


def extract_sensor_name_from_ldrcolor_path(path):
    """
    Extract the calibration sensor name from an S3 ``LdrColor`` directory path.

    :param path: S3 directory path containing an ``LdrColor`` segment.
    :type path: str
    :return: Sensor name with render-specific suffixes removed.
    :rtype: str
    """
    ldr_sensor_name = path.split("/")[-3]
    sensor_name = ldr_sensor_name.replace("_Render_Metro", "").replace("Rp", "")
    return sensor_name


def validate_bin_sensors_present_in_s3(
    ldrcolor_directories,
    unique_bev_groups,
    bev_to_sensor_map,
):
    """
    Validate that S3 ``LdrColor`` directories contain the expected BEV sensors.

    :param ldrcolor_directories: S3 directory prefixes containing ``LdrColor``
        files.
    :type ldrcolor_directories: list[str]
    :param unique_bev_groups: BEV group names expected from ground-truth
        validation.
    :type unique_bev_groups: set[str]
    :param bev_to_sensor_map: Mapping from BEV group name to expected sensor
        names.
    :type bev_to_sensor_map: dict
    :return: Validation result containing ``status`` and ``message``.
    :rtype: dict
    """
    unique_ldr_color_sensor_names = set()
    status = True
    message = ""

    for path in ldrcolor_directories:
        ldr_sensor_name = extract_sensor_name_from_ldrcolor_path(path)
        unique_ldr_color_sensor_names.add(ldr_sensor_name)
    logging.info(f"Unique LDR color sensor names: {unique_ldr_color_sensor_names}")

    for bev_group in unique_bev_groups:
        sensors_missing_in_the_bev_group = set(bev_to_sensor_map[bev_group]).difference(
            unique_ldr_color_sensor_names
        ).union(unique_ldr_color_sensor_names.difference(set(bev_to_sensor_map[bev_group])))

        if sensors_missing_in_the_bev_group:
            status = False
            message += f"{bev_group}: {sensors_missing_in_the_bev_group}"

    if status:
        return {
            "status": True,
            "message": "All sensors are present in s3. Continuing to next step...",
        }

    return {
        "status": False,
        "message": f"Sensors missing in the BEV group: \n {message}",
    }


def check_if_all_bin_files_are_present_in_s3(
    args,
    env_variables,
    fps,
    bev_to_sensor_map,
    unique_bev_groups,
):
    """
    Validate sensor bridge BIN file presence and counts in S3.

    Finds ``LdrColor`` directories under the ground-truth prefix, verifies that
    the discovered sensors match the expected BEV group mapping, and checks each
    sensor directory against configured warning/error thresholds.

    :param args: Parsed validation arguments containing simulation duration and
        ground-truth record-count threshold ratios.
    :type args: argparse.Namespace
    :param env_variables: Environment/configuration values containing AWS
        credentials, region, bucket, base prefix, and simulation ID.
    :type env_variables: dict
    :param fps: Frames per second used to compute expected BIN file counts.
    :type fps: int | float
    :param bev_to_sensor_map: Mapping from BEV group name to expected sensors.
    :type bev_to_sensor_map: dict
    :param unique_bev_groups: BEV group names to validate.
    :type unique_bev_groups: set[str]
    :return: Validation result containing ``status`` and ``message``.
    :rtype: dict
    """
    message = ""

    s3_client = get_s3_client(
        env_variables["AWS_ACCESS_KEY_ID"],
        env_variables["AWS_SECRET_ACCESS_KEY"],
        env_variables["AWS_REGION"],
    )

    ground_truth_base_path = (
        f"{env_variables['AWS_S3_BASE_PREFIX_PATH']}"
        f"{env_variables['SIMULATION_ID']}/ground-truth/"
    )
    logging.info(f"Looking for sensor bridge bin directories in: {ground_truth_base_path}")

    ldrcolor_directories = get_ldrcolor_directories(
        s3_client,
        env_variables["AWS_BUCKET"],
        ground_truth_base_path,
    )
    logging.info(f"Found sensor bridge bin directories: {ldrcolor_directories}")

    sensors_missing = validate_bin_sensors_present_in_s3(
        ldrcolor_directories,
        unique_bev_groups,
        bev_to_sensor_map,
    )
    if sensors_missing["status"]:
        logging.info(f"{sensors_missing['message']}")
    else:
        logging.error(f"!!{sensors_missing['message']} Exiting...")
        exit(1)

    warning_threshold_record_count = int(
        args.simulation_seconds
        * fps
        * args.ground_truth_record_count_warning_threshold_ratio
    )
    error_threshold_record_count = int(
        args.simulation_seconds
        * fps
        * args.ground_truth_record_count_error_threshold_ratio
    )

    for aws_s3_base_prefix_path in ldrcolor_directories:
        actual_count = count_the_files_in_s3(env_variables, aws_s3_base_prefix_path)
        logging.info(
            f"Number of bin files in s3 for {aws_s3_base_prefix_path}: {actual_count}"
        )

        if actual_count < error_threshold_record_count:
            return {
                "status": False,
                "message": (
                    f"Number of bin files in s3 for {aws_s3_base_prefix_path} is "
                    f"{actual_count} which is less than expected error threshold count "
                    f"{error_threshold_record_count}. Total number of bin files expected "
                    f"in ground truth is {fps * args.simulation_seconds}."
                ),
            }
        elif actual_count < warning_threshold_record_count:
            message += (
                f"Number of bin files in s3 for {aws_s3_base_prefix_path} is "
                f"{actual_count} which is less than expected warning threshold count "
                f"{warning_threshold_record_count}.\n"
            )

    if message:
        message += (
            f"Total number of bin files expected for each sensor in ground truth is "
            f"{fps * args.simulation_seconds}."
        )
    else:
        message = "All bin files are present in s3."

    return {
        "status": True,
        "message": message,
    }
