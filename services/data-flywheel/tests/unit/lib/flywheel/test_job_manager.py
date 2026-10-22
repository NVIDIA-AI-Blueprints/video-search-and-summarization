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
from bson import ObjectId

from src.api.models import FlywheelRun
from src.lib.flywheel.job_manager import FlywheelJobManager


@pytest.fixture
def mock_db_manager():
    """Fixture to create a mock database manager."""
    mock = MagicMock()
    return mock


@pytest.fixture
def job_manager(mock_db_manager):
    """Fixture to create a FlywheelJobManager instance with mocked dependencies."""
    with (
        patch("src.backends.nmp.provider.NmpBackendProvider.training") as mock_customizer_class,
        patch("src.backends.nmp.provider.NmpBackendProvider.evaluator") as mock_evaluator_class,
    ):
        # Create and configure mock instances
        mock_customizer = MagicMock()
        mock_evaluator = MagicMock()

        # Set up the mock class to return our configured instances
        mock_customizer_class.return_value = mock_customizer
        mock_evaluator_class.return_value = mock_evaluator

        # Create the job manager
        manager = FlywheelJobManager(mock_db_manager)

        # Replace the auto-created instances with our mocks
        manager.customizer = mock_customizer
        manager.evaluator = mock_evaluator

        return manager


