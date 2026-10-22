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

"""Tests for customization tasks."""

from datetime import datetime
from unittest.mock import ANY, patch

import pytest
from bson import ObjectId

from src.api.models import (
    DatasetType,
    EvaluationResult,
    TaskResult,
    WorkloadClassification,
)
from src.config import DataSplitConfig, MLflowConfig, NIMConfig, NMPConfig
from src.lib.flywheel.cancellation import FlywheelCancelledError
from src.tasks.tasks import start_customization
from tests.unit.tasks.conftest import (
    convert_result_to_task_result,
    get_default_job_config,
    get_default_mlflow_config,
    get_default_nmp_config,
)


def assert_task_results_equal(result: TaskResult, expected: TaskResult):
    """Compare two TaskResult objects, handling DataSplitConfig union type differences.

    Due to union type ordering (EmbeddingDataSplitConfig | DataSplitConfig),
    a DataSplitConfig may be deserialized as EmbeddingDataSplitConfig with default values.
    This is expected behavior and should not cause test failures.
    """
    # Compare all fields except data_split_config
    assert result.status == expected.status
    assert result.workload_id == expected.workload_id
    assert result.client_id == expected.client_id
    assert result.flywheel_run_id == expected.flywheel_run_id
    assert result.job_type == expected.job_type
    assert result.nim == expected.nim
    assert result.workload_type == expected.workload_type
    assert result.datasets == expected.datasets
    assert result.evaluations == expected.evaluations
    assert result.customization == expected.customization
    assert result.llm_judge_config == expected.llm_judge_config
    assert result.error == expected.error
    assert result.customized_nim == expected.customized_nim
    assert result.job_config == expected.job_config

    # For data_split_config, compare the base DataSplitConfig fields
    if result.data_split_config is None and expected.data_split_config is None:
        pass  # Both None, OK
    elif result.data_split_config is not None and expected.data_split_config is not None:
        # Compare base fields that exist on both DataSplitConfig and EmbeddingDataSplitConfig
        assert result.data_split_config.eval_size == expected.data_split_config.eval_size
        assert result.data_split_config.val_ratio == expected.data_split_config.val_ratio
        assert (
            result.data_split_config.min_total_records
            == expected.data_split_config.min_total_records
        )
        assert result.data_split_config.random_seed == expected.data_split_config.random_seed
        assert result.data_split_config.limit == expected.data_split_config.limit
        assert (
            result.data_split_config.parse_function_arguments
            == expected.data_split_config.parse_function_arguments
        )
    else:
        # One is None, the other isn't - this should fail
        assert result.data_split_config == expected.data_split_config


