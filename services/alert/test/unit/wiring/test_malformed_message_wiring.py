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

"""Wiring test for the ``malformed_message`` failure-reason guard.

The downstream VST stage dereferences ``message['timestamp']`` and
``message['end']`` directly. The HTTP JSON endpoint validates these, but a
producer that bypasses it (the protobuf endpoint, a raw Kafka producer, or
replay tooling) can still enqueue a message missing one of them. Without a
guard those raise ``KeyError`` deep in ``_resolve_video_url``.

``_prepare_message_context`` now rejects such messages up-front, recording
``record_event_complete(..., failure_reason="malformed_message")`` and
early-returning before any prompt lookup or VST call. These tests assert that
wiring via a spy on ``record_event_complete``.
"""

import logging
import os
import sys
import threading
import types
from unittest.mock import Mock

import pytest


# ── Module-level setup (mirrors test_no_prompt_wiring.py) ─────────────────
os.environ.setdefault("PROMETHEUS_METRICS_ENABLED", "false")

_stub_modules = [
    'its_redis', 'clients.redis_handler',
    'mdx', 'mdx', 'mdx.event_bridge_factory',
    'mdx.sink', 'mdx.sink.vlm_enhanced_sink',
    'mdx.utils', 'mdx.utils.elastic_ready',
    'handlers', 'handlers.enrichment', 'handlers.direct_media',
    'handlers.prompt_handler', 'handlers.prompt_handler.alert_type_config_loader',
    'handlers.async_dispatch_mixin',
    'handlers.event_loop_pipeline_mixin',
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

sys.modules['clients.redis_handler'].RedisHandler = Mock
sys.modules['mdx.event_bridge_factory'].EventBridgeFactory = Mock()
sys.modules['mdx.sink.vlm_enhanced_sink'].build_vlm_enhanced_sink = Mock()
sys.modules['mdx.utils.elastic_ready'].generate_alert_fingerprint = Mock(return_value='fp')
sys.modules['mdx.utils.elastic_ready'].generate_incident_fingerprint = Mock(return_value='fp')
sys.modules['handlers.enrichment'].EnrichmentProcessor = Mock
sys.modules['handlers.direct_media'].DirectMediaHandler = Mock
sys.modules['handlers.prompt_handler.alert_type_config_loader'].AlertTypeConfig = Mock
sys.modules['handlers.prompt_handler.alert_type_config_loader'].AlertTypeConfigLoader = Mock

class _AsyncDispatchMixinStub: pass
class _AsyncExternalIOMixinStub: pass
class _AsyncVLMModeMixinStub: pass
sys.modules['handlers.async_dispatch_mixin'].AsyncDispatchMixin = _AsyncDispatchMixinStub
sys.modules['handlers.async_dispatch_mixin'].PIPELINE_MODE_SYNC = 'sync'
sys.modules['handlers.async_dispatch_mixin'].PIPELINE_MODE_THREAD_BRIDGE = 'thread_bridge'
sys.modules['handlers.async_dispatch_mixin'].PIPELINE_MODE_EVENT_LOOP = 'event_loop'
sys.modules['handlers.async_dispatch_mixin'].resolve_pipeline_mode = lambda raw, legacy: (
    str(raw).strip().lower()
    if raw is not None and str(raw).strip().lower() in ('sync', 'thread_bridge', 'event_loop')
    else ('thread_bridge' if legacy else 'sync')
)
sys.modules['handlers.event_loop_pipeline_mixin'].EventLoopPipelineMixin = type(
    'EventLoopPipelineMixinStub', (), {})
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


def _bind_real_stage_helpers(stub):
    for _name in (
        '_prepare_message_context', '_resolve_video_url', '_transform_video_urls',
        '_handle_media_collection_failure', '_handle_url_validation_failure',
        '_apply_vlm_response', '_apply_vlm_parse_failure',
        '_publish_outcome_and_complete', '_handle_vlm_exception',
        '_apply_vlm_exception', '_log_vlm_exception',
    ):
        setattr(stub, _name, getattr(AnomalyEnhancer, _name).__get__(stub))
    for _name in (
        '_classify_vst_failure', '_classify_vst_failure_reason',
        '_classify_pre_processing_failure', '_extract_root_cause',
    ):
        setattr(stub, _name, getattr(AnomalyEnhancer, _name))


@pytest.fixture
def spy_record(monkeypatch):
    spy = Mock()
    monkeypatch.setattr(eavw, "record_event_complete", spy)
    return spy


def _make_stub():
    stub = Mock(spec=AnomalyEnhancer)
    _bind_real_stage_helpers(stub)
    stub.config = {
        'vst_config': {'retry_without_overlay': False},
        'vlm': {'max_retries': 0, 'model': 'test'},
        'alert_agent': {
            'include_latency_info': False,
            'url_transform': {'enabled': False},
        },
    }
    stub.prompt_manager = Mock()
    stub.prompt_manager.alert_config_loader = None
    stub.prompt_manager.get_prompts_for_message.return_value = (None, None)
    stub._vst_handler = Mock()
    stub._set_message_id_and_should_skip = Mock(return_value=False)
    stub._compute_fingerprint = Mock(return_value=None)
    return stub


def _valid_msg():
    return {
        'sensorId': 'cam-1',
        'category': 'collision',
        'timestamp': '2025-01-01T00:00:00Z',
        'end': '2025-01-01T00:00:02Z',
        'objectIds': [],
    }


class TestMalformedMessageRecordsFailure:
    @pytest.mark.parametrize("field", ["sensorId", "timestamp", "end"])
    def test_missing_field_fires_record_event_complete_with_reason(self, spy_record, field):
        stub = _make_stub()
        msg = _valid_msg()
        del msg[field]

        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=msg)

        spy_record.assert_called_once()
        assert spy_record.call_args.kwargs.get("failure_reason") == "malformed_message"

    @pytest.mark.parametrize("field", ["sensorId", "timestamp", "end"])
    def test_empty_field_is_treated_as_missing(self, spy_record, field):
        stub = _make_stub()
        msg = _valid_msg()
        msg[field] = ""

        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=msg)

        spy_record.assert_called_once()
        assert spy_record.call_args.kwargs.get("failure_reason") == "malformed_message"

    def test_malformed_message_does_not_reach_prompt_or_vst(self, spy_record):
        stub = _make_stub()
        msg = _valid_msg()
        del msg['end']

        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=msg)

        stub._set_message_id_and_should_skip.assert_not_called()
        stub.prompt_manager.get_prompts_for_message.assert_not_called()
        stub._vst_handler.get_video_stream_url.assert_not_called()


class TestValidMessageSkipsMalformedBranch:
    def test_valid_message_does_not_emit_malformed_reason(self, spy_record):
        stub = _make_stub()

        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=_valid_msg())

        reasons = [call.kwargs.get("failure_reason") for call in spy_record.call_args_list]
        assert "malformed_message" not in reasons
