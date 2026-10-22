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

from datetime import datetime

import pytest
from bson import ObjectId
from pydantic import ValidationError

from src.api.config_manager.schemas import (
    ConfigurationRequest,
    ConfigurationResponse,
    ConfigurationSummary,
)


@pytest.fixture
def valid_llm_config_data():
    """Valid LLM job configuration data."""
    return {
        "nims": [
            {
                "model_name": "meta/llama-3.2-1b-instruct",
                "model_type": "llm",
                "context_length": 8192,
                "gpus": 1,
                "pvc_size": "25Gi",
                "tag": "1.8.3",
            }
        ],
        "data_split_config": {
            "eval_size": 100,
            "val_ratio": 0.1,
            "min_total_records": 50,
            "limit": 1000,
            "parse_function_arguments": True,
        },
        "icl_config": {
            "max_context_length": 2048,
            "reserved_tokens": 512,
            "max_examples": 2,
            "min_examples": 1,
            "example_selection": "uniform_distribution",
        },
        "training_config": {
            "training_type": "sft",
            "finetuning_type": "lora",
            "epochs": 1,
            "batch_size": 8,
            "learning_rate": 0.001,
            "lora": {"adapter_dim": 16, "adapter_dropout": 0.05},
        },
    }


@pytest.fixture
def valid_embedding_config_data():
    """Valid embedding job configuration data."""
    return {
        "nims": [
            {
                "model_name": "nvidia/embedding-model",
                "model_type": "embedding",
                "context_length": 1024,
                "gpus": 1,
                "pvc_size": "20Gi",
                "tag": "1.0.0",
            }
        ],
        "data_split_config": {
            "val_ratio": 0.1,
            "min_total_records": 50,
            "limit": 1000,
        },
        "training_config": {
            "training_type": "sft",
            "finetuning_type": "all_weights",
            "epochs": 1,
            "batch_size": 256,
            "learning_rate": 0.000005,
            "micro_batch_size": 1,
            "tensor_parallel_size": 1,
            "pipeline_parallel_size": 1,
            "use_sequence_parallel": False,
            "training_precision": "bf16-mixed",
        },
    }