class TestCustomizationBasic:
    """Tests for basic customization functionality."""

    def test_start_customization(self, mock_task_db, valid_nim_config):
        """Test starting customization process."""
        nim_id = ObjectId()
        customization_id = ObjectId()
        flywheel_run_id = str(ObjectId())

        EvaluationResult(
            job_id="test-job",
            scores={"accuracy": 0.95},
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            status="completed",
        )

        previous_result = TaskResult(
            status="success",
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            evaluations={},
            customization=None,
            llm_judge_config=None,
            job_config=get_default_job_config([valid_nim_config]),
            data_split_config=None,
        )

        # Configure DB-helper
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }
        mock_task_db.insert_customization.return_value = customization_id

        # Mock the settings
        with (
            patch("src.backends.nmp.provider.NmpBackendProvider.training") as mock_customizer_class,
            patch("src.lib.flywheel.cancellation.get_db_manager") as mock_task_db_cancel,
        ):
            mock_customizer = mock_customizer_class.return_value
            mock_customizer.start_training_job.return_value = ("job-123", "customized-test-model")
            mock_customizer.get_job_uri.return_value = "http://test-uri"

            mock_task_db_cancel.return_value.is_flywheel_run_cancelled.return_value = False

            # Configure mock wait_for_customization to accept positional arguments
            mock_customizer.wait_for_customization.return_value = {"status": "completed"}

            start_customization(previous_result)

            # Verify Customizer calls
            mock_customizer.start_training_job.assert_called_once()
            call_kwargs = mock_customizer.start_training_job.call_args.kwargs
            # NMP 26.03 job name uses ``cust-{workload}-{model}-{run_suffix}`` (run_suffix
            # is the last 8 chars of flywheel_run_id, appended to avoid 409 conflicts on
            # re-runs and to stay within the 63-char ``^[a-z]...`` job-name limit).
            run_suffix = str(flywheel_run_id)[-8:]
            assert call_kwargs["name"] == f"cust-test-workload-test-model-{run_suffix}"
            assert call_kwargs["base_model"] == valid_nim_config.model_name
            assert isinstance(call_kwargs["output_model_name"], str)  # Random generated name
            assert len(call_kwargs["output_model_name"]) == 3  # Default length is 3
            assert call_kwargs["dataset_name"] == "test-train-dataset"
            assert call_kwargs["nim_config"] == valid_nim_config

            # Verify wait_for_customization was called with the correct arguments
            mock_customizer.wait_for_customization.assert_called_once()
            args, kwargs = mock_customizer.wait_for_customization.call_args
            assert args[0] == "job-123"  # First positional argument should be job_id
            assert kwargs["flywheel_run_id"] == flywheel_run_id
            assert kwargs["progress_callback"] is not None

            # wait_for_model_sync should be called for LoRA fine-tuning (default finetuning_type is lora)
            mock_customizer.wait_for_model_sync.assert_called_once_with(
                flywheel_run_id=flywheel_run_id,
                customized_model="customized-test-model",  # From mock return value
            )

            # Verify DB-helper interactions
            mock_task_db.find_nim_run.assert_called_once()
            mock_task_db.insert_customization.assert_called_once()

    def test_start_customization_failure(self, mock_task_db, valid_nim_config):
        """Test starting customization process when it fails."""
        nim_id = ObjectId()
        customization_id = ObjectId()

        # Create a mock job_config for the test

        previous_result = TaskResult(
            status="success",
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=str(ObjectId()),
            nim=valid_nim_config,
            workload_type=WorkloadClassification.GENERIC,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            evaluations={},
            customization=None,
            llm_judge_config=None,
            job_config=get_default_job_config([valid_nim_config]),
            data_split_config=None,
        )

        # Configure DB-helper
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }
        mock_task_db.insert_customization.return_value = customization_id

        # Mock the Customizer to fail
        with patch(
            "src.backends.nmp.provider.NmpBackendProvider.training"
        ) as mock_customizer_class:
            mock_customizer = mock_customizer_class.return_value
            mock_customizer.start_training_job.side_effect = Exception("Training job failed")

            start_customization(previous_result)

            # Verify error handling
            mock_task_db.update_customization.assert_called_with(
                ANY,
                {
                    "error": "Error starting customization: Training job failed",
                    "finished_at": ANY,
                    "progress": 0.0,
                },
            )

    def test_start_customization_progress_callback(self, mock_task_db, valid_nim_config):
        """Test start_customization progress callback functionality."""
        flywheel_run_id = str(ObjectId())
        nim_id = ObjectId()

        # Create a mock job_config for the test

        previous_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            job_config=get_default_job_config([valid_nim_config]),
            data_split_config=None,
        )

        # Configure DB manager
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }

        with (
            patch("src.backends.nmp.provider.NmpBackendProvider.training") as mock_customizer_class,
            patch("src.lib.flywheel.cancellation.get_db_manager") as mock_task_db_cancel,
        ):
            mock_customizer = mock_customizer_class.return_value
            mock_customizer.start_training_job.return_value = ("job-123", "customized-test-model")
            mock_customizer.get_job_uri.return_value = "http://test-uri"

            mock_task_db_cancel.return_value.is_flywheel_run_cancelled.return_value = False

            # Mock wait_for_customization to call progress callback
            def mock_wait_for_customization(job_id, flywheel_run_id, progress_callback=None):
                if progress_callback:
                    # Simulate progress updates
                    progress_callback({"progress": 0.25, "status": "training"})
                    progress_callback({"progress": 0.5, "status": "training"})
                    progress_callback({"progress": 1.0, "status": "completed"})
                return {"status": "completed"}

            mock_customizer.wait_for_customization.side_effect = mock_wait_for_customization

            start_customization(previous_result)

            # Verify progress callback was used and database was updated
            assert mock_task_db.update_customization.call_count >= 3  # At least 3 progress updates


