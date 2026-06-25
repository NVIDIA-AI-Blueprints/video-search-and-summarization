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
Unit tests for VST error propagation in _process_single_message().

Covers how different VST failure modes (network, HTTP, config, missing data)
propagate through the processing pipeline and are captured in the message
info block that gets written to Elasticsearch.

Updated for the typed VSTError exception system: the VST handler now wraps
raw exceptions into VSTError subclasses, and _classify_vst_failure() maps
them to specific HTTP codes and user-facing messages.
"""

import sys
import types
import logging
import threading
from unittest.mock import Mock, patch, call

import pytest

# Stub heavy modules before importing enhance_alert_with_vlm
_stub_modules = [
    'its_redis', 'its_redis.redis_handler',
    'mdx', 'mdx.anomaly', 'mdx.anomaly.event_bridge_factory',
    'mdx.anomaly.sink', 'mdx.anomaly.sink.vlm_enhanced_sink',
    'mdx.anomaly.utils', 'mdx.anomaly.utils.elastic_ready',
    'handlers', 'handlers.enrichment', 'handlers.direct_media',
    'handlers.prompt_handler', 'handlers.prompt_handler.alert_type_config_loader',
    'utils.logging_config',
    'utils.schema_util',
    'vlm.warmup',
    'vss',
    'metrics', 'metrics.prometheus_metrics',
]
for mod_name in _stub_modules:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# Mark `handlers` and `handlers.prompt_handler` as packages so dotted imports
# (e.g. `from handlers.prompt_handler.alert_type_config_loader import X`)
# resolve through the stub tree instead of erroring with "not a package".
sys.modules['handlers'].__path__ = []
sys.modules['handlers.prompt_handler'].__path__ = []

sys.modules['its_redis.redis_handler'].RedisHandler = Mock
sys.modules['mdx.anomaly.event_bridge_factory'].EventBridgeFactory = Mock()
sys.modules['mdx.anomaly.sink.vlm_enhanced_sink'].build_vlm_enhanced_sink = Mock()
sys.modules['mdx.anomaly.utils.elastic_ready'].generate_alert_fingerprint = Mock(return_value='fp')
sys.modules['mdx.anomaly.utils.elastic_ready'].generate_incident_fingerprint = Mock(return_value='fp')
sys.modules['handlers.enrichment'].EnrichmentProcessor = Mock
sys.modules['handlers.direct_media'].DirectMediaHandler = Mock
sys.modules['handlers.prompt_handler.alert_type_config_loader'].AlertTypeConfig = Mock
sys.modules['handlers.prompt_handler.alert_type_config_loader'].AlertTypeConfigLoader = Mock
sys.modules['utils.logging_config'].setup_logging = Mock()
sys.modules['utils.logging_config'].get_logger = lambda name: logging.getLogger(name)
sys.modules['utils.logging_config'].enforce_log_level = Mock()
sys.modules['utils.schema_util'].protobuf_anomalies_to_json_string_list = Mock()
sys.modules['vlm.warmup'].warmup_vlm = Mock()
sys.modules['vlm.warmup'].WARMUP_VIDEO = '/tmp/fake.mp4'
sys.modules['vss'].VSSHandler = Mock
sys.modules['metrics'].PROMETHEUS_ENABLED = False

from enhance_alert_with_vlm import AnomalyEnhancer
from vst.exceptions import (
    VSTError,
    VSTUnavailableError,
    VSTTimeoutError,
    VSTOverloadedError,
    VSTRecordingNotFoundError,
    VSTClientError,
)


def _make_enhancer(retry_without_overlay=False):
    stub = Mock(spec=AnomalyEnhancer)
    stub.config = {
        'vst_config': {'retry_without_overlay': retry_without_overlay},
        'vlm': {'max_retries': 0, 'model': 'test', 'dynamic_frame_count': False},
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
    stub.prompt_manager.get_prompts_for_message.return_value = ("u", "s")
    stub.vlm_enhanced_event_sink = Mock()
    stub.enrichment_processor = Mock(process=Mock(return_value=None))
    stub.redis_handler = Mock()
    stub.validate_video_url = Mock(return_value=True)
    stub._set_message_id_and_should_skip = Mock(return_value=False)
    stub._compute_fingerprint = Mock(return_value=None)
    stub._classify_vst_failure = AnomalyEnhancer._classify_vst_failure
    stub._analyze_video_url_with_mode = Mock(
        side_effect=lambda video_url, user_prompt, system_prompt, num_frames=10, use_base64=False: (
            stub.vlm_client.analyze_video_url(
                video_url,
                user_prompt,
                system_prompt,
                num_frames=num_frames,
            )
        )
    )
    stub._get_video_stream_url_with_mode = Mock(
        side_effect=lambda sensor_id, start, end, **kwargs: (
            stub._vst_handler.get_video_stream_url(sensor_id, start, end, **kwargs)
        )
    )
    stub._publish_success_with_mode = Mock(
        side_effect=lambda message, user_prompt, system_prompt, response_content: (
            stub.vlm_enhanced_event_sink.publish_success(
                message, user_prompt, system_prompt, response_content
            )
        )
    )
    stub._publish_error_with_mode = Mock(
        side_effect=lambda message, user_prompt, system_prompt, error_payload: (
            stub.vlm_enhanced_event_sink.publish_error(
                message, user_prompt, system_prompt, error_payload
            )
        )
    )
    stub._update_enrichment_with_mode = Mock(return_value=None)
    return stub


def _make_msg():
    return {
        'sensorId': 'cam-1',
        'category': 'loitering',
        'timestamp': '2025-09-10T11:16:31Z',
        'end': '2025-09-10T11:16:33Z',
        'objectIds': [],
    }


def _run(stub, msg=None):
    """Helper: run _process_single_message and return the info block."""
    msg = msg or _make_msg()
    AnomalyEnhancer._process_single_message(stub, worker_id=0, message=msg)
    return msg.get('info', {})


# ---------------------------------------------------------------------------
# VST connection failures → 503  (VSTUnavailableError)
# ---------------------------------------------------------------------------

class TestVSTConnectionError:
    """VST unreachable — handler raises VSTUnavailableError."""

    def test_reports_503_unavailable(self):
        stub = _make_enhancer()
        stub._vst_handler.get_video_stream_url.side_effect = VSTUnavailableError(
            "Cannot connect to VST: Connection refused",
            category="connection_failed",
        )
        info = _run(stub)

        assert info['verificationResponseCode'] == '503'
        assert 'unavailable' in info['verificationResponseStatus'].lower()
        assert info['verdict'] == 'verification-failed'
        assert info.get('videoSource') == ''

    def test_publishes_error_to_sink(self):
        stub = _make_enhancer()
        stub._vst_handler.get_video_stream_url.side_effect = VSTUnavailableError(
            "Cannot connect to VST: refused", category="connection_failed",
        )
        _run(stub)

        stub.vlm_enhanced_event_sink.publish_error.assert_called_once()
        stub.vlm_enhanced_event_sink.publish_success.assert_not_called()


# ---------------------------------------------------------------------------
# VST retry behavior
# ---------------------------------------------------------------------------

class TestVSTRetry:
    """retry_without_overlay enabled: first call fails, retry attempted."""

    def test_both_fail_reports_503(self):
        stub = _make_enhancer(retry_without_overlay=True)
        stub._vst_handler.get_video_stream_url.side_effect = VSTUnavailableError(
            "Cannot connect to VST: refused", category="connection_failed",
        )

        info = _run(stub)

        assert stub._vst_handler.get_video_stream_url.call_count == 2
        assert info['verificationResponseCode'] == '503'

    def test_retry_succeeds_proceeds_to_vlm(self):
        stub = _make_enhancer(retry_without_overlay=True)
        stub._vst_handler.get_video_stream_url.side_effect = [
            VSTUnavailableError("Cannot connect", category="connection_failed"),
            ('http://vst/clip.mp4', '2025-01-01T00:00:00Z', '2025-01-01T00:00:10Z'),
        ]

        msg = _make_msg()
        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=msg)

        info = msg.get('info', {})
        assert info.get('verificationResponseCode') != '503'
        assert info.get('verificationResponseCode') != '404'
        stub._analyze_video_url_with_mode.assert_called_once()


# ---------------------------------------------------------------------------
# No recording (no exception) → 404
# ---------------------------------------------------------------------------

class TestVSTNoRecording:
    """VST returns successfully but video_url is None → 404."""

    def test_reports_404(self):
        stub = _make_enhancer()
        stub._vst_handler.get_video_stream_url.return_value = (None, 'start', 'end')

        info = _run(stub)

        assert info['verificationResponseCode'] == '404'
        assert info['verificationResponseStatus'] == 'No video recording found for the requested time'

    def test_publishes_error_to_sink(self):
        stub = _make_enhancer()
        stub._vst_handler.get_video_stream_url.return_value = (None, 'start', 'end')
        _run(stub)

        stub.vlm_enhanced_event_sink.publish_error.assert_called_once()
        stub.vlm_enhanced_event_sink.publish_success.assert_not_called()


# ---------------------------------------------------------------------------
# Typed VST exceptions → correct codes via _classify_vst_failure
# ---------------------------------------------------------------------------

class TestTypedVSTExceptionClassification:
    """Each VSTError subclass maps to the correct HTTP code and status."""

    def test_recording_not_found_returns_404(self):
        stub = _make_enhancer()
        stub._vst_handler.get_video_stream_url.side_effect = VSTRecordingNotFoundError(
            "No recording (HTTP 404)", status_code=404, response_body="not found",
            category="recording_not_found",
        )
        info = _run(stub)
        assert info['verificationResponseCode'] == '404'

    def test_overloaded_429_returns_429(self):
        stub = _make_enhancer()
        stub._vst_handler.get_video_stream_url.side_effect = VSTOverloadedError(
            "VST overloaded (HTTP 429)", status_code=429, response_body="rate limited",
            category="overloaded",
        )
        info = _run(stub)
        assert info['verificationResponseCode'] == '429'
        assert 'overloaded' in info['verificationResponseStatus'].lower()

    def test_overloaded_503_returns_503(self):
        stub = _make_enhancer()
        stub._vst_handler.get_video_stream_url.side_effect = VSTOverloadedError(
            "VST overloaded (HTTP 503)", status_code=503, response_body="busy",
            category="overloaded",
        )
        info = _run(stub)
        assert info['verificationResponseCode'] == '503'
        assert 'overloaded' in info['verificationResponseStatus'].lower()

    def test_timeout_returns_504(self):
        stub = _make_enhancer()
        stub._vst_handler.get_video_stream_url.side_effect = VSTTimeoutError(
            "VST request timed out", category="timeout",
        )
        info = _run(stub)
        assert info['verificationResponseCode'] == '504'
        assert 'timed out' in info['verificationResponseStatus'].lower()

    def test_client_error_returns_status_code(self):
        stub = _make_enhancer()
        stub._vst_handler.get_video_stream_url.side_effect = VSTClientError(
            "VST client error (HTTP 400)", status_code=400, response_body="bad request",
            category="client_error",
        )
        info = _run(stub)
        assert info['verificationResponseCode'] == '400'

    def test_missing_video_url_returns_502(self):
        stub = _make_enhancer()
        stub._vst_handler.get_video_stream_url.side_effect = VSTError(
            "videoUrl missing", status_code=200, response_body="{}", category="missing_video_url",
        )
        info = _run(stub)
        assert info['verificationResponseCode'] == '502'
        assert 'without video URL' in info['verificationResponseStatus']
