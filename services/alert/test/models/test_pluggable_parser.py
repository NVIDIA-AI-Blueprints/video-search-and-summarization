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

"""Tests for the pluggable VLM response parser architecture."""

import json
import pytest
from unittest.mock import patch, MagicMock

from models.base_response_parser import BaseResponseParser, load_response_parser
from models.responses import (
    AlertBridgeResponse,
    merge_info_with_response,
)


# ============================================================================
# Sample parser implementations for testing
# ============================================================================

class PPEClassifier(BaseResponseParser):
    """Sample parser from the design doc."""

    def parse(self, raw_response: str) -> dict:
        data = json.loads(raw_response)
        return {
            "classification": data.get("label", "unknown"),
            "severity": data.get("severity", "unknown"),
            "confidence": str(data.get("confidence", 0)),
            "reasoning": data.get("reasoning", ""),
        }


class VehicleCounter(BaseResponseParser):
    """Sample analytics parser."""

    def parse(self, raw_response: str) -> dict:
        from collections import Counter
        data = json.loads(raw_response)
        types = data.get("vehicle_types", [])
        breakdown = dict(Counter(types))
        return {
            "vehicle_count": str(data.get("vehicle_count", 0)),
            "vehicle_breakdown": json.dumps(breakdown),
            "congestion_level": data.get("congestion_level", "unknown"),
        }


class BrokenParserReturnsList(BaseResponseParser):
    """Returns a list instead of dict — should be caught."""

    def parse(self, raw_response: str) -> dict:
        return ["not", "a", "dict"]  # type: ignore


class BrokenParserRaises(BaseResponseParser):
    """Always raises."""

    def parse(self, raw_response: str) -> dict:
        raise RuntimeError("Parser exploded")


class ExternalParser:
    """Plain class with parse() — no BaseResponseParser subclass.

    Simulates an external parser living outside the AB repo with zero
    dependency on Alert Bridge code.
    """

    def parse(self, raw_response: str) -> dict:
        data = json.loads(raw_response)
        return {"result": data.get("label", "unknown")}


class ClassWithoutParse:
    """Class that does NOT have a parse method."""

    def analyze(self, text: str) -> dict:
        return {}


# ============================================================================
# BaseResponseParser contract
# ============================================================================

class TestBaseResponseParserContract:

    def test_subclass_parse_returns_dict(self):
        parser = PPEClassifier()
        result = parser.parse(json.dumps({
            "label": "no-helmet",
            "severity": "high",
            "confidence": 0.92,
            "reasoning": "No hard hat detected",
        }))
        assert isinstance(result, dict)
        assert result["classification"] == "no-helmet"
        assert result["severity"] == "high"
        assert result["confidence"] == "0.92"
        assert result["reasoning"] == "No hard hat detected"

    def test_vehicle_counter_parser(self):
        parser = VehicleCounter()
        result = parser.parse(json.dumps({
            "vehicle_count": 5,
            "vehicle_types": ["car", "car", "truck", "car", "bus"],
            "congestion_level": "low",
        }))
        assert result["vehicle_count"] == "5"
        assert result["congestion_level"] == "low"
        breakdown = json.loads(result["vehicle_breakdown"])
        assert breakdown["car"] == 3
        assert breakdown["truck"] == 1

    def test_cannot_instantiate_abc_directly(self):
        with pytest.raises(TypeError):
            BaseResponseParser()

    def test_stringified_json_values(self):
        """Values can be stringified JSON per the pluggable-parser design."""
        parser = PPEClassifier()
        result = parser.parse(json.dumps({
            "label": "no-vest",
            "severity": "medium",
            "confidence": 0.85,
            "reasoning": "Missing vest",
        }))
        assert isinstance(result["confidence"], str)


# ============================================================================
# load_response_parser
# ============================================================================