class TestCustomizationConfiguration:
    """Tests for customization configuration and setup."""

    def test_start_customization_disabled(self, mock_task_db, valid_nim_config):
        """Test start_customization when customization is disabled."""
        flywheel_run_id = str(ObjectId())

        # Create NIM config with customization disabled
        nim_config_no_customization = NIMConfig(
            model_name="external-nim-model",
            context_length=2048,
            gpus=1,
            pvc_size="10Gi",
            tag="latest",
            registry_base="nvcr.io/nim",
            customization_enabled=False,  # This will cause customization to be skipped
        )

        previous_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=nim_config_no_customization,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            data_split_config=DataSplitConfig(limit=100),
            job_config=get_default_job_config([valid_nim_config]),
        )

        result = start_customization(previous_result)

        # Convert result to TaskResult if it's a dict
        result = convert_result_to_task_result(result)

        # Should return the same result without attempting customization
        assert_task_results_equal(result, previous_result)
        assert result.nim.customization_enabled is False

        # Verify no customization operations occurred
        mock_task_db.find_nim_run.assert_not_called()
        mock_task_db.insert_customization.assert_not_called()

    def test_start_customization_skip_customization_disabled(self, mock_task_db, valid_nim_config):
        """Test start_customization skips when customization_enabled is False."""
        flywheel_run_id = str(ObjectId())

        # Create NIM config with customization disabled
        nim_config_no_customization = NIMConfig(
            model_name="external-nim-model",
            context_length=2048,
            gpus=1,
            pvc_size="10Gi",
            tag="latest",
            registry_base="nvcr.io/nim",
            customization_enabled=False,  # This will cause customization to be skipped
        )

        previous_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=nim_config_no_customization,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            mlflow_config=get_default_mlflow_config(),
            data_split_config=DataSplitConfig(limit=100),
            job_config=get_default_job_config([valid_nim_config]),
        )

        result = start_customization(previous_result)

        # Convert result to TaskResult if it's a dict
        result = convert_result_to_task_result(result)

        # Should return the same result without attempting customization
        assert_task_results_equal(result, previous_result)
        assert result.nim.customization_enabled is False

        # Verify no customization operations occurred
        mock_task_db.find_nim_run.assert_not_called()
        mock_task_db.insert_customization.assert_not_called()


