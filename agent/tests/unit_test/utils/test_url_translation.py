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
"""Unit tests for url_translation module."""

from vss_agents.utils.url_translation import translate_url


class TestTranslateUrl:
    """Test translate_url function."""

    def test_empty_url_returns_empty(self):
        result = translate_url("", "remote", "10.0.0.1", "1.2.3.4")
        assert result == ""

    def test_none_vlm_mode_returns_original(self):
        url = "http://10.0.0.1:8080/video.mp4"
        result = translate_url(url, None, "10.0.0.1", "1.2.3.4")
        assert result == url

    def test_empty_vlm_mode_returns_original(self):
        url = "http://10.0.0.1:8080/video.mp4"
        result = translate_url(url, "", "10.0.0.1", "1.2.3.4")
        assert result == url

    def test_missing_external_ip_returns_original(self):
        url = "http://10.0.0.1:8080/video.mp4"
        result = translate_url(url, "remote", "10.0.0.1", None)
        assert result == url

    def test_empty_external_ip_returns_original(self):
        url = "http://10.0.0.1:8080/video.mp4"
        result = translate_url(url, "remote", "10.0.0.1", "")
        assert result == url

    def test_missing_internal_ip_returns_original(self):
        url = "http://10.0.0.1:8080/video.mp4"
        result = translate_url(url, "remote", None, "1.2.3.4")
        assert result == url

    def test_empty_internal_ip_returns_original(self):
        url = "http://10.0.0.1:8080/video.mp4"
        result = translate_url(url, "remote", "", "1.2.3.4")
        assert result == url

    def test_same_ips_returns_original(self):
        url = "http://10.0.0.1:8080/video.mp4"
        result = translate_url(url, "remote", "10.0.0.1", "10.0.0.1")
        assert result == url

    def test_remote_mode_internal_to_external(self):
        url = "http://10.0.0.1:8080/video.mp4"
        result = translate_url(url, "remote", "10.0.0.1", "1.2.3.4")
        assert result == "http://1.2.3.4:8080/video.mp4"

    def test_remote_mode_no_match(self):
        url = "http://10.1.2.3:8080/video.mp4"
        result = translate_url(url, "remote", "10.0.0.1", "1.2.3.4")
        assert result == url

    def test_local_mode_external_to_internal(self):
        url = "http://1.2.3.4:8080/video.mp4"
        result = translate_url(url, "local", "10.0.0.1", "1.2.3.4")
        assert result == "http://10.0.0.1:8080/video.mp4"

    def test_local_shared_mode_external_to_internal(self):
        url = "http://1.2.3.4:8080/video.mp4"
        result = translate_url(url, "local_shared", "10.0.0.1", "1.2.3.4")
        assert result == "http://10.0.0.1:8080/video.mp4"

    def test_unknown_vlm_mode_returns_original(self):
        url = "http://10.0.0.1:8080/video.mp4"
        result = translate_url(url, "unknown_mode", "10.0.0.1", "1.2.3.4")
        assert result == url

    def test_url_without_netloc_returns_original(self):
        url = "/just/a/path"
        result = translate_url(url, "remote", "10.0.0.1", "1.2.3.4")
        assert result == url

    def test_case_insensitive_vlm_mode(self):
        url = "http://10.0.0.1:8080/video.mp4"
        result = translate_url(url, "REMOTE", "10.0.0.1", "1.2.3.4")
        assert result == "http://1.2.3.4:8080/video.mp4"

    def test_local_mode_no_match(self):
        url = "http://10.1.2.3:8080/video.mp4"
        result = translate_url(url, "local", "10.0.0.1", "1.2.3.4")
        assert result == url

    # --- Reverse proxy fallback tests ---
    # When behind a reverse proxy (e.g., Brev secure links with nginx),
    # the URL host is the proxy hostname, not a direct IP.

    def test_proxy_url_local_mode_with_vst_internal_url(self):
        """Local VLM behind proxy: replace proxy base with internal VST URL."""
        url = "https://7777-abc123.brevlab.com/vst/storage/temp_files/video.mp4"
        result = translate_url(url, "local_shared", "10.0.0.1", "1.2.3.4", "http://10.0.0.1:30888")
        assert result == "http://10.0.0.1:30888/vst/storage/temp_files/video.mp4"

    def test_proxy_url_local_mode_without_vst_internal_url(self):
        """Local VLM behind proxy without vst_internal_url: no translation (backwards compat)."""
        url = "https://7777-abc123.brevlab.com/vst/storage/temp_files/video.mp4"
        result = translate_url(url, "local_shared", "10.0.0.1", "1.2.3.4")
        assert result == url

    def test_proxy_url_remote_mode_no_fallback(self):
        """Remote VLM behind proxy: no proxy fallback (only local modes use it)."""
        url = "https://7777-abc123.brevlab.com/vst/storage/temp_files/video.mp4"
        result = translate_url(url, "remote", "10.0.0.1", "1.2.3.4", "http://10.0.0.1:30888")
        assert result == url

    def test_proxy_url_preserves_path_and_query(self):
        """Proxy fallback preserves full path and query string."""
        url = "https://proxy.example.com/vst/api/v1/replay/stream/123/picture?startTime=2025-01-01"
        result = translate_url(url, "local", "10.0.0.1", "1.2.3.4", "http://10.0.0.1:30888")
        assert result == "http://10.0.0.1:30888/vst/api/v1/replay/stream/123/picture?startTime=2025-01-01"

    def test_proxy_url_vst_internal_url_trailing_slash(self):
        """Trailing slash on vst_internal_url doesn't cause double-slash."""
        url = "https://proxy.example.com/vst/storage/video.mp4"
        result = translate_url(url, "local", "10.0.0.1", "1.2.3.4", "http://10.0.0.1:30888/")
        assert result == "http://10.0.0.1:30888/vst/storage/video.mp4"

    def test_ip_match_takes_priority_over_proxy_fallback(self):
        """When the IP matches, normal IP swap happens even if vst_internal_url is provided."""
        url = "http://1.2.3.4:30888/vst/storage/video.mp4"
        result = translate_url(url, "local", "10.0.0.1", "1.2.3.4", "http://10.0.0.1:30888")
        assert result == "http://10.0.0.1:30888/vst/storage/video.mp4"
