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
Camera Filtering Module

This module provides utility functions for filtering and validating camera sensors
in calibration data, including checking sensor existence and validation.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


def check_if_sensor_in_sensor_set(sensor, sensor_set):
    """
    Check if a sensor exists in a sensor set.

    This is a base utility function that checks if a sensor ID is present
    in a set of sensor IDs.

    :param sensor: Sensor ID to check.
    :type sensor: str
    :param sensor_set: Set of sensor IDs.
    :type sensor_set: set
    :return: True if sensor exists in the set, False otherwise.
    :rtype: bool
    """
    return sensor in sensor_set


def check_sensors_in_calibration_dict(sensor_ids, calibration_data):
    """
    Check if multiple sensor IDs exist in loaded calibration data.

    This function validates whether all given sensor IDs are present in the
    calibration data dictionary.

    :param sensor_ids: List of sensor IDs to check.
    :type sensor_ids: list or iterable
    :param calibration_data: Loaded calibration data dictionary.
    :type calibration_data: dict
    :return: True if all sensor IDs exist in calibration data, False otherwise.
    :rtype: bool
    """
    try:
        # Validate calibration data structure
        if not isinstance(calibration_data, dict):
            logger.warning("Calibration data must be a dictionary")
            return False

        if "sensors" not in calibration_data:
            logger.warning("Calibration data missing 'sensors' key")
            return False

        # Build sensor set from calibration data
        sensors_in_calib = set(
            sensor.get("id")
            for sensor in calibration_data["sensors"]
            if sensor.get("id") is not None
        )

        # Check each sensor_id
        for sensor_id in sensor_ids:
            if not check_if_sensor_in_sensor_set(sensor_id, sensors_in_calib):
                logger.debug(f"Sensor '{sensor_id}' not found in calibration data")
                return False

        return True

    except Exception as e:
        logger.error(f"Error validating sensor IDs: {e}")
        return False


def check_sensor_in_calibration_dict(sensor, calib_json):
    """
    Check if a single sensor ID exists in a raw NVSchema calibration JSON.

    This function takes the **raw on-disk** NVSchema calibration dict
    (the parsed JSON file with a top-level ``"sensors"`` array) — not
    the post-processed flat ``calib_dict`` keyed by camera name.  It
    walks ``calib_json["sensors"]``, collects every sensor ``"id"``,
    and reports whether the requested *sensor* matches one of them
    (delegating the matching to :func:`check_if_sensor_in_sensor_set`).

    :param sensor: Sensor ID to check.
    :type sensor: str
    :param calib_json: Raw parsed NVSchema calibration JSON,
        e.g. ``{"sensors": [{"id": "Camera_01", ...}, ...], ...}``.
    :type calib_json: dict
    :return: True if sensor ID exists in calibration data, False otherwise.
    :rtype: bool
    """
    try:
        # Validate calibration data structure
        if not isinstance(calib_json, dict):
            logger.warning("Calibration data must be a dictionary")
            return False

        if "sensors" not in calib_json:
            logger.warning("Calibration data missing 'sensors' key")
            return False

        # Build sensor set from calibration data
        calibration_set = set(
            s.get("id") for s in calib_json["sensors"] if s.get("id") is not None
        )

        # Check if sensor exists in the set
        if not check_if_sensor_in_sensor_set(sensor, calibration_set):
            logger.debug(f"Sensor '{sensor}' not found in calibration data")
            return False

        return True

    except Exception as e:
        logger.error(f"Error validating sensor '{sensor}': {e}")
        return False


def check_sensors_in_calibration_file(sensor_ids, calibration_file):
    """
    Check if multiple sensor IDs exist in calibration file.

    This function loads calibration data from a file and validates whether
    all given sensor IDs are present in it.

    :param sensor_ids: List of sensor IDs to check.
    :type sensor_ids: list or iterable
    :param calibration_file: Path to calibration JSON file.
    :type calibration_file: str
    :return: True if all sensor IDs exist in calibration file, False otherwise.
    :rtype: bool
    """
    try:
        # Check if file exists
        if not os.path.exists(calibration_file):
            logger.warning(f"Calibration file not found: {calibration_file}")
            return False

        # Load calibration data from file
        with open(calibration_file, "r") as f:
            calibration_data = json.load(f)

        # Use check_sensors_in_calibration_dict to validate all sensors
        return check_sensors_in_calibration_dict(sensor_ids, calibration_data)

    except Exception as e:
        logger.error(f"Error loading calibration file '{calibration_file}': {e}")
        return False


