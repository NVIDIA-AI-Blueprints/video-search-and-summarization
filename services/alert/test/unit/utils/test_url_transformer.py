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

"""Unit tests for URL transformer utilities."""

import os
import pytest
from unittest.mock import patch

from utils.url_transformer import transform_video_url, is_vlm_local, rewrite_to_internal_url


class TestTransformVideoUrl:
    """Tests for transform_video_url function."""

    def test_no_transform_when_to_external_false(self):
        """URL should not change when to_external is False."""
        url = "http://localhost:8080/video.mp4"
        result = transform_video_url(url, to_external=False)
        assert result == url

    @patch.dict(os.environ, {"INTERNAL_IP": "localhost", "EXTERNAL_IP": "203.0.113.5"})
    def test_transform_internal_to_external(self):
        """URL should be transformed when to_external is True."""
        url = "http://localhost:8080/video.mp4"
        result = transform_video_url(url, to_external=True)
        assert result == "http://203.0.113.5:8080/video.mp4"

    @patch.dict(os.environ, {"INTERNAL_IP": "localhost", "EXTERNAL_IP": "203.0.113.5"})
    def test_no_transform_when_internal_ip_not_in_url(self):
        """URL should not change when INTERNAL_IP is not present in URL."""
        url = "http://198.51.100.10:8080/video.mp4"
        result = transform_video_url(url, to_external=True)
        assert result == url

    @patch.dict(os.environ, {"INTERNAL_IP": "", "EXTERNAL_IP": "203.0.113.5"})
    def test_no_transform_when_internal_ip_empty(self):
        """URL should not change when INTERNAL_IP is empty."""
        url = "http://localhost:8080/video.mp4"
        result = transform_video_url(url, to_external=True)
        assert result == url

    @patch.dict(os.environ, {"INTERNAL_IP": "localhost", "EXTERNAL_IP": ""})
    def test_no_transform_when_external_ip_empty(self):
        """URL should not change when EXTERNAL_IP is empty."""
        url = "http://localhost:8080/video.mp4"
        result = transform_video_url(url, to_external=True)
        assert result == url

    @patch.dict(os.environ, {}, clear=True)
    def test_no_transform_when_env_vars_not_set(self):
        """URL should not change when environment variables are not set."""
        # Clear any existing env vars
        os.environ.pop("INTERNAL_IP", None)
        os.environ.pop("EXTERNAL_IP", None)
        url = "http://localhost:8080/video.mp4"
        result = transform_video_url(url, to_external=True)
        assert result == url

    @patch.dict(os.environ, {"INTERNAL_IP": "localhost", "EXTERNAL_IP": "203.0.113.5"})
    def test_transform_multiple_occurrences(self):
        """All occurrences of INTERNAL_IP should be replaced."""
        url = "http://localhost:8080/api/localhost/video.mp4"
        result = transform_video_url(url, to_external=True)
        assert result == "http://203.0.113.5:8080/api/203.0.113.5/video.mp4"


class TestRewriteToInternalUrl:
    """Tests for rewrite_to_internal_url function."""

    @patch.dict(os.environ, {"VST_INTERNAL_URL": "http://vst-ingress:30888"})
    def test_public_origin_is_replaced(self):
        """A URL minted on the public origin becomes fetchable from inside."""
        url = "http://203.0.113.5:7777/vst/storage/temp_files/clip.mp4"
        result = rewrite_to_internal_url(url)
        assert result == "http://vst-ingress:30888/vst/storage/temp_files/clip.mp4"

    @patch.dict(os.environ, {"VST_INTERNAL_URL": "http://vst-ingress:30888"})
    def test_https_public_origin_is_replaced(self):
        """Scheme is taken from the internal URL, not carried over.

        This is the case the substitution in transform_video_url cannot serve:
        TLS terminating upstream means neither the host nor the scheme is usable
        from in-network, and there is no INTERNAL_IP substring to swap.
        """
        url = "https://vss.example.com/vst/storage/temp_files/clip.mp4"
        result = rewrite_to_internal_url(url)
        assert result == "http://vst-ingress:30888/vst/storage/temp_files/clip.mp4"

    @patch.dict(os.environ, {"VST_INTERNAL_URL": "http://vst-ingress:30888"})
    def test_query_and_path_are_preserved(self):
        """Only scheme and authority change."""
        url = "https://vss.example.com/vst/storage/f.mp4?expiry=600&overlay=true"
        result = rewrite_to_internal_url(url)
        assert result == "http://vst-ingress:30888/vst/storage/f.mp4?expiry=600&overlay=true"

    @patch.dict(os.environ, {"VST_INTERNAL_URL": "http://vst-ingress:30888/"})
    def test_trailing_slash_on_internal_url(self):
        """A configured internal URL with a trailing slash does not double it."""
        url = "http://203.0.113.5:7777/vst/storage/clip.mp4"
        result = rewrite_to_internal_url(url)
        assert result == "http://vst-ingress:30888/vst/storage/clip.mp4"

    @patch.dict(os.environ, {"VST_INTERNAL_URL": "http://vst-ingress:30888"})
    def test_already_internal_is_idempotent(self):
        """Rewriting a URL that is already internal changes nothing."""
        url = "http://vst-ingress:30888/vst/storage/clip.mp4"
        assert rewrite_to_internal_url(url) == url

    @patch.dict(os.environ, {"VST_INTERNAL_URL": "http://vst-ingress:30888"})
    def test_non_vst_path_is_left_alone(self):
        """VST does not serve it, so pointing it at VST would break the fetch."""
        url = "http://203.0.113.5:7777/other/thing.mp4"
        assert rewrite_to_internal_url(url) == url

    @patch.dict(os.environ, {"VST_INTERNAL_URL": "http://vst-ingress:30888"})
    def test_relative_url_is_left_alone(self):
        """No authority to replace, and no basis for choosing one."""
        url = "/vst/storage/clip.mp4"
        assert rewrite_to_internal_url(url) == url

    @patch.dict(os.environ, {}, clear=True)
    def test_unconfigured_internal_url_is_a_no_op(self):
        """Without VST_INTERNAL_URL there is nothing to re-anchor on."""
        os.environ.pop("VST_INTERNAL_URL", None)
        url = "http://203.0.113.5:7777/vst/storage/clip.mp4"
        assert rewrite_to_internal_url(url) == url

    @patch.dict(os.environ, {"VST_INTERNAL_URL": "http://vst-ingress:30888"})
    def test_empty_url(self):
        """An empty URL stays empty rather than becoming a bare origin."""
        assert rewrite_to_internal_url("") == ""


