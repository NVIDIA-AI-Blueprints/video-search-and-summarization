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

"""Unit tests for models/responses.py, specifically merge_info_with_response and VLMResponse parsing."""

import json

import pytest

from models.responses import (
    AlertBridgeResponse,
    VLMResponse,
    merge_info_with_response,
    register_parser,
    _custom_parsers,
)


class TestVLMResponseParsing:
    """Tests for VLMResponse.model_validate_text() - CR2 format parsing."""

    def test_parse_cr2_format_yes(self):
        """Test parsing CR2 format with YES answer."""
        text = "<think>The video shows a person entering through an authorized door.</think>\n\nYES"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert response.reasoning == "The video shows a person entering through an authorized door."
        assert response.verdict == "YES"

    def test_parse_cr2_format_no(self):
        """Test parsing CR2 format with NO answer."""
        text = "<think>No collision detected in the footage.</think>\n\nNO"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert response.reasoning == "No collision detected in the footage."
        assert response.verdict == "NO"

    def test_parse_cr2_format_lowercase(self):
        """Test parsing CR2 format with lowercase answers."""
        text = "<think>Analysis complete.</think>\n\nyes"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert response.verdict == "YES"

        text = "<think>Analysis complete.</think>\n\nno"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert response.verdict == "NO"

    def test_parse_cr2_format_single_newline(self):
        """Test parsing CR2 format with single newline separator."""
        text = "<think>Reasoning here.</think>\nYES"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert response.verdict == "YES"

    def test_parse_cr2_format_double_newline(self):
        """Test parsing CR2 format with double newline separator."""
        text = "<think>Reasoning here.</think>\n\nNO"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert response.verdict == "NO"

    def test_parse_cr2_format_space_separator(self):
        """Test parsing CR2 format with space separator."""
        text = "<think>Reasoning here.</think> YES"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert response.verdict == "YES"

    def test_parse_cr2_format_no_separator(self):
        """Test parsing CR2 format with no separator."""
        text = "<think>Reasoning here.</think>YES"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert response.verdict == "YES"

    def test_parse_cr2_format_a_b_answers(self):
        """Test parsing CR2 format with A/B answers."""
        text = "<think>Option A is correct.</think>\n\nA"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert response.verdict == "A"

        text = "<think>Option B is correct.</think>\n\nB"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert response.verdict == "B"

    def test_parse_cr2_format_multiline_reasoning(self):
        """Test parsing CR2 format with multiline reasoning."""
        text = """<think>First I observe the video.
Then I analyze the scene.
Finally I make my determination.</think>

YES"""
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert "First I observe" in response.reasoning
        assert "Finally I make" in response.reasoning
        assert response.verdict == "YES"

    def test_parse_cr2_format_non_standard_verdict_raises(self):
        """Layer 1 rejects non-binary verdicts via the VerdictType Literal."""
        text = "<think>Analysis complete.</think>\n\nMAYBE"
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")

        text = "<think>Analysis complete.</think>\n\nUNKNOWN"
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")

    def test_parse_cr2_format_empty_answer_raises(self):
        """Test that empty answer text raises ValueError."""
        text = "<think>Reasoning here.</think>\n\n"
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")

        text = "<think>Reasoning here.</think>"
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")

    def test_parse_cr2_format_non_standard_answer_raises(self):
        """Layer 1 rejects arbitrary non-binary answer text."""
        text = "<think>Analysis complete.</think>\n\nThe collision was confirmed in the video."
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")

        text = "<think>Analysis complete.</think>\n\nMAYBE"
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")

    def test_parse_cr2_direct_verdict_succeeds(self):
        """Test CR2 direct verdict without think tags succeeds (B1 fallback case)."""
        # Direct verdict without <think> tags is allowed in CR2 (B1 case)
        response = VLMResponse.model_validate_text("YES", model_name="nvidia/cosmos-reason2-7b")
        assert response.verdict == "YES"
        assert response.reasoning == ""

        response = VLMResponse.model_validate_text("NO", model_name="nvidia/cosmos-reason2-7b")
        assert response.verdict == "NO"
        assert response.reasoning == ""

    def test_verdict_serialization_yes_to_confirmed(self):
        """Test that YES verdict serializes to 'confirmed'."""
        response = VLMResponse(reasoning="test", verdict="YES")
        dumped = response.model_dump()
        assert dumped["verdict"] == "confirmed"

    def test_verdict_serialization_no_to_rejected(self):
        """Test that NO verdict serializes to 'rejected'."""
        response = VLMResponse(reasoning="test", verdict="NO")
        dumped = response.model_dump()
        assert dumped["verdict"] == "rejected"


