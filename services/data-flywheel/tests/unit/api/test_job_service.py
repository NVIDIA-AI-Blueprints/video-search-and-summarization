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
from fastapi import HTTPException

from src.api.job_service import (
    cancel_job,
    delete_job,
    get_job_details,
    validate_object_id,
    validate_object_id_list,
)
from src.api.schemas import (
    DeploymentStatus,
    FlywheelRunStatus,
    JobDeleteResponse,
    JobDetailResponse,
)

"""Unit tests for JobService.

These tests focus on the different code-paths involved when retrieving job details
and processing the various stages of the data flywheel workflow. The tests validate
the structure and content of the job responses at each stage, and verify handling
of various error conditions.

The main components and scenarios covered are:

Base Job Service functionality:
   - Successful retrieval of job details with complete data
   - Handling of non-existent job IDs
   - Proper status transitions (running, completed, failed)
   - Different job states (with/without datasets, NIMs, evaluations, customizations, LLM Judge)
   - Processing of partially completed jobs
   - Validation of object IDs (single and lists)

End-to-end workflow validation:
   - Proper state transitions from dataset creation through workflow completion
   - Consistency of job response structure throughout the workflow
   - Handling of asynchronous operations and status updates
"""

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def validate_job_response_structure(response, expected_id, expected_workload_id):
    """Validate common job response structure"""
    assert isinstance(response, JobDetailResponse)
    response_json = response.model_dump()

    assert response_json["id"] == expected_id
    assert response_json["workload_id"] == expected_workload_id
    assert "status" in response_json
    assert "num_records" in response_json
    assert "datasets" in response_json
    assert "nims" in response_json
    assert "llm_judge" in response_json

    return response_json


def validate_datasets(datasets, expected_count=0, expected_num_records=None):
    """Validate datasets section of job response"""
    assert len(datasets) == expected_count

    if expected_count > 0 and expected_num_records is not None:
        for dataset in datasets:
            assert "name" in dataset
            assert "num_records" in dataset
            assert "nmp_uri" in dataset
            assert dataset["num_records"] == expected_num_records


def validate_nims(nims, expected_count=0):
    """Validate NIMs section of job response"""
    assert len(nims) == expected_count

    if expected_count > 0:
        for nim in nims:
            assert "model_name" in nim
            assert "deployment_status" in nim
            assert "evaluations" in nim
            assert "customizations" in nim


def validate_nim_details(
    nims, nim_index, expected_model_name, expected_status, expected_eval_score=None
):
    """Validate specific NIM details"""
    assert nims[nim_index]["model_name"] == expected_model_name
    assert nims[nim_index]["deployment_status"] == expected_status

    if expected_eval_score is not None:
        assert nims[nim_index]["evaluations"][0]["scores"]["function_name"] == expected_eval_score


def validate_llm_judge(llm_judge, expected_status=None, expected_model_name=None):
    """Validate LLM Judge section of job response"""
    if llm_judge is None:
        return

    assert "model_name" in llm_judge
    assert "deployment_status" in llm_judge

    if expected_model_name:
        assert llm_judge["model_name"] == expected_model_name

    if expected_status:
        assert llm_judge["deployment_status"] == expected_status


# ---------------------------------------------------------------------------
# Job Service Tests
# ---------------------------------------------------------------------------


