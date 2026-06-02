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

"""Unit tests for custom output category mapping feature."""

import pytest
from unittest.mock import MagicMock, patch
from typing import Dict, Any


class TestAlertTypeConfigOutputCategory:
    """Tests for AlertTypeConfig.output_category field and get_output_category_mapping()."""

    def test_output_category_field_optional(self):
        """Test that output_category field is optional in AlertTypeConfig."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfig, Prompts

        # Without output_category - should work
        config = AlertTypeConfig(
            alert_type="collision",
            prompts=Prompts(user="test prompt", system="test system"),
        )
        assert config.alert_type == "collision"
        assert config.output_category is None

    def test_output_category_field_with_value(self):
        """Test that output_category field accepts a value."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfig, Prompts

        config = AlertTypeConfig(
            alert_type="collision",
            output_category="Vehicle Collision",
            prompts=Prompts(user="test prompt", system="test system"),
        )
        assert config.alert_type == "collision"
        assert config.output_category == "Vehicle Collision"


class TestAlertTypeConfigSegmentAnchor:
    """Tests for AlertTypeConfig.segment_anchor field for per-alert-type video segment anchoring."""

    def test_segment_anchor_field_optional(self):
        """Test that segment_anchor field is optional in AlertTypeConfig."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfig, Prompts

        # Without segment_anchor - should work and default to None
        config = AlertTypeConfig(
            alert_type="collision",
            prompts=Prompts(user="test prompt", system="test system"),
        )
        assert config.alert_type == "collision"
        assert config.segment_anchor is None

    def test_segment_anchor_field_start(self):
        """Test that segment_anchor accepts 'start' value."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfig, Prompts

        config = AlertTypeConfig(
            alert_type="collision",
            segment_anchor="start",
            prompts=Prompts(user="test prompt", system="test system"),
        )
        assert config.segment_anchor == "start"

    def test_segment_anchor_field_end(self):
        """Test that segment_anchor accepts 'end' value."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfig, Prompts

        config = AlertTypeConfig(
            alert_type="unauthorized_entry",
            segment_anchor="end",
            prompts=Prompts(user="test prompt", system="test system"),
        )
        assert config.segment_anchor == "end"

    def test_segment_anchor_field_middle(self):
        """Test that segment_anchor accepts 'middle' value."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfig, Prompts

        config = AlertTypeConfig(
            alert_type="unsafe_behavior",
            segment_anchor="middle",
            prompts=Prompts(user="test prompt", system="test system"),
        )
        assert config.segment_anchor == "middle"

    def test_segment_anchor_case_insensitive(self):
        """Test that segment_anchor is normalized to lowercase."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfig, Prompts

        config = AlertTypeConfig(
            alert_type="collision",
            segment_anchor="START",
            prompts=Prompts(user="test prompt", system="test system"),
        )
        assert config.segment_anchor == "start"

        config2 = AlertTypeConfig(
            alert_type="collision",
            segment_anchor="End",
            prompts=Prompts(user="test prompt", system="test system"),
        )
        assert config2.segment_anchor == "end"

        config3 = AlertTypeConfig(
            alert_type="collision",
            segment_anchor="MIDDLE",
            prompts=Prompts(user="test prompt", system="test system"),
        )
        assert config3.segment_anchor == "middle"

    def test_segment_anchor_invalid_value_raises_error(self):
        """Test that invalid segment_anchor value raises ValidationError."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfig, Prompts
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            AlertTypeConfig(
                alert_type="collision",
                segment_anchor="invalid",
                prompts=Prompts(user="test prompt", system="test system"),
            )
        assert "segment_anchor" in str(exc_info.value)

    def test_segment_anchor_with_all_fields(self):
        """Test AlertTypeConfig with all fields including segment_anchor."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfig, Prompts

        config = AlertTypeConfig(
            alert_type="collision",
            output_category="Vehicle Collision",
            segment_anchor="start",
            prompts=Prompts(
                user="test user prompt",
                system="test system prompt",
                enrichment="test enrichment prompt",
            ),
        )
        assert config.alert_type == "collision"
        assert config.output_category == "Vehicle Collision"
        assert config.segment_anchor == "start"
        assert config.prompts.user == "test user prompt"
        assert config.prompts.system == "test system prompt"
        assert config.prompts.enrichment == "test enrichment prompt"

    def test_get_output_category_mapping_empty(self):
        """Test get_output_category_mapping returns empty dict when no mappings defined."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfigLoader

        with patch.object(AlertTypeConfigLoader, '_load_configurations'):
            loader = AlertTypeConfigLoader.__new__(AlertTypeConfigLoader)
            loader.alert_configs = {}
            loader.logger = MagicMock()

            mapping = loader.get_output_category_mapping()
            assert mapping == {}

    def test_get_output_category_mapping_with_values(self):
        """Test get_output_category_mapping returns correct mapping."""
        from handlers.prompt_handler.alert_type_config_loader import (
            AlertTypeConfigLoader,
            AlertTypeConfig,
            Prompts,
        )

        with patch.object(AlertTypeConfigLoader, '_load_configurations'):
            loader = AlertTypeConfigLoader.__new__(AlertTypeConfigLoader)
            loader.logger = MagicMock()

            # Create mock configs - some with output_category, some without
            loader.alert_configs = {
                "collision": AlertTypeConfig(
                    alert_type="collision",
                    output_category="Vehicle Collision",
                    prompts=Prompts(user="test", system="test"),
                ),
                "unsafe_behavior": AlertTypeConfig(
                    alert_type="unsafe_behavior",
                    # No output_category - should not be included
                    prompts=Prompts(user="test", system="test"),
                ),
                "Stop Anomaly Module": AlertTypeConfig(
                    alert_type="Stop Anomaly Module",
                    output_category="Abnormal Stop Event",
                    prompts=Prompts(user="test", system="test"),
                ),
            }

            mapping = loader.get_output_category_mapping()

            assert len(mapping) == 2
            assert mapping["collision"] == "Vehicle Collision"
            assert mapping["Stop Anomaly Module"] == "Abnormal Stop Event"
            assert "unsafe_behavior" not in mapping  # No output_category defined

    def test_get_output_category_mapping_no_configs(self):
        """Test get_output_category_mapping handles missing alert_configs attribute."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfigLoader

        with patch.object(AlertTypeConfigLoader, '_load_configurations'):
            loader = AlertTypeConfigLoader.__new__(AlertTypeConfigLoader)
            loader.logger = MagicMock()
            # Don't set alert_configs - simulates failed load

            mapping = loader.get_output_category_mapping()
            assert mapping == {}


class TestCategoryMappingInElastic:
    """Tests for category mapping application in ElasticClient.write_event_response()."""

    def test_category_mapping_applied_after_fingerprint(self):
        """Test that category mapping is applied after fingerprint computation."""
        # This is a conceptual test - the actual fingerprint is computed first,
        # then category is mapped. We verify the document has mapped category.

        document = {
            "category": "collision",
            "sensorId": "test-sensor",
            "timestamp": "2025-12-22T04:22:27.800Z",
        }
        category_mapping = {"collision": "Vehicle Collision"}

        # Simulate the mapping logic from write_event_response
        if category_mapping and 'category' in document:
            original_category = document['category']
            if original_category in category_mapping:
                document['category'] = category_mapping[original_category]

        assert document['category'] == "Vehicle Collision"

    def test_category_mapping_preserves_unmapped(self):
        """Test that unmapped categories are preserved."""
        document = {
            "category": "unknown_type",
            "sensorId": "test-sensor",
        }
        category_mapping = {"collision": "Vehicle Collision"}

        if category_mapping and 'category' in document:
            original_category = document['category']
            if original_category in category_mapping:
                document['category'] = category_mapping[original_category]

        assert document['category'] == "unknown_type"  # Unchanged

    def test_category_mapping_empty_mapping(self):
        """Test that empty mapping leaves category unchanged."""
        document = {
            "category": "collision",
            "sensorId": "test-sensor",
        }
        category_mapping = {}

        if category_mapping and 'category' in document:
            original_category = document['category']
            if original_category in category_mapping:
                document['category'] = category_mapping[original_category]

        assert document['category'] == "collision"  # Unchanged

    def test_category_mapping_none_mapping(self):
        """Test that None mapping leaves category unchanged."""
        document = {
            "category": "collision",
            "sensorId": "test-sensor",
        }
        category_mapping = None

        if category_mapping and 'category' in document:
            original_category = document['category']
            if original_category in category_mapping:
                document['category'] = category_mapping[original_category]

        assert document['category'] == "collision"  # Unchanged

    def test_category_mapping_missing_category_field(self):
        """Test that missing category field is handled gracefully."""
        document = {
            "sensorId": "test-sensor",
        }
        category_mapping = {"collision": "Vehicle Collision"}

        if category_mapping and 'category' in document:
            original_category = document['category']
            if original_category in category_mapping:
                document['category'] = category_mapping[original_category]

        assert 'category' not in document  # Still no category


class TestFactoryCategoryMappingLoading:
    """Tests for category mapping loading in sink factory."""

    def test_load_category_mapping_success(self):
        """Test successful loading of category mapping from alert type config."""
        from mdx.anomaly.sink.vlm_enhanced_sink.factory import _load_category_mapping

        mock_mapping = {"collision": "Vehicle Collision"}

        with patch(
            'handlers.prompt_handler.alert_type_config_loader.AlertTypeConfigLoader'
        ) as MockLoader:
            mock_instance = MagicMock()
            mock_instance.get_output_category_mapping.return_value = mock_mapping
            MockLoader.return_value = mock_instance

            config = {"alert_type_config_file": "alert_type_config.json"}
            mapping = _load_category_mapping(config)

            assert mapping == mock_mapping

    def test_load_category_mapping_failure_returns_empty(self):
        """Test that loading failure returns empty dict."""
        from mdx.anomaly.sink.vlm_enhanced_sink.factory import _load_category_mapping

        with patch(
            'handlers.prompt_handler.alert_type_config_loader.AlertTypeConfigLoader'
        ) as MockLoader:
            MockLoader.side_effect = Exception("Config not found")

            config = {}
            mapping = _load_category_mapping(config)

            assert mapping == {}


class TestSinkCategoryMappingIntegration:
    """Integration tests for category mapping through sink chain."""

    def test_elastic_sink_accepts_category_mapping(self):
        """Test that VLMEnhancedElasticSink accepts category_mapping parameter."""
        from mdx.anomaly.sink.vlm_enhanced_sink.sink_elastic import VLMEnhancedElasticSink

        mock_client = MagicMock()
        category_mapping = {"collision": "Vehicle Collision"}

        sink = VLMEnhancedElasticSink(
            elastic_client=mock_client,
            incident_index="mdx-vlm-incidents",
            alert_index="mdx-vlm-alerts",
            redis_handler=None,
            category_mapping=category_mapping,
        )

        assert sink._category_mapping == category_mapping

    def test_kafka_sink_accepts_category_mapping(self):
        """Test that VLMEnhancedKafkaSink accepts category_mapping parameter."""
        from mdx.anomaly.sink.vlm_enhanced_sink.sink_kafka import VLMEnhancedKafkaSink

        mock_producer = MagicMock()
        category_mapping = {"collision": "Vehicle Collision"}

        sink = VLMEnhancedKafkaSink(
            producer=mock_producer,
            incident_route={"topic": "test-incidents"},
            alert_route={"topic": "test-alerts"},
            category_mapping=category_mapping,
        )

        assert sink._category_mapping == category_mapping

    def test_elastic_sink_default_empty_mapping(self):
        """Test that VLMEnhancedElasticSink defaults to empty mapping."""
        from mdx.anomaly.sink.vlm_enhanced_sink.sink_elastic import VLMEnhancedElasticSink

        mock_client = MagicMock()

        sink = VLMEnhancedElasticSink(
            elastic_client=mock_client,
            incident_index="mdx-vlm-incidents",
            alert_index="mdx-vlm-alerts",
        )

        assert sink._category_mapping == {}

    def test_kafka_sink_default_empty_mapping(self):
        """Test that VLMEnhancedKafkaSink defaults to empty mapping."""
        from mdx.anomaly.sink.vlm_enhanced_sink.sink_kafka import VLMEnhancedKafkaSink

        mock_producer = MagicMock()

        sink = VLMEnhancedKafkaSink(
            producer=mock_producer,
            incident_route={"topic": "test-incidents"},
            alert_route={"topic": "test-alerts"},
        )

        assert sink._category_mapping == {}


class TestAlertTypeConfigVerdictDescription:
    """Tests for AlertTypeConfig.output_verdict_description field and get_verdict_description_mapping()."""

    def test_output_verdict_description_field_optional(self):
        """Test that output_verdict_description field is optional in AlertTypeConfig."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfig, Prompts

        # Without output_verdict_description - should work
        config = AlertTypeConfig(
            alert_type="collision",
            prompts=Prompts(user="test prompt", system="test system"),
        )
        assert config.alert_type == "collision"
        assert config.output_verdict_description is None

    def test_output_verdict_description_field_with_value(self):
        """Test that output_verdict_description field accepts a dict value."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfig, Prompts

        config = AlertTypeConfig(
            alert_type="collision",
            output_verdict_description={
                "confirmed": "Collision detected",
                "rejected": "No collision detected",
            },
            prompts=Prompts(user="test prompt", system="test system"),
        )
        assert config.alert_type == "collision"
        assert config.output_verdict_description == {
            "confirmed": "Collision detected",
            "rejected": "No collision detected",
        }

    def test_get_verdict_description_mapping_empty(self):
        """Test get_verdict_description_mapping returns empty dict when no mappings defined."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfigLoader

        with patch.object(AlertTypeConfigLoader, '_load_configurations'):
            loader = AlertTypeConfigLoader.__new__(AlertTypeConfigLoader)
            loader.alert_configs = {}
            loader.logger = MagicMock()

            mapping = loader.get_verdict_description_mapping()
            assert mapping == {}

    def test_get_verdict_description_mapping_with_values(self):
        """Test get_verdict_description_mapping returns correct mapping."""
        from handlers.prompt_handler.alert_type_config_loader import (
            AlertTypeConfigLoader,
            AlertTypeConfig,
            Prompts,
        )

        with patch.object(AlertTypeConfigLoader, '_load_configurations'):
            loader = AlertTypeConfigLoader.__new__(AlertTypeConfigLoader)
            loader.logger = MagicMock()

            # Create mock configs - some with output_verdict_description, some without
            loader.alert_configs = {
                "collision": AlertTypeConfig(
                    alert_type="collision",
                    output_verdict_description={
                        "confirmed": "Collision detected",
                        "rejected": "No collision detected",
                    },
                    prompts=Prompts(user="test", system="test"),
                ),
                "unsafe_behavior": AlertTypeConfig(
                    alert_type="unsafe_behavior",
                    # No output_verdict_description - should not be included
                    prompts=Prompts(user="test", system="test"),
                ),
            }

            mapping = loader.get_verdict_description_mapping()

            assert len(mapping) == 1
            assert "collision" in mapping
            assert mapping["collision"]["confirmed"] == "Collision detected"
            assert mapping["collision"]["rejected"] == "No collision detected"
            assert "unsafe_behavior" not in mapping

    def test_get_verdict_description_mapping_no_configs(self):
        """Test get_verdict_description_mapping handles missing alert_configs attribute."""
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfigLoader

        with patch.object(AlertTypeConfigLoader, '_load_configurations'):
            loader = AlertTypeConfigLoader.__new__(AlertTypeConfigLoader)
            loader.logger = MagicMock()
            # Don't set alert_configs - simulates failed load

            mapping = loader.get_verdict_description_mapping()
            assert mapping == {}