class TestMergeInfoWithResponse:
    """Tests for merge_info_with_response utility function."""

    def test_preserves_original_info_fields(self):
        """Test that original info fields like primaryObjectId are preserved."""
        message = {
            "sensorId": "HWY_20_AND_WACKER__EBA",
            "timestamp": "2025-12-17T17:04:39.248Z",
            "category": "collision",
            "info": {
                "primaryObjectId": "141395",
                "location": "42.492120993533035,-90.72281178320493,0.0"
            }
        }

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source="http://example.com/video.mp4",
                verification_response_code=200,
                verification_response_status="OK",
            ),
        )

        # Original fields preserved
        assert message["info"]["primaryObjectId"] == "141395"
        assert message["info"]["location"] == "42.492120993533035,-90.72281178320493,0.0"
        # New fields added
        assert message["info"]["videoSource"] == "http://example.com/video.mp4"
        assert message["info"]["verificationResponseCode"] == "200"
        assert message["info"]["verificationResponseStatus"] == "OK"

    def test_works_with_empty_original_info(self):
        """Test that merge works when original info is empty dict."""
        message = {
            "sensorId": "sensor1",
            "info": {}
        }

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source="http://example.com/video.mp4",
                verification_response_code=404,
                verification_response_status="Not found",
            ),
        )

        assert message["info"]["videoSource"] == "http://example.com/video.mp4"
        assert message["info"]["verificationResponseCode"] == "404"

    def test_works_with_missing_info(self):
        """Test that merge works when info key is missing entirely."""
        message = {
            "sensorId": "sensor1",
        }

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source="http://example.com/video.mp4",
                verification_response_code=500,
                verification_response_status="Error",
            ),
        )

        assert "info" in message
        assert message["info"]["videoSource"] == "http://example.com/video.mp4"
        assert message["info"]["verificationResponseCode"] == "500"

    def test_works_with_none_info(self):
        """Test that merge works when info is None."""
        message = {
            "sensorId": "sensor1",
            "info": None
        }

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source="http://example.com/video.mp4",
                verification_response_code=200,
                verification_response_status="OK",
            ),
        )

        assert message["info"]["videoSource"] == "http://example.com/video.mp4"

    def test_response_fields_override_original_on_conflict(self):
        """Test that AlertBridgeResponse fields override original info on key collision."""
        message = {
            "sensorId": "sensor1",
            "info": {
                "primaryObjectId": "141395",
                "video_source": "old_value",  # This will be overwritten
            }
        }

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source="new_value",
                verification_response_code=200,
                verification_response_status="OK",
            ),
        )

        # Original field preserved
        assert message["info"]["primaryObjectId"] == "141395"
        # Response field overwrites existing
        assert message["info"]["videoSource"] == "new_value"

    def test_includes_latency_when_flag_set(self):
        """Test that latency is included when include_latency=True."""
        message = {
            "sensorId": "sensor1",
            "info": {"primaryObjectId": "123"}
        }
        latency = {"vlm_request": {"success": True, "duration": 1.5}}

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source="http://example.com/video.mp4",
                verification_response_code=200,
                verification_response_status="OK",
            ),
            latency=latency,
            include_latency=True,
        )

        import json
        assert message["info"]["latency"] == json.dumps(latency, separators=(',', ':'))
        assert message["info"]["primaryObjectId"] == "123"

    def test_excludes_latency_when_flag_false(self):
        """Test that latency is excluded when include_latency=False."""
        message = {
            "sensorId": "sensor1",
            "info": {"primaryObjectId": "123"}
        }
        latency = {"vlm_request": {"success": True, "duration": 1.5}}

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source="http://example.com/video.mp4",
                verification_response_code=200,
                verification_response_status="OK",
            ),
            latency=latency,
            include_latency=False,
        )

        assert "latency" not in message["info"]

    def test_excludes_latency_when_none(self):
        """Test that latency is not added when latency dict is None."""
        message = {
            "sensorId": "sensor1",
            "info": {}
        }

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source="http://example.com/video.mp4",
                verification_response_code=200,
                verification_response_status="OK",
            ),
            latency=None,
            include_latency=True,
        )

        assert "latency" not in message["info"]

    def test_with_vlm_response_flattens_correctly(self):
        """Test that VLMResponse is flattened into info."""
        message = {
            "sensorId": "sensor1",
            "info": {"primaryObjectId": "141395"}
        }

        vlm_response = VLMResponse(reasoning="Test reasoning", verdict="YES")

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=vlm_response,
                video_source="http://example.com/video.mp4",
                verification_response_code=200,
                verification_response_status="OK",
            ),
        )

        assert message["info"]["primaryObjectId"] == "141395"
        assert message["info"]["reasoning"] == "Test reasoning"
        assert message["info"]["verdict"] == "confirmed"  # YES maps to confirmed
        assert message["info"]["videoSource"] == "http://example.com/video.mp4"

    def test_handles_non_dict_info_gracefully(self):
        """Test that non-dict info values are handled gracefully."""
        message = {
            "sensorId": "sensor1",
            "info": "invalid_string_value"
        }

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source="http://example.com/video.mp4",
                verification_response_code=200,
                verification_response_status="OK",
            ),
        )

        # Should replace with dict containing response fields
        assert isinstance(message["info"], dict)
        assert message["info"]["videoSource"] == "http://example.com/video.mp4"

    def test_verification_failed_verdict_when_vlm_response_none(self):
        """Test that verification-failed verdict is used when vlm_response is None."""
        message = {
            "sensorId": "HWY_20_AND_LOCUST__WBA",
            "category": "collision",
            "info": {"primaryObjectId": "141395"}
        }

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source=None,
                verification_response_code=404,
                verification_response_status="No video recording found for timestamp",
                verdict="verification-failed",
            ),
        )

        assert message["info"]["verdict"] == "verification-failed"
        assert message["info"]["verificationResponseCode"] == "404"
        assert message["info"]["primaryObjectId"] == "141395"

    def test_verification_failed_verdict_when_vlm_parse_fails(self):
        """Test that verification-failed verdict is set when VLM response parsing fails."""
        message = {
            "sensorId": "HWY_20_AND_LOCUST__WBA",
            "category": "collision",
            "info": {}
        }

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=None,
                video_source="http://example.com/video.mp4",
                verification_response_code=500,
                verification_response_status="VLM response parsing failed [sensor=HWY_20_AND_LOCUST__WBA category=collision]: Text is not in expected format",
                verdict="verification-failed",
            ),
        )

        assert message["info"]["verdict"] == "verification-failed"
        assert message["info"]["verificationResponseCode"] == "500"
        assert "VLM response parsing failed" in message["info"]["verificationResponseStatus"]

    def test_vlm_response_verdict_takes_precedence_over_standalone(self):
        """Test that VLM response verdict overrides standalone verdict field."""
        message = {
            "sensorId": "sensor1",
            "info": {}
        }

        vlm_response = VLMResponse(reasoning="Test reasoning", verdict="YES")

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=vlm_response,
                video_source="http://example.com/video.mp4",
                verification_response_code=200,
                verification_response_status="OK",
                verdict="verification-failed",  # This should be ignored
            ),
        )

        # VLM verdict (confirmed from YES) should take precedence
        assert message["info"]["verdict"] == "confirmed"
        assert message["info"]["reasoning"] == "Test reasoning"

    def test_vlm_rejected_verdict_maps_correctly(self):
        """Test that VLM NO answer maps to rejected verdict."""
        message = {
            "sensorId": "sensor1",
            "info": {}
        }

        vlm_response = VLMResponse(reasoning="No collision observed", verdict="NO")

        merge_info_with_response(
            message,
            AlertBridgeResponse(
                vlm_response=vlm_response,
                video_source="http://example.com/video.mp4",
                verification_response_code=200,
                verification_response_status="OK",
            ),
        )

        assert message["info"]["verdict"] == "rejected"
        assert message["info"]["reasoning"] == "No collision observed"

