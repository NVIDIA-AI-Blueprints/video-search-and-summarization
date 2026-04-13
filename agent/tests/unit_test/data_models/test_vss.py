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
"""Tests for vss_agents/data_models/vss.py."""

from datetime import UTC
from datetime import datetime

import pytest

from vss_agents.data_models.vss import MediaInfoOffset
from vss_agents.data_models.vss import float_to_int
from vss_agents.data_models.vss import remove_timezone
from vss_agents.data_models.vss import timestamp_validator


class TestFloatToInt:
    """Tests for float_to_int function."""

    def test_float_to_int_positive(self):
        """Test converting positive float to int (ceil)."""
        assert float_to_int(1.1) == 2
        assert float_to_int(1.9) == 2
        assert float_to_int(1.0) == 1

    def test_float_to_int_zero(self):
        """Test converting zero."""
        assert float_to_int(0.0) == 0

    def test_float_to_int_already_int(self):
        """Test converting integer value."""
        assert float_to_int(5) == 5

    def test_float_to_int_none(self):
        """Test converting None returns None."""
        assert float_to_int(None) is None

    def test_float_to_int_large(self):
        """Test converting large float."""
        assert float_to_int(999.1) == 1000


class TestTimestampValidator:
    """Tests for timestamp_validator function."""

    def test_valid_rfc3339_timestamp(self):
        """Test valid RFC3339 timestamp."""

        # Create a mock validation_info
        class MockValidationInfo:
            field_name = "timestamp"

        result = timestamp_validator("2024-01-15T10:30:45.123Z", MockValidationInfo())
        assert result == "2024-01-15T10:30:45.123Z"

    def test_invalid_timestamp_format(self):
        """Test that timestamp_validator raises ValueError for malformed timestamp strings."""

        class MockValidationInfo:
            field_name = "timestamp"

        with pytest.raises(ValueError):
            timestamp_validator("2024-01-15", MockValidationInfo())

    def test_invalid_timestamp_values(self):
        """Test invalid timestamp values."""

        class MockValidationInfo:
            field_name = "timestamp"

        with pytest.raises(ValueError):
            timestamp_validator("2024-13-45T99:99:99.999Z", MockValidationInfo())


class TestRemoveTimezone:
    """Tests for remove_timezone function."""

    def test_remove_timezone_from_z_suffix(self):
        """Test removing timezone from Z suffix string."""
        result = remove_timezone("2024-01-15T10:30:45.123456Z")
        assert result.tzinfo is None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_remove_timezone_from_offset(self):
        """Test removing timezone from offset string."""
        result = remove_timezone("2024-01-15T10:30:45+05:00")
        assert result.tzinfo is None

    def test_remove_timezone_from_datetime(self):
        """Test removing timezone from datetime object."""

        dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        result = remove_timezone(dt)
        assert result.tzinfo is None
        assert result.year == 2024

    def test_remove_timezone_naive_datetime(self):
        """Test with naive datetime (no timezone)."""
        dt = datetime(2024, 1, 15, 10, 30, 45)
        result = remove_timezone(dt)
        assert result.tzinfo is None
        assert result == dt

    def test_remove_timezone_invalid_string(self):
        """Test with invalid string raises ValueError."""
        with pytest.raises(ValueError):
            remove_timezone("not-a-timestamp")

    def test_remove_timezone_invalid_type(self):
        """Test with invalid type raises TypeError."""
        with pytest.raises(TypeError):
            remove_timezone(12345)

    def test_remove_timezone_without_microseconds(self):
        """Test timestamp without microseconds."""
        result = remove_timezone("2024-01-15T10:30:45Z")
        assert result.year == 2024


class TestMediaInfoOffset:
    """Tests for MediaInfoOffset model."""

    def test_create_media_info_offset(self):
        """Test creating MediaInfoOffset."""
        info = MediaInfoOffset(start_offset=10, end_offset=60)
        assert info.type == "offset"
        assert info.start_offset == 10
        assert info.end_offset == 60

    def test_media_info_offset_defaults(self):
        """Test MediaInfoOffset with default values."""
        info = MediaInfoOffset()
        assert info.start_offset == 0
        assert info.end_offset == 4000000000

    def test_media_info_offset_float_conversion(self):
        """Test MediaInfoOffset converts floats to ints (ceil)."""
        info = MediaInfoOffset(start_offset=10.5, end_offset=59.1)
        assert info.start_offset == 11
        assert info.end_offset == 60

    def test_media_info_offset_none_to_default(self):
        """Test MediaInfoOffset converts None to default values."""
        info = MediaInfoOffset(start_offset=None, end_offset=None)
        assert info.start_offset == 0
        assert info.end_offset == 4000000000

    def test_media_info_offset_alias_start(self):
        """Test MediaInfoOffset with start_offset field."""
        # Note: Aliases work for field names in validation, not as kwargs
        # The model uses start_offset and end_offset as primary field names
        data = {"start_offset": 10, "end_offset": 60}
        info = MediaInfoOffset(**data)
        assert info.start_offset == 10
        assert info.end_offset == 60

    def test_media_info_offset_type_literal(self):
        """Test MediaInfoOffset type is always 'offset'."""
        info = MediaInfoOffset(start_offset=0, end_offset=100)
        assert info.type == "offset"

    def test_media_info_offset_large_values(self):
        """Test MediaInfoOffset with large offset values."""
        info = MediaInfoOffset(start_offset=0, end_offset=4000000000)
        assert info.end_offset == 4000000000

    def test_media_info_offset_forbid_extra(self):
        """Test MediaInfoOffset forbids extra fields."""
        with pytest.raises(Exception):  # Pydantic validation error
            MediaInfoOffset(start_offset=0, end_offset=100, extra_field="invalid")
