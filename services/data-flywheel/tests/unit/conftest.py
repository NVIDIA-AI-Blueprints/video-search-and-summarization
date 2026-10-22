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
from unittest.mock import MagicMock, patch

import pytest
from bson.objectid import ObjectId

from src.api.models import LLMJudgeConfig, TaskResult
from src.api.schemas import DeploymentStatus, FlywheelRunStatus
from src.config import EmbeddingConfig, ICLConfig, SimilarityConfig, settings
from src.lib.flywheel.icl_selection import ICLSelection


@pytest.fixture(scope="session", autouse=True)
def mock_db():
    """Fixture to provide a database connection for each test."""
    # Import here after patching to avoid real MongoDB connections

    # Create a TaskDBManager mock
    mock_db_manager = MagicMock()

    # Configure default return values for common methods
    mock_db_manager.create_nim_run.return_value = MagicMock()
    mock_db_manager.find_nim_run.return_value = {}
    mock_db_manager.insert_evaluation.return_value = MagicMock()
    mock_db_manager.insert_customization.return_value = MagicMock()

    # Patch TaskDBManager
    with patch("src.api.db_manager.TaskDBManager", return_value=mock_db_manager):
        yield mock_db_manager


@pytest.fixture(scope="function", autouse=True)
def mock_db_functions(mock_db):
    """
    Mock all database-related functions to prevent direct DB access during tests.

    This fixture is automatically applied to all tests and ensures that:
    1. No actual database connections are made
    2. All db.init_db() and db.get_db() calls are mocked
    3. All functions in different modules that use these database
       functions are properly mocked
    """
    # Configure the mock database manager to return False for cancellation checks by default
    mock_db.is_flywheel_run_cancelled.return_value = False

    with (
        patch("src.api.db.get_db", return_value=mock_db),
        patch("src.api.db.init_db", return_value=mock_db),
        patch("src.api.job_service.get_db", return_value=mock_db),
        patch("src.api.db_manager.get_db_manager", return_value=mock_db),
        patch("src.tasks.tasks.db_manager", mock_db),
        patch("src.lib.integration.dataset_creator.get_db", return_value=mock_db),
    ):
        yield


@pytest.fixture
def remote_llm_judge_config():
    """Create a remote LLM judge configuration for testing."""
    return LLMJudgeConfig.from_json(
        {
            "deployment_type": "remote",
            "url": "http://test-remote-url/v1/chat/completions",
            "model_name": "remote-model-id",
            "api_key": "test-api-key",
            "api_key_env": "TEST_API_KEY_ENV",
        }
    )


@pytest.fixture()
def sample_customizer_config():
    """Fixture to create a sample CustomizerConfig instance."""
    from src.config import CustomizerConfig

    return CustomizerConfig(
        target="test-model-id@v1.0.0",
        gpus=1,
        num_nodes=1,
        tensor_parallel_size=1,
        data_parallel_size=1,
        use_sequence_parallel=False,
        micro_batch_size=1,
        training_precision="bf16-mixed",
        max_seq_length=2048,
    )


@pytest.fixture()
def local_llm_judge_config():
    """Create a local LLM judge configuration for testing."""
    return LLMJudgeConfig.local_config(
        {
            "deployment_type": "local",
            "model_name": "test-model-id",
            "context_length": 2048,
            "gpus": 1,
            "pvc_size": "10Gi",
            "tag": "latest",
            "registry_base": "nvcr.io/nim",
        }
    )


@pytest.fixture()
def sample_nim_config_with_customization(sample_customizer_config):
    """Create a NIMConfig with customization enabled for testing customization features."""
    from src.config import NIMConfig

    return NIMConfig(
        model_name="test-model-customizable",
        context_length=2048,
        gpus=1,
        pvc_size="10Gi",
        tag="latest",
        registry_base="nvcr.io/nim",
        customization_enabled=True,
        customizer_configs=sample_customizer_config,
    )


