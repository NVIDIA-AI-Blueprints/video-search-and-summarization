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

"""Regression tests for pluggable-parser failure publish routing.

A prior review flagged that pluggable-parser exceptions on
both the VST path (``_process_single_message``) and the local-file path
(``_evaluate_local_video``) were being dispatched through
``_publish_success_with_mode`` instead of ``_publish_error_with_mode``.
The error-shaped ``info`` block was correct, but the sink-level
operation was mislabeled — which skews sink metrics, alerting, and
operational triage.

These tests lock the fix: when the pluggable parser raises, the final
sink call must be ``publish_error``, never ``publish_success``.
"""

from __future__ import annotations

import logging
import sys
import threading
import types
from unittest.mock import Mock

import pytest

# ---------------------------------------------------------------------------
# Heavy-module stubs (same shape as test_vst_error_reporting.py)
# ---------------------------------------------------------------------------

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


class _DispatchStub: pass
class _IOStub: pass
class _VLMStub: pass


sys.modules['handlers.async_dispatch_mixin'].AsyncDispatchMixin = _DispatchStub
sys.modules['handlers.async_external_io_mixin'].AsyncExternalIOMixin = _IOStub
sys.modules['handlers.async_vlm_mode_mixin'].AsyncVLMModeMixin = _VLMStub
sys.modules['vlm.vlm_client'].VLMClient = Mock
sys.modules['vlm.vlm_client'].AsyncVLMRuntime = Mock

from enhance_alert_with_vlm import AnomalyEnhancer  # noqa: E402


# ---------------------------------------------------------------------------
# Stub builder
# ---------------------------------------------------------------------------


def _make_enhancer() -> Mock:
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
    stub._vst_handler.get_video_stream_url = Mock(
        return_value=("rtsp://cam/01", "2025-09-10T11:16:31Z", "2025-09-10T11:16:33Z")
    )
    stub.vlm_client = Mock()
    stub.vlm_client.config = {'num_frames': 10}
    stub.vlm_client.model = 'test-model'
    stub.vlm_client.base_url = 'http://vlm'
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
    stub._process_enrichment_with_mode = Mock(return_value=None)
    stub._update_enrichment_with_mode = Mock(return_value=None)
    stub._sleep_retry_with_mode = Mock()
    stub._run_redis_operation_with_mode = Mock(return_value=None)

    mock_vlm_response = Mock()
    mock_vlm_response.content = '{"malformed": "data"}'
    # Per-alert-type VLM config overrides introduced per-alert-type VLM config merging.
    # _process_single_message now calls self._get_merged_vlm_config(category)
    # instead of reading self.config['vlm'] directly. Return the test's
    # global vlm config so ``max_retries`` / ``num_frames`` stay real ints
    # (otherwise arithmetic ``max_retries + 1`` fails with Mock + int).
    stub._get_merged_vlm_config = Mock(return_value=stub.config['vlm'])

    stub._analyze_video_url_with_mode = Mock(return_value=mock_vlm_response)
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
    return stub


class _CrashingParser:
    """Pluggable parser that always raises — simulates a buggy custom
    parser shipped by a downstream team."""

    def parse(self, raw):
        raise RuntimeError("boom — classifier regex failed on this payload")


class _GoodParser:
    """Pluggable parser that succeeds — baseline for routing symmetry
    (success must still route through publish_success)."""

    def parse(self, raw):
        return {"label": "no-spotter", "severity": "high"}


def _vst_msg():
    return {
        'sensorId': 'cam-1',
        'category': 'loitering',
        'timestamp': '2025-09-10T11:16:31Z',
        'end': '2025-09-10T11:16:33Z',
        'objectIds': [],
    }


def _local_msg():
    return {
        'sensorId': 'cam-local',
        'category': 'ppe',
        'videoPath': '/tmp/fake.mp4',
        'id': 'local-1',
        'info': {
            'sensorId': 'cam-local',
            'category': 'ppe',
            'primaryObjectId': '1',
        },
    }


# ---------------------------------------------------------------------------
# VST path (_process_single_message)
# ---------------------------------------------------------------------------


