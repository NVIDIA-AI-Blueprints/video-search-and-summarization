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

"""CLI entry point for MTMC validation and evaluation."""

import argparse
import logging
import os

from dotenv import load_dotenv

from spatialai_data_utils.configs.eval.detection import (
    DET_CONFIG_CENTER_DISTANCE,
    DET_CONFIG_IOU3D,
)
from spatialai_data_utils.datasets.cloud_utils.s3_utils.download_utils import (
    download_and_merge_data_from_s3,
    get_calibration_from_s3,
)
from spatialai_data_utils.datasets.cloud_utils.s3_utils.upload_utils import (
    combine_and_upload_detection_metrics_csv_to_s3,
)
from spatialai_data_utils.datasets.cloud_utils.s3_utils.validation_utils import (
    check_if_all_bin_files_are_present_in_s3,
    check_if_all_ground_truth_files_are_present_in_s3,
)
from spatialai_data_utils.eval.detection.evaluate import (
    evaluate_detection_per_BEV_sensor,
)
from spatialai_data_utils.loaders.calibration import fetch_fps_from_calibration
from spatialai_data_utils.utils.filesystem_utils import all_files_valid, validate_file_path
from spatialai_data_utils.validation.bev_utils import bev_data_validation
from spatialai_data_utils.validation.gt_utils import (
    get_unique_types_from_ground_truth,
    ground_truth_data_validation,
)

# Mapping from the user-facing ``--eval_options`` CLI choice to the
# detection-config preset consumed by ``evaluate_detection_per_BEV_sensor``.
# ``location`` -> centre-distance matching (the historical MTMC default,
# preserved when the flag is omitted); ``bbox`` -> 3D-IoU bounding-box
# matching. Keys must stay in lock-step with ``parse_args``'s
# ``--eval_options`` ``choices``.
_EVAL_OPTION_TO_CONFIG = {
    "location": DET_CONFIG_CENTER_DISTANCE,
    "bbox": DET_CONFIG_IOU3D,
}

logger = logging.getLogger(__name__)

