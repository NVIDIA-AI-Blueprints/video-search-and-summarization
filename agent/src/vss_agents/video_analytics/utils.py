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
"""Utility functions for video analytics tools."""

from datetime import UTC
from datetime import datetime
from datetime import timedelta
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def validate_iso_timestamp(timestamp: str) -> str:
    """
    Validate ISO 8601 timestamp format with milliseconds and Z timezone.

    Expected format: YYYY-MM-DDTHH:MM:SS.sssZ
    Example: 2022-08-25T00:00:10.000Z

    Args:
        timestamp: The timestamp string to validate

    Returns:
        str: The validated timestamp string

    Raises:
        ValueError: If timestamp format is invalid
    """
    # ISO 8601 pattern with milliseconds and Z timezone
    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"

    if not re.match(iso_pattern, timestamp):
        raise ValueError(
            f"Video Analytics: Invalid timestamp format: '{timestamp}'. "
            f"Expected ISO 8601 format with milliseconds: YYYY-MM-DDTHH:MM:SS.sssZ "
            f"(e.g., 2022-08-25T00:00:10.000Z)"
        )

    # Validate that it can be parsed as a valid datetime
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"Video Analytics: Invalid datetime values in timestamp '{timestamp}': {e}") from e

    return timestamp


def build_sensor_map(sensors: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """
    Build a hierarchical map of places to sensor IDs.

    Creates a structure: city -> intersection -> [sensor_ids]

    Args:
        sensors: List of sensor objects from calibration configuration.
                Each sensor should have 'id' and 'place' fields.

    Returns:
        Dict mapping city -> intersection -> list of sensor IDs

    Example:
        {
            "San Jose": {
                "Intersection_A": ["sensor-1", "sensor-2"],
                "Intersection_B": ["sensor-3"]
            },
            "Mountain View": {
                "Intersection_C": ["sensor-4", "sensor-5"]
            }
        }
    """
    place_map: dict[str, dict[str, list[str]]] = {}

    for sensor in sensors:
        # Validate sensor has required structure
        if "place" not in sensor or not isinstance(sensor["place"], list) or len(sensor["place"]) < 2:
            logger.warning(f"Skipping sensor due to missing or malformed 'place': {sensor}")
            continue

        # Extract city and intersection values
        city = sensor["place"][0].get("value")
        intersection = sensor["place"][1].get("value")

        if city is None or intersection is None:
            logger.warning(f"Sensor missing city or intersection value: {sensor}")
            continue

        # Initialize nested dictionaries if needed
        if city not in place_map:
            place_map[city] = {}

        if intersection not in place_map[city]:
            place_map[city][intersection] = []

        # Add sensor ID if present
        if "id" in sensor:
            sensor_id = sensor["id"]
            place_map[city][intersection].append(sensor_id)
        else:
            logger.warning(f"Skipping sensor with malformed place due to missing 'id': {sensor}")

    return place_map


def build_place_map(sensors: list[dict[str, Any]]) -> dict[str, list[str]]:
    """
    Build a map from city name to a list of intersections (no sensor id information).

    Creates a structure: city -> [intersection1, intersection2, ...]

    Args:
        sensors: List of sensor objects from calibration configuration.
                Each sensor should have 'place' field as list of at least two dicts (city, intersection).

    Returns:
        Dict mapping city -> list of intersection names

    Example:
        {
            "San Jose": ["Intersection_A", "Intersection_B"],
            "Mountain View": ["Intersection_C"]
        }
    """
    city_map: dict[str, set[str]] = {}

    for sensor in sensors:
        # Validate sensor has required structure
        if "place" not in sensor or not isinstance(sensor["place"], list) or len(sensor["place"]) < 2:
            logger.warning(f"Skipping sensor due to missing or malformed 'place': {sensor}")
            continue

        city = sensor["place"][0].get("value")
        intersection = sensor["place"][1].get("value")

        if city is None or intersection is None:
            logger.warning(f"Sensor missing city or intersection value: {sensor}")
            continue

        if city not in city_map:
            city_map[city] = set()

        city_map[city].add(intersection)

    # Convert sets to sorted lists for consistency
    city_map_lists: dict[str, list[str]] = {city: sorted(intersections) for city, intersections in city_map.items()}
    return city_map_lists


def parse_vst_sensor_list_response(sensors_str: str) -> set[str]:
    """
    Parse VST sensor list response string into a set of sensor names.

    Supports:
    - VSTSensorListOutput format: {"sensor_names": ["name1", "name2", ...]}
    - Legacy format: {"sensor_id": {"name": "...", "sensorId": "...", ...}, ...}

    Args:
        sensors_str: String response from VST sensor list tool

    Returns:
        Set of sensor names extracted from the response (always set[str], empty on parse failure).
    """
    text = (sensors_str or "").strip()

    # Trim surrounding quotes if present (e.g., "..." or '...')
    if text and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1]

    if not text:
        return set()

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as e:
        logger.debug(f"Failed to parse VST sensor list response: {e}")
        return set()

    result: set[str] = set()

    if isinstance(decoded, dict):
        if "sensor_names" in decoded and isinstance(decoded["sensor_names"], list):
            for item in decoded["sensor_names"]:
                if isinstance(item, str):
                    result.add(item)
        else:
            # Fallback: {"sensor_id": {"name": ...}, ...}
            for value in decoded.values():
                if isinstance(value, dict) and "name" in value:
                    name = value["name"]
                    if isinstance(name, str):
                        result.add(name)

    return result


