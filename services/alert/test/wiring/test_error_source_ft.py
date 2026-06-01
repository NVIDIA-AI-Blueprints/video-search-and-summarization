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

"""Structured ``info.errorSource``.

Before
------
Error classification relied on substring-matching
``verificationResponseStatus`` (e.g. grep for ``"Pluggable parser failed"``
or ``"VLM service timeout"``). That is:

* Brittle — any status-text wording change breaks downstream consumers.
* Locale-sensitive — grep on English status strings does not survive
  translation / reformatting.
* Ambiguous — pluggable-parser ``TypeError`` and VLM-schema ``TypeError``
  both look alike after one ``extra["error"]`` roundtrip.

After
-----
Every error path sets a structured bucket (``errorSource``) — one of:

* ``pluggable_parser`` — custom ``parse()`` raised / returned non-dict.
* ``vlm_schema`` — default verification path could not coerce VLM text
  into :class:`VLMResponse`.
* ``vlm_api`` — the VLM endpoint itself failed (timeout, 5xx, 4xx).
* ``media_download`` — Mode-3 media fetch failed before VLM was called.

Success paths leave ``info["errorSource"]`` absent (not empty string —
empty-string leakage would pollute ES maps and defeat the filter).

These tests lock each of the six error sites identified in the errorSource
audit plus the success-path "no leak" behaviour.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import types
from unittest.mock import Mock, patch

import pytest

from openai import APITimeoutError, APIConnectionError, InternalServerError


# ---------------------------------------------------------------------------
# Shared stubs for the orchestrator (mirrors
# ``test_pluggable_parser_publish_routing.py``).
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
from models.pluggable_parser_runtime import (  # noqa: E402
    ERROR_SOURCE_MEDIA_DOWNLOAD,
    ERROR_SOURCE_PLUGGABLE_PARSER,
    ERROR_SOURCE_VLM_API,
    ERROR_SOURCE_VLM_SCHEMA,
)


# ---------------------------------------------------------------------------
# Orchestrator stub builder (local-file + VST paths)
# ---------------------------------------------------------------------------


def _make_enhancer_stub() -> Mock:
    import threading

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
    stub._pluggable_parser = None

    mock_vlm_response = Mock()
    mock_vlm_response.content = '{"malformed": "data"}'
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
    stub._extract_root_cause = AnomalyEnhancer._extract_root_cause
    return stub


def _vst_msg():
    return {
        'sensorId': 'cam-1',
        'category': 'loitering',
        'timestamp': '2025-09-10T11:16:31Z',
        'end': '2025-09-10T11:16:33Z',
        'objectIds': [],
    }


# ---------------------------------------------------------------------------
# Orchestrator tests (Site 1-4: schema-fail × 2, api-fail × 1, download × 1)
# ---------------------------------------------------------------------------


class TestSchemaFailSetsVlmSchema:
    """Sites 1 & 2 — default-path schema parse failure sets ``vlm_schema``."""

    def test_local_file_schema_fail_sets_vlm_schema(self):
        stub = _make_enhancer_stub()
        vlm_resp = Mock()
        vlm_resp.content = 'not-a-valid-verdict'
        stub.vlm_client.analyze_local_video = Mock(return_value=vlm_resp)
        msg = {'id': 'local-1', 'info': {}, 'category': 'ppe'}

        # Force the default-path schema parser to raise, bypassing any
        # lenient heuristic paths inside ``model_validate_text``.
        with patch('os.path.isfile', return_value=True), \
             patch(
                 'models.responses.VLMResponse.model_validate_text',
                 side_effect=ValueError("not in expected format"),
             ):
            AnomalyEnhancer._evaluate_local_video(
                stub,
                worker_id=0,
                message=msg,
                video_path='/tmp/fake.mp4',
                user_prompt='u',
                system_prompt='s',
            )

        assert msg['info']['errorSource'] == ERROR_SOURCE_VLM_SCHEMA
        assert msg['info']['verdict'] == 'verification-failed'

    def test_vst_schema_fail_sets_vlm_schema(self):
        stub = _make_enhancer_stub()
        msg = _vst_msg()

        with patch(
            'models.responses.VLMResponse.model_validate_text',
            side_effect=ValueError("not in expected format"),
        ):
            AnomalyEnhancer._process_single_message(stub, worker_id=0, message=msg)

        info = msg['info']
        assert info.get('errorSource') == ERROR_SOURCE_VLM_SCHEMA, (
            f"VST schema fail must set vlm_schema; got {info.get('errorSource')!r} "
            f"with status={info.get('verificationResponseStatus')!r}"
        )


class TestVLMAPIErrorsSetVlmApi:
    """Site 3 — VLM transport/server errors bucket as ``vlm_api``."""

    @pytest.mark.parametrize("exc_cls,expected_code", [
        (APITimeoutError, '504'),
        (APIConnectionError, '503'),
        (InternalServerError, '500'),
    ])
    def test_vlm_api_errors_set_vlm_api(self, exc_cls, expected_code):
        stub = _make_enhancer_stub()

        def _make_exc():
            if exc_cls is APITimeoutError:
                return APITimeoutError(request=Mock())
            if exc_cls is APIConnectionError:
                return APIConnectionError(request=Mock())
            if exc_cls is InternalServerError:
                return InternalServerError(
                    message="boom",
                    response=Mock(status_code=500),
                    body=None,
                )
            raise AssertionError("unreachable")

        stub._analyze_video_url_with_mode = Mock(side_effect=_make_exc())
        msg = _vst_msg()

        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=msg)

        info = msg['info']
        assert info['errorSource'] == ERROR_SOURCE_VLM_API, (
            f"VLM {exc_cls.__name__} must set vlm_api; got "
            f"{info.get('errorSource')!r}"
        )
        assert info['verificationResponseCode'] == expected_code


class TestVSTDownloadSetsMediaDownload:
    """Site 4 — VST video-URL retrieval failure buckets as ``media_download``.
    """

    def test_vst_url_retrieval_failure_sets_media_download(self):
        stub = _make_enhancer_stub()
        stub._get_video_stream_url_with_mode = Mock(side_effect=Exception("VST down"))

        msg = _vst_msg()
        AnomalyEnhancer._process_single_message(stub, worker_id=0, message=msg)

        info = msg['info']
        assert info['errorSource'] == ERROR_SOURCE_MEDIA_DOWNLOAD


# ---------------------------------------------------------------------------
# Mode-3 tests (Site 5: pluggable_parser; Site 6: media_download / vlm_api)
# ---------------------------------------------------------------------------


def _load_direct_media_handler():
    here = os.path.dirname(os.path.abspath(__file__))
    handler_path = os.path.normpath(
        os.path.join(here, "..", "handlers", "direct_media", "direct_media_handler.py")
    )
    downloader_path = os.path.normpath(
        os.path.join(here, "..", "handlers", "direct_media", "media_downloader.py")
    )

    pkg_name = "_errsrc_dmh_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [os.path.dirname(handler_path)]
        sys.modules[pkg_name] = pkg

        dl_spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.media_downloader", downloader_path
        )
        dl_mod = importlib.util.module_from_spec(dl_spec)
        sys.modules[f"{pkg_name}.media_downloader"] = dl_mod
        dl_spec.loader.exec_module(dl_mod)

        h_spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.direct_media_handler", handler_path
        )
        h_mod = importlib.util.module_from_spec(h_spec)
        sys.modules[f"{pkg_name}.direct_media_handler"] = h_mod
        h_spec.loader.exec_module(h_mod)

    return sys.modules[f"{pkg_name}.direct_media_handler"].DirectMediaHandler


DirectMediaHandler = _load_direct_media_handler()


class _ExplodingParser:
    def parse(self, raw_response: str) -> dict:
        raise ValueError("parser blew up")


def _mode3_msg():
    return {
        'id': 'm3',
        'sensorId': 'cam-3',
        'category': 'ppe',
        'info': {'media_urls': ['https://cdn/a.jpg']},
    }


def _make_mode3_handler(parser=None):
    return DirectMediaHandler(
        vlm_client=Mock(),
        vlm_enhanced_event_sink=Mock(),
        config={
            "alert_agent": {"media_download": {"enabled": True, "use_verdict": False}},
            "vlm": {"model": "m"},
        },
        pluggable_parser=parser,
    )


class TestMode3ParserFailureSetsPluggableParser:
    """Site 5 — custom parser raising inside Mode-3 sets
    ``pluggable_parser`` both on ``info`` and on the sink payload."""

    def test_info_error_source_is_pluggable_parser(self):
        handler = _make_mode3_handler(parser=_ExplodingParser())
        msg = _mode3_msg()

        handler._publish_success(
            msg, "u", "s", "garbage", "images",
        )

        assert msg['info']['errorSource'] == ERROR_SOURCE_PLUGGABLE_PARSER

    def test_sink_payload_uses_error_source_key(self):
        """The ``publish_error`` payload must expose the same bucket under
        the ``errorSource`` key (renamed from the legacy ``source`` key to
        harmonise with ``info["errorSource"]``)."""
        handler = _make_mode3_handler(parser=_ExplodingParser())
        msg = _mode3_msg()

        handler._publish_success(
            msg, "u", "s", "garbage", "images",
        )

        call = handler.vlm_enhanced_event_sink.publish_error.call_args
        payload = call.args[3]
        assert payload["errorSource"] == ERROR_SOURCE_PLUGGABLE_PARSER
        assert "source" not in payload, (
            "Contract: legacy 'source' key must not be dual-written; "
            "sink consumers should key on 'errorSource' only"
        )


class TestMode3DownloadFailureSetsMediaDownload:
    """Site 6 — Mode-3 download failure / bad input buckets as
    ``media_download``; VLM exceptions bucket as ``vlm_api``."""

    def test_missing_media_urls_sets_media_download(self):
        handler = _make_mode3_handler()
        msg = _mode3_msg()

        handler.evaluate(
            worker_id=0,
            message=msg,
            info_block={'media_type': 'image', 'media_urls': []},
            user_prompt='u',
            system_prompt='s',
        )

        assert msg['info']['errorSource'] == ERROR_SOURCE_MEDIA_DOWNLOAD

    def test_vlm_api_error_sets_vlm_api(self):
        handler = _make_mode3_handler()
        handler.vlm_client.analyze_multiple_image_urls = Mock(
            side_effect=APITimeoutError(request=Mock())
        )
        msg = _mode3_msg()

        handler._evaluate_images(
            worker_id=0,
            message=msg,
            media_urls=['https://cdn/a.jpg'],
            user_prompt='u',
            system_prompt='s',
        )

        assert msg['info']['errorSource'] == ERROR_SOURCE_VLM_API


# ---------------------------------------------------------------------------
# Success-path no-leak: errorSource must NOT pollute successful events with
# an empty ``errorSource`` field.
# ---------------------------------------------------------------------------


class TestSuccessPathNoErrorSourceLeak:
    """Successful paths (VLM returns 200 and parser succeeds) must not
    emit ``info["errorSource"]`` at all — neither as ``None`` nor as an
    empty string. The empty-string leak would defeat downstream filters
    that rely on ``exists(info.errorSource)`` semantics."""

    def test_pluggable_success_has_no_error_source_key(self):
        from models.pluggable_parser_runtime import apply_pluggable_parser_output

        msg = {"info": {}}
        apply_pluggable_parser_output(
            msg,
            {"label": "ok"},
            video_source="src",
        )
        assert "errorSource" not in msg["info"]

    def test_default_path_success_has_no_error_source_key(self):
        from models.responses import AlertBridgeResponse, merge_info_with_response

        msg = {"info": {}}
        merge_info_with_response(
            msg,
            AlertBridgeResponse(
                verification_response_code=200,
                verification_response_status="OK",
            ),
        )
        assert "errorSource" not in msg["info"]
