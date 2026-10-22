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

"""
LLM as Judge Integration Tests

Tests LLM as Judge workflows with real database dependencies:
- Remote LLM judge workflows (immediate return)
- Local LLM judge workflows (deployment and validation)
- Error handling and rollback scenarios
- Cleanup and resource management
- Only mocks external LLM judge APIs, keeps internal logic real
"""

import json
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from src.api.models import TaskResult
from src.config import LLMJudgeConfig, settings
from src.tasks.tasks import (
    cancel_job_resources,
    wait_for_llm_as_judge,
)


def dict_to_task_result(data: dict[str, Any]) -> TaskResult:
    """Convert dictionary back to TaskResult object for testing."""
    return TaskResult(**data)


def ensure_task_result(result) -> TaskResult:
    """Ensure result is a TaskResult, converting from dict if needed."""
    return dict_to_task_result(result) if isinstance(result, dict) else result


@pytest.fixture(autouse=True)
def setup_db_manager(mongo_db):
    """Setup database manager for LLM judge tests."""
    import src.tasks.tasks as tasks_module
    from src.api.db import init_db
    from src.api.db_manager import get_db_manager

    init_db()
    tasks_module.db_manager = get_db_manager()

    yield


def load_tool_calling_data():
    """Load first 35 records from aiva-test.jsonl for tool-calling tests."""
    try:
        with open("data/aiva-test.jsonl") as f:
            records = []
            for i, line in enumerate(f):
                if i >= 35:  # Load 35 records to get more unique records after deduplication
                    break
                records.append(json.loads(line.strip()))
            return records
    except FileNotFoundError:
        return []


@pytest.fixture(scope="module")
def load_llm_judge_test_data():
    """Load test data once for all LLM as Judge tests."""
    from src.lib.integration.es_client import ES_COLLECTION_NAME, get_es_client

    es_client = get_es_client()

    # Load tool-calling data once
    aiva_records = load_tool_calling_data()
    for i, aiva_record in enumerate(aiva_records):
        record = {
            "client_id": "test-client-tool_calling-llm-judge",
            "workload_id": "test-tool_calling-llm-judge",
            "request": aiva_record["request"],
            "response": aiva_record["response"],
            "timestamp": f"2023-01-01T{i // 60:02d}:{i % 60:02d}:00Z",
        }
        es_client.index(
            index=ES_COLLECTION_NAME,
            body=record,
            refresh=True,
        )

    # Load generic data once
    for i in range(20):
        record = {
            "client_id": "test-client-generic-llm-judge",
            "workload_id": "test-generic-llm-judge",
            "request": {
                "messages": [
                    {"role": "user", "content": f"Test question {i}?"},
                ]
            },
            "response": {
                "choices": [{"message": {"role": "assistant", "content": f"Test response {i}"}}]
            },
            "timestamp": f"2023-01-01T{i // 60:02d}:{i % 60:02d}:00Z",
        }
        es_client.index(
            index=ES_COLLECTION_NAME,
            body=record,
            refresh=True,
        )

    yield

    # Cleanup: Remove test data from Elasticsearch after all tests in module are completed
    try:
        es_client.delete_by_query(
            index=ES_COLLECTION_NAME,
            body={
                "query": {
                    "bool": {
                        "should": [
                            {
                                "bool": {
                                    "must": [
                                        {
                                            "match": {
                                                "client_id": "test-client-toolcalling-llm-judge"
                                            }
                                        },
                                        {"match": {"workload_id": "test-toolcalling-llm-judge"}},
                                    ]
                                }
                            },
                            {
                                "bool": {
                                    "must": [
                                        {"match": {"client_id": "test-client-generic-llm-judge"}},
                                        {"match": {"workload_id": "test-generic-llm-judge"}},
                                    ]
                                }
                            },
                        ]
                    }
                }
            },
            refresh=True,
        )
    except Exception as e:
        print(f"Warning: Failed to cleanup ES data: {e}")


