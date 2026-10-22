# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Tests for model resolver."""

import pytest
from bson import ObjectId

from src.tasks.triggers.model_resolver import ModelResolver


class TestModelResolver:
    """Tests for ModelResolver class."""

    def test_resolve_base_models(
        self,
        mock_trigger_db,
        sample_data_threshold_trigger_model,
        sample_config_with_nims,
    ):
        """Test resolving base models from config."""
        resolver = ModelResolver(mock_trigger_db)

        # Mock config retrieval
        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        result = resolver.resolve(sample_data_threshold_trigger_model)

        assert len(result) == 2
        assert "meta/llama-3.2-1b-instruct" in result
        assert "meta/llama-3.2-3b-instruct" in result

        # Check ModelInfo structure - target is model_name for base models
        assert result["meta/llama-3.2-1b-instruct"]["is_fine_tuned"] is False
        assert result["meta/llama-3.2-1b-instruct"]["target"] == "meta/llama-3.2-1b-instruct"

    def test_resolve_fine_tuned_from_stored(
        self,
        mock_trigger_db,
        sample_embedding_metric_degradation_trigger_model,
        sample_config_with_nims,
    ):
        """Test resolving fine-tuned models from trigger storage."""
        resolver = ModelResolver(mock_trigger_db)

        # Clear reference_job_id to test pure stored model resolution
        sample_embedding_metric_degradation_trigger_model.reference_job_id = None

        # Mock config retrieval
        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        result = resolver.resolve(sample_embedding_metric_degradation_trigger_model)

        # Should have stored fine-tuned model for first NIM
        assert result["meta/llama-3.2-1b-instruct"]["is_fine_tuned"] is True
        assert result["meta/llama-3.2-1b-instruct"]["target"] == "dfwbp/model@cust-123"

        # Should fall back to base for second NIM (not in stored models)
        assert result["meta/llama-3.2-3b-instruct"]["is_fine_tuned"] is False
        assert result["meta/llama-3.2-3b-instruct"]["target"] == "meta/llama-3.2-3b-instruct"

    def test_resolve_fine_tuned_from_reference_job(
        self,
        mock_trigger_db,
        sample_embedding_metric_degradation_trigger_model,
        sample_config_with_nims,
    ):
        """Test resolving fine-tuned models from reference job."""
        resolver = ModelResolver(mock_trigger_db)

        # Clear stored models to test reference job fallback
        sample_embedding_metric_degradation_trigger_model.latest_fine_tuned_models = {}

        # Mock config retrieval
        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        # Mock trigger_executions query for reference job validation
        ref_job_id = sample_embedding_metric_degradation_trigger_model.reference_job_id
        mock_trigger_db.trigger_executions.find_one.return_value = {
            "_id": ObjectId(),
            "trigger_id": str(sample_embedding_metric_degradation_trigger_model.id),
            "training_job_id": ref_job_id,
            "nim_results": [
                {
                    "model_name": "meta/llama-3.2-1b-instruct",
                    "fine_tuned_model": "dfwbp/model@cust-ref-123",
                },
                {
                    "model_name": "meta/llama-3.2-3b-instruct",
                    "fine_tuned_model": None,  # Fallback to base
                },
            ],
        }

        # Mock retraining_triggers query
        mock_trigger_db.retraining_triggers.find_one.return_value = {
            "_id": sample_embedding_metric_degradation_trigger_model.id,
            "config_name": "test-config",
        }

        # Mock customizations from reference job
        mock_trigger_db.customizations.find.return_value = [
            {
                "_id": ObjectId(),
                "flywheel_run_id": ObjectId(ref_job_id),
                "model_name": "meta/llama-3.2-1b-instruct",
                "status": "completed",
                "customized_nim": "dfwbp/model@cust-ref-123",
            },
        ]

        result = resolver.resolve(sample_embedding_metric_degradation_trigger_model)

        # Should use model from reference job
        assert result["meta/llama-3.2-1b-instruct"]["is_fine_tuned"] is True
        assert result["meta/llama-3.2-1b-instruct"]["target"] == "dfwbp/model@cust-ref-123"

    def test_reference_job_fallback_preserves_all_nims(
        self,
        mock_trigger_db,
        sample_embedding_metric_degradation_trigger_model,
        sample_config_with_nims,
    ):
        """Test reference job fallback doesn't omit NIMs missing from job."""
        resolver = ModelResolver(mock_trigger_db)

        # Clear stored models
        sample_embedding_metric_degradation_trigger_model.latest_fine_tuned_models = {}

        # Mock config retrieval
        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        # Mock trigger_executions query for reference job validation
        ref_job_id = sample_embedding_metric_degradation_trigger_model.reference_job_id
        mock_trigger_db.trigger_executions.find_one.return_value = {
            "_id": ObjectId(),
            "trigger_id": str(sample_embedding_metric_degradation_trigger_model.id),
            "training_job_id": ref_job_id,
            "nim_results": [
                {
                    "model_name": "meta/llama-3.2-1b-instruct",
                    "fine_tuned_model": "dfwbp/model@cust-ref-123",
                },
                # NOTE: meta/llama-3.2-3b-instruct is missing from this job
            ],
        }

        # Mock retraining_triggers query
        mock_trigger_db.retraining_triggers.find_one.return_value = {
            "_id": sample_embedding_metric_degradation_trigger_model.id,
            "config_name": "test-config",
        }

        # Mock customizations from reference job - only includes ONE NIM
        mock_trigger_db.customizations.find.return_value = [
            {
                "_id": ObjectId(),
                "flywheel_run_id": ObjectId(ref_job_id),
                "model_name": "meta/llama-3.2-1b-instruct",
                "status": "completed",
                "customized_nim": "dfwbp/model@cust-ref-123",
            },
        ]

        result = resolver.resolve(sample_embedding_metric_degradation_trigger_model)

        # Both NIMs should be present
        assert len(result) == 2

        # First NIM has fine-tuned model from reference job
        assert result["meta/llama-3.2-1b-instruct"]["is_fine_tuned"] is True
        assert result["meta/llama-3.2-1b-instruct"]["target"] == "dfwbp/model@cust-ref-123"

        # Second NIM falls back to base (missing from reference job)
        assert result["meta/llama-3.2-3b-instruct"]["is_fine_tuned"] is False
        assert result["meta/llama-3.2-3b-instruct"]["target"] == "meta/llama-3.2-3b-instruct"

        # Should still have BOTH NIMs (not omit the second one)
        assert len(result) == 2
        assert "meta/llama-3.2-1b-instruct" in result
        assert "meta/llama-3.2-3b-instruct" in result

    def test_reference_job_overrides_stored_models(
        self,
        mock_trigger_db,
        sample_embedding_metric_degradation_trigger_model,
        sample_config_with_nims,
    ):
        """Test reference job models override stored models when reference_job_id is provided."""
        resolver = ModelResolver(mock_trigger_db)

        # Stored model present
        sample_embedding_metric_degradation_trigger_model.latest_fine_tuned_models = {
            "meta/llama-3.2-1b-instruct": "dfwbp/model@cust-stored-999",
        }

        # Mock config retrieval
        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        # Mock trigger_executions query for reference job validation
        ref_job_id = sample_embedding_metric_degradation_trigger_model.reference_job_id
        mock_trigger_db.trigger_executions.find_one.return_value = {
            "_id": ObjectId(),
            "trigger_id": str(sample_embedding_metric_degradation_trigger_model.id),
            "training_job_id": ref_job_id,
            "nim_results": [
                {
                    "model_name": "meta/llama-3.2-1b-instruct",
                    "fine_tuned_model": "dfwbp/model@cust-ref-123",  # Different from stored
                },
            ],
        }

        # Mock retraining_triggers query
        mock_trigger_db.retraining_triggers.find_one.return_value = {
            "_id": sample_embedding_metric_degradation_trigger_model.id,
            "config_name": "test-config",
        }

        # Mock customizations from reference job (different model)
        mock_trigger_db.customizations.find.return_value = [
            {
                "_id": ObjectId(),
                "flywheel_run_id": ObjectId(ref_job_id),
                "model_name": "meta/llama-3.2-1b-instruct",
                "status": "completed",
                "customized_nim": "dfwbp/model@cust-ref-123",
            },
        ]

        result = resolver.resolve(sample_embedding_metric_degradation_trigger_model)

        # When reference_job_id is provided, it takes precedence over stored models
        # (stored models only used as fallback for NIMs missing from reference job)
        assert result["meta/llama-3.2-1b-instruct"]["is_fine_tuned"] is True
        assert result["meta/llama-3.2-1b-instruct"]["target"] == "dfwbp/model@cust-ref-123"

    def test_model_info_structure(
        self,
        mock_trigger_db,
        sample_data_threshold_trigger_model,
        sample_config_with_nims,
    ):
        """Test ModelInfo has correct structure."""
        resolver = ModelResolver(mock_trigger_db)

        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        result = resolver.resolve(sample_data_threshold_trigger_model)

        for model_info in result.values():
            # Check keys
            assert "target" in model_info
            assert "is_fine_tuned" in model_info

            # Check types
            assert isinstance(model_info["target"], str)
            assert isinstance(model_info["is_fine_tuned"], bool)

    def test_no_config_found_raises_error(
        self, mock_trigger_db, sample_data_threshold_trigger_model
    ):
        """Test error when config is not found."""
        resolver = ModelResolver(mock_trigger_db)

        # No config found
        mock_trigger_db.configurations.find_one.return_value = None

        with pytest.raises(ValueError) as exc_info:
            resolver.resolve(sample_data_threshold_trigger_model)

        assert "not found" in str(exc_info.value).lower()

    def test_config_with_no_nims_raises_error(
        self, mock_trigger_db, sample_data_threshold_trigger_model
    ):
        """Test error when config has no NIMs."""
        resolver = ModelResolver(mock_trigger_db)

        # Config with no NIMs
        mock_trigger_db.configurations.find_one.return_value = {
            "_id": ObjectId(),
            "name": "test-config",
            "nims": [],
        }

        with pytest.raises(ValueError) as exc_info:
            resolver.resolve(sample_data_threshold_trigger_model)

        assert "No NIMs found" in str(exc_info.value)


class TestModelResolverTargetExtraction:
    """Tests for target model extraction from config."""

    def test_extract_target_from_customizer_config(
        self, mock_trigger_db, sample_data_threshold_trigger_model
    ):
        """Test that model_name is used as target for base models."""
        resolver = ModelResolver(mock_trigger_db)

        config = {
            "_id": ObjectId(),
            "name": "test-config",
            "config_data": {
                "nims": [
                    {
                        "model_name": "test-model",
                        "customizer_configs": {
                            "target": "test-model@v1.0",
                        },
                    },
                ],
            },
        }

        mock_trigger_db.configurations.find_one.return_value = config

        result = resolver.resolve(sample_data_threshold_trigger_model)

        # For base models, model_name itself is used as the target
        # The @version or customizer_configs.target is handled by the pipeline, not the resolver
        assert result["test-model"]["target"] == "test-model"
        assert result["test-model"]["is_fine_tuned"] is False
