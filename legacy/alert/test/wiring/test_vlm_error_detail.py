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
Unit tests for VLM error status reporting.

Verifies that verificationResponseStatus contains human-readable, concise
error messages with root cause for all VLM error paths.
"""

import sys
import types
import logging
from unittest.mock import Mock

import pytest

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
    'vlm.vlm_client',
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
sys.modules['vlm.vlm_client'].VLMClient = Mock
sys.modules['vlm.vlm_client'].AsyncVLMRuntime = Mock


class _AsyncDispatchMixinStub:
    pass


class _AsyncExternalIOMixinStub:
    pass


class _AsyncVLMModeMixinStub:
    pass


sys.modules['handlers.async_dispatch_mixin'].AsyncDispatchMixin = _AsyncDispatchMixinStub
sys.modules['handlers.async_external_io_mixin'].AsyncExternalIOMixin = _AsyncExternalIOMixinStub
sys.modules['handlers.async_vlm_mode_mixin'].AsyncVLMModeMixin = _AsyncVLMModeMixinStub

sys.modules['vss'].VSSHandler = Mock
sys.modules['metrics'].PROMETHEUS_ENABLED = False

from enhance_alert_with_vlm import AnomalyEnhancer


def _make_enhancer():
    stub = Mock(spec=AnomalyEnhancer)
    stub.config = {
        'vst_config': {'retry_without_overlay': False},
        'vlm': {'max_retries': 0, 'model': 'test', 'dynamic_frame_count': False},
        'alert_agent': {
            'include_latency_info': False,
            'url_transform': {'enabled': False},
        },
    }
    stub.url_transform_enabled = False
    stub.include_latency_info = False
    stub._vst_handler = Mock()
    stub._vst_handler.get_video_stream_url.return_value = (
        'http://vst/clip.mp4', '2025-01-01T00:00:00Z', '2025-01-01T00:00:10Z',
    )
    # Upstream async refactor in ab_int_ga routes VST fetch through
    # ``_get_video_stream_url_with_mode`` on the enhancer itself. Stub it
    # with the same shape as _vst_handler.get_video_stream_url so the
    # existing assertions keep working.
    stub._get_video_stream_url_with_mode = Mock(return_value=(
        'http://vst/clip.mp4', '2025-01-01T00:00:00Z', '2025-01-01T00:00:10Z',
    ))
    stub.vlm_media_source_using_base64 = False
    # Upstream async refactor funnels publishes through ``*_with_mode``
    # helpers provided by the async mixins. Stub them so the SUT can
    # complete without network I/O.
    stub._publish_success_with_mode = Mock()
    stub._publish_error_with_mode = Mock()
    # Upstream also routes the actual VLM call through a ``_with_mode``
    # wrapper on the enhancer rather than calling vlm_client directly.
    # Tests below override side_effect / return_value per scenario.
    stub._analyze_video_url_with_mode = Mock()
    # Pluggable parser branch opt-out: tests in this file exercise the
    # default (verification) parse path.
    stub._pluggable_parser = None
    # All async mixin entry points the production code uses on the success
    # path — each one is a pass-through in the sync/test execution model.
    stub._process_enrichment_with_mode = Mock(return_value=None)
    stub._update_enrichment_with_mode = Mock()
    stub._sleep_retry_with_mode = Mock()
    stub._run_redis_operation_with_mode = Mock(return_value=False)
    stub.vlm_client = Mock()
    stub.vlm_client.config = {'num_frames': 10}
    stub.vlm_client.model = "nvidia/cosmos-reason2-8b"
    stub.vlm_client.base_url = "http://localhost:30082/v1"
    stub.prompt_manager = Mock()
    stub.prompt_manager.alert_config_loader = None
    stub.prompt_manager.get_prompts_for_message.return_value = ("u", "s")
    stub.vlm_enhanced_event_sink = Mock()
    stub.enrichment_processor = Mock(process=Mock(return_value=None))
    stub.redis_handler = Mock()
    stub.validate_video_url = Mock(return_value=True)
    stub._set_message_id_and_should_skip = Mock(return_value=False)
    stub._compute_fingerprint = Mock(return_value=None)
    stub._extract_root_cause = AnomalyEnhancer._extract_root_cause
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
    msg = msg or _make_msg()
    AnomalyEnhancer._process_single_message(stub, worker_id=0, message=msg)
    return msg.get('info', {})


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestExtractRootCause:
    """_extract_root_cause returns a concise root cause string."""

    def test_simple_exception(self):
        exc = ValueError("bad value")
        result = AnomalyEnhancer._extract_root_cause(exc)
        assert result == "ValueError: bad value"

    def test_chained_exception(self):
        try:
            try:
                raise ConnectionRefusedError("port 8080")
            except ConnectionRefusedError as inner:
                raise TimeoutError("request timed out") from inner
        except TimeoutError as outer:
            result = AnomalyEnhancer._extract_root_cause(outer)
            assert "ConnectionRefusedError" in result
            assert "port 8080" in result

    def test_truncation(self):
        exc = ValueError("x" * 300)
        result = AnomalyEnhancer._extract_root_cause(exc, max_len=50)
        assert len(result.split(": ", 1)[1]) <= 50


# ---------------------------------------------------------------------------
# VLM error handlers: verificationResponseStatus format
# ---------------------------------------------------------------------------

class TestVLMErrorStatus:
    """VLM error handlers produce human-readable verificationResponseStatus."""

    def test_timeout_status(self):
        from openai import APITimeoutError
        stub = _make_enhancer()
        stub._analyze_video_url_with_mode.side_effect = APITimeoutError(request=Mock())
        info = _run(stub)

        assert info['verificationResponseCode'] == '504'
        assert info['verificationResponseStatus'].startswith('VLM request timed out, ')
        assert 'verificationErrorDetail' not in info

    def test_connection_error_status(self):
        from openai import APIConnectionError
        stub = _make_enhancer()
        stub._analyze_video_url_with_mode.side_effect = APIConnectionError(request=Mock())
        info = _run(stub)

        assert info['verificationResponseCode'] == '503'
        assert info['verificationResponseStatus'].startswith('Failed to connect to VLM service, ')
        assert 'verificationErrorDetail' not in info

    def test_internal_server_error_status(self):
        from openai import InternalServerError
        stub = _make_enhancer()
        stub._analyze_video_url_with_mode.side_effect = InternalServerError(
            message="CUDA OOM", response=Mock(), body=None,
        )
        info = _run(stub)

        assert info['verificationResponseCode'] == '500'
        assert info['verificationResponseStatus'].startswith('VLM service internal error, ')
        assert 'InternalServerError' in info['verificationResponseStatus']
        assert 'verificationErrorDetail' not in info

    def test_unprocessable_entity_status(self):
        from openai import UnprocessableEntityError
        stub = _make_enhancer()
        stub._analyze_video_url_with_mode.side_effect = UnprocessableEntityError(
            message="Video too large", response=Mock(), body=None,
        )
        info = _run(stub)

        assert info['verificationResponseCode'] == '422'
        assert info['verificationResponseStatus'].startswith('Invalid VLM request payload, ')
        assert 'UnprocessableEntityError' in info['verificationResponseStatus']
        assert 'verificationErrorDetail' not in info

    def test_parse_error_empty_response(self):
        """Empty VLM response produces human-readable status."""
        stub = _make_enhancer()
        mock_resp = Mock()
        mock_resp.content = ""
        stub._analyze_video_url_with_mode.return_value = mock_resp
        info = _run(stub)

        assert info['verificationResponseCode'] == '500'
        assert 'VLM returned an empty response' in info['verificationResponseStatus']
        assert 'model produced no output' in info['verificationResponseStatus']
        assert 'verificationErrorDetail' not in info

    def test_parse_error_format_mismatch(self):
        """Non YES/NO VLM response produces human-readable status with raw excerpt."""
        import unittest.mock as um
        stub = _make_enhancer()
        mock_resp = Mock()
        mock_resp.content = "The person appears to be wearing a hardhat"
        stub._analyze_video_url_with_mode.return_value = mock_resp
        with um.patch('enhance_alert_with_vlm.VLMResponse') as mock_vlm:
            mock_vlm.model_validate_text.side_effect = ValueError(
                "Response not in expected format"
            )
            info = _run(stub)

        assert info['verificationResponseCode'] == '500'
        assert 'VLM response not in expected YES/NO format' in info['verificationResponseStatus']
        assert 'free-form text' in info['verificationResponseStatus']
        assert 'verificationErrorDetail' not in info

    def test_parse_error_validation_failed(self):
        """Validation error produces human-readable status with raw excerpt."""
        import unittest.mock as um
        stub = _make_enhancer()
        mock_resp = Mock()
        mock_resp.content = "<think>analyzing...</think>maybe"
        stub._analyze_video_url_with_mode.return_value = mock_resp
        with um.patch('enhance_alert_with_vlm.VLMResponse') as mock_vlm:
            mock_vlm.model_validate_text.side_effect = RuntimeError("unexpected failure")
            info = _run(stub)

        assert info['verificationResponseCode'] == '500'
        assert 'VLM response failed validation' in info['verificationResponseStatus']
        assert 'verificationErrorDetail' not in info

    def test_generic_error_status(self):
        """Errors outside the retry loop reach the outer generic except handler."""
        stub = _make_enhancer()
        stub.vlm_client.config = Mock(side_effect=RuntimeError("config broken"))
        stub.vlm_client.config.get = Mock(side_effect=RuntimeError("config broken"))
        info = _run(stub)

        assert info['verificationResponseCode'] == '500'
        assert info['verificationResponseStatus'].startswith('Video verification could not be completed, ')
        assert 'RuntimeError' in info['verificationResponseStatus']
        assert 'verificationErrorDetail' not in info

    def test_no_error_on_success(self):
        """Successful VLM response has OK status."""
        stub = _make_enhancer()
        mock_response = Mock()
        mock_response.content = "YES"
        stub._analyze_video_url_with_mode.return_value = mock_response
        info = _run(stub)

        assert info.get('verificationResponseCode') == '200'
        assert info.get('verificationResponseStatus') == 'OK'
        assert 'verificationErrorDetail' not in info