def create_llm_judge_environment(
    mongo_db,
    llm_judge_type: str,
    workload_type: str = "generic",
    load_llm_judge_test_data=None,
):
    """Common function to create LLM judge environment with different configurations."""
    from src.tasks.tasks import create_datasets, initialize_workflow

    base_client_id = f"test-client-{workload_type}-llm-judge"
    base_workload_id = f"test-{workload_type}-llm-judge"

    # Setup comprehensive mocking for external services only
    with (
        patch("src.backends.nmp.data_uploader.DataUploader.upload_data") as mock_upload,
        patch("src.backends.nmp.data_uploader.DataUploader.get_file_uri") as mock_get_uri,
        patch("time.sleep") as mock_sleep,
        patch("src.backends.nmp.dms_client.NeMoPlatform") as mock_nemo_dms_ms,
        patch("src.backends.nmp.dms_client.DMSClient") as mock_dms,
        patch("src.backends.nmp.embedding.Embedding") as mock_embedding_class,
        patch("src.backends.nmp.provider.NmpBackendProvider.embedding") as mock_embedding_class_icl,
        patch("src.lib.integration.es_client.index_embeddings_to_es") as mock_index_embeddings,
        patch("src.lib.integration.es_client.search_similar_embeddings") as mock_search_embeddings,
        patch("src.lib.integration.es_client.delete_embeddings_index") as mock_delete_embeddings,
        patch("src.lib.flywheel.icl_selection.index_embeddings_to_es") as mock_index_embeddings_icl,
        patch(
            "src.lib.flywheel.icl_selection.search_similar_embeddings"
        ) as mock_search_embeddings_icl,
    ):
        # Setup DMS client mocking for local scenario
        mock_dms_instance = mock_dms.return_value
        mock_dms_instance.is_deployed.return_value = False
        mock_dms_instance.deploy_model.return_value = None
        mock_dms_instance.wait_for_deployment.return_value = None
        mock_dms_instance.wait_for_model_sync.return_value = None

        # Setup DMS client NeMo Platform mocking.
        # NMP 26.03 surfaces deployment APIs under ``client.inference``:
        #   - client.inference.deployment_configs.create
        #   - client.inference.deployments.{create,retrieve,delete}
        #   - client.inference.models.list
        # Status comes from ``deployment.status`` (uppercase string), translated
        # to legacy lowercase via _LEGACY_STATUS_MAP in src/lib/nemo/dms_client.py.
        mock_dms_client = mock_nemo_dms_ms.return_value
        mock_dms_deployments = mock_dms_client.inference.deployments
        mock_dms_deployment_configs = mock_dms_client.inference.deployment_configs
        mock_dms_inference_models = mock_dms_client.inference.models

        # Mock deployment_config creation (called before deploy)
        mock_dms_deployment_configs.create.return_value = MagicMock()

        # Mock deployment creation
        def mock_deployment_create(**kwargs):
            mock_deployment = MagicMock()
            mock_deployment.name = kwargs.get("name", f"{llm_judge_type}-deployment-123")
            mock_deployment.model_dump.return_value = {"id": f"{llm_judge_type}-deployment-123"}
            return mock_deployment

        mock_dms_deployments.create.side_effect = mock_deployment_create

        # Mock deployment retrieval — use NMP 26.03 uppercase status on the
        # top-level ``status`` attribute (not ``status_details.status``).
        def mock_deployment_retrieve(**kwargs):
            mock_deployment = MagicMock()
            mock_deployment.status = "READY"
            return mock_deployment

        mock_dms_deployments.retrieve.side_effect = mock_deployment_retrieve

        # Mock deployment deletion
        def mock_deployment_delete(**kwargs):
            mock_result = MagicMock()
            mock_result.model_dump.return_value = {"status": "deleted"}
            return mock_result

        mock_dms_deployments.delete.side_effect = mock_deployment_delete

        # Mock inference models list (used by is_model_available to short-circuit
        # wait_for_deployment when the model is already up).
        def mock_dms_models_list(**kwargs):
            mock_models_response = MagicMock()
            mock_model_1 = MagicMock()
            mock_model_1.id = f"{llm_judge_type}-test-judge"  # Use original vendor prefix
            mock_models_response.data = [mock_model_1]
            return mock_models_response

        mock_dms_inference_models.list.side_effect = mock_dms_models_list

        # Setup file upload mocking
        mock_upload.return_value = None
        mock_get_uri.return_value = f"test://dataset-uri-{base_workload_id}"

        # Setup embedding mocking
        def mock_get_embedding(queries, input_type="query"):
            """Mock get_embedding to return appropriate number of embeddings"""
            if isinstance(queries, list):
                # Return one embedding per query
                return [[0.1] * 2048 for _ in queries]  # Mock 2048-dim embeddings
            else:
                # Single query
                return [[0.1] * 2048]  # Single mock 2048-dim embedding

        mock_embedding_instance = MagicMock()
        mock_embedding_instance.get_embedding.side_effect = mock_get_embedding
        mock_embedding_instance.get_embeddings_batch.side_effect = mock_get_embedding
        mock_embedding_class.return_value = mock_embedding_instance
        mock_embedding_class_icl.return_value = mock_embedding_instance

        # Setup Elasticsearch embeddings mocking
        mock_index_embeddings.return_value = "test-embeddings-index"
        mock_index_embeddings_icl.return_value = "test-embeddings-index"
        # Mock search_similar_embeddings to return list of (score, tool_name, record) tuples
        mock_record = {
            "request": {"messages": [{"role": "user", "content": "Test question"}]},
            "response": {"choices": [{"message": {"content": "Test response"}}]},
        }
        mock_search_embeddings.return_value = [(0.9, "no_tool", mock_record)]
        mock_search_embeddings_icl.return_value = [(0.9, "no_tool", mock_record)]
        mock_delete_embeddings.return_value = None

        # Setup sleep mocking
        mock_sleep.return_value = None

        # Create flywheel run record
        flywheel_run_id = ObjectId()
        mongo_db.flywheel_runs.insert_one(
            {
                "_id": flywheel_run_id,
                "workload_id": base_workload_id,
                "client_id": base_client_id,
                "started_at": datetime.utcnow(),
                "status": "pending",
                "num_records": 0,
            }
        )

        # Run real initialize_workflow
        with (
            patch("src.tasks.tasks.settings") as mock_settings,
        ):
            from src.config import (
                DataSplitConfig,
                ICLConfig,
                LLMJobConfig,
                NIMConfig,
                TrainingConfig,
            )

            # Create a proper mock LLM job config
            mock_llm_job = LLMJobConfig(
                nims=[
                    NIMConfig(
                        model_name="nim/test-model",  # Use original vendor prefix
                        context_length=8192,
                        customization_enabled=False,
                    )
                ],
                data_split_config=DataSplitConfig(
                    eval_size=10,
                    limit=100,
                    min_total_records=10,
                    val_ratio=0.1,
                    parse_function_arguments=True,
                ),
                icl_config=ICLConfig(
                    max_examples=5,
                    min_examples=1,
                    example_selection="uniform_distribution",
                ),
                training_config=TrainingConfig(
                    max_epochs=3,
                    learning_rate=1e-4,
                    batch_size=1,
                ),
            )
            mock_settings.job_configs = {"llm_job": mock_llm_job}

            # Configure LLM judge based on type
            if llm_judge_type == "remote":
                llm_judge_config = LLMJudgeConfig(
                    deployment_type="remote",
                    model_name="test-judge",
                    context_length=8192,
                    url="http://test-judge-url/v1/chat/completions",
                    api_key="test-api-key",
                )
            else:  # local
                llm_judge_config = LLMJudgeConfig(
                    deployment_type="local",
                    model_name="local-test-judge",
                    context_length=8192,
                    url=None,
                    api_key=None,
                )

            settings.system_config.llm_judge_config = llm_judge_config

            from src.tasks.tasks import load_job_configuration

            job_config = load_job_configuration("llm_job", None)
            task_result = initialize_workflow(
                workload_id=base_workload_id,  # Use base ID for data lookup
                flywheel_run_id=str(flywheel_run_id),
                client_id=base_client_id,  # Use base ID for data lookup
                job_type="llm_job",
                job_config=job_config.model_dump(),  # Convert to dict for Celery
                data_split_config=job_config.data_split_config.model_dump(),
            )

        # Ensure task_result is TaskResult object
        task_result = ensure_task_result(task_result)

        # Adjust validation requirements for tool-calling tests
        if workload_type == "tool_calling":
            # Use custom split config with lower requirements for tool-calling data
            from src.config import DataSplitConfig

            custom_split_config = DataSplitConfig(
                eval_size=10,  # Lower eval size to fit within available records
                val_ratio=0.1,
                min_total_records=10,  # Lower minimum for tool-calling due to deduplication
                random_seed=42,  # Set specific seed for consistent behavior
                limit=1000,
                parse_function_arguments=True,
            )
            task_result.data_split_config = custom_split_config
        else:
            # Use custom split config for generic data as well
            from src.config import DataSplitConfig

            custom_split_config = DataSplitConfig(
                eval_size=10,  # Lower eval size to fit within available records
                val_ratio=0.1,
                min_total_records=20,  # Lower minimum
                random_seed=42,  # Set specific seed for consistent behavior
                limit=1000,
                parse_function_arguments=True,
            )
            task_result.data_split_config = custom_split_config

        # Run real create_datasets (RecordExporter will find the indexed data)
        task_result = ensure_task_result(create_datasets(task_result))

        # Return comprehensive environment info
        environment_info = {
            "flywheel_run_id": str(flywheel_run_id),
            "task_result": task_result,
            "test_records": [],  # Data is now pre-loaded, so no need to return it here
            "num_records": 0,  # Data is now pre-loaded, so no need to return it here
            "workload_type": workload_type,
            "workload_id": base_workload_id,
            "client_id": base_client_id,
            "llm_judge_type": llm_judge_type,
            "mocks": {
                "sleep": mock_sleep,
                "dms_client": mock_dms_client,
                "dms_deployments": mock_dms_deployments,
                "dms": mock_dms,
                "upload": mock_upload,
                "embedding": mock_embedding_instance,
            },
        }

        return environment_info


