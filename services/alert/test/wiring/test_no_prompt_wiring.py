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

"""Integration test for the C10 ``no_prompt`` failure-reason wiring.

Before C10, when ``prompt_manager.get_prompts_for_message`` returned
``(None, None)``, ``_process_single_message`` returned immediately with
only a WARNING log — no Prometheus metric, no visibility. A misconfigured
alert type would cause events to silently stop flowing and operators
would have no dashboard correlate.

After C10, the same path calls ``record_event_complete(...,
failure_reason="no_prompt")`` before returning, so every event is
accounted for in the VERIFICATION_FAILURES counter and the C2
reconciliation identity stays valid.

These tests assert the wiring via a spy on ``record_event_complete`` so
we do not need to stand up the real Prometheus registry. The helper
itself is already covered by ``test/metrics/test_metrics_recorder.py``.

Run with: pytest test/test_no_prompt_wiring.py -v
"""

import logging
import os
import sys
import threading
import types
from unittest.mock import Mock

import pytest


# ── Module-level setup ───────────────────────────────────────────────────
os.environ.setdefault("PROMETHEUS_METRICS_ENABLED", "false")

_stub_modules = [
    'its_redis', 'its_redis.redis_handler',
    'mdx', 'mdx.anomaly', 'mdx.anomaly.event_bridge_factory',
    'mdx.anomaly.sink', 'mdx.anomaly.sink.vlm_enhanced_sink',
    'mdx.anomaly.utils', 'mdx.anomaly.utils.elastic_ready',
    'handlers', 'handlers.enrichment', 'handlers.direct_media',
    'handlers.prompt_handler', 'handlers.prompt_handler.alert_type_config_loader',
    'handlers.async_dispatch_mixin',
    'handlers.async_external_io_mixin',
    'handlers.async_vlm_mode_mixin',
    'utils.logging_config',
    'utils.schema_util',
    'vlm.warmup',
    'vss',
    'metrics', 'metrics.prometheus_metrics', 'metrics.recorder',
]
for mod_name in _stub_modules:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

sys.modules['handlers'].__path__ = []
sys.modules['handlers.prompt_handler'].__path__ = []
sys.modules['metrics'].__path__ = []

sys.modules['its_redis.redis_handler'].RedisHandler = Mock
sys.modules['mdx.anomaly.event_bridge_factory'].EventBridgeFactory = Mock()
sys.modules['mdx.anomaly.sink.vlm_enhanced_sink'].build_vlm_enhanced_sink = Mock()
sys.modules['mdx.anomaly.utils.elastic_ready'].generate_alert_fingerprint = Mock(return_value='fp')
sys.modules['mdx.anomaly.utils.elastic_ready'].generate_incident_fingerprint = Mock(return_value='fp')
sys.modules['handlers.enrichment'].EnrichmentProcessor = Mock
sys.modules['handlers.direct_media'].DirectMediaHandler = Mock
sys.modules['handlers.prompt_handler.alert_type_config_loader'].AlertTypeConfig = Mock
sys.modules['handlers.prompt_handler.alert_type_config_loader'].AlertTypeConfigLoader = Mock

class _AsyncDispatchMixinStub: pass
class _AsyncExternalIOMixinStub: pass
class _AsyncVLMModeMixinStub: pass
sys.modules['handlers.async_dispatch_mixin'].AsyncDispatchMixin = _AsyncDispatchMixinStub
sys.modules['handlers.async_external_io_mixin'].AsyncExternalIOMixin = _AsyncExternalIOMixinStub
sys.modules['handlers.async_vlm_mode_mixin'].AsyncVLMModeMixin = _AsyncVLMModeMixinStub

sys.modules['utils.logging_config'].setup_logging = Mock()
sys.modules['utils.logging_config'].get_logger = lambda name: logging.getLogger(name)
sys.modules['utils.logging_config'].enforce_log_level = Mock()
sys.modules['utils.schema_util'].protobuf_anomalies_to_json_string_list = Mock()
sys.modules['vlm.warmup'].warmup_vlm = Mock()
sys.modules['vlm.warmup'].WARMUP_VIDEO = '/tmp/fake.mp4'
sys.modules['vss'].VSSHandler = Mock

sys.modules['metrics'].PROMETHEUS_ENABLED = False
for name in (
    "inc_events_after_dedup", "inc_events_dropped", "inc_events_skipped_confirmed",
    "observe_pipeline_latency", "observe_video_length", "observe_vlm_duration",
    "observe_vst_duration", "record_event_complete", "warm_startup_labels",
):
    setattr(sys.modules['metrics.recorder'], name, Mock())

import enhance_alert_with_vlm as eavw  # noqa: E402
from enhance_alert_with_vlm import AnomalyEnhancer  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def spy_record(monkeypatch):
    """Replace ``record_event_complete`` (imported into ``eavw``) with
    a spy so we can inspect exactly what failure_reason the no-prompt
    path passes through."""
    spy = Mock()
    monkeypatch.setattr(eavw, "record_event_complete", spy)
    return spy