class TestVLMResponseCR1Format:
    """Tests for CR1 format with <answer> tags."""

    def test_parse_cr1_format_yes(self):
        """Test parsing CR1 format: <think>...</think><answer>YES</answer>"""
        text = "<think>Analysis complete.</think><answer>YES</answer>"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason1-7b")
        assert response.reasoning == "Analysis complete."
        assert response.verdict == "YES"

    def test_parse_cr1_format_no(self):
        """Test parsing CR1 format with NO answer."""
        text = "<think>No collision detected.</think><answer>NO</answer>"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason1-7b")
        assert response.reasoning == "No collision detected."
        assert response.verdict == "NO"

    def test_parse_cr1_format_with_whitespace(self):
        """Test CR1 with whitespace between tags."""
        text = "<think>Reasoning here.</think>\n\n<answer>YES</answer>"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason1-7b")
        assert response.verdict == "YES"

    def test_parse_cr1_format_lowercase_tags(self):
        """Test CR1 with lowercase answer tags."""
        text = "<think>Test</think><answer>yes</answer>"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason1-7b")
        assert response.verdict == "YES"

    def test_parse_cr1_format_a_b_answers(self):
        """Test CR1 format with A/B answers."""
        text = "<think>Option A is correct.</think><answer>A</answer>"
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason1-7b")
        assert response.verdict == "A"

    def test_parse_cr1_format_non_standard_verdict_raises(self):
        """Layer 1 rejects non-binary verdicts from answer tags."""
        text = "<think>Test</think><answer>MAYBE</answer>"
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason1-7b")

    def test_parse_cr1_format_empty_answer_raises(self):
        """Test that empty answer tags raise ValueError."""
        text = "<think>Test</think><answer></answer>"
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason1-7b")

    def test_parse_cr1_format_multiline_reasoning(self):
        """Test CR1 format with multiline reasoning."""
        text = """<think>First observation: vehicles approaching.
Second observation: impact detected.
Final conclusion: collision confirmed.</think><answer>YES</answer>"""
        response = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason1-7b")
        assert "First observation" in response.reasoning
        assert "Final conclusion" in response.reasoning
        assert response.verdict == "YES"

    def test_parse_cr1_direct_verdict_accepted(self):
        """Unified parser: bare verdict without think tags is accepted (B1 fallback)."""
        resp = VLMResponse.model_validate_text("YES", model_name="nvidia/cosmos-reason1-7b")
        assert resp.verdict == "YES"
        assert resp.reasoning == ""


class TestVLMResponseOtherFormat:
    """Tests for 'other' models - description only, no verdict."""

    def test_parse_other_freeform_response(self):
        """Test 'other' model returns description only, no verdict."""
        text = "The video shows a collision between two vehicles at an intersection."
        response = VLMResponse.model_validate_text(text, model_name="custom/my-vlm-model")
        assert response.verdict is None
        assert response.description == text
        assert response.reasoning == ""

    def test_parse_other_multiline_response(self):
        """Test 'other' model with multiline response."""
        text = """First observation: vehicles approaching.
Second observation: impact occurred.
Conclusion: collision confirmed."""
        response = VLMResponse.model_validate_text(text, model_name="openai/gpt-4v")
        assert response.verdict is None
        assert "collision confirmed" in response.description

    def test_parse_other_empty_raises(self):
        """Test 'other' model with empty response raises error."""
        with pytest.raises(ValueError, match="Empty response"):
            VLMResponse.model_validate_text("", model_name="custom/model")

    def test_parse_other_with_think_tags(self):
        """Test 'other' model captures think-tagged response as description."""
        text = "<think>Some reasoning here</think>\n\nYES"
        response = VLMResponse.model_validate_text(text, model_name="custom/unknown-model")
        assert response.verdict is None
        assert response.description == text

    def test_verdict_serialization_none(self):
        """Test that None verdict serializes to None."""
        response = VLMResponse(reasoning="", verdict=None, description="Test description")
        dumped = response.model_dump()
        assert dumped["verdict"] is None
        assert dumped["description"] == "Test description"


