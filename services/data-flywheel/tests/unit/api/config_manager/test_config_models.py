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

from src.api.config_manager.models import ConfigurationDocument
from src.config import (
    EmbeddingJobConfig,
    LLMJobConfig,
    build_job_config,
)


@pytest.fixture
def sample_llm_config_data():
    """Sample LLM job configuration data."""
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
def sample_embedding_config_data():
    """Sample embedding job configuration data."""
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


class TestConfigurationDocument:
    """Test ConfigurationDocument model."""

    def test_creation_with_required_fields(self, sample_llm_config_data):
        """Test ConfigurationDocument creation with required fields."""
        doc = ConfigurationDocument(
            name="test-config",
            description="Test configuration",
            config_type="llm_job",
            config_data=sample_llm_config_data,
        )

        assert doc.name == "test-config"
        assert doc.description == "Test configuration"
        assert doc.config_type == "llm_job"
        assert isinstance(doc.config_data, LLMJobConfig)
        # Test key fields instead of exact dictionary match (Pydantic adds defaults)
        assert len(doc.config_data.nims) == len(sample_llm_config_data["nims"])
        assert doc.config_data.nims[0].model_name == sample_llm_config_data["nims"][0]["model_name"]
        assert (
            doc.config_data.data_split_config.eval_size
            == sample_llm_config_data["data_split_config"]["eval_size"]
        )
        assert doc.id is not None
        assert doc.created_at is not None
        assert doc.updated_at is not None

    def test_creation_with_optional_fields(self, sample_llm_config_data):
        """Test ConfigurationDocument creation with optional fields."""
        obj_id = ObjectId()
        created_time = datetime.utcnow()
        updated_time = datetime.utcnow()

        doc = ConfigurationDocument(
            _id=obj_id,
            name="test-config",
            description="Test configuration",
            config_type="llm_job",
            config_data=sample_llm_config_data,
            created_at=created_time,
            updated_at=updated_time,
        )

        assert doc.id == obj_id
        assert doc.created_at == created_time
        assert doc.updated_at == updated_time

    def test_missing_config_type(self, sample_llm_config_data):
        """Test config_type is required."""
        with pytest.raises(ValidationError, match="Field required"):
            ConfigurationDocument(name="test-config", config_data=sample_llm_config_data)

    def test_optional_description(self, sample_llm_config_data):
        """Test optional description field."""
        doc = ConfigurationDocument(
            name="test-config", config_type="llm_job", config_data=sample_llm_config_data
        )

        assert doc.description is None

    def test_embedding_job_config_type(self, sample_embedding_config_data):
        """Test embedding job config_type."""
        doc = ConfigurationDocument(
            name="test-config",
            config_type="embedding_job",
            config_data=sample_embedding_config_data,
        )

        assert doc.config_type == "embedding_job"

    def test_missing_required_fields(self):
        """Test missing required fields."""
        with pytest.raises(ValidationError):
            ConfigurationDocument(
                name="test-config"
                # Missing config_data
            )

    def test_empty_name(self, sample_llm_config_data):
        """Test empty name field."""
        with pytest.raises(ValidationError):
            ConfigurationDocument(
                name="", config_type="llm_job", config_data=sample_llm_config_data
            )


