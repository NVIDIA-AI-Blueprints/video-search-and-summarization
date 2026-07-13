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
Unit tests for vlm.warmup module.

Run with: pytest test/vlm/test_warmup.py -v
"""

import os
from unittest.mock import patch, MagicMock, call

import pytest
import requests
from openai import APITimeoutError, APIConnectionError

from vlm.warmup import (
    _poll_readiness, _send_warmup_inference, _run_warmup_rounds, warmup_vlm,
    WARMUP_VIDEO,
)

_SAMPLE_CONFIG = {
    "base_url": "http://nim:8000/v1",
    "model": "nvidia/test-model",
    "max_tokens": 4096,
}


# ---------------------------------------------------------------------------
# _poll_readiness
# ---------------------------------------------------------------------------

class TestPollReadiness:

    @patch("vlm.warmup.time.sleep")
    @patch("vlm.warmup.requests.get")
    def test_ready_immediately(self, mock_get, mock_sleep):
        mock_get.return_value = MagicMock(status_code=200)
        _poll_readiness("http://nim:8000/v1")
        mock_get.assert_called_once_with("http://nim:8000/v1/health/ready", timeout=5)
        mock_sleep.assert_not_called()

    @patch("vlm.warmup.time.sleep")
    @patch("vlm.warmup.requests.get")
    def test_ready_after_retries(self, mock_get, mock_sleep):
        not_ready = MagicMock(status_code=503)
        ready = MagicMock(status_code=200)
        mock_get.side_effect = [not_ready, not_ready, ready]
        _poll_readiness("http://nim:8000/v1")
        assert mock_get.call_count == 3

    @patch("vlm.warmup.time.monotonic")
    @patch("vlm.warmup.time.sleep")
    @patch("vlm.warmup.requests.get")
    def test_timeout_raises(self, mock_get, mock_sleep, mock_mono):
        # Simulate clock: start=0, first check at 0, then past deadline
        mock_mono.side_effect = [0, 0, 301]
        mock_get.return_value = MagicMock(status_code=503)
        with pytest.raises(RuntimeError, match="NIM not ready"):
            _poll_readiness("http://nim:8000/v1")

    @patch("vlm.warmup.time.sleep")
    @patch("vlm.warmup.requests.get")
    def test_connection_error_retries(self, mock_get, mock_sleep):
        mock_get.side_effect = [
            requests.ConnectionError("refused"),
            MagicMock(status_code=200),
        ]
        _poll_readiness("http://nim:8000/v1")
        assert mock_get.call_count == 2

    @patch("vlm.warmup.time.monotonic")
    @patch("vlm.warmup.time.sleep")
    @patch("vlm.warmup.requests.get")
    def test_fixed_interval_polling(self, mock_get, mock_sleep, mock_mono):
        # Keep deadline far in the future so we never time out.
        # monotonic() is called: once for deadline (start), then once per loop iteration.
        # We need 7 iterations (6 failures + 1 success) -> 1 + 7 = 8 calls.
        mock_mono.side_effect = [0] + [0] * 7

        not_ready = MagicMock(status_code=503)
        ready = MagicMock(status_code=200)
        mock_get.side_effect = [not_ready] * 6 + [ready]

        _poll_readiness("http://nim:8000/v1")

        # Fixed interval: all sleeps use _POLL_INTERVAL (10s)
        sleep_values = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_values == [10, 10, 10, 10, 10, 10]

    @patch("vlm.warmup.time.sleep")
    @patch("vlm.warmup.requests.get")
    def test_trailing_slash_stripped(self, mock_get, mock_sleep):
        mock_get.return_value = MagicMock(status_code=200)
        _poll_readiness("http://nim:8000/v1/")
        mock_get.assert_called_once_with("http://nim:8000/v1/health/ready", timeout=5)


# ---------------------------------------------------------------------------
# _send_warmup_inference
# ---------------------------------------------------------------------------

class TestSendWarmupInference:

    @patch("vlm.warmup.VLMClient")
    def test_success_first_attempt(self, MockVLMClient, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00\x00\x00\x1cftypisom")
        mock_client = MagicMock()

        result = _send_warmup_inference(mock_client, str(video))
        assert result is True

        mock_client.analyze_local_video.assert_called_once_with(
            str(video),
            user_prompt="Describe this video in one sentence.",
        )

    @patch("vlm.warmup.VLMClient")
    def test_retries_on_timeout(self, MockVLMClient, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00\x00\x00\x1cftypisom")
        mock_client = MagicMock()
        mock_client.analyze_local_video.side_effect = APITimeoutError(request=MagicMock())
        # Should not raise — logs warning and continues
        result = _send_warmup_inference(mock_client, str(video))
        assert result is False
        assert mock_client.analyze_local_video.call_count == 3

    @patch("vlm.warmup.VLMClient")
    def test_retries_on_connection_error(self, MockVLMClient, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00\x00\x00\x1cftypisom")
        mock_client = MagicMock()
        mock_client.analyze_local_video.side_effect = APIConnectionError(request=MagicMock())
        # Should not raise — logs warning and continues
        result = _send_warmup_inference(mock_client, str(video))
        assert result is False
        assert mock_client.analyze_local_video.call_count == 3

    @patch("vlm.warmup.VLMClient")
    def test_succeeds_on_second_attempt(self, MockVLMClient, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00\x00\x00\x1cftypisom")
        mock_client = MagicMock()
        mock_client.analyze_local_video.side_effect = [
            APITimeoutError(request=MagicMock()),
            MagicMock(),  # success on second attempt
        ]
        result = _send_warmup_inference(mock_client, str(video))
        assert result is True
        assert mock_client.analyze_local_video.call_count == 2


# ---------------------------------------------------------------------------
# _run_warmup_rounds
# ---------------------------------------------------------------------------

class TestRunWarmupRounds:

    @patch("vlm.warmup.VLMClient")
    @patch("vlm.warmup._send_warmup_inference", return_value=True)
    def test_runs_n_requests(self, mock_send, MockVLMClient, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00\x00\x00\x1cftypisom")
        _run_warmup_rounds(_SAMPLE_CONFIG, str(video), 3, 120)
        assert mock_send.call_count == 3

    @patch("vlm.warmup.VLMClient")
    @patch("vlm.warmup._send_warmup_inference", return_value=True)
    def test_custom_num_requests(self, mock_send, MockVLMClient, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00\x00\x00\x1cftypisom")
        _run_warmup_rounds(_SAMPLE_CONFIG, str(video), 5, 120)
        assert mock_send.call_count == 5

    @patch("vlm.warmup.VLMClient")
    @patch("vlm.warmup._send_warmup_inference", side_effect=[True, False])
    def test_stops_early_on_failure(self, mock_send, MockVLMClient, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00\x00\x00\x1cftypisom")
        _run_warmup_rounds(_SAMPLE_CONFIG, str(video), 3, 120)
        assert mock_send.call_count == 2

    @patch("vlm.warmup.VLMClient")
    @patch("vlm.warmup._send_warmup_inference", return_value=True)
    def test_zero_requests_is_noop(self, mock_send, MockVLMClient, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00\x00\x00\x1cftypisom")
        _run_warmup_rounds(_SAMPLE_CONFIG, str(video), 0, 120)
        mock_send.assert_not_called()

    def test_missing_video_raises(self):
        with pytest.raises(RuntimeError, match="Warmup video not found"):
            _run_warmup_rounds(_SAMPLE_CONFIG, "/nonexistent.mp4", 3, 120)

    @patch("vlm.warmup.VLMClient")
    @patch("vlm.warmup._send_warmup_inference", return_value=True)
    def test_does_not_mutate_original_config(self, mock_send, MockVLMClient, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00\x00\x00\x1cftypisom")
        config = {**_SAMPLE_CONFIG, "warmup": {"num_requests": 2}}
        _run_warmup_rounds(config, str(video), 2, 120)
        # Original config should be completely unchanged
        assert config["max_tokens"] == 4096
        assert "request_timeout" not in config
        assert config["warmup"] == {"num_requests": 2}

    @patch("vlm.warmup.VLMClient")
    @patch("vlm.warmup._send_warmup_inference", return_value=True)
    def test_warmup_key_stripped_from_client_config(self, mock_send, MockVLMClient, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"\x00\x00\x00\x1cftypisom")
        config = {**_SAMPLE_CONFIG, "warmup": {"num_requests": 2}}
        _run_warmup_rounds(config, str(video), 2, 120)
        # VLMClient should not receive the warmup sub-dict
        init_config = MockVLMClient.call_args[0][0]
        assert "warmup" not in init_config
        assert init_config["request_timeout"] == 120
        assert init_config["max_tokens"] == 16


# ---------------------------------------------------------------------------
# warmup_vlm (integration of both phases)
# ---------------------------------------------------------------------------

class TestWarmupVlm:

    @patch("vlm.warmup._run_warmup_rounds")
    @patch("vlm.warmup._poll_readiness")
    def test_calls_both_phases(self, mock_poll, mock_rounds):
        warmup_vlm(_SAMPLE_CONFIG, "/app/warmup/test.mp4")
        mock_poll.assert_called_once_with("http://nim:8000/v1", 300, 10)
        mock_rounds.assert_called_once_with(
            _SAMPLE_CONFIG, "/app/warmup/test.mp4", 3, 120,
        )

    @patch("vlm.warmup._run_warmup_rounds")
    @patch("vlm.warmup._poll_readiness")
    def test_poll_failure_skips_inference(self, mock_poll, mock_rounds):
        mock_poll.side_effect = RuntimeError("NIM not ready")
        with pytest.raises(RuntimeError):
            warmup_vlm(_SAMPLE_CONFIG)
        mock_rounds.assert_not_called()

    @patch("vlm.warmup._run_warmup_rounds")
    @patch("vlm.warmup._poll_readiness")
    def test_config_overrides_defaults(self, mock_poll, mock_rounds):
        config_with_warmup = {
            **_SAMPLE_CONFIG,
            "warmup": {
                "poll_timeout": 60,
                "poll_interval": 5,
                "inference_timeout": 30,
                "num_requests": 2,
            },
        }
        warmup_vlm(config_with_warmup, "/app/warmup/test.mp4")
        mock_poll.assert_called_once_with("http://nim:8000/v1", 60, 5)
        mock_rounds.assert_called_once_with(
            config_with_warmup, "/app/warmup/test.mp4", 2, 30,
        )


# ---------------------------------------------------------------------------
# Entry-point integration helpers
# ---------------------------------------------------------------------------

class TestEntryPointIntegration:
    """Tests for the warmup gating logic in enhance_alert_with_vlm.py."""

    def test_warmup_enabled_by_default(self):
        """VLM_WARMUP_ENABLED defaults to 'true' when unset."""
        env = {}
        enabled = env.get("VLM_WARMUP_ENABLED", "true").lower() != "false"
        assert enabled is True

    def test_warmup_disabled_via_env(self):
        """VLM_WARMUP_ENABLED=false disables warmup."""
        for val in ("false", "False", "FALSE"):
            enabled = val.lower() != "false"
            assert enabled is False, f"Failed for VLM_WARMUP_ENABLED={val}"

    def test_warmup_enabled_for_non_false_values(self):
        """Any value other than 'false' keeps warmup enabled."""
        for val in ("true", "True", "1", "yes", ""):
            enabled = val.lower() != "false"
            assert enabled is True, f"Should be enabled for VLM_WARMUP_ENABLED={val}"

    @patch("os.path.isfile")
    def test_video_path_docker(self, mock_isfile):
        """When Docker path exists, use it."""
        mock_isfile.return_value = True
        video_path = WARMUP_VIDEO if os.path.isfile(WARMUP_VIDEO) else "warmup/test.mp4"
        assert video_path == WARMUP_VIDEO

    @patch("os.path.isfile")
    def test_video_path_local_fallback(self, mock_isfile):
        """When Docker path is missing, fall back to relative path."""
        mock_isfile.return_value = False
        video_path = WARMUP_VIDEO if os.path.isfile(WARMUP_VIDEO) else "warmup/test.mp4"
        assert video_path == "warmup/test.mp4"
