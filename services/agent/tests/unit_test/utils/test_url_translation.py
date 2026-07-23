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

from vss_agents.utils.url_translation import rewrite_to_internal_vst_url


class TestRewriteToInternalVstUrl:
    """Test direct VST internal URL rewriting."""

    def test_rewrites_proxy_vst_url(self):
        url = "https://7777-abc123.brevlab.com/vst/storage/temp_files/video.mp4"
        result = rewrite_to_internal_vst_url(url, "http://10.0.0.1:30888")
        assert result == "http://10.0.0.1:30888/vst/storage/temp_files/video.mp4"

    def test_preserves_query_and_fragment(self):
        url = "https://proxy.example.com/vst/storage/video.mp4?token=abc#clip"
        result = rewrite_to_internal_vst_url(url, "http://10.0.0.1:30888/")
        assert result == "http://10.0.0.1:30888/vst/storage/video.mp4?token=abc#clip"

    def test_missing_internal_url_returns_original(self):
        url = "https://proxy.example.com/vst/storage/video.mp4"
        result = rewrite_to_internal_vst_url(url, None)
        assert result == url

    def test_non_vst_path_returns_original(self):
        url = "https://proxy.example.com/api/v1/health"
        result = rewrite_to_internal_vst_url(url, "http://10.0.0.1:30888")
        assert result == url
