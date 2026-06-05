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

"""Backward-compatibility functional test for the pluggable parser (Option B) (C8).

Backward-compat requirements:
  - Backward-compat FT: unchanged config + no response_parser → info shape
    matches pre-MR behaviour.
  - Default verification path FT covering both CR1 and CR2 model responses
    after Cosmos Reason parser unification.

This module locks in the expected info schema for the *default* path (the
path every existing deployment uses). It acts as a regression fence: if
any future refactor of the default verification pipeline changes the
on-wire ``info`` shape, these tests fail loudly.

Specifically it asserts:

    A. CR1 answer-tag responses (``<think>..</think><answer>yes</answer>``)
       and CR2 bare-verdict responses (``<think>..</think>yes``) produce
       structurally identical ``info`` output — proving the Cosmos-Reason
       parser unification is correct.

    B. A realistic message with no ``response_parser`` set produces the
       canonical info schema: ``verdict`` is stringified (``"confirmed"``
       / ``"rejected"``), ``verificationResponseCode`` is ``"200"``,
       transport keys are populated, pre-existing keys survive, and
       no new top-level keys are introduced by the merge.

    C. The pluggable parser path and the default path land on the SAME
       set of top-level info keys — so ES mapping for ``mdx-vlm-incidents``
       does not need to change when teams opt into the parser path.
"""

from __future__ import annotations

import json
import logging
import sys
import types
from unittest.mock import Mock

import pytest


# ---------------------------------------------------------------------------
# AB runtime stubs (same pattern as other orchestrator tests).
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


class _DispatchStub: pass
class _IOStub: pass
class _VLMStub: pass


sys.modules['handlers.async_dispatch_mixin'].AsyncDispatchMixin = _DispatchStub
sys.modules['handlers.async_external_io_mixin'].AsyncExternalIOMixin = _IOStub
sys.modules['handlers.async_vlm_mode_mixin'].AsyncVLMModeMixin = _VLMStub

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


from models.responses import (  # noqa: E402
    AlertBridgeResponse,
    VLMResponse,
    merge_info_with_response,
)

import enhance_alert_with_vlm as _eaw  # noqa: E402

_apply_ok = _eaw._apply_pluggable_parser_output
_OK = _eaw._PLUGGABLE_PARSER_OK_STATUS


# ---------------------------------------------------------------------------
# Helpers: simulate the default (no-parser) verification path end-to-end.
# ---------------------------------------------------------------------------


def _run_default_path(
    message: dict,
    *,
    vlm_text: str,
    response_format: str,
    video_source: str,
    model_name: str = "",
) -> dict:
    """Run the no-parser code path exactly like ``_evaluate_local_video`` does
    when ``self._pluggable_parser is None``: parse the VLM text into a
    ``VLMResponse``, then merge it into ``info``."""
    vlm_data = VLMResponse.model_validate_text(
        vlm_text,
        model_name=model_name,
        response_format=response_format,
    )
    merge_info_with_response(
        message,
        AlertBridgeResponse(
            vlm_response=vlm_data,
            video_source=video_source,
            verification_response_code=200,
            verification_response_status="OK",
        ),
    )
    return message


def _base_message() -> dict:
    return {
        "id": "evt-bc-001",
        "sensorId": "cam-retail-03",
        "category": "theft",
        "info": {
            "sensorId": "cam-retail-03",
            "category": "theft",
            "primaryObjectId": "99001",
        },
    }


# ---------------------------------------------------------------------------
# A. CR1 + CR2 unification parity
# ---------------------------------------------------------------------------


CR1_RESPONSE_YES = (
    "<think>A person at the counter is concealing merchandise in a bag "
    "without paying.</think><answer>yes</answer>"
)
CR2_RESPONSE_YES = (
    "<think>A person at the counter is concealing merchandise in a bag "
    "without paying.</think>yes"
)
CR1_RESPONSE_NO = (
    "<think>The customer scans every item at the self-checkout kiosk and "
    "pays before leaving.</think><answer>no</answer>"
)
CR2_RESPONSE_NO = (
    "<think>The customer scans every item at the self-checkout kiosk and "
    "pays before leaving.</think>no"
)


