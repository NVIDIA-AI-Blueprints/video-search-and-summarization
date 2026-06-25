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

"""
Unit tests for VLMClient timeout configuration.

1. Verify default timeout (5s) is passed to OpenAI client
2. Verify a slow VLM server actually triggers APITimeoutError

Run with: pytest test/vlm/test_vlm_client_timeout.py -v
"""

import json
import socket
import threading
import time
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch

import pytest
from openai import APITimeoutError


def _get_free_port():
    """Find an available port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _SlowHandler(BaseHTTPRequestHandler):
    """HTTP handler that sleeps 30s before responding – simulates a hanging VLM."""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(content_length)

        # Sleep longer than any reasonable timeout
        time.sleep(30)

        body = json.dumps({
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # silence logs


@pytest.fixture()
def slow_vlm_server():
    """Start a local HTTP server that sleeps 30s before replying."""
    port = _get_free_port()
    server = HTTPServer(("127.0.0.1", port), _SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/v1"
    server.shutdown()


class TestVLMClientTimeout:

    @patch("vlm.vlm_client.OpenAI")
    def test_default_timeout_is_5_seconds(self, mock_openai_cls):
        """Default request_timeout (5s) must be forwarded to OpenAI(timeout=5)."""
        from vlm.vlm_client import VLMClient

        VLMClient({
            "base_url": "http://localhost:8080/v1",
            "model": "nvidia/cosmos-reason1-7b",
        })

        mock_openai_cls.assert_called_once_with(
            base_url="http://localhost:8080/v1",
            api_key="not-used",
            timeout=5,
        )

    def test_slow_server_triggers_timeout(self, slow_vlm_server):
        """
        Server sleeps 30s, client timeout is 3s.
        Must raise APITimeoutError, not block for 30s.

        Note: OpenAI SDK retries twice by default (3 attempts × 3s ≈ 9-12s),
        so we assert < 20s to prove the timeout fires instead of waiting 30s.
        """
        from vlm.vlm_client import VLMClient

        vlm = VLMClient({
            "base_url": slow_vlm_server,
            "model": "test-model",
            "max_tokens": 64,
            "request_timeout": 3,
        })

        start = time.monotonic()
        with pytest.raises(APITimeoutError):
            vlm.analyze_image_url("http://example.com/img.jpg", "Describe this image")
        elapsed = time.monotonic() - start

        # Without timeout the request would block 30s.
        # With timeout=3s + SDK retries (2 retries) it should finish in ~9-12s.
        assert elapsed < 20, f"Timeout not working – request took {elapsed:.1f}s (server sleeps 30s)"


class TestAsyncVLMClientTimeout:

    @patch("vlm.vlm_client.AsyncOpenAI")
    def test_default_timeout_is_5_seconds(self, mock_async_openai_cls):
        """Default request_timeout (5s) must be forwarded to AsyncOpenAI(timeout=5)."""
        from vlm.vlm_client import AsyncVLMClient

        AsyncVLMClient({
            "base_url": "http://localhost:8080/v1",
            "model": "nvidia/cosmos-reason1-7b",
        })

        mock_async_openai_cls.assert_called_once_with(
            base_url="http://localhost:8080/v1",
            api_key="not-used",
            timeout=5,
        )

    def test_slow_server_triggers_timeout(self, slow_vlm_server):
        """
        Server sleeps 30s, client timeout is 3s.
        Must raise APITimeoutError for async client as well.
        """
        from vlm.vlm_client import AsyncVLMClient

        vlm = AsyncVLMClient({
            "base_url": slow_vlm_server,
            "model": "test-model",
            "max_tokens": 64,
            "request_timeout": 3,
        })

        start = time.monotonic()
        with pytest.raises(APITimeoutError):
            asyncio.run(vlm.analyze_image_url("http://example.com/img.jpg", "Describe this image"))
        elapsed = time.monotonic() - start

        assert elapsed < 20, f"Timeout not working – request took {elapsed:.1f}s (server sleeps 30s)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