@pytest.fixture()
def tweak_settings(monkeypatch):
    """Provide deterministic test configuration via the global `settings`."""

    # --- Data-split parameters (fields are *not* frozen) --------------------
    monkeypatch.setattr(
        settings.job_configs["llm_job"].data_split_config, "min_total_records", 1, raising=False
    )
    monkeypatch.setattr(
        settings.job_configs["llm_job"].data_split_config, "random_seed", 42, raising=False
    )
    monkeypatch.setattr(
        settings.job_configs["llm_job"].data_split_config, "eval_size", 10, raising=False
    )  # Changed from 1 to 10 to allow validation
    monkeypatch.setattr(
        settings.job_configs["llm_job"].data_split_config, "val_ratio", 0.25, raising=False
    )
    monkeypatch.setattr(
        settings.job_configs["llm_job"].data_split_config, "limit", 100, raising=False
    )

    # --- NMP namespace (field *is* frozen, so create a new object) ----------
    new_nmp_cfg = settings.system_config.nmp_config.model_copy(
        update={"nmp_namespace": "test-namespace"}
    )
    monkeypatch.setattr(settings, "nmp_config", new_nmp_cfg, raising=True)

    yield


@pytest.fixture
def mock_data_uploader():
    """Fixture to mock DataUploader."""
    with patch("src.backends.nmp.provider.NmpBackendProvider.datastore") as mock:
        mock_instance = MagicMock()
        # Ensure that `get_file_uri` (used when recording dataset metadata) returns a
        # plain string.  A raw ``MagicMock`` instance cannot be encoded by BSON and
        # causes an ``InvalidDocument`` error when the code under test attempts to
        # update MongoDB.
        mock_instance.get_file_uri.return_value = "nmp://test-namespace/datasets/dummy.jsonl"
        mock.return_value = mock_instance
        yield mock_instance


@pytest.fixture(autouse=True)
def mock_es_client():
    """Fixture to mock Elasticsearch client."""
    # Close any existing ES client before each test to avoid state pollution
    from src.lib.integration.es_client import close_es_client

    close_es_client()

    mock_instance = MagicMock()

    # Mock only the methods actually used in test paths
    mock_instance.search.return_value = {"hits": {"hits": []}}
    mock_instance.ping.return_value = True
    mock_instance.cluster.health.return_value = {"status": "green"}
    mock_instance.indices.exists.return_value = False
    mock_instance.indices.create.return_value = {}
    mock_instance.indices.refresh.return_value = {}
    mock_instance.indices.delete.return_value = {}

    # Patch all the specific import locations where get_es_client is used
    with (
        patch("src.lib.integration.es_client.get_es_client", return_value=mock_instance),
        patch("src.lib.integration.record_exporter.get_es_client", return_value=mock_instance),
        patch("src.lib.flywheel.icl_selection.get_es_client", return_value=mock_instance),
        patch("src.lib.integration.es_client.index_embeddings_to_es", return_value="test_index"),
        patch("src.lib.integration.es_client.search_similar_embeddings", return_value=[]),
        patch("src.lib.integration.es_client.delete_embeddings_index", return_value=None),
        patch("src.lib.flywheel.icl_selection.search_similar_embeddings", return_value=[]),
        patch("src.lib.flywheel.icl_selection.index_embeddings_to_es", return_value="test_index"),
    ):
        yield mock_instance

    # Properly close the ES client after the test to ensure clean state
    close_es_client()


@pytest.fixture(autouse=True)
def mock_embedding_client():
    """Fixture to mock Embedding client."""

    def mock_get_embedding(queries, input_type="query"):
        """Mock get_embedding to return appropriate number of embeddings"""
        if isinstance(queries, list):
            # Return one embedding per query
            return [[0.1, 0.2, 0.3] * 682 for _ in queries]  # Mock 2048-dim embeddings
        else:
            # Single query
            return [[0.1, 0.2, 0.3] * 682]  # Single mock 2048-dim embedding

    with (
        patch("src.backends.nmp.provider.NmpBackendProvider.embedding") as mock_embedding_class,
        patch("src.backends.nmp.embedding.Embedding") as mock_embedding_class_nemo,
    ):
        mock_instance = MagicMock()
        mock_instance.get_embedding.side_effect = mock_get_embedding
        mock_embedding_class.return_value = mock_instance
        mock_embedding_class_nemo.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def workflow_params():
    """Setup parameters for workflow initialization tests"""
    return {
        "workload_id": "test_workload",
        "flywheel_run_id": str(ObjectId()),
        "client_id": "test_client",
    }


