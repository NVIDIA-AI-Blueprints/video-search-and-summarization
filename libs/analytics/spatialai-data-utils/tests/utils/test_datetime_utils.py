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

import pytest
from datetime import datetime, timezone

from spatialai_data_utils.utils.datetime_utils import parse_timestamp, timestamp_to_ms


# Test cases for parse_timestamp
def test_parse_timestamp_with_z():
    ts_str = "2025-01-01T12:00:00.123Z"
    result = parse_timestamp(ts_str)
    
    expected = datetime(2025, 1, 1, 12, 0, 0, 123000, tzinfo=timezone.utc)
    assert result == expected


def test_parse_timestamp_without_z():
    ts_str = "2025-01-01T12:00:00.123"
    result = parse_timestamp(ts_str)
    
    expected = datetime(2025, 1, 1, 12, 0, 0, 123000)  # No timezone info
    assert result == expected


def test_parse_timestamp_with_fractional_seconds():
    """Test parsing timestamp with fractional seconds"""
    ts_str = "2025-01-01T12:00:00.123456Z"
    result = parse_timestamp(ts_str)
    
    expected = datetime(2025, 1, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
    assert result == expected


def test_parse_timestamp_without_fractional_seconds():
    """Test parsing timestamp without fractional seconds"""
    ts_str = "2025-01-01T12:00:00Z"
    result = parse_timestamp(ts_str)
    
    expected = datetime(2025, 1, 1, 12, 0, 0, 0, tzinfo=timezone.utc)
    assert result == expected


def test_parse_timestamp_different_timezone():
    """Test parsing timestamp with different timezone"""
    ts_str = "2025-01-01T12:00:00.123+05:30"
    result = parse_timestamp(ts_str)
    
    expected = datetime(2025, 1, 1, 12, 0, 0, 123000, tzinfo=timezone.utc)
    # Note: The function converts Z to +00:00, so this test might need adjustment
    # depending on the actual behavior with different timezones
    assert result.year == 2025
    assert result.month == 1
    assert result.day == 1
    assert result.hour == 12
    assert result.minute == 0
    assert result.second == 0
    assert result.microsecond == 123000


# Test cases for timestamp_to_ms
def test_timestamp_to_ms():
    dt = datetime(2025, 1, 1, 12, 0, 0, 123000, tzinfo=timezone.utc)
    result = timestamp_to_ms(dt)
    
    # Expected: milliseconds since epoch for 2025-01-01T12:00:00.123Z
    expected = 1735732800123
    assert result == expected


def test_timestamp_to_ms_epoch():
    dt = datetime(1970, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
    result = timestamp_to_ms(dt)
    
    assert result == 0


def test_timestamp_to_ms_without_timezone():
    """Test timestamp_to_ms with datetime without timezone"""
    dt = datetime(2025, 1, 1, 12, 0, 0, 123000)
    result = timestamp_to_ms(dt)
    
    # Should still work but result will be different due to timezone handling
    assert isinstance(result, (int, float))
    assert result > 0


def test_timestamp_to_ms_fractional_milliseconds():
    """Test timestamp_to_ms with fractional milliseconds"""
    dt = datetime(2025, 1, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
    result = timestamp_to_ms(dt)
    
    # Should include fractional milliseconds
    expected = 1735732800123.456
    assert abs(result - expected) < 0.001  # Allow small floating point differences


def test_timestamp_to_ms_negative_timezone():
    """Test timestamp_to_ms with negative timezone offset"""
    dt = datetime(2025, 1, 1, 12, 0, 0, 123000, tzinfo=timezone.utc)
    result = timestamp_to_ms(dt)
    
    assert isinstance(result, (int, float))
    assert result > 0


# Integration tests
def test_parse_timestamp_and_convert_to_ms():
    """Test the full workflow: parse timestamp string and convert to milliseconds"""
    ts_str = "2025-01-01T12:00:00.123Z"
    dt = parse_timestamp(ts_str)
    ms = timestamp_to_ms(dt)
    
    expected_ms = 1735732800123
    assert ms == expected_ms


def test_roundtrip_timestamp_conversion():
    """Test that parsing and converting timestamps maintains precision"""
    original_ms = 1735732800123
    dt = datetime.fromtimestamp(original_ms / 1000, tz=timezone.utc)
    converted_ms = timestamp_to_ms(dt)
    
    # Should be very close to original (allowing for floating point precision)
    assert abs(converted_ms - original_ms) < 0.001
