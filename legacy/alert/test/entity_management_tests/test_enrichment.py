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

"""Unit tests for enrichment prompt support feature."""

import asyncio
import json
import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from typing import Dict, Any

from models.responses import EnrichmentResponse
from handlers.enrichment import EnrichmentProcessor


class TestEnrichmentResponse:
    """Tests for EnrichmentResponse model."""

    def test_create_success_response(self):
        """Test creating a successful enrichment response."""
        response = EnrichmentResponse(
            reasoning="Detailed description of the event...",
            response_code=200,
            response_status="OK",
        )
        assert response.reasoning == "Detailed description of the event..."
        assert response.response_code == 200
        assert response.response_status == "OK"

    def test_create_error_response(self):
        """Test creating an error enrichment response."""
        response = EnrichmentResponse(
            reasoning=None,
            response_code=504,
            response_status="VLM service timeout",
        )
        assert response.reasoning is None
        assert response.response_code == 504
        assert response.response_status == "VLM service timeout"

    def test_model_dump(self):
        """Test model_dump returns correct dictionary structure."""
        response = EnrichmentResponse(
            reasoning="Test reasoning",
            response_code=200,
            response_status="OK",
        )
        dumped = response.model_dump()
        
        assert isinstance(dumped, dict)
        assert dumped["reasoning"] == "Test reasoning"
        assert dumped["responseCode"] == 200
        assert dumped["responseStatus"] == "OK"
        assert len(dumped) == 3

    def test_model_dump_with_none_reasoning(self):
        """Test model_dump handles None reasoning correctly."""
        response = EnrichmentResponse(
            reasoning=None,
            response_code=503,
            response_status="Failed to connect",
        )
        dumped = response.model_dump()
        
        assert dumped["reasoning"] is None
        assert dumped["responseCode"] == 503