@pytest.fixture
def task_result_setup():
    """Setup TaskResult for dataset creation tests"""
    from src.config import settings

    return TaskResult(
        workload_id="test_workload",
        flywheel_run_id=str(ObjectId()),
        client_id="test_client",
        data_split_config=settings.job_configs["llm_job"].data_split_config,
        job_config=settings.job_configs["llm_job"],
    )


@pytest.fixture
def job_config_setup():
    """Setup job config for initialize_workflow tests"""
    from src.config import settings

    return settings.job_configs["llm_job"].model_dump()


@pytest.fixture
def split_config_setup():
    """Setup split config for data validator tests"""
    from src.config import settings

    return settings.job_configs["llm_job"].data_split_config


@pytest.fixture
def workflow_setup():
    """Setup fixture for workflow initialization tests"""
    return {
        "nims_config": [
            {"model_name": "model_1", "deployment_type": "local", "gpus": 1, "tag": "latest"},
            {"model_name": "model_2", "deployment_type": "local", "gpus": 1, "tag": "latest"},
        ],
        "llm_judge_config": {
            "deployment_type": "remote",
            "url": "http://llm-judge-endpoint.com",
            "model_name": "llm_judge_model",
            "api_key": "test_api_key",
        },
    }


@pytest.fixture
def dataset_setup():
    """Setup fixture for dataset creation tests"""
    return {
        "workload_id": "test_workload",
        "flywheel_run_id": str(ObjectId()),
        "client_id": "test_client",
        "num_records": 100,
        "output_dataset_prefix": "test_prefix",
    }


@pytest.fixture
def common_workflow_params():
    """Common workflow parameters used across multiple test files"""
    return {
        "workload_id": "test-workload-123",
        "flywheel_run_id": str(ObjectId()),
        "client_id": "test-client-456",
        "output_dataset_prefix": "",
    }


@pytest.fixture
def sample_es_data():
    """Sample Elasticsearch data for testing dataset creation"""
    return {
        "_scroll_id": "scroll123",
        "hits": {
            "hits": [
                {
                    "_source": {
                        "request": {
                            "messages": [
                                {"role": "user", "content": f"Question {i}"},
                                {"role": "assistant", "content": f"Answer {i}"},
                            ]
                        },
                        "response": {"choices": [{"message": {"content": f"Response {i}"}}]},
                    }
                }
                for i in range(30)
            ]
        },
    }


@pytest.fixture
def empty_es_data():
    """Empty Elasticsearch data for testing error scenarios"""
    return {"_scroll_id": "scroll123", "hits": {"hits": []}}


@pytest.fixture
def test_db(mock_db):
    """Fixture to set up test database with basic sample data"""
    # Create test flywheel run
    flywheel_run_id = ObjectId()
    mock_db.flywheel_runs.insert_one.return_value = {"inserted_id": flywheel_run_id}

    # Create test NIMs
    nim1_id = ObjectId()
    nim2_id = ObjectId()

    # Create test LLM Judge
    llm_judge_id = ObjectId()

    # Mock flywheel run data
    mock_db.flywheel_runs.find_one.return_value = {
        "_id": flywheel_run_id,
        "status": FlywheelRunStatus.PENDING,
        "workload_id": "test_workload",
        "client_id": "test_client",
        "started_at": datetime.utcnow(),
        "num_records": 0,
        "nims": [],
        "datasets": [],
    }

    # Mock NIMs data
    mock_db.nims.find.return_value = []

    mock_db.nims.insert_many.return_value = {"inserted_ids": [nim1_id, nim2_id]}

    # Mock evaluations data
    mock_db.evaluations.find.return_value = []
    # Mock customizations data
    mock_db.customizations.find.return_value = []

    mock_db.llm_judge_runs.insert_one.return_value = {"inserted_id": llm_judge_id}

    # Mock LLM Judge data
    mock_db.llm_judge_runs.find_one.return_value = None

    return {
        "flywheel_run_id": str(flywheel_run_id),
        "workload_id": "test_workload",
        "client_id": "test_client",
        "nim1_id": str(nim1_id),
        "nim2_id": str(nim2_id),
        "llm_judge_id": str(llm_judge_id),
    }