class TestConfigurationRequest:
    """Test ConfigurationRequest schema validation."""

    def test_valid_llm_job_request(self, valid_llm_config_data):
        """Test valid LLM job configuration request."""
        request = ConfigurationRequest(
            name="test-llm-config",
            description="Test LLM configuration",
            config_type="llm_job",
            config_data=valid_llm_config_data,
        )

        assert request.name == "test-llm-config"
        assert request.description == "Test LLM configuration"
        assert request.config_type == "llm_job"
        assert request.config_data == valid_llm_config_data

    def test_valid_embedding_job_request(self, valid_embedding_config_data):
        """Test valid embedding job configuration request."""
        request = ConfigurationRequest(
            name="test-embedding-config",
            description="Test embedding configuration",
            config_type="embedding_job",
            config_data=valid_embedding_config_data,
        )

        assert request.name == "test-embedding-config"
        assert request.description == "Test embedding configuration"
        assert request.config_type == "embedding_job"
        assert request.config_data == valid_embedding_config_data

    def test_missing_config_type(self, valid_llm_config_data):
        """Test config_type is required."""
        with pytest.raises(ValidationError, match="Field required"):
            ConfigurationRequest(
                name="test-config",
                description="Test configuration",
                config_data=valid_llm_config_data,
            )

    def test_optional_description(self, valid_llm_config_data):
        """Test optional description field."""
        request = ConfigurationRequest(
            name="test-config", config_type="llm_job", config_data=valid_llm_config_data
        )

        assert request.description is None

    def test_invalid_config_type(self, valid_llm_config_data):
        """Test invalid config_type."""
        with pytest.raises(ValidationError):
            ConfigurationRequest(
                name="test-config",
                description="Test configuration",
                config_type="invalid_type",
                config_data=valid_llm_config_data,
            )

    def test_invalid_llm_config_data(self, valid_llm_config_data):
        """Test invalid LLM configuration data."""
        invalid_data = valid_llm_config_data.copy()
        invalid_data["nims"] = []  # Empty nims should fail validation

        with pytest.raises(ValidationError, match="Invalid configuration data"):
            ConfigurationRequest(
                name="test-config",
                description="Test configuration",
                config_type="llm_job",
                config_data=invalid_data,
            )

    def test_invalid_embedding_config_data(self, valid_embedding_config_data):
        """Test invalid embedding configuration data."""
        invalid_data = valid_embedding_config_data.copy()
        invalid_data["nims"] = [
            {
                "model_name": "test-model",
                "model_type": "llm",  # Wrong type for embedding job
                "context_length": 1024,
                "gpus": 1,
                "pvc_size": "20Gi",
                "tag": "1.0.0",
            }
        ]

        with pytest.raises(ValidationError, match="Invalid configuration data"):
            ConfigurationRequest(
                name="test-config",
                description="Test configuration",
                config_type="embedding_job",
                config_data=invalid_data,
            )

    def test_missing_required_fields(self):
        """Test missing required fields."""
        with pytest.raises(ValidationError):
            ConfigurationRequest(
                name="test-config",
                # Missing description, config_type, config_data
            )

    def test_empty_name(self, valid_llm_config_data):
        """Test empty name field."""
        with pytest.raises(ValidationError):
            ConfigurationRequest(
                name="",
                description="Test configuration",
                config_type="llm_job",
                config_data=valid_llm_config_data,
            )

    def test_config_data_validation_llm_job(self, valid_llm_config_data):
        """Test config_data validation for LLM job."""
        # Test with valid data
        request = ConfigurationRequest(
            name="test-config", config_type="llm_job", config_data=valid_llm_config_data
        )
        assert request.config_data is not None

    def test_config_data_validation_embedding_job(self, valid_embedding_config_data):
        """Test config_data validation for embedding job."""
        # Test with valid data
        request = ConfigurationRequest(
            name="test-config", config_type="embedding_job", config_data=valid_embedding_config_data
        )
        assert request.config_data is not None