@pytest.fixture(params=["generic", "tool_calling"])
def workload_type(request):
    """Simple workload type parameterization."""
    return request.param


@pytest.fixture
def remote_llm_judge_environment(mongo_db, workload_type, load_llm_judge_test_data):
    """Setup remote LLM as Judge environment with parameterized workload types."""
    env_info = create_llm_judge_environment(
        mongo_db=mongo_db,
        llm_judge_type="remote",
        workload_type=workload_type,
        load_llm_judge_test_data=load_llm_judge_test_data,
    )

    yield env_info

    mongo_db.llm_judge_runs.delete_many({})
    mongo_db.flywheel_runs.delete_many({})


@pytest.fixture
def local_llm_judge_environment(mongo_db, workload_type, load_llm_judge_test_data):
    """Setup local LLM judge environment with proper DMS client mocking."""

    env_info = create_llm_judge_environment(
        mongo_db=mongo_db,
        llm_judge_type="local",
        workload_type=workload_type,
        load_llm_judge_test_data=load_llm_judge_test_data,
    )

    yield env_info

    mongo_db.llm_judge_runs.delete_many({})
    mongo_db.flywheel_runs.delete_many({})


@pytest.mark.integration
@pytest.mark.llmasjudge
class TestRemoteLLMAsJudgeWorkflows:
    """Test remote LLM as Judge workflows with real database dependencies."""

    def test_complete_remote_llm_as_judge_workflow(
        self,
        remote_llm_judge_environment,
        mongo_db,
    ):
        """Test complete remote LLM as Judge workflow with real data flow."""
        env_info = remote_llm_judge_environment
        task_result = env_info["task_result"]

        # Execute wait_for_llm_as_judge
        result = ensure_task_result(wait_for_llm_as_judge(task_result))
        assert result.error is None

        # Verify database state - LLM judge run should exist from initialize_workflow
        llm_judge_runs = list(
            mongo_db.llm_judge_runs.find({"flywheel_run_id": ObjectId(env_info["flywheel_run_id"])})
        )
        assert len(llm_judge_runs) == 1

        llm_judge_doc = llm_judge_runs[0]
        # For remote judges, status should be READY (set by initialize_workflow)
        assert llm_judge_doc["deployment_status"] == "ready"
        assert llm_judge_doc["model_name"] == "test-judge"
        assert llm_judge_doc["deployment_type"] == "remote"

    def test_remote_llm_as_judge_previous_error(
        self,
        remote_llm_judge_environment,
        mongo_db,
    ):
        """Test remote LLM as Judge skipping when previous task has error."""
        env_info = remote_llm_judge_environment
        task_result = env_info["task_result"]

        # Add previous error to task result
        task_result.error = "Previous task failed"

        # wait_for_llm_judge will not be skipped as it expects the previous task to raise an error
        result = ensure_task_result(wait_for_llm_as_judge(task_result))
        # Check that wait_for_model_sync was NOT called for remote judge
        dms_client = env_info["mocks"]["dms"].return_value
        assert (
            not dms_client.wait_for_model_sync.called
        ), "wait_for_model_sync should NOT be called when previous task errored"
        assert result.error == "Previous task failed"


