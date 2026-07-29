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

"""Unit tests for OnDemandVerificationService (prepare + process_and_publish)."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_web_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "alert-agent-web")
)
_repo_root = os.path.abspath(os.path.join(_web_root, ".."))

_saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "app" or k.startswith("app.")}
_saved_path = sys.path[:]
sys.path = [p for p in sys.path if os.path.abspath(p) != _repo_root and p != ""]
sys.path.insert(0, _web_root)
try:
    import web.main  # noqa: F401
    from web.service import ondemand_verification_service as _svc_mod
    from web.service.ondemand_verification_service import (
        AlertTypeNotFoundError,
        OnDemandVerificationService,
    )
finally:
    sys.path = _saved_path
    sys.modules.update(_saved)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_config(max_media_count=5):
    return {
        "vlm": {"model": "test-model", "vlm_media_source_using_base64": False},
        "alert_agent": {
            "media_download": {
                "enabled": True,
                "max_media_count": max_media_count,
                "use_verdict": False,
            }
        },
        "vst_config": {"download_dir": "/tmp/test_media"},
        "elastic": {"enabled": False, "hosts": []},
        "vlm_enhanced_sink": {},
    }


def _make_payload(media_urls=None, media_type="video", category="collision", **extra):
    return {
        "category": category,
        "info": {
            "media_urls": media_urls or ["http://host/video.mp4"],
            "media_type": media_type,
        },
        **extra,
    }


class _ServiceContext:
    """Keeps patches active for the lifetime of a test."""

    def __init__(
        self,
        user_prompt="Detect collisions",
        system_prompt="Be concise",
        max_media_count=5,
        metrics_enabled=True,
    ):
        self.prompt_mgr = MagicMock()
        self.prompt_mgr.get_prompts_for_message.return_value = (user_prompt, system_prompt)
        self.mock_handler = MagicMock()
        self.mock_sink = MagicMock()
        self.mock_completion_recorder = MagicMock()
        self.metrics_enabled = metrics_enabled

        self._patches = [
            patch.object(_svc_mod, "load_config", return_value=_stub_config(max_media_count)),
            patch.object(_svc_mod, "load_config_path", return_value="config.yaml"),
            patch.object(_svc_mod, "VLMClient"),
            patch.object(_svc_mod, "PromptManager", return_value=self.prompt_mgr),
            patch.object(_svc_mod, "build_vlm_enhanced_sink", return_value=self.mock_sink),
            patch.object(_svc_mod, "DirectMediaHandler", return_value=self.mock_handler),
            patch.object(
                _svc_mod,
                "record_ondemand_event_complete",
                self.mock_completion_recorder,
            ),
            # The service gates every metrics call on this flag, which is
            # driven by the PROMETHEUS_METRICS_ENABLED env var and is off by
            # default. Pin it explicitly so the metrics assertions below do
            # not depend on the ambient environment.
            patch.object(_svc_mod, "PROMETHEUS_ENABLED", metrics_enabled),
        ]

    def start(self):
        for p in self._patches:
            p.start()
        self.svc = OnDemandVerificationService()
        self.svc.prompt_manager = self.prompt_mgr
        return self

    def stop(self):
        for p in reversed(self._patches):
            p.stop()


@pytest.fixture()
def ctx():
    c = _ServiceContext().start()
    yield c
    c.stop()


# ---------------------------------------------------------------------------
# prepare() — message defaults
# ---------------------------------------------------------------------------

class TestPrepareDefaults:

    def test_auto_generates_id(self, ctx):
        msg, _, _ = ctx.svc.prepare(_make_payload())
        assert msg["id"].startswith("ondemand-")

    def test_default_sensorId(self, ctx):
        msg, _, _ = ctx.svc.prepare(_make_payload())
        assert msg["sensorId"] == "ondemand"

    def test_auto_generates_timestamps(self, ctx):
        msg, _, _ = ctx.svc.prepare(_make_payload())
        assert "timestamp" in msg
        assert "end" in msg

    def test_caller_id_preserved(self, ctx):
        msg, _, _ = ctx.svc.prepare(_make_payload(id="my-id"))
        assert msg["id"] == "my-id"

    def test_caller_sensorId_preserved(self, ctx):
        msg, _, _ = ctx.svc.prepare(_make_payload(sensorId="cam-01"))
        assert msg["sensorId"] == "cam-01"

    def test_caller_timestamp_preserved(self, ctx):
        ts = "2026-01-01T00:00:00Z"
        msg, _, _ = ctx.svc.prepare(_make_payload(timestamp=ts, end=ts))
        assert msg["timestamp"] == ts


# ---------------------------------------------------------------------------
# prepare() — prompt resolution
# ---------------------------------------------------------------------------

class TestPreparePrompts:

    def test_returns_prompts(self, ctx):
        _, user, system = ctx.svc.prepare(_make_payload())
        assert user == "Detect collisions"
        assert system == "Be concise"

    def test_no_prompt_raises_alert_type_not_found(self):
        c = _ServiceContext(user_prompt=None).start()
        try:
            with pytest.raises(AlertTypeNotFoundError, match="No prompt"):
                c.svc.prepare(_make_payload(category="nonexistent"))
        finally:
            c.stop()

    def test_prompt_manager_exception_raises_value_error(self):
        c = _ServiceContext().start()
        try:
            c.prompt_mgr.get_prompts_for_message.side_effect = RuntimeError("redis down")
            with pytest.raises(ValueError, match="redis down"):
                c.svc.prepare(_make_payload())
        finally:
            c.stop()


# ---------------------------------------------------------------------------
# prepare() — media_urls truncation
# ---------------------------------------------------------------------------

class TestPrepareTruncation:

    def test_excess_urls_truncated(self):
        c = _ServiceContext(max_media_count=2).start()
        try:
            urls = [f"http://host/{i}.jpg" for i in range(5)]
            msg, _, _ = c.svc.prepare(_make_payload(media_urls=urls, media_type="image"))
            assert len(msg["info"]["media_urls"]) == 2
        finally:
            c.stop()


# ---------------------------------------------------------------------------
# process_and_publish() — delegates to DirectMediaHandler
# ---------------------------------------------------------------------------

class TestProcessAndPublish:

    def test_calls_handler_evaluate(self, ctx):
        msg, user, system = ctx.svc.prepare(_make_payload())
        ctx.svc.process_and_publish(msg, user, system)

        ctx.mock_handler.evaluate.assert_called_once()
        call_kwargs = ctx.mock_handler.evaluate.call_args.kwargs
        assert call_kwargs["worker_id"] == 0
        assert call_kwargs["message"] is msg
        assert call_kwargs["info_block"] == msg["info"]
        assert call_kwargs["user_prompt"] == user
        assert call_kwargs["system_prompt"] == system

    def test_handler_receives_full_message(self, ctx):
        payload = _make_payload(id="test-123", sensorId="cam-77")
        msg, user, system = ctx.svc.prepare(payload)
        ctx.svc.process_and_publish(msg, user, system)

        call_kwargs = ctx.mock_handler.evaluate.call_args.kwargs
        assert call_kwargs["message"]["id"] == "test-123"
        assert call_kwargs["message"]["sensorId"] == "cam-77"

    def test_records_completion_with_request_start(self, ctx):
        msg, user, system = ctx.svc.prepare(_make_payload())

        ctx.svc.process_and_publish(
            msg, user, system, request_start_time=123.0
        )

        ctx.mock_completion_recorder.assert_called_once()
        call_kwargs = ctx.mock_completion_recorder.call_args.kwargs
        assert call_kwargs["request_start_time"] == 123.0
        assert call_kwargs["message"] is msg
        assert call_kwargs["failure_reason"] is None
        assert isinstance(call_kwargs["processing_start_time"], float)

    def test_records_background_exception_once_and_reraises(self, ctx):
        msg, user, system = ctx.svc.prepare(_make_payload())
        ctx.mock_handler.evaluate.side_effect = RuntimeError("sink down")

        with pytest.raises(RuntimeError, match="sink down"):
            ctx.svc.process_and_publish(
                msg, user, system, request_start_time=123.0
            )

        ctx.mock_completion_recorder.assert_called_once()
        assert (
            ctx.mock_completion_recorder.call_args.kwargs["failure_reason"]
            == "background_exception"
        )


# ---------------------------------------------------------------------------
# __init__ — wiring
# ---------------------------------------------------------------------------

class TestInit:

    def test_sink_built(self, ctx):
        assert ctx.svc.vlm_enhanced_event_sink is ctx.mock_sink

    def test_handler_built_with_sink(self, ctx):
        _svc_mod.DirectMediaHandler.assert_called_once()
        call_kwargs = _svc_mod.DirectMediaHandler.call_args.kwargs
        assert call_kwargs["vlm_enhanced_event_sink"] is ctx.mock_sink

    def test_handler_built_with_ondemand_vlm_observer(self, ctx):
        call_kwargs = _svc_mod.DirectMediaHandler.call_args.kwargs
        assert (
            call_kwargs["vlm_duration_observer"]
            is _svc_mod.observe_ondemand_vlm_duration
        )


# ---------------------------------------------------------------------------
# Metrics disabled — the default deployment shape
# ---------------------------------------------------------------------------

class TestMetricsDisabled:
    """PROMETHEUS_METRICS_ENABLED is off unless a deployment sets it.

    Everything the service does for metrics is gated on that flag, so the
    disabled path is the one most installs actually run and it must stay
    functional — the verification itself still has to happen.
    """

    @pytest.fixture()
    def ctx_no_metrics(self):
        c = _ServiceContext(metrics_enabled=False).start()
        yield c
        c.stop()

    def test_no_vlm_duration_observer_is_wired(self, ctx_no_metrics):
        call_kwargs = _svc_mod.DirectMediaHandler.call_args.kwargs
        assert call_kwargs["vlm_duration_observer"] is None

    def test_the_handler_is_still_built(self, ctx_no_metrics):
        assert ctx_no_metrics.svc.vlm_enhanced_event_sink is ctx_no_metrics.mock_sink

    def test_prepare_still_works(self, ctx_no_metrics):
        message, user_prompt, system_prompt = ctx_no_metrics.svc.prepare(_make_payload())

        assert message["sensorId"] == "ondemand"
        assert user_prompt == "Detect collisions"
        assert system_prompt == "Be concise"

    def test_the_verification_still_runs(self, ctx_no_metrics):
        message, user_prompt, system_prompt = ctx_no_metrics.svc.prepare(_make_payload())

        ctx_no_metrics.svc.process_and_publish(message, user_prompt, system_prompt, None)

        ctx_no_metrics.mock_handler.evaluate.assert_called_once()

    def test_no_completion_is_recorded(self, ctx_no_metrics):
        message, user_prompt, system_prompt = ctx_no_metrics.svc.prepare(_make_payload())

        ctx_no_metrics.svc.process_and_publish(message, user_prompt, system_prompt, None)

        ctx_no_metrics.mock_completion_recorder.assert_not_called()

    def test_a_background_failure_still_propagates(self, ctx_no_metrics):
        message, user_prompt, system_prompt = ctx_no_metrics.svc.prepare(_make_payload())
        ctx_no_metrics.mock_handler.evaluate.side_effect = RuntimeError("sink down")

        with pytest.raises(RuntimeError, match="sink down"):
            ctx_no_metrics.svc.process_and_publish(message, user_prompt, system_prompt, None)