@pytest.fixture
def test_db_success(mock_db):
    """Fixture to set up test database with complete successful data"""
    # Create test flywheel run
    flywheel_run_id = ObjectId()
    mock_db.flywheel_runs.insert_one.return_value = {"inserted_id": flywheel_run_id}

    # Create test NIMs
    nim1_id = ObjectId()
    nim2_id = ObjectId()

    # Create test LLM Judge
    llm_judge_id = ObjectId()

    # Mock flywheel run data
    mock_db.flywheel_runs.find_one.return_value = {
        "_id": flywheel_run_id,
        "workload_id": "test_workload",
        "client_id": "test_client",
        "started_at": datetime.utcnow(),
        "num_records": 100,
        "nims": [],
        "datasets": [
            {
                "name": f"test_dataset_{i + 1}",
                "num_records": 100,
                "nmp_uri": f"test_uri_{i + 1}",
            }
            for i in range(4)
        ],
    }

    # Mock NIMs data
    mock_db.nims.find.return_value = [
        {
            "_id": nim1_id,
            "model_name": "test_model_1",
            "flywheel_run_id": flywheel_run_id,
            "deployment_status": DeploymentStatus.COMPLETED,
            "runtime_seconds": 120.0,
        },
        {
            "_id": nim2_id,
            "model_name": "test_model_2",
            "flywheel_run_id": flywheel_run_id,
            "deployment_status": DeploymentStatus.PENDING,
            "runtime_seconds": 60.0,
        },
    ]

    mock_db.nims.insert_many.return_value = {"inserted_ids": [nim1_id, nim2_id]}

    # Mock evaluations data
    mock_db.evaluations.find.return_value = [
        {
            "nim_id": nim1_id,
            "eval_type": "accuracy",
            "dataset": "test_dataset_1",
            "scores": {"function_name": 0.95},
            "started_at": datetime.utcnow(),
            "finished_at": datetime.utcnow(),
            "runtime_seconds": 15.0,
            "progress": 100,
            "nmp_uri": "test_uri_eval_1",
        },
        {
            "nim_id": nim2_id,
            "eval_type": "accuracy",
            "dataset": "test_dataset_2",
            "scores": {"function_name": 0.85},
            "started_at": datetime.utcnow(),
            "finished_at": datetime.utcnow(),
            "runtime_seconds": 15.0,
            "progress": 100,
            "nmp_uri": "test_uri_eval_2",
        },
    ]

    # Mock customizations data
    mock_db.customizations.find.return_value = [
        {
            "nim_id": nim1_id,
            "started_at": datetime.utcnow(),
            "finished_at": datetime.utcnow(),
            "runtime_seconds": 30.0,
            "progress": 100,
            "epochs_completed": 10,
            "steps_completed": 1000,
            "nmp_uri": "test_uri_custom_1",
        },
        {
            "nim_id": nim2_id,
            "started_at": datetime.utcnow(),
            "finished_at": datetime.utcnow(),
            "runtime_seconds": 30.0,
            "progress": 100,
            "epochs_completed": 8,
            "steps_completed": 800,
            "nmp_uri": "test_uri_custom_2",
        },
    ]

    mock_db.llm_judge_runs.insert_one.return_value = {"inserted_id": llm_judge_id}

    # Mock LLM Judge data
    mock_db.llm_judge_runs.find_one.return_value = {
        "_id": llm_judge_id,
        "flywheel_run_id": flywheel_run_id,
        "model_name": "test-llm-judge",
        "deployment_type": "remote",
        "deployment_status": DeploymentStatus.READY,
        "url": "http://test-llm-judge.com",
    }

    return {
        "flywheel_run_id": str(flywheel_run_id),
        "workload_id": "test_workload",
        "client_id": "test_client",
        "nim1_id": str(nim1_id),
        "nim2_id": str(nim2_id),
        "llm_judge_id": str(llm_judge_id),
    }


