# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from datetime import datetime, timedelta, timezone

import numpy as np
from google.protobuf.timestamp_pb2 import Timestamp

from mdx.analytics.core.constants import DATE_PATTERN, NANOS_PER_MILLISECOND, NANOS_PER_SECOND


def cosine_similarity(x: np.ndarray, y: np.ndarray) -> float:
    """
    Calculate the cosine similarity between two vectors.
    The cosine similarity is the cosine of the angle between the vectors,
    which is equal to the dot product of the vectors divided by the product of their magnitudes.

    :param np.ndarray x: First vector for similarity calculation.
    :param np.ndarray y: Second vector for similarity calculation.
    :return float: Cosine similarity value between the two vectors, ranging from -1 to 1.
    :raises AssertionError: If the input vectors have different lengths.

    Examples::
        >>> x = np.array([1, 2, 3])
        >>> y = np.array([4, 5, 6])
        >>> similarity = cosine_similarity(x, y)
        >>> print(f"Cosine similarity: {similarity}")
    """
    assert len(x) == len(y), "Vectors must be of the same length"
    return dot_product(x, y) / (magnitude(x) * magnitude(y))


def dot_product(x: np.ndarray, y: np.ndarray) -> float:
    """
    Calculate the dot product of two vectors.
    The dot product is the sum of the products of corresponding elements.

    :param np.ndarray x: First vector for dot product calculation.
    :param np.ndarray y: Second vector for dot product calculation.
    :return float: Dot product of the two vectors.
    :raises AssertionError: If the input vectors have different lengths.

    Examples::
        >>> x = np.array([1, 2, 3])
        >>> y = np.array([4, 5, 6])
        >>> product = dot_product(x, y)
        >>> print(f"Dot product: {product}")
    """
    return np.sum(x * y)


def normalize(x: np.ndarray) -> np.ndarray:
    """
    Normalize a vector to unit length.
    The normalized vector has the same direction but a magnitude of 1.

    :param np.ndarray x: Vector to be normalized.
    :return np.ndarray: Normalized vector with unit length.
    :raises ZeroDivisionError: If the input vector has zero magnitude.

    Examples::
        >>> x = np.array([3, 4])
        >>> normalized = normalize(x)
        >>> print(f"Normalized vector: {normalized}")
        >>> print(f"Magnitude: {magnitude(normalized)}")  # Should be 1.0
    """
    m = magnitude(x)
    return x / m


def magnitude(x: np.ndarray) -> float:
    """
    Calculate the magnitude (Euclidean norm) of a vector.
    The magnitude is the square root of the sum of squared elements.

    :param np.ndarray x: Vector whose magnitude is to be calculated.
    :return float: Magnitude (length) of the vector.

    Examples::
        >>> x = np.array([3, 4])
        >>> mag = magnitude(x)
        >>> print(f"Magnitude: {mag}")  # Should be 5.0
    """
    return np.sqrt(np.sum(x**2))


def get_proto_time_now() -> Timestamp:
    """
    Get the current time as a Google protobuf Timestamp.
    The timestamp represents the current UTC time with nanosecond precision.

    :return Timestamp: Current time in protobuf Timestamp format.

    Examples::
        >>> current_time = get_proto_time_now()
        >>> print(f"Current time: {current_time}")
    """
    ts = Timestamp()
    ts.GetCurrentTime()
    return ts


def get_offset_in_ms(ts1: Timestamp, ts2: Timestamp) -> int:
    """
    Calculate the offset in milliseconds between two protobuf Timestamps.
    The offset is calculated as ts1 - ts2.

    :param Timestamp ts1: First timestamp for offset calculation.
    :param Timestamp ts2: Second timestamp for offset calculation.
    :return int: Offset in milliseconds between ts1 and ts2.

    Examples::
        >>> ts1 = get_proto_time_now()
        >>> # Wait for 1 second
        >>> ts2 = get_proto_time_now()
        >>> offset = get_offset_in_ms(ts2, ts1)
        >>> print(f"Offset: {offset}ms")  # Should be approximately 1000
    """
    return ts1.ToMilliseconds() - ts2.ToMilliseconds()


def add_offset_in_ms(ts: Timestamp, offset_in_ms: int) -> Timestamp:
    """
    Add an offset in milliseconds to a protobuf Timestamp.
    The offset can be positive (future) or negative (past).

    :param Timestamp ts: Original timestamp to add offset to.
    :param int offset_in_ms: Offset in milliseconds to add (can be negative).
    :return Timestamp: New timestamp with the offset added.

    Examples::
        >>> current = get_proto_time_now()
        >>> future = add_offset_in_ms(current, 1000)  # 1 second in future
        >>> past = add_offset_in_ms(current, -1000)   # 1 second in past
        >>> print(f"Future time: {future}")
        >>> print(f"Past time: {past}")
    """
    n = ts.nanos + offset_in_ms * NANOS_PER_MILLISECOND
    s = ts.seconds + n // NANOS_PER_SECOND
    return Timestamp(seconds=s, nanos=n % NANOS_PER_SECOND)


