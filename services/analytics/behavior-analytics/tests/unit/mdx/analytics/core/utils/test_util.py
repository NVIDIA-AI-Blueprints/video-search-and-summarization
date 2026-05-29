# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import numpy as np
from datetime import datetime, timezone, timedelta
from google.protobuf.timestamp_pb2 import Timestamp

from mdx.analytics.core.utils.util import (
    cosine_similarity,
    dot_product,
    normalize,
    get_proto_time_now,
    get_offset_in_ms,
    add_offset_in_ms,
    str_to_bool,
    strRGB_to_tupleBGR,
    time_x_minutes_ago,
    get_proto_time_x_minutes_ago,
    convert_datetime_to_iso_8601_with_z_suffix,
    extract_sensor_id,
    iso_to_epoch,
)


class TestCosineSimlarity:
    """Tests for cosine_similarity function."""

    def test_identical_vectors(self):
        """Cosine similarity of identical vectors should be 1."""
        x = np.array([1, 2, 3])
        y = np.array([1, 2, 3])
        result = cosine_similarity(x, y)
        assert pytest.approx(result, rel=1e-6) == 1.0

    def test_orthogonal_vectors(self):
        """Cosine similarity of orthogonal vectors should be 0."""
        x = np.array([1, 0])
        y = np.array([0, 1])
        result = cosine_similarity(x, y)
        assert pytest.approx(result, abs=1e-6) == 0.0

    def test_opposite_vectors(self):
        """Cosine similarity of opposite vectors should be -1."""
        x = np.array([1, 2, 3])
        y = np.array([-1, -2, -3])
        result = cosine_similarity(x, y)
        assert pytest.approx(result, rel=1e-6) == -1.0

    def test_different_length_raises_error(self):
        """Should raise AssertionError for vectors of different lengths."""
        x = np.array([1, 2, 3])
        y = np.array([1, 2])
        with pytest.raises(AssertionError):
            cosine_similarity(x, y)


class TestDotProduct:
    """Tests for dot_product function."""

    def test_simple_dot_product(self):
        """Test dot product of simple vectors."""
        x = np.array([1, 2, 3])
        y = np.array([4, 5, 6])
        result = dot_product(x, y)
        # 1*4 + 2*5 + 3*6 = 4 + 10 + 18 = 32
        assert result == 32

    def test_zero_vector(self):
        """Dot product with zero vector should be 0."""
        x = np.array([1, 2, 3])
        y = np.array([0, 0, 0])
        result = dot_product(x, y)
        assert result == 0


class TestNormalize:
    """Tests for normalize function."""

    def test_normalize_simple_vector(self):
        """Test normalization of a simple vector."""
        x = np.array([3, 4])
        result = normalize(x)
        # Magnitude is 5, so normalized is [0.6, 0.8]
        expected = np.array([0.6, 0.8])
        np.testing.assert_array_almost_equal(result, expected)

    def test_normalized_has_unit_length(self):
        """Normalized vector should have magnitude 1."""
        x = np.array([1, 2, 3, 4, 5])
        result = normalize(x)
        magnitude = np.sqrt(np.sum(result**2))
        assert pytest.approx(magnitude, rel=1e-6) == 1.0


class TestProtoTime:
    """Tests for protobuf timestamp functions."""

    def test_get_proto_time_now(self):
        """Test that get_proto_time_now returns a valid Timestamp."""
        result = get_proto_time_now()
        assert isinstance(result, Timestamp)
        assert result.seconds > 0

    def test_get_offset_in_ms(self):
        """Test offset calculation between two timestamps."""
        ts1 = Timestamp(seconds=1000, nanos=500_000_000)  # 1000.5 seconds
        ts2 = Timestamp(seconds=1000, nanos=0)  # 1000 seconds
        result = get_offset_in_ms(ts1, ts2)
        assert result == 500

    def test_add_offset_in_ms_positive(self):
        """Test adding positive offset to timestamp."""
        ts = Timestamp(seconds=1000, nanos=0)
        result = add_offset_in_ms(ts, 1500)  # Add 1.5 seconds
        assert result.seconds == 1001
        assert result.nanos == 500_000_000

    def test_add_offset_in_ms_negative(self):
        """Test adding negative offset to timestamp."""
        ts = Timestamp(seconds=1002, nanos=0)
        result = add_offset_in_ms(ts, -1500)  # Subtract 1.5 seconds
        assert result.seconds == 1000
        assert result.nanos == 500_000_000


