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

"""Orchestrator-level tests for the pluggable VLM response parser path.

Covers the production helpers exported by enhance_alert_with_vlm:
    - _safe_json_dumps_parser_output
    - _apply_pluggable_parser_output
    - _apply_pluggable_parser_error

These tests exercise the real module-level helpers (not a mirror), so
behaviour asserted here is what the VST and local-file paths actually
see at runtime.

Addresses review threads:
    C3 — json.dumps must not crash on datetime/Decimal/bytes/set
    C4 — pluggable-parser failures emit a distinct, explicit error event
    C5 — orchestrator-level failure paths are covered
    C6 — info schema preservation (no new top-level keys from parser)
    C2 — latency parity between VST and local-file call sites
"""

import datetime
import decimal
import logging
import sys
import types
from unittest.mock import Mock

import pytest


# ---------------------------------------------------------------------------
# Stub the broader Alert Bridge runtime so ``import enhance_alert_with_vlm``
# succeeds in a pure unit-test environment (no Redis / VST / Kafka / VLM).
# Mirrors the pattern used by test/test_vlm_error_detail.py, extended for
# async mixins introduced by the ``ab_int_ga`` async rollout.
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
for _mod_name in _stub_modules:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

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


class _AsyncDispatchMixinStub:  # distinct so multiple-inheritance MRO resolves
    pass


class _AsyncExternalIOMixinStub:
    pass


class _AsyncVLMModeMixinStub:
    pass


sys.modules['handlers.async_dispatch_mixin'].AsyncDispatchMixin = _AsyncDispatchMixinStub
sys.modules['handlers.async_external_io_mixin'].AsyncExternalIOMixin = _AsyncExternalIOMixinStub
sys.modules['handlers.async_vlm_mode_mixin'].AsyncVLMModeMixin = _AsyncVLMModeMixinStub

sys.modules['utils.logging_config'].setup_logging = Mock()
sys.modules['utils.logging_config'].get_logger = lambda name: logging.getLogger(name)
sys.modules['utils.logging_config'].enforce_log_level = Mock()
sys.modules['utils.schema_util'].protobuf_anomalies_to_json_string_list = Mock()
sys.modules['vlm.warmup'].warmup_vlm = Mock()
sys.modules['vlm.warmup'].WARMUP_VIDEO = '/tmp/fake.mp4'
sys.modules['vlm.vlm_client'].VLMClient = Mock
sys.modules['vlm.vlm_client'].AsyncVLMRuntime = Mock
sys.modules['vss'].VSSHandler = Mock
sys.modules['metrics'].PROMETHEUS_ENABLED = False


import enhance_alert_with_vlm as _eaw  # noqa: E402 — must follow stubs


# Shorthands; tests below treat these as the system under test.
_safe_dumps = _eaw._safe_json_dumps_parser_output
_apply_ok = _eaw._apply_pluggable_parser_output
_apply_err = _eaw._apply_pluggable_parser_error
_OK = _eaw._PLUGGABLE_PARSER_OK_STATUS
_ERR_PREFIX = _eaw._PLUGGABLE_PARSER_ERROR_STATUS


# ---------------------------------------------------------------------------
# C3 — _safe_json_dumps_parser_output
# ---------------------------------------------------------------------------