class TestModelTypeDetection:
    """Tests for model type auto-detection (auto format)."""

    def test_detect_cosmos_reason1(self):
        """Test cosmos-reason1 model auto-detects to cosmos-reason parser."""
        response = VLMResponse.model_validate_text(
            "<think>test</think><answer>YES</answer>",
            model_name="nvidia/cosmos-reason1-7b"
        )
        assert response.verdict == "YES"

    def test_detect_cosmos_reason2(self):
        """Test cosmos-reason2 model auto-detects to cosmos-reason parser."""
        response = VLMResponse.model_validate_text(
            "<think>test</think>\n\nYES",
            model_name="nvidia/cosmos-reason2-7b"
        )
        assert response.verdict == "YES"

    def test_detect_other(self):
        """Test unknown model name falls back to 'other' parser."""
        response = VLMResponse.model_validate_text(
            "This is a free-form response.",
            model_name="custom/my-model"
        )
        assert response.verdict is None
        assert response.description == "This is a free-form response."

    def test_empty_model_name_defaults_to_other(self):
        """Test empty model name defaults to 'other' parsing."""
        response = VLMResponse.model_validate_text(
            "Free form text.",
            model_name=""
        )
        assert response.verdict is None
        assert response.description == "Free form text."


class TestJSONResponseParsing:
    """Tests for JSON response format parsing (_parse_json_response)."""

    # --- Real CR2 responses captured via curl (Phase 1 fixtures) ---

    REAL_SIMPLE_JSON = '```json\n{\n  "prediction_answer": "NO",\n  "reasoning": "The presence of a green safety path does not inherently prevent a worker from walking outside it."\n}\n```'

    REAL_COOKBOOK_JSON = '```json\n{\n  "prediction_class_id": 0,\n  "prediction_label": "Walking Outside Designated Path",\n  "video_description": "A worker is seen walking outside the green safety path on a warehouse floor.",\n  "hazard_detection": {\n    "is_hazardous": true,\n    "temporal_segment": null\n  }\n}\n```'

    # --- Happy path: simple flat JSON ---

    def test_simple_json_yes(self):
        text = '```json\n{"prediction_answer": "YES", "reasoning": "Violation detected."}\n```'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.verdict == "YES"
        assert resp.reasoning == "Violation detected."

    def test_simple_json_no(self):
        text = '```json\n{"prediction_answer": "NO", "reasoning": "All clear."}\n```'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.verdict == "NO"
        assert resp.reasoning == "All clear."

    def test_real_simple_json_response(self):
        """Test with actual CR2 curl response (Phase 1 Test B)."""
        resp = VLMResponse.model_validate_text(self.REAL_SIMPLE_JSON, response_format="json")
        assert resp.verdict == "NO"
        assert "green safety path" in resp.reasoning

    def test_json_a_b_verdicts(self):
        text = '{"prediction_answer": "A", "reasoning": "Option A selected."}'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.verdict == "A"

        text = '{"prediction_answer": "B", "reasoning": "Option B selected."}'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.verdict == "B"

    def test_json_case_insensitive_verdict(self):
        text = '{"prediction_answer": "yes", "reasoning": "test"}'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.verdict == "YES"

        text = '{"prediction_answer": "no", "reasoning": "test"}'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.verdict == "NO"

    # --- Code fence handling ---

    def test_json_without_code_fences(self):
        text = '{"prediction_answer": "YES", "reasoning": "Direct JSON."}'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.verdict == "YES"

    def test_json_with_code_fences(self):
        text = '```json\n{"prediction_answer": "YES", "reasoning": "Fenced."}\n```'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.verdict == "YES"

    def test_json_with_bare_backtick_fences(self):
        text = '```\n{"prediction_answer": "NO", "reasoning": "Bare fences."}\n```'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.verdict == "NO"

    # --- Cookbook-style nested JSON with custom config ---

    def test_cookbook_format_with_custom_config(self):
        """Test cookbook nested JSON using verdict_mapping + dot-notation."""
        config = {
            "verdict_field": "hazard_detection.is_hazardous",
            "verdict_mapping": {"true": "YES", "false": "NO"},
            "reasoning_fields": ["video_description"],
        }
        resp = VLMResponse.model_validate_text(
            self.REAL_COOKBOOK_JSON, response_format="json", json_config=config
        )
        assert resp.verdict == "YES"
        assert "walking outside" in resp.reasoning.lower()

    def test_cookbook_format_not_hazardous(self):
        text = '{"hazard_detection": {"is_hazardous": false}, "video_description": "Safe."}'
        config = {
            "verdict_field": "hazard_detection.is_hazardous",
            "verdict_mapping": {"false": "NO"},
            "reasoning_fields": ["video_description"],
        }
        resp = VLMResponse.model_validate_text(text, response_format="json", json_config=config)
        assert resp.verdict == "NO"
        assert resp.reasoning == "Safe."

    # --- Custom field names ---

    def test_custom_verdict_field(self):
        text = '{"answer": "YES", "rationale": "Custom field."}'
        config = {"verdict_field": "answer", "reasoning_fields": ["rationale"]}
        resp = VLMResponse.model_validate_text(text, response_format="json", json_config=config)
        assert resp.verdict == "YES"
        assert resp.reasoning == "Custom field."

    def test_reasoning_field_fallback(self):
        """First matching reasoning field wins."""
        text = '{"prediction_answer": "YES", "thinking": "From thinking field."}'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.reasoning == "From thinking field."

    def test_no_reasoning_field_returns_empty(self):
        text = '{"prediction_answer": "YES"}'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.verdict == "YES"
        assert resp.reasoning == ""

    # --- Error paths ---

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            VLMResponse.model_validate_text("not json", response_format="json")

    def test_not_object_raises(self):
        with pytest.raises(ValueError, match="Expected JSON object"):
            VLMResponse.model_validate_text("[1, 2, 3]", response_format="json")

    def test_missing_verdict_field_raises(self):
        text = '{"reasoning": "No verdict here."}'
        with pytest.raises(ValueError, match="missing required field"):
            VLMResponse.model_validate_text(text, response_format="json")

    def test_empty_verdict_field_raises(self):
        text = '{"prediction_answer": "", "reasoning": "Empty verdict."}'
        with pytest.raises(ValueError, match="is empty"):
            VLMResponse.model_validate_text(text, response_format="json")

    # --- Verdict serialization (JSON → confirmed/rejected) ---

    def test_json_yes_serializes_to_confirmed(self):
        text = '{"prediction_answer": "YES", "reasoning": "test"}'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.model_dump()["verdict"] == "confirmed"

    def test_json_no_serializes_to_rejected(self):
        text = '{"prediction_answer": "NO", "reasoning": "test"}'
        resp = VLMResponse.model_validate_text(text, response_format="json")
        assert resp.model_dump()["verdict"] == "rejected"

    # --- Backward compatibility ---

    def test_auto_format_cr1_unchanged(self):
        """response_format='auto' with CR1 model still works exactly as before."""
        text = "<think>Analysis complete.</think><answer>YES</answer>"
        resp = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason1-7b")
        assert resp.verdict == "YES"

    def test_auto_format_cr2_unchanged(self):
        """response_format='auto' with CR2 model still works exactly as before."""
        text = "<think>No collision.</think>\n\nNO"
        resp = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert resp.verdict == "NO"

    def test_auto_format_other_unchanged(self):
        """response_format='auto' with unknown model still returns description."""
        text = "Free form text."
        resp = VLMResponse.model_validate_text(text, model_name="custom/model")
        assert resp.verdict is None
        assert resp.description == "Free form text."

    def test_explicit_cr1_format(self):
        """response_format='cr1' bypasses model name detection."""
        text = "<think>Reasoning.</think><answer>YES</answer>"
        resp = VLMResponse.model_validate_text(text, response_format="cr1")
        assert resp.verdict == "YES"

    def test_explicit_cr2_format(self):
        """response_format='cr2' bypasses model name detection."""
        text = "<think>Reasoning.</think>\n\nNO"
        resp = VLMResponse.model_validate_text(text, response_format="cr2")
        assert resp.verdict == "NO"


