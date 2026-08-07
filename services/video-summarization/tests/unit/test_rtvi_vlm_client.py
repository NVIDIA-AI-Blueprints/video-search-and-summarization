# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Unit tests for RtviVlmClient.generate_captions_stream response cleanup.

Verifies that resp.close() is called on every exit path:
  - Normal termination ([DONE] received)
  - Stream ends without [DONE]
  - Non-200 HTTP status (error path)
  - Exception raised during line iteration
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rtvi_vlm_client import RtviVlmClient


def _make_client():
    """Create an RtviVlmClient bypassing __init__ health checks."""
    with patch.object(RtviVlmClient, "__init__", lambda self, *a, **k: None):
        client = RtviVlmClient(None)
    client._base_url = "http://fake-rtvi:8000"
    client._session = MagicMock()
    client._model_info = SimpleNamespace(id="test-model")
    return client


def _make_resp(status_code=200, lines=None):
    """Build a mock requests.Response with iter_lines() returning `lines`."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    if lines is not None:
        resp.iter_lines.return_value = iter(lines)
    return resp


def _drain(gen):
    """Exhaust a generator, collecting all yielded items."""
    return list(gen)


class TestGenerateCaptionsStreamResponseClose:
    """resp.close() must be called on every exit path."""

    def _run(self, client, resp):
        client._session.post.return_value = resp
        gen = client.generate_captions_stream(
            file_id="test-uuid",
            model="test-model",
            url="http://example.com/video.mp4",
        )
        try:
            _drain(gen)
        except Exception:
            pass
        return resp

    def test_close_called_after_done_marker(self):
        client = _make_client()
        chunk = json.dumps(
            {"id": "c1", "chunk_responses": [{"chunk_id": 0, "content": "hello"}]}
        )
        resp = _make_resp(lines=[f"data: {chunk}", "data: [DONE]"])
        self._run(client, resp)
        resp.close.assert_called_once()

    def test_close_called_when_stream_ends_without_done(self):
        client = _make_client()
        resp = _make_resp(lines=[])
        self._run(client, resp)
        resp.close.assert_called_once()

    def test_close_called_on_non_200_status(self):
        client = _make_client()
        resp = _make_resp(status_code=500)
        resp.text = json.dumps({"code": "InternalError", "message": "boom"})
        self._run(client, resp)
        resp.close.assert_called_once()

    def test_close_called_on_iteration_exception(self):
        client = _make_client()
        resp = _make_resp()

        def failing_lines():
            yield "data: {}"
            raise RuntimeError("stream interrupted")

        resp.iter_lines.return_value = failing_lines()
        self._run(client, resp)
        resp.close.assert_called_once()