def str_to_bool(s: str) -> bool:
    """
    Convert a string representation of a boolean value to a Python bool.
    The string is case-insensitive and must be "true" to return True.

    :param str s: String to convert to boolean.
    :return bool: True if the string is "true" (case-insensitive), False otherwise.

    Examples::
        >>> str_to_bool("true")   # Returns True
        >>> str_to_bool("True")   # Returns True
        >>> str_to_bool("false")  # Returns False
        >>> str_to_bool("other")  # Returns False
    """
    return s.lower() == "true"


def strRGB_to_tupleBGR(s: str) -> tuple:
    """
    Convert a string representation of RGB color to a BGR tuple.
    The input string should be in the format "R,G,B" where R, G, and B are integers.

    :param str s: RGB color string in format "R,G,B".
    :return tuple[int, int, int]: BGR color as a tuple of integers (B, G, R).
    :raises ValueError: If the input string is not in the correct format.

    Examples::
        >>> color = strRGB_to_tupleBGR("255,0,0")  # Red in RGB
        >>> print(color)  # (0, 0, 255) - Red in BGR
    """
    strRGB = s.split(",")
    tupleBGR = (int(strRGB[2]), int(strRGB[1]), int(strRGB[0]))
    return tupleBGR


def time_x_minutes_ago(minutes: int) -> datetime:
    """
    Calculate the datetime that was x minutes ago from the current UTC time.

    :param int minutes: Number of minutes to go back from current time.
    :return datetime: Datetime object representing the time x minutes ago in UTC.

    Examples::
        >>> past_time = time_x_minutes_ago(30)
        >>> print(past_time)  # 2024-03-14 12:00:00+00:00 (30 minutes ago)
    """
    utc_now = datetime.now(timezone.utc)
    return utc_now - timedelta(minutes=minutes)


def get_proto_time_x_minutes_ago(minutes: int) -> Timestamp:
    """
    Get the time of x minutes ago as a Google protobuf Timestamp.
    The timestamp represents the UTC time x minutes ago with nanosecond precision.

    :param int minutes: Number of minutes to go back from current time.
    :return Timestamp: Timestamp representing the time x minutes ago.

    Examples::
        >>> past_time = get_proto_time_x_minutes_ago(30)
        >>> print(f"Time 30 minutes ago: {past_time}")
    """
    ts = Timestamp()
    ts.FromDatetime(time_x_minutes_ago(minutes))
    return ts


def convert_datetime_to_iso_8601_with_z_suffix(dt: datetime) -> str:
    """
    Convert a datetime object to ISO 8601 format with Z suffix.
    The output string will be in the format "YYYY-MM-DDTHH:MM:SS.mmmZ".

    :param datetime dt: Datetime object to convert.
    :return str: ISO 8601 formatted string with Z suffix.

    Examples::
        >>> now = datetime.now(timezone.utc)
        >>> iso_str = convert_datetime_to_iso_8601_with_z_suffix(now)
        >>> print(iso_str)  # "2024-03-14T12:00:00.000Z"
    """
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def extract_sensor_id(sensor_id: str) -> str:
    """
    Extract the base sensor ID by removing any trailing date pattern.
    If the sensor ID contains a date pattern after "__", it will be removed.

    :param str sensor_id: The original sensor ID string.
    :return str: The extracted base sensor ID without any date pattern.

    Examples::
        >>> extract_sensor_id("camera_1__20240314")  # Returns "camera_1"
        >>> extract_sensor_id("camera_1")            # Returns "camera_1"
    """
    i = sensor_id.rfind("__")
    if i < 0:
        return sensor_id
    potential_time = sensor_id[i:]
    if bool(DATE_PATTERN.match(potential_time)):
        return sensor_id[:i]
    return sensor_id


def iso_to_epoch(timestamp: str | datetime | Timestamp | int) -> int:
    """
    Convert various timestamp formats to epoch time in milliseconds.
    Supports ISO 8601 strings, datetime objects, protobuf Timestamps, and epoch timestamps.

    :param str | datetime, Timestamp, int timestamp: Input timestamp in one of the supported formats:
        - str: ISO 8601 format (e.g., "2024-03-14T12:00:00Z")
        - datetime: Python datetime object
        - Timestamp: Google protobuf Timestamp
        - int: Epoch timestamp in milliseconds
    :return int: Epoch time in milliseconds.
    :raises ValueError: If the input format is invalid.

    Examples::
        >>> # From ISO string
        >>> epoch = iso_to_epoch("2024-03-14T12:00:00Z")
        >>> # From datetime
        >>> epoch = iso_to_epoch(datetime.now())
        >>> # From protobuf Timestamp
        >>> epoch = iso_to_epoch(Timestamp())
        >>> # From epoch milliseconds
        >>> epoch = iso_to_epoch(1710417600000)
    """
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

    if isinstance(timestamp, datetime):
        epoch_time = int(timestamp.timestamp() * 1000)
    elif isinstance(timestamp, Timestamp):
        epoch_time = int(timestamp.seconds * 1000 + timestamp.nanos / 1_000_000)
    else:
        epoch_time = int(timestamp)

    return epoch_time