class TestConfigurationResponse:
    """Test ConfigurationResponse schema validation."""

    def test_valid_response_creation(self, valid_llm_config_data):
        """Test valid response creation."""
        response = ConfigurationResponse(
            id=str(ObjectId()),
            name="test-config",
            description="Test configuration",
            config_type="llm_job",
            config_data=valid_llm_config_data,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        assert response.name == "test-config"
        assert response.description == "Test configuration"
        assert response.config_type == "llm_job"
        assert response.config_data == valid_llm_config_data
        assert response.created_at is not None
        assert response.updated_at is not None

    def test_optional_description_in_response(self, valid_llm_config_data):
        """Test optional description in response."""
        response = ConfigurationResponse(
            id=str(ObjectId()),
            name="test-config",
            config_type="llm_job",
            config_data=valid_llm_config_data,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        assert response.description is None

    def test_missing_config_type_in_response(self, valid_llm_config_data):
        """Test config_type is required in response."""
        with pytest.raises(ValidationError, match="Field required"):
            ConfigurationResponse(
                id=str(ObjectId()),
                name="test-config",
                config_data=valid_llm_config_data,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )

    def test_embedding_job_response(self, valid_embedding_config_data):
        """Test embedding job response creation."""
        response = ConfigurationResponse(
            id=str(ObjectId()),
            name="test-embedding-config",
            description="Test embedding configuration",
            config_type="embedding_job",
            config_data=valid_embedding_config_data,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        assert response.config_type == "embedding_job"
        assert response.name == "test-embedding-config"

    def test_missing_required_fields_response(self):
        """Test missing required fields in response."""
        with pytest.raises(ValidationError):
            ConfigurationResponse(
                name="test-config",
                # Missing id, config_type, config_data, created_at, updated_at
            )


class TestConfigurationSummary:
    """Test ConfigurationSummary schema validation."""

    def test_valid_summary_creation(self):
        """Test valid summary creation."""
        summary = ConfigurationSummary(
            id=str(ObjectId()),
            name="test-config",
            description="Test configuration",
            config_type="llm_job",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        assert summary.name == "test-config"
        assert summary.description == "Test configuration"
        assert summary.config_type == "llm_job"
        assert summary.created_at is not None
        assert summary.updated_at is not None

    def test_optional_description_in_summary(self):
        """Test optional description in summary."""
        summary = ConfigurationSummary(
            id=str(ObjectId()),
            name="test-config",
            config_type="llm_job",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        assert summary.description is None

    def test_missing_config_type_in_summary(self):
        """Test config_type is required in summary."""
        with pytest.raises(ValidationError, match="Field required"):
            ConfigurationSummary(
                id=str(ObjectId()),
                name="test-config",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )

    def test_embedding_job_summary(self):
        """Test embedding job summary creation."""
        summary = ConfigurationSummary(
            id=str(ObjectId()),
            name="test-embedding-config",
            description="Test embedding configuration",
            config_type="embedding_job",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        assert summary.config_type == "embedding_job"
        assert summary.name == "test-embedding-config"

    def test_missing_required_fields_summary(self):
        """Test missing required fields in summary."""
        with pytest.raises(ValidationError):
            ConfigurationSummary(
                name="test-config",
                # Missing id, config_type, created_at, updated_at
            )


class TestSchemaValidationEdgeCases:
    """Test edge cases for schema validation."""

    def test_config_data_with_extra_fields(self, valid_llm_config_data):
        """Test config_data with extra fields (should be rejected)."""
        config_data = valid_llm_config_data.copy()
        config_data["extra_field"] = "extra_value"

        with pytest.raises(ValidationError, match="Invalid configuration data"):
            ConfigurationRequest(name="test-config", config_type="llm_job", config_data=config_data)

    def test_config_data_with_missing_optional_fields(self, valid_llm_config_data):
        """Test config_data with missing optional fields."""
        config_data = valid_llm_config_data.copy()
        # Remove optional fields that actually exist in the test data
        del config_data["data_split_config"]["parse_function_arguments"]

        request = ConfigurationRequest(
            name="test-config", config_type="llm_job", config_data=config_data
        )

        assert request.config_data is not None
        # Verify the optional field is not present in the config_data
        assert "parse_function_arguments" not in request.config_data["data_split_config"]

    def test_config_data_with_none_values(self, valid_llm_config_data):
        """Test config_data with None values for optional fields."""
        config_data = valid_llm_config_data.copy()
        # Set a boolean field to None (should fail validation)
        config_data["data_split_config"]["parse_function_arguments"] = None

        with pytest.raises(ValidationError, match="Invalid configuration data"):
            ConfigurationRequest(name="test-config", config_type="llm_job", config_data=config_data)

    def test_config_type_case_sensitivity(self, valid_llm_config_data):
        """Test config_type case sensitivity."""
        with pytest.raises(ValidationError):
            ConfigurationRequest(
                name="test-config",
                config_type="LLM_JOB",  # Wrong case
                config_data=valid_llm_config_data,
            )

    def test_datetime_serialization(self, valid_llm_config_data):
        """Test datetime serialization in responses."""
        now = datetime.utcnow()
        response = ConfigurationResponse(
            id=str(ObjectId()),
            name="test-config",
            config_type="llm_job",
            config_data=valid_llm_config_data,
            created_at=now,
            updated_at=now,
        )

        assert response.created_at == now
        assert response.updated_at == now

    def test_objectid_serialization(self, valid_llm_config_data):
        """Test ObjectId serialization in responses."""
        obj_id = ObjectId()
        response = ConfigurationResponse(
            id=str(obj_id),
            name="test-config",
            config_type="llm_job",
            config_data=valid_llm_config_data,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        assert response.id == str(obj_id)
