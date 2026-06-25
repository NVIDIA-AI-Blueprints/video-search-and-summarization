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

from spatialai_data_utils.utils.datetime_utils import parse_timestamp, timestamp_to_ms
from spatialai_data_utils.utils.filesystem_utils import load_json_from_file

logger = logging.getLogger(__name__)


def get_sensor_bev_group_map(calibration_file):
    """
    Build a sensor-to-BEV-group mapping from calibration content.

    :param calibration_file: Parsed calibration JSON containing a ``sensors``
        list where each sensor has ``id`` and ``group.name`` fields.
    :type calibration_file: dict
    :return: Mapping from sensor ID to BEV group name.
    :rtype: dict
    """
    sensor_to_bev_map = {sensor['id']:sensor['group']['name'] for sensor in calibration_file['sensors']}
    return sensor_to_bev_map


def ground_truth_data_validation(args, ground_truth_file_path, calibration_file_path, fps):
    """
    Validate ground-truth JSONL synchronization and expected record counts.

    Reads ground-truth records, groups them by timestamp, verifies that each
    timestamp contains the expected calibration sensors for the active BEV
    groups, checks timestamp spacing against configured tolerances, and returns
    warning/error count thresholds for downstream validation.

    :param args: Parsed validation arguments containing timestamp tolerances,
        simulation duration, and ground-truth record-count threshold ratios.
    :type args: argparse.Namespace
    :param ground_truth_file_path: Path to the ground-truth JSONL file.
    :type ground_truth_file_path: str
    :param calibration_file_path: Path to the calibration JSON file.
    :type calibration_file_path: str
    :param fps: Frames per second used to compute expected record thresholds.
    :type fps: int | float
    :return: A validation result dictionary. Successful results include
        ``actual_count``, warning/error thresholds, ``bev_to_sensor_map``, and
        ``unique_bev_groups``.
    :rtype: dict
    """
    last_seen_timestamp = None
    sensor_id_set = set()
    bev_to_sensor_map = {}
    unique_bev_groups = set()

    calibration_file_content = load_json_from_file(calibration_file_path)
    calibration_sensor_to_bev_map = get_sensor_bev_group_map(calibration_file_content)
    bev_sensors_group_created = False
    calibration_bev_sensors_processed = set()
    ground_truth_record_count = 1

    with open(ground_truth_file_path, 'r') as ground_truth:
        for record in ground_truth:
            try:
                record = json.loads(record)
                record_timestamp_in_ms = timestamp_to_ms(parse_timestamp(record['timestamp']))
                if (not last_seen_timestamp) or (record_timestamp_in_ms == last_seen_timestamp):
                    last_seen_timestamp = record_timestamp_in_ms
                    sensor_id_set.add(record['sensorId'])
                else:
                    if not bev_sensors_group_created:
                        unknown_sensors = sensor_id_set.difference(calibration_sensor_to_bev_map.keys())
                        if unknown_sensors:
                            return {
                                "status": False,
                                "message": f"Unknown sensorId(s) in ground truth: {sorted(unknown_sensors)}",
                            }
                        bev_sensors_group_created = True
                        unique_bev_groups = {calibration_sensor_to_bev_map[sensor] for sensor in sensor_id_set}
                        for sensor in calibration_sensor_to_bev_map:
                            bev_group = calibration_sensor_to_bev_map[sensor]
                            if bev_group in unique_bev_groups:
                                if bev_group not in bev_to_sensor_map:
                                    bev_to_sensor_map[bev_group] = [sensor]
                                else:
                                    bev_to_sensor_map[bev_group].append(sensor)
                        calibration_bev_sensors_processed = set([sensor for sensor_list in bev_to_sensor_map.values() for sensor in sensor_list])
                        logger.info(f"Calibration sensors that are getting processed: {sorted(list(calibration_bev_sensors_processed))}")
                    set_diff = (
                        calibration_bev_sensors_processed.difference(sensor_id_set)
                        .union(sensor_id_set.difference(calibration_bev_sensors_processed))
                    )
                    if set_diff:
                        return {
                            "status": False,
                            "message": f"Timestamps of sensors are not synced across the ground truth file. Timestamp {record['timestamp']} is not present in the ground truth file for sensors: {set_diff}"
                        }
                    else:
                        time_difference = record_timestamp_in_ms - last_seen_timestamp
                        if time_difference >= args.min_tolerance_ms_for_bev_record and time_difference <= args.max_tolerance_ms_for_bev_record:
                            last_seen_timestamp = record_timestamp_in_ms
                            sensor_id_set = {record['sensorId']}
                            ground_truth_record_count += 1
                        else:
                            return {
                                "status": False,
                                "message": (
                                    f"Timestamp gap {time_difference:g} ms is outside the allowed range "
                                    f"[{args.min_tolerance_ms_for_bev_record}, {args.max_tolerance_ms_for_bev_record}]"
                                ),
                            }
            except json.JSONDecodeError:
                continue

        if last_seen_timestamp is None:
            return {
                "status": False,
                "message": "Empty or invalid ground truth file",
            }

        warning_threshold_record_count = int(args.simulation_seconds * fps * args.ground_truth_record_count_warning_threshold_ratio)
        error_threshold_record_count = int(args.simulation_seconds * fps * args.ground_truth_record_count_error_threshold_ratio)
        
        return {
            "status": True,
            "actual_count": ground_truth_record_count,
            "warning_threshold_record_count": warning_threshold_record_count,
            "error_threshold_record_count": error_threshold_record_count,
            "bev_to_sensor_map": bev_to_sensor_map,
            "unique_bev_groups": unique_bev_groups
        }

def get_unique_types_from_ground_truth(ground_truth_file):
    """
    Extract unique object types from a ground-truth JSONL file.

    Invalid JSON lines are skipped. Objects with missing or blank ``type``
    values raise an error because object classes are required for validation and
    evaluation.

    :param ground_truth_file: Path to the ground-truth JSONL file.
    :type ground_truth_file: str
    :return: Sorted list of unique object type strings.
    :rtype: list[str]
    :raises ValueError: If any ground-truth object has a blank or empty type.
    """
    unique_types = set()
    with open(ground_truth_file, 'r') as f:
        for line in f:
            try:
                data = json.loads(line)
                if 'objects' in data:
                    for obj in data['objects']:
                        if 'type' in obj:
                            if not obj['type'] or obj['type'].strip() == '':
                                raise ValueError("Found object with blank/empty type in ground truth file")
                            unique_types.add(obj['type'])
            except json.JSONDecodeError:
                continue
    
    return sorted(list(unique_types))