class TestSafeJsonDumpsParserOutput:
    """Parser output must survive serialization even when the author returns
    non-JSON-primitive values. A runtime TypeError would blow up the whole
    worker; we fall back to default=str instead."""

    def test_plain_primitives_pass_through(self):
        out = _safe_dumps({"label": "ok", "count": 3, "ratio": 0.5})
        assert out == '{"label": "ok", "count": 3, "ratio": 0.5}'

    def test_datetime_coerced_to_string(self, caplog):
        ts = datetime.datetime(2026, 3, 30, 12, 0, 0, tzinfo=datetime.timezone.utc)
        with caplog.at_level(logging.WARNING, logger="enhance_alert_with_vlm"):
            out = _safe_dumps({"captured_at": ts, "label": "helmet"})
        assert "2026-03-30" in out
        assert '"label": "helmet"' in out
        assert any("non-JSON-serializable" in r.message for r in caplog.records)

    def test_decimal_coerced(self):
        out = _safe_dumps({"confidence": decimal.Decimal("0.9321")})
        assert "0.9321" in out

    def test_bytes_coerced(self):
        out = _safe_dumps({"blob": b"\x00\x01"})
        # default=str will call str(b'\x00\x01') → "b'\\x00\\x01'"
        assert "\\x00\\x01" in out or "b'" in out

    def test_set_coerced(self):
        out = _safe_dumps({"tags": {"a", "b"}})
        # set repr isn't order-stable; assert presence, not exact form
        assert "'a'" in out and "'b'" in out

    def test_nested_mixed(self):
        payload = {
            "event": {
                "at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
                "score": decimal.Decimal("0.5"),
                "extra": {b"k"},
            },
            "label": "mixed",
        }
        out = _safe_dumps(payload)
        # Nothing raised; output is a non-empty string containing the label.
        assert isinstance(out, str)
        assert '"label": "mixed"' in out

    def test_happy_path_avoids_fallback_warning(self, caplog):
        """When parsed is JSON-safe, the fallback branch must NOT fire."""
        with caplog.at_level(logging.WARNING, logger="enhance_alert_with_vlm"):
            _safe_dumps({"only": "primitives", "n": 1})
        assert not any("non-JSON-serializable" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# C5/C6 — _apply_pluggable_parser_output: success-path info-shape contract
# ---------------------------------------------------------------------------


class TestApplyPluggableParserOutput:
    """Contract: parser output is JSON-stringified into info["vlm_response"];
    info["verdict"] is "" (null stringified by the nvschema alignment); the transport
    metadata (videoSource, verificationResponseCode, verificationResponseStatus)
    is set by the orchestrator; preexisting info keys are preserved; no new
    top-level keys leak from the parser into info."""

    def _base_message(self):
        return {
            "id": "evt-42",
            "sensorId": "cam-01",
            "category": "ppe-violation",
            "info": {
                "sensorId": "cam-01",
                "category": "ppe-violation",
                "primaryObjectId": "36044",
            },
        }

    def test_vlm_response_holds_serialized_parser_output(self):
        msg = self._base_message()
        _apply_ok(
            msg,
            {"label": "no-helmet", "severity": "high", "confidence": 0.9},
            video_source="rtsp://cam-01",
        )
        import json as _json
        decoded = _json.loads(msg["info"]["vlm_response"])
        assert decoded["label"] == "no-helmet"
        assert decoded["severity"] == "high"

    def test_verdict_is_empty_string(self):
        msg = self._base_message()
        _apply_ok(msg, {"x": 1}, video_source="rtsp://cam-01")
        assert msg["info"]["verdict"] == ""

    def test_transport_metadata_is_set_by_orchestrator(self):
        msg = self._base_message()
        _apply_ok(msg, {"x": 1}, video_source="rtsp://cam-01")
        assert msg["info"]["verificationResponseCode"] == "200"
        assert msg["info"]["verificationResponseStatus"] == _OK
        assert msg["info"]["videoSource"] == "rtsp://cam-01"

    def test_preexisting_info_keys_preserved(self):
        msg = self._base_message()
        _apply_ok(msg, {"label": "ok"}, video_source="rtsp://cam-01")
        assert msg["info"]["sensorId"] == "cam-01"
        assert msg["info"]["category"] == "ppe-violation"
        assert msg["info"]["primaryObjectId"] == "36044"

    def test_parser_keys_do_not_flat_merge_into_info(self):
        """Opaque-blob contract: parser's top-level keys MUST land inside
        vlm_response (as JSON), never in info."""
        msg = self._base_message()
        _apply_ok(
            msg,
            {"classification": "no-helmet", "severity": "high", "custom_metric": 0.7},
            video_source="rtsp://cam-01",
        )
        assert "classification" not in msg["info"]
        assert "severity" not in msg["info"]
        assert "custom_metric" not in msg["info"]

    def test_parser_cannot_spoof_transport_keys(self):
        """Even if the parser returns keys that collide with transport
        metadata, those land inside vlm_response — the orchestrator-set
        transport values win."""
        msg = self._base_message()
        _apply_ok(
            msg,
            {
                "verificationResponseStatus": "spoofed",
                "videoSource": "evil://",
                "verdict": "confirmed",
            },
            video_source="rtsp://legit",
        )
        assert msg["info"]["verificationResponseStatus"] == _OK
        assert msg["info"]["videoSource"] == "rtsp://legit"
        assert msg["info"]["verdict"] == ""

    def test_info_stays_map_string_string(self):
        """The nvschema alignment enforces map<string,string>. After the helper runs,
        every value in info must be a str."""
        msg = self._base_message()
        _apply_ok(msg, {"n": 1, "nested": {"a": 1}}, video_source="rtsp://cam-01")
        for k, v in msg["info"].items():
            assert isinstance(v, str), f"info[{k!r}] = {v!r} is not str"

    def test_non_serializable_parser_output_does_not_crash(self):
        """End-to-end: parser returns datetime inside the dict; the helper
        must serialize it via default=str fallback instead of raising."""
        msg = self._base_message()
        payload = {
            "label": "time-sensitive",
            "captured_at": datetime.datetime(2026, 3, 30, tzinfo=datetime.timezone.utc),
        }
        _apply_ok(msg, payload, video_source="rtsp://cam-01")
        assert "2026-03-30" in msg["info"]["vlm_response"]


# ---------------------------------------------------------------------------
# C4 — _apply_pluggable_parser_error: distinct error shape
# ---------------------------------------------------------------------------


class TestApplyPluggableParserError:
    """When a pluggable parser raises, operators need to see that it was
    the PARSER that failed, not the VLM. A distinct status string and a
    verification-failed verdict are the observability contract."""

    def _msg(self):
        return {
            "id": "evt-err",
            "sensorId": "cam-02",
            "category": "classification",
            "info": {"sensorId": "cam-02"},
        }

    def test_status_uses_parser_failed_prefix(self):
        msg = self._msg()
        _apply_err(
            msg,
            RuntimeError("boom"),
            video_source="rtsp://cam-02",
        )
        status = msg["info"]["verificationResponseStatus"]
        assert status.startswith(_ERR_PREFIX)
        assert "RuntimeError" in status
        assert "boom" in status

    def test_response_code_is_500(self):
        msg = self._msg()
        _apply_err(msg, ValueError("bad json"), video_source="rtsp://cam-02")
        assert msg["info"]["verificationResponseCode"] == "500"

    def test_verdict_is_verification_failed(self):
        msg = self._msg()
        _apply_err(msg, TypeError("bad type"), video_source="rtsp://cam-02")
        assert msg["info"]["verdict"] == "verification-failed"

    def test_error_status_is_distinct_from_vlm_schema_error(self):
        """Must NOT be confused with the VLM-schema failure path, which
        emits 'Incorrect VLM response schema'."""
        msg = self._msg()
        _apply_err(msg, RuntimeError("x"), video_source="rtsp://cam-02")
        assert "Incorrect VLM response schema" not in msg["info"]["verificationResponseStatus"]

    def test_video_source_still_populated_on_error(self):
        msg = self._msg()
        _apply_err(msg, RuntimeError("x"), video_source="rtsp://cam-02/err")
        assert msg["info"]["videoSource"] == "rtsp://cam-02/err"

    def test_error_emits_log_record_with_context(self, caplog):
        msg = self._msg()
        with caplog.at_level(logging.WARNING, logger="enhance_alert_with_vlm"):
            _apply_err(msg, RuntimeError("boom"), video_source="rtsp://cam-02")
        assert any("Pluggable parser failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# C2 — latency parity between VST and local-file call sites
# ---------------------------------------------------------------------------


class TestLatencyParity:
    """Both ingestion paths call the SAME helper — so whatever latency
    handling exists must be uniform. These tests lock that in: with the
    same latency dict, both call sites produce identical info (modulo
    video_source)."""

    def _msg(self):
        return {
            "id": "evt-lat",
            "sensorId": "cam-03",
            "info": {"sensorId": "cam-03"},
        }

    def test_include_latency_false_omits_latency_keys(self):
        msg = self._msg()
        _apply_ok(
            msg,
            {"label": "ok"},
            video_source="rtsp://cam-03",
            latency={"timestamps": {"elasticReadyAt": "2026-03-30T00:00:00Z"}},
            include_latency=False,
        )
        # when include_latency=False, latency must not leak into info.
        assert "latency" not in msg["info"]
        assert "timestamps" not in msg["info"]

    def test_include_latency_true_injects_latency_keys(self):
        msg = self._msg()
        _apply_ok(
            msg,
            {"label": "ok"},
            video_source="rtsp://cam-03",
            latency={"timestamps": {"elasticReadyAt": "2026-03-30T00:00:00Z"}},
            include_latency=True,
        )
        # Latency is projected into info via merge_info_with_response; the
        # exact key name (latency / latencies / timestamps) is downstream
        # contract. We assert SOMETHING latency-shaped landed in info.
        info_blob = str(msg["info"])
        assert "elasticReadyAt" in info_blob or "2026-03-30" in info_blob

    def test_vst_path_and_local_path_produce_identical_info_modulo_source(self):
        """Simulate both call sites with the same parser output +
        same latency handling. The resulting info blobs should be
        identical up to videoSource."""
        parsed = {"label": "ok", "confidence": 0.9}
        latency = {"timestamps": {"elasticReadyAt": "2026-03-30T00:00:00Z"}}

        vst_msg = {"info": {"sensorId": "cam-X"}}
        _apply_ok(
            vst_msg, parsed,
            video_source="http://vst/clip.mp4",
            latency=latency, include_latency=True,
        )

        local_msg = {"info": {"sensorId": "cam-X"}}
        _apply_ok(
            local_msg, parsed,
            video_source="/tmp/local.mp4",
            latency=latency, include_latency=True,
        )

        vst_info = dict(vst_msg["info"])
        local_info = dict(local_msg["info"])
        assert vst_info.pop("videoSource") == "http://vst/clip.mp4"
        assert local_info.pop("videoSource") == "/tmp/local.mp4"
        assert vst_info == local_info

    def test_error_path_also_accepts_latency(self):
        """The error helper mirrors the success helper's latency API.
        This is the parity contract: an error in either ingestion path
        should still emit the same latency-shaped info."""
        msg = self._msg()
        _apply_err(
            msg,
            RuntimeError("x"),
            video_source="rtsp://cam-03",
            latency={"timestamps": {"elasticReadyAt": "2026-03-30T00:00:00Z"}},
            include_latency=True,
        )
        info_blob = str(msg["info"])
        assert "elasticReadyAt" in info_blob or "2026-03-30" in info_blob


# ---------------------------------------------------------------------------
# Edge cases in C5
# ---------------------------------------------------------------------------


class TestEdgeCases:

    def test_empty_parser_output_is_emitted_as_empty_json_object(self):
        msg = {"info": {}}
        _apply_ok(msg, {}, video_source="rtsp://x")
        assert msg["info"]["vlm_response"] == "{}"
        assert msg["info"]["verificationResponseCode"] == "200"

    def test_large_parser_output_survives_serialization(self):
        """2KB+ payloads serialize through without truncation."""
        large = {"items": [{"id": i, "label": "x" * 32} for i in range(200)]}
        msg = {"info": {}}
        _apply_ok(msg, large, video_source="rtsp://x")
        # Decode back to validate structural integrity.
        import json as _json
        decoded = _json.loads(msg["info"]["vlm_response"])
        assert len(decoded["items"]) == 200

    def test_deeply_nested_parser_output(self):
        """Nested dicts must serialize without recursion depth issues."""
        deep = {"l1": {"l2": {"l3": {"l4": {"l5": "bottom"}}}}}
        msg = {"info": {}}
        _apply_ok(msg, deep, video_source="rtsp://x")
        import json as _json
        decoded = _json.loads(msg["info"]["vlm_response"])
        assert decoded["l1"]["l2"]["l3"]["l4"]["l5"] == "bottom"

    def test_error_status_truncates_sanely_on_long_error_messages(self):
        """Very long exception messages must not balloon the status line
        (reasonable cap; we just check nothing explodes)."""
        msg = {"info": {}}
        _apply_err(
            msg,
            RuntimeError("x" * 5000),
            video_source="rtsp://x",
        )
        status = msg["info"]["verificationResponseStatus"]
        assert status.startswith(_ERR_PREFIX)
        # No crash, and status is still a reasonable string (well under 1MB).
        assert len(status) < 20000