def _make_stub(user_prompt=None, system_prompt=None):
    """Build a minimal ``AnomalyEnhancer`` stub wired to test the
    no-prompt early-return. Every downstream path (VLM analyze, VST,
    sink publish) is intentionally left unconfigured — reaching any of
    them means the no-prompt branch did NOT early-return as expected,
    and the test should fail."""
    stub = Mock(spec=AnomalyEnhancer)
    stub.config = {
        'vst_config': {'retry_without_overlay': False},
        'vlm': {'max_retries': 0, 'model': 'test'},
        'alert_agent': {
            'include_latency_info': False,
            'url_transform': {'enabled': False},
        },
    }
    stub.url_transform_enabled = False
    stub.include_latency_info = False
    stub.vlm_media_source_using_base64 = False
    stub.async_vst_enabled = False
    stub.async_elastic_enabled = False
    stub.async_redis_enabled = False
    stub.async_io_enabled = False
    stub.async_vlm_runtime = None
    stub.async_external_timeout_seconds = 30.0
    stub._vlm_sink_type = "elastic"
    stub._sink_async_lock = threading.Lock()
    stub._sink_async_futures = set()
    stub._vst_handler = Mock()
    stub.vlm_client = Mock()
    stub.vlm_client.config = {'num_frames': 10}
    stub.prompt_manager = Mock()
    stub.prompt_manager.alert_config_loader = None
    stub.prompt_manager.get_prompts_for_message.return_value = (user_prompt, system_prompt)
    stub.vlm_enhanced_event_sink = Mock()
    stub.enrichment_processor = Mock(process=Mock(return_value=None))
    stub.redis_handler = Mock()
    stub.validate_video_url = Mock(return_value=True)
    stub._set_message_id_and_should_skip = Mock(return_value=False)
    stub._compute_fingerprint = Mock(return_value=None)
    stub._pluggable_parser = None
    return stub


def _msg():
    return {
        'sensorId': 'cam-1',
        'category': 'missing-prompt-type',
        'timestamp': '2025-01-01T00:00:00Z',
        'end': '2025-01-01T00:00:02Z',
        'objectIds': [],
    }


# ── Tests ────────────────────────────────────────────────────────────────


class TestNoPromptPathRecordsFailure:
    def test_no_prompt_fires_record_event_complete_with_reason(self, spy_record):
        """The common case: prompt lookup returns (None, None) and the
        function early-returns. The spy must see exactly one call with
        ``failure_reason="no_prompt"`` before the return."""
        stub = _make_stub(user_prompt=None, system_prompt=None)
        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=_msg())

        spy_record.assert_called_once()
        kwargs = spy_record.call_args.kwargs
        assert kwargs.get("failure_reason") == "no_prompt"

    def test_no_prompt_does_not_call_vst_or_vlm(self, spy_record):
        """The no-prompt branch MUST early-return. If any downstream
        component was called the refactor went wrong — this test
        catches the regression directly."""
        stub = _make_stub(user_prompt=None, system_prompt=None)
        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=_msg())
        stub._vst_handler.get_video_stream_url.assert_not_called()
        stub.vlm_client.analyze_video_url.assert_not_called()
        stub.vlm_enhanced_event_sink.publish_success.assert_not_called()
        stub.vlm_enhanced_event_sink.publish_error.assert_not_called()


class TestBothPromptsPresentSkipsNoPromptBranch:
    """Regression guard: when a prompt IS configured, the
    ``failure_reason="no_prompt"`` label must NOT be emitted. Otherwise
    every successfully-verified event would also be counted as a
    no-prompt failure."""

    def test_both_prompts_set_no_early_return_no_prompt_reason(self, spy_record):
        stub = _make_stub(user_prompt="u", system_prompt="s")
        # Prevent the flow from reaching VST — return no video URL, but
        # raise a VSTError-like exception to short-circuit. We only care
        # about the branching decision at the no-prompt check.
        stub._get_video_stream_url_with_mode = Mock(
            side_effect=RuntimeError("test short-circuit after no-prompt check")
        )
        try:
            AnomalyEnhancer._process_single_message(stub, worker_id=0, message=_msg())
        except Exception:
            pass

        # If record_event_complete fired, it must NOT be with no_prompt.
        reasons = [call.kwargs.get("failure_reason") for call in spy_record.call_args_list]
        assert "no_prompt" not in reasons


class TestOnlyOneSidePresentStillExitsEarly:
    """Edge: ``_process_single_message`` checks ``both`` are ``None``.
    If only one is None the function continues into the VLM pipeline.
    Lock that behavior down so a future ``or`` → ``and`` typo (which
    would break every event with a system-prompt-only alert type)
    fails this test immediately."""

    def test_only_user_prompt_none_does_not_trigger_no_prompt(self, spy_record):
        stub = _make_stub(user_prompt=None, system_prompt="s")
        stub._get_video_stream_url_with_mode = Mock(
            side_effect=RuntimeError("test short-circuit after no-prompt check")
        )
        try:
            AnomalyEnhancer._process_single_message(stub, worker_id=0, message=_msg())
        except Exception:
            pass

        reasons = [call.kwargs.get("failure_reason") for call in spy_record.call_args_list]
        assert "no_prompt" not in reasons

    def test_only_system_prompt_none_does_not_trigger_no_prompt(self, spy_record):
        stub = _make_stub(user_prompt="u", system_prompt=None)
        stub._get_video_stream_url_with_mode = Mock(
            side_effect=RuntimeError("test short-circuit after no-prompt check")
        )
        try:
            AnomalyEnhancer._process_single_message(stub, worker_id=0, message=_msg())
        except Exception:
            pass

        reasons = [call.kwargs.get("failure_reason") for call in spy_record.call_args_list]
        assert "no_prompt" not in reasons