@pytest.mark.integration
@pytest.mark.llmasjudge
class TestLocalLLMAsJudgeWorkflows:
    """Test local LLM as Judge workflows with real database dependencies."""

    def test_complete_local_llm_as_judge_workflow(
        self,
        local_llm_judge_environment,
        mongo_db,
    ):
        """Test complete local LLM as Judge workflow with real data flow."""
        env_info = local_llm_judge_environment
        task_result = env_info["task_result"]

        # Mock only what wait_for_llm_as_judge actually needs
        with (
            patch("src.backends.nmp.dms_client.NeMoPlatform") as mock_nemo_ms,
            patch("time.sleep") as mock_sleep,
        ):
            # NMP 26.03: use the inference.* surface; status is a top-level
            # uppercase string on the deployment object.
            mock_nemo_client = mock_nemo_ms.return_value

            # Mock inference.deployments.retrieve() for get_deployment_status()
            mock_deployment = MagicMock()
            mock_deployment.status = "READY"
            mock_nemo_client.inference.deployments.retrieve.return_value = mock_deployment
            mock_nemo_client.inference.deployment_configs.create.return_value = MagicMock()

            # Mock inference.models.list() for wait_for_model_sync()
            mock_models_response = MagicMock()
            mock_model = MagicMock()
            mock_model.id = "local-test-judge"  # Use original vendor prefix
            mock_models_response.data = [mock_model]
            mock_nemo_client.inference.models.list.return_value = mock_models_response

            # Mock sleep to speed up tests
            mock_sleep.return_value = None

            # Execute wait_for_llm_as_judge with minimal mocks
            result = ensure_task_result(wait_for_llm_as_judge(task_result))
            assert result.error is None

        # Verify database state - LLM judge run should exist from initialize_workflow
        llm_judge_runs = list(
            mongo_db.llm_judge_runs.find({"flywheel_run_id": ObjectId(env_info["flywheel_run_id"])})
        )
        assert len(llm_judge_runs) == 1

        llm_judge_doc = llm_judge_runs[0]
        # For local judges, status should be READY after deployment
        assert llm_judge_doc["deployment_status"] == "ready"
        assert llm_judge_doc["model_name"] == "local-test-judge"
        assert llm_judge_doc["deployment_type"] == "local"

    def test_local_llm_as_judge_deployment_failure(
        self,
        local_llm_judge_environment,
        mongo_db,
    ):
        """Test local LLM as Judge deployment failure handling."""
        env_info = local_llm_judge_environment
        task_result = env_info["task_result"]

        # Mock NeMo Platform client to fail deployment. NMP 26.03 paths:
        # client.inference.deployments + client.inference.deployment_configs.
        with patch("src.backends.nmp.dms_client.NeMoPlatform") as mock_nemo_dms_ms:
            mock_dms_client = mock_nemo_dms_ms.return_value
            mock_dms_deployments = mock_dms_client.inference.deployments
            # Force is_model_available -> False so wait_for_deployment proceeds
            # (otherwise it short-circuits before deploy_model is invoked).
            mock_dms_client.inference.models.list.return_value = MagicMock(data=[])
            mock_dms_client.inference.deployment_configs.create.return_value = MagicMock()
            mock_dms_deployments.create.side_effect = Exception("Deployment failed")
            mock_dms_deployments.retrieve.side_effect = Exception("Deployment failed")

            # Execute wait_for_llm_as_judge (should fail)
            with pytest.raises(Exception) as exc_info:
                ensure_task_result(wait_for_llm_as_judge(task_result))
            # Verify the error was properly handled
            assert "Deployment failed" in str(exc_info.value)