class TestConfigurationDocumentMongoOperations:
    """Test MongoDB operations for ConfigurationDocument."""

    def test_to_mongo(self, sample_llm_config_data):
        """Test to_mongo method."""
        doc = ConfigurationDocument(
            name="test-config",
            description="Test configuration",
            config_type="llm_job",
            config_data=sample_llm_config_data,
        )

        mongo_data = doc.to_mongo()

        assert "_id" in mongo_data
        assert mongo_data["name"] == "test-config"
        assert mongo_data["description"] == "Test configuration"
        assert mongo_data["config_type"] == "llm_job"
        # Test key fields instead of exact dictionary match (Pydantic adds defaults)
        config_data = mongo_data["config_data"]
        assert len(config_data["nims"]) == len(sample_llm_config_data["nims"])
        assert (
            config_data["nims"][0]["model_name"] == sample_llm_config_data["nims"][0]["model_name"]
        )
        assert (
            config_data["data_split_config"]["eval_size"]
            == sample_llm_config_data["data_split_config"]["eval_size"]
        )
        assert "created_at" in mongo_data
        assert "updated_at" in mongo_data

    def test_from_mongo(self, sample_llm_config_data):
        """Test from_mongo method."""
        obj_id = ObjectId()
        created_time = datetime.utcnow()
        updated_time = datetime.utcnow()

        mongo_data = {
            "_id": obj_id,
            "name": "test-config",
            "description": "Test configuration",
            "config_type": "llm_job",
            "config_data": sample_llm_config_data,
            "created_at": created_time,
            "updated_at": updated_time,
        }

        doc = ConfigurationDocument.from_mongo(mongo_data)

        assert doc.id == obj_id
        assert doc.name == "test-config"
        assert doc.description == "Test configuration"
        assert doc.config_type == "llm_job"
        assert isinstance(doc.config_data, LLMJobConfig)
        # Test key fields instead of exact dictionary match (Pydantic adds defaults)
        assert len(doc.config_data.nims) == len(sample_llm_config_data["nims"])
        assert doc.config_data.nims[0].model_name == sample_llm_config_data["nims"][0]["model_name"]
        assert (
            doc.config_data.data_split_config.eval_size
            == sample_llm_config_data["data_split_config"]["eval_size"]
        )
        assert doc.created_at == created_time
        assert doc.updated_at == updated_time

    def test_from_mongo_with_missing_fields(self, sample_llm_config_data):
        """Test from_mongo with missing optional fields."""
        obj_id = ObjectId()

        mongo_data = {
            "_id": obj_id,
            "name": "test-config",
            "config_type": "llm_job",
            "config_data": sample_llm_config_data,
        }

        doc = ConfigurationDocument.from_mongo(mongo_data)

        assert doc.id == obj_id
        assert doc.name == "test-config"
        assert doc.description is None
        assert doc.config_type == "llm_job"
        assert isinstance(doc.config_data, LLMJobConfig)
        # Test key fields instead of exact dictionary match (Pydantic adds defaults)
        assert len(doc.config_data.nims) == len(sample_llm_config_data["nims"])
        assert doc.config_data.nims[0].model_name == sample_llm_config_data["nims"][0]["model_name"]
        assert (
            doc.config_data.data_split_config.eval_size
            == sample_llm_config_data["data_split_config"]["eval_size"]
        )

    def test_from_mongo_embedding_job(self, sample_embedding_config_data):
        """Test from_mongo with embedding job configuration."""
        obj_id = ObjectId()

        mongo_data = {
            "_id": obj_id,
            "name": "test-embedding-config",
            "description": "Test embedding configuration",
            "config_type": "embedding_job",
            "config_data": sample_embedding_config_data,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        doc = ConfigurationDocument.from_mongo(mongo_data)

        assert doc.config_type == "embedding_job"
        assert doc.name == "test-embedding-config"
        assert isinstance(doc.config_data, EmbeddingJobConfig)
        # Test key fields instead of exact dictionary match (Pydantic adds defaults)
        assert len(doc.config_data.nims) == len(sample_embedding_config_data["nims"])
        assert (
            doc.config_data.nims[0].model_name
            == sample_embedding_config_data["nims"][0]["model_name"]
        )
        assert (
            doc.config_data.data_split_config.val_ratio
            == sample_embedding_config_data["data_split_config"]["val_ratio"]
        )


class TestConfigurationDocumentJobConfigOperations:
    """Test job configuration operations for ConfigurationDocument."""

    def test_to_job_config_llm_job(self, sample_llm_config_data):
        """Test to_job_config method for LLM job."""
        doc = ConfigurationDocument(
            name="test-config",
            description="Test configuration",
            config_type="llm_job",
            config_data=sample_llm_config_data,
        )

        job_config = doc.to_job_config()

        assert isinstance(job_config, LLMJobConfig)
        assert len(job_config.nims) == 1
        assert job_config.nims[0].model_name == "meta/llama-3.2-1b-instruct"
        assert job_config.nims[0].model_type == "llm"
        assert job_config.data_split_config.eval_size == 100
        assert job_config.icl_config.max_examples == 2
        assert job_config.training_config.training_type == "sft"
        assert job_config.training_config.lora.adapter_dim == 16

    def test_to_job_config_embedding_job(self, sample_embedding_config_data):
        """Test to_job_config method for embedding job."""
        doc = ConfigurationDocument(
            name="test-config",
            description="Test configuration",
            config_type="embedding_job",
            config_data=sample_embedding_config_data,
        )

        job_config = doc.to_job_config()

        assert isinstance(job_config, EmbeddingJobConfig)
        assert len(job_config.nims) == 1
        assert job_config.nims[0].model_name == "nvidia/embedding-model"
        assert job_config.nims[0].model_type == "embedding"
        assert job_config.data_split_config.val_ratio == 0.1
        assert job_config.training_config.training_type == "sft"

    def test_from_job_config_llm_job(self, sample_llm_config_data):
        """Test from_job_config method for LLM job."""
        llm_config = LLMJobConfig(**sample_llm_config_data)

        doc = ConfigurationDocument.from_job_config(
            job_config=llm_config, name="test-config", description="Test configuration"
        )

        assert doc.name == "test-config"
        assert doc.description == "Test configuration"
        assert doc.config_type == "llm_job"
        assert isinstance(doc.to_job_config(), LLMJobConfig)

        # Verify the round-trip conversion
        converted_back = doc.to_job_config()
        assert converted_back.nims[0].model_name == llm_config.nims[0].model_name
        assert converted_back.data_split_config.eval_size == llm_config.data_split_config.eval_size

    def test_from_job_config_embedding_job(self, sample_embedding_config_data):
        """Test from_job_config method for embedding job."""
        embedding_config = EmbeddingJobConfig(**sample_embedding_config_data)

        doc = ConfigurationDocument.from_job_config(
            job_config=embedding_config,
            name="test-embedding-config",
            description="Test embedding configuration",
        )

        assert doc.name == "test-embedding-config"
        assert doc.description == "Test embedding configuration"
        assert doc.config_type == "embedding_job"
        assert isinstance(doc.to_job_config(), EmbeddingJobConfig)

        # Verify the round-trip conversion
        converted_back = doc.to_job_config()
        assert converted_back.nims[0].model_name == embedding_config.nims[0].model_name
        assert (
            converted_back.data_split_config.val_ratio
            == embedding_config.data_split_config.val_ratio
        )

    @pytest.mark.parametrize("config_type", ["aoi_job", "its_job"])
    def test_from_job_config_keeps_the_two_deft_job_types_apart(self, config_type):
        """The one place ``config_type_for`` is called in production code.

        ``aoi_job`` and ``its_job`` are the SAME class, so an ``isinstance``-based
        classifier would persist every ITS config as ``aoi_job`` and the job would
        later be dispatched down the wrong job-type branch — with a green suite.
        Both the body and the assertion are deliberately use_case-blind: the
        stored type must follow the door the config was BUILT through.
        """
        job_config = build_job_config(
            config_type,
            {
                "use_case": "aoi",
                "model_name": "changenet-aoi",
                "selection": {"filters": {"created_after": "1970-01-01T00:00:00Z"}},
            },
        )

        doc = ConfigurationDocument.from_job_config(job_config=job_config, name="deft-config")

        assert doc.config_type == config_type
        # The stamp is a PrivateAttr, so what lands in Mongo is unchanged — and
        # cannot contradict the ``config_type`` column stored beside it.
        stored = doc.to_mongo()
        assert "_config_type" not in stored["config_data"]
        # Rebuilding from Mongo goes back through ``build_job_config``, which
        # re-stamps from the stored column, so the round trip is stable.
        assert ConfigurationDocument.from_mongo(stored).config_type == config_type

    def test_from_job_config_with_none_description(self, sample_llm_config_data):
        """Test from_job_config with None description."""
        llm_config = LLMJobConfig(**sample_llm_config_data)

        doc = ConfigurationDocument.from_job_config(job_config=llm_config, name="test-config")

        assert doc.description is None


class TestConfigurationDocumentEdgeCases:
    """Test edge cases for ConfigurationDocument."""

    def test_config_data_with_extra_fields(self, sample_llm_config_data):
        """Unknown config fields are rejected (backend-delegated build enforces
        the models' extra='forbid' at the top level, consistently with the
        create-request schema)."""
        config_data = sample_llm_config_data.copy()
        config_data["extra_field"] = "extra_value"

        with pytest.raises(ValidationError, match="unexpected field"):
            ConfigurationDocument(
                name="test-config", config_type="llm_job", config_data=config_data
            )

    def test_config_data_with_none_values(self, sample_llm_config_data):
        """Test config_data with None values for optional fields."""
        config_data = sample_llm_config_data.copy()
        config_data["data_split_config"]["random_seed"] = None

        doc = ConfigurationDocument(
            name="test-config", config_type="llm_job", config_data=config_data
        )

        assert isinstance(doc.config_data, LLMJobConfig)
        assert doc.config_data.data_split_config.random_seed is None

    def test_datetime_handling(self, sample_llm_config_data):
        """Test datetime handling in MongoDB operations."""
        now = datetime.utcnow()
        doc = ConfigurationDocument(
            name="test-config",
            config_type="llm_job",
            config_data=sample_llm_config_data,
            created_at=now,
            updated_at=now,
        )

        mongo_data = doc.to_mongo()
        assert mongo_data["created_at"] == now
        assert mongo_data["updated_at"] == now

        # Test round-trip
        doc_from_mongo = ConfigurationDocument.from_mongo(mongo_data)
        assert doc_from_mongo.created_at == now
        assert doc_from_mongo.updated_at == now

    def test_objectid_handling(self, sample_llm_config_data):
        """Test ObjectId handling in MongoDB operations."""
        obj_id = ObjectId()
        doc = ConfigurationDocument(
            _id=obj_id,
            name="test-config",
            config_type="llm_job",
            config_data=sample_llm_config_data,
        )

        mongo_data = doc.to_mongo()
        assert mongo_data["_id"] == obj_id

        # Test round-trip
        doc_from_mongo = ConfigurationDocument.from_mongo(mongo_data)
        assert doc_from_mongo.id == obj_id

    def test_config_data_validation_during_conversion(self, sample_llm_config_data):
        """Test that config_data is validated during document creation."""
        # Test with invalid config_data that should fail validation
        invalid_config_data = sample_llm_config_data.copy()
        invalid_config_data["nims"] = []  # Empty nims should fail

        # The document creation should fail due to validation in @model_validator
        with pytest.raises(ValidationError):
            ConfigurationDocument(
                name="test-config", config_type="llm_job", config_data=invalid_config_data
            )

    def test_round_trip_conversion(self, sample_llm_config_data):
        """Test round-trip conversion from job_config to document and back."""
        llm_config = LLMJobConfig(**sample_llm_config_data)

        # Convert to document
        doc = ConfigurationDocument.from_job_config(
            job_config=llm_config, name="test-config", description="Test configuration"
        )

        # Convert back to job_config
        converted_config = doc.to_job_config()

        # Verify they are equivalent
        assert isinstance(converted_config, LLMJobConfig)
        assert len(converted_config.nims) == len(llm_config.nims)
        assert converted_config.nims[0].model_name == llm_config.nims[0].model_name
        assert (
            converted_config.data_split_config.eval_size == llm_config.data_split_config.eval_size
        )
        assert converted_config.icl_config.max_examples == llm_config.icl_config.max_examples
        assert (
            converted_config.training_config.training_type
            == llm_config.training_config.training_type
        )
        assert (
            converted_config.training_config.lora.adapter_dim
            == llm_config.training_config.lora.adapter_dim
        )

    def test_embedding_round_trip_conversion(self, sample_embedding_config_data):
        """Test round-trip conversion for embedding job configuration."""
        embedding_config = EmbeddingJobConfig(**sample_embedding_config_data)

        # Convert to document
        doc = ConfigurationDocument.from_job_config(
            job_config=embedding_config,
            name="test-embedding-config",
            description="Test embedding configuration",
        )

        # Convert back to job_config
        converted_config = doc.to_job_config()

        # Verify they are equivalent
        assert isinstance(converted_config, EmbeddingJobConfig)
        assert len(converted_config.nims) == len(embedding_config.nims)
        assert converted_config.nims[0].model_name == embedding_config.nims[0].model_name
        assert (
            converted_config.data_split_config.val_ratio
            == embedding_config.data_split_config.val_ratio
        )
        assert (
            converted_config.training_config.training_type
            == embedding_config.training_config.training_type
        )