@pytest.fixture
def valid_openai_record():
    """Valid OpenAI chat completion format record."""
    return {
        "request": {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What is the capital of France?"},
            ]
        },
        "response": {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The capital of France is Paris.",
                    }
                }
            ]
        },
    }


@pytest.fixture
def openai_record_with_tool_calls():
    """OpenAI record with tool calls."""
    return {
        "request": {"messages": [{"role": "user", "content": "What's the weather in New York?"}]},
        "response": {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "I'll check the weather for you.",
                        "tool_calls": [
                            {
                                "id": "call_abc123",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"location": "New York", "unit": "fahrenheit"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    }


@pytest.fixture
def openai_records_batch():
    """Batch of various OpenAI format records for testing."""
    return [
        # Simple conversation
        {
            "request": {"messages": [{"role": "user", "content": "Hello"}]},
            "response": {"choices": [{"message": {"content": "Hi there!"}}]},
        },
        # With system message
        {
            "request": {
                "messages": [
                    {"role": "system", "content": "You are helpful"},
                    {"role": "user", "content": "Tell me a joke"},
                ]
            },
            "response": {
                "choices": [{"message": {"content": "Why did the chicken cross the road?"}}]
            },
        },
        # Multi-turn conversation
        {
            "request": {
                "messages": [
                    {"role": "user", "content": "What is 2+2?"},
                    {"role": "assistant", "content": "2+2 equals 4"},
                    {"role": "user", "content": "What about 3+3?"},
                ]
            },
            "response": {"choices": [{"message": {"content": "3+3 equals 6"}}]},
        },
    ]


@pytest.fixture
def invalid_openai_records():
    """Collection of invalid OpenAI format records."""
    return [
        # Missing request
        {"response": {"choices": []}},
        # Missing response
        {"request": {"messages": []}},
        # Invalid request type
        {"request": "not a dict", "response": {"choices": []}},
        # Missing messages
        {"request": {}, "response": {"choices": []}},
        # Invalid messages type
        {"request": {"messages": "not a list"}, "response": {"choices": []}},
        # Missing choices
        {"request": {"messages": []}, "response": {}},
        # Invalid choices type
        {"request": {"messages": []}, "response": {"choices": "not a list"}},
    ]


# ICL-related common fixtures
@pytest.fixture
def uniform_icl_config():
    """Common ICL config for uniform distribution testing."""

    return ICLConfig(
        max_context_length=8192,
        reserved_tokens=4096,
        max_examples=3,
        min_examples=1,
        example_selection="uniform_distribution",
    )


@pytest.fixture
def job_config():
    """Fixture to provide a consistent job config for tests."""
    from src.config import DataSplitConfig, ICLConfig, settings

    # Create a data split config with smaller eval_size to match test expectations
    split_config = DataSplitConfig(
        eval_size=5, limit=100, min_total_records=10, val_ratio=0.1, parse_function_arguments=True
    )
    # Create an ICL config with max_examples that doesn't exceed eval_size
    icl_config = ICLConfig(max_examples=3, min_examples=1, example_selection="uniform_distribution")

    return settings.job_configs["llm_job"].model_copy(
        update={"data_split_config": split_config, "icl_config": icl_config}
    )


@pytest.fixture
def icl_selection_factory():
    """Factory fixture for creating ICL Selection instances with different configs."""

    def _create_icl_selection(config_type="uniform", workload_suffix=""):
        if config_type == "semantic":
            embedding_config = EmbeddingConfig(
                deployment_type="local",
                model_name="nvidia/llama-3.2-nv-embedqa-1b-v2",
                context_length=32768,
                gpus=1,
                pvc_size="25Gi",
                tag="1.9.0",
            )
            similarity_config = SimilarityConfig(embedding_nim_config=embedding_config)
            config = ICLConfig(
                example_selection="semantic_similarity", similarity_config=similarity_config
            )
        else:  # uniform
            config = ICLConfig(example_selection="uniform_distribution")

        with patch("src.lib.flywheel.icl_selection.get_es_client") as mock_es:
            mock_es.return_value = MagicMock()
            return ICLSelection(
                config=config,
                workload_id=f"test-{config_type}-workload{workload_suffix}",
                client_id=f"test-{config_type}-client",
            )

    return _create_icl_selection