def check_sensor_in_calibration_file(sensor, calibration_file):
    """
    Check if a sensor ID exists in calibration file.

    This function loads calibration data from a file and validates whether
    a given sensor ID is present in it.

    :param sensor: Sensor ID to check.
    :type sensor: str
    :param calibration_file: Path to calibration JSON file.
    :type calibration_file: str
    :return: True if sensor ID exists in calibration file, False otherwise.
    :rtype: bool
    """
    try:
        # Check if file exists
        if not os.path.exists(calibration_file):
            logger.warning(f"Calibration file not found: {calibration_file}")
            return False

        # Load calibration data from file
        with open(calibration_file, "r") as f:
            calibration_data = json.load(f)

        # Use check_sensor_in_calibration_dict to validate the sensor
        return check_sensor_in_calibration_dict(sensor, calibration_data)

    except Exception as e:
        logger.error(f"Error loading calibration file '{calibration_file}': {e}")
        return False


def filter_sensors_in_objects(objects, sensor_names, object_type="object"):
    """
    Filter sensor lists in objects (ROIs, tripwires, etc.) to only include specified sensor names.

    This function processes a list of objects (such as ROIs or tripwires) and filters
    each object's sensor list to only include sensors that match the specified names.
    If an object ends up with an empty sensor list, its groups field is cleared.

    :param objects: List of object dictionaries, each potentially containing 'sensors' and 'groups' fields.
    :type objects: list of dict
    :param sensor_names: List of sensor IDs to keep.
    :type sensor_names: list of str
    :param object_type: Type of objects being filtered (for logging purposes, e.g., 'ROI', 'tripwire').
    :type object_type: str
    :return: Filtered list of objects with updated sensor lists.
    :rtype: list of dict
    """
    if not isinstance(objects, list):
        logger.warning(f"{object_type} must be a list")
        return objects

    filtered_objects = []
    sensor_names_set = set(sensor_names)

    for obj in objects:
        # Create a copy of the object
        filtered_obj = obj.copy()

        # Filter the sensors list in this object
        if "sensors" in obj and isinstance(obj["sensors"], list):
            filtered_obj_sensors = [s for s in obj["sensors"] if s in sensor_names_set]
            filtered_obj["sensors"] = filtered_obj_sensors

            # If no sensors remain, clear the groups field
            if len(filtered_obj_sensors) == 0 and "groups" in filtered_obj:
                filtered_obj["groups"] = []
                logger.debug(
                    f"{object_type} '{obj.get('id', 'unknown')}' has no matching sensors, clearing groups"
                )

        filtered_objects.append(filtered_obj)

    logger.info(
        f"Filtered {object_type} sensor lists across {len(filtered_objects)} {object_type}"
    )
    return filtered_objects


def filter_sensors_by_names(calibration_data, sensor_names):
    """
    Filter calibration data to only include specified sensor names.

    This function validates that all specified sensor names exist in the
    calibration data and returns a filtered version containing only those sensors.
    Also filters sensor lists in ROIs and tripwires, and clears their groups if
    no sensors remain. Logs detailed error messages if validation fails.

    :param calibration_data: Dictionary containing calibration data with 'sensors' field.
    :type calibration_data: dict
    :param sensor_names: List of sensor IDs to filter.
    :type sensor_names: list of str
    :return: Filtered calibration data containing only specified sensors, or None if validation fails.
    :rtype: dict or None
    """
    try:
        # Validate calibration data structure
        if not isinstance(calibration_data, dict):
            logger.error("Calibration data must be a dictionary")
            return None

        if "sensors" not in calibration_data:
            logger.error("Calibration data missing 'sensors' key")
            return None

        logger.info(f"Filtering sensors by names: {sensor_names}")

        # Validate that all specified sensor names exist
        if not check_sensors_in_calibration_dict(sensor_names, calibration_data):
            logger.error(
                "One or more sensor names do not exist in the calibration data."
            )
            logger.error(f"Requested sensors: {sensor_names}")
            logger.error(
                f"Available sensors: {[sensor['id'] for sensor in calibration_data['sensors']]}"
            )
            return None

        # Filter calibration data to only include specified sensors (deep copy to avoid mutation)
        filtered_sensors = [
            sensor.copy()
            for sensor in calibration_data["sensors"]
            if sensor["id"] in sensor_names
        ]

        # Create a filtered copy of calibration data
        filtered_calibration = calibration_data.copy()
        filtered_calibration["sensors"] = filtered_sensors

        logger.info(f"Filtered to {len(filtered_sensors)} sensor(s)")

        # Filter ROIs if they exist
        if "rois" in calibration_data:
            filtered_calibration["rois"] = filter_sensors_in_objects(
                calibration_data["rois"], sensor_names, object_type="rois"
            )

        # Filter tripwires if they exist
        if "tripwires" in calibration_data:
            filtered_calibration["tripwires"] = filter_sensors_in_objects(
                calibration_data["tripwires"], sensor_names, object_type="tripwires"
            )

        return filtered_calibration

    except Exception as e:
        logger.error(f"Error filtering sensors by names: {e}")
        return None