@pytest.mark.integration
@pytest.mark.llmasjudge
class TestLLMAsJudgeErrorHandling:
    """Test LLM as Judge error handling scenarios."""

    def test_llm_as_judge_cancellation_handling(
        self,
        remote_llm_judge_environment,
        mongo_db,
    ):
        """Test LLM as Judge cancellation handling."""
        env_info = remote_llm_judge_environment
        task_result = env_info["task_result"]

        # Cancel the flywheel run using the proper function
        cancel_job_resources(env_info["flywheel_run_id"])

        # Execute wait_for_llm_as_judge (should raise error due to cancellation)
        with pytest.raises(ValueError) as exc_info:
            ensure_task_result(wait_for_llm_as_judge(task_result))
        # Verify the error message contains cancellation info
        assert "was cancelled" in str(exc_info.value)

    def test_llm_as_judge_timeout_handling(
        self,
        local_llm_judge_environment,
        mongo_db,
    ):
        """Test LLM as Judge timeout handling."""
        env_info = local_llm_judge_environment
        task_result = env_info["task_result"]

        # Mock DMS client to timeout
        with (
            patch("src.backends.nmp.dms_client.DMSClient") as mock_dms,
            patch("src.backends.nmp.dms_client.NeMoPlatform") as mock_nemo_dms_ms,
        ):
            mock_dms_instance = mock_dms.return_value
            mock_dms_instance.is_deployed.return_value = False
            mock_dms_instance.deploy_model.return_value = None
            mock_dms_instance.wait_for_deployment.side_effect = Exception("Deployment timeout")

            # NMP 26.03 paths: client.inference.{deployments,deployment_configs,models}
            mock_dms_client = mock_nemo_dms_ms.return_value
            mock_dms_client.inference.models.list.return_value = MagicMock(data=[])
            mock_dms_client.inference.deployment_configs.create.return_value = MagicMock()
            mock_dms_deployments = mock_dms_client.inference.deployments
            mock_deployment = MagicMock()
            mock_deployment.name = "timeout-deployment-123"
            mock_deployment.model_dump.return_value = {"id": "timeout-deployment-123"}
            mock_dms_deployments.create.return_value = mock_deployment
            mock_dms_deployments.retrieve.side_effect = Exception("Deployment timeout")

            # Execute wait_for_llm_as_judge (should fail)
            with pytest.raises(Exception) as exc_info:
                ensure_task_result(wait_for_llm_as_judge(task_result))
            # Verify the error was properly handled
            assert "Deployment timeout" in str(exc_info.value)


