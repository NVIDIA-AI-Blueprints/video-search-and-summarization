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
from unittest.mock import patch

import pytest
from bson import ObjectId

from conftest import validate_job_response_no_llm_judge, validate_job_response_success
from src.api.job_service import get_job_details
from src.api.models import TaskResult
from src.tasks.tasks import initialize_workflow

"""
Workflow Initialization (initialize_workflow):
   - Successful initialization of NIMs and LLM Judge
   - Validation of workflow components in job response
   - Parameter flow validation from initialize_workflow to create_datasets
   - Error scenarios:
     - Invalid NIM configuration
     - LLM Judge availability validation failures
     - NIM parsing errors
     - Database errors during initialization
     - Validation errors for parameters
"""


@pytest.fixture
def test_failure_db(mock_db):
    """Fixture to set up test database with sample data"""
    # Create test flywheel run
    flywheel_run_id = ObjectId()
    mock_db.flywheel_runs.insert_one.return_value = {"inserted_id": flywheel_run_id}

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
    mock_db.nims.find.return_value = []

    mock_db.nims.insert_many.return_value = {"inserted_ids": []}
    return {
        "flywheel_run_id": str(flywheel_run_id),
        "workload_id": "test_workload",
        "client_id": "test_client",
    }


class TestJobServiceInitializeWorkflow:
    """Test class for workflow initialization API functionality"""

    def _convert_result_to_task_result(self, result):
        """Helper method to convert result to TaskResult if it's a dictionary."""
        if isinstance(result, dict):
            return TaskResult(**result)
        return result

    def test_initialize_workflow_success(
        self, mock_db, workflow_params, test_db_success, job_config_setup, llm_judge_config_dict
    ):
        """Test successful workflow initialization and validate in job details"""
        with patch("src.api.job_service.get_db", return_value=mock_db):
            # Setup mock responses
            nim1_id = ObjectId()
            nim2_id = ObjectId()
            llm_judge_id = ObjectId()

            mock_db.nims.insert_many.return_value = {"inserted_ids": [nim1_id, nim2_id]}
            mock_db.llm_judge_runs.insert_one.return_value = {"inserted_id": llm_judge_id}

            result = initialize_workflow(
                workload_id=workflow_params["workload_id"],
                flywheel_run_id=workflow_params["flywheel_run_id"],
                client_id=workflow_params["client_id"],
                job_type="llm_job",
                job_config=job_config_setup,
                data_split_config=job_config_setup["data_split_config"],
            )

            # Convert to TaskResult if needed
            result = self._convert_result_to_task_result(result)

            # Verify initialize_workflow response
            assert isinstance(result, TaskResult)
            assert result.workload_id == workflow_params["workload_id"]
            assert result.flywheel_run_id == workflow_params["flywheel_run_id"]
            assert result.client_id == workflow_params["client_id"]
            assert result.error is None
            assert result.datasets == {}
            assert result.llm_judge_config is not None

            job_result = get_job_details(test_db_success["flywheel_run_id"])
            job_json = job_result.model_dump()

            # Use the validation helper
            validate_job_response_success(job_json)

    def test_initialize_workflow_nims_only(
        self,
        mock_db,
        workflow_params,
        test_db_no_llm_judge,
        job_config_setup,
        llm_judge_config_dict,
    ):
        """Test workflow initialization with NIMs only (no LLM Judge)"""
        with patch("src.api.job_service.get_db", return_value=mock_db):
            # Setup mock responses
            nim1_id = ObjectId()
            nim2_id = ObjectId()

            mock_db.nims.insert_many.return_value = {"inserted_ids": [nim1_id, nim2_id]}

            result = initialize_workflow(
                workload_id=workflow_params["workload_id"],
                flywheel_run_id=workflow_params["flywheel_run_id"],
                client_id=workflow_params["client_id"],
                job_type="llm_job",
                job_config=job_config_setup,
                data_split_config=job_config_setup["data_split_config"],
            )
            result = self._convert_result_to_task_result(result)
            assert isinstance(result, TaskResult)

            job_result = get_job_details(test_db_no_llm_judge["flywheel_run_id"])
            job_json = job_result.model_dump()

            # Use the validation helper
            validate_job_response_no_llm_judge(job_json)

    @pytest.mark.parametrize(
        "missing_param",
        [
            "workload_id",
            "flywheel_run_id",
            "client_id",
        ],
    )
    def test_initialize_workflow_missing_parameters(
        self, mock_db, workflow_params, missing_param, job_config_setup, llm_judge_config_dict
    ):
        """Test workflow initialization with missing parameters"""
        with patch("src.api.job_service.get_db", return_value=mock_db):
            # Remove the specified parameter
            params = workflow_params.copy()
            params[missing_param] = None

            result = initialize_workflow(
                workload_id=params["workload_id"],
                flywheel_run_id=params["flywheel_run_id"],
                client_id=params["client_id"],
                job_type="llm_job",
                job_config=job_config_setup,
                data_split_config=job_config_setup["data_split_config"],
            )

            result = self._convert_result_to_task_result(result)
            assert getattr(result, missing_param) is None

    @pytest.mark.parametrize(
        "param_name,param_value",
        [
            ("workload_id", ""),
            ("flywheel_run_id", ""),
            ("client_id", ""),
            ("workload_id", "valid-workload-123"),
            ("flywheel_run_id", str(ObjectId())),
            ("client_id", "valid-client-456"),
        ],
    )
    def test_initialize_workflow_parameter_validation(
        self,
        mock_db,
        workflow_params,
        param_name,
        param_value,
        job_config_setup,
        llm_judge_config_dict,
    ):
        """Test workflow initialization with various parameter values"""
        with patch("src.api.job_service.get_db", return_value=mock_db):
            mock_db.nims.insert_many.return_value = {"inserted_ids": [ObjectId()]}
            mock_db.llm_judge_runs.insert_one.return_value = {"inserted_id": ObjectId()}

            # Update the parameter
            params = workflow_params.copy()
            params[param_name] = param_value

            # Special case: flywheel_run_id with empty string should raise InvalidId
            if param_name == "flywheel_run_id" and param_value == "":
                from bson.errors import InvalidId

                with pytest.raises(InvalidId):
                    initialize_workflow(
                        workload_id=params["workload_id"],
                        flywheel_run_id=params["flywheel_run_id"],
                        client_id=params["client_id"],
                        job_type="llm_job",
                        job_config=job_config_setup,
                        data_split_config=job_config_setup["data_split_config"],
                    )
                return

            result = initialize_workflow(
                workload_id=params["workload_id"],
                flywheel_run_id=params["flywheel_run_id"],
                client_id=params["client_id"],
                job_type="llm_job",
                job_config=job_config_setup,
                data_split_config=job_config_setup["data_split_config"],
            )

            result = self._convert_result_to_task_result(result)
            assert isinstance(result, TaskResult)
            assert getattr(result, param_name) == param_value

    @pytest.mark.xfail(
        reason="This test is expected to fail because status is not updated to failed."
    )
    @pytest.mark.parametrize(
        "error_type,error_message",
        [
            (ValueError, "Invalid NIM configuration"),
            (Exception, "Database error"),
            (RuntimeError, "Unexpected error"),
        ],
    )
    def test_initialize_workflow_errors(
        self,
        mock_db,
        workflow_params,
        test_failure_db,
        task_db_manager_mock,
        mock_nim,
        job_config_setup,
        error_type,
        error_message,
        llm_judge_config_dict,
    ):
        """Test workflow initialization with various error conditions"""
        with patch("src.api.job_service.get_db", return_value=mock_db):
            # Mock TaskDBManager to raise the specified exception
            task_db_manager_mock.create_nim_run.side_effect = error_type(error_message)

            # Test the function directly
            with pytest.raises(error_type) as exc_info:
                initialize_workflow(
                    workload_id=workflow_params["workload_id"],
                    flywheel_run_id=workflow_params["flywheel_run_id"],
                    client_id=workflow_params["client_id"],
                    job_type="llm_job",
                    job_config=job_config_setup,
                    data_split_config=job_config_setup["data_split_config"],
                )

            # Verify the exception
            assert error_message in str(exc_info.value)

            job_result = get_job_details(test_failure_db["flywheel_run_id"])
            job_json = job_result.model_dump()

            # Use the validation helper
            assert len(job_json["nims"]) == 0
            assert job_json.get("llm_judge") is None
            assert job_json["status"] == "failed"

    @pytest.mark.xfail(reason="LLM Judge availability validation not implemented yet")
    def test_initialize_workflow_llm_judge_unavailable(
        self, mock_db, workflow_params, test_failure_db, job_config_setup, llm_judge_config_dict
    ):
        """Test workflow initialization when LLM Judge is unavailable"""
        with patch("src.api.job_service.get_db", return_value=mock_db):
            # Mock LLM Judge to be unavailable
            with patch("src.tasks.tasks.LLMAsJudge") as mock_llm_judge:
                mock_llm_judge.side_effect = Exception("LLM Judge unavailable")

                # Initialize workflow and expect an error
                with pytest.raises(Exception) as exc_info:
                    initialize_workflow(
                        workload_id=workflow_params["workload_id"],
                        flywheel_run_id=workflow_params["flywheel_run_id"],
                        client_id=workflow_params["client_id"],
                        job_type="llm_job",
                        job_config=job_config_setup,
                        data_split_config=job_config_setup["data_split_config"],
                    )

                assert "LLM Judge unavailable" in str(exc_info.value)

                job_result = get_job_details(test_failure_db["flywheel_run_id"])
                job_json = job_result.model_dump()

                # Validate that the job failed
                assert job_json["status"] == "failed"