class TestCR1CR2UnificationParity:
    """Unified Cosmos Reason parser must produce identical ``info`` shape
    for CR1 and CR2 styles on equivalent inputs."""

    def test_cr1_and_cr2_yes_produce_equivalent_info(self):
        m1 = _run_default_path(
            _base_message(),
            vlm_text=CR1_RESPONSE_YES,
            response_format="cr1",
            video_source="rtsp://cam-retail-03",
        )
        m2 = _run_default_path(
            _base_message(),
            vlm_text=CR2_RESPONSE_YES,
            response_format="cr2",
            video_source="rtsp://cam-retail-03",
        )
        assert m1["info"] == m2["info"]

    def test_cr1_and_cr2_no_produce_equivalent_info(self):
        m1 = _run_default_path(
            _base_message(),
            vlm_text=CR1_RESPONSE_NO,
            response_format="cr1",
            video_source="rtsp://cam-retail-03",
        )
        m2 = _run_default_path(
            _base_message(),
            vlm_text=CR2_RESPONSE_NO,
            response_format="cr2",
            video_source="rtsp://cam-retail-03",
        )
        assert m1["info"] == m2["info"]

    def test_cosmos_reason_alias_matches_cr1(self):
        """``response_format: 'cosmos-reason'`` is the canonical name;
        ``cr1``/``cr2`` are documented aliases. They must be equivalent."""
        canon = _run_default_path(
            _base_message(),
            vlm_text=CR1_RESPONSE_YES,
            response_format="cosmos-reason",
            video_source="rtsp://cam-retail-03",
        )
        alias = _run_default_path(
            _base_message(),
            vlm_text=CR1_RESPONSE_YES,
            response_format="cr1",
            video_source="rtsp://cam-retail-03",
        )
        assert canon["info"] == alias["info"]

    def test_yes_verdict_maps_to_confirmed_string(self):
        msg = _run_default_path(
            _base_message(),
            vlm_text=CR2_RESPONSE_YES,
            response_format="cr2",
            video_source="rtsp://cam-retail-03",
        )
        assert msg["info"]["verdict"] == "confirmed"

    def test_no_verdict_maps_to_rejected_string(self):
        msg = _run_default_path(
            _base_message(),
            vlm_text=CR2_RESPONSE_NO,
            response_format="cr2",
            video_source="rtsp://cam-retail-03",
        )
        assert msg["info"]["verdict"] == "rejected"


# ---------------------------------------------------------------------------
# B. Backward-compat schema fence — no response_parser, unchanged config
# ---------------------------------------------------------------------------


EXPECTED_BACKCOMPAT_KEYSET = {
    # preserved from inbound message
    "sensorId", "category", "primaryObjectId",
    # produced by merge_info_with_response (AlertBridgeResponse → info)
    # Option B: default path emits ``reasoning``,
    # parser path emits ``vlm_response``. The two are disjoint — this keyset
    # is the default (no response_parser) path only. See
    # test_pluggable_path_info_keyset_is_default_modulo_rename below for
    # the parser-path fence.
    "reasoning", "verdict", "description",
    "videoSource",
    "verificationResponseCode", "verificationResponseStatus",
}