class TestVLMParsingErrorContext:
    """Tests that VLM parsing errors include raw response and diagnostic context."""

    def test_cr2_non_standard_verdict_raises(self):
        """Layer 1 rejects non-binary verdicts strictly via VerdictType Literal."""
        text = "<think>Analysis complete.</think>\n\nMAYBE"
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")

    def test_cr2_long_response_non_standard_verdict_raises(self):
        """Long reasoning with non-standard verdict still raises at Layer 1."""
        long_reasoning = "A" * 5000
        text = f"<think>{long_reasoning}</think>\n\nINVALID_VERDICT"
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")

    def test_cr2_empty_verdict_includes_raw_response(self):
        """Empty verdict (after think tags) should report raw response."""
        text = "<think>Reasoning here.</think>\n\n"
        with pytest.raises(ValueError) as exc_info:
            VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert "raw response" in str(exc_info.value).lower() or "empty" in str(exc_info.value).lower()

    def test_cr2_no_think_tags_non_binary_raises(self):
        """Without think tags, bare non-binary text is rejected by Layer 1."""
        text = "This is a free-form response that isn't YES/NO/A/B"
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")

    def test_cosmos_reason_freeform_text_raises(self):
        """Cosmos Reason model with free-form non-binary text is rejected."""
        raw = "The video shows a collision between two vehicles at an intersection."
        with pytest.raises(ValueError):
            VLMResponse.model_validate_text(raw, model_name="nvidia/cosmos-reason1-7b")

    def test_other_model_empty_raises_with_context(self):
        """Empty response for 'other' model should raise with clear message."""
        with pytest.raises(ValueError, match="Empty response"):
            VLMResponse.model_validate_text("", model_name="custom/model")