class TestLoadResponseParser:

    def test_valid_dotted_path(self):
        parser = load_response_parser(
            "test.models.test_pluggable_parser.PPEClassifier"
        )
        assert isinstance(parser, PPEClassifier)
        assert callable(getattr(parser, "parse", None))

    def test_no_dot_in_path_raises(self):
        with pytest.raises(ValueError, match="must be 'module.ClassName'"):
            load_response_parser("NoDotPath")

    def test_empty_path_raises(self):
        with pytest.raises(ValueError, match="must be 'module.ClassName'"):
            load_response_parser("")

    def test_nonexistent_module_raises(self):
        with pytest.raises(ImportError, match="Failed to import"):
            load_response_parser("nonexistent.module.Parser")

    def test_nonexistent_class_raises(self):
        with pytest.raises(ValueError, match="has no attribute"):
            load_response_parser("test.models.test_pluggable_parser.NoSuchClass")

    def test_class_without_parse_raises(self):
        with pytest.raises(ValueError, match="does not declare a callable parse"):
            load_response_parser("test.models.test_pluggable_parser.ClassWithoutParse")

    def test_external_parser_no_subclass_loads(self):
        """Duck typing: class with parse() method loads without extending BaseResponseParser."""
        parser = load_response_parser(
            "test.models.test_pluggable_parser.ExternalParser"
        )
        result = parser.parse(json.dumps({"label": "fire"}))
        assert result == {"result": "fire"}

    def test_external_parser_is_not_base_subclass(self):
        """External parser is NOT a BaseResponseParser subclass — that's fine."""
        parser = load_response_parser(
            "test.models.test_pluggable_parser.ExternalParser"
        )
        assert not isinstance(parser, BaseResponseParser)

    def test_loaded_parser_is_functional(self):
        parser = load_response_parser(
            "test.models.test_pluggable_parser.VehicleCounter"
        )
        result = parser.parse(json.dumps({
            "vehicle_count": 3,
            "vehicle_types": ["car", "truck", "car"],
            "congestion_level": "low",
        }))
        assert result["vehicle_count"] == "3"


# ============================================================================
# Pipeline integration (merge into info)
# ============================================================================

def _apply_pluggable_parser_to_info(message, parsed, *, video_source, latency=None):
    """Mirror the orchestrator logic in enhance_alert_with_vlm.py.

    The pluggable parser (Option B): the pluggable path bypasses VLMResponse
    and writes parser JSON directly into ``info["vlm_response"]``. The
    default verification path keeps emitting ``info["reasoning"]`` — the
    two are disjoint so deployments that do not opt into
    ``vlm.response_parser`` see zero wire change.

    ``verdict`` is null (stringified to "" by the nvschema alignment).
    Parser output NEVER flat-merges into info.

    Runs through merge_info_with_response so the nvschema alignment's map<string,string>
    stringifier applies (None→"", int→str, dict→JSON).
    """
    merge_info_with_response(
        message,
        AlertBridgeResponse(
            vlm_response=None,
            video_source=video_source,
            verification_response_code=200,
            verification_response_status="OK",
        ),
        latency=latency,
        include_latency=bool(latency),
    )
    info = message.get("info") or {}
    info["vlm_response"] = json.dumps(parsed)
    # Pluggable parsers have no binary verdict; overwrite so stale verdicts
    # from retries / upstream pollution do not leak through.
    info["verdict"] = ""
    message["info"] = info
    return message