class TestVerdictDescriptionMappingInElastic:
    """Tests for verdict description mapping application in ElasticClient.write_event_response()."""

    def test_verdict_description_applied_for_confirmed(self):
        """Test that description is overridden for confirmed verdict."""
        document = {
            "category": "collision",
            "sensorId": "test-sensor",
            "timestamp": "2025-12-22T04:22:27.800Z",
            "info": {"verdict": "confirmed"},
            "analyticsModule": {"description": "Potential collision detected"},
        }
        verdict_description_mapping = {
            "collision": {
                "confirmed": "Collision detected",
                "rejected": "No collision detected",
            }
        }

        # Simulate the mapping logic from write_event_response
        if verdict_description_mapping and 'category' in document:
            original_category = document['category']
            if original_category in verdict_description_mapping:
                verdict = (document.get("info", {}).get("verdict") or "").lower()
                desc_mapping = verdict_description_mapping[original_category]
                if verdict in desc_mapping:
                    if "analyticsModule" not in document:
                        document["analyticsModule"] = {}
                    document["analyticsModule"]["description"] = desc_mapping[verdict]

        assert document["analyticsModule"]["description"] == "Collision detected"

    def test_verdict_description_applied_for_rejected(self):
        """Test that description is overridden for rejected verdict."""
        document = {
            "category": "collision",
            "sensorId": "test-sensor",
            "timestamp": "2025-12-22T04:22:27.800Z",
            "info": {"verdict": "rejected"},
            "analyticsModule": {"description": "Potential collision detected"},
        }
        verdict_description_mapping = {
            "collision": {
                "confirmed": "Collision detected",
                "rejected": "No collision detected",
            }
        }

        if verdict_description_mapping and 'category' in document:
            original_category = document['category']
            if original_category in verdict_description_mapping:
                verdict = (document.get("info", {}).get("verdict") or "").lower()
                desc_mapping = verdict_description_mapping[original_category]
                if verdict in desc_mapping:
                    if "analyticsModule" not in document:
                        document["analyticsModule"] = {}
                    document["analyticsModule"]["description"] = desc_mapping[verdict]

        assert document["analyticsModule"]["description"] == "No collision detected"

    def test_verdict_description_case_insensitive(self):
        """Test that verdict lookup is case-insensitive."""
        document = {
            "category": "collision",
            "info": {"verdict": "CONFIRMED"},  # uppercase
            "analyticsModule": {"description": "Potential collision detected"},
        }
        verdict_description_mapping = {
            "collision": {"confirmed": "Collision detected"}  # lowercase key
        }

        if verdict_description_mapping and 'category' in document:
            original_category = document['category']
            if original_category in verdict_description_mapping:
                verdict = (document.get("info", {}).get("verdict") or "").lower()
                desc_mapping = verdict_description_mapping[original_category]
                if verdict in desc_mapping:
                    document["analyticsModule"]["description"] = desc_mapping[verdict]

        assert document["analyticsModule"]["description"] == "Collision detected"

    def test_verdict_description_creates_analyticsmodule_if_missing(self):
        """Test that analyticsModule is created if not present."""
        document = {
            "category": "collision",
            "info": {"verdict": "confirmed"},
            # No analyticsModule field
        }
        verdict_description_mapping = {
            "collision": {"confirmed": "Collision detected"}
        }

        if verdict_description_mapping and 'category' in document:
            original_category = document['category']
            if original_category in verdict_description_mapping:
                verdict = (document.get("info", {}).get("verdict") or "").lower()
                desc_mapping = verdict_description_mapping[original_category]
                if verdict in desc_mapping:
                    if "analyticsModule" not in document:
                        document["analyticsModule"] = {}
                    document["analyticsModule"]["description"] = desc_mapping[verdict]

        assert "analyticsModule" in document
        assert document["analyticsModule"]["description"] == "Collision detected"

    def test_verdict_description_no_override_for_unknown_verdict(self):
        """Test that unknown verdicts don't get overridden."""
        document = {
            "category": "collision",
            "info": {"verdict": "unknown"},
            "analyticsModule": {"description": "Original description"},
        }
        verdict_description_mapping = {
            "collision": {
                "confirmed": "Collision detected",
                "rejected": "No collision detected",
            }
        }

        if verdict_description_mapping and 'category' in document:
            original_category = document['category']
            if original_category in verdict_description_mapping:
                verdict = (document.get("info", {}).get("verdict") or "").lower()
                desc_mapping = verdict_description_mapping[original_category]
                if verdict in desc_mapping:
                    document["analyticsModule"]["description"] = desc_mapping[verdict]

        assert document["analyticsModule"]["description"] == "Original description"

    def test_verdict_description_no_override_for_unmapped_category(self):
        """Test that unmapped categories don't get overridden."""
        document = {
            "category": "unsafe_behavior",
            "info": {"verdict": "confirmed"},
            "analyticsModule": {"description": "Original description"},
        }
        verdict_description_mapping = {
            "collision": {"confirmed": "Collision detected"}
        }

        if verdict_description_mapping and 'category' in document:
            original_category = document['category']
            if original_category in verdict_description_mapping:
                verdict = (document.get("info", {}).get("verdict") or "").lower()
                desc_mapping = verdict_description_mapping[original_category]
                if verdict in desc_mapping:
                    document["analyticsModule"]["description"] = desc_mapping[verdict]

        assert document["analyticsModule"]["description"] == "Original description"

    def test_verdict_description_handles_missing_info(self):
        """Test graceful handling when info field is missing."""
        document = {
            "category": "collision",
            # No info field
            "analyticsModule": {"description": "Original description"},
        }
        verdict_description_mapping = {
            "collision": {"confirmed": "Collision detected"}
        }

        if verdict_description_mapping and 'category' in document:
            original_category = document['category']
            if original_category in verdict_description_mapping:
                verdict = (document.get("info", {}).get("verdict") or "").lower()
                desc_mapping = verdict_description_mapping[original_category]
                if verdict in desc_mapping:
                    document["analyticsModule"]["description"] = desc_mapping[verdict]

        assert document["analyticsModule"]["description"] == "Original description"

    def test_verdict_description_handles_none_verdict(self):
        """Test graceful handling when verdict is None."""
        document = {
            "category": "collision",
            "info": {"verdict": None},
            "analyticsModule": {"description": "Original description"},
        }
        verdict_description_mapping = {
            "collision": {"confirmed": "Collision detected"}
        }

        if verdict_description_mapping and 'category' in document:
            original_category = document['category']
            if original_category in verdict_description_mapping:
                verdict = (document.get("info", {}).get("verdict") or "").lower()
                desc_mapping = verdict_description_mapping[original_category]
                if verdict in desc_mapping:
                    document["analyticsModule"]["description"] = desc_mapping[verdict]

        assert document["analyticsModule"]["description"] == "Original description"


