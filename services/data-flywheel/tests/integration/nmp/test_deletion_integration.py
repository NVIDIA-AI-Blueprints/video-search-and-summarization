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
Consolidated Job Deletion Integration Tests

Tests job deletion workflows with real database dependencies:
- Complete job deletion workflow with resource cleanup
- Validation of deletion requirements (only completed jobs)
- Resource-specific cleanup verification
- Error handling and partial failure scenarios
- Only mocks external deletion APIs, keeps internal logic real
"""

import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from src.api.models import (
    EvalType,
    FlywheelRunStatus,
    NIMRunStatus,
)
from src.api.schemas import Dataset, DeploymentStatus
from src.lib.flywheel.job_manager import FlywheelJobManager
from src.tasks.tasks import delete_job_resources


@pytest.fixture
def captured_job_manager():
    """Fixture that captures the FlywheelJobManager instance created inside delete_job_resources.

    The patch is active for the entire test function duration, which is perfect for deletion tests
    since they focus exclusively on testing deletion behavior.

    Returns:
        dict: Container with 'job_manager' key set after delete_job_resources completes

    Usage:
        def test_deletion(self, deletion_environment, captured_job_manager):
            delete_job_resources(job_id)
            assert len(captured_job_manager["job_manager"].cleanup_errors) == expected_count
    """
    capture = {"job_manager": None}

    def capture_job_manager(*args, **kwargs):
        job_manager = FlywheelJobManager(*args, **kwargs)
        capture["job_manager"] = job_manager
        return job_manager

    with patch("src.tasks.tasks.FlywheelJobManager", side_effect=capture_job_manager):
        yield capture


@pytest.fixture(autouse=True)
def setup_deletion_db_manager(mongo_db):
    """Setup database manager for deletion tests."""
    import src.tasks.tasks as tasks_module
    from src.api.db import init_db
    from src.api.db_manager import get_db_manager

    init_db()
    tasks_module.db_manager = get_db_manager()

    yield


@pytest.fixture
def deletion_environment(mongo_db):
    """Setup deletion test environment with completed jobs and all resource types."""

    # Generate unique IDs
    workload_id = f"deletion-test-{uuid.uuid4()}"
    client_id = f"deletion-client-{uuid.uuid4()}"
    flywheel_run_id = ObjectId()

    # Create datasets
    datasets = [
        Dataset(name=f"dataset-train-{workload_id}", num_records=100),
        Dataset(name=f"dataset-eval-{workload_id}", num_records=50),
    ]

    # Create completed flywheel run
    flywheel_run_data = {
        "_id": flywheel_run_id,
        "workload_id": workload_id,
        "client_id": client_id,
        "started_at": datetime.utcnow(),
        "finished_at": datetime.utcnow(),  # Key: job is completed
        "status": FlywheelRunStatus.COMPLETED.value,
        "num_records": 100,
        "datasets": [dataset.model_dump() for dataset in datasets],
    }
    mongo_db.flywheel_runs.insert_one(flywheel_run_data)

    # Create NIMs with various resource types
    nim_ids = []
    for i in range(2):  # Create 2 NIMs
        nim_id = ObjectId()
        nim_ids.append(nim_id)

        nim_data = {
            "_id": nim_id,
            "flywheel_run_id": flywheel_run_id,
            "model_name": f"nim/test-model-{i}",  # Use original vendor prefix
            "started_at": datetime.utcnow(),
            "finished_at": datetime.utcnow(),
            "runtime_seconds": 300.0,
            "status": NIMRunStatus.COMPLETED.value,
            "deployment_status": DeploymentStatus.READY.value,
        }
        mongo_db.nims.insert_one(nim_data)

        # Create customizations for each NIM
        customization_data = {
            "_id": ObjectId(),
            "nim_id": nim_id,
            "flywheel_run_id": flywheel_run_id,
            "job_id": f"customization-job-{i}",
            "workload_id": workload_id,
            "base_model": f"nim/test-model-{i}",  # Use original vendor prefix
            "customized_model": f"customized-model-{i}",
            "started_at": datetime.utcnow(),
            "finished_at": datetime.utcnow(),
            "runtime_seconds": 600.0,
            "progress": 100.0,
            "epochs_completed": 3,
            "steps_completed": 150,
        }
        mongo_db.customizations.insert_one(customization_data)

        # Create evaluations for each NIM
        for eval_type in [EvalType.BASE, EvalType.CUSTOMIZED]:
            evaluation_data = {
                "_id": ObjectId(),
                "nim_id": nim_id,
                "flywheel_run_id": flywheel_run_id,
                "job_id": f"evaluation-job-{i}-{eval_type.value}",
                "eval_type": eval_type.value,
                "scores": {"accuracy": 0.85, "f1": 0.82},
                "started_at": datetime.utcnow(),
                "finished_at": datetime.utcnow(),
                "runtime_seconds": 120.0,
                "progress": 100.0,
            }
            mongo_db.evaluations.insert_one(evaluation_data)

    # Create LLM judge run
    llm_judge_data = {
        "_id": ObjectId(),
        "flywheel_run_id": flywheel_run_id,
        "model_name": "test-judge-model",
        "deployment_type": "remote",
        "deployment_status": DeploymentStatus.READY.value,
    }
    mongo_db.llm_judge_runs.insert_one(llm_judge_data)

    # Setup comprehensive mocking for external services
    #
    # NMP 26.03 SDK paths (see src/lib/nemo/*):
    # - Customizer: client.models.{retrieve,delete}, client.customization.jobs.cancel
    #   (NO targets, NO configs)
    # - Evaluator: client.evaluation.metric_jobs.delete
    # - DataUploader: client.files.filesets.delete  (datasets unified into filesets;
    #   delete_dataset() is now a no-op folded into unregister_dataset())
    with (
        patch("src.backends.nmp.customizer.NeMoPlatform") as mock_nemo_customizer_ms,
        patch("src.backends.nmp.evaluator.NeMoPlatform") as mock_nemo_evaluator_ms,
        patch("src.backends.nmp.data_uploader.NeMoPlatform") as mock_nemo_data_uploader_ms,
        patch("src.lib.integration.mlflow_client.MLflowClient") as mock_mlflow_client,
        patch("time.sleep") as mock_sleep,
    ):
        # Setup NeMo Platform client mocking for deletion operations

        # Mock customizer NeMo client
        mock_customizer_client = mock_nemo_customizer_ms.return_value
        mock_customizer_models = mock_customizer_client.models
        mock_customizer_jobs = mock_customizer_client.customization.jobs

        # Mock model retrieval and deletion (used by Customizer.delete_customized_model
        # which first calls get_customized_model_info -> client.models.retrieve, then
        # client.models.delete)
        def mock_model_retrieve(**kwargs):
            mock_model = MagicMock()
            mock_model.model_dump.return_value = {
                "id": "customized-model-1",
                "name": "customized-model-1",
            }
            return mock_model

        mock_customizer_models.retrieve.side_effect = mock_model_retrieve
        mock_customizer_models.delete.return_value = None
        mock_customizer_jobs.cancel.return_value = None

        # Mock evaluator NeMo client — Evaluator.delete_evaluation_job calls
        # client.evaluation.metric_jobs.delete in 26.03
        mock_evaluator_client = mock_nemo_evaluator_ms.return_value
        mock_evaluator_jobs = mock_evaluator_client.evaluation.metric_jobs
        mock_evaluator_jobs.delete.return_value = None

        # Mock data uploader NeMo client — DataUploader.unregister_dataset calls
        # client.files.filesets.delete in 26.03 (replaces legacy HTTP requests
        # against the Data Store endpoint)
        mock_data_uploader_client = mock_nemo_data_uploader_ms.return_value
        mock_data_uploader_filesets = mock_data_uploader_client.files.filesets
        mock_data_uploader_filesets.delete.return_value = None

        # Mock MLflow client
        mock_mlflow_instance = mock_mlflow_client.return_value
        mock_mlflow_instance.cleanup_experiment.return_value = None

        # Mock sleep to speed up tests
        mock_sleep.return_value = None

        # Return environment info
        environment_info = {
            "flywheel_run_id": str(flywheel_run_id),
            "nim_ids": [str(nim_id) for nim_id in nim_ids],
            "workload_id": workload_id,
            "client_id": client_id,
            "datasets": datasets,
            "mocks": {
                "customizer_client": mock_customizer_client,
                "customizer_models": mock_customizer_models,
                "customizer_jobs": mock_customizer_jobs,
                "evaluator_client": mock_evaluator_client,
                "evaluator_jobs": mock_evaluator_jobs,
                "data_uploader_client": mock_data_uploader_client,
                "data_uploader_filesets": mock_data_uploader_filesets,
                "mlflow_client": mock_mlflow_client,
                "sleep": mock_sleep,
            },
        }

        yield environment_info

    # Cleanup: Remove all test data
    mongo_db.evaluations.delete_many({"flywheel_run_id": flywheel_run_id})
    mongo_db.customizations.delete_many({"flywheel_run_id": flywheel_run_id})
    mongo_db.nims.delete_many({"flywheel_run_id": flywheel_run_id})
    mongo_db.llm_judge_runs.delete_many({"flywheel_run_id": flywheel_run_id})
    mongo_db.flywheel_runs.delete_many({"_id": flywheel_run_id})


@pytest.fixture
def running_job_environment(mongo_db):
    """Setup environment with a running (non-completed) job for validation tests."""

    workload_id = f"running-job-{uuid.uuid4()}"
    client_id = f"running-client-{uuid.uuid4()}"
    flywheel_run_id = ObjectId()

    # Create running flywheel run (no finished_at)
    flywheel_run_data = {
        "_id": flywheel_run_id,
        "workload_id": workload_id,
        "client_id": client_id,
        "started_at": datetime.utcnow(),
        "finished_at": None,  # Key: job is still running
        "status": FlywheelRunStatus.RUNNING.value,
        "num_records": 50,
        "datasets": [],
    }
    mongo_db.flywheel_runs.insert_one(flywheel_run_data)

    yield {
        "flywheel_run_id": str(flywheel_run_id),
        "workload_id": workload_id,
        "client_id": client_id,
    }

    # Cleanup
    mongo_db.flywheel_runs.delete_many({"_id": flywheel_run_id})


@pytest.mark.integration
@pytest.mark.deletion
class TestJobDeletionWorkflows:
    """Test complete job deletion workflows with real database dependencies."""

    def test_complete_job_deletion_success(self, deletion_environment, mongo_db):
        """Test complete successful deletion of a job with all resource types."""
        env_info = deletion_environment
        job_id = env_info["flywheel_run_id"]

        # Create job manager and execute deletion
        delete_job_resources(job_id)

        # Verify all external service calls were made
        mocks = env_info["mocks"]

        # Verify customized model deletions
        expected_model_calls = 2  # 2 NIMs with customizations
        assert mocks["customizer_models"].delete.call_count == expected_model_calls

        # Verify evaluation job deletions
        expected_eval_calls = 4  # 2 NIMs x 2 evaluation types
        assert mocks["evaluator_jobs"].delete.call_count == expected_eval_calls

        # Verify dataset deletions — in 26.03 unregister_dataset() goes through
        # client.files.filesets.delete; delete_dataset() is a no-op.
        expected_dataset_calls = 2  # 2 datasets unregistered via the NeMo Platform client
        assert mocks["data_uploader_filesets"].delete.call_count == expected_dataset_calls

        # Verify all database records are deleted
        flywheel_run_id = ObjectId(job_id)
        assert mongo_db.evaluations.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.customizations.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.nims.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.llm_judge_runs.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.flywheel_runs.count_documents({"_id": flywheel_run_id}) == 0


@pytest.mark.integration
@pytest.mark.deletion
class TestDeletionValidation:
    """Test deletion validation rules and requirements."""

    def test_cannot_delete_running_job(self, running_job_environment, mongo_db):
        """Test that running jobs cannot be deleted."""
        env_info = running_job_environment
        job_id = env_info["flywheel_run_id"]

        # Verify the job is running
        flywheel_run = mongo_db.flywheel_runs.find_one({"_id": ObjectId(job_id)})
        assert flywheel_run["finished_at"] is None
        assert flywheel_run["status"] == FlywheelRunStatus.RUNNING.value

        # API-level validation should prevent deletion
        from fastapi import HTTPException

        from src.api.job_service import delete_job

        with pytest.raises(HTTPException) as exc_info:
            delete_job(job_id)

        assert exc_info.value.status_code == 400
        assert "Cannot delete a running job" in str(exc_info.value.detail)

    def test_cannot_delete_nonexistent_job(self, mongo_db):
        """Test that deletion fails gracefully for non-existent jobs."""
        nonexistent_job_id = str(ObjectId())

        from fastapi import HTTPException

        from src.api.job_service import delete_job

        with pytest.raises(HTTPException) as exc_info:
            delete_job(nonexistent_job_id)

        assert exc_info.value.status_code == 404
        assert "not found" in str(exc_info.value.detail)

    def test_invalid_job_id_format(self, mongo_db):
        """Test that invalid job ID formats are rejected."""
        invalid_job_id = "invalid-job-id-format"

        from fastapi import HTTPException

        from src.api.job_service import delete_job

        with pytest.raises(HTTPException) as exc_info:
            delete_job(invalid_job_id)

        assert exc_info.value.status_code == 400
        assert "Invalid job_id format" in str(exc_info.value.detail)


@pytest.mark.integration
@pytest.mark.deletion
class TestDeletionResourceCleanup:
    """Test resource-specific cleanup verification."""

    def test_customized_model_cleanup(self, deletion_environment, mongo_db):
        """Test that customized models are properly deleted."""
        env_info = deletion_environment
        job_id = env_info["flywheel_run_id"]

        delete_job_resources(job_id)

        # Verify customizer API calls
        mocks = env_info["mocks"]
        customizer_models = mocks["customizer_models"]

        # Should call retrieve to verify model exists, then delete
        assert customizer_models.delete.call_count == 2  # Exactly 2 models

        # Verify delete calls have correct parameters (NMP 26.03 uses workspace+name)
        delete_calls = customizer_models.delete.call_args_list
        for call in delete_calls:
            assert "workspace" in call.kwargs and "name" in call.kwargs

    def test_evaluation_job_cleanup(self, deletion_environment, mongo_db):
        """Test that evaluation jobs are properly deleted."""
        env_info = deletion_environment
        job_id = env_info["flywheel_run_id"]

        delete_job_resources(job_id)

        # Verify evaluator API calls
        mocks = env_info["mocks"]
        evaluator_jobs = mocks["evaluator_jobs"]

        # Should delete all evaluation jobs
        assert evaluator_jobs.delete.call_count == 4  # 2 NIMs x 2 eval types

        # Verify delete calls have correct parameters
        # NMP 26.03: client.evaluation.metric_jobs.delete(name=..., workspace=...)
        delete_calls = evaluator_jobs.delete.call_args_list
        for call in delete_calls:
            assert "name" in call.kwargs and "workspace" in call.kwargs

    def test_dataset_cleanup(self, deletion_environment, mongo_db):
        """Test that datasets are properly deleted and unregistered.

        In NMP 26.03 datasets are unified into ``client.files.filesets``.
        ``delete_dataset()`` is folded into ``unregister_dataset()``, which calls
        ``client.files.filesets.delete`` once per dataset.
        """
        env_info = deletion_environment
        job_id = env_info["flywheel_run_id"]

        delete_job_resources(job_id)

        mocks = env_info["mocks"]
        data_uploader_filesets = mocks["data_uploader_filesets"]

        # One fileset delete call per dataset
        assert data_uploader_filesets.delete.call_count == 2

        # Verify each delete was called with a name kwarg (workspace + name in 26.03)
        for call in data_uploader_filesets.delete.call_args_list:
            assert "name" in call.kwargs

    def test_mlflow_experiment_cleanup(self, deletion_environment, mongo_db):
        """Test that MLflow experiments are properly cleaned up."""
        env_info = deletion_environment
        job_id = env_info["flywheel_run_id"]

        from src.api.db_manager import get_db_manager

        job_manager = FlywheelJobManager(get_db_manager())

        # Mock MLflow config directly on the imported settings object
        with (
            patch.object(job_manager, "_cleanup_mlflow_experiments") as mock_mlflow_cleanup,
        ):
            job_manager.delete_job(job_id)

        # Verify MLflow cleanup was called
        mock_mlflow_cleanup.assert_called_once_with(job_id)

    def test_database_records_cleanup(self, deletion_environment, mongo_db):
        """Test that all database records are properly deleted."""
        env_info = deletion_environment
        job_id = env_info["flywheel_run_id"]
        flywheel_run_id = ObjectId(job_id)

        # Verify pre-deletion state
        assert mongo_db.flywheel_runs.count_documents({"_id": flywheel_run_id}) == 1
        assert mongo_db.nims.count_documents({"flywheel_run_id": flywheel_run_id}) == 2
        assert mongo_db.customizations.count_documents({"flywheel_run_id": flywheel_run_id}) == 2
        assert mongo_db.evaluations.count_documents({"flywheel_run_id": flywheel_run_id}) == 4
        assert mongo_db.llm_judge_runs.count_documents({"flywheel_run_id": flywheel_run_id}) == 1

        delete_job_resources(job_id)

        # Verify all records deleted
        assert mongo_db.flywheel_runs.count_documents({"_id": flywheel_run_id}) == 0
        assert mongo_db.nims.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.customizations.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.evaluations.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.llm_judge_runs.count_documents({"flywheel_run_id": flywheel_run_id}) == 0


@pytest.mark.integration
@pytest.mark.deletion
class TestDeletionErrorHandling:
    """Test deletion error handling and partial failure scenarios."""

    def test_partial_deletion_with_model_failure(
        self, deletion_environment, mongo_db, captured_job_manager
    ):
        """Test deletion continues when customized model deletion fails."""
        env_info = deletion_environment
        job_id = env_info["flywheel_run_id"]

        # Mock model deletion failure
        mocks = env_info["mocks"]
        mocks["customizer_models"].retrieve.side_effect = lambda **kwargs: MagicMock(
            model_dump=lambda: {"id": "customized-model-1"}
        )
        mocks["customizer_models"].delete.side_effect = Exception("Internal server error")

        # Call delete_job_resources - the fixture captures the FlywheelJobManager automatically
        delete_job_resources(job_id)

        # Verify partial cleanup occurred
        assert (
            len(captured_job_manager["job_manager"].cleanup_errors) == 2
        )  # 2 failed model deletions
        assert all(
            "Failed to delete model" in error
            for error in captured_job_manager["job_manager"].cleanup_errors
        )

        # Other resources should still be cleaned up
        flywheel_run_id = ObjectId(job_id)
        assert mongo_db.evaluations.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.flywheel_runs.count_documents({"_id": flywheel_run_id}) == 0

    def test_partial_deletion_with_evaluation_failure(
        self, deletion_environment, mongo_db, captured_job_manager
    ):
        """Test deletion continues when evaluation job deletion fails."""
        env_info = deletion_environment
        job_id = env_info["flywheel_run_id"]

        # Mock evaluation deletion failure
        mocks = env_info["mocks"]
        mocks["evaluator_jobs"].delete.side_effect = Exception("Evaluation job not found")

        # Call delete_job_resources - the fixture captures the FlywheelJobManager automatically
        delete_job_resources(job_id)

        # Verify partial cleanup occurred
        assert (
            len(captured_job_manager["job_manager"].cleanup_errors) == 4
        )  # 4 failed evaluation deletions
        assert all(
            "Failed to delete evaluation job" in error
            for error in captured_job_manager["job_manager"].cleanup_errors
        )

        # Other resources should still be cleaned up
        flywheel_run_id = ObjectId(job_id)
        assert mongo_db.customizations.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.flywheel_runs.count_documents({"_id": flywheel_run_id}) == 0

    def test_partial_deletion_with_dataset_failure(
        self, deletion_environment, mongo_db, captured_job_manager
    ):
        """Test deletion continues when dataset deletion fails."""
        env_info = deletion_environment
        job_id = env_info["flywheel_run_id"]

        # In 26.03, dataset cleanup runs unregister_dataset -> filesets.delete.
        # Simulate the fileset delete raising for each dataset.
        mocks = env_info["mocks"]
        mocks["data_uploader_filesets"].delete.side_effect = Exception(
            "Fileset deletion access denied"
        )

        # Call delete_job_resources - the fixture captures the FlywheelJobManager automatically
        delete_job_resources(job_id)

        # One "Failed to delete dataset" error per dataset
        assert len(captured_job_manager["job_manager"].cleanup_errors) == 2
        assert all(
            "Failed to delete dataset" in error
            for error in captured_job_manager["job_manager"].cleanup_errors
        )

        # Other resources should still be cleaned up
        flywheel_run_id = ObjectId(job_id)
        assert mongo_db.nims.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.flywheel_runs.count_documents({"_id": flywheel_run_id}) == 0

    def test_multiple_cleanup_failures(self, deletion_environment, mongo_db, captured_job_manager):
        """Test deletion with multiple types of failures."""
        env_info = deletion_environment
        job_id = env_info["flywheel_run_id"]

        # Mock multiple failures
        mocks = env_info["mocks"]
        mocks["customizer_models"].retrieve.side_effect = lambda **kwargs: MagicMock(
            model_dump=lambda: {"id": "customized-model-1"}
        )
        mocks["customizer_models"].delete.side_effect = Exception("Model deletion failed")
        mocks["evaluator_jobs"].delete.side_effect = Exception("Evaluation not found")
        mocks["data_uploader_filesets"].delete.side_effect = Exception("Dataset access denied")

        # Call delete_job_resources - the fixture captures the FlywheelJobManager automatically
        delete_job_resources(job_id)

        # Verify all errors collected — one error per dataset since unregister
        # raises (delete_dataset is now a no-op).
        total_expected_errors = 2 + 4 + 2  # models + evaluations + datasets
        assert len(captured_job_manager["job_manager"].cleanup_errors) == total_expected_errors

        # Verify database cleanup still occurred
        flywheel_run_id = ObjectId(job_id)
        assert mongo_db.flywheel_runs.count_documents({"_id": flywheel_run_id}) == 0

    def test_database_consistency_on_errors(
        self, deletion_environment, mongo_db, captured_job_manager
    ):
        """Test that database remains consistent despite external service failures."""
        env_info = deletion_environment
        job_id = env_info["flywheel_run_id"]
        flywheel_run_id = ObjectId(job_id)

        # Mock all external services to fail
        mocks = env_info["mocks"]
        mocks["customizer_models"].delete.side_effect = Exception("Service unavailable")
        mocks["evaluator_jobs"].delete.side_effect = Exception("Service unavailable")
        mocks["data_uploader_filesets"].delete.side_effect = Exception("Service unavailable")

        # Mock MLflow to fail
        mocks["mlflow_client"].return_value.cleanup_experiment.side_effect = Exception(
            "MLflow error"
        )

        # Call delete_job_resources - the fixture captures the FlywheelJobManager automatically
        delete_job_resources(job_id)

        # All errors should be collected
        assert len(captured_job_manager["job_manager"].cleanup_errors) > 0

        # Despite all external failures, database should be cleaned up
        assert mongo_db.flywheel_runs.count_documents({"_id": flywheel_run_id}) == 0
        assert mongo_db.nims.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.customizations.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.evaluations.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
        assert mongo_db.llm_judge_runs.count_documents({"flywheel_run_id": flywheel_run_id}) == 0
