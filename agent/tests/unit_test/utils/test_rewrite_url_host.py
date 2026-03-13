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

"""Unit tests for rewrite_url_host."""

import pytest
from vss_agents.utils.url_translation import rewrite_url_host


class TestRewriteUrlHost:
    """Tests for the rewrite_url_host helper."""

    # --- Direct-IP cases (explicit port) ---

    def test_replaces_host_keeps_port(self):
        result = rewrite_url_host(
            "http://232.2.2.34:22324/vst/api/v1/storage/file.mp4",
            "10.0.1.1",
        )
        assert result == "http://10.0.1.1:22324/vst/api/v1/storage/file.mp4"

    def test_preserves_scheme(self):
        result = rewrite_url_host(
            "https://proxy.example.com:443/vst/api/v1/clip?start=0&end=10#section",
            "10.0.1.1",
        )
        assert result == "https://10.0.1.1:443/vst/api/v1/clip?start=0&end=10#section"

    def test_preserves_query_and_fragment(self):
        result = rewrite_url_host(
            "http://1.2.3.4:30888/vst/api?key=val#frag",
            "10.0.0.5",
        )
        assert result == "http://10.0.0.5:30888/vst/api?key=val#frag"

    def test_no_path(self):
        result = rewrite_url_host("http://external:9999", "10.0.1.1")
        assert result == "http://10.0.1.1:9999"

    def test_root_path(self):
        result = rewrite_url_host("http://external:9999/", "10.0.1.1")
        assert result == "http://10.0.1.1:9999/"

    def test_same_host_with_port(self):
        result = rewrite_url_host(
            "http://10.0.1.1:30888/vst/api/v1/storage/file.mp4",
            "10.0.1.1",
        )
        assert result == "http://10.0.1.1:30888/vst/api/v1/storage/file.mp4"

    @pytest.mark.parametrize(
        "url,target_ip,expected",
        [
            (
                "http://1.2.3.4:30888/vst/api/v1/clip",
                "localhost",
                "http://localhost:30888/vst/api/v1/clip",
            ),
            (
                "https://brev-proxy.example.com:8443/vst/storage/video.mp4",
                "10.0.0.5",
                "https://10.0.0.5:8443/vst/storage/video.mp4",
            ),
        ],
    )
    def test_parametrized(self, url, target_ip, expected):
        assert rewrite_url_host(url, target_ip) == expected

    # --- Already target IP, no port ---

    def test_already_target_ip_no_port_returns_unchanged(self):
        url = "http://10.0.1.1/vst/storage/video.mp4"
        assert rewrite_url_host(url, "10.0.1.1") == url

    # --- Proxy cases (no explicit port, host != target_ip) ---

    def test_proxy_vst_url_rewrites_to_port_30888(self):
        url = "https://7777-abc123.brevlab.com/vst/storage/temp_files/video.mp4"
        result = rewrite_url_host(url, "10.0.0.1")
        assert result == "http://10.0.0.1:30888/vst/storage/temp_files/video.mp4"

    def test_proxy_static_url_rewrites_to_port_8000(self):
        url = "https://7777-abc123.brevlab.com/static/vss_report_20260310.pdf"
        result = rewrite_url_host(url, "10.0.0.1")
        assert result == "http://10.0.0.1:8000/static/vss_report_20260310.pdf"

    def test_proxy_api_url_rewrites_to_port_8000(self):
        url = "https://7777-abc123.brevlab.com/api/v1/videos"
        result = rewrite_url_host(url, "10.0.0.1")
        assert result == "http://10.0.0.1:8000/api/v1/videos"

    def test_proxy_health_url_rewrites_to_port_8000(self):
        url = "https://7777-abc123.brevlab.com/health"
        result = rewrite_url_host(url, "10.0.0.1")
        assert result == "http://10.0.0.1:8000/health"

    def test_proxy_incidents_url_rewrites_to_port_8081(self):
        url = "https://7777-abc123.brevlab.com/incidents"
        result = rewrite_url_host(url, "10.0.0.1")
        assert result == "http://10.0.0.1:8081/incidents"

    def test_proxy_unknown_path_uses_default_port(self):
        """Unknown path prefix falls back to agent port 8000."""
        url = "https://7777-abc123.brevlab.com/unknown/path"
        result = rewrite_url_host(url, "10.0.0.1")
        assert result == "http://10.0.0.1:8000/unknown/path"

    def test_proxy_preserves_path_query_fragment(self):
        url = "https://proxy.example.com/vst/api/v1/replay/stream/123?startTime=2025-01-01#section"
        result = rewrite_url_host(url, "10.0.0.1")
        assert result == "http://10.0.0.1:30888/vst/api/v1/replay/stream/123?startTime=2025-01-01#section"