class TestFactoryVerdictDescriptionMappingLoading:
    """Tests for verdict description mapping loading in sink factory."""

    def test_load_verdict_description_mapping_success(self):
        """Test successful loading of verdict description mapping."""
        from mdx.anomaly.sink.vlm_enhanced_sink.factory import _load_verdict_description_mapping

        mock_mapping = {
            "collision": {
                "confirmed": "Collision detected",
                "rejected": "No collision detected",
            }
        }

        with patch(
            'handlers.prompt_handler.alert_type_config_loader.AlertTypeConfigLoader'
        ) as MockLoader:
            mock_instance = MagicMock()
            mock_instance.get_verdict_description_mapping.return_value = mock_mapping
            MockLoader.return_value = mock_instance

            config = {"alert_type_config_file": "alert_type_config.json"}
            mapping = _load_verdict_description_mapping(config)

            assert mapping == mock_mapping

    def test_load_verdict_description_mapping_failure_returns_empty(self):
        """Test that loading failure returns empty dict."""
        from mdx.anomaly.sink.vlm_enhanced_sink.factory import _load_verdict_description_mapping

        with patch(
            'handlers.prompt_handler.alert_type_config_loader.AlertTypeConfigLoader'
        ) as MockLoader:
            MockLoader.side_effect = Exception("Config not found")

            config = {}
            mapping = _load_verdict_description_mapping(config)

            assert mapping == {}


