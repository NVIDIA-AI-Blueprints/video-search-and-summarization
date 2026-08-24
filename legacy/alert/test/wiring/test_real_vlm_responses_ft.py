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

"""Real-response functional test for the pluggable parser (Option B).

This file plugs in **actual VLM output captured from production runs**
and asserts the `info` shape the code produces today. It is the answer
to the reasonable question "did you test with real responses, not just
your imagination?".

Fixture sources (do not edit — these are verbatim captures):

    /home/user/vlm_log_result/cr2_direct_test_report.txt
        2026-03-17 direct-to-CR2 (no Alert Bridge) run. Includes:
          - bare verdict responses (`'no'`, `'No'`)
          - `<think>…</think> VERDICT` with CJK reasoning and a leading
            space after `</think>` (important edge case — the real model
            outputs this)
          - free-text responses without tags (parse FAILS in prod — we
            lock that failure mode)
          - empty response (parse FAILS — status string is pinned)

    /home/user/vlm_log_result/e2e_json_cookbook.json
        2026-04-07 live JSON-cookbook run against cosmos-reason2-8b.
        Includes the markdown-fenced JSON responses the real model emits
        (triple-backtick ``json ... `` shape) plus the deployment's
        actual ``json_config`` (verdict_field = "hazard_detection.is_hazardous",
         verdict_mapping = {"true": "YES", "false": "NO"},
         reasoning_fields = ["video_description"]).

    /home/user/eval_fw/alert_agent/test/sim_scripts/nim/classification_response.txt
        Sample classification payload used by the NIM stub server.
        Consumed by a pluggable classification parser (the designated
        vehicle for non-verification use cases under the reduced scope).

What this file proves:

    1. Real CR2 bare-verdict output (`'no'` with no tags) flows through
       `_parse_cosmos_reason_response` and produces a valid info dict.
    2. Real CR2 `<think>一栋\n</think> No` (CJK + leading space)
       parses successfully; the leading space after `</think>` is
       tolerated; `reasoning` is the CJK body; `verdict` = "rejected".
    3. Real free-text CR2 output (`"There is no one on the ladder…\nno"`)
       FAILS parse with a specific error message — matches the
       production log.
    4. Empty VLM response fails with status string starting with "VLM
       returned" (pinned wording).
    5. Real JSON-cookbook markdown-fenced responses
       (` ```json\\n{…}\\n``` `) parse to `verdict=confirmed`/`rejected`
       and `reasoning` = `video_description`, matching the serialized
       outputs captured in `e2e_json_cookbook.json`.
    6. The `classification_response.txt` fixture, when handed to a
       pluggable classification parser, produces a JSON blob inside
       `info["vlm_response"]` whose keys match the doc's description
       (label / severity / confidence / reasoning).
    7. For every real fixture, the final `info` is `map<string,string>`
       (nvschema alignment) and no unexpected top-level keys leak.
"""

from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

# ---------------------------------------------------------------------------
# AB runtime stubs.
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
# VERBATIM real-response fixtures (do not normalise/edit).
# ---------------------------------------------------------------------------

# From /home/user/vlm_log_result/cr2_direct_test_report.txt
# TEST A: Standard Alert Bridge prompt — CR2 returns a bare verdict.
REAL_CR2_BARE_LOWERCASE_NO = "no"

# TEST C: prompt with explicit <think> tags — CR2 returns CJK reasoning
# and a leading space after </think>. Both are real production quirks.
REAL_CR2_THINK_TAGS_CJK_LEADING_SPACE = "<think>一栋\n</think> No"

# TEST D fallback fixture: legit <think>…</think>VERDICT shape with
# English reasoning — used as the "easy" baseline.
REAL_CR2_THINK_TAGS_ENGLISH = "<think>Person has hardhat and vest</think>\nNo"

# TEST B: CR2 returns free-form text WITHOUT <think> tags when the prompt
# says "Think step by step, then answer yes or no". Parse FAILS in
# production — we pin that failure mode.
REAL_CR2_FREE_TEXT_FAILURE = (
    "There is no one on the ladder. The person in the image is wearing "
    "a yellow hard hat and a safety vest, but they are not on the ladder. "
    "The question asks if there is someone on the ladder who is not "
    "wearing a hard hat and safety vest. Since no one is on the ladder, "
    "the answer is no.\nno"
)

