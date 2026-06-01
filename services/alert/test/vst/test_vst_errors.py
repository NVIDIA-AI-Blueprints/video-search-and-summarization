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

"""Unit tests for VST error classification in get_video_stream_url().

Tests verify that the VST handler raises typed exceptions with correct
status_code, response_body, and category for different HTTP failure modes.
"""

import json
from unittest.mock import patch, MagicMock

import pytest
import requests

from vst.its_vst_handler import ITS_VST_HANDLER
from vst.exceptions import (
    VSTError,
    VSTClientError,
    VSTOverloadedError,
    VSTRecordingNotFoundError,
    VSTTimeoutError,
    VSTUnavailableError,
)


def _make_config(**overrides):
    cfg = {
        "vst_config": {
            "base_url": "http://vst-test:30011",
            "sensor_list_endpoint": "/vst/api/v1/sensor/streams",
            "segment_duration_seconds": 10,
            "timeout": 5,
            "url_retention_minutes": 1440,
            "storage": {"base_url": "http://vst-test:30011"},
        }
    }
    cfg["vst_config"].update(overrides)
    return cfg


def _build_handler(**overrides):
    return ITS_VST_HANDLER(_make_config(**overrides))


def _mock_http_error(status_code, body=""):
    """Create a requests.exceptions.HTTPError with a mock response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = body
    mock_response.content = body.encode() if body else b""
    mock_response.url = "http://vst-test:30011/vst/api/v1/storage/file/cam1/url"
    error = requests.exceptions.HTTPError(response=mock_response)
    return error


class TestVSTErrorClassification:
    """Tests that get_video_stream_url raises the correct typed exception."""

    @patch.object(ITS_VST_HANDLER, '_get_stream_id_from_name', return_value='cam1')
    @patch('vst.its_vst_handler.requests.get')
    def test_404_raises_recording_not_found(self, mock_get, mock_stream_id):
        handler = _build_handler()
        body = '{"error":"no recording found"}'
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = body
        mock_response.raise_for_status.side_effect = _mock_http_error(404, body)
        mock_get.return_value = mock_response

        with pytest.raises(VSTRecordingNotFoundError) as exc_info:
            handler.get_video_stream_url("cam1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")

        assert exc_info.value.status_code == 404
        assert exc_info.value.response_body == body
        assert exc_info.value.category == "recording_not_found"

    @patch.object(ITS_VST_HANDLER, '_get_stream_id_from_name', return_value='cam1')
    @patch('vst.its_vst_handler.requests.get')
    def test_429_raises_overloaded(self, mock_get, mock_stream_id):
        handler = _build_handler()
        body = '{"error":"rate limit exceeded","retryAfter":5}'
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = body
        mock_response.raise_for_status.side_effect = _mock_http_error(429, body)
        mock_get.return_value = mock_response

        with pytest.raises(VSTOverloadedError) as exc_info:
            handler.get_video_stream_url("cam1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")

        assert exc_info.value.status_code == 429
        assert exc_info.value.response_body == body
        assert exc_info.value.category == "overloaded"

    @patch.object(ITS_VST_HANDLER, '_get_stream_id_from_name', return_value='cam1')
    @patch('vst.its_vst_handler.requests.get')
    def test_503_raises_overloaded(self, mock_get, mock_stream_id):
        handler = _build_handler()
        body = '{"error":"service temporarily unavailable"}'
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = body
        mock_response.raise_for_status.side_effect = _mock_http_error(503, body)
        mock_get.return_value = mock_response

        with pytest.raises(VSTOverloadedError) as exc_info:
            handler.get_video_stream_url("cam1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")

        assert exc_info.value.status_code == 503
        assert exc_info.value.category == "overloaded"

    @patch.object(ITS_VST_HANDLER, '_get_stream_id_from_name', return_value='cam1')
    @patch('vst.its_vst_handler.requests.get')
    def test_500_raises_unavailable(self, mock_get, mock_stream_id):
        handler = _build_handler()
        body = '{"error":"internal server error"}'
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = body
        mock_response.raise_for_status.side_effect = _mock_http_error(500, body)
        mock_get.return_value = mock_response

        with pytest.raises(VSTUnavailableError) as exc_info:
            handler.get_video_stream_url("cam1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")

        assert exc_info.value.status_code == 500
        assert exc_info.value.category == "server_error"

    @patch.object(ITS_VST_HANDLER, '_get_stream_id_from_name', return_value='cam1')
    @patch('vst.its_vst_handler.requests.get')
    def test_400_raises_client_error(self, mock_get, mock_stream_id):
        handler = _build_handler()
        body = '{"error":"bad request"}'
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = body
        mock_response.raise_for_status.side_effect = _mock_http_error(400, body)
        mock_get.return_value = mock_response

        with pytest.raises(VSTClientError) as exc_info:
            handler.get_video_stream_url("cam1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")

        assert exc_info.value.status_code == 400
        assert exc_info.value.category == "client_error"

    @patch.object(ITS_VST_HANDLER, '_get_stream_id_from_name', return_value='cam1')
    @patch('vst.its_vst_handler.requests.get')
    def test_timeout_raises_vst_timeout(self, mock_get, mock_stream_id):
        handler = _build_handler()
        mock_get.side_effect = requests.exceptions.Timeout("Read timed out")

        with pytest.raises(VSTTimeoutError) as exc_info:
            handler.get_video_stream_url("cam1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")

        assert exc_info.value.category == "timeout"
        assert "timed out" in str(exc_info.value)

    @patch.object(ITS_VST_HANDLER, '_get_stream_id_from_name', return_value='cam1')
    @patch('vst.its_vst_handler.requests.get')
    def test_connection_error_raises_unavailable(self, mock_get, mock_stream_id):
        handler = _build_handler()
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        with pytest.raises(VSTUnavailableError) as exc_info:
            handler.get_video_stream_url("cam1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")

        assert exc_info.value.category == "connection_failed"

    @patch.object(ITS_VST_HANDLER, '_get_stream_id_from_name', return_value='cam1')
    @patch('vst.its_vst_handler.requests.get')
    def test_200_missing_video_url_raises_vst_error(self, mock_get, mock_stream_id):
        """HTTP 200 but no videoUrl in body — could be malformed response, not necessarily 'not found'."""
        handler = _build_handler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"streamId":"cam1"}'
        mock_response.content = b'{"streamId":"cam1"}'
        mock_response.url = "http://vst-test:30011/vst/api/v1/storage/file/cam1/url"
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"streamId": "cam1"}
        mock_get.return_value = mock_response

        with pytest.raises(VSTError) as exc_info:
            handler.get_video_stream_url("cam1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")

        assert exc_info.value.category == "missing_video_url"
        assert exc_info.value.status_code == 200
        assert '{"streamId":"cam1"}' in exc_info.value.response_body


class TestVSTExceptionAttributes:
    """Tests that VST exception attributes are preserved (full, not truncated)."""

    def test_response_body_preserved_full(self):
        long_body = "x" * 10000
        err = VSTOverloadedError(
            "VST overloaded",
            status_code=503,
            response_body=long_body,
            category="overloaded",
        )
        assert err.response_body == long_body
        assert len(err.response_body) == 10000

    def test_status_code_preserved(self):
        err = VSTClientError("bad request", status_code=400, category="client_error")
        assert err.status_code == 400

    def test_category_preserved(self):
        err = VSTTimeoutError("timeout", category="timeout")
        assert err.category == "timeout"

    def test_inheritance(self):
        err = VSTRecordingNotFoundError("not found")
        assert isinstance(err, VSTError)
        assert isinstance(err, Exception)


def _classify_vst_failure(vst_error):
    """Standalone copy of AnomalyEnhancer._classify_vst_failure for testing
    without importing the full enhance_alert_with_vlm module (heavy deps).
    """
    if vst_error is None:
        return 404, "No video recording found for the requested time"
    if isinstance(vst_error, VSTRecordingNotFoundError):
        return 404, "No video recording found for the requested time"
    if isinstance(vst_error, VSTOverloadedError):
        return vst_error.status_code or 503, "VST service overloaded"
    if isinstance(vst_error, VSTTimeoutError):
        return 504, "VST request timed out"
    if isinstance(vst_error, VSTUnavailableError):
        return vst_error.status_code or 503, "VST service unavailable"
    if isinstance(vst_error, VSTClientError):
        return vst_error.status_code or 400, f"VST request error (HTTP {vst_error.status_code})"
    if isinstance(vst_error, VSTError):
        if vst_error.category == "missing_video_url":
            return 502, "VST returned response without video URL"
        return vst_error.status_code or 500, "VST error: could not retrieve video"
    return 500, "VST error: could not retrieve video"


class TestClassifyVstFailure:
    """Tests for VST failure classification logic (mirrors AnomalyEnhancer._classify_vst_failure)."""

    def _classify(self, error):
        return _classify_vst_failure(error)

    def test_none_returns_404(self):
        code, status = self._classify(None)
        assert code == 404
        assert "No video recording found" in status

    def test_recording_not_found(self):
        err = VSTRecordingNotFoundError("not found", status_code=404, category="recording_not_found")
        code, status = self._classify(err)
        assert code == 404
        assert "No video recording found" in status

    def test_overloaded_429(self):
        err = VSTOverloadedError("rate limited", status_code=429, category="overloaded")
        code, status = self._classify(err)
        assert code == 429
        assert "overloaded" in status.lower()

    def test_overloaded_503(self):
        err = VSTOverloadedError("unavailable", status_code=503, category="overloaded")
        code, status = self._classify(err)
        assert code == 503
        assert "overloaded" in status.lower()

    def test_timeout(self):
        err = VSTTimeoutError("timed out", category="timeout")
        code, status = self._classify(err)
        assert code == 504
        assert "timed out" in status.lower()

    def test_unavailable(self):
        err = VSTUnavailableError("down", status_code=502, category="server_error")
        code, status = self._classify(err)
        assert code == 502
        assert "unavailable" in status.lower()

    def test_client_error(self):
        err = VSTClientError("bad request", status_code=400, category="client_error")
        code, status = self._classify(err)
        assert code == 400
        assert "400" in status

    def test_missing_video_url_returns_502(self):
        err = VSTError("videoUrl missing", status_code=200, category="missing_video_url")
        code, status = self._classify(err)
        assert code == 502
        assert "without video URL" in status

    def test_generic_vst_error(self):
        err = VSTError("something unexpected", category="unexpected")
        code, status = self._classify(err)
        assert code == 500
        assert "could not retrieve video" in status.lower()