DEFAULT_ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def run_evaluation(args, env_variables, output_root_dir, calibration_file_path, fps):
    """
    Execute the evaluation pipeline using the provided arguments, environment variables, and output directory.

    :param args: Parsed command-line arguments or configuration parameters.
    :type args: Namespace or dict
    :param dict env_variables: Dictionary containing environment variables.
    :param str output_root_dir: Directory where outputs will be saved.
    :return: None
    :rtype: None
    """

    # Download data from S3
    download_and_merge_data_from_s3(args, env_variables, output_root_dir)
    print("--------------------------------------------------------------")

    # Get all file names
    file_path = os.path.join(output_root_dir, env_variables["SIMULATION_ID"])
    ground_truth_file_path = validate_file_path(os.path.join(file_path, "ground-truth", "ground-truth-sorted.json"))
    mdx_bev_file_path = validate_file_path(os.path.join(file_path, "mdx-bev", "mdx-bev-sorted.json"))

    # Compute Sparse4D results
    if all_files_valid(ground_truth_file_path, calibration_file_path, mdx_bev_file_path):

        ground_truth_validation_output = ground_truth_data_validation(args, ground_truth_file_path, calibration_file_path, fps)
        if ground_truth_validation_output["status"]:
            if ground_truth_validation_output["actual_count"] < ground_truth_validation_output["error_threshold_record_count"]:
                logger.error(f"!!Number of ground truth files in s3 is {ground_truth_validation_output['actual_count']} which is less than expected error threshold count {ground_truth_validation_output['error_threshold_record_count']}. Total number of records expected in ground truth is {fps * args.simulation_seconds}. Exiting...")
                exit(1)
            elif ground_truth_validation_output["actual_count"] < ground_truth_validation_output["warning_threshold_record_count"]:
                logger.warning(
                    f"Number of ground truth files in s3 is {ground_truth_validation_output['actual_count']} which is less than expected warning threshold count {ground_truth_validation_output['warning_threshold_record_count']}. Total number of records expected in ground truth is {fps * args.simulation_seconds}. Continuing..."
                )
            else:
                logger.info(f"Number of ground truth files in s3 is {ground_truth_validation_output['actual_count']}, satisfying the expected count. Continuing to next step...")
            print("--------------------------------------------------------------")
        else:
            logger.error(f"!!{ground_truth_validation_output['message']} Exiting...")
            exit(1)

        bev_to_sensor_map = ground_truth_validation_output['bev_to_sensor_map']
        unique_bev_groups = ground_truth_validation_output['unique_bev_groups']

        unique_types = get_unique_types_from_ground_truth(ground_truth_file_path)
        if len(unique_types) == 0:
            logger.error("!!No types found in ground truth. Exiting...")
            exit(1)
        else:
            logger.info(f"Unique types present in ground truth are: {unique_types}")
        print("--------------------------------------------------------------")

        if not args.skip_s3_bin_files_check:
            output = check_if_all_bin_files_are_present_in_s3(args, env_variables, fps, bev_to_sensor_map, unique_bev_groups)
            if output["status"]:
                logger.info(f"{output['message']}")
            else:
                logger.error(f"!!{output['message']} Exiting...")
                exit(1)
            print("--------------------------------------------------------------")

        bev_validation_output = bev_data_validation(args, mdx_bev_file_path, fps, ground_truth_file_path)
        if not bev_validation_output["status"]:
            logger.error(f"{bev_validation_output['message']}")
            exit(1)
        else:
            logger.info(f"{bev_validation_output['message']}")
        print("--------------------------------------------------------------")
        
        output_data_path = os.path.join(file_path, "evaluation_results", "sparse4d")
        # Pick the detection-config preset from the CLI flag. ``location``
        # (the historical default for this tool) -> centre-distance match;
        # ``bbox`` -> 3D-IoU match. Library default is 3D-IoU; we
        # explicitly pass the resolved preset so the choice is auditable.
        selected_config = _EVAL_OPTION_TO_CONFIG[args.eval_options]
        evaluate_detection_per_BEV_sensor(
            ground_truth_file=ground_truth_file_path,
            prediction_file=mdx_bev_file_path,
            calibration_file=calibration_file_path,
            output_root_dir=output_data_path,
            confidence_threshold=args.confidence_threshold,
            num_frames_to_eval=args.num_frames_to_eval,
            ground_truth_frame_offset_secs=args.ground_truth_frame_offset_secs,
            config=selected_config,
        )

        print("--------------------------------------------------------------")

        input_csvs_path = file_path + '/evaluation_results/sparse4d/detection_results/'
        local_output_csv_dump_path = file_path + '/evaluation_results/detection_metrics_summary.csv'
        s3_dump_path = f'{env_variables["AWS_S3_BASE_PREFIX_PATH"]}{env_variables["SIMULATION_ID"]}/evaluation_results/detection_metrics_summary.csv'

        combine_and_upload_detection_metrics_csv_to_s3(env_variables, input_csvs_path, local_output_csv_dump_path, s3_dump_path)
            
    else:
        logger.error("!!Skipping Sparse4D evaluation. One or more required files have file size as 0. Exiting...")
        exit(1)

    print("--------------------------------------------------------------")


def load_env_variables(env_file_path=DEFAULT_ENV_FILE_PATH):
    """
    Load environment variables from the specified .env file and return them.

    :param str env_file_path: Path to the .env file.
    :return: Dictionary of loaded environment variables.
    :rtype: dict
    """
    if not os.path.exists(env_file_path):
        raise FileNotFoundError(f".env file not found at {env_file_path}")
    
    load_dotenv(dotenv_path=env_file_path)

    env_vars = {
        "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "AWS_REGION": os.getenv("AWS_REGION"),
        "AWS_BUCKET": os.getenv("AWS_BUCKET"),
        "AWS_S3_BASE_PREFIX_PATH": os.getenv("AWS_S3_BASE_PREFIX_PATH"),
        "SIMULATION_ID": os.getenv("SIMULATION_ID"),
    }
    
    # Ensure required env variables are present
    required_env_vars = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "AWS_BUCKET", "AWS_S3_BASE_PREFIX_PATH", "SIMULATION_ID"]
    for var in required_env_vars:
        if not env_vars[var]:
            raise EnvironmentError(f"Required environment variable {var} is not set.")

    return env_vars