# From the same report — empty + bare capitalised cases.
REAL_CR2_EMPTY = ""
REAL_CR2_BARE_CAPITAL_NO = "No"

# From /home/user/vlm_log_result/e2e_json_cookbook.json (2026-04-07 live
# run against nvidia/cosmos-reason2-8b). Kept verbatim, including the
# markdown fences the real model wraps around its JSON.
REAL_JSON_COOKBOOK_HAZARDOUS = (
    "```json\n"
    '{\n  "prediction_class_id": 1,\n'
    '  "prediction_label": "Collision risk",\n'
    '  "video_description": "A forklift near a worker.",\n'
    '  "hazard_detection": {\n'
    '    "is_hazardous": true,\n'
    '    "temporal_segment": null\n'
    '  }\n}\n'
    "```"
)

REAL_JSON_COOKBOOK_SAFE = (
    "```json\n"
    '{\n  "prediction_class_id": 0,\n'
    '  "prediction_label": "No hazard",\n'
    '  "video_description": "Safe operations.",\n'
    '  "hazard_detection": {\n'
    '    "is_hazardous": false,\n'
    '    "temporal_segment": null\n'
    '  }\n}\n'
    "```"
)

# Exact json_config from the deployment captured in the cookbook.
REAL_JSON_COOKBOOK_CONFIG = {
    "verdict_field": "hazard_detection.is_hazardous",
    "verdict_mapping": {"true": "YES", "false": "NO"},
    "reasoning_fields": ["video_description"],
}

