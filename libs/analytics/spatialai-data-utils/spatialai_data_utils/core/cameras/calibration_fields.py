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
Calibration Fields Module

This module provides functions for populating derived fields in calibration data,
including region metadata on sensors and group assignment on tripwires/ROIs.
"""

import logging

from spatialai_data_utils.utils.string_utils import natural_sort_key

logger = logging.getLogger(__name__)


def add_region_field(sensors, map_width, map_height):
    """
    Add region metadata to sensors based on map dimensions and calibration.

    This function calculates the region field for each sensor using the map size
    and the sensor's coordinate transformation parameters. The region represents
    the full map coverage area in world coordinates.

    :param sensors: List of sensor dictionaries with calibration data.
    :type sensors: List[dict]
    :param map_width: Width of the map image in pixels.
    :type map_width: int
    :param map_height: Height of the map image in pixels.
    :type map_height: int
    :return: None (modifies sensors in-place).
    :rtype: None
    """
    for sensor in sensors:
        try:
            # Extract coordinate transformation parameters
            translation = sensor["translationToGlobalCoordinates"]
            scale = sensor["scaleFactor"]

            # Calculate region origin in world coordinates
            origin = [-translation["x"], -translation["y"]]

            # Convert map dimensions from pixels to world coordinates
            length = map_height / scale
            width = map_width / scale

            # Add region metadata to sensor
            sensor["region"] = {
                "placeLevel": "region",
                "origin": origin,
                "dimensions": {"length": length, "width": width},
            }
        except KeyError as e:
            sensor_id = sensor.get("id", "unknown")
            logger.warning(
                f"Skipping sensor {sensor_id}: missing required calibration key {e}"
            )
            continue
        except (ZeroDivisionError, TypeError) as e:
            sensor_id = sensor.get("id", "unknown")
            logger.warning(
                f"Skipping sensor {sensor_id}: invalid calibration data - {e}"
            )
            continue


def _fill_groups_for_items(items: list, group_to_sensors: dict) -> dict:
    """
    Fill the "groups" field for a list of tripwires or ROIs.

    For each item, checks which BEV groups share at least one sensor with the
    item's sensor list, and sets the "groups" field accordingly.  Existing
    ``groups`` values are validated and corrected when they differ from the
    computed result.

    :param items: List of tripwire or ROI dictionaries, each with a "sensors" field.
    :type items: list[dict]
    :param group_to_sensors: Mapping from group names to sets of sensor IDs.
    :type group_to_sensors: dict[str, set[str]]
    :return: Summary dict with keys ``added`` (groups field was missing or
        empty), ``corrected`` (groups existed but was wrong), and
        ``unchanged`` (already correct).
    :rtype: dict[str, int]
    :raises ValueError: If any item has a missing or empty ``sensors`` list.
    """
    counts = {"added": 0, "corrected": 0, "unchanged": 0}

    for item in items:
        item_id = item.get("id", "unknown")
        if "sensors" not in item or not item["sensors"]:
            raise ValueError(
                f"Tripwire/ROI '{item_id}' must have a non-empty 'sensors' list"
            )
        item_sensors = set(item["sensors"])

        matching_groups = sorted(
            [
                group_name
                for group_name, group_sensors in group_to_sensors.items()
                if item_sensors & group_sensors
            ],
            key=natural_sort_key,
        )

        existing = item.get("groups")

        if existing is None or existing == []:
            counts["added"] += 1
            logger.info(f"  {item_id}: added groups {matching_groups}")
        elif sorted(existing, key=natural_sort_key) != matching_groups:
            counts["corrected"] += 1
            logger.warning(
                f"  {item_id}: corrected groups {existing} -> {matching_groups}"
            )
        else:
            counts["unchanged"] += 1

        item["groups"] = matching_groups

    return counts


def update_tripwire_roi_groups(
    calibration_data: dict,
    group_to_sensors: dict,
) -> None:
    """Fill/correct the ``groups`` field on tripwires and ROIs in *calibration_data*.

    Skipped silently when no tripwires or ROIs are present.
    Modifies *calibration_data* in place.

    :param calibration_data: Calibration data dictionary.
    :param group_to_sensors: Mapping from group names to sets of sensor IDs.
    """
    tripwires = calibration_data.get("tripwires", [])
    rois = calibration_data.get("rois", [])
    if not tripwires and not rois:
        return

    if tripwires:
        logger.info(f"Filling groups for {len(tripwires)} tripwire(s)")
    tw_counts = _fill_groups_for_items(tripwires, group_to_sensors)

    if rois:
        logger.info(f"Filling groups for {len(rois)} ROI(s)")
    roi_counts = _fill_groups_for_items(rois, group_to_sensors)

    for label, counts in [("Tripwires", tw_counts), ("ROIs", roi_counts)]:
        parts = []
        if counts["added"]:
            parts.append(f"{counts['added']} added")
        if counts["corrected"]:
            parts.append(f"{counts['corrected']} corrected")
        if counts["unchanged"]:
            parts.append(f"{counts['unchanged']} unchanged")
        if parts:
            logger.info(f"  {label}: {', '.join(parts)}")