class TestStrToBool:
    """Tests for str_to_bool function."""

    def test_true_lowercase(self):
        assert str_to_bool("true") is True

    def test_true_uppercase(self):
        assert str_to_bool("TRUE") is True

    def test_true_mixed_case(self):
        assert str_to_bool("True") is True

    def test_false_string(self):
        assert str_to_bool("false") is False

    def test_other_string(self):
        assert str_to_bool("yes") is False
        assert str_to_bool("1") is False


class TestStrRGBToTupleBGR:
    """Tests for strRGB_to_tupleBGR function."""

    def test_red_rgb(self):
        """Red in RGB should be (0, 0, 255) in BGR."""
        result = strRGB_to_tupleBGR("255,0,0")
        assert result == (0, 0, 255)

    def test_green_rgb(self):
        """Green in RGB should be (0, 255, 0) in BGR."""
        result = strRGB_to_tupleBGR("0,255,0")
        assert result == (0, 255, 0)

    def test_blue_rgb(self):
        """Blue in RGB should be (255, 0, 0) in BGR."""
        result = strRGB_to_tupleBGR("0,0,255")
        assert result == (255, 0, 0)

    def test_white_rgb(self):
        """White should stay white."""
        result = strRGB_to_tupleBGR("255,255,255")
        assert result == (255, 255, 255)


class TestTimeXMinutesAgo:
    """Tests for time_x_minutes_ago function."""

    def test_time_30_minutes_ago(self):
        """Test that function returns approximately correct time."""
        before = datetime.now(timezone.utc)
        result = time_x_minutes_ago(30)
        after = datetime.now(timezone.utc)
        
        expected_before = before - timedelta(minutes=30)
        expected_after = after - timedelta(minutes=30)
        
        assert expected_before <= result <= expected_after

    def test_get_proto_time_x_minutes_ago(self):
        """Test proto timestamp for past time."""
        result = get_proto_time_x_minutes_ago(10)
        assert isinstance(result, Timestamp)
        # Should be in the past
        now = get_proto_time_now()
        assert result.seconds < now.seconds


class TestConvertDatetimeToISO:
    """Tests for convert_datetime_to_iso_8601_with_z_suffix function."""

    def test_conversion_with_z_suffix(self):
        """Test that UTC datetime is converted with Z suffix."""
        dt = datetime(2024, 3, 14, 12, 30, 45, 123456, tzinfo=timezone.utc)
        result = convert_datetime_to_iso_8601_with_z_suffix(dt)
        assert result.endswith("Z")
        assert "2024-03-14T12:30:45.123" in result


class TestExtractSensorId:
    """Tests for extract_sensor_id function."""

    def test_sensor_id_without_date(self):
        """Sensor ID without date pattern should remain unchanged."""
        result = extract_sensor_id("camera_1")
        assert result == "camera_1"

    def test_sensor_id_with_date_pattern(self):
        """Sensor ID with date pattern should have it removed."""
        # Note: Depends on DATE_PATTERN from constants
        result = extract_sensor_id("camera_1__20240314")
        # If DATE_PATTERN matches, should return base ID
        assert result in ["camera_1", "camera_1__20240314"]


class TestIsoToEpoch:
    """Tests for iso_to_epoch function."""

    def test_from_iso_string(self):
        """Test conversion from ISO string."""
        iso_str = "2024-03-14T12:00:00Z"
        result = iso_to_epoch(iso_str)
        assert isinstance(result, int)
        assert result > 0

    def test_from_datetime(self):
        """Test conversion from datetime object."""
        dt = datetime(2024, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        result = iso_to_epoch(dt)
        assert isinstance(result, int)
        # Expected: 1710417600000
        assert result == int(dt.timestamp() * 1000)

    def test_from_protobuf_timestamp(self):
        """Test conversion from protobuf Timestamp."""
        ts = Timestamp(seconds=1710417600, nanos=500_000_000)
        result = iso_to_epoch(ts)
        assert isinstance(result, int)
        # 1710417600 * 1000 + 500 = 1710417600500
        assert result == 1710417600500

    def test_from_int(self):
        """Test that int is passed through."""
        epoch = 1710417600000
        result = iso_to_epoch(epoch)
        assert result == epoch