class TestCosmosReasonUnified:
    """Tests for unified Cosmos Reason parser — accepts both answer-tagged and bare verdict."""

    def test_answer_tags_with_cr2_model(self):
        """CR2 model now accepts <answer> tags (previously rejected)."""
        text = "<think>Analysis complete.</think><answer>YES</answer>"
        resp = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason2-7b")
        assert resp.verdict == "YES"
        assert resp.reasoning == "Analysis complete."

    def test_bare_verdict_with_cr1_model(self):
        """CR1 model now accepts bare verdict after </think> (previously rejected)."""
        text = "<think>Analysis complete.</think>\n\nYES"
        resp = VLMResponse.model_validate_text(text, model_name="nvidia/cosmos-reason1-7b")
        assert resp.verdict == "YES"
        assert resp.reasoning == "Analysis complete."

    def test_bare_b1_with_cr1_model(self):
        """CR1 model now accepts bare verdict without <think> tags."""
        resp = VLMResponse.model_validate_text("NO", model_name="nvidia/cosmos-reason1-7b")
        assert resp.verdict == "NO"
        assert resp.reasoning == ""

    def test_explicit_cosmos_reason_format_answer_tags(self):
        """Explicit 'cosmos-reason' format handles answer-tagged output."""
        text = "<think>Reasoning.</think><answer>YES</answer>"
        resp = VLMResponse.model_validate_text(text, response_format="cosmos-reason")
        assert resp.verdict == "YES"

    def test_explicit_cosmos_reason_format_bare_verdict(self):
        """Explicit 'cosmos-reason' format handles bare verdict output."""
        text = "<think>Reasoning.</think>\n\nNO"
        resp = VLMResponse.model_validate_text(text, response_format="cosmos-reason")
        assert resp.verdict == "NO"

    def test_cr1_alias_uses_unified_parser(self):
        """response_format='cr1' is an alias for cosmos-reason; accepts bare verdict too."""
        text = "<think>Reasoning.</think>\n\nYES"
        resp = VLMResponse.model_validate_text(text, response_format="cr1")
        assert resp.verdict == "YES"

    def test_cr2_alias_uses_unified_parser(self):
        """response_format='cr2' is an alias for cosmos-reason; accepts answer tags too."""
        text = "<think>Reasoning.</think><answer>NO</answer>"
        resp = VLMResponse.model_validate_text(text, response_format="cr2")
        assert resp.verdict == "NO"

    def test_answer_tags_preferred_over_bare(self):
        """When both <answer> tags and trailing text exist, answer tags win."""
        text = "<think>Reasoning.</think><answer>YES</answer>\n\nExtra text"
        resp = VLMResponse.model_validate_text(text, response_format="cosmos-reason")
        assert resp.verdict == "YES"

    def test_explicit_other_format(self):
        """response_format='other' forces free-form parsing."""
        text = "<think>Some reasoning here</think>\n\nYES"
        resp = VLMResponse.model_validate_text(text, response_format="other")
        assert resp.verdict is None
        assert resp.description == text

    def test_unknown_format_raises(self):
        """Unknown response_format with no registered parser raises ValueError."""
        with pytest.raises(ValueError, match="Unknown response_format"):
            VLMResponse.model_validate_text("YES", response_format="nonexistent")


class TestCustomParserRegistry:
    """Tests for the custom parser registry (register_parser)."""

    def setup_method(self):
        _custom_parsers.clear()

    def teardown_method(self):
        _custom_parsers.clear()

    def test_register_and_use_custom_parser(self):
        def my_parser(text, json_config=None):
            parts = text.strip().split("|")
            return VLMResponse.model_validate({
                "reasoning": parts[1].strip() if len(parts) > 1 else "",
                "verdict": parts[0].strip().upper(),
            })

        register_parser("pipe", my_parser)
        resp = VLMResponse.model_validate_text("YES|Some reasoning", response_format="pipe")
        assert resp.verdict == "YES"
        assert resp.reasoning == "Some reasoning"

    def test_custom_parser_receives_json_config(self):
        received = {}

        def capturing_parser(text, json_config=None):
            received["json_config"] = json_config
            return VLMResponse.model_validate({"reasoning": "", "verdict": "YES"})

        register_parser("capture", capturing_parser)
        VLMResponse.model_validate_text(
            "test", response_format="capture", json_config={"key": "val"}
        )
        assert received["json_config"] == {"key": "val"}

    def test_cannot_override_builtin_auto(self):
        with pytest.raises(ValueError, match="Cannot override built-in"):
            register_parser("auto", lambda t, c: None)

    def test_cannot_override_builtin_json(self):
        with pytest.raises(ValueError, match="Cannot override built-in"):
            register_parser("json", lambda t, c: None)

    def test_cannot_override_builtin_cosmos_reason(self):
        with pytest.raises(ValueError, match="Cannot override built-in"):
            register_parser("cosmos-reason", lambda t, c: None)

    def test_cannot_override_builtin_cr1(self):
        with pytest.raises(ValueError, match="Cannot override built-in"):
            register_parser("cr1", lambda t, c: None)

    def test_cannot_override_builtin_cr2(self):
        with pytest.raises(ValueError, match="Cannot override built-in"):
            register_parser("cr2", lambda t, c: None)

    def test_cannot_override_builtin_other(self):
        with pytest.raises(ValueError, match="Cannot override built-in"):
            register_parser("other", lambda t, c: None)

    def test_custom_parser_takes_priority_over_auto(self):
        def xml_parser(text, json_config=None):
            return VLMResponse.model_validate({"reasoning": "from xml", "verdict": "NO"})

        register_parser("xml", xml_parser)
        resp = VLMResponse.model_validate_text(
            "<data/>", response_format="xml", model_name="nvidia/cosmos-reason2-8b"
        )
        assert resp.verdict == "NO"
        assert resp.reasoning == "from xml"