class TestVSTPathPluggableParserRouting:
    """VST path: ensure parse() exceptions go through
    publish_error, not publish_success."""

    def test_parser_crash_routes_to_publish_error_not_success(self):
        stub = _make_enhancer()
        stub._pluggable_parser = _CrashingParser()

        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=_vst_msg())

        stub.vlm_enhanced_event_sink.publish_error.assert_called_once()
        stub.vlm_enhanced_event_sink.publish_success.assert_not_called()

    def test_parser_crash_publishes_error_payload_shape(self):
        """publish_error is called with an empty dict (the current
        contract for parser-error events)."""
        stub = _make_enhancer()
        stub._pluggable_parser = _CrashingParser()

        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=_vst_msg())

        args, _ = stub.vlm_enhanced_event_sink.publish_error.call_args
        message, _user_prompt, _system_prompt, error_payload = args
        assert error_payload == {}
        # info block still carries the parser-error diagnostics.
        assert message['info']['verificationResponseCode'] == '500'
        assert message['info']['verdict'] == 'verification-failed'
        assert message['info']['verificationResponseStatus'].startswith(
            "Pluggable parser failed"
        )

    def test_successful_parser_still_routes_to_publish_success(self):
        """Symmetry check — the fix must not regress the happy path."""
        stub = _make_enhancer()
        stub._pluggable_parser = _GoodParser()

        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=_vst_msg())

        stub.vlm_enhanced_event_sink.publish_success.assert_called_once()
        stub.vlm_enhanced_event_sink.publish_error.assert_not_called()

    def test_parser_returning_non_dict_also_routes_to_publish_error(self):
        """TypeError raised by the orchestrator for non-dict parser
        output flows through the same error branch as parse()
        exceptions."""

        class _ReturnsList:
            def parse(self, raw):
                return ["not", "a", "dict"]

        stub = _make_enhancer()
        stub._pluggable_parser = _ReturnsList()

        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=_vst_msg())

        stub.vlm_enhanced_event_sink.publish_error.assert_called_once()
        stub.vlm_enhanced_event_sink.publish_success.assert_not_called()


# ---------------------------------------------------------------------------
# Local-file path (_evaluate_local_video)
# ---------------------------------------------------------------------------


class TestLocalPathPluggableParserRouting:
    """Local path: ensure parse() exceptions go
    through publish_error, not publish_success."""

    def _local_vlm_response(self, content="{}"):
        resp = Mock()
        resp.content = content
        return resp

    def _call(self, stub, msg=None):
        import os
        import unittest.mock as um
        msg = msg or _local_msg()
        with um.patch.object(os.path, 'isfile', return_value=True):
            AnomalyEnhancer._evaluate_local_video(
                stub,
                worker_id=0,
                message=msg,
                video_path='/tmp/fake.mp4',
                user_prompt='u',
                system_prompt='s',
            )
        return msg

    def test_parser_crash_routes_to_publish_error_not_success(self):
        stub = _make_enhancer()
        stub._pluggable_parser = _CrashingParser()
        stub.vlm_client.analyze_local_video = Mock(
            return_value=self._local_vlm_response()
        )

        self._call(stub)

        stub.vlm_enhanced_event_sink.publish_error.assert_called_once()
        stub.vlm_enhanced_event_sink.publish_success.assert_not_called()

    def test_parser_crash_preserves_error_info_block(self):
        stub = _make_enhancer()
        stub._pluggable_parser = _CrashingParser()
        stub.vlm_client.analyze_local_video = Mock(
            return_value=self._local_vlm_response()
        )

        msg = self._call(stub)

        assert msg['info']['verificationResponseCode'] == '500'
        assert msg['info']['verdict'] == 'verification-failed'
        assert msg['info']['verificationResponseStatus'].startswith(
            "Pluggable parser failed"
        )

    def test_successful_parser_still_routes_to_publish_success(self):
        stub = _make_enhancer()
        stub._pluggable_parser = _GoodParser()
        stub.vlm_client.analyze_local_video = Mock(
            return_value=self._local_vlm_response()
        )

        self._call(stub)

        stub.vlm_enhanced_event_sink.publish_success.assert_called_once()
        stub.vlm_enhanced_event_sink.publish_error.assert_not_called()

    def test_default_path_without_parser_happy_path_routes_to_publish_success(self):
        """Happy path sanity: no pluggable parser + valid YES verdict
        goes through publish_success."""
        stub = _make_enhancer()
        stub._pluggable_parser = None
        stub.vlm_client.analyze_local_video = Mock(
            return_value=self._local_vlm_response(content="YES")
        )

        self._call(stub)

        stub.vlm_enhanced_event_sink.publish_success.assert_called_once()
        stub.vlm_enhanced_event_sink.publish_error.assert_not_called()


