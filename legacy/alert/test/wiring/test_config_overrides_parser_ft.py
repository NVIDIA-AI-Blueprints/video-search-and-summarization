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

"""Regression lock: per-category ``config_overrides``
must reach both the VLM *request* and the VLM *response parser*.

Findings covered
----------------
* The local-file and VST response-parse sites used to read
  ``self.config.get('vlm', {})`` directly, so a per-category override of
  ``model`` / ``response_format`` / ``json_parser`` affected the outbound
  request but silently fell back to the global config when parsing the
  response.
* The direct-media image path (single-URL, multi-URL, and
  base64 upload) did not thread ``config_overrides`` into
  ``analyze_multiple_image_urls`` / ``analyze_multiple_images``. The
  video path had been threading it through since per-alert-type VLM config overrides.

These tests drive the fix paths with a synthetic VLM stub and assert
that the effective parser args / VLM-client args come from the
``config_overrides`` dict, not the global config.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
import types
from unittest.mock import Mock, patch

import pytest


# ---------------------------------------------------------------------------
# Heavy-module stubs mirroring test_pluggable_parser_publish_routing.py.
# These let us import ``enhance_alert_with_vlm`` without pulling in the
# real event-bridge / redis / prometheus clients.
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
# Parser uses config_overrides on the local-file path
# ---------------------------------------------------------------------------


def _make_local_enhancer(global_vlm: dict) -> Mock:
    stub = Mock(spec=AnomalyEnhancer)
    stub.config = {
        'vlm': global_vlm,
        'alert_agent': {'include_latency_info': False},
    }
    stub.include_latency_info = False
    stub.vlm_client = Mock()
    stub.vlm_client.model = global_vlm.get('model', '')
    stub.vlm_client.base_url = 'http://vlm'
    stub.prompt_manager = Mock()
    stub.prompt_manager.get_prompts_for_message.return_value = ("u", "s")
    stub.vlm_enhanced_event_sink = Mock()
    stub._pluggable_parser = None
    stub._publish_success_with_mode = Mock()
    stub._publish_error_with_mode = Mock()

    mock_vlm_response = Mock()
    mock_vlm_response.content = 'YES'  # valid CR1/CR2 bare verdict
    stub.vlm_client.analyze_local_video = Mock(return_value=mock_vlm_response)
    return stub


class TestR31LocalFileParserUsesConfigOverrides:
    """Local-file response parser must honour per-category overrides."""

    def test_model_override_reaches_parser(self):
        """When a per-category override sets ``model``, the parser is called
        with that model name (not the global ``self.config['vlm']['model']``).
        """
        global_vlm = {'model': 'global-model', 'response_format': 'auto'}
        stub = _make_local_enhancer(global_vlm)

        captured = {}

        def _capture(text, *, model_name='', response_format='auto', json_config=None):
            captured['text'] = text
            captured['model_name'] = model_name
            captured['response_format'] = response_format
            captured['json_config'] = json_config
            return Mock(
                reasoning='YES',
                verdict='confirmed',
                description='',
                extra={},
            )

        with patch('os.path.isfile', return_value=True), \
             patch('models.responses.VLMResponse.model_validate_text', side_effect=_capture):
            AnomalyEnhancer._evaluate_local_video(
                stub,
                worker_id=0,
                message={'id': 'm1', 'info': {}, 'category': 'ppe'},
                video_path='/tmp/x.mp4',
                user_prompt='u',
                system_prompt='s',
                config_overrides={
                    'model': 'override-model',
                    'response_format': 'cosmos-reason',
                },
            )

        assert captured['model_name'] == 'override-model', (
            "parser must see the per-category model, not the global one"
        )
        assert captured['response_format'] == 'cosmos-reason'

    def test_response_format_override_reaches_parser(self):
        """Per-category ``response_format: json`` must reach the parser."""
        global_vlm = {'model': 'm', 'response_format': 'cosmos-reason'}
        stub = _make_local_enhancer(global_vlm)

        captured = {}

        def _capture(text, *, model_name='', response_format='auto', json_config=None):
            captured['response_format'] = response_format
            captured['json_config'] = json_config
            return Mock(reasoning='', verdict='confirmed', description='', extra={})

        with patch('os.path.isfile', return_value=True), \
             patch('models.responses.VLMResponse.model_validate_text', side_effect=_capture):
            AnomalyEnhancer._evaluate_local_video(
                stub,
                worker_id=0,
                message={'id': 'm2', 'info': {}, 'category': 'ppe'},
                video_path='/tmp/y.mp4',
                user_prompt='u',
                system_prompt='s',
                config_overrides={
                    'response_format': 'json',
                    'json_parser': {'verdict_field': 'label'},
                },
            )

        assert captured['response_format'] == 'json'
        assert captured['json_config'] == {'verdict_field': 'label'}

    def test_no_overrides_falls_back_to_global(self):
        """When ``config_overrides`` is None, the global ``vlm`` config wins
        (backwards-compat — unchanged behaviour for non-opt-in deployments).
        """
        global_vlm = {'model': 'global-model', 'response_format': 'auto'}
        stub = _make_local_enhancer(global_vlm)

        captured = {}

        def _capture(text, *, model_name='', response_format='auto', json_config=None):
            captured['model_name'] = model_name
            captured['response_format'] = response_format
            return Mock(reasoning='', verdict='confirmed', description='', extra={})

        with patch('os.path.isfile', return_value=True), \
             patch('models.responses.VLMResponse.model_validate_text', side_effect=_capture):
            AnomalyEnhancer._evaluate_local_video(
                stub,
                worker_id=0,
                message={'id': 'm3', 'info': {}, 'category': 'ppe'},
                video_path='/tmp/z.mp4',
                user_prompt='u',
                system_prompt='s',
                config_overrides=None,
            )

        assert captured['model_name'] == 'global-model'
        assert captured['response_format'] == 'auto'


# ---------------------------------------------------------------------------
# Direct-media image path threads config_overrides into vlm_client
# ---------------------------------------------------------------------------


def _load_direct_media_handler():
    """Load ``DirectMediaHandler`` from disk, bypassing the stub packages
    installed above for ``handlers.direct_media``."""
    here = os.path.dirname(os.path.abspath(__file__))
    handler_path = os.path.normpath(
        os.path.join(here, "..", "handlers", "direct_media", "direct_media_handler.py")
    )
    downloader_path = os.path.normpath(
        os.path.join(here, "..", "handlers", "direct_media", "media_downloader.py")
    )

    pkg_name = "_cfgoverride_dmh_pkg"
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


def _make_mode3_handler(use_base64: bool = False):
    vlm_client = Mock()
    vlm_response = Mock()
    vlm_response.content = '{"label":"a"}'
    vlm_client.analyze_multiple_image_urls = Mock(return_value=vlm_response)
    vlm_client.analyze_multiple_images = Mock(return_value=vlm_response)

    sink = Mock()
    handler = DirectMediaHandler(
        vlm_client=vlm_client,
        vlm_enhanced_event_sink=sink,
        config={
            "alert_agent": {
                "media_download": {
                    "enabled": True,
                    "use_verdict": False,
                },
            },
            # ``vlm_media_source_using_base64`` lives under ``vlm`` in
            # production config (reused across VLM code paths), not under
            # ``alert_agent.media_download``.
            "vlm": {
                "model": "nvidia/cosmos-reason2-8b",
                "vlm_media_source_using_base64": use_base64,
            },
        },
        pluggable_parser=None,
    )
    return handler, vlm_client


class TestR32ImageUrlPathThreadsConfigOverrides:
    """Single/multi image-URL VLM call receives ``config_overrides``."""

    def test_analyze_multiple_image_urls_receives_overrides(self):
        handler, vlm_client = _make_mode3_handler(use_base64=False)
        overrides = {
            'num_frames': 4,
            'max_tokens': 512,
            'temperature': 0.7,
        }

        handler._evaluate_images(
            worker_id=0,
            message={'id': 'm', 'info': {'media_urls': ['https://cdn/a.jpg']}, 'sensorId': 's', 'category': 'ppe'},
            media_urls=['https://cdn/a.jpg'],
            user_prompt='u',
            system_prompt='s',
            config_overrides=overrides,
        )

        vlm_client.analyze_multiple_image_urls.assert_called_once()
        kwargs = vlm_client.analyze_multiple_image_urls.call_args.kwargs
        assert kwargs.get('config_overrides') == overrides, (
            "Mode-3 image-URL path must forward config_overrides to the VLM "
            "client (was ignored, per-category VLM settings lost)"
        )

    def test_evaluate_call_propagates_from_evaluate(self):
        """The top-level ``evaluate()`` must thread overrides through to the
        image branch (not just the video branch)."""
        handler, vlm_client = _make_mode3_handler(use_base64=False)
        overrides = {'num_frames': 2}

        handler.evaluate(
            worker_id=0,
            message={'id': 'm2', 'info': {}, 'sensorId': 's', 'category': 'ppe'},
            info_block={
                'media_type': 'image',
                'media_urls': ['https://cdn/b.jpg'],
            },
            user_prompt='u',
            system_prompt='s',
            config_overrides=overrides,
        )

        vlm_client.analyze_multiple_image_urls.assert_called_once()
        assert (
            vlm_client.analyze_multiple_image_urls.call_args.kwargs.get('config_overrides')
            == overrides
        )


class TestR32ImageBase64PathThreadsConfigOverrides:
    """Base64 upload path (``analyze_multiple_images``) also receives
    ``config_overrides``."""

    def test_analyze_multiple_images_receives_overrides(self, tmp_path):
        handler, vlm_client = _make_mode3_handler(use_base64=True)

        fake_img = tmp_path / "a.jpg"
        fake_img.write_bytes(b"\x89PNG\r\n\x1a\n")
        handler.downloader = Mock()
        handler.downloader.download = Mock(return_value=str(fake_img))

        overrides = {'num_frames': 8, 'temperature': 0.1}

        handler._evaluate_images(
            worker_id=0,
            message={'id': 'm3', 'info': {'media_urls': ['https://cdn/x.jpg']}, 'sensorId': 's', 'category': 'ppe'},
            media_urls=['https://cdn/x.jpg'],
            user_prompt='u',
            system_prompt='s',
            config_overrides=overrides,
        )

        vlm_client.analyze_multiple_images.assert_called_once()
        kwargs = vlm_client.analyze_multiple_images.call_args.kwargs
        assert kwargs.get('config_overrides') == overrides


class TestR32ImageClientSignatures:
    """Sanity — the four ``vlm_client`` image methods accept
    ``config_overrides`` as a keyword argument so the image path call
    doesn't bind on ``TypeError`` at runtime.

    The module-level ``vlm.vlm_client`` stub we install above for the
    orchestrator tests hides the real classes, so load the source file
    directly via ``importlib`` to inspect the real signatures.
    """

    def test_sync_and_async_methods_accept_config_overrides_kwarg(self):
        import inspect

        here = os.path.dirname(os.path.abspath(__file__))
        vlm_client_path = os.path.normpath(
            os.path.join(here, "..", "vlm", "vlm_client.py")
        )
        spec = importlib.util.spec_from_file_location(
            "_vlm_client_real", vlm_client_path
        )
        module = importlib.util.module_from_spec(spec)
        # Don't touch the stubbed ``vlm.vlm_client`` in sys.modules; load
        # a parallel copy under a different name.
        sys.modules.setdefault("_vlm_client_real", module)
        spec.loader.exec_module(module)

        for cls_name in ("VLMClient", "AsyncVLMClient"):
            cls = getattr(module, cls_name)
            for method_name in (
                "analyze_multiple_image_urls",
                "analyze_multiple_images",
            ):
                sig = inspect.signature(getattr(cls, method_name))
                assert "config_overrides" in sig.parameters, (
                    f"{cls_name}.{method_name} must accept config_overrides "
                    "(image path must honour per-category VLM settings)"
                )