class TestXmlVerdictSampleParser:
    """Tests for the sample custom parser: custom_parsers.xml_verdict_parser."""

    def setup_method(self):
        _custom_parsers.clear()
        from custom_parsers.xml_verdict_parser import parse_xml_verdict
        register_parser("xml-verdict", parse_xml_verdict)

    def teardown_method(self):
        _custom_parsers.clear()

    def test_verdict_first_yes(self):
        text = (
            "<result>\n"
            "  <verdict>YES</verdict>\n"
            "  <reasoning>Worker is not wearing PPE.</reasoning>\n"
            "</result>"
        )
        resp = VLMResponse.model_validate_text(text, response_format="xml-verdict")
        assert resp.verdict == "YES"
        assert resp.reasoning == "Worker is not wearing PPE."

    def test_verdict_first_no(self):
        text = (
            "<result>\n"
            "  <verdict>NO</verdict>\n"
            "  <reasoning>All workers are wearing helmets.</reasoning>\n"
            "</result>"
        )
        resp = VLMResponse.model_validate_text(text, response_format="xml-verdict")
        assert resp.verdict == "NO"
        assert resp.reasoning == "All workers are wearing helmets."

    def test_reasoning_first_ordering(self):
        text = (
            "<result>\n"
            "  <reasoning>Forklift approaching pedestrian zone.</reasoning>\n"
            "  <verdict>yes</verdict>\n"
            "</result>"
        )
        resp = VLMResponse.model_validate_text(text, response_format="xml-verdict")
        assert resp.verdict == "YES"
        assert resp.reasoning == "Forklift approaching pedestrian zone."

    def test_case_insensitive_verdict(self):
        text = "<result><verdict>Yes</verdict><reasoning>Test.</reasoning></result>"
        resp = VLMResponse.model_validate_text(text, response_format="xml-verdict")
        assert resp.verdict == "YES"

    def test_multiline_reasoning(self):
        text = (
            "<result>\n"
            "  <verdict>NO</verdict>\n"
            "  <reasoning>\n"
            "    The video shows a normal scene.\n"
            "    No hazards detected in any frame.\n"
            "  </reasoning>\n"
            "</result>"
        )
        resp = VLMResponse.model_validate_text(text, response_format="xml-verdict")
        assert resp.verdict == "NO"
        assert "normal scene" in resp.reasoning
        assert "No hazards" in resp.reasoning

    def test_extra_whitespace(self):
        text = "<result>  <verdict>  YES  </verdict>  <reasoning>  Hazard found.  </reasoning>  </result>"
        resp = VLMResponse.model_validate_text(text, response_format="xml-verdict")
        assert resp.verdict == "YES"
        assert resp.reasoning == "Hazard found."

    def test_invalid_xml_raises(self):
        with pytest.raises(ValueError, match="XML verdict parser"):
            VLMResponse.model_validate_text("just plain text", response_format="xml-verdict")

    def test_missing_verdict_tag_raises(self):
        text = "<result><reasoning>No verdict here.</reasoning></result>"
        with pytest.raises(ValueError, match="XML verdict parser"):
            VLMResponse.model_validate_text(text, response_format="xml-verdict")

    def test_verdict_serialization(self):
        text = "<result><verdict>YES</verdict><reasoning>Test</reasoning></result>"
        resp = VLMResponse.model_validate_text(text, response_format="xml-verdict")
        dumped = resp.model_dump()
        assert dumped["verdict"] == "confirmed"


class TestVLMResponseExtraField:
    """Tests for the VLMResponse.extra pass-through field."""

    def test_extra_none_by_default(self):
        resp = VLMResponse(reasoning="test", verdict="YES")
        assert resp.extra is None

    def test_extra_stored_on_model(self):
        resp = VLMResponse(
            reasoning="test", verdict=None,
            extra={"vehicle_counts": {"cars": 20}},
        )
        assert resp.extra == {"vehicle_counts": {"cars": 20}}

    def test_extra_excluded_from_flat_dump_when_none(self):
        resp = VLMResponse(reasoning="test", verdict="YES")
        bridge = AlertBridgeResponse(
            vlm_response=resp, video_source="/video.mp4",
            verification_response_code=200, verification_response_status="OK",
        )
        flat = bridge.model_dump_flat()
        assert "extra" not in flat
        assert flat["verdict"] == "confirmed"
        assert flat["videoSource"] == "/video.mp4"

    def test_extra_merged_into_flat_dump(self):
        resp = VLMResponse(
            reasoning="Vehicle counts based on detected objects.",
            verdict=None,
            extra={"vehicle_counts": {"cars": 20, "trucks": 40, "buses": 5}},
        )
        bridge = AlertBridgeResponse(
            vlm_response=resp, video_source="/video.mp4",
            verification_response_code=200, verification_response_status="OK",
        )
        flat = bridge.model_dump_flat()
        assert flat["vehicle_counts"] == {"cars": 20, "trucks": 40, "buses": 5}
        assert flat["reasoning"] == "Vehicle counts based on detected objects."
        assert flat["verdict"] is None
        assert flat["videoSource"] == "/video.mp4"
        assert "extra" not in flat

    def test_extra_merged_into_message_info(self):
        resp = VLMResponse(
            reasoning="Counting complete.", verdict=None,
            extra={"vehicle_counts": {"cars": 10}, "scene_type": "highway"},
        )
        bridge = AlertBridgeResponse(
            vlm_response=resp, video_source="/video.mp4",
            verification_response_code=200, verification_response_status="OK",
        )
        message = {"id": "msg-1", "info": {"sensorId": "cam-1"}}
        merge_info_with_response(message, bridge)
        info = message["info"]
        assert info["sensorId"] == "cam-1"
        # Post-nvschema alignment: info is map<string,string>; nested objects are stringified
        assert json.loads(info["vehicle_counts"]) == {"cars": 10}
        assert info["scene_type"] == "highway"
        assert info["reasoning"] == "Counting complete."
        assert "extra" not in info

    def test_extra_cannot_overwrite_existing_keys(self):
        resp = VLMResponse(
            reasoning="real analysis", verdict="YES",
            extra={
                "videoSource": "fake://evil",
                "verificationResponseCode": 999,
                "verificationResponseStatus": "spoofed",
                "verdict": "tampered",
                "reasoning": "fake",
                "description": "overwritten",
                "legitimate_field": "kept",
            },
        )
        bridge = AlertBridgeResponse(
            vlm_response=resp, video_source="/real/video.mp4",
            verification_response_code=200, verification_response_status="OK",
        )
        flat = bridge.model_dump_flat()
        assert flat["videoSource"] == "/real/video.mp4"
        assert flat["verificationResponseCode"] == 200
        assert flat["verificationResponseStatus"] == "OK"
        assert flat["verdict"] == "confirmed"  # serialized from "YES"
        assert flat["reasoning"] == "real analysis"
        assert flat["legitimate_field"] == "kept"

    def test_extra_does_not_affect_builtin_parsers(self):
        text = "<think>Reasoning here.</think><answer>YES</answer>"
        resp = VLMResponse.model_validate_text(text, response_format="cosmos-reason")
        assert resp.extra is None
        assert resp.verdict == "YES"