class TestPipelineMerge:
    """Simulate what the pipeline does with pluggable parser output.

    Option B (conditional rename):
      - parser output → json.dumps() into info["vlm_response"] (this key is
        EXCLUSIVE to the pluggable path; default verification emits
        info["reasoning"] instead so un-opted-in deployments see zero wire
        change)
      - info["verdict"] = null  → stringified to "" by the nvschema alignment enforcer
      - info schema matches verification mode *modulo the rename* above
      - raw VLM response is NOT stored separately (consumers parse vlm_response)

    The nvschema alignment: all values in info are coerced to
    str by merge_info_with_response — None→"", int→str, dict→JSON.
    """

    def test_parser_output_stringified_into_vlm_response(self):
        message = {
            "id": "evt-123",
            "sensorId": "cam-01",
            "category": "ppe-violation",
            "info": {"sensorId": "cam-01", "category": "ppe-violation"},
        }
        parser = PPEClassifier()
        parsed = parser.parse(json.dumps({
            "label": "no-helmet",
            "severity": "high",
            "confidence": 0.92,
            "reasoning": "Worker without hard hat",
        }))

        _apply_pluggable_parser_to_info(
            message, parsed, video_source="rtsp://cam-01/stream"
        )

        # vlm_response is a JSON string containing the parser output
        assert isinstance(message["info"]["vlm_response"], str)
        decoded = json.loads(message["info"]["vlm_response"])
        assert decoded["classification"] == "no-helmet"
        assert decoded["severity"] == "high"
        assert decoded["confidence"] == "0.92"

        # verdict is null for non-verification pluggable parsers; the nvschema alignment
        # stringifies None → "" so info stays map<string,string>.
        assert message["info"]["verdict"] == ""

        # Transport keys set by the pipeline (nvschema alignment: all values stringified)
        assert message["info"]["verificationResponseCode"] == "200"
        assert message["info"]["verificationResponseStatus"] == "OK"
        assert message["info"]["videoSource"] == "rtsp://cam-01/stream"

        # Pre-existing info fields preserved
        assert message["info"]["sensorId"] == "cam-01"
        assert message["info"]["category"] == "ppe-violation"

    def test_parser_output_keys_do_not_flat_merge_into_info(self):
        """Parser output is opaque (stringified into vlm_response). It MUST
        NOT add new keys to info — this is the structural protection for
        transport metadata."""
        message = {"info": {}}
        parser = PPEClassifier()
        parsed = parser.parse(json.dumps({
            "label": "no-helmet",
            "severity": "high",
            "confidence": 0.9,
            "reasoning": "x",
        }))
        _apply_pluggable_parser_to_info(
            message, parsed, video_source="rtsp://x"
        )
        assert "classification" not in message["info"]
        assert "severity" not in message["info"]
        assert "confidence" not in message["info"]

    def test_parser_cannot_spoof_transport_keys(self):
        """Even if parser returns keys like verificationResponseStatus,
        they land in vlm_response JSON — not in info — so transport metadata
        is structurally immune to spoofing."""
        message = {"info": {}}

        class MaliciousParser:
            def parse(self, raw):
                return {
                    "verificationResponseStatus": "spoofed",
                    "videoSource": "evil://attacker",
                    "verdict": "confirmed",
                }

        parsed = MaliciousParser().parse("anything")
        _apply_pluggable_parser_to_info(
            message, parsed, video_source="rtsp://legit"
        )

        assert message["info"]["verificationResponseStatus"] == "OK"
        assert message["info"]["videoSource"] == "rtsp://legit"
        assert message["info"]["verdict"] == ""

        # The spoofed values are captured inside vlm_response (opaque blob)
        decoded = json.loads(message["info"]["vlm_response"])
        assert decoded["verificationResponseStatus"] == "spoofed"
        assert decoded["videoSource"] == "evil://attacker"

    def test_preserves_original_info_fields(self):
        message = {
            "info": {"existing_field": "keep_me", "timestamp": "2026-01-01"},
        }
        parser = PPEClassifier()
        parsed = parser.parse(json.dumps({
            "label": "compliant",
            "severity": "low",
            "confidence": 0.99,
            "reasoning": "All PPE present",
        }))
        _apply_pluggable_parser_to_info(
            message, parsed, video_source="rtsp://x"
        )

        assert message["info"]["existing_field"] == "keep_me"
        assert message["info"]["timestamp"] == "2026-01-01"

    def test_stale_vlm_response_is_overwritten(self):
        """If message['info'] already has a stale vlm_response from a retry
        or upstream, the current parser output must overwrite it."""
        stale = '{"label": "compliant", "severity": "low"}'
        message = {
            "id": "evt-retry",
            "info": {"vlm_response": stale, "verdict": "confirmed"},
        }
        parser = PPEClassifier()
        parsed = parser.parse(json.dumps({
            "label": "no-helmet",
            "severity": "high",
            "confidence": 0.9,
            "reasoning": "new run",
        }))
        _apply_pluggable_parser_to_info(
            message, parsed, video_source="rtsp://x"
        )

        decoded = json.loads(message["info"]["vlm_response"])
        assert decoded["classification"] == "no-helmet"
        assert message["info"]["vlm_response"] != stale
        assert message["info"]["verdict"] == ""  # stale verdict cleared (nvschema alignment: None→"")

    def test_empty_info_handled(self):
        message = {"info": None}
        parser = PPEClassifier()
        parsed = parser.parse(json.dumps({
            "label": "no-helmet",
            "severity": "high",
            "confidence": 0.9,
            "reasoning": "test",
        }))
        _apply_pluggable_parser_to_info(
            message, parsed, video_source="rtsp://x"
        )

        assert isinstance(message["info"], dict)
        decoded = json.loads(message["info"]["vlm_response"])
        assert decoded["classification"] == "no-helmet"
        assert message["info"]["verdict"] == ""

    def test_latency_included_when_configured(self):
        message = {"info": {}}
        parser = PPEClassifier()
        parsed = parser.parse(json.dumps({
            "label": "no-helmet",
            "severity": "high",
            "confidence": 0.9,
            "reasoning": "x",
        }))
        latency = {"vlm_request": {"success": True, "duration": 1.23}}
        _apply_pluggable_parser_to_info(
            message, parsed, video_source="rtsp://x", latency=latency
        )

        # Nvschema alignment: latency dict is JSON-stringified in info
        assert message["info"]["latency"] == json.dumps(
            latency, separators=(',', ':')
        )