class TestEnrichmentProcessor:
    """Tests for EnrichmentProcessor class."""

    def create_mock_vlm_client(self, response_content: str = "VLM response"):
        """Create a mock VLM client."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.content = response_content
        mock_client.analyze_video_url.return_value = mock_response
        return mock_client

    def create_mock_prompt_manager(self, enrichment_prompt: str = None):
        """Create a mock prompt manager."""
        mock_manager = Mock()
        mock_manager.get_enrichment_prompt_for_message.return_value = enrichment_prompt
        return mock_manager

    def create_test_message(self) -> Dict[str, Any]:
        """Create a test message."""
        return {
            "id": "test-123",
            "sensorId": "sensor-001",
            "category": "collision",
            "timestamp": "2025-01-01T00:00:00Z",
            "info": {
                "primaryObjectId": "vehicle-1",
            },
        }

    # --- Feature Flag Tests ---

    def test_disabled_returns_none(self):
        """Test that disabled processor returns None."""
        processor = EnrichmentProcessor(
            vlm_client=self.create_mock_vlm_client(),
            prompt_manager=self.create_mock_prompt_manager("Test prompt"),
            enabled=False,
        )
        
        result = processor.process(
            message=self.create_test_message(),
            video_url="http://example.com/video.mp4",
            system_prompt="System prompt",
            sensor_id="sensor-001",
        )
        
        assert result is None

    def test_enabled_with_prompt_processes(self):
        """Test that enabled processor with prompt processes successfully."""
        processor = EnrichmentProcessor(
            vlm_client=self.create_mock_vlm_client("Enrichment result"),
            prompt_manager=self.create_mock_prompt_manager("Describe the event"),
            enabled=True,
        )
        
        result = processor.process(
            message=self.create_test_message(),
            video_url="http://example.com/video.mp4",
            system_prompt="System prompt",
            sensor_id="sensor-001",
        )
        
        assert result is not None
        assert isinstance(result, EnrichmentResponse)
        assert result.reasoning == "Enrichment result"
        assert result.response_code == 200
        assert result.response_status == "OK"

    # --- No Prompt Defined Tests ---

    def test_no_enrichment_prompt_returns_none(self):
        """Test that missing enrichment prompt returns None."""
        processor = EnrichmentProcessor(
            vlm_client=self.create_mock_vlm_client(),
            prompt_manager=self.create_mock_prompt_manager(None),  # No prompt
            enabled=True,
        )
        
        result = processor.process(
            message=self.create_test_message(),
            video_url="http://example.com/video.mp4",
            system_prompt="System prompt",
            sensor_id="sensor-001",
        )
        
        assert result is None

    def test_empty_enrichment_prompt_returns_none(self):
        """Test that empty enrichment prompt returns None."""
        processor = EnrichmentProcessor(
            vlm_client=self.create_mock_vlm_client(),
            prompt_manager=self.create_mock_prompt_manager(""),  # Empty prompt
            enabled=True,
        )
        
        # Empty string is falsy, should return None
        result = processor.process(
            message=self.create_test_message(),
            video_url="http://example.com/video.mp4",
            system_prompt="System prompt",
            sensor_id="sensor-001",
        )
        
        assert result is None

    # --- VLM Call Tests ---

    def test_vlm_client_called_with_correct_params(self):
        """Test that VLM client is called with correct parameters."""
        mock_vlm = self.create_mock_vlm_client("Response")
        processor = EnrichmentProcessor(
            vlm_client=mock_vlm,
            prompt_manager=self.create_mock_prompt_manager("Enrichment prompt"),
            enabled=True,
        )
        
        processor.process(
            message=self.create_test_message(),
            video_url="http://example.com/video.mp4",
            system_prompt="System prompt",
            sensor_id="sensor-001",
        )
        
        mock_vlm.analyze_video_url.assert_called_once_with(
            "http://example.com/video.mp4",
            "Enrichment prompt",
            "System prompt",
        )

    # --- Error Handling Tests ---

    def test_api_connection_error(self):
        """Test handling of APIConnectionError."""
        from openai import APIConnectionError
        
        mock_vlm = Mock()
        mock_vlm.analyze_video_url.side_effect = APIConnectionError(request=Mock())
        
        processor = EnrichmentProcessor(
            vlm_client=mock_vlm,
            prompt_manager=self.create_mock_prompt_manager("Prompt"),
            enabled=True,
        )
        
        result = processor.process(
            message=self.create_test_message(),
            video_url="http://example.com/video.mp4",
            system_prompt="System prompt",
            sensor_id="sensor-001",
        )
        
        assert result is not None
        assert result.reasoning is None
        assert result.response_code == 503
        assert result.response_status == "Failed to connect to VLM service"

    def test_api_timeout_error(self):
        """Test handling of APITimeoutError."""
        from openai import APITimeoutError
        
        mock_vlm = Mock()
        mock_vlm.analyze_video_url.side_effect = APITimeoutError(request=Mock())
        
        processor = EnrichmentProcessor(
            vlm_client=mock_vlm,
            prompt_manager=self.create_mock_prompt_manager("Prompt"),
            enabled=True,
        )
        
        result = processor.process(
            message=self.create_test_message(),
            video_url="http://example.com/video.mp4",
            system_prompt="System prompt",
            sensor_id="sensor-001",
        )
        
        assert result is not None
        assert result.reasoning is None
        assert result.response_code == 504
        assert result.response_status == "VLM service timeout"

    def test_internal_server_error(self):
        """Test handling of InternalServerError."""
        from openai import InternalServerError
        
        mock_vlm = Mock()
        mock_vlm.analyze_video_url.side_effect = InternalServerError(
            message="Internal error",
            response=Mock(),
            body=None,
        )
        
        processor = EnrichmentProcessor(
            vlm_client=mock_vlm,
            prompt_manager=self.create_mock_prompt_manager("Prompt"),
            enabled=True,
        )
        
        result = processor.process(
            message=self.create_test_message(),
            video_url="http://example.com/video.mp4",
            system_prompt="System prompt",
            sensor_id="sensor-001",
        )
        
        assert result is not None
        assert result.reasoning is None
        assert result.response_code == 500
        assert result.response_status == "VLM service internal error"

    def test_generic_exception(self):
        """Test handling of generic exceptions."""
        mock_vlm = Mock()
        mock_vlm.analyze_video_url.side_effect = Exception("Unexpected error")
        
        processor = EnrichmentProcessor(
            vlm_client=mock_vlm,
            prompt_manager=self.create_mock_prompt_manager("Prompt"),
            enabled=True,
        )
        
        result = processor.process(
            message=self.create_test_message(),
            video_url="http://example.com/video.mp4",
            system_prompt="System prompt",
            sensor_id="sensor-001",
        )
        
        assert result is not None
        assert result.reasoning is None
        assert result.response_code == 500
        assert "Enrichment error" in result.response_status

    # --- Merge Into Message Tests ---

    def test_merge_into_message_creates_info(self):
        """Test merge_into_message creates info dict if missing."""
        processor = EnrichmentProcessor(
            vlm_client=self.create_mock_vlm_client(),
            prompt_manager=self.create_mock_prompt_manager(),
            enabled=True,
        )
        
        message = {"id": "test"}  # No info field
        response = EnrichmentResponse(
            reasoning="Test",
            response_code=200,
            response_status="OK",
        )
        
        processor.merge_into_message(message, response)
        
        assert "info" in message
        assert "enrichment" in message["info"]
        enrichment = json.loads(message["info"]["enrichment"])
        assert enrichment["reasoning"] == "Test"

    def test_merge_into_message_preserves_existing_info(self):
        """Test merge_into_message preserves existing info fields."""
        processor = EnrichmentProcessor(
            vlm_client=self.create_mock_vlm_client(),
            prompt_manager=self.create_mock_prompt_manager(),
            enabled=True,
        )
        
        message = {
            "id": "test",
            "info": {
                "primaryObjectId": "obj-1",
                "reasoning": "Verification reasoning",
                "verdict": "confirmed",
            },
        }
        response = EnrichmentResponse(
            reasoning="Enrichment text",
            response_code=200,
            response_status="OK",
        )
        
        processor.merge_into_message(message, response)
        
        # Original fields preserved
        assert message["info"]["primaryObjectId"] == "obj-1"
        assert message["info"]["reasoning"] == "Verification reasoning"
        assert message["info"]["verdict"] == "confirmed"
        # Enrichment added as JSON string
        enrichment = json.loads(message["info"]["enrichment"])
        assert enrichment["reasoning"] == "Enrichment text"
        assert enrichment["responseCode"] == 200

    def test_merge_into_message_structure(self):
        """Test merge_into_message creates correct nested structure."""
        processor = EnrichmentProcessor(
            vlm_client=self.create_mock_vlm_client(),
            prompt_manager=self.create_mock_prompt_manager(),
            enabled=True,
        )
        
        message = {"id": "test", "info": {}}
        response = EnrichmentResponse(
            reasoning="Detailed analysis",
            response_code=200,
            response_status="OK",
        )
        
        processor.merge_into_message(message, response)
        
        raw = message["info"]["enrichment"]
        assert isinstance(raw, str)
        enrichment = json.loads(raw)
        assert "reasoning" in enrichment
        assert "responseCode" in enrichment
        assert "responseStatus" in enrichment
        assert len(enrichment) == 3

    def test_process_async_uses_async_client_when_available(self):
        """Async path should use async_vlm_client when provided."""
        mock_vlm = self.create_mock_vlm_client("sync-fallback-should-not-run")
        mock_async_vlm = Mock()
        mock_async_resp = Mock()
        mock_async_resp.content = "async enrichment result"
        mock_async_vlm.analyze_video_url = AsyncMock(return_value=mock_async_resp)

        processor = EnrichmentProcessor(
            vlm_client=mock_vlm,
            prompt_manager=self.create_mock_prompt_manager("Enrichment prompt"),
            async_vlm_client=mock_async_vlm,
            enabled=True,
        )

        result = asyncio.run(
            processor.process_async(
                message=self.create_test_message(),
                video_url="http://example.com/video.mp4",
                system_prompt="System prompt",
                sensor_id="sensor-001",
            )
        )

        assert result is not None
        assert result.reasoning == "async enrichment result"
        assert result.response_code == 200
        assert result.response_status == "OK"
        mock_async_vlm.analyze_video_url.assert_awaited_once_with(
            "http://example.com/video.mp4",
            "Enrichment prompt",
            "System prompt",
        )
        mock_vlm.analyze_video_url.assert_not_called()

    def test_process_async_without_async_client_returns_unavailable(self):
        """Async path should not call sync VLM client when async client is missing."""
        mock_vlm = self.create_mock_vlm_client("sync enrichment result")
        processor = EnrichmentProcessor(
            vlm_client=mock_vlm,
            prompt_manager=self.create_mock_prompt_manager("Enrichment prompt"),
            async_vlm_client=None,
            enabled=True,
        )

        result = asyncio.run(
            processor.process_async(
                message=self.create_test_message(),
                video_url="http://example.com/video.mp4",
                system_prompt="System prompt",
                sensor_id="sensor-001",
            )
        )

        assert result is not None
        assert result.reasoning is None
        assert result.response_code == 503
        assert result.response_status == "Async VLM client unavailable"
        mock_vlm.analyze_video_url.assert_not_called()

    def test_process_async_timeout_mapping(self):
        """Async path should preserve timeout mapping semantics."""
        from openai import APITimeoutError

        mock_vlm = self.create_mock_vlm_client("unused")
        mock_async_vlm = Mock()
        mock_async_vlm.analyze_video_url = AsyncMock(side_effect=APITimeoutError(request=Mock()))

        processor = EnrichmentProcessor(
            vlm_client=mock_vlm,
            prompt_manager=self.create_mock_prompt_manager("Enrichment prompt"),
            async_vlm_client=mock_async_vlm,
            enabled=True,
        )

        result = asyncio.run(
            processor.process_async(
                message=self.create_test_message(),
                video_url="http://example.com/video.mp4",
                system_prompt="System prompt",
                sensor_id="sensor-001",
            )
        )

        assert result is not None
        assert result.reasoning is None
        assert result.response_code == 504
        assert result.response_status == "VLM service timeout"
        mock_async_vlm.analyze_video_url.assert_awaited_once()


class TestPromptsModel:
    """Tests for Prompts model with enrichment field."""

    def test_prompts_with_enrichment(self):
        """Test Prompts model accepts enrichment field."""
        from handlers.prompt_handler.alert_type_config_loader import Prompts
        
        prompts = Prompts(
            user="User prompt",
            system="System prompt",
            enrichment="Enrichment prompt",
        )
        
        assert prompts.user == "User prompt"
        assert prompts.system == "System prompt"
        assert prompts.enrichment == "Enrichment prompt"

    def test_prompts_without_enrichment(self):
        """Test Prompts model works without enrichment field."""
        from handlers.prompt_handler.alert_type_config_loader import Prompts
        
        prompts = Prompts(
            user="User prompt",
            system="System prompt",
        )
        
        assert prompts.user == "User prompt"
        assert prompts.system == "System prompt"
        assert prompts.enrichment is None

    def test_prompts_enrichment_optional(self):
        """Test enrichment field is optional."""
        from handlers.prompt_handler.alert_type_config_loader import Prompts
        
        prompts = Prompts(user="Required user prompt")
        
        assert prompts.enrichment is None


class TestPromptManagerEnrichment:
    """Tests for PromptManager enrichment methods.

    Now backed by ``AlertConfigStore`` instead of the legacy
    ``DynamicPromptHandler.get_all_prompts`` bucket structure.
    """

    def test_get_enrichment_prompt_for_message_with_prompt(self):
        """get_enrichment_prompt_for_message returns the enrichment_prompt
        from alert_config:{alert_type} when present."""
        mock_store = MagicMock()
        mock_store.get.return_value = {
            'enrichment_prompt': 'Describe the collision in detail.'
        }

        from handlers.prompt_handler.prompt_manager import PromptManager
        manager = PromptManager.__new__(PromptManager)
        manager.logger = Mock()
        manager.alert_config_store = mock_store

        message = {'category': 'collision'}
        result = manager.get_enrichment_prompt_for_message(message)

        assert result == 'Describe the collision in detail.'

    def test_get_enrichment_prompt_for_message_no_prompt(self):
        """Returns None when alert_config:* exists but no enrichment_prompt."""
        mock_store = MagicMock()
        mock_store.get.return_value = {
            'prompt': 'User prompt',
            'system_prompt': 'System prompt',
        }

        from handlers.prompt_handler.prompt_manager import PromptManager
        manager = PromptManager.__new__(PromptManager)
        manager.logger = Mock()
        manager.alert_config_store = mock_store

        message = {'category': 'collision'}
        result = manager.get_enrichment_prompt_for_message(message)

        assert result is None

    def test_get_enrichment_prompt_for_message_no_category(self):
        """Returns None when message has no category."""
        from handlers.prompt_handler.prompt_manager import PromptManager

        manager = PromptManager.__new__(PromptManager)
        manager.logger = Mock()
        manager.alert_config_store = Mock()

        message = {}
        result = manager.get_enrichment_prompt_for_message(message)

        assert result is None


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
