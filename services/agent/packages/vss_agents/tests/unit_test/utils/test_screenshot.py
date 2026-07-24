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
"""Unit tests for build_screenshot_url."""

from vss_agents.tools.vst.snapshot import build_screenshot_url


class TestBuildScreenshotUrl:
    """Test build_screenshot_url function."""

    def test_build_screenshot_url(self):
        result = build_screenshot_url("http://vst-external:8080", "stream1", "2025-01-01T00:00:00Z")
        assert (
            result == "http://vst-external:8080/vst/api/v1/replay/stream/stream1/picture?startTime=2025-01-01T00:00:00Z"
        )

    def test_build_screenshot_url_different_params(self):
        result = build_screenshot_url("https://vst.example.com", "abc-123", "2025-06-15T14:30:00Z")
        assert (
            result == "https://vst.example.com/vst/api/v1/replay/stream/abc-123/picture?startTime=2025-06-15T14:30:00Z"
        )

    def test_build_screenshot_url_always_returns_string(self):
        """Build function always returns a non-empty string (no validation)."""
        result = build_screenshot_url("http://host", "id", "ts")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_screenshot_url_strips_trailing_slash(self):
        """Trailing slash on vst_external_url is stripped to avoid double slashes."""
        result = build_screenshot_url("http://vst-external:8080/", "stream1", "2025-01-01T00:00:00Z")
        assert "//" not in result.split("://", 1)[1]

    def test_build_screenshot_url_empty_stream_id(self):
        """Empty stream_id produces a URL with empty segment (caller should guard)."""
        result = build_screenshot_url("http://host", "", "ts")
        assert "/stream//picture" in result

    def test_build_screenshot_url_empty_timestamp(self):
        """Empty timestamp produces a URL with empty startTime param."""
        result = build_screenshot_url("http://host", "s1", "")
        assert result.endswith("startTime=")