class TestIsVlmLocal:
    """Tests for is_vlm_local function."""

    @patch.dict(os.environ, {"VLM_MODE": "local"})
    def test_local_mode_returns_true(self):
        """VLM_MODE=local should return True."""
        assert is_vlm_local() is True

    @patch.dict(os.environ, {"VLM_MODE": "local_shared"})
    def test_local_shared_mode_returns_true(self):
        """VLM_MODE=local_shared should return True."""
        assert is_vlm_local() is True

    @patch.dict(os.environ, {"VLM_MODE": "LOCAL"})
    def test_local_mode_case_insensitive(self):
        """VLM_MODE comparison should be case insensitive."""
        assert is_vlm_local() is True

    @patch.dict(os.environ, {"VLM_MODE": "remote"})
    def test_remote_mode_returns_false(self):
        """VLM_MODE=remote should return False."""
        assert is_vlm_local() is False

    @patch.dict(os.environ, {"VLM_MODE": ""})
    def test_empty_mode_returns_false(self):
        """Empty VLM_MODE should return False (default to remote)."""
        assert is_vlm_local() is False

    @patch.dict(os.environ, {}, clear=True)
    def test_unset_mode_returns_false(self):
        """Unset VLM_MODE should return False (default to remote)."""
        os.environ.pop("VLM_MODE", None)
        assert is_vlm_local() is False


class TestIntegrationScenarios:
    """Integration tests for typical usage scenarios."""

    @patch.dict(os.environ, {
        "INTERNAL_IP": "localhost",
        "EXTERNAL_IP": "203.0.113.5",
        "VLM_MODE": "local"
    })
    def test_local_vlm_mode_scenario(self):
        """In local mode: VLM gets internal URL, ES gets external URL."""
        video_url = "http://localhost:8080/video.mp4"

        # VLM should get internal URL (no transform)
        vlm_url = transform_video_url(video_url, to_external=not is_vlm_local())
        assert vlm_url == video_url

        # ES/storage should get external URL
        storage_url = transform_video_url(video_url, to_external=True)
        assert storage_url == "http://203.0.113.5:8080/video.mp4"

    @patch.dict(os.environ, {
        "INTERNAL_IP": "localhost",
        "EXTERNAL_IP": "203.0.113.5",
        "VLM_MODE": "local_shared"
    })
    def test_local_shared_vlm_mode_scenario(self):
        """In local_shared mode: VLM gets internal URL, ES gets external URL."""
        video_url = "http://localhost:8080/video.mp4"

        # VLM should get internal URL (no transform)
        vlm_url = transform_video_url(video_url, to_external=not is_vlm_local())
        assert vlm_url == video_url

        # ES/storage should get external URL
        storage_url = transform_video_url(video_url, to_external=True)
        assert storage_url == "http://203.0.113.5:8080/video.mp4"

    @patch.dict(os.environ, {
        "INTERNAL_IP": "localhost",
        "EXTERNAL_IP": "203.0.113.5",
        "VLM_MODE": "remote"
    })
    def test_remote_vlm_mode_scenario(self):
        """In remote mode: both VLM and ES get external URL."""
        video_url = "http://localhost:8080/video.mp4"

        # VLM should get external URL
        vlm_url = transform_video_url(video_url, to_external=not is_vlm_local())
        assert vlm_url == "http://203.0.113.5:8080/video.mp4"

        # ES/storage should also get external URL
        storage_url = transform_video_url(video_url, to_external=True)
        assert storage_url == "http://203.0.113.5:8080/video.mp4"