class TestVehicleCountSampleParser:
    """Tests for the sample custom parser: custom_parsers.vehicle_count_parser."""

    def setup_method(self):
        _custom_parsers.clear()
        from custom_parsers.vehicle_count_parser import parse_vehicle_count
        register_parser("vehicle-count", parse_vehicle_count)

    def teardown_method(self):
        _custom_parsers.clear()

    SAMPLE_RESPONSE = (
        '{"result": {"vehicle_counts": {"cars": 20, "trucks": 40, "buses": 5, '
        '"motorcycles": 12}, "reasoning": "Vehicle counts are based on objects '
        'detected in the road scene."}}'
    )

    def test_parse_basic(self):
        resp = VLMResponse.model_validate_text(self.SAMPLE_RESPONSE, response_format="vehicle-count")
        assert resp.reasoning == "Vehicle counts are based on objects detected in the road scene."
        assert resp.verdict is None
        assert resp.extra == {
            "vehicle_counts": {"cars": 20, "trucks": 40, "buses": 5, "motorcycles": 12}
        }

    def test_extra_flows_to_flat_dump(self):
        resp = VLMResponse.model_validate_text(self.SAMPLE_RESPONSE, response_format="vehicle-count")
        bridge = AlertBridgeResponse(
            vlm_response=resp, video_source="/traffic.mp4",
            verification_response_code=200, verification_response_status="OK",
        )
        flat = bridge.model_dump_flat()
        assert flat["vehicle_counts"] == {"cars": 20, "trucks": 40, "buses": 5, "motorcycles": 12}
        assert flat["videoSource"] == "/traffic.mp4"
        assert "extra" not in flat

    def test_extra_flows_to_message_info(self):
        resp = VLMResponse.model_validate_text(self.SAMPLE_RESPONSE, response_format="vehicle-count")
        bridge = AlertBridgeResponse(
            vlm_response=resp, video_source="/traffic.mp4",
            verification_response_code=200, verification_response_status="OK",
        )
        message = {"id": "msg-1", "info": {"sensorId": "cam-1"}}
        merge_info_with_response(message, bridge)
        info = message["info"]
        assert info["sensorId"] == "cam-1"
        # Post-nvschema alignment: info is map<string,string>; nested objects are stringified
        vc = json.loads(info["vehicle_counts"])
        assert vc["cars"] == 20
        assert vc["motorcycles"] == 12
        assert "extra" not in info

    def test_markdown_code_fences_stripped(self):
        text = '```json\n{"result": {"vehicle_counts": {"cars": 5}, "reasoning": "Test"}}\n```'
        resp = VLMResponse.model_validate_text(text, response_format="vehicle-count")
        assert resp.extra == {"vehicle_counts": {"cars": 5}}

    def test_flat_json_without_result_wrapper(self):
        text = '{"vehicle_counts": {"cars": 3}, "reasoning": "Direct format."}'
        resp = VLMResponse.model_validate_text(text, response_format="vehicle-count")
        assert resp.extra == {"vehicle_counts": {"cars": 3}}
        assert resp.reasoning == "Direct format."

    def test_missing_vehicle_counts_raises(self):
        text = '{"result": {"reasoning": "No counts here."}}'
        with pytest.raises(ValueError, match="missing 'vehicle_counts'"):
            VLMResponse.model_validate_text(text, response_format="vehicle-count")

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            VLMResponse.model_validate_text("not json", response_format="vehicle-count")

    def test_empty_reasoning_defaults(self):
        text = '{"result": {"vehicle_counts": {"cars": 1}}}'
        resp = VLMResponse.model_validate_text(text, response_format="vehicle-count")
        assert resp.reasoning == ""