class TestJobService:
    def test_get_job_details_success(self, test_db_success, mock_db):
        """Test successful retrieval of job details"""
        with patch("src.api.job_service.get_db", return_value=mock_db):
            result = get_job_details(test_db_success["flywheel_run_id"])
            result_json = validate_job_response_structure(
                result, test_db_success["flywheel_run_id"], "test_workload"
            )

            # Validate specifics for successful job
            assert result_json["status"] == FlywheelRunStatus.PENDING
            assert result_json["num_records"] == 100

            # Validate datasets
            validate_datasets(result_json["datasets"], 4, 100)

            # Validate NIMs
            validate_nims(result_json["nims"], 2)
            validate_nim_details(
                result_json["nims"], 0, "test_model_1", DeploymentStatus.COMPLETED, 0.95
            )
            validate_nim_details(
                result_json["nims"], 1, "test_model_2", DeploymentStatus.PENDING, 0.85
            )

            # Validate NIM customizations
            assert result_json["nims"][0]["customizations"][0]["epochs_completed"] == 10
            assert result_json["nims"][1]["customizations"][0]["epochs_completed"] == 8

            # Validate LLM Judge
            validate_llm_judge(result_json["llm_judge"], DeploymentStatus.READY, "test-llm-judge")

    def test_get_job_details_not_found(self, mock_db):
        """Test handling of non-existent job ID"""
        with patch("src.api.job_service.get_db", return_value=mock_db):
            mock_db.flywheel_runs.find_one.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                get_job_details(str(ObjectId()))

            assert exc_info.value.status_code == 404
            assert exc_info.value.detail == "Job not found"

    @pytest.mark.parametrize(
        "test_scenario, update_data, expected_status",
        [
            (
                "completed",
                {"finished_at": datetime.utcnow(), "status": FlywheelRunStatus.COMPLETED},
                "completed",
            ),
            (
                "failed",
                {"error": "Job processing failed", "status": FlywheelRunStatus.FAILED},
                "failed",
            ),
        ],
    )
    def test_get_job_details_status(
        self, test_db, mock_db, test_scenario, update_data, expected_status
    ):
        """Test retrieval of job details with different statuses"""
        with patch("src.api.job_service.get_db", return_value=mock_db):
            # Update mock with scenario-specific data
            mock_run = mock_db.flywheel_runs.find_one.return_value.copy()
            for key, value in update_data.items():
                mock_run[key] = value
            mock_db.flywheel_runs.find_one.return_value = mock_run

            result = get_job_details(test_db["flywheel_run_id"])
            result_json = result.model_dump()

            assert result_json["status"] == expected_status

            # For completed jobs, verify finished_at
            if "finished_at" in update_data:
                assert result_json["finished_at"] is not None

            # For failed jobs, verify error message
            if "error" in update_data:
                assert result_json["error"] == update_data["error"]

    @pytest.mark.parametrize(
        "test_scenario, mock_update_func, validation_func",
        [
            (
                "empty_datasets",
                lambda mock_db: mock_db.flywheel_runs.find_one.return_value.update(
                    {"datasets": []}
                ),
                lambda result_json: validate_datasets(result_json["datasets"], 0),
            ),
            (
                "no_nims",
                lambda mock_db: setattr(mock_db.nims, "find", MagicMock(return_value=[])),
                lambda result_json: validate_nims(result_json["nims"], 0),
            ),
            (
                "no_evaluations",
                lambda mock_db: setattr(mock_db.evaluations, "find", MagicMock(return_value=[])),
                lambda result_json: all(
                    len(nim["evaluations"]) == 0 for nim in result_json["nims"]
                ),
            ),
            (
                "no_customizations",
                lambda mock_db: setattr(mock_db.customizations, "find", MagicMock(return_value=[])),
                lambda result_json: all(
                    len(nim["customizations"]) == 0 for nim in result_json["nims"]
                ),
            ),
            (
                "no_llm_judge",
                lambda mock_db: setattr(
                    mock_db.llm_judge_runs, "find_one", MagicMock(return_value=None)
                ),
                lambda result_json: result_json["llm_judge"] is None,
            ),
        ],
    )
    def test_get_job_details_missing_components(
        self, test_db, mock_db, test_scenario, mock_update_func, validation_func
    ):
        """Test retrieval of job details with missing components"""
        with patch("src.api.job_service.get_db", return_value=mock_db):
            # Update mock based on test scenario
            mock_update_func(mock_db)

            result = get_job_details(test_db["flywheel_run_id"])
            result_json = result.model_dump()

            # Validate base structure
            assert result_json["id"] == test_db["flywheel_run_id"]

            # Run scenario-specific validation
            validation_func(result_json)

    # ---------------------------------------------------------------------------
    # ObjectId Validation Tests
    # ---------------------------------------------------------------------------

    def test_validate_object_id_valid(self):
        """Test validation of a valid ObjectId string"""
        valid_id = str(ObjectId())
        result = validate_object_id(valid_id)
        assert isinstance(result, ObjectId)
        assert str(result) == valid_id

    @pytest.mark.parametrize(
        "test_id, param_name, expected_message",
        [
            ("invalid_id", None, "Invalid id format"),
            ("invalid_id", "job_id", "Invalid job_id format"),
            (None, None, "Invalid id format"),
            ("", None, "Invalid id format"),
        ],
    )
    def test_validate_object_id_invalid(self, test_id, param_name, expected_message):
        """Test validation with invalid ObjectId formats"""
        with pytest.raises(HTTPException) as exc_info:
            if param_name:
                validate_object_id(test_id, param_name=param_name)
            else:
                validate_object_id(test_id)

        assert exc_info.value.status_code == 400
        assert expected_message in str(exc_info.value.detail)

    def test_validate_object_id_list_valid(self):
        """Test validation of a list of valid ObjectIds"""
        # Create ObjectId objects directly, not string IDs
        valid_ids = [ObjectId() for _ in range(3)]
        result = validate_object_id_list(valid_ids)
        assert isinstance(result, list)
        assert all(isinstance(id_obj, ObjectId) for id_obj in result)
        assert len(result) == 3

    @pytest.mark.parametrize(
        "test_ids, expected_message",
        [
            ([ObjectId(), "invalid_id", ObjectId()], "Invalid ID in list"),
            ([], None),  # Empty list is valid, should not raise
            ("not_a_list", "Expected a list of IDs"),
            (None, "Expected a list of IDs"),
        ],
    )
    def test_validate_object_id_list_cases(self, test_ids, expected_message):
        """Test validation with various list cases"""
        if expected_message:
            with pytest.raises(HTTPException) as exc_info:
                validate_object_id_list(test_ids)
            assert exc_info.value.status_code == 400
            assert expected_message in str(exc_info.value.detail)
        else:
            # For valid cases (empty list)
            result = validate_object_id_list(test_ids)
            assert isinstance(result, list)
            assert len(result) == 0

    # ---------------------------------------------------------------------------
    # End-to-End Workflow Test
    # ---------------------------------------------------------------------------

    def test_end_to_end_workflow(
        self, mock_db, dataset_setup, mock_es_client, test_db, workflow_setup
    ):
        """Test end-to-end workflow from dataset creation to completion"""
        # Skip this test - too much mocking required for the HF Hub API and DataCreator
        # Creating a simplified version to just test the job service integration

        # Setup mocked data
        flywheel_run_id = test_db["flywheel_run_id"]
        nim1_id = ObjectId()
        nim2_id = ObjectId()
        llm_judge_id = ObjectId()

        # Direct testing of get_job_details with mocked data
        # 1. Simulate successful dataset creation
        mock_db.flywheel_runs.find_one.return_value = {
            "_id": ObjectId(flywheel_run_id),
            "workload_id": dataset_setup["workload_id"],
            "client_id": dataset_setup["client_id"],
            "started_at": datetime.utcnow(),
            "status": FlywheelRunStatus.RUNNING,
            "num_records": 100,
            "datasets": [
                {"name": "dataset_1", "num_records": 50, "nmp_uri": "uri_1"},
                {"name": "dataset_2", "num_records": 50, "nmp_uri": "uri_2"},
            ],
            "nims": [],
        }

        # Create test function to assert the job details at a given step
        def assert_job_details(
            expected_datasets_count, expected_nims_count, expected_status, expected_llm_judge=None
        ):
            with patch("src.api.job_service.get_db", return_value=mock_db):
                job_details = get_job_details(flywheel_run_id)
                job_json = job_details.model_dump()

                assert job_json["id"] == flywheel_run_id
                assert job_json["workload_id"] == "test_workload"
                assert job_json["status"] == expected_status
                validate_datasets(job_json["datasets"], expected_datasets_count)
                validate_nims(job_json["nims"], expected_nims_count)

                if expected_llm_judge is not None:
                    if expected_llm_judge == "none":
                        assert job_json["llm_judge"] is None
                    else:
                        assert job_json["llm_judge"] is not None
                        assert job_json["llm_judge"]["deployment_status"] == expected_llm_judge

                return job_json

        # 2. Check job details after dataset creation - LLM Judge is None
        mock_db.llm_judge_runs.find_one.return_value = None
        assert_job_details(2, 0, "running", "none")

        # 3. Simulate workflow initialization with NIMs and LLM Judge
        mock_db.nims.find.return_value = [
            {
                "_id": nim1_id,
                "model_name": "model_1",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "deployment_status": DeploymentStatus.PENDING,
                "runtime_seconds": 0.0,
            },
            {
                "_id": nim2_id,
                "model_name": "model_2",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "deployment_status": DeploymentStatus.PENDING,
                "runtime_seconds": 0.0,
            },
        ]

        mock_db.llm_judge_runs.find_one.return_value = {
            "_id": llm_judge_id,
            "flywheel_run_id": ObjectId(flywheel_run_id),
            "model_name": "llm_judge_model",
            "deployment_type": "remote",  # Adding the required deployment_type field
            "deployment_status": DeploymentStatus.PENDING,
        }

        # 4. Check job details after workflow initialization
        assert_job_details(2, 2, "running", DeploymentStatus.PENDING)

        # 5. Simulate successful deployment
        mock_db.nims.find.return_value = [
            {
                "_id": nim1_id,
                "model_name": "model_1",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "deployment_status": DeploymentStatus.COMPLETED,
                "runtime_seconds": 120.0,
            },
            {
                "_id": nim2_id,
                "model_name": "model_2",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "deployment_status": DeploymentStatus.COMPLETED,
                "runtime_seconds": 90.0,
            },
        ]

        mock_db.llm_judge_runs.find_one.return_value = {
            "_id": llm_judge_id,
            "flywheel_run_id": ObjectId(flywheel_run_id),
            "model_name": "llm_judge_model",
            "deployment_type": "remote",  # Adding the required deployment_type field
            "deployment_status": DeploymentStatus.READY,
        }

        # 6. Simulate evaluations and customizations
        mock_db.evaluations.find.return_value = [
            {
                "nim_id": nim1_id,
                "eval_type": "accuracy",
                "dataset": "test_dataset_1",
                "scores": {"accuracy": 0.95},
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
                "scores": {"accuracy": 0.92},
                "started_at": datetime.utcnow(),
                "finished_at": datetime.utcnow(),
                "runtime_seconds": 15.0,
                "progress": 100,
                "nmp_uri": "test_uri_eval_2",
            },
        ]

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
                "epochs_completed": 12,
                "steps_completed": 800,
                "nmp_uri": "test_uri_custom_2",
            },
        ]

        # 7. Simulate job completion
        mock_db.flywheel_runs.find_one.return_value.update(
            {"finished_at": datetime.utcnow(), "status": FlywheelRunStatus.COMPLETED}
        )

        # 8. Check final job state
        final_job = assert_job_details(2, 2, "completed", DeploymentStatus.READY)

        # 9. Verify specific NIM details in the final state
        for nim in final_job["nims"]:
            assert nim["deployment_status"] == DeploymentStatus.COMPLETED
            assert len(nim["evaluations"]) == 1
            assert len(nim["customizations"]) == 1

            if nim["model_name"] == "model_1":
                assert nim["evaluations"][0]["scores"]["accuracy"] == 0.95
                assert nim["customizations"][0]["epochs_completed"] == 10
            else:
                assert nim["evaluations"][0]["scores"]["accuracy"] == 0.92
                assert nim["customizations"][0]["epochs_completed"] == 12

    @patch("src.tasks.tasks.db_manager")
    def test_delete_job_success(self, mock_db_manager, test_db, mock_db):
        """Test successful deletion of a completed job"""
        # Create a fresh mock to avoid side effects from other tests
        fresh_mock_db = MagicMock()
        fresh_mock_db.flywheel_runs.find_one.return_value = {
            "_id": ObjectId(test_db["flywheel_run_id"]),
            "workload_id": "test_workload",
            "started_at": datetime.utcnow(),
            "finished_at": datetime.utcnow(),
        }

        with (
            patch("src.api.job_service.get_db", return_value=fresh_mock_db),
            patch("src.api.job_service.delete_job_resources.delay") as mock_task,
        ):
            # Test deletion
            result = delete_job(test_db["flywheel_run_id"])

            # Verify task was called with correct ID
            mock_task.assert_called_once_with(test_db["flywheel_run_id"])

            # Verify response
            assert isinstance(result, JobDeleteResponse)
            assert result.id == test_db["flywheel_run_id"]
            assert "Resources will be cleaned up in the background" in result.message

    @patch("src.tasks.tasks.db_manager")
    def test_delete_job_invalid_id(self, mock_db_manager, mock_db):
        """Test deletion with invalid job ID format"""
        with (
            patch("src.api.job_service.get_db", return_value=mock_db),
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_db.flywheel_runs.find_one.return_value = None
            delete_job("invalid-id")

        assert exc_info.value.status_code == 400
        assert "Invalid job_id format" in str(exc_info.value.detail)

    @patch("src.tasks.tasks.db_manager")
    def test_delete_job_not_found(self, mock_db_manager, mock_db):
        """Test deletion of non-existent job"""
        non_existent_id = str(ObjectId())
        with (
            patch("src.api.job_service.get_db", return_value=mock_db),
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_db.flywheel_runs.find_one.return_value = None
            delete_job(non_existent_id)

        assert exc_info.value.status_code == 404
        assert f"Job with ID {non_existent_id} not found" in str(exc_info.value.detail)

    @patch("src.tasks.tasks.db_manager")
    def test_delete_running_job(self, mock_db_manager, test_db, mock_db):
        """Test attempt to delete a running job"""
        # The job in test_db is running by default (no finished_at)
        with (
            patch("src.api.job_service.get_db", return_value=mock_db),
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_db.flywheel_runs.find_one.return_value = {
                "_id": ObjectId(test_db["flywheel_run_id"]),
                "workload_id": "test_workload",
                "started_at": datetime.utcnow(),
            }
            delete_job(test_db["flywheel_run_id"])

        assert exc_info.value.status_code == 400
        assert "Cannot delete a running job" in str(exc_info.value.detail)

    @patch("src.tasks.tasks.db_manager")
    def test_delete_job_task_failure(self, mock_db_manager, test_db, mock_db):
        """Test handling of task initiation failure"""
        # Create a fresh mock to avoid side effects from other tests
        fresh_mock_db = MagicMock()
        fresh_mock_db.flywheel_runs.find_one.return_value = {
            "_id": ObjectId(test_db["flywheel_run_id"]),
            "workload_id": "test_workload",
            "started_at": datetime.utcnow(),
            "finished_at": datetime.utcnow(),
        }

        with (
            patch("src.api.job_service.get_db", return_value=fresh_mock_db),
            patch(
                "src.api.job_service.delete_job_resources.delay",
                side_effect=Exception("Task failed"),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                delete_job(test_db["flywheel_run_id"])

            assert exc_info.value.status_code == 500
            assert f"Failed to initiate job deletion for {test_db['flywheel_run_id']}" in str(
                exc_info.value.detail
            )

    @patch("src.api.job_service.get_db_manager")
    def test_cancel_job_success(self, mock_get_db_manager, test_db):
        """Test successful cancellation of a running job"""
        mock_db_manager = mock_get_db_manager.return_value

        # Mock the db_manager.get_flywheel_run call
        mock_db_manager.get_flywheel_run.return_value = {
            "_id": ObjectId(test_db["flywheel_run_id"]),
            "workload_id": "test_workload",
            "started_at": datetime.utcnow(),
            "status": FlywheelRunStatus.RUNNING,
        }

        with patch("src.api.job_service.cancel_job_resources.delay") as mock_task:
            # Test cancellation
            result = cancel_job(test_db["flywheel_run_id"])

            # Verify task was called with correct ID
            mock_task.assert_called_once_with(test_db["flywheel_run_id"])

        # Verify db_manager was called with correct ID
        mock_db_manager.get_flywheel_run.assert_called_once_with(
            ObjectId(test_db["flywheel_run_id"])
        )

        # Verify response
        assert result.id == test_db["flywheel_run_id"]
        assert "successfully" in result.message.lower()

    def test_cancel_job_invalid_id(self):
        """Test cancellation with invalid job ID"""
        invalid_id = "invalid_id"

        with pytest.raises(HTTPException) as exc_info:
            cancel_job(invalid_id)

        assert exc_info.value.status_code == 400
        assert "Invalid job_id format" in str(exc_info.value.detail)

    @patch("src.api.job_service.get_db_manager")
    def test_cancel_job_not_found(self, mock_get_db_manager):
        """Test cancellation of non-existent job"""
        mock_db_manager = mock_get_db_manager.return_value
        non_existent_id = str(ObjectId())

        # Mock job not found
        mock_db_manager.get_flywheel_run.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            cancel_job(non_existent_id)

        assert exc_info.value.status_code == 404
        assert f"Job with ID {non_existent_id} not found" in str(exc_info.value.detail)

    @patch("src.api.job_service.get_db_manager")
    def test_cancel_finished_job(self, mock_get_db_manager, test_db):
        """Test attempt to cancel a job that has already finished"""
        mock_db_manager = mock_get_db_manager.return_value

        # Mock a finished job
        mock_db_manager.get_flywheel_run.return_value = {
            "_id": ObjectId(test_db["flywheel_run_id"]),
            "workload_id": "test_workload",
            "started_at": datetime.utcnow(),
            "finished_at": datetime.utcnow(),
            "status": FlywheelRunStatus.COMPLETED,
        }

        with pytest.raises(HTTPException) as exc_info:
            cancel_job(test_db["flywheel_run_id"])

        # Should raise 400 error for trying to cancel a finished job
        assert exc_info.value.status_code == 400
        assert "Cannot cancel a job that has already finished" in str(exc_info.value.detail)

    @patch("src.api.job_service.get_db_manager")
    def test_cancel_already_cancelled_job(self, mock_get_db_manager, test_db):
        """Test cancellation of a job that is already cancelled"""
        mock_db_manager = mock_get_db_manager.return_value

        # Mock an already cancelled job
        mock_db_manager.get_flywheel_run.return_value = {
            "_id": ObjectId(test_db["flywheel_run_id"]),
            "workload_id": "test_workload",
            "started_at": datetime.utcnow(),
            "status": FlywheelRunStatus.CANCELLED,
        }

        result = cancel_job(test_db["flywheel_run_id"])

        # Should return success but indicate it was already cancelled
        assert result.id == test_db["flywheel_run_id"]
        assert "already" in result.message.lower() and "cancelled" in result.message.lower()

    @patch("src.api.job_service.get_db_manager")
    def test_cancel_job_task_error(self, mock_get_db_manager, test_db):
        """Test handling of task error during cancellation"""
        mock_db_manager = mock_get_db_manager.return_value

        # Mock successful get_flywheel_run but failing task
        mock_db_manager.get_flywheel_run.return_value = {
            "_id": ObjectId(test_db["flywheel_run_id"]),
            "workload_id": "test_workload",
            "started_at": datetime.utcnow(),
            "status": FlywheelRunStatus.RUNNING,
        }

        with patch(
            "src.api.job_service.cancel_job_resources.delay", side_effect=Exception("Task error")
        ):
            with pytest.raises(HTTPException) as exc_info:
                cancel_job(test_db["flywheel_run_id"])

        assert exc_info.value.status_code == 500
        assert "Failed to cancel job" in str(exc_info.value.detail)