# ============================================================================
# Error handling
# ============================================================================

class TestErrorHandling:

    def test_parser_returns_non_dict_detected(self):
        parser = BrokenParserReturnsList()
        result = parser.parse("anything")
        assert not isinstance(result, dict)

    def test_parser_raises_propagates(self):
        parser = BrokenParserRaises()
        with pytest.raises(RuntimeError, match="Parser exploded"):
            parser.parse("anything")

    def test_invalid_json_input(self):
        parser = PPEClassifier()
        with pytest.raises(json.JSONDecodeError):
            parser.parse("not valid json {{{")


# ============================================================================
# Default behavior (no response_parser configured)
# ============================================================================

class TestDefaultBehavior:
    """When no response_parser is configured, existing flow is unchanged."""

    def test_no_config_returns_none(self):
        """Simulates _load_pluggable_parser when config has no response_parser."""
        config = {"vlm": {"model": "nvidia/cosmos-reason2-8b"}}
        dotted_path = config.get("vlm", {}).get("response_parser")
        assert dotted_path is None

    def test_default_verification_path_unaffected(self):
        """When response_parser is absent, default CR parsing works normally."""
        from models.responses import VLMResponse

        text = "<think>I see a collision.</think>\n\nYES"
        vlm = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert vlm.verdict == "YES"
        assert vlm.reasoning == "I see a collision."


# ============================================================================
# End-to-end: realistic VLM responses
# ============================================================================