class TestCustomizationErrorHandling:
    """Tests for customization error handling scenarios."""

    def test_start_customization_missing_train_dataset(self, mock_task_db, valid_nim_config):
        """Test start_customization when training dataset is missing."""
        flywheel_run_id = str(ObjectId())
        nim_id = ObjectId()

        previous_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={},  # Missing training dataset,
            nmp_config=NMPConfig(**get_default_nmp_config()),
            mlflow_config=MLflowConfig(**get_default_mlflow_config()),
            job_config=get_default_job_config([valid_nim_config]),
        )

        # Configure DB manager - find_nim_run will be called before dataset check
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }

        result = start_customization(previous_result)

        # Convert result to TaskResult if it's a dict
        result = convert_result_to_task_result(result)

        # Should have error due to missing training dataset
        assert result.error is not None
        assert "DatasetType.TRAIN" in result.error

        # Verify find_nim_run was called (it's called before dataset check)
        mock_task_db.find_nim_run.assert_called_once_with(
            flywheel_run_id, valid_nim_config.model_name
        )

        # Verify customization was inserted but no training job was started
        mock_task_db.insert_customization.assert_called_once()

    def test_start_customization_training_job_failure(self, mock_task_db, valid_nim_config):
        """Test start_customization when training job fails to start."""
        flywheel_run_id = str(ObjectId())
        nim_id = ObjectId()

        # Create a mock job_config for the test

        previous_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            job_config=get_default_job_config([valid_nim_config]),
        )

        # Configure DB-helper
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }

        with (
            patch("src.backends.nmp.provider.NmpBackendProvider.training") as mock_customizer_class,
        ):
            mock_customizer = mock_customizer_class.return_value
            mock_customizer.start_training_job.side_effect = Exception(
                "Training job failed to start"
            )

            result = start_customization(previous_result)

            # Convert result to TaskResult if it's a dict
            result = convert_result_to_task_result(result)

            # Should have error due to training job failure
            assert result.error is not None
            assert "Error starting customization" in result.error
            assert "Training job failed to start" in result.error

            # Verify training job was attempted
            mock_customizer.start_training_job.assert_called_once()

    def test_start_customization_wait_failure(self, mock_task_db, valid_nim_config):
        """Test start_customization when wait_for_customization fails."""
        flywheel_run_id = str(ObjectId())
        nim_id = ObjectId()

        # Create a mock job_config for the test

        previous_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            job_config=get_default_job_config([valid_nim_config]),
        )

        # Configure DB-helper
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }

        with (
            patch("src.backends.nmp.provider.NmpBackendProvider.training") as mock_customizer_class,
        ):
            mock_customizer = mock_customizer_class.return_value
            mock_customizer.start_training_job.return_value = ("job-123", "customized-test-model")
            mock_customizer.get_job_uri.return_value = "http://test-uri"
            mock_customizer.wait_for_customization.side_effect = Exception("Wait failed")

            result = start_customization(previous_result)

            # Convert result to TaskResult if it's a dict
            result = convert_result_to_task_result(result)

            # Should have error due to wait failure
            assert result.error is not None
            assert "Error starting customization" in result.error
            assert "Wait failed" in result.error

            # Verify training job was started and wait was attempted
            mock_customizer.start_training_job.assert_called_once()
            mock_customizer.wait_for_customization.assert_called_once()

    def test_start_customization_nim_run_not_found(self, mock_task_db, valid_nim_config):
        """Test start_customization when NIM run is not found."""
        flywheel_run_id = str(ObjectId())

        previous_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            data_split_config=DataSplitConfig(limit=100),
            job_config=get_default_job_config([valid_nim_config]),
        )

        # Configure DB manager to return None (NIM run not found)
        mock_task_db.find_nim_run.return_value = None

        with pytest.raises(ValueError) as exc_info:
            start_customization(previous_result)

        # Verify the specific error message
        assert f"No NIM run found for model {valid_nim_config.model_name}" in str(exc_info.value)

        # Verify find_nim_run was called
        mock_task_db.find_nim_run.assert_called_once()

        # Verify no customization operations occurred
        mock_task_db.insert_customization.assert_not_called()

    def test_start_customization_cancel_job_exception(self, mock_task_db, valid_nim_config):
        """Test start_customization when cancel_job raises exception."""
        flywheel_run_id = str(ObjectId())
        nim_run_id = ObjectId()

        # Create a mock job_config for the test

        previous_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            job_config=get_default_job_config([valid_nim_config]),
        )

        # Configure DB manager
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_run_id,
            "model_name": valid_nim_config.model_name,
        }

        with (
            patch("src.backends.nmp.provider.NmpBackendProvider.training") as mock_customizer_class,
        ):
            mock_customizer = mock_customizer_class.return_value
            # Make start_training_job succeed first, then wait_for_customization fail
            mock_customizer.start_training_job.return_value = ("job-123", "customized-test-model")
            mock_customizer.get_job_uri.return_value = "http://test-uri"
            mock_customizer.wait_for_customization.side_effect = Exception(
                "Customization wait failed"
            )
            # Make cancel_job also fail
            mock_customizer.cancel_job.side_effect = Exception("Cancel job also failed")

            result = start_customization(previous_result)

            # Convert result to TaskResult if it's a dict
            result = convert_result_to_task_result(result)

            # Verify cancel_job was called and failed
            mock_customizer.cancel_job.assert_called_once_with("job-123")

            # Verify error message in result (original error, not cancel error)
            assert result.error is not None
            assert "Error starting customization" in result.error
            assert "Customization wait failed" in result.error

            # Verify customization document was updated with error
            mock_task_db.update_customization.assert_called_with(
                ANY,
                {
                    "error": ANY,
                    "finished_at": ANY,
                    "progress": 0.0,
                },
            )

    def test_start_customization_customizer_wait_for_model_sync_failure(
        self, mock_task_db, valid_nim_config
    ):
        """Test start_customization when wait_for_model_sync fails."""
        flywheel_run_id = str(ObjectId())
        nim_run_id = ObjectId()

        # Create a mock job_config for the test

        previous_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            job_config=get_default_job_config([valid_nim_config]),
        )

        # Configure DB manager
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_run_id,
            "model_name": valid_nim_config.model_name,
        }

        with (
            patch("src.backends.nmp.provider.NmpBackendProvider.training") as mock_customizer_class,
        ):
            mock_customizer = mock_customizer_class.return_value
            mock_customizer.start_training_job.return_value = ("job-123", "customized-test-model")
            mock_customizer.get_job_uri.return_value = "http://test-uri"

            # Configure wait_for_customization to succeed but wait_for_model_sync to fail
            mock_customizer.wait_for_customization.return_value = {"status": "completed"}
            mock_customizer.wait_for_model_sync.side_effect = Exception("Model sync failed")

            result = start_customization(previous_result)

            # Convert result to TaskResult if it's a dict
            result = convert_result_to_task_result(result)

            # Verify both wait functions were called
            mock_customizer.wait_for_customization.assert_called_once()
            mock_customizer.wait_for_model_sync.assert_called_once()

            # Verify error message in result
            assert result.error is not None
            assert "Error starting customization" in result.error
            assert "Model sync failed" in result.error

            # Verify customization document was updated with error
            mock_task_db.update_customization.assert_called_with(
                ANY,
                {
                    "error": ANY,
                    "finished_at": ANY,
                    "progress": 0.0,
                },
            )