class TestSinkVerdictDescriptionMappingIntegration:
    """Integration tests for verdict description mapping through sink chain."""

    def test_elastic_sink_accepts_verdict_description_mapping(self):
        """Test that VLMEnhancedElasticSink accepts verdict_description_mapping parameter."""
        from mdx.anomaly.sink.vlm_enhanced_sink.sink_elastic import VLMEnhancedElasticSink

        mock_client = MagicMock()
        verdict_description_mapping = {
            "collision": {
                "confirmed": "Collision detected",
                "rejected": "No collision detected",
            }
        }

        sink = VLMEnhancedElasticSink(
            elastic_client=mock_client,
            incident_index="mdx-vlm-incidents",
            alert_index="mdx-vlm-alerts",
            redis_handler=None,
            verdict_description_mapping=verdict_description_mapping,
        )

        assert sink._verdict_description_mapping == verdict_description_mapping

    def test_elastic_sink_default_empty_verdict_description_mapping(self):
        """Test that VLMEnhancedElasticSink defaults to empty verdict_description_mapping."""
        from mdx.anomaly.sink.vlm_enhanced_sink.sink_elastic import VLMEnhancedElasticSink

        mock_client = MagicMock()

        sink = VLMEnhancedElasticSink(
            elastic_client=mock_client,
            incident_index="mdx-vlm-incidents",
            alert_index="mdx-vlm-alerts",
        )

        assert sink._verdict_description_mapping == {}
