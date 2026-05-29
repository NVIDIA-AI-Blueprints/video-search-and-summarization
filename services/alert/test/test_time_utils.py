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

"""Unit tests for ``utils.time_utils``.

Covers the shared ISO-8601 helpers extracted from
``enhance_alert_with_vlm.py::_iso_delta`` / ``set_max_frames``. These helpers
are called from the prometheus recorder and from the frame-count heuristic,
so correctness here underpins both latency metrics and VLM request shaping.

Run with: pytest test/test_time_utils.py -v
"""

from datetime import datetime, timezone

import pytest

from utils.time_utils import iso_delta_seconds, parse_iso_utc


class TestParseIsoUtc:
    def test_parses_z_suffix(self):
        dt = parse_iso_utc("2025-01-02T03:04:05Z")
        assert dt == datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def test_parses_plus_offset(self):
        dt = parse_iso_utc("2025-01-02T03:04:05+00:00")
        assert dt == datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def test_parses_fractional_seconds(self):
        dt = parse_iso_utc("2025-01-02T03:04:05.123456Z")
        assert dt == datetime(2025, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)

    def test_parses_non_utc_offset(self):
        dt = parse_iso_utc("2025-01-02T03:04:05-05:00")
        assert dt.utcoffset().total_seconds() == -5 * 3600

    def test_malformed_input_raises_valueerror(self):
        with pytest.raises(ValueError):
            parse_iso_utc("not-a-timestamp")

    def test_non_string_input_raises_typeerror_or_attributeerror(self):
        # Calling .replace() on a non-string object triggers AttributeError.
        # We intentionally let this propagate rather than swallowing it, so
        # callers passing the wrong type see their bug.
        with pytest.raises((AttributeError, TypeError)):
            parse_iso_utc(12345)  # type: ignore[arg-type]


class TestIsoDeltaSeconds:
    def test_successful_positive_delta(self):
        start = "2025-01-02T03:04:05Z"
        end = "2025-01-02T03:04:15Z"
        assert iso_delta_seconds(start, end) == pytest.approx(10.0)

    def test_fractional_delta(self):
        start = "2025-01-02T03:04:05.000Z"
        end = "2025-01-02T03:04:05.500Z"
        assert iso_delta_seconds(start, end) == pytest.approx(0.5)

    def test_none_start_returns_none(self):
        assert iso_delta_seconds(None, "2025-01-02T03:04:05Z") is None

    def test_none_end_returns_none(self):
        assert iso_delta_seconds("2025-01-02T03:04:05Z", None) is None

    def test_empty_string_returns_none(self):
        assert iso_delta_seconds("", "2025-01-02T03:04:05Z") is None
        assert iso_delta_seconds("2025-01-02T03:04:05Z", "") is None

    def test_malformed_input_returns_none(self):
        # ValueError from fromisoformat is swallowed — this is a metric
        # observation helper and a bad timestamp should not crash the
        # pipeline, just silently skip the observation.
        assert iso_delta_seconds("junk", "2025-01-02T03:04:05Z") is None
        assert iso_delta_seconds("2025-01-02T03:04:05Z", "junk") is None

    def test_negative_delta_returns_none(self):
        # Clock skew guard: end-before-start is almost always wall-clock
        # drift, not a real metric we want to record.
        start = "2025-01-02T03:04:15Z"
        end = "2025-01-02T03:04:05Z"
        assert iso_delta_seconds(start, end) is None

    def test_mixed_timezone_inputs(self):
        # Same instant expressed two ways — delta must be zero.
        start = "2025-01-02T03:04:05Z"
        end = "2025-01-02T03:04:05+00:00"
        assert iso_delta_seconds(start, end) == pytest.approx(0.0)

    def test_non_string_input_propagates(self):
        # Deliberate: non-string input is a programmer bug. The caller
        # handed in the wrong shape and we want that exception to surface.
        with pytest.raises((AttributeError, TypeError)):
            iso_delta_seconds(12345, "2025-01-02T03:04:05Z")  # type: ignore[arg-type]
