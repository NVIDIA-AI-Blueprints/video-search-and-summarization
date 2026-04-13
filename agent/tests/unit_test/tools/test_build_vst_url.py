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

"""Unit tests for build_vst_url."""

import pytest

from vss_agents.tools.vst.utils import build_vst_url


class TestBuildVstUrl:
    """Tests for the build_vst_url helper."""

    def test_replaces_scheme_and_host(self):
        result = build_vst_url(
            "http://10.0.1.1:30888",
            "http://232.2.2.34:22324/vst/api/v1/storage/file.mp4",
        )
        assert result == "http://10.0.1.1:30888/vst/api/v1/storage/file.mp4"

    def test_preserves_path_query_fragment(self):
        result = build_vst_url(
            "http://10.0.1.1:30888",
            "https://proxy.example.com:443/vst/api/v1/clip?start=0&end=10#section",
        )
        assert result == "http://10.0.1.1:30888/vst/api/v1/clip?start=0&end=10#section"

    def test_https_base_url(self):
        result = build_vst_url(
            "https://internal:8443",
            "http://external:9999/vst/storage/file.mp4",
        )
        assert result == "https://internal:8443/vst/storage/file.mp4"

    def test_base_url_trailing_slash(self):
        result = build_vst_url(
            "http://10.0.1.1:30888/",
            "http://other:1234/vst/api/v1/resource",
        )
        assert result == "http://10.0.1.1:30888/vst/api/v1/resource"

    def test_no_path(self):
        result = build_vst_url(
            "http://10.0.1.1:30888",
            "http://external:9999",
        )
        assert result == "http://10.0.1.1:30888"

    def test_root_path(self):
        result = build_vst_url(
            "http://10.0.1.1:30888",
            "http://external:9999/",
        )
        assert result == "http://10.0.1.1:30888/"

    def test_same_host(self):
        result = build_vst_url(
            "http://10.0.1.1:30888",
            "http://10.0.1.1:30888/vst/api/v1/storage/file.mp4",
        )
        assert result == "http://10.0.1.1:30888/vst/api/v1/storage/file.mp4"

    @pytest.mark.parametrize(
        "base,url,expected",
        [
            (
                "http://localhost:30888",
                "http://1.2.3.4:30888/vst/api/v1/clip",
                "http://localhost:30888/vst/api/v1/clip",
            ),
            (
                "http://10.0.0.5:30888",
                "https://brev-proxy.example.com/vst/storage/video.mp4",
                "http://10.0.0.5:30888/vst/storage/video.mp4",
            ),
        ],
    )
    def test_parametrized(self, base, url, expected):
        assert build_vst_url(base, url) == expected