class TestFlywheelJobManager:
    """Test cases for the FlywheelJobManager class."""

    def test_init(self, mock_db_manager):
        """Test initialization of FlywheelJobManager."""
        with (
            patch("src.backends.nmp.provider.NmpBackendProvider.training") as mock_customizer_class,
            patch("src.backends.nmp.provider.NmpBackendProvider.evaluator") as mock_evaluator_class,
        ):
            manager = FlywheelJobManager(mock_db_manager)

            assert manager.db_manager is mock_db_manager
            assert manager.cleanup_errors == []
            mock_customizer_class.assert_called_once()
            mock_evaluator_class.assert_called_once()

    def test_cancel_job_success(self, job_manager, mock_db_manager):
        """Test successful cancellation of a job."""
        job_id = str(ObjectId())

        # Execute the method
        job_manager.cancel_job(job_id)

        # Verify database call
        mock_db_manager.mark_flywheel_run_cancelled.assert_called_once_with(
            ObjectId(job_id),
            error_msg="Job cancelled by user",
        )

    def test_cancel_job_invalid_id(self, job_manager):
        """Test cancellation with invalid job ID."""
        invalid_job_id = "invalid-id"

        # Execute and verify it raises an exception
        with pytest.raises(Exception) as exc_info:
            job_manager.cancel_job(invalid_job_id)

        assert "ObjectId" in str(exc_info.value)

    @patch("src.lib.flywheel.job_manager.MLflowClient")
    @patch("src.lib.flywheel.job_manager.settings")
    def test_cleanup_mlflow_experiments_enabled(
        self, mock_settings, mock_mlflow_client_class, job_manager
    ):
        """Test MLflow cleanup when MLflow is enabled."""
        job_id = "test-job-id"

        # Mock MLflow config as enabled
        mock_settings.system_config.mlflow_config.enabled = True
        mock_settings.system_config.mlflow_config.experiment_name_prefix = "flywheel"

        # Mock MLflowClient
        mock_mlflow = MagicMock()
        mock_mlflow_client_class.return_value = mock_mlflow

        # Execute the method
        job_manager._cleanup_mlflow_experiments(job_id)

        # Verify MLflowClient was created and cleanup was called
        mock_mlflow_client_class.assert_called_once_with(mock_settings.system_config.mlflow_config)
        mock_mlflow.cleanup_experiment.assert_called_once_with("flywheel-test-job-id")
        assert len(job_manager.cleanup_errors) == 0

    @patch("src.lib.flywheel.job_manager.MLflowClient")
    @patch("src.lib.flywheel.job_manager.settings")
    def test_cleanup_mlflow_experiments_error(
        self, mock_settings, mock_mlflow_client_class, job_manager
    ):
        """Test MLflow cleanup handles errors gracefully."""
        job_id = "test-job-id"

        # Mock MLflow config as enabled
        mock_settings.system_config.mlflow_config.enabled = True

        # Mock MLflowClient to raise an exception
        mock_mlflow = MagicMock()
        mock_mlflow.cleanup_experiment.side_effect = Exception("Cleanup failed")
        mock_mlflow_client_class.return_value = mock_mlflow

        # Execute the method - should not raise
        job_manager._cleanup_mlflow_experiments(job_id)

        # Verify error was logged
        assert len(job_manager.cleanup_errors) == 1
        assert "Failed to clean up MLflow experiment" in job_manager.cleanup_errors[0]

    def test_cancel_job_database_error(self, job_manager, mock_db_manager):
        """Test cancellation with database error."""
        job_id = str(ObjectId())
        mock_db_manager.mark_flywheel_run_cancelled.side_effect = Exception("Database error")

        # Execute and verify it raises the exception
        with pytest.raises(Exception) as exc_info:
            job_manager.cancel_job(job_id)

        assert "Database error" in str(exc_info.value)

    def test_delete_job_success(self, job_manager, mock_db_manager):
        """Test successful deletion of all job resources."""
        job_id = str(ObjectId())
        nim_id = ObjectId()

        # Mock flywheel run with datasets
        flywheel_run = FlywheelRun(
            workload_id="test-workload",
            client_id="test-client",
            started_at=datetime.utcnow(),
            datasets=[
                {"name": "test_dataset_1", "num_records": 100, "nmp_uri": "test_uri_1"},
                {"name": "test_dataset_2", "num_records": 100, "nmp_uri": "test_uri_2"},
            ],
        )

        # Configure mock DB responses
        mock_db_manager.get_flywheel_run.return_value = flywheel_run.to_mongo()
        mock_db_manager.find_nims_for_job.return_value = [
            {"_id": nim_id, "model_name": "test_model"}
        ]
        mock_db_manager.find_customizations_for_nim.return_value = [
            {"customized_model": "test_model_custom_1"},
            {"customized_model": "test_model_custom_2"},
        ]
        mock_db_manager.find_evaluations_for_nim.return_value = [
            {"job_id": "eval_job_1"},
            {"job_id": "eval_job_2"},
        ]

        # Patch DataUploader at the module level where it's used
        with patch(
            "src.backends.nmp.provider.NmpBackendProvider.datastore"
        ) as mock_data_uploader_class:
            mock_data_uploader = MagicMock()
            mock_data_uploader_class.return_value = mock_data_uploader

            # Execute cleanup
            job_manager.delete_job(job_id)

            # Verify DataUploader was called for each dataset
            assert mock_data_uploader_class.call_count == 2
            mock_data_uploader_class.assert_any_call(dataset_name="test_dataset_1")
            mock_data_uploader_class.assert_any_call(dataset_name="test_dataset_2")

            # Verify dataset deletion methods were called
            assert mock_data_uploader.delete_dataset.call_count == 2
            assert mock_data_uploader.unregister_dataset.call_count == 2

        # Verify customized models were deleted
        assert job_manager.customizer.delete_customized_model.call_count == 2
        job_manager.customizer.delete_customized_model.assert_any_call("test_model_custom_1")
        job_manager.customizer.delete_customized_model.assert_any_call("test_model_custom_2")

        # Verify evaluation jobs were deleted
        assert job_manager.evaluator.delete_evaluation_job.call_count == 2
        job_manager.evaluator.delete_evaluation_job.assert_any_call("eval_job_1")
        job_manager.evaluator.delete_evaluation_job.assert_any_call("eval_job_2")

        # Verify MongoDB cleanup
        mock_db_manager.delete_job_records.assert_called_once_with(ObjectId(job_id))

    def test_delete_job_partial_failure(self, job_manager, mock_db_manager):
        """Test deletion with some resources failing but overall task succeeding."""
        job_id = str(ObjectId())
        nim_id = ObjectId()

        # Mock flywheel run with datasets
        flywheel_run = FlywheelRun(
            workload_id="test-workload",
            client_id="test-client",
            started_at=datetime.utcnow(),
            datasets=[
                {"name": "test_dataset_1", "num_records": 100, "nmp_uri": "test_uri_1"},
            ],
        )

        # Configure mock DB responses
        mock_db_manager.get_flywheel_run.return_value = flywheel_run.to_mongo()
        mock_db_manager.find_nims_for_job.return_value = [
            {"_id": nim_id, "model_name": "test_model"}
        ]
        mock_db_manager.find_customizations_for_nim.return_value = [
            {"customized_model": "test_model_custom_1"},
        ]
        mock_db_manager.find_evaluations_for_nim.return_value = [
            {"job_id": "eval_job_1"},
        ]

        # Configure mock instance to fail
        job_manager.customizer.delete_customized_model.side_effect = Exception(
            "Failed to delete model"
        )

        # Patch DataUploader at the module level where it's used
        with patch(
            "src.backends.nmp.provider.NmpBackendProvider.datastore"
        ) as mock_data_uploader_class:
            mock_data_uploader = MagicMock()
            mock_data_uploader_class.return_value = mock_data_uploader

            # Execute cleanup
            job_manager.delete_job(job_id)

            # Verify DataUploader was called for the dataset
            mock_data_uploader_class.assert_called_once_with(dataset_name="test_dataset_1")
            mock_data_uploader.delete_dataset.assert_called_once()
            mock_data_uploader.unregister_dataset.assert_called_once()

        # Verify the task continued despite the model deletion failure
        job_manager.customizer.delete_customized_model.assert_called_once()
        job_manager.evaluator.delete_evaluation_job.assert_called_once()

        # Verify MongoDB cleanup still happened
        mock_db_manager.delete_job_records.assert_called_once_with(ObjectId(job_id))

        # Verify errors were logged
        assert len(job_manager.cleanup_errors) == 1
        assert "Failed to delete model" in job_manager.cleanup_errors[0]

    def test_delete_job_complete_failure(self, job_manager, mock_db_manager):
        """Test complete failure of job deletion."""
        job_id = str(ObjectId())

        # Mock database to raise an exception
        mock_db_manager.get_flywheel_run.side_effect = Exception("Database connection failed")

        # Execute cleanup and verify it raises the exception
        with pytest.raises(Exception) as exc_info:
            job_manager.delete_job(job_id)

        assert "Database connection failed" in str(exc_info.value)

        # Verify no further operations were attempted
        mock_db_manager.find_nims_for_job.assert_not_called()
        mock_db_manager.delete_job_records.assert_not_called()

    def test_delete_job_invalid_job_id(self, job_manager):
        """Test deletion with invalid job ID."""
        invalid_job_id = "invalid-id"

        # Execute cleanup and verify it raises an exception
        with pytest.raises(Exception) as exc_info:
            job_manager.delete_job(invalid_job_id)

        # Verify the error message
        assert "ObjectId" in str(exc_info.value), "Should raise error about invalid ObjectId format"

        # Verify no database operations were attempted
        job_manager.db_manager.get_flywheel_run.assert_not_called()
        job_manager.db_manager.find_nims_for_job.assert_not_called()
        job_manager.db_manager.delete_job_records.assert_not_called()

    def test_cleanup_nim_resources_with_customizations_without_model_name(
        self, job_manager, mock_db_manager
    ):
        """Test cleanup of NIM resources with customizations that don't have a customized_model field."""
        nim_id = ObjectId()

        # Mock customizations without customized_model field
        mock_db_manager.find_customizations_for_nim.return_value = [
            {"job_id": "custom-job-1"},  # Missing customized_model
            {"customized_model": "test_model_custom_1"},  # Has customized_model
        ]
        mock_db_manager.find_evaluations_for_nim.return_value = []

        # Execute the method
        job_manager._cleanup_nim_resources(nim_id)

        # Verify only one model deletion was attempted (for the one with customized_model)
        job_manager.customizer.delete_customized_model.assert_called_once_with(
            "test_model_custom_1"
        )

    def test_cleanup_nim_resources_with_evaluations_without_job_id(
        self, job_manager, mock_db_manager
    ):
        """Test cleanup of NIM resources with evaluations that don't have a job_id field."""
        nim_id = ObjectId()

        # Mock evaluations without job_id field
        mock_db_manager.find_customizations_for_nim.return_value = []
        mock_db_manager.find_evaluations_for_nim.return_value = [
            {"evaluation_id": "eval-1"},  # Missing job_id
            {"job_id": "eval_job_1"},  # Has job_id
        ]

        # Execute the method
        job_manager._cleanup_nim_resources(nim_id)

        # Verify only one evaluation deletion was attempted (for the one with job_id)
        job_manager.evaluator.delete_evaluation_job.assert_called_once_with("eval_job_1")

    def test_cleanup_nim_resources_all_failures(self, job_manager, mock_db_manager):
        """Test cleanup of NIM resources where all operations fail."""
        nim_id = ObjectId()

        # Mock DB responses
        mock_db_manager.find_customizations_for_nim.return_value = [
            {"customized_model": "test_model_1"},
            {"customized_model": "test_model_2"},
        ]
        mock_db_manager.find_evaluations_for_nim.return_value = [
            {"job_id": "eval_job_1"},
            {"job_id": "eval_job_2"},
        ]

        # Configure all operations to fail
        job_manager.customizer.delete_customized_model.side_effect = Exception(
            "Model deletion failed"
        )
        job_manager.evaluator.delete_evaluation_job.side_effect = Exception(
            "Evaluation deletion failed"
        )

        # Execute the method
        job_manager._cleanup_nim_resources(nim_id)

        # Verify all operations were attempted
        assert job_manager.customizer.delete_customized_model.call_count == 2
        assert job_manager.evaluator.delete_evaluation_job.call_count == 2

        # Verify all errors were recorded
        assert len(job_manager.cleanup_errors) == 4
        assert all("failed" in error.lower() for error in job_manager.cleanup_errors)

    def test_cleanup_datasets_success(self, job_manager):
        """Test successful cleanup of datasets."""
        # Create flywheel run with datasets
        flywheel_run = FlywheelRun(
            workload_id="test-workload",
            client_id="test-client",
            started_at=datetime.utcnow(),
            datasets=[
                {"name": "dataset_1", "num_records": 100, "nmp_uri": "uri_1"},
                {"name": "dataset_2", "num_records": 200, "nmp_uri": "uri_2"},
            ],
        )

        # Patch DataUploader
        with patch(
            "src.backends.nmp.provider.NmpBackendProvider.datastore"
        ) as mock_data_uploader_class:
            mock_data_uploader = MagicMock()
            mock_data_uploader_class.return_value = mock_data_uploader

            # Execute the method
            job_manager._cleanup_datasets(flywheel_run)

            # Verify DataUploader was called for each dataset
            assert mock_data_uploader_class.call_count == 2
            mock_data_uploader_class.assert_any_call(dataset_name="dataset_1")
            mock_data_uploader_class.assert_any_call(dataset_name="dataset_2")

            # Verify deletion methods were called
            assert mock_data_uploader.delete_dataset.call_count == 2
            assert mock_data_uploader.unregister_dataset.call_count == 2

        # Verify no errors were recorded
        assert len(job_manager.cleanup_errors) == 0

    def test_cleanup_datasets_with_failures(self, job_manager):
        """Test cleanup of datasets with some failures."""
        # Create flywheel run with datasets
        flywheel_run = FlywheelRun(
            workload_id="test-workload",
            client_id="test-client",
            started_at=datetime.utcnow(),
            datasets=[
                {"name": "dataset_1", "num_records": 100, "nmp_uri": "uri_1"},
                {"name": "dataset_2", "num_records": 200, "nmp_uri": "uri_2"},
            ],
        )

        # Patch DataUploader to fail for first dataset
        with patch(
            "src.backends.nmp.provider.NmpBackendProvider.datastore"
        ) as mock_data_uploader_class:

            def side_effect(dataset_name):
                mock_uploader = MagicMock()
                if dataset_name == "dataset_1":
                    mock_uploader.delete_dataset.side_effect = Exception("Delete failed")
                return mock_uploader

            mock_data_uploader_class.side_effect = side_effect

            # Execute the method
            job_manager._cleanup_datasets(flywheel_run)

            # Verify both datasets were attempted
            assert mock_data_uploader_class.call_count == 2

        # Verify one error was recorded
        assert len(job_manager.cleanup_errors) == 1
        assert "Failed to delete dataset dataset_1" in job_manager.cleanup_errors[0]

    def test_cleanup_datasets_empty_list(self, job_manager):
        """Test cleanup of datasets with empty dataset list."""
        # Create flywheel run without datasets
        flywheel_run = FlywheelRun(
            workload_id="test-workload",
            client_id="test-client",
            started_at=datetime.utcnow(),
            datasets=[],
        )

        # Patch DataUploader
        with patch(
            "src.backends.nmp.provider.NmpBackendProvider.datastore"
        ) as mock_data_uploader_class:
            # Execute the method
            job_manager._cleanup_datasets(flywheel_run)

            # Verify no DataUploader was created
            mock_data_uploader_class.assert_not_called()

        # Verify no errors were recorded
        assert len(job_manager.cleanup_errors) == 0