class TestEndToEnd:
    """Full flow with realistic VLM response strings."""

    def test_classification_ppe(self):
        parser = PPEClassifier()
        vlm_output = json.dumps({
            "label": "no-helmet",
            "severity": "high",
            "confidence": 0.92,
            "reasoning": "Worker near active forklift zone without hard hat",
        })
        result = parser.parse(vlm_output)
        assert result == {
            "classification": "no-helmet",
            "severity": "high",
            "confidence": "0.92",
            "reasoning": "Worker near active forklift zone without hard hat",
        }

    def test_analytics_vehicle_count(self):
        parser = VehicleCounter()
        vlm_output = json.dumps({
            "vehicle_count": 12,
            "vehicle_types": [
                "car", "car", "truck", "car", "bus", "car", "car",
                "motorcycle", "car", "car", "truck", "car",
            ],
            "congestion_level": "medium",
        })
        result = parser.parse(vlm_output)
        assert result["vehicle_count"] == "12"
        assert result["congestion_level"] == "medium"
        breakdown = json.loads(result["vehicle_breakdown"])
        assert breakdown["car"] == 8
        assert breakdown["truck"] == 2
        assert breakdown["bus"] == 1
        assert breakdown["motorcycle"] == 1

    def test_enhancement_scene_description(self):
        class SceneEnhancer(BaseResponseParser):
            def parse(self, raw_response: str) -> dict:
                data = json.loads(raw_response)
                return {
                    "summary": data.get("summary", ""),
                    "objects_detected": json.dumps(data.get("objects", [])),
                    "severity": str(data.get("severity", 0)),
                    "recommended_action": data.get("recommended_action", ""),
                    "reasoning": data.get("reasoning", ""),
                }

        parser = SceneEnhancer()
        vlm_output = json.dumps({
            "summary": "Forklift operating in pedestrian zone with no spotter",
            "objects": ["forklift", "worker", "pallet", "safety-cone"],
            "severity": 4,
            "recommended_action": "Dispatch safety officer to warehouse zone A",
            "reasoning": "Forklift is within 2m of walking path",
        })
        result = parser.parse(vlm_output)
        assert result["summary"] == "Forklift operating in pedestrian zone with no spotter"
        assert result["severity"] == "4"
        objects = json.loads(result["objects_detected"])
        assert "forklift" in objects
        assert len(objects) == 4

    def test_xml_vlm_response(self):
        """Parser handles non-JSON VLM responses (XML example from design doc)."""
        import re

        class XMLClassifier(BaseResponseParser):
            def parse(self, raw_response: str) -> dict:
                label = re.search(r"<label>(.*?)</label>", raw_response)
                severity = re.search(r"<severity>(.*?)</severity>", raw_response)
                return {
                    "classification": label.group(1) if label else "unknown",
                    "severity": severity.group(1) if severity else "unknown",
                }

        parser = XMLClassifier()
        vlm_output = (
            "<classification>\n"
            "  <label>no-helmet</label>\n"
            "  <severity>high</severity>\n"
            "</classification>"
        )
        result = parser.parse(vlm_output)
        assert result["classification"] == "no-helmet"
        assert result["severity"] == "high"

    def test_full_pipeline_simulation(self):
        """Simulate the complete pipeline: VLM call → parser → info.

        Matches the expected output shape:
          info = {primaryObjectId / preserved fields, verdict: null,
                  vlm_response: <stringified parser output>,
                  verificationResponseCode, verificationResponseStatus,
                  videoSource}
        """
        parser = PPEClassifier()
        raw_vlm = json.dumps({
            "label": "no-goggles",
            "severity": "medium",
            "confidence": 0.78,
            "reasoning": "Safety goggles not detected on worker",
        })

        message = {
            "id": "alert-456",
            "sensorId": "factory-cam-12",
            "category": "ppe-violation",
            "info": {
                "sensorId": "factory-cam-12",
                "category": "ppe-violation",
                "timestamp": "2026-04-16T10:00:00Z",
                "primaryObjectId": "36044",
            },
        }

        parsed = parser.parse(raw_vlm)
        assert isinstance(parsed, dict)

        _apply_pluggable_parser_to_info(
            message, parsed,
            video_source="https://vst.example.com/video.mp4",
        )

        assert message["info"]["sensorId"] == "factory-cam-12"
        assert message["info"]["category"] == "ppe-violation"
        assert message["info"]["timestamp"] == "2026-04-16T10:00:00Z"
        assert message["info"]["primaryObjectId"] == "36044"
        # Nvschema alignment: verdict None → "", code int → "200" (map<string,string>)
        assert message["info"]["verdict"] == ""
        assert message["info"]["videoSource"] == "https://vst.example.com/video.mp4"
        assert message["info"]["verificationResponseCode"] == "200"
        assert message["info"]["verificationResponseStatus"] == "OK"

        # Parser output lives inside vlm_response as a JSON string
        decoded = json.loads(message["info"]["vlm_response"])
        assert decoded == {
            "classification": "no-goggles",
            "severity": "medium",
            "confidence": "0.78",
            "reasoning": "Safety goggles not detected on worker",
        }

        # Parser output does NOT flat-merge into info
        assert "classification" not in message["info"]
        assert "severity" not in message["info"]
        assert "confidence" not in message["info"]