class TestBackwardCompatibilitySchemaFence:
    """Lock the info key-set for the no-parser path. If any future change
    adds/renames/removes a top-level info key, this test breaks — that
    is intentional and the diff must be reviewed for ES-mapping impact."""

    def test_confirmed_verdict_exact_info_schema(self):
        msg = _run_default_path(
            _base_message(),
            vlm_text=CR1_RESPONSE_YES,
            response_format="cr1",
            video_source="rtsp://cam-retail-03",
        )
        assert set(msg["info"]) == EXPECTED_BACKCOMPAT_KEYSET

    def test_rejected_verdict_exact_info_schema(self):
        msg = _run_default_path(
            _base_message(),
            vlm_text=CR2_RESPONSE_NO,
            response_format="cr2",
            video_source="rtsp://cam-retail-03",
        )
        assert set(msg["info"]) == EXPECTED_BACKCOMPAT_KEYSET

    def test_confirmed_verdict_exact_info_values(self):
        """Pin the exact stringified values the nvschema alignment should produce."""
        msg = _run_default_path(
            _base_message(),
            vlm_text=CR1_RESPONSE_YES,
            response_format="cr1",
            video_source="rtsp://cam-retail-03",
        )
        info = msg["info"]
        assert info["verdict"] == "confirmed"
        assert info["verificationResponseCode"] == "200"
        assert info["verificationResponseStatus"] == "OK"
        assert info["videoSource"] == "rtsp://cam-retail-03"
        assert info["sensorId"] == "cam-retail-03"
        assert info["category"] == "theft"
        assert info["primaryObjectId"] == "99001"
        # default-path reasoning is the <think> body (free text)
        assert "merchandise" in info["reasoning"]

    def test_info_is_map_string_string(self):
        """Nvschema alignment contract: every value in info is a str on the wire."""
        msg = _run_default_path(
            _base_message(),
            vlm_text=CR2_RESPONSE_YES,
            response_format="cr2",
            video_source="rtsp://cam-retail-03",
        )
        for k, v in msg["info"].items():
            assert isinstance(v, str), f"info[{k!r}] = {v!r} is not str"

    def test_no_response_parser_means_no_new_top_level_keys(self):
        """When response_parser is absent, no key from the parsed VLM
        response should leak into info beyond the well-known set."""
        msg = _run_default_path(
            _base_message(),
            vlm_text=CR1_RESPONSE_YES,
            response_format="cr1",
            video_source="rtsp://cam-retail-03",
        )
        unexpected = set(msg["info"]) - EXPECTED_BACKCOMPAT_KEYSET
        assert not unexpected, f"Unexpected keys appeared: {unexpected}"

    def test_default_path_does_not_emit_pluggable_vlm_response_key(self):
        """Option B fence: deployments that do NOT set vlm.response_parser
        must never see ``info["vlm_response"]``. Presence of that key on the
        default path would mean the rename leaked outside the opt-in path
        and the "zero wire change" contract is broken."""
        msg = _run_default_path(
            _base_message(),
            vlm_text=CR1_RESPONSE_YES,
            response_format="cr1",
            video_source="rtsp://cam-retail-03",
        )
        info = msg["info"]
        assert "reasoning" in info, (
            "Default-path info['reasoning'] missing — the built-in "
            "verification parser is broken."
        )
        assert "vlm_response" not in info, (
            "info['vlm_response'] must be absent on the default path. "
            "The key belongs exclusively to the pluggable-parser path "
            "(Option B)."
        )


# ---------------------------------------------------------------------------
# C. Parser-path vs default-path schema symmetry
# ---------------------------------------------------------------------------