@pytest.mark.integration
@pytest.mark.llmasjudge
class TestLLMAsJudgeCleanup:
    """Test LLM as Judge cleanup and resource management."""

    def test_llm_as_judge_cleanup_on_success(
        self,
        remote_llm_judge_environment,
        mongo_db,
    ):
        """Test proper cleanup after successful LLM as Judge execution."""
        env_info = remote_llm_judge_environment
        task_result = env_info["task_result"]

        # Execute complete workflow
        result = ensure_task_result(wait_for_llm_as_judge(task_result))

        # Verify all components completed successfully
        assert result.error is None

        # Verify database state is clean - LLM judge run should exist from initialize_workflow
        llm_judge_runs = list(
            mongo_db.llm_judge_runs.find({"flywheel_run_id": ObjectId(env_info["flywheel_run_id"])})
        )
        assert len(llm_judge_runs) == 1

        llm_judge_doc = llm_judge_runs[0]
        assert llm_judge_doc["deployment_status"] == "ready"
        assert llm_judge_doc["error"] is None

    def test_llm_as_judge_cleanup_on_failure(
        self,
        local_llm_judge_environment,
        mongo_db,
    ):
        """Test proper cleanup after LLM as Judge failure."""
        env_info = local_llm_judge_environment
        task_result = env_info["task_result"]

        # Mock DMS client to fail
        with (
            patch("src.backends.nmp.dms_client.DMSClient") as mock_dms,
            patch("src.backends.nmp.dms_client.NeMoPlatform") as mock_nemo_dms_ms,
        ):
            mock_dms_instance = mock_dms.return_value
            mock_dms_instance.is_deployed.return_value = False
            mock_dms_instance.deploy_model.side_effect = Exception("Deployment failed")
            mock_dms_instance.wait_for_deployment.side_effect = Exception("Deployment failed")

            # NMP 26.03 paths: client.inference.{deployments,deployment_configs,models}
            mock_dms_client = mock_nemo_dms_ms.return_value
            mock_dms_client.inference.models.list.return_value = MagicMock(data=[])
            mock_dms_client.inference.deployment_configs.create.return_value = MagicMock()
            mock_dms_deployments = mock_dms_client.inference.deployments
            mock_dms_deployments.create.side_effect = Exception("Deployment failed")
            mock_dms_deployments.retrieve.side_effect = Exception("Deployment failed")

            with pytest.raises(Exception) as exc_info:
                ensure_task_result(wait_for_llm_as_judge(task_result))
            # Verify error was properly handled
            assert "Deployment failed" in str(exc_info.value)

            # Verify database state reflects the failure
            llm_judge_runs = list(
                mongo_db.llm_judge_runs.find(
                    {"flywheel_run_id": ObjectId(env_info["flywheel_run_id"])}
                )
            )
            assert len(llm_judge_runs) == 1
            assert llm_judge_runs[0]["deployment_status"] == "failed"
            assert "Deployment failed" in llm_judge_runs[0]["error"]

    def test_llm_as_judge_cancellation_cleanup(
        self,
        local_llm_judge_environment,
        mongo_db,
    ):
        """Test cleanup when LLM as Judge is cancelled."""
        env_info = local_llm_judge_environment
        task_result = env_info["task_result"]

        # Cancel the flywheel run using the proper function
        cancel_job_resources(env_info["flywheel_run_id"])

        # Execute workflow with cancellation
        with pytest.raises(Exception) as exc_info:
            ensure_task_result(wait_for_llm_as_judge(task_result))
        # Verify cancellation was handled properly
        assert "Flywheel run" in str(exc_info.value) and "cancelled" in str(exc_info.value)