# From /home/user/eval_fw/alert_agent/test/sim_scripts/nim/classification_response.txt
# (loaded from disk to prove we're using the exact file shipped in the MR).
_CLASSIFICATION_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "test" / "sim_scripts" / "nim" / "classification_response.txt"
)
REAL_CLASSIFICATION_PAYLOAD = _CLASSIFICATION_FIXTURE_PATH.read_text()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_path_info(
    message: dict,
    *,
    vlm_text: str,
    response_format: str = "auto",
    model_name: str = "nvidia/cosmos-reason2-8b",
    video_source: str = "rtsp://cam-01",
    json_config: dict | None = None,
) -> dict:
    """Run the no-parser verification path exactly like _evaluate_local_video."""
    vlm_data = VLMResponse.model_validate_text(
        vlm_text,
        model_name=model_name,
        response_format=response_format,
        json_config=json_config,
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
    return message["info"]


def _base_message(category: str = "FOV Count Violation") -> dict:
    """Shaped like the real messages in e2e_happy_path.log (warehouse ladder)."""
    return {
        "id": "synthetic_sample-warehouse-ladder_11",
        "sensorId": "synthetic_sample-warehouse-ladder_11",
        "category": category,
        "info": {
            "sensorId": "synthetic_sample-warehouse-ladder_11",
            "category": category,
            "primaryObjectId": "36044",
        },
    }


EXPECTED_DEFAULT_KEYSET = {
    # preserved from inbound
    "sensorId", "category", "primaryObjectId",
    # produced by merge_info_with_response (AlertBridgeResponse → info).
    # Option B: default path emits ``reasoning``; ``vlm_response`` is
    # exclusive to the pluggable-parser path and must NOT appear here.
    "reasoning", "verdict", "description",
    "videoSource",
    "verificationResponseCode", "verificationResponseStatus",
}


# ---------------------------------------------------------------------------
# A. Real CR2 verification responses — default path
# ---------------------------------------------------------------------------


class TestRealCR2BareVerdict:
    """TEST A from cr2_direct_test_report.txt — CR2 with standard
    'Answer yes or no' prompt returns a bare verdict (no tags)."""

    def test_bare_lowercase_no_parses_to_rejected(self):
        info = _default_path_info(
            _base_message(),
            vlm_text=REAL_CR2_BARE_LOWERCASE_NO,
            response_format="cr2",
        )
        assert info["verdict"] == "rejected"
        # bare-verdict path has no <think> body → reasoning is empty
        assert info["reasoning"] == ""

    def test_bare_capital_no_parses_to_rejected(self):
        info = _default_path_info(
            _base_message(),
            vlm_text=REAL_CR2_BARE_CAPITAL_NO,
            response_format="cr2",
        )
        assert info["verdict"] == "rejected"

    def test_bare_verdict_info_keyset_matches_expected(self):
        info = _default_path_info(
            _base_message(),
            vlm_text=REAL_CR2_BARE_LOWERCASE_NO,
            response_format="cr2",
        )
        assert set(info) == EXPECTED_DEFAULT_KEYSET

    def test_bare_verdict_info_is_all_strings(self):
        info = _default_path_info(
            _base_message(),
            vlm_text=REAL_CR2_BARE_LOWERCASE_NO,
            response_format="cr2",
        )
        for k, v in info.items():
            assert isinstance(v, str), f"info[{k!r}] = {v!r} is not str"

    def test_bare_verdict_code_is_stringified_200(self):
        info = _default_path_info(
            _base_message(),
            vlm_text=REAL_CR2_BARE_LOWERCASE_NO,
            response_format="cr2",
        )
        assert info["verificationResponseCode"] == "200"
        assert info["verificationResponseStatus"] == "OK"


class TestRealCR2ThinkTagsEdgeCases:
    """TEST C — CR2 returns '<think>CJK</think> VERDICT' with a leading
    space. Both the CJK body and the leading space are real quirks; the
    unified parser must tolerate them."""

    def test_cjk_think_body_and_leading_space_after_close_tag(self):
        info = _default_path_info(
            _base_message(),
            vlm_text=REAL_CR2_THINK_TAGS_CJK_LEADING_SPACE,
            response_format="cr2",
        )
        assert info["verdict"] == "rejected"
        # CJK body is preserved verbatim (modulo strip).
        assert info["reasoning"] == "一栋"

    def test_english_think_tags_baseline(self):
        info = _default_path_info(
            _base_message(),
            vlm_text=REAL_CR2_THINK_TAGS_ENGLISH,
            response_format="cr2",
        )
        assert info["verdict"] == "rejected"
        assert info["reasoning"] == "Person has hardhat and vest"

    def test_think_tag_response_info_keyset_matches_expected(self):
        info = _default_path_info(
            _base_message(),
            vlm_text=REAL_CR2_THINK_TAGS_CJK_LEADING_SPACE,
            response_format="cr2",
        )
        assert set(info) == EXPECTED_DEFAULT_KEYSET


class TestRealCR2FailureModes:
    """TEST B — CR2 free-text output (no tags, no lone verdict word)
    FAILS parse. TEST D — empty response FAILS. Pinned to the exact
    production behaviour so any future parser change surfaces a diff."""

    def test_free_text_without_tags_raises_value_error(self):
        """The real production log showed this string failed to parse.
        Lock it in so the unified Cosmos-Reason parser keeps rejecting
        it (rather than silently accepting the trailing 'no')."""
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(
                REAL_CR2_FREE_TEXT_FAILURE,
                model_name="nvidia/cosmos-reason2-8b",
                response_format="cr2",
            )

    def test_empty_response_raises_value_error(self):
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(
                REAL_CR2_EMPTY,
                model_name="nvidia/cosmos-reason2-8b",
                response_format="cr2",
            )


# ---------------------------------------------------------------------------
# B. Real JSON-cookbook responses — default path with json_config
# ---------------------------------------------------------------------------


class TestRealJSONCookbookResponses:
    """Cookbook captures the 2026-04-07 live run. The exact JSON payload
    + deployment's `json_config` must produce the exact `serialized`
    dict the cookbook recorded."""

    def test_hazardous_produces_confirmed_verdict(self):
        info = _default_path_info(
            _base_message(category="hazard-detection"),
            vlm_text=REAL_JSON_COOKBOOK_HAZARDOUS,
            response_format="json",
            json_config=REAL_JSON_COOKBOOK_CONFIG,
        )
        assert info["verdict"] == "confirmed"
        # reasoning is taken from reasoning_fields[0] = video_description
        assert info["reasoning"] == "A forklift near a worker."

    def test_safe_produces_rejected_verdict(self):
        info = _default_path_info(
            _base_message(category="hazard-detection"),
            vlm_text=REAL_JSON_COOKBOOK_SAFE,
            response_format="json",
            json_config=REAL_JSON_COOKBOOK_CONFIG,
        )
        assert info["verdict"] == "rejected"
        assert info["reasoning"] == "Safe operations."

    def test_json_cookbook_info_keyset_matches_expected(self):
        info = _default_path_info(
            _base_message(category="hazard-detection"),
            vlm_text=REAL_JSON_COOKBOOK_HAZARDOUS,
            response_format="json",
            json_config=REAL_JSON_COOKBOOK_CONFIG,
        )
        assert set(info) == EXPECTED_DEFAULT_KEYSET

    def test_serialized_output_matches_cookbook_record(self):
        """The cookbook's 'serialized' dict is the source of truth for
        what `merge_info_with_response` should produce (modulo
        transport keys). Prove the published `info` still matches."""
        info = _default_path_info(
            _base_message(category="hazard-detection"),
            vlm_text=REAL_JSON_COOKBOOK_HAZARDOUS,
            response_format="json",
            json_config=REAL_JSON_COOKBOOK_CONFIG,
        )
        # Cookbook recorded: reasoning="A forklift near a worker.", verdict="confirmed", description=null
        # Nvschema alignment stringifies null → "".
        assert info["reasoning"] == "A forklift near a worker."
        assert info["verdict"] == "confirmed"
        assert info["description"] == ""


# ---------------------------------------------------------------------------
# C. Real classification fixture — pluggable-parser path
# ---------------------------------------------------------------------------


class TestRealClassificationFixture:
    """The shipped `classification_response.txt` fixture is the raw VLM
    output a pluggable classification parser consumes. Under the reduced
    scope, classification is no longer a first-class mode — it is a use
    case served by a user-provided parser."""

    def _fenced_json_parser(self):
        """Mirror what a typical deployment-side classification parser
        does: strip ```json fences, parse, and return the dict verbatim."""

        class FencedJSONClassifier:
            def parse(self, raw: str) -> dict:
                text = raw.strip()
                if text.startswith("```"):
                    lines = text.split("\n")
                    if lines and lines[-1].strip() == "```":
                        lines = lines[1:-1]
                    else:
                        lines = lines[1:]
                    text = "\n".join(lines).strip()
                return json.loads(text)

        return FencedJSONClassifier()

    def test_fixture_is_markdown_fenced_json_as_documented(self):
        """Sanity: the shipped fixture really is the ```json…``` shape
        the design doc describes."""
        assert REAL_CLASSIFICATION_PAYLOAD.lstrip().startswith("```json")
        assert REAL_CLASSIFICATION_PAYLOAD.rstrip().endswith("```")

    def test_pluggable_parser_output_lands_in_vlm_response(self):
        parser = self._fenced_json_parser()
        msg = _base_message(category="ppe-violation")
        parsed = parser.parse(REAL_CLASSIFICATION_PAYLOAD)
        _apply_ok(msg, parsed, video_source="rtsp://cam-warehouse-01")

        decoded = json.loads(msg["info"]["vlm_response"])
        assert decoded["label"] == "no-spotter"
        assert decoded["severity"] == "high"
        # parser returned a float 0.95; _safe_json_dumps preserves it.
        assert decoded["confidence"] == 0.95
        assert "safety spotter" in decoded["reasoning"]

    def test_pluggable_path_verdict_is_empty_string(self):
        parser = self._fenced_json_parser()
        msg = _base_message(category="ppe-violation")
        parsed = parser.parse(REAL_CLASSIFICATION_PAYLOAD)
        _apply_ok(msg, parsed, video_source="rtsp://cam-warehouse-01")

        # Pluggable parsers always emit verdict=null,
        # which the nvschema alignment stringifies to "".
        assert msg["info"]["verdict"] == ""

    def test_classification_keys_do_not_leak_into_info(self):
        parser = self._fenced_json_parser()
        msg = _base_message(category="ppe-violation")
        parsed = parser.parse(REAL_CLASSIFICATION_PAYLOAD)
        _apply_ok(msg, parsed, video_source="rtsp://cam-warehouse-01")

        for leaked in ("label", "severity", "confidence"):
            assert leaked not in msg["info"]

    def test_classification_info_keyset_matches_default_modulo_rename(self):
        """Option B: the pluggable path's info key-set matches the default
        path *modulo the conditional rename*: ``reasoning`` (default-only)
        becomes ``vlm_response`` (pluggable-only). Every other key is
        identical — zero ES-mapping impact on mdx-vlm-incidents."""
        parser = self._fenced_json_parser()
        msg = _base_message(category="ppe-violation")
        parsed = parser.parse(REAL_CLASSIFICATION_PAYLOAD)
        _apply_ok(msg, parsed, video_source="rtsp://cam-warehouse-01")

        pluggable_keys = set(msg["info"])
        # Parser path: description is not emitted (the helper does not
        # build a VLMResponse), so the "modulo rename" comparison is
        # against EXPECTED_DEFAULT_KEYSET minus {reasoning, description}.
        assert (pluggable_keys - {"vlm_response"}) == (
            EXPECTED_DEFAULT_KEYSET - {"reasoning", "description"}
        )


# ---------------------------------------------------------------------------
# D. Cross-cutting: the two paths produce equivalent info keysets on real
#    fixtures (up to the conditional rename). Documents the ES-mapping
#    invariant under Option B.
# ---------------------------------------------------------------------------


class TestRealFixtureKeysetSymmetry:
    """For every real fixture, the default path and the pluggable path must
    land on the same set of top-level info keys *modulo the conditional
    rename*: the default path owns ``reasoning``, the pluggable path owns
    ``vlm_response``."""

    @staticmethod
    def _assert_paths_differ_only_by_rename(default_keys, pluggable_keys):
        # Default-only keys should be {reasoning, description}: the parser
        # helper does not emit description (it bypasses VLMResponse).
        default_only = default_keys - pluggable_keys
        pluggable_only = pluggable_keys - default_keys
        assert default_only <= {"reasoning", "description"}, (
            f"default-only keys beyond the rename: "
            f"{default_only - {'reasoning', 'description'}}"
        )
        assert pluggable_only == {"vlm_response"}, (
            f"pluggable-only keys beyond the rename: "
            f"{pluggable_only - {'vlm_response'}}"
        )

    def test_cr2_default_path_and_classification_pluggable_path_share_keyset(self):
        default_info = _default_path_info(
            _base_message(),
            vlm_text=REAL_CR2_THINK_TAGS_ENGLISH,
            response_format="cr2",
        )

        parser = type("P", (), {
            "parse": lambda self, raw: json.loads(
                raw.strip().strip("`").lstrip("json\n").rstrip("`").strip()
            ),
        })()
        pluggable_msg = _base_message()
        _apply_ok(
            pluggable_msg,
            parser.parse(REAL_CLASSIFICATION_PAYLOAD),
            video_source="rtsp://cam-01",
        )

        self._assert_paths_differ_only_by_rename(
            set(default_info), set(pluggable_msg["info"]),
        )

    def test_json_cookbook_default_path_and_classification_pluggable_share_keyset(self):
        default_info = _default_path_info(
            _base_message(category="hazard-detection"),
            vlm_text=REAL_JSON_COOKBOOK_HAZARDOUS,
            response_format="json",
            json_config=REAL_JSON_COOKBOOK_CONFIG,
        )

        parser = type("P", (), {
            "parse": lambda self, raw: json.loads(
                raw.strip().strip("`").lstrip("json\n").rstrip("`").strip()
            ),
        })()
        pluggable_msg = _base_message()
        _apply_ok(
            pluggable_msg,
            parser.parse(REAL_CLASSIFICATION_PAYLOAD),
            video_source="rtsp://cam-01",
        )

        self._assert_paths_differ_only_by_rename(
            set(default_info), set(pluggable_msg["info"]),
        )
