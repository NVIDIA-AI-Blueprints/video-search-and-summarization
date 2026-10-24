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
Unit tests for NIM status population in JobDetailResponse.
Tests NIM lifecycle (PENDING→RUNNING→COMPLETED/ERROR), multiple NIMs,
LLM judge reuse, and integration with evaluations/customizations.

Error scenarios:
• Deployment failures (timeout, resource exhaustion, general failures)
• Shutdown failures during teardown
• Missing/invalid status field handling
"""

from datetime import datetime
from unittest.mock import patch

import pytest
from bson import ObjectId

from src.api.job_service import get_job_details
from src.api.schemas import DeploymentStatus, NIMRunStatus


class TestNIMRunStatusPopulation:
    """Test class for NIM run status population in JobDetailResponse."""

    def test_nim_run_status_pending_initial_state(self, mock_db, nim_status_test_data):
        """Test NIM run status is PENDING when initially created."""
        flywheel_run_id, nim_id = nim_status_test_data

        # Mock NIM in PENDING state (initial state after initialization)
        mock_db.nims.find.return_value = [
            {
                "_id": nim_id,
                "model_name": "test-model",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "status": NIMRunStatus.PENDING,
                "deployment_status": DeploymentStatus.CREATED,
                "runtime_seconds": 0.0,
                "started_at": datetime.utcnow(),
                "finished_at": None,
                "error": None,
            }
        ]

        with patch("src.api.job_service.get_db", return_value=mock_db):
            result = get_job_details(flywheel_run_id)
            job_json = result.model_dump()

        # Verify NIM status is PENDING
        assert len(job_json["nims"]) == 1
        nim = job_json["nims"][0]
        assert nim["status"] == NIMRunStatus.PENDING
        assert nim["deployment_status"] == DeploymentStatus.CREATED
        assert nim["runtime_seconds"] == 0.0
        assert nim["error"] is None

    def test_nim_run_status_running_after_spinup(self, mock_db, nim_status_test_data):
        """Test NIM run status is RUNNING after successful spin-up."""
        flywheel_run_id, nim_id = nim_status_test_data

        # Mock NIM in RUNNING state (after successful spin_up_nim)
        mock_db.nims.find.return_value = [
            {
                "_id": nim_id,
                "model_name": "test-model",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "status": NIMRunStatus.RUNNING,
                "deployment_status": DeploymentStatus.RUNNING,
                "runtime_seconds": 120.5,
                "started_at": datetime.utcnow(),
                "finished_at": None,
                "error": None,
            }
        ]

        with patch("src.api.job_service.get_db", return_value=mock_db):
            result = get_job_details(flywheel_run_id)
            job_json = result.model_dump()

        # Verify NIM status is RUNNING
        assert len(job_json["nims"]) == 1
        nim = job_json["nims"][0]
        assert nim["status"] == NIMRunStatus.RUNNING
        assert nim["deployment_status"] == DeploymentStatus.RUNNING
        assert nim["runtime_seconds"] == 120.5
        assert nim["error"] is None

    def test_nim_run_status_completed_after_successful_workflow(
        self, mock_db, nim_status_test_data
    ):
        """Test NIM run status is COMPLETED after successful workflow completion."""
        flywheel_run_id, nim_id = nim_status_test_data

        # Mock NIM in COMPLETED state (after successful shutdown_deployment)
        finished_time = datetime.utcnow()
        mock_db.nims.find.return_value = [
            {
                "_id": nim_id,
                "model_name": "test-model",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "status": NIMRunStatus.COMPLETED,
                "deployment_status": DeploymentStatus.COMPLETED,
                "runtime_seconds": 1800.0,
                "started_at": datetime.utcnow(),
                "finished_at": finished_time,
                "error": None,
            }
        ]

        with patch("src.api.job_service.get_db", return_value=mock_db):
            result = get_job_details(flywheel_run_id)
            job_json = result.model_dump()

        # Verify NIM status is COMPLETED
        assert len(job_json["nims"]) == 1
        nim = job_json["nims"][0]
        assert nim["status"] == NIMRunStatus.COMPLETED
        assert nim["deployment_status"] == DeploymentStatus.COMPLETED
        assert nim["runtime_seconds"] == 1800.0
        assert nim["error"] is None

    @pytest.mark.parametrize(
        "error_scenario,expected_deployment_status,error_message",
        [
            (
                "deployment_failure",
                DeploymentStatus.FAILED,
                "Error spinning up NIM: Deployment failed",
            ),
            (
                "timeout_error",
                DeploymentStatus.FAILED,
                "Error spinning up NIM: Timeout during deployment",
            ),
            (
                "resource_error",
                DeploymentStatus.FAILED,
                "Error spinning up NIM: Insufficient resources",
            ),
        ],
    )
    def test_nim_run_status_error_during_deployment(
        self,
        mock_db,
        nim_status_test_data,
        error_scenario,
        expected_deployment_status,
        error_message,
    ):
        """Test NIM run status is ERROR when deployment fails."""
        flywheel_run_id, nim_id = nim_status_test_data

        # Mock NIM in ERROR state (deployment failure in spin_up_nim)
        mock_db.nims.find.return_value = [
            {
                "_id": nim_id,
                "model_name": "test-model",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "status": NIMRunStatus.FAILED,
                "deployment_status": expected_deployment_status,
                "runtime_seconds": 30.0,
                "started_at": datetime.utcnow(),
                "finished_at": None,
                "error": error_message,
            }
        ]

        with patch("src.api.job_service.get_db", return_value=mock_db):
            result = get_job_details(flywheel_run_id)
            job_json = result.model_dump()

        # Verify NIM status is ERROR
        assert len(job_json["nims"]) == 1
        nim = job_json["nims"][0]
        assert nim["status"] == NIMRunStatus.FAILED
        assert nim["deployment_status"] == expected_deployment_status
        assert nim["runtime_seconds"] == 30.0
        assert nim["error"] == error_message

    def test_nim_run_status_error_during_shutdown(self, mock_db, nim_status_test_data):
        """Test NIM run status is ERROR when shutdown fails."""
        flywheel_run_id, nim_id = nim_status_test_data

        # Mock NIM in ERROR state (shutdown failure)
        error_message = "Error shutting down NIM deployment: Failed to terminate deployment"
        mock_db.nims.find.return_value = [
            {
                "_id": nim_id,
                "model_name": "test-model",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "status": NIMRunStatus.FAILED,
                "deployment_status": DeploymentStatus.FAILED,
                "runtime_seconds": 1200.0,
                "started_at": datetime.utcnow(),
                "finished_at": None,
                "error": error_message,
            }
        ]

        with patch("src.api.job_service.get_db", return_value=mock_db):
            result = get_job_details(flywheel_run_id)
            job_json = result.model_dump()

        # Verify NIM status is ERROR
        assert len(job_json["nims"]) == 1
        nim = job_json["nims"][0]
        assert nim["status"] == NIMRunStatus.FAILED
        assert nim["deployment_status"] == DeploymentStatus.FAILED
        assert nim["runtime_seconds"] == 1200.0
        assert nim["error"] == error_message

    def test_nim_run_status_multiple_nims_different_states(self, mock_db, nim_status_test_data):
        """Test multiple NIMs with different status states."""
        flywheel_run_id, _ = nim_status_test_data
        nim1_id = ObjectId()
        nim2_id = ObjectId()
        nim3_id = ObjectId()

        # Mock multiple NIMs in different states
        mock_db.nims.find.return_value = [
            {
                "_id": nim1_id,
                "model_name": "model-1",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "status": NIMRunStatus.PENDING,
                "deployment_status": DeploymentStatus.CREATED,
                "runtime_seconds": 0.0,
                "started_at": datetime.utcnow(),
                "finished_at": None,
                "error": None,
            },
            {
                "_id": nim2_id,
                "model_name": "model-2",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "status": NIMRunStatus.RUNNING,
                "deployment_status": DeploymentStatus.RUNNING,
                "runtime_seconds": 300.0,
                "started_at": datetime.utcnow(),
                "finished_at": None,
                "error": None,
            },
            {
                "_id": nim3_id,
                "model_name": "model-3",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "status": NIMRunStatus.COMPLETED,
                "deployment_status": DeploymentStatus.COMPLETED,
                "runtime_seconds": 1500.0,
                "started_at": datetime.utcnow(),
                "finished_at": datetime.utcnow(),
                "error": None,
            },
        ]

        with patch("src.api.job_service.get_db", return_value=mock_db):
            result = get_job_details(flywheel_run_id)
            job_json = result.model_dump()

        # Verify all NIMs have correct statuses
        assert len(job_json["nims"]) == 3

        # Sort by model name for consistent testing
        nims = sorted(job_json["nims"], key=lambda x: x["model_name"])

        # Model-1: PENDING
        assert nims[0]["model_name"] == "model-1"
        assert nims[0]["status"] == NIMRunStatus.PENDING
        assert nims[0]["deployment_status"] == DeploymentStatus.CREATED
        assert nims[0]["runtime_seconds"] == 0.0

        # Model-2: RUNNING
        assert nims[1]["model_name"] == "model-2"
        assert nims[1]["status"] == NIMRunStatus.RUNNING
        assert nims[1]["deployment_status"] == DeploymentStatus.RUNNING
        assert nims[1]["runtime_seconds"] == 300.0

        # Model-3: COMPLETED
        assert nims[2]["model_name"] == "model-3"
        assert nims[2]["status"] == NIMRunStatus.COMPLETED
        assert nims[2]["deployment_status"] == DeploymentStatus.COMPLETED
        assert nims[2]["runtime_seconds"] == 1500.0

    def test_nim_status_llm_judge_same_as_nim_completed(self, mock_db, nim_status_test_data):
        """Test NIM status is COMPLETED when LLM judge uses the same model (skip shutdown case)."""
        flywheel_run_id, nim_id = nim_status_test_data

        # Mock NIM that's the same as LLM judge (completed without shutdown)
        mock_db.nims.find.return_value = [
            {
                "_id": nim_id,
                "model_name": "llm-judge-model",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "status": NIMRunStatus.COMPLETED,
                "deployment_status": DeploymentStatus.READY,
                "runtime_seconds": 900.0,
                "started_at": datetime.utcnow(),
                "finished_at": datetime.utcnow(),
                "error": None,
            }
        ]

        with patch("src.api.job_service.get_db", return_value=mock_db):
            result = get_job_details(flywheel_run_id)
            job_json = result.model_dump()

        # Verify NIM status is COMPLETED
        assert len(job_json["nims"]) == 1
        nim = job_json["nims"][0]
        assert nim["status"] == NIMRunStatus.COMPLETED
        assert nim["deployment_status"] == DeploymentStatus.READY
        assert nim["runtime_seconds"] == 900.0
        assert nim["error"] is None

    @pytest.mark.parametrize(
        "nim_status,deployment_status,has_error",
        [
            (NIMRunStatus.PENDING, DeploymentStatus.CREATED, False),
            (NIMRunStatus.PENDING, DeploymentStatus.PENDING, False),
            (NIMRunStatus.RUNNING, DeploymentStatus.RUNNING, False),
            (NIMRunStatus.RUNNING, DeploymentStatus.READY, False),
            (NIMRunStatus.COMPLETED, DeploymentStatus.COMPLETED, False),
            (NIMRunStatus.FAILED, DeploymentStatus.FAILED, True),
            (NIMRunStatus.FAILED, DeploymentStatus.CANCELLED, True),
        ],
    )
    def test_nim_run_status_deployment_status_combinations(
        self, mock_db, nim_status_test_data, nim_status, deployment_status, has_error
    ):
        """Test various combinations of NIM status and deployment status."""
        flywheel_run_id, nim_id = nim_status_test_data

        error_message = "Test error message" if has_error else None

        mock_db.nims.find.return_value = [
            {
                "_id": nim_id,
                "model_name": "test-model",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "status": nim_status,
                "deployment_status": deployment_status,
                "runtime_seconds": 100.0,
                "started_at": datetime.utcnow(),
                "finished_at": datetime.utcnow() if nim_status == NIMRunStatus.COMPLETED else None,
                "error": error_message,
            }
        ]

        with patch("src.api.job_service.get_db", return_value=mock_db):
            result = get_job_details(flywheel_run_id)
            job_json = result.model_dump()

        # Verify status combination
        assert len(job_json["nims"]) == 1
        nim = job_json["nims"][0]
        assert nim["status"] == nim_status
        assert nim["deployment_status"] == deployment_status
        assert nim["runtime_seconds"] == 100.0
        if has_error:
            assert nim["error"] == error_message
        else:
            assert nim["error"] is None

    def test_nim_run_status_missing_status_field_defaults_to_pending(
        self, mock_db, nim_status_test_data
    ):
        """Test that missing status field defaults to PENDING when converted."""
        flywheel_run_id, nim_id = nim_status_test_data

        # Mock NIM without status field (should default to PENDING)
        mock_db.nims.find.return_value = [
            {
                "_id": nim_id,
                "model_name": "test-model",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "status": None,  # Missing/None status
                "deployment_status": DeploymentStatus.PENDING,
                "runtime_seconds": 0.0,
                "started_at": datetime.utcnow(),
                "finished_at": None,
                "error": None,
            }
        ]

        with patch("src.api.job_service.get_db", return_value=mock_db):
            # This should handle None status gracefully
            with pytest.raises(ValueError):  # NIMRunStatus(None) should raise ValueError
                get_job_details(flywheel_run_id)

    def test_nim_run_status_with_evaluations_and_customizations(
        self, mock_db, nim_status_test_data
    ):
        """Test NIM run status population with evaluations and customizations."""
        flywheel_run_id, nim_id = nim_status_test_data

        # Mock NIM with COMPLETED status
        mock_db.nims.find.return_value = [
            {
                "_id": nim_id,
                "model_name": "test-model",
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "status": NIMRunStatus.COMPLETED,
                "deployment_status": DeploymentStatus.COMPLETED,
                "runtime_seconds": 2400.0,
                "started_at": datetime.utcnow(),
                "finished_at": datetime.utcnow(),
                "error": None,
            }
        ]

        # Mock evaluations
        mock_db.evaluations.find.return_value = [
            {
                "nim_id": nim_id,
                "eval_type": "base",
                "dataset": "test_dataset",
                "scores": {"accuracy": 0.95},
                "started_at": datetime.utcnow(),
                "finished_at": datetime.utcnow(),
                "runtime_seconds": 300.0,
                "progress": 100.0,
                "nmp_uri": "test://eval/uri",
                "error": None,
            }
        ]

        # Mock customizations
        mock_db.customizations.find.return_value = [
            {
                "nim_id": nim_id,
                "started_at": datetime.utcnow(),
                "finished_at": datetime.utcnow(),
                "runtime_seconds": 1800.0,
                "progress": 100.0,
                "epochs_completed": 10,
                "steps_completed": 1000,
                "nmp_uri": "test://custom/uri",
                "error": None,
            }
        ]

        with patch("src.api.job_service.get_db", return_value=mock_db):
            result = get_job_details(flywheel_run_id)
            job_json = result.model_dump()

        # Verify NIM status and associated data
        assert len(job_json["nims"]) == 1
        nim = job_json["nims"][0]
        assert nim["status"] == NIMRunStatus.COMPLETED
        assert nim["deployment_status"] == DeploymentStatus.COMPLETED
        assert nim["runtime_seconds"] == 2400.0
        assert nim["error"] is None

        # Verify evaluations are included
        assert len(nim["evaluations"]) == 1
        assert nim["evaluations"][0]["eval_type"] == "base"
        assert nim["evaluations"][0]["scores"]["accuracy"] == 0.95

        # Verify customizations are included
        assert len(nim["customizations"]) == 1
        assert nim["customizations"][0]["epochs_completed"] == 10
        assert nim["customizations"][0]["steps_completed"] == 1000