# ---------------------------------------------------------------------------
# VLM-API failure never reaches the parser.
# When the VLM itself errors out (timeout / 5xx / connection refused) the
# pluggable parser must NOT be invoked — the error must surface as a
# VLM-service failure with its own distinct status, not as a parser
# failure.  Mis-attribution confuses on-call: "is the VLM down or is the
# parser buggy?".
# ---------------------------------------------------------------------------


class _ParserThatMustNotBeCalled:
    """Fails loud if ever invoked — proves the VLM error path skips parse()."""

    def __init__(self):
        self.called = False

    def parse(self, raw_response):
        self.called = True
        raise AssertionError(
            "parse() MUST NOT be invoked when the VLM call itself failed — "
            "VLM errors have their own status and must not be attributed to "
            "the custom parser."
        )


class TestVLMFailureSkipsPluggableParser:
    """Lock VLM-vs-parser error attribution."""

    def _call_local(self, stub, msg=None):
        import os
        import unittest.mock as um
        msg = msg or _local_msg()
        with um.patch.object(os.path, 'isfile', return_value=True):
            AnomalyEnhancer._evaluate_local_video(
                stub,
                worker_id=0,
                message=msg,
                video_path='/tmp/fake.mp4',
                user_prompt='u',
                system_prompt='s',
            )
        return msg

    @staticmethod
    def _expect_vlm_error_to_propagate(stub, *, call_local_fn):
        """``_evaluate_local_video`` does not catch VLM-layer exceptions —
        they bubble to the outer dispatcher loop (which logs + skips).
        The contract we are locking is simpler: the parser MUST NOT be
        invoked and the exception must surface unchanged (never
        re-labelled as a parser error).
        """
        with pytest.raises(Exception) as excinfo:
            call_local_fn(stub)
        return excinfo

    def test_vlm_timeout_does_not_invoke_pluggable_parser(self):
        from openai import APITimeoutError

        parser = _ParserThatMustNotBeCalled()
        stub = _make_enhancer()
        stub._pluggable_parser = parser
        stub.vlm_client.analyze_local_video = Mock(
            side_effect=APITimeoutError(request=Mock())
        )

        excinfo = self._expect_vlm_error_to_propagate(
            stub, call_local_fn=self._call_local
        )

        assert parser.called is False, (
            "Pluggable parser was called on a VLM-service failure — this "
            "mis-attributes on-call alerts."
        )
        assert isinstance(excinfo.value, APITimeoutError)

    def test_vlm_connection_error_does_not_invoke_pluggable_parser(self):
        from openai import APIConnectionError

        parser = _ParserThatMustNotBeCalled()
        stub = _make_enhancer()
        stub._pluggable_parser = parser
        stub.vlm_client.analyze_local_video = Mock(
            side_effect=APIConnectionError(request=Mock())
        )

        excinfo = self._expect_vlm_error_to_propagate(
            stub, call_local_fn=self._call_local
        )

        assert parser.called is False
        assert isinstance(excinfo.value, APIConnectionError)

    def test_vlm_internal_server_error_does_not_invoke_pluggable_parser(self):
        from openai import InternalServerError

        parser = _ParserThatMustNotBeCalled()
        stub = _make_enhancer()
        stub._pluggable_parser = parser

        resp = Mock()
        resp.status_code = 500
        resp.request = Mock()
        stub.vlm_client.analyze_local_video = Mock(
            side_effect=InternalServerError("boom", response=resp, body=None)
        )

        excinfo = self._expect_vlm_error_to_propagate(
            stub, call_local_fn=self._call_local
        )

        assert parser.called is False
        assert isinstance(excinfo.value, InternalServerError)

    def test_vlm_generic_exception_does_not_invoke_pluggable_parser(self):
        """Any VLM-layer exception — not just ``openai`` subtypes — must
        skip the parser entirely."""
        parser = _ParserThatMustNotBeCalled()
        stub = _make_enhancer()
        stub._pluggable_parser = parser
        stub.vlm_client.analyze_local_video = Mock(
            side_effect=RuntimeError("vlm runtime died")
        )

        excinfo = self._expect_vlm_error_to_propagate(
            stub, call_local_fn=self._call_local
        )

        assert parser.called is False
        assert isinstance(excinfo.value, RuntimeError)
        assert "vlm runtime died" in str(excinfo.value)
