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
Regression lock: Mode-3 (DirectMediaHandler)
must route VLM output through the same pluggable parser as Mode-2 / VST.

Before this fix, the direct-media path only branched on
``alert_agent.media_download.use_verdict`` and silently ignored
``vlm.response_parser``.  The same deployment produced two different
output shapes depending on ingress path.

These tests lock the parity:

* When ``vlm.response_parser`` is configured, Mode-3 parses the VLM
  response through the pluggable parser and emits the standard
  ``info["vlm_response"] = json.dumps(parsed)`` shape.
* When the parser raises, Mode-3 emits the same explicit
  ``verification-failed`` error shape as Mode-2 / VST.
* When no parser is configured, Mode-3 keeps its legacy behaviour.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from unittest.mock import Mock

import pytest


def _load_direct_media_handler():
    """Load ``DirectMediaHandler`` directly from its source file.

    ``test_pluggable_parser_publish_routing.py`` installs Mock stubs at
    ``sys.modules['handlers.*']`` at import time, poisoning the real
    package for the rest of the pytest session.  Rather than fight over
    ``sys.modules``, load the handler module from disk with an isolated
    spec — it only depends on ``models.responses`` and its sibling
    ``media_downloader``, both of which we import explicitly.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    handler_path = os.path.normpath(
        os.path.join(here, "..", "handlers", "direct_media", "direct_media_handler.py")
    )
    downloader_path = os.path.normpath(
        os.path.join(here, "..", "handlers", "direct_media", "media_downloader.py")
    )

    # Ensure media_downloader is loadable as an attribute of a package
    # named ``_dm_pkg`` so the handler's ``from .media_downloader import
    # ...`` relative import resolves without touching ``handlers.*``.
    pkg_name = "_dmh_isolated_pkg"
    if pkg_name not in sys.modules:
        import types
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


class _EchoingParser:
    """Trivial parser: echoes ``{"label": ...}`` from JSON VLM output."""

    def parse(self, raw_response: str) -> dict:
        data = json.loads(raw_response)
        return {"label": data.get("label", "unknown"), "confidence": data.get("confidence", 0.0)}


class _ExplodingParser:
    """Parser that always raises — used to lock the error contract."""

    def parse(self, raw_response: str) -> dict:
        raise ValueError("boom from custom parser")


def _base_message() -> dict:
    return {
        "id": "evt-123",
        "sensorId": "cam-01",
        "category": "ppe",
        "primaryObjectId": "42",
        "info": {
            "media_urls": ["https://cdn.example.com/a.jpg"],
            "sensorId": "cam-01",
            "category": "ppe",
            "primaryObjectId": "42",
        },
    }


def _make_handler(parser=None, use_verdict: bool = False) -> DirectMediaHandler:
    return DirectMediaHandler(
        vlm_client=Mock(),
        vlm_enhanced_event_sink=Mock(),
        config={
            "alert_agent": {"media_download": {"enabled": True, "use_verdict": use_verdict}},
            "vlm": {"model": "nvidia/cosmos-reason2-8b"},
        },
        pluggable_parser=parser,
    )


class TestMode3PluggableParserParity:
    """Mode-3 must honour ``vlm.response_parser`` identically to Mode-2 / VST."""

    def test_pluggable_parser_output_merged_into_info(self):
        handler = _make_handler(parser=_EchoingParser())
        msg = _base_message()
        vlm_text = '{"label": "no-helmet", "confidence": 0.91}'

        handler._publish_success(
            msg,
            user_prompt="u",
            system_prompt="s",
            response_content=vlm_text,
            media_type="image",
        )

        info = msg["info"]
        assert "vlm_response" in info
        decoded = json.loads(info["vlm_response"])
        assert decoded == {"label": "no-helmet", "confidence": 0.91}
        assert info["verificationResponseCode"] == "200"
        assert info["verificationResponseStatus"] == "OK"
        # verdict is null-stringified on the pluggable path
        assert info["verdict"] == ""

    def test_pluggable_parser_failure_emits_verification_failed(self):
        handler = _make_handler(parser=_ExplodingParser())
        msg = _base_message()

        handler._publish_success(
            msg,
            user_prompt="u",
            system_prompt="s",
            response_content="whatever the VLM said",
            media_type="image",
        )

        info = msg["info"]
        assert info["verdict"] == "verification-failed"
        assert info["verificationResponseCode"] == "500"
        assert "Pluggable parser failed" in info["verificationResponseStatus"]
        assert "ValueError" in info["verificationResponseStatus"]
        assert "boom from custom parser" in info["verificationResponseStatus"]

    def test_pluggable_parser_returning_non_dict_is_an_error(self):
        class BadParser:
            def parse(self, raw_response: str):
                return "i am a string, not a dict"

        handler = _make_handler(parser=BadParser())
        msg = _base_message()

        handler._publish_success(
            msg,
            user_prompt="u",
            system_prompt="s",
            response_content="anything",
            media_type="image",
        )

        info = msg["info"]
        assert info["verdict"] == "verification-failed"
        assert "Pluggable parser failed" in info["verificationResponseStatus"]

    def test_no_pluggable_parser_preserves_legacy_no_verdict_path(self):
        handler = _make_handler(parser=None, use_verdict=False)
        msg = _base_message()

        handler._publish_success(
            msg,
            user_prompt="u",
            system_prompt="s",
            response_content="free-text VLM body",
            media_type="image",
        )

        info = msg["info"]
        # Legacy Mode-3 behaviour (Option B): raw VLM text lands in
        # ``info["reasoning"]`` (same key the VST / local paths use on
        # the default no-parser path). ``vlm_response`` is reserved for
        # the pluggable-parser opt-in path and must NOT appear here.
        assert info["reasoning"] == "free-text VLM body"
        assert "vlm_response" not in info
        assert info["verificationResponseCode"] == "200"
        assert info["verificationResponseStatus"] == "OK"

    def test_pluggable_parser_short_circuits_use_verdict(self):
        """Pluggable parser wins over use_verdict (documented precedence)."""
        handler = _make_handler(parser=_EchoingParser(), use_verdict=True)
        msg = _base_message()
        vlm_text = '{"label": "safe", "confidence": 0.98}'

        handler._publish_success(
            msg,
            user_prompt="u",
            system_prompt="s",
            response_content=vlm_text,
            media_type="image",
        )

        info = msg["info"]
        assert json.loads(info["vlm_response"]) == {"label": "safe", "confidence": 0.98}
        # Pluggable path always sets verdict null (stringified "")
        assert info["verdict"] == ""

    def test_videosource_populated_for_single_url(self):
        handler = _make_handler(parser=_EchoingParser())
        msg = _base_message()

        handler._publish_success(
            msg,
            user_prompt="u",
            system_prompt="s",
            response_content='{"label": "x"}',
            media_type="image",
        )

        assert msg["info"]["videoSource"] == "https://cdn.example.com/a.jpg"

    def test_preserves_original_inbound_fields(self):
        handler = _make_handler(parser=_EchoingParser())
        msg = _base_message()

        handler._publish_success(
            msg,
            user_prompt="u",
            system_prompt="s",
            response_content='{"label": "x"}',
            media_type="image",
        )

        info = msg["info"]
        assert info["sensorId"] == "cam-01"
        assert info["category"] == "ppe"
        assert info["primaryObjectId"] == "42"


class TestMode3SinkInvocation:
    """The sink must be called exactly once per publish — success or error."""

    def test_sink_called_once_on_parser_success(self):
        """Success path uses ``publish_success`` (transport method)."""
        parser = _EchoingParser()
        sink = Mock()
        handler = DirectMediaHandler(
            vlm_client=Mock(),
            vlm_enhanced_event_sink=sink,
            config={
                "alert_agent": {"media_download": {"enabled": True, "use_verdict": False}},
                "vlm": {"model": "m"},
            },
            pluggable_parser=parser,
        )
        msg = _base_message()

        handler._publish_success(
            msg, "u", "s", '{"label":"a"}', "image",
        )
        assert sink.publish_success.call_count == 1
        assert sink.send.call_count == 0

    def test_sink_called_once_on_parser_failure(self):
        """Parser failure routes through the sink's ``publish_error`` API.

        The real ``VLMEnhancedEventSinkBase`` only exposes
        ``publish_success(...)`` and ``publish_error(...)`` — there is no
        ``send(...)`` method on the interface. A parser exception must
        therefore publish through ``publish_error`` so the error event
        lands in the same ES index as any other
        ``verification-failed`` event, and so that a real (non-Mock) sink
        does not blow up with ``AttributeError`` at runtime.
        """
        sink = Mock()
        handler = DirectMediaHandler(
            vlm_client=Mock(),
            vlm_enhanced_event_sink=sink,
            config={
                "alert_agent": {"media_download": {"enabled": True, "use_verdict": False}},
                "vlm": {"model": "m"},
            },
            pluggable_parser=_ExplodingParser(),
        )
        msg = _base_message()

        handler._publish_success(msg, "u", "s", "raw", "image")
        assert sink.publish_error.call_count == 1
        assert sink.publish_success.call_count == 0
        assert sink.send.call_count == 0

        # Lock the exact argument shape: (message, user_prompt, system_prompt, error_payload).
        call_args = sink.publish_error.call_args
        assert call_args.args[0] is msg
        assert call_args.args[1] == "u"
        assert call_args.args[2] == "s"
        error_payload = call_args.args[3]
        assert isinstance(error_payload, dict)
        # Payload uses ``errorSource`` (renamed from ``source``) so the
        # sink descriptor uses the same key as ``info["errorSource"]``.
        assert error_payload.get("errorSource") == "pluggable_parser"
        assert "source" not in error_payload, (
            "legacy 'source' key must be removed — the schema rename to "
            "'errorSource' is a single-key contract, not a dual-write"
        )
        assert "error" in error_payload
        assert "error_type" in error_payload

    def test_parser_failure_does_not_call_nonexistent_send_method(self):
        """Lock the real sink API contract against future regressions.

        Uses a minimal stub sink that exposes *only* ``publish_success`` /
        ``publish_error`` — matching ``mdx.anomaly.sink.vlm_enhanced_sink.
        sink_base.VLMEnhancedEventSinkBase``. Any attempt to call
        ``sink.send(...)`` would raise ``AttributeError`` at runtime (the
        Previously-reported bug). This test would fail under the old buggy
        code even without Mock help.
        """

        class _StrictSinkStub:
            """Mirrors the real sink's public surface (success / error only)."""

            def __init__(self) -> None:
                self.success_calls = []
                self.error_calls = []

            def publish_success(self, message, user_prompt, system_prompt,
                                vlm_response, latency=None):
                self.success_calls.append((message, user_prompt, system_prompt))

            def publish_error(self, message, user_prompt, system_prompt,
                              error_payload, latency=None):
                self.error_calls.append(
                    (message, user_prompt, system_prompt, error_payload)
                )

        sink = _StrictSinkStub()
        handler = DirectMediaHandler(
            vlm_client=Mock(),
            vlm_enhanced_event_sink=sink,
            config={
                "alert_agent": {
                    "media_download": {"enabled": True, "use_verdict": False}
                },
                "vlm": {"model": "m"},
            },
            pluggable_parser=_ExplodingParser(),
        )
        msg = _base_message()

        # With the fix in place this must not raise; under the old
        # ``sink.send(message)`` code it would raise AttributeError on
        # this strict stub.
        handler._publish_success(msg, "u", "s", "raw", "image")

        assert len(sink.error_calls) == 1
        assert len(sink.success_calls) == 0
