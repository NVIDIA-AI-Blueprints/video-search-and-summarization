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
from unittest.mock import Mock, patch

import pytest
from bson import ObjectId
from pydantic import ValidationError

from src.api.config_manager.exceptions import (
    ConfigurationAlreadyExistsError,
    ConfigurationNotFoundError,
    ConfigurationOperationError,
    ConfigurationUpdateError,
)
from src.api.config_manager.manager import ConfigurationManager
from src.api.config_manager.models import ConfigurationDocument
from src.api.config_manager.schemas import ConfigurationSummary


@pytest.fixture
def mock_db():
    """Mock database for testing."""
    mock_db = Mock()
    mock_collection = Mock()
    mock_db.configurations = mock_collection
    return mock_db


@pytest.fixture
def config_manager(mock_db):
    """ConfigurationManager instance with mocked database."""
    with patch("src.api.config_manager.manager.get_db", return_value=mock_db):
        manager = ConfigurationManager()
        return manager


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


class TestConfigurationManager:
    """Test ConfigurationManager CRUD operations."""

    def test_init_with_database_connection(self, mock_db):
        """Test ConfigurationManager initialization with database connection."""
        with patch("src.api.config_manager.manager.get_db", return_value=mock_db):
            manager = ConfigurationManager()
            assert manager.db == mock_db
            assert manager.collection == mock_db.configurations

    def test_init_with_database_error(self):
        """Test ConfigurationManager initialization when database is not initialized."""
        with patch(
            "src.api.config_manager.manager.get_db",
            side_effect=RuntimeError("Database not initialized"),
        ):
            with patch("src.api.config_manager.manager.init_db") as mock_init_db:
                mock_db = Mock()
                mock_init_db.return_value = mock_db
                manager = ConfigurationManager()
                assert manager.db == mock_db
                mock_init_db.assert_called_once()

    def test_list_configurations_empty(self, config_manager):
        """Test listing configurations when none exist."""
        # Mock the find method to return an empty cursor
        mock_cursor = Mock()
        mock_cursor.sort.return_value = []
        config_manager.collection.find.return_value = mock_cursor

        result = config_manager.list_configurations()

        assert result == []
        config_manager.collection.find.assert_called_once()
        mock_cursor.sort.assert_called_once_with("created_at", -1)

    def test_list_configurations_with_data(self, config_manager):
        """Test listing configurations with existing data."""
        mock_docs = [
            {
                "_id": ObjectId(),
                "name": "test-config-1",
                "description": "Test config 1",
                "config_type": "llm_job",
                "config_data": {
                    "nims": [
                        {
                            "model_name": "test",
                            "model_type": "llm",
                            "deployment_type": "local",
                            "context_length": 8192,
                            "gpus": 1,
                            "pvc_size": "25Gi",
                            "tag": "1.0",
                        }
                    ],
                    "data_split_config": {"eval_size": 100},
                    "icl_config": {
                        "max_context_length": 8192,
                        "reserved_tokens": 4096,
                        "max_examples": 3,
                        "min_examples": 1,
                    },
                    "training_config": {
                        "training_type": "sft",
                        "finetuning_type": "lora",
                        "epochs": 1,
                        "batch_size": 16,
                        "learning_rate": 0.0001,
                    },
                },
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            },
            {
                "_id": ObjectId(),
                "name": "test-config-2",
                "description": "Test config 2",
                "config_type": "embedding_job",
                "config_data": {
                    "nims": [
                        {
                            "model_name": "test-embedding",
                            "model_type": "embedding",
                            "deployment_type": "local",
                            "context_length": 32768,
                            "gpus": 1,
                            "pvc_size": "25Gi",
                            "tag": "1.0",
                        }
                    ],
                    "data_split_config": {"val_ratio": 0.05, "min_total_records": 50},
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
                },
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            },
        ]
        # Mock the find method to return a cursor with sort method
        mock_cursor = Mock()
        mock_cursor.sort.return_value = mock_docs
        config_manager.collection.find.return_value = mock_cursor

        result = config_manager.list_configurations()

        assert len(result) == 2
        assert all(isinstance(item, ConfigurationSummary) for item in result)
        assert result[0].name == "test-config-1"
        assert result[1].name == "test-config-2"
        config_manager.collection.find.assert_called_once()
        mock_cursor.sort.assert_called_once_with("created_at", -1)

    def test_create_configuration_llm_job_success(self, config_manager, sample_llm_config_data):
        """Test successful creation of LLM job configuration."""
        mock_result = Mock()
        mock_result.inserted_id = ObjectId()
        config_manager.collection.insert_one.return_value = mock_result

        # Create the config_data dictionary with all required fields
        config_data = {
            "name": "test-llm-config",
            "description": "Test LLM configuration",
            "config_type": "llm_job",
            "config_data": sample_llm_config_data,
        }

        result = config_manager.create_configuration(config_data)

        assert isinstance(result, ConfigurationDocument)
        assert result.name == "test-llm-config"
        assert result.config_type == "llm_job"
        config_manager.collection.insert_one.assert_called_once()

    def test_create_configuration_embedding_job_success(
        self, config_manager, sample_embedding_config_data
    ):
        """Test successful creation of embedding job configuration."""
        mock_result = Mock()
        mock_result.inserted_id = ObjectId()
        config_manager.collection.insert_one.return_value = mock_result

        # Create the config_data dictionary with all required fields
        config_data = {
            "name": "test-embedding-config",
            "description": "Test embedding configuration",
            "config_type": "embedding_job",
            "config_data": sample_embedding_config_data,
        }

        result = config_manager.create_configuration(config_data)

        assert isinstance(result, ConfigurationDocument)
        assert result.name == "test-embedding-config"
        assert result.config_type == "embedding_job"
        config_manager.collection.insert_one.assert_called_once()

    def test_create_configuration_duplicate_name(self, config_manager, sample_llm_config_data):
        """Test creation of configuration with duplicate name."""
        from pymongo.errors import DuplicateKeyError

        # Simulate DuplicateKeyError when trying to insert
        config_manager.collection.insert_one.side_effect = DuplicateKeyError("Duplicate key")

        config_data = {
            "name": "test-config",
            "description": "Test configuration",
            "config_type": "llm_job",
            "config_data": sample_llm_config_data,
        }

        with pytest.raises(ConfigurationAlreadyExistsError):
            config_manager.create_configuration(config_data)

    def test_create_configuration_invalid_config_data(self, config_manager):
        """Test creation with invalid configuration data."""
        config_manager.collection.find_one.return_value = None

        config_data = {
            "name": "test-config",
            "description": "Test configuration",
            "config_type": "llm_job",
            "config_data": {"invalid": "data"},
        }

        with pytest.raises(
            (ValidationError, KeyError)
        ):  # Pydantic ValidationError or KeyError will be raised
            config_manager.create_configuration(config_data)

    def test_get_configuration_success(self, config_manager, sample_llm_config_data):
        """Test successful retrieval of configuration."""
        mock_doc = {
            "_id": ObjectId(),
            "name": "test-config",
            "description": "Test configuration",
            "config_type": "llm_job",
            "config_data": sample_llm_config_data,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        config_manager.collection.find_one.return_value = mock_doc

        result = config_manager.get_configuration("test-config")

        assert isinstance(result, ConfigurationDocument)
        assert result.name == "test-config"
        config_manager.collection.find_one.assert_called_once_with({"name": "test-config"})

    def test_get_configuration_not_found(self, config_manager):
        """Test retrieval of non-existent configuration."""
        config_manager.collection.find_one.return_value = None

        result = config_manager.get_configuration("test-config")

        assert result is None
        config_manager.collection.find_one.assert_called_once_with({"name": "test-config"})

    def test_update_configuration_success(self, config_manager, sample_llm_config_data):
        """Test successful update of configuration."""
        existing_doc = {
            "_id": ObjectId(),
            "name": "test-config",
            "description": "Old description",
            "config_type": "llm_job",
            "config_data": sample_llm_config_data,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        config_manager.collection.find_one.return_value = existing_doc

        mock_result = Mock()
        mock_result.modified_count = 1
        config_manager.collection.replace_one.return_value = mock_result

        updated_data = sample_llm_config_data.copy()
        updated_data["data_split_config"]["eval_size"] = 200

        config_data = {
            "name": "test-config",
            "description": "Updated description",
            "config_type": "llm_job",
            "config_data": updated_data,
        }

        result = config_manager.update_configuration("test-config", config_data)

        assert isinstance(result, ConfigurationDocument)
        assert result.name == "test-config"
        config_manager.collection.find_one.assert_called_once_with({"name": "test-config"})
        config_manager.collection.replace_one.assert_called_once()

    def test_update_configuration_not_found(self, config_manager, sample_llm_config_data):
        """Test update of non-existent configuration."""
        config_manager.collection.find_one.return_value = None

        with pytest.raises(ConfigurationNotFoundError):
            config_manager.update_configuration(
                "test-config",
                {
                    "name": "test-config",
                    "description": "Updated description",
                    "config_type": "llm_job",
                    "config_data": sample_llm_config_data,
                },
            )

    def test_update_configuration_name_mismatch(self, config_manager, sample_llm_config_data):
        """Test update with name mismatch."""
        existing_doc = {
            "_id": ObjectId(),
            "name": "test-config",
            "description": "Old description",
            "config_type": "llm_job",
            "config_data": sample_llm_config_data,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        config_manager.collection.find_one.return_value = existing_doc

        with pytest.raises(ConfigurationUpdateError):
            config_manager.update_configuration(
                "test-config",
                {
                    "name": "different-name",
                    "description": "Updated description",
                    "config_type": "llm_job",
                    "config_data": sample_llm_config_data,
                },
            )

    def test_delete_configuration_success(self, config_manager):
        """Test successful deletion of configuration."""
        # Mock find_one to return existing config
        existing_doc = {"_id": ObjectId(), "name": "test-config"}
        config_manager.collection.find_one.return_value = existing_doc

        # Mock delete_one to return successful deletion
        mock_result = Mock()
        mock_result.deleted_count = 1
        config_manager.collection.delete_one.return_value = mock_result

        result = config_manager.delete_configuration("test-config")

        assert result == "test-config"
        config_manager.collection.find_one.assert_called_once_with({"name": "test-config"})
        config_manager.collection.delete_one.assert_called_once_with({"name": "test-config"})

    def test_delete_configuration_not_found(self, config_manager):
        """Test deletion of non-existent configuration."""
        config_manager.collection.find_one.return_value = None  # No existing config

        with pytest.raises(ConfigurationNotFoundError):
            config_manager.delete_configuration("test-config")

    def test_delete_configuration_operation_failed(self, config_manager):
        """Test deletion when delete_one returns deleted_count = 0."""
        # Mock find_one to return existing config
        existing_doc = {"_id": ObjectId(), "name": "test-config"}
        config_manager.collection.find_one.return_value = existing_doc

        # Mock delete_one to return failed deletion
        mock_result = Mock()
        mock_result.deleted_count = 0
        config_manager.collection.delete_one.return_value = mock_result

        with pytest.raises(ConfigurationOperationError):
            config_manager.delete_configuration("test-config")


class TestConfigurationManagerIntegration:
    """Integration tests for ConfigurationManager."""

    def test_full_crud_workflow(self, config_manager, sample_llm_config_data):
        """Test complete CRUD workflow."""
        # Create
        config_manager.collection.find_one.return_value = None
        mock_result = Mock()
        mock_result.inserted_id = ObjectId()
        config_manager.collection.insert_one.return_value = mock_result

        config_data = {
            "name": "test-config",
            "description": "Test configuration",
            "config_type": "llm_job",
            "config_data": sample_llm_config_data,
        }

        created = config_manager.create_configuration(config_data)

        assert created.name == "test-config"

        # Read
        mock_doc = {
            "_id": mock_result.inserted_id,
            "name": "test-config",
            "description": "Test configuration",
            "config_type": "llm_job",
            "config_data": sample_llm_config_data,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        config_manager.collection.find_one.return_value = mock_doc

        retrieved = config_manager.get_configuration("test-config")
        assert retrieved.name == "test-config"

        # Update
        updated_data = sample_llm_config_data.copy()
        updated_data["data_split_config"]["eval_size"] = 200

        mock_update_result = Mock()
        mock_update_result.modified_count = 1
        config_manager.collection.replace_one.return_value = mock_update_result

        updated = config_manager.update_configuration(
            "test-config",
            {
                "name": "test-config",
                "description": "Updated configuration",
                "config_type": "llm_job",
                "config_data": updated_data,
            },
        )

        assert updated.name == "test-config"

        # Delete
        mock_delete_result = Mock()
        mock_delete_result.deleted_count = 1
        config_manager.collection.delete_one.return_value = mock_delete_result

        deleted_name = config_manager.delete_configuration("test-config")
        assert deleted_name == "test-config"

    def test_error_handling_database_errors(self, config_manager, sample_llm_config_data):
        """Test error handling for database errors."""
        # Test create with database error
        config_manager.collection.find_one.return_value = None
        config_manager.collection.insert_one.side_effect = Exception("Database error")

        config_data = {
            "name": "test-config",
            "description": "Test configuration",
            "config_type": "llm_job",
            "config_data": sample_llm_config_data,
        }

        with pytest.raises(Exception, match="Database error"):
            config_manager.create_configuration(config_data)

    def test_configuration_validation_edge_cases(self, config_manager):
        """Test configuration validation with edge cases."""
        # Test with empty nims list
        invalid_config = {
            "nims": [],
            "data_split_config": {"eval_size": 20},
            "icl_config": {"max_examples": 3},
            "training_config": {"training_type": "sft"},
        }

        config_manager.collection.find_one.return_value = None

        config_data = {
            "name": "test-config",
            "description": "Test configuration",
            "config_type": "llm_job",
            "config_data": invalid_config,
        }

        with pytest.raises(
            (ValidationError, KeyError)
        ):  # Pydantic ValidationError or KeyError will be raised
            config_manager.create_configuration(config_data)