class TestCustomizationCancellation:
    """Tests for customization cancellation scenarios."""

    def test_start_customization_cancellation(self, mock_task_db, valid_nim_config):
        """Test start_customization when job is cancelled."""
        flywheel_run_id = str(ObjectId())

        previous_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            data_split_config=DataSplitConfig(limit=100),
            job_config=get_default_job_config([valid_nim_config]),
        )

        with patch(
            "src.tasks.handlers.nmp.customization.CustomizationHandler.check_cancellation"
        ) as mock_check_cancellation:
            # Configure cancellation check to return True (cancelled)
            mock_check_cancellation.return_value = True

            result = start_customization(previous_result)

            # Convert result to TaskResult if it's a dict
            result = convert_result_to_task_result(result)

            # Verify cancellation was checked
            mock_check_cancellation.assert_called_once_with(flywheel_run_id, raise_error=False)

            # Verify error message in result
            assert result.error is not None
            assert "Task cancelled for flywheel run" in result.error

            # Verify no customization operations occurred
            mock_task_db.insert_customization.assert_not_called()

    def test_start_customization_cancellation_during_wait_for_customization(
        self, mock_task_db, valid_nim_config
    ):
        """Test start_customization when cancellation occurs during wait_for_customization."""
        flywheel_run_id = str(ObjectId())
        nim_run_id = ObjectId()

        # Create a mock job_config for the test

        previous_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            job_config=get_default_job_config([valid_nim_config]),
        )

        # Configure DB manager
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_run_id,
            "model_name": valid_nim_config.model_name,
        }

        with (
            patch("src.backends.nmp.provider.NmpBackendProvider.training") as mock_customizer_class,
        ):
            mock_customizer = mock_customizer_class.return_value
            mock_customizer.start_training_job.return_value = ("job-123", "customized-test-model")
            mock_customizer.get_job_uri.return_value = "http://test-uri"

            # Configure wait_for_customization to raise FlywheelCancelledError
            mock_customizer.wait_for_customization.side_effect = FlywheelCancelledError(
                flywheel_run_id, "Flywheel run was cancelled during customization wait"
            )

            result = start_customization(previous_result)

            # Convert result to TaskResult if it's a dict
            result = convert_result_to_task_result(result)

            # Verify training job was started
            mock_customizer.start_training_job.assert_called_once()
            mock_customizer.wait_for_customization.assert_called_once()

            # wait_for_model_sync should NOT be called since wait_for_customization failed
            mock_customizer.wait_for_model_sync.assert_not_called()

            # Verify error message in result
            assert result.error is not None
            assert "Error starting customization" in result.error
            assert "cancelled during customization wait" in result.error

            # Verify customization document was created and updated with error
            mock_task_db.insert_customization.assert_called_once()
            mock_task_db.update_customization.assert_called_with(
                ANY,
                {
                    "error": ANY,
                    "finished_at": ANY,
                    "progress": 0.0,
                },
            )

    def test_start_customization_cancellation_during_wait_for_model_sync(
        self, mock_task_db, valid_nim_config
    ):
        """Test start_customization when cancellation occurs during wait_for_model_sync."""
        flywheel_run_id = str(ObjectId())
        nim_run_id = ObjectId()

        # Create a mock job_config for the test

        previous_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            job_config=get_default_job_config([valid_nim_config]),
        )

        # Configure DB manager
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_run_id,
            "model_name": valid_nim_config.model_name,
        }

        with (
            patch("src.backends.nmp.provider.NmpBackendProvider.training") as mock_customizer_class,
        ):
            mock_customizer = mock_customizer_class.return_value
            mock_customizer.start_training_job.return_value = ("job-123", "customized-test-model")
            mock_customizer.get_job_uri.return_value = "http://test-uri"

            # Configure wait_for_customization to succeed but wait_for_model_sync to fail
            mock_customizer.wait_for_customization.return_value = {"status": "completed"}
            mock_customizer.wait_for_model_sync.side_effect = FlywheelCancelledError(
                flywheel_run_id, "Flywheel run was cancelled during model sync wait"
            )

            result = start_customization(previous_result)

            # Convert result to TaskResult if it's a dict
            result = convert_result_to_task_result(result)

            # Verify both wait functions were called
            mock_customizer.wait_for_customization.assert_called_once()
            mock_customizer.wait_for_model_sync.assert_called_once()

            # Verify error message in result
            assert result.error is not None
            assert "Error starting customization" in result.error
            assert "cancelled during model sync wait" in result.error

            # Verify customization document was updated with error
            mock_task_db.update_customization.assert_called_with(
                ANY,
                {
                    "error": ANY,
                    "finished_at": ANY,
                    "progress": 0.0,
                },
            )