def main(args, env_variables):

    # Setup output directory to store results
    output_root_dir = "results/"

    if not os.path.exists(os.path.join(output_root_dir, env_variables["SIMULATION_ID"])):
        os.makedirs(os.path.join(output_root_dir, env_variables["SIMULATION_ID"]), exist_ok=True)

    # Download calibration file from s3
    get_calibration_from_s3(env_variables["AWS_ACCESS_KEY_ID"], env_variables["AWS_SECRET_ACCESS_KEY"], env_variables["AWS_REGION"], args.calibration_url, env_variables["SIMULATION_ID"], output_root_dir)
    print("--------------------------------------------------------------")

    calibration_file_path = validate_file_path(os.path.join(output_root_dir, env_variables["SIMULATION_ID"],  "calibration.json"))
    fps = fetch_fps_from_calibration(calibration_file_path)

    # Check if mdx-bev only validation is requested
    if args.only_mdx_bev_validation:
        logger.info("mdx-bev only validation mode enabled. Skipping ground truth validation and evaluation.")
        
        # Download data from S3 for mdx-bev validation
        download_and_merge_data_from_s3(args, env_variables, output_root_dir)
        print("--------------------------------------------------------------")

        # Get mdx-bev file path
        file_path = os.path.join(output_root_dir, env_variables["SIMULATION_ID"])
        mdx_bev_file_path = validate_file_path(os.path.join(file_path, "mdx-bev", "mdx-bev-sorted.json"))

        # Validate mdx-bev file exists and has content
        if not os.path.exists(mdx_bev_file_path) or os.path.getsize(mdx_bev_file_path) == 0:
            logger.error("!!mdx-bev file not found or empty. Exiting...")
            exit(1)

        # Run mdx-bev data validation
        bev_validation_output = bev_data_validation(args, mdx_bev_file_path, fps)
        if not bev_validation_output["status"]:
            logger.error(f"{bev_validation_output['message']}")
            exit(1)
        else:
            logger.info(f"{bev_validation_output['message']}")
        
        logger.info("mdx-bev only validation completed successfully!")
        print("--------------------------------------------------------------")
    else:
        # Original full validation flow
        output = check_if_all_ground_truth_files_are_present_in_s3(args, env_variables, fps)
        if output["actual_count"] < output["error_threshold_record_count"]:
            logger.error(f"!!Number of ground truth files in s3 is {output['actual_count']} which is less than expected error threshold count {output['error_threshold_record_count']}. Total number of records expected in ground truth is {fps * args.simulation_seconds}. Exiting...")
            exit(1)
        elif output["actual_count"] < output["warning_threshold_record_count"]:
            logger.warning(
                f"Number of ground truth files in s3 is {output['actual_count']} which is less than expected warning threshold count {output['warning_threshold_record_count']}. Total number of records expected in ground truth is {fps * args.simulation_seconds}. Continuing..."
            )
        else:
            logger.info(f"Number of ground truth files in s3 is {output['actual_count']}, satisfying the expected count. Continuing to next step...")
        print("--------------------------------------------------------------")
        
        # Run evaluation
        run_evaluation(args, env_variables, output_root_dir, calibration_file_path, fps)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MTMC Validation - evaluate multi-camera tracking results.",
    )
    parser.add_argument("--calibration_url", help="Input calibration url file", required=True)
    parser.add_argument("--confidence_threshold", type=float, default=0.0, help="Confidence threshold. Values less than threshold will be filtered out.")
    parser.add_argument("--num_frames_to_eval", type=int, default=200000)
    parser.add_argument("--ground_truth_frame_offset_secs", type=float, default=0.0)
    parser.add_argument(
        "--eval_options",
        choices=["location", "bbox"],
        default="location",
        help=(
            "Detection-evaluation matching function. 'location' uses "
            "centre-distance matching (DET_CONFIG_CENTER_DISTANCE — "
            "the historical MTMC default); 'bbox' uses 3D-IoU "
            "bounding-box matching (DET_CONFIG_IOU3D). "
            "Default: 'location'."
        ),
    )
    parser.add_argument("--simulation_seconds", type=int, default=120)
    parser.add_argument("--ground_truth_record_count_warning_threshold_ratio", type=float, default=0.99)
    parser.add_argument("--ground_truth_record_count_error_threshold_ratio", type=float, default=0.99)
    parser.add_argument("--bev_record_count_warning_threshold_ratio", type=float, default=0.85)
    parser.add_argument("--bev_record_count_error_threshold_ratio", type=float, default=0.5)
    parser.add_argument("--min_tolerance_ms_for_bev_record", type=int, default=33)
    parser.add_argument("--max_tolerance_ms_for_bev_record", type=int, default=34)
    parser.add_argument("--bev_intra_record_timestamp_tolerance_ms", type=int, default=34)
    parser.add_argument("--skip_s3_bin_files_check", action="store_true", help="Skip the check for bin files in S3")
    parser.add_argument("--only_mdx_bev_validation", action="store_true", help="Validate only mdx-bev data, skip ground truth validation and evaluation")
    parser.add_argument("--bev_delay", type=int, default=33, help="Warn when the first BEV record is more than this many milliseconds after the first ground truth record")
    return parser.parse_args()

if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s - %(message)s", datefmt="%y/%m/%d %H:%M:%S", level=logging.INFO)
    args = parse_args()
    env_variables = load_env_variables()
    main(args, env_variables)