def compute_bucket_size_seconds(start_time: str, end_time: str, bucket_count: int) -> int:
    """
    Compute bucket size in seconds for histogram.

    Args:
        start_time: Start timestamp in ISO format
        end_time: End timestamp in ISO format
        bucket_count: Number of buckets desired

    Returns:
        Bucket size in seconds
    """
    if bucket_count <= 0:
        raise ValueError(f"Video Analytics: bucket_count must be a positive integer, got {bucket_count}")

    start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))

    time_range_seconds = (end_dt - start_dt).total_seconds()
    bucket_size_seconds = int(time_range_seconds / bucket_count)

    # Ensure at least 1 second bucket size
    return max(1, bucket_size_seconds)


def create_empty_histogram_buckets(start_time: str, end_time: str, bucket_size_sec: int) -> list[dict[str, Any]]:
    """
    Create empty histogram buckets covering the time range.

    Args:
        start_time: Start timestamp in ISO format
        end_time: End timestamp in ISO format
        bucket_size_sec: Size of each bucket in seconds

    Returns:
        List of empty histogram buckets with start and end times
    """
    if bucket_size_sec <= 0:
        raise ValueError(f"Video Analytics: bucket_size_sec must be a positive integer, got {bucket_size_sec}")

    start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))

    # Align start_dt to bucket boundary (floor to bucket_size_sec)
    epoch_seconds = int(start_dt.timestamp())
    aligned_epoch = (epoch_seconds // bucket_size_sec) * bucket_size_sec
    current_start = datetime.fromtimestamp(aligned_epoch, tz=UTC)

    buckets = []

    while current_start < end_dt:
        current_end = current_start + timedelta(seconds=bucket_size_sec)
        if current_end > end_dt:
            current_end = end_dt

        # Format with milliseconds explicitly (isoformat() omits .000 when microseconds are 0)
        start_str = current_start.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"  # Convert microseconds to milliseconds
        end_str = current_end.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        buckets.append({"start": start_str, "end": end_str, "objects": []})

        current_start = current_end

    return buckets


def create_events_from_incidents(incidents: list[dict[str, Any]]) -> tuple[list[tuple[datetime, int]], int]:
    """
    Convert incident list to events for overlap analysis using sweep line algorithm.

    Args:
        incidents: List of incident dictionaries with 'timestamp' and 'end' fields

    Returns:
        tuple: (sorted events list, valid_incident_count)
            - events: List of (datetime, delta) tuples where delta is +1 for start, -1 for end
            - valid_incident_count: Number of incidents with valid timestamps
    """
    events = []
    valid_incident_count = 0

    for incident in incidents:
        start_time_str = incident.get("timestamp")
        end_time_str = incident.get("end")

        if start_time_str and end_time_str:
            # Parse timestamps
            start = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))

            # Add start event (+1) and end event (-1)
            events.append((start, 1))  # Incident starts
            events.append((end, -1))  # Incident ends
            valid_incident_count += 1

    # Sort events by time, with start events (+1) before end events (-1) at same time
    events.sort(key=lambda x: (x[0], -x[1]))

    return events, valid_incident_count


def sweep_overlapping_incidents(
    events: list[tuple[datetime, int]],
) -> tuple[int, datetime | None, int, datetime | None]:
    """
    Sweep through events to find min and max overlapping counts.

    Uses sweep line algorithm to efficiently find both minimum and maximum
    overlapping incidents in a single pass.

    Args:
        events: Sorted list of (time, delta) tuples where delta is +1 for start, -1 for end

    Returns:
        tuple: (max_count, max_time, min_count, min_time)
            - max_count: Maximum number of overlapping incidents
            - max_time: Time when maximum overlap occurred
            - min_count: Minimum number of overlapping incidents
            - min_time: Time when minimum overlap occurred
    """
    current_count = 0
    max_count = 0
    max_time: datetime | None = None
    min_count: int | float = float("inf")
    min_time: datetime | None = None

    for time, delta in events:
        current_count += delta
        if current_count > max_count:
            max_count = current_count
            max_time = time
        if current_count < min_count:
            min_count = current_count
            min_time = time

    # Convert min_count to int (will be inf if no events, convert to 0)
    final_min_count = 0 if min_count == float("inf") else int(min_count)
    return max_count, max_time, final_min_count, min_time