class TestCustomizationSkipping:
    """Tests for customization skipping scenarios."""

    def test_start_customization_skip_due_to_previous_error(self, mock_task_db, valid_nim_config):
        """Test start_customization skips execution when previous task has error."""
        flywheel_run_id = str(ObjectId())

        # Create previous result with an error
        previous_result_with_error = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            error="Previous task failed with error",  # This will cause the stage to be skipped,
            data_split_config=DataSplitConfig(limit=100),
            job_config=get_default_job_config([valid_nim_config]),
        )

        result = start_customization(previous_result_with_error)

        # Convert result to TaskResult if it's a dict
        result = convert_result_to_task_result(result)

        # Should return the same result without attempting customization
        assert_task_results_equal(result, previous_result_with_error)
        assert result.error == "Previous task failed with error"

        # Verify no customization operations occurred
        mock_task_db.find_nim_run.assert_not_called()
        mock_task_db.insert_customization.assert_not_called()


class TestPrepareCustomizedNIM:
    """Tests for preparing customized NIM for deployment."""

    def test_prepare_customized_nim_success(self, mock_task_db, valid_nim_config):
        """Test successful preparation of customized NIM config."""
        from src.api.models import CustomizationResult
        from src.tasks.tasks import prepare_customized_nim

        flywheel_run_id = str(ObjectId())

        # Create customization result
        customization = CustomizationResult(
            job_id="job-123",
            model_name="customized-test-model",
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            percent_done=100.0,
        )

        previous_result = TaskResult(
            status="success",
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            evaluations={},
            customization=customization,
            llm_judge_config=None,
            job_config=get_default_job_config([valid_nim_config]),
        )

        result = prepare_customized_nim(previous_result)

        # Convert result to TaskResult if it's a dict
        result = convert_result_to_task_result(result)

        # Verify customized NIM config was created
        assert result.customized_nim is not None
        assert result.customized_nim["model_name"] == "customized-test-model"
        assert result.customized_nim["base_model_for_image"] == valid_nim_config.model_name
        assert result.customized_nim["customization_enabled"] is False

        # No error should be present
        assert result.error is None

    def test_prepare_customized_nim_skip_no_customization(self, mock_task_db, valid_nim_config):
        """Test preparation skips when no customization available."""
        from src.tasks.tasks import prepare_customized_nim

        flywheel_run_id = str(ObjectId())

        previous_result = TaskResult(
            status="success",
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            evaluations={},
            customization=None,  # No customization
            llm_judge_config=None,
            job_config=get_default_job_config([valid_nim_config]),
        )

        result = prepare_customized_nim(previous_result)

        # Convert result to TaskResult if it's a dict
        result = convert_result_to_task_result(result)

        # Should skip preparation
        assert result.customized_nim is None
        assert result.error is None

    def test_prepare_customized_nim_skip_no_model_name(self, mock_task_db, valid_nim_config):
        """Test preparation skips when customization has no model name."""
        from src.api.models import CustomizationResult
        from src.tasks.tasks import prepare_customized_nim

        flywheel_run_id = str(ObjectId())

        # Customization without model name
        customization = CustomizationResult(
            job_id="job-123",
            model_name=None,  # No model name yet
            started_at=datetime.utcnow(),
            finished_at=None,
            percent_done=50.0,
        )

        previous_result = TaskResult(
            status="success",
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            evaluations={},
            customization=customization,
            llm_judge_config=None,
            job_config=get_default_job_config([valid_nim_config]),
        )

        result = prepare_customized_nim(previous_result)

        # Convert result to TaskResult if it's a dict
        result = convert_result_to_task_result(result)

        # Should skip preparation
        assert result.customized_nim is None
        assert result.error is None

    def test_prepare_customized_nim_skip_previous_error(self, mock_task_db, valid_nim_config):
        """Test preparation skips when previous task has error."""
        from src.tasks.tasks import prepare_customized_nim

        flywheel_run_id = str(ObjectId())

        previous_result = TaskResult(
            status="error",
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=flywheel_run_id,
            nim=valid_nim_config,
            datasets={DatasetType.TRAIN: "test-train-dataset"},
            evaluations={},
            customization=None,
            llm_judge_config=None,
            job_config=get_default_job_config([valid_nim_config]),
            error="Previous task failed",
        )

        result = prepare_customized_nim(previous_result)

        # Convert result to TaskResult if it's a dict
        result = convert_result_to_task_result(result)

        # Should skip preparation
        assert result.customized_nim is None
        assert result.error == "Previous task failed"