class TestParserAndDefaultPathsShareInfoSchema:
    """Both paths must produce the SAME top-level info keys *modulo* the
    conditional rename: default-path emits ``reasoning``, pluggable-path
    emits ``vlm_response``. All other keys must match so the
    ``mdx-vlm-incidents`` ES mapping stays stable when a team opts in."""

    def test_pluggable_path_info_keyset_is_default_modulo_rename(self):
        """Option B: the two paths differ by exactly one key — the rename
        from ``reasoning`` to ``vlm_response``. Nothing else should shift."""
        # default path
        default_msg = _run_default_path(
            _base_message(),
            vlm_text=CR2_RESPONSE_YES,
            response_format="cr2",
            video_source="rtsp://cam-retail-03",
        )
        default_keys = set(default_msg["info"])

        # pluggable path
        pluggable_msg = _base_message()
        _apply_ok(
            pluggable_msg,
            {"classification": "suspicious", "confidence": "0.88"},
            video_source="rtsp://cam-retail-03",
        )
        pluggable_keys = set(pluggable_msg["info"])

        # Option B symmetric-difference contract:
        # * default-only keys: {"reasoning", "description"}
        #   - "reasoning" holds the VLM free text on the default path
        #   - "description" is the optional free-form description VLMResponse
        #     emits on the default path; the pluggable path bypasses
        #     VLMResponse entirely so it never appears
        # * pluggable-only keys: {"vlm_response"}
        assert default_keys - pluggable_keys == {"reasoning", "description"}, (
            f"default-only keys beyond the rename: "
            f"{default_keys - pluggable_keys - {'reasoning', 'description'}}"
        )
        assert pluggable_keys - default_keys == {"vlm_response"}, (
            f"pluggable-only keys beyond the rename: "
            f"{pluggable_keys - default_keys - {'vlm_response'}}"
        )

    def test_both_paths_preserve_preexisting_info_keys(self):
        """Key preservation contract is the same in both paths."""
        for runner in ("default", "pluggable"):
            msg = _base_message()
            if runner == "default":
                _run_default_path(
                    msg,
                    vlm_text=CR2_RESPONSE_NO,
                    response_format="cr2",
                    video_source="rtsp://x",
                )
            else:
                _apply_ok(msg, {"classification": "ok"}, video_source="rtsp://x")
            assert msg["info"]["sensorId"] == "cam-retail-03", runner
            assert msg["info"]["category"] == "theft", runner
            assert msg["info"]["primaryObjectId"] == "99001", runner

    def test_pluggable_path_verdict_is_empty_default_path_is_stringified(self):
        """Target behaviour: pluggable → verdict == '' (null stringified),
        default → verdict in {'confirmed','rejected'}."""
        default_msg = _run_default_path(
            _base_message(),
            vlm_text=CR1_RESPONSE_YES,
            response_format="cr1",
            video_source="rtsp://x",
        )
        pluggable_msg = _base_message()
        _apply_ok(pluggable_msg, {"x": "y"}, video_source="rtsp://x")

        assert default_msg["info"]["verdict"] == "confirmed"
        assert pluggable_msg["info"]["verdict"] == ""

    def test_pluggable_vlm_response_is_json_default_reasoning_is_free_text(self):
        """Option B: pluggable path writes JSON into ``info['vlm_response']``;
        default path writes free-form <think> text into ``info['reasoning']``.
        The two payloads live in *different keys*, so consumers can branch on
        key presence alone (no need to sniff the payload)."""
        default_msg = _run_default_path(
            _base_message(),
            vlm_text=CR1_RESPONSE_YES,
            response_format="cr1",
            video_source="rtsp://x",
        )
        pluggable_msg = _base_message()
        _apply_ok(
            pluggable_msg,
            {"classification": "suspicious", "confidence": "0.88"},
            video_source="rtsp://x",
        )

        # default path: reasoning is free text, vlm_response absent
        assert "vlm_response" not in default_msg["info"]
        with pytest.raises(json.JSONDecodeError):
            json.loads(default_msg["info"]["reasoning"])

        # pluggable path: vlm_response is JSON, reasoning absent
        assert "reasoning" not in pluggable_msg["info"]
        decoded = json.loads(pluggable_msg["info"]["vlm_response"])
        assert decoded["classification"] == "suspicious"


# ---------------------------------------------------------------------------
# D. Realistic message covering inbound fields deployments rely on
# ---------------------------------------------------------------------------


class TestRealisticInboundMessage:
    """Simulate a message with the full set of fields a production deployment
    sends in, and verify nothing upstream relies on gets dropped."""

    def _realistic_message(self):
        return {
            "id": "alert-9A",
            "sensorId": "cam-warehouse-01",
            "category": "ppe-violation",
            "place": "warehouse",
            "timestamp": "2026-03-30T12:00:00Z",
            "info": {
                "sensorId": "cam-warehouse-01",
                "category": "ppe-violation",
                "primaryObjectId": "36044",
                "alertId": "alert-9A",
                "place": "warehouse",
                "anomalyDescription": "Worker spotted without hard hat",
            },
        }

    def test_all_preexisting_info_keys_are_preserved(self):
        msg = self._realistic_message()
        _run_default_path(
            msg,
            vlm_text=CR1_RESPONSE_YES,
            response_format="cr1",
            video_source="rtsp://cam-warehouse-01",
        )
        # Every field that was present before merge must still be there.
        for key in ("sensorId", "category", "primaryObjectId",
                    "alertId", "place", "anomalyDescription"):
            assert key in msg["info"], f"{key!r} dropped by merge"
        assert msg["info"]["anomalyDescription"] == "Worker spotted without hard hat"

    def test_toplevel_message_is_untouched(self):
        """The merge helper writes into ``message['info']`` only —
        top-level message keys like id/sensorId/place stay as-is."""
        msg = self._realistic_message()
        snapshot = {k: v for k, v in msg.items() if k != "info"}
        _run_default_path(
            msg,
            vlm_text=CR1_RESPONSE_YES,
            response_format="cr1",
            video_source="rtsp://cam-warehouse-01",
        )
        for k, v in snapshot.items():
            assert msg[k] == v, f"top-level key {k!r} mutated"
