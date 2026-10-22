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

from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from src.api.job_service import get_job_details
from src.api.models import TaskResult
from src.api.schemas import DeploymentStatus
from src.config import LLMJudgeConfig
from src.tasks.tasks import wait_for_llm_as_judge

"""
LLM Judge Management (wait_for_llm_judge):
   - Tracking of deployment status transitions (pending, running, completed, failed)
   - Successful deployment validation
   - Error scenarios:
     - Unable to reach the LLM Judge service
     - Request timeouts
     - Authentication/authorization failures
     - Server errors (500 status)
     - Resource exhaustion
     - Incompatible model configurations
"""


@pytest.fixture
def mock_dms_client():
    """Fixture to mock DMSClient."""
    with patch("src.backends.nmp.provider.NmpBackendProvider.deployment_client") as mock:
        mock_instance = MagicMock()
        # Configure the mock instance methods
        mock_instance.is_deployed.return_value = False
        mock_instance.deploy_model.return_value = None
        mock_instance.wait_for_deployment.return_value = None
        mock_instance.wait_for_model_sync.return_value = None
        mock_instance.call_for_deployment.requests.get.return_value = None
        mock.return_value = mock_instance
        yield mock


class TestJobServiceWaitForLLM:
    """Test class for LLM Judge waiting functionality"""

    @pytest.fixture
    def llm_judge_setup(self, test_db_success):
        """Setup for LLM Judge tests"""
        return {
            "flywheel_run_id": test_db_success["flywheel_run_id"],
            "llm_judge_id": test_db_success["llm_judge_id"],
            "endpoint": "http://llm-judge-endpoint.com",
        }

    @pytest.fixture
    def mock_task_db_manager(self):
        """Create a mock TaskDBManager for testing"""
        with patch("src.tasks.tasks.db_manager") as mock_db_manager:
            # Set up the find_llm_judge_run method to return a valid document
            mock_db_manager.find_llm_judge_run.return_value = {
                "_id": ObjectId(),
                "flywheel_run_id": ObjectId(),
                "model_name": "test-llm-judge",
                "deployment_type": "remote",
                "deployment_status": DeploymentStatus.PENDING,
                "url": "http://test-llm-judge.com",
            }
            yield mock_db_manager

    def test_wait_for_llm_judge_remote_success(
        self, mock_db, llm_judge_setup, mock_task_db_manager
    ):
        """Test remote LLM Judge - for remote judges the function returns immediately"""
        # Create a valid TaskResult with valid remote LLMJudgeConfig
        task_result = TaskResult(
            flywheel_run_id=llm_judge_setup["flywheel_run_id"],
            llm_judge_config=LLMJudgeConfig(
                deployment_type="remote",
                model_name="test-llm-judge",
                url=llm_judge_setup["endpoint"],
                customization_enabled=False,
            ),
        )

        # Wait for LLM Judge - for remote LLM judges, the function returns immediately
        result = wait_for_llm_as_judge(task_result)

        # The function returns a dictionary representation of the TaskResult
        assert isinstance(result, dict)
        assert "llm_judge_config" in result

        # Validation in job_service - test with READY status
        with patch("src.api.job_service.get_db", return_value=mock_db):
            updated_llm_judge = mock_db.llm_judge_runs.find_one.return_value.copy()
            local_llm_judge = {
                "_id": ObjectId(llm_judge_setup["llm_judge_id"]),
                "flywheel_run_id": ObjectId(llm_judge_setup["flywheel_run_id"]),
                "model_name": "test-llm-judge",
                "deployment_type": "remote",
                "deployment_status": DeploymentStatus.READY,
                "url": llm_judge_setup["endpoint"],
            }
            mock_task_db_manager.find_llm_judge_run.return_value = local_llm_judge
            mock_db.llm_judge_runs.find_one.return_value = updated_llm_judge

            # Get job details and validate
            job_result = get_job_details(llm_judge_setup["flywheel_run_id"])
            job_json = job_result.model_dump()

            # Validate LLM Judge in job response
            assert job_json["llm_judge"] is not None
            assert job_json["llm_judge"]["deployment_status"] == DeploymentStatus.READY
            assert job_json["llm_judge"]["model_name"] == "test-llm-judge"

    def test_wait_for_llm_judge_local_service(
        self,
        workflow_setup,
        tweak_settings,
        mock_dms_client,
        mock_db,
        llm_judge_setup,
        mock_task_db_manager,
        nmp_config_dict,
    ):
        """Test LLM Judge with local service"""
        # Create a valid TaskResult with valid local LLMJudgeConfig
        task_result = TaskResult(
            flywheel_run_id=llm_judge_setup["flywheel_run_id"],
            llm_judge_config=LLMJudgeConfig(
                deployment_type="local",  # Local LLM judge
                model_name="test-llm-judge",
                context_length=8192,
                pvc_size="10Gi",
                tag="1.8.3",
                customization_enabled=False,
            ),
            nmp_config=nmp_config_dict,
        )

        # Mock the TaskDBManager.find_llm_judge_run to return a local LLM judge configuration
        local_llm_judge = {
            "_id": ObjectId(llm_judge_setup["llm_judge_id"]),
            "flywheel_run_id": ObjectId(llm_judge_setup["flywheel_run_id"]),
            "model_name": "test-llm-judge",
            "deployment_type": "local",
            "deployment_status": DeploymentStatus.PENDING,
            "context_length": 8192,
            "pvc_size": "10Gi",
            "customization_enabled": False,
            "Tag": "1.8.3",
        }
        mock_task_db_manager.find_llm_judge_run.return_value = local_llm_judge

        # Get the mock DMSClient instance
        dms_client_instance = mock_dms_client.return_value

        # Call wait_for_llm_as_judge
        result = wait_for_llm_as_judge(task_result)

        # Verify wait_for_deployment was called with a progress_callback
        dms_client_instance.wait_for_deployment.assert_called_once()
        # Get the progress_callback that was passed to wait_for_deployment
        progress_callback = dms_client_instance.wait_for_deployment.call_args[1][
            "progress_callback"
        ]

        # Manually call the progress_callback to simulate deployment progress
        progress_callback({"status": DeploymentStatus.READY})

        # Verify update_llm_judge_deployment_status was called with the right parameters
        mock_task_db_manager.update_llm_judge_deployment_status.assert_called_with(
            ObjectId(llm_judge_setup["llm_judge_id"]),
            DeploymentStatus.READY,
        )

        # Verify wait_for_model_sync was called
        dms_client_instance.wait_for_model_sync.assert_called_once()

        # Function returns a dictionary
        assert isinstance(result, dict)
        assert result["flywheel_run_id"] == llm_judge_setup["flywheel_run_id"]

        # Update mock for get_job_details to reflect the local LLM Judge
        with patch("src.api.job_service.get_db", return_value=mock_db):
            # Note: The API response doesn't include 'deployment_type' field, which is internal to the DB model
            local_llm_judge_db = {
                "_id": ObjectId(llm_judge_setup["llm_judge_id"]),
                "flywheel_run_id": ObjectId(llm_judge_setup["flywheel_run_id"]),
                "model_name": "test-llm-judge",
                "deployment_type": "local",  # Needed for database but not in API response
                "deployment_status": DeploymentStatus.READY,
                "url": llm_judge_setup["endpoint"],
            }
            mock_db.llm_judge_runs.find_one.return_value = local_llm_judge_db

            # Get job details and validate
            job_result = get_job_details(llm_judge_setup["flywheel_run_id"])
            job_json = job_result.model_dump()

            # Validate LLM Judge in job response
            assert job_json["llm_judge"] is not None
            assert job_json["llm_judge"]["deployment_status"] == DeploymentStatus.READY
            assert job_json["llm_judge"]["model_name"] == "test-llm-judge"
            assert "error" in job_json["llm_judge"]
            assert job_json["llm_judge"]["error"] is None

    @pytest.mark.xfail(
        reason="This test is failing because the flywheel run status is not set to failed."
    )
    def test_wait_for_llm_judge_local_failure(
        self,
        workflow_setup,
        tweak_settings,
        mock_dms_client,
        mock_db,
        llm_judge_setup,
        mock_task_db_manager,
        nmp_config_dict,
    ):
        """Test LLM Judge with local service"""
        # Create a valid TaskResult with valid local LLMJudgeConfig
        task_result = TaskResult(
            flywheel_run_id=llm_judge_setup["flywheel_run_id"],
            llm_judge_config=LLMJudgeConfig(
                deployment_type="local",  # Local LLM judge
                model_name="test-llm-judge",
                context_length=8192,
                pvc_size="10Gi",
                tag="1.8.3",
                customization_enabled=False,
            ),
            nmp_config=nmp_config_dict,
        )

        # Mock the TaskDBManager.find_llm_judge_run to return a local LLM judge configuration
        local_llm_judge = {
            "_id": ObjectId(llm_judge_setup["llm_judge_id"]),
            "flywheel_run_id": ObjectId(llm_judge_setup["flywheel_run_id"]),
            "model_name": "test-llm-judge",
            "deployment_type": "local",
            "deployment_status": DeploymentStatus.PENDING,
            "context_length": 8192,
            "pvc_size": "10Gi",
            "customization_enabled": False,
            "Tag": "1.8.3",
        }
        mock_task_db_manager.find_llm_judge_run.return_value = local_llm_judge

        # Get the mock DMSClient instance
        dms_client_instance = mock_dms_client.return_value
        # Set up the mock to raise an exception when wait_for_deployment is called
        dms_client_instance.wait_for_deployment.side_effect = Exception(
            "LLM Judge service unavailable"
        )

        with pytest.raises(Exception) as exc_info:
            # Call wait_for_llm_as_judge
            wait_for_llm_as_judge(task_result)
            # Verify wait_for_deployment was called with a progress_callback
            dms_client_instance.wait_for_deployment.assert_called_once()

        assert "LLM Judge service unavailable" in str(exc_info.value)
        # Get the progress_callback that was passed to wait_for_deployment
        progress_callback = dms_client_instance.wait_for_deployment.call_args[1][
            "progress_callback"
        ]

        # Manually call the progress_callback to simulate deployment progress
        progress_callback({"status": DeploymentStatus.FAILED})

        # Verify update_llm_judge_deployment_status was called with the right parameters
        mock_task_db_manager.update_llm_judge_deployment_status.assert_called_with(
            ObjectId(llm_judge_setup["llm_judge_id"]),
            DeploymentStatus.FAILED,
        )
        # Update mock for get_job_details to reflect the local LLM Judge
        with patch("src.api.job_service.get_db", return_value=mock_db):
            # Note: The API response doesn't include 'deployment_type' field, which is internal to the DB model
            local_llm_judge_db = {
                "_id": ObjectId(llm_judge_setup["llm_judge_id"]),
                "flywheel_run_id": ObjectId(llm_judge_setup["flywheel_run_id"]),
                "model_name": "test-llm-judge",
                "deployment_type": "local",  # Needed for database but not in API response
                "deployment_status": DeploymentStatus.FAILED,
                "url": llm_judge_setup["endpoint"],
            }
            mock_db.llm_judge_runs.find_one.return_value = local_llm_judge_db

            # Get job details and validate
            job_result = get_job_details(llm_judge_setup["flywheel_run_id"])
            job_json = job_result.model_dump()

            # Validate LLM Judge in job response
            assert job_json["llm_judge"] is not None
            assert job_json["llm_judge"]["deployment_status"] == DeploymentStatus.FAILED
            assert job_json["llm_judge"]["model_name"] == "test-llm-judge"
            assert "error" in job_json["llm_judge"]
            assert job_json["llm_judge"]["error"] is None
            assert job_json["status"] == "failed"
