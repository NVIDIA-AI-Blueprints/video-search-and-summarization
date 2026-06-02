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

"""Functional test for the pluggable VLM response parser (C9).

Functional test that:
  - loads a REAL classification parsing snippet
  - as an EXTERNAL Python module (outside the AB repo, on PYTHONPATH)
  - and verifies the parsed result against the expected output.

This test creates a parser module on disk under a temporary directory,
puts that directory on ``sys.path`` (simulating the Phase-6 deployment
convention of mounting ``/app/parsers`` via Docker volume + PYTHONPATH),
calls the real :func:`load_response_parser` dotted-path loader, feeds
it a realistic Cosmos-Reason-flavoured JSON payload, and asserts both
the parsed dict and the final ``info`` shape produced by the
orchestrator helper.

Covers end-to-end:
  1. Dotted-path loading contract (ValueError / ImportError paths)
  2. Duck-typed parser contract (no BaseResponseParser subclass needed)
  3. Parser output → ``info["vlm_response"]`` JSON-stringification
  4. ``info["verdict"] == ""`` (nvschema alignment stringified null)
  5. Transport metadata set by orchestrator, not parser
  6. Preexisting info keys preserved
  7. No new top-level keys leak from parser into info
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
import textwrap
import types
from pathlib import Path
from unittest.mock import Mock

import pytest


# ---------------------------------------------------------------------------
# Stub the broader AB runtime the same way the orchestrator unit tests do,
# so we can import enhance_alert_with_vlm for its pluggable-parser helpers.
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


class _DispatchMixinStub: pass
class _IOMixinStub: pass
class _VLMMixinStub: pass


sys.modules['handlers.async_dispatch_mixin'].AsyncDispatchMixin = _DispatchMixinStub
sys.modules['handlers.async_external_io_mixin'].AsyncExternalIOMixin = _IOMixinStub
sys.modules['handlers.async_vlm_mode_mixin'].AsyncVLMModeMixin = _VLMMixinStub

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


# Import real production helpers — this is the whole point of the FT.
from models.base_response_parser import load_response_parser  # noqa: E402
import enhance_alert_with_vlm as _eaw  # noqa: E402

_apply_ok = _eaw._apply_pluggable_parser_output
_apply_err = _eaw._apply_pluggable_parser_error
_OK = _eaw._PLUGGABLE_PARSER_OK_STATUS


# ---------------------------------------------------------------------------
# Realistic external parser source — what a deployment team would ship.
# ---------------------------------------------------------------------------

# Keep this as source text (string) so it is genuinely written to disk and
# imported via the file system. No shortcuts via sys.modules pre-population.
_EXTERNAL_PPE_PARSER_SRC = textwrap.dedent(
    """\
    # PPE classification parser.  Lives OUTSIDE alert-bridge — no AB imports.
    # Accepts a Cosmos-Reason-flavoured response that wraps a JSON payload
    # between <think>...</think> tags, extracts the JSON, and maps it to a
    # flat dict suitable for info["vlm_response"] serialisation.
    import json
    import re


    class PPEClassifier:
        _LABEL_TO_SEVERITY = {
            "no-helmet": "high",
            "no-vest": "medium",
            "no-gloves": "low",
            "compliant": "none",
        }

        _JSON_RE = re.compile(r"\\{.*\\}", re.DOTALL)

        def parse(self, raw_response):
            # Strip <think>...</think> blocks if present; otherwise take the
            # first {...} blob we see.
            text = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL)
            m = self._JSON_RE.search(text)
            if not m:
                raise ValueError("no JSON object found in VLM response")
            payload = json.loads(m.group(0))

            label = payload.get("label", "unknown")
            severity = self._LABEL_TO_SEVERITY.get(label, "unknown")
            return {
                "classification": label,
                "severity": severity,
                "confidence": str(payload.get("confidence", 0.0)),
                "reasoning": payload.get("reasoning", ""),
            }
    """
)


_EXTERNAL_BROKEN_PARSER_SRC = textwrap.dedent(
    """\
    # Intentionally broken — used to exercise the load-time failure paths.
    class NotAClass_just_a_function:
        pass


    NotAClass_just_a_function = lambda: None  # reassign to non-class


    class MissingParseMethod:
        def analyze(self, raw):
            return {}
    """
)


_EXTERNAL_DATETIME_PARSER_SRC = textwrap.dedent(
    """\
    # Returns non-JSON-primitive values; exercises the _safe_json_dumps fallback.
    import datetime, decimal


    class TimestampedClassifier:
        def parse(self, raw_response):
            return {
                "classification": "event",
                "captured_at": datetime.datetime(2026, 3, 30, 12, 0, 0,
                                                 tzinfo=datetime.timezone.utc),
                "confidence": decimal.Decimal("0.9321"),
            }
    """
)


# ---------------------------------------------------------------------------
# Fixture: drop external parser files under a temp directory that we add
# to sys.path, mimicking the production deployment pattern (mount under
# /app/parsers and prepend to PYTHONPATH).
# ---------------------------------------------------------------------------


@pytest.fixture
def external_parsers_dir(tmp_path, monkeypatch):
    """Write the external parser modules and put tmp_path on sys.path."""
    pkg_dir = tmp_path / "external_parsers"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "ppe.py").write_text(_EXTERNAL_PPE_PARSER_SRC)
    (pkg_dir / "broken.py").write_text(_EXTERNAL_BROKEN_PARSER_SRC)
    (pkg_dir / "timestamped.py").write_text(_EXTERNAL_DATETIME_PARSER_SRC)

    monkeypatch.syspath_prepend(str(tmp_path))
    # Purge any previously-imported copies so each test gets fresh state.
    for name in list(sys.modules):
        if name.startswith("external_parsers"):
            del sys.modules[name]
    importlib.invalidate_caches()
    yield pkg_dir
    for name in list(sys.modules):
        if name.startswith("external_parsers"):
            del sys.modules[name]


# ---------------------------------------------------------------------------
# Dotted-path loader contract
# ---------------------------------------------------------------------------


class TestExternalParserLoading:
    """load_response_parser must resolve external dotted paths correctly."""

    def test_loads_real_external_module_and_class(self, external_parsers_dir):
        parser = load_response_parser("external_parsers.ppe.PPEClassifier")
        # Duck-typed — class is defined with zero AB imports.
        assert callable(getattr(parser, "parse", None))
        # And it's the class from the on-disk file, not a pre-stubbed mock.
        assert type(parser).__name__ == "PPEClassifier"
        assert type(parser).__module__ == "external_parsers.ppe"

    def test_malformed_dotted_path_raises_value_error(self):
        with pytest.raises(ValueError, match="module.ClassName"):
            load_response_parser("no_dots_here")

    def test_unknown_module_raises_import_error(self, external_parsers_dir):
        with pytest.raises(ImportError):
            load_response_parser("external_parsers.does_not_exist.Foo")

    def test_missing_class_attr_raises_value_error(self, external_parsers_dir):
        with pytest.raises(ValueError, match="has no attribute"):
            load_response_parser("external_parsers.ppe.DoesNotExist")

    def test_non_class_target_raises_value_error(self, external_parsers_dir):
        with pytest.raises(ValueError, match="is not a class"):
            load_response_parser("external_parsers.broken.NotAClass_just_a_function")

    def test_class_without_parse_raises_value_error(self, external_parsers_dir):
        with pytest.raises(ValueError, match="parse"):
            load_response_parser("external_parsers.broken.MissingParseMethod")


# ---------------------------------------------------------------------------
# Parsed result → expected output
# ---------------------------------------------------------------------------


class TestExternalParserParsedResult:
    """Feed a realistic VLM response; assert the parser returns what
    the design doc promises (flat dict, classification + severity + etc.)."""

    _SAMPLE_VLM_RESPONSE = (
        "<think>The worker at the front of the scene has a hard hat and "
        "safety vest on, but no gloves.</think>\n"
        '{"label": "no-gloves", "confidence": 0.87, '
        '"reasoning": "Worker visible at frame center is missing gloves"}'
    )

    def test_parse_returns_expected_dict_shape(self, external_parsers_dir):
        parser = load_response_parser("external_parsers.ppe.PPEClassifier")
        parsed = parser.parse(self._SAMPLE_VLM_RESPONSE)

        assert parsed == {
            "classification": "no-gloves",
            "severity": "low",
            "confidence": "0.87",
            "reasoning": "Worker visible at frame center is missing gloves",
        }

    def test_parse_handles_compliant_label(self, external_parsers_dir):
        parser = load_response_parser("external_parsers.ppe.PPEClassifier")
        parsed = parser.parse('{"label":"compliant","confidence":0.99}')
        assert parsed["classification"] == "compliant"
        assert parsed["severity"] == "none"

    def test_parse_raises_on_malformed_vlm_output(self, external_parsers_dir):
        parser = load_response_parser("external_parsers.ppe.PPEClassifier")
        with pytest.raises((ValueError, json.JSONDecodeError)):
            parser.parse("no JSON at all here, just words")


# ---------------------------------------------------------------------------
# End-to-end: external parser output → orchestrator helper → info shape
# ---------------------------------------------------------------------------


class TestExternalParserEndToEnd:
    """Full pipeline: dotted-path load → parse() → _apply_pluggable_parser_output
    produces the info schema promised by the design doc."""

    def _base_message(self):
        return {
            "id": "evt-ft-001",
            "sensorId": "cam-warehouse-01",
            "category": "ppe-violation",
            "info": {
                "sensorId": "cam-warehouse-01",
                "category": "ppe-violation",
                "primaryObjectId": "36044",
            },
        }

    def test_full_pipeline_info_shape(self, external_parsers_dir):
        parser = load_response_parser("external_parsers.ppe.PPEClassifier")
        msg = self._base_message()
        parsed = parser.parse(
            '{"label":"no-helmet","confidence":0.92,'
            '"reasoning":"Hard hat missing at frame center"}'
        )
        _apply_ok(msg, parsed, video_source="rtsp://cam-warehouse-01/stream")

        info = msg["info"]

        # 1. vlm_response holds serialised parser output
        decoded = json.loads(info["vlm_response"])
        assert decoded["classification"] == "no-helmet"
        assert decoded["severity"] == "high"
        assert decoded["confidence"] == "0.92"
        assert decoded["reasoning"] == "Hard hat missing at frame center"

        # 2. verdict is "" (nvschema alignment stringified null)
        assert info["verdict"] == ""

        # 3. transport metadata set by orchestrator
        assert info["verificationResponseCode"] == "200"
        assert info["verificationResponseStatus"] == _OK
        assert info["videoSource"] == "rtsp://cam-warehouse-01/stream"

        # 4. preexisting keys preserved
        assert info["sensorId"] == "cam-warehouse-01"
        assert info["category"] == "ppe-violation"
        assert info["primaryObjectId"] == "36044"

        # 5. parser keys did NOT flat-merge into info
        for leaked in ("classification", "severity", "confidence"):
            assert leaked not in info

        # 6. map<string,string> invariant
        for k, v in info.items():
            assert isinstance(v, str), f"info[{k!r}] = {v!r} must be str"

    def test_no_unexpected_top_level_keys_in_info(self, external_parsers_dir):
        """Tight whitelist: after running an external parser the info dict
        contains ONLY pre-existing keys + the well-known transport keys."""
        parser = load_response_parser("external_parsers.ppe.PPEClassifier")
        msg = self._base_message()
        parsed = parser.parse('{"label":"no-helmet","confidence":0.9,"reasoning":"x"}')
        _apply_ok(msg, parsed, video_source="rtsp://x")

        allowed = {
            "sensorId", "category", "primaryObjectId",  # preserved
            # Pluggable-path helper emits exactly these (Option B):
            "vlm_response",        # parser JSON
            "verdict",             # empty string (null stringified)
            "videoSource",
            "verificationResponseCode", "verificationResponseStatus",
        }
        unexpected = set(msg["info"]) - allowed
        assert not unexpected, f"Unexpected info keys leaked from parser: {unexpected}"
        # Option B fence: default-path-only key must never appear on parser path.
        assert "reasoning" not in msg["info"], (
            "info['reasoning'] is default-path only (Option B)."
        )

    def test_parser_raising_flows_into_error_helper(self, external_parsers_dir):
        """When the external parser raises (e.g. malformed VLM output), the
        orchestrator's _apply_pluggable_parser_error is the correct handler
        and the info carries the explicit parser-failed status."""
        parser = load_response_parser("external_parsers.ppe.PPEClassifier")
        msg = self._base_message()
        try:
            parser.parse("this is not JSON and has no braces")
        except Exception as exc:
            _apply_err(msg, exc, video_source="rtsp://cam-warehouse-01/stream")

        info = msg["info"]
        assert info["verificationResponseCode"] == "500"
        assert info["verdict"] == "verification-failed"
        assert info["verificationResponseStatus"].startswith("Pluggable parser failed")
        # And NOT the VLM-schema error path
        assert "Incorrect VLM response schema" not in info["verificationResponseStatus"]

    def test_non_json_primitive_parser_output_survives(self, external_parsers_dir):
        """External parser returning datetime + Decimal → _safe_json_dumps
        fallback keeps the pipeline alive (no TypeError)."""
        parser = load_response_parser(
            "external_parsers.timestamped.TimestampedClassifier"
        )
        msg = {"info": {"sensorId": "cam-ts-01"}}
        parsed = parser.parse("anything")
        _apply_ok(msg, parsed, video_source="rtsp://cam-ts-01")

        # Decoded vlm_response contains the coerced date and decimal.
        decoded = json.loads(msg["info"]["vlm_response"])
        assert decoded["classification"] == "event"
        assert "2026-03-30" in decoded["captured_at"]
        assert str(decoded["confidence"]) == "0.9321"
