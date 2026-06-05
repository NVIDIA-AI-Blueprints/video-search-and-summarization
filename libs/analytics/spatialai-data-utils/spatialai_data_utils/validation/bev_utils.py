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

import logging
import json

from spatialai_data_utils.utils.datetime_utils import parse_timestamp, timestamp_to_ms

logger = logging.getLogger(__name__)


def bev_data_validation(args, mdx_bev_file, fps, ground_truth_file=None):
    """
    Validate BEV JSONL records for timestamp synchronization and record counts.

    Checks that the BEV file is non-empty, validates intra-record sensor
    timestamp synchronization, verifies inter-record timestamp spacing, and
    compares the BEV record count against configured warning/error thresholds
    when a ground-truth file is provided.

    :param args: Parsed validation arguments containing BEV delay, timestamp
        tolerance, simulation length, and record-count threshold settings.
    :type args: argparse.Namespace
    :param mdx_bev_file: Path to the BEV prediction JSONL file.
    :type mdx_bev_file: str
    :param fps: Frames per second for the simulation. This parameter is kept
        for the validation interface.
    :type fps: int | float
    :param ground_truth_file: Optional path to the ground-truth JSONL file used
        to compare BEV start delay and expected record count.
    :type ground_truth_file: str | None
    :return: A validation result containing ``status`` and ``message``.
    :rtype: dict
    """
    with open(mdx_bev_file, "r") as bev_file:
        bev_content = bev_file.read().splitlines()
    
    # Handle empty file
    if not bev_content:
        return {"status": False, "message": "Empty BEV file provided"}
    else:
        bev_first_record = json.loads(bev_content[0])
        bev_first_record_timestamp = timestamp_to_ms(parse_timestamp(bev_first_record['timestamp']))

    if ground_truth_file:
        with open(ground_truth_file, "r") as ground_truth_file:
            ground_truth_content = ground_truth_file.read().splitlines()
            if ground_truth_content:
                ground_truth_record = json.loads(ground_truth_content[0])
                ground_truth_record_timestamp = timestamp_to_ms(parse_timestamp(ground_truth_record['timestamp']))
                ground_truth_last_record = json.loads(ground_truth_content[-1])
                ground_truth_last_record_timestamp = timestamp_to_ms(parse_timestamp(ground_truth_last_record['timestamp']))
                actual_bev_delay = bev_first_record_timestamp - ground_truth_record_timestamp
                if actual_bev_delay > args.bev_delay:
                    actual_bev_delay_text = f"{actual_bev_delay:g}"
                    logger.warning(
                        "BEV first record timestamp is %s ms after the first ground truth "
                        "record, greater than bev_delay=%s ms.",
                        actual_bev_delay_text,
                        args.bev_delay,
                    )
            else:
                ground_truth_record_timestamp = None
                ground_truth_last_record_timestamp = None
    else:
        ground_truth_record_timestamp = None
        ground_truth_last_record_timestamp = None

    bev_record_count_objects_not_found_in_bev_file = 0
    bev_unsynchronized_timestamp_row_count = 0
    bev_inter_record_timestamp_out_of_tolerance_row_count = 0
    previous_record = None
    previous_record_timestamp = None

    for record in bev_content:
        record = json.loads(record)
        record_info = record.get('info') or {}
        if 'objects' in record.keys() and len(record['objects']) == 0:
            bev_record_count_objects_not_found_in_bev_file += 1
        else:
            # If objects are present, info section should also be present
            timestamps = [timestamp_to_ms(parse_timestamp(ts)) for sensor_name, ts in record_info.items()]
            if timestamps:
                timestamp_difference_ms = max(timestamps) - min(timestamps)
                if timestamp_difference_ms > args.bev_intra_record_timestamp_tolerance_ms:
                    bev_unsynchronized_timestamp_row_count += 1
                    faulty_record = {"id": record['id'], "sensorId": record['sensorId'], "timestamp": record['timestamp'], "info": record_info}
                    logger.warning(
                        f"BEV record has unsynchronized sensor timestamps. "
                        f"Max difference observed: {timestamp_difference_ms} ms; "
                        f"allowed maximum difference: {args.bev_intra_record_timestamp_tolerance_ms} ms; "
                        f"Record: {faulty_record}"
                    )

        record_timestamp_in_ms = timestamp_to_ms(parse_timestamp(record['timestamp']))

        current_record = {"id": record['id'], "sensorId": record['sensorId'], "timestamp": record['timestamp'], "info": record_info}
        if previous_record_timestamp:
            inter_record_timestamp_diff_ms = record_timestamp_in_ms - previous_record_timestamp
            if not (
                args.min_tolerance_ms_for_bev_record
                <= inter_record_timestamp_diff_ms
                <= args.max_tolerance_ms_for_bev_record
            ):
                bev_inter_record_timestamp_out_of_tolerance_row_count += 1
                logger.warning(
                    "BEV record timestamp spacing is not within tolerance. "
                    "Observed difference: %s ms; expected range: [%s, %s] ms. "
                    "Previous record: %s, Current record: %s",
                    inter_record_timestamp_diff_ms,
                    args.min_tolerance_ms_for_bev_record,
                    args.max_tolerance_ms_for_bev_record,
                    previous_record,
                    current_record,
                )
        previous_record = current_record
        previous_record_timestamp = record_timestamp_in_ms

    if bev_first_record_timestamp and ground_truth_last_record_timestamp:
        time_difference = (ground_truth_last_record_timestamp - bev_first_record_timestamp)
        records_expected = (time_difference // 100)*3 + (time_difference % 100)//33
        bev_records_count_actual = len(bev_content)
    
        if bev_records_count_actual < records_expected * args.bev_record_count_error_threshold_ratio:
            return {"status": False, "message": f"!!Number of BEV records is {bev_records_count_actual} which is less than expected error threshold count {records_expected * args.bev_record_count_error_threshold_ratio}. Total number of records expected in BEV is {records_expected}. Exiting..."}
        elif bev_records_count_actual < records_expected * args.bev_record_count_warning_threshold_ratio:
            logger.warning(f"!!Number of BEV records is {bev_records_count_actual} which is less than expected warning threshold count {records_expected * args.bev_record_count_warning_threshold_ratio}. Total number of records expected in BEV is {records_expected}. Continuing...")
        else:
            logger.info(f"Number of BEV records is {bev_records_count_actual}, satisfying the expected count. Continuing...")
    if bev_unsynchronized_timestamp_row_count > 0:
        summary_message = (
            f"Bev records generated by combining frames from different sensors are unsynchronized. There are {bev_unsynchronized_timestamp_row_count} out of {len(bev_content)} records have unsynchronized timestamps. Accuracy may be compromised."
        )
    else:
        summary_message = "All bev records are within tolerance and have synchronized timestamps."
    if bev_inter_record_timestamp_out_of_tolerance_row_count > 0:
        summary_message = (
            f"{summary_message} \nThere are "
            f"{bev_inter_record_timestamp_out_of_tolerance_row_count} out of {len(bev_content)} "
            f"records with inter-record timestamp spacing outside "
            f"[{args.min_tolerance_ms_for_bev_record}, {args.max_tolerance_ms_for_bev_record}] ms. "
            f"Continuing to next step..."
        )
    if bev_record_count_objects_not_found_in_bev_file > 0:
        summary_message = (
            f"{summary_message} Total number of records with no objects: "
            f"{bev_record_count_objects_not_found_in_bev_file} out of total {len(bev_content)} records. "
            f"Continuing to next step..."
        )

    return {"status": True, "message": summary_message}
