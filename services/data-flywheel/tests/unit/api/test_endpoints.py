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

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException

from src.api.endpoints import (
    cancel_job_endpoint,
    create_job,
    delete_job_endpoint,
    get_job,
    get_jobs,
)
from src.api.schemas import (
    FlywheelRunStatus,
    JobCancelResponse,
    JobDeleteResponse,
    JobDetailResponse,
    JobRequest,
    JobResponse,
    JobsListResponse,
)
from src.config import DataSplitConfig, EmbeddingDataSplitConfig


class TestCreateJob:
    """Test cases for the create_job endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.endpoints.run_nim_workflow_dag")
    @patch("src.api.endpoints.get_db")
    async def test_create_job_success(self, mock_get_db, mock_workflow_dag):
        """Test successful job creation."""
        # Setup mocks
        mock_db = Mock()
        mock_get_db.return_value = mock_db

        job_id = ObjectId()
        mock_result = Mock()
        mock_result.inserted_id = job_id
        mock_db.flywheel_runs.insert_one.return_value = mock_result

        mock_workflow_dag.delay = Mock()

        # Create request
        request = JobRequest(workload_id="test_workload_123", client_id="test_client_456")

        # Call endpoint
        response = await create_job(request)

        # Verify response
        assert isinstance(response, JobResponse)
        assert response.id == str(job_id)
        assert response.status == "queued"
        assert response.message == "LLM_JOB workflow started"

        # Verify database interaction
        mock_get_db.assert_called_once()
        mock_db.flywheel_runs.insert_one.assert_called_once()

        # Verify the FlywheelRun document structure
        call_args = mock_db.flywheel_runs.insert_one.call_args[0][0]
        assert call_args["workload_id"] == "test_workload_123"
        assert call_args["client_id"] == "test_client_456"
        assert call_args["started_at"] is None
        assert call_args["num_records"] == 0
        assert call_args["nims"] == []
        assert call_args["status"] == FlywheelRunStatus.PENDING

        # Verify workflow task was called
        mock_workflow_dag.delay.assert_called_once_with(
            workload_id="test_workload_123",
            flywheel_run_id=str(job_id),
            client_id="test_client_456",
            job_type="llm_job",
            data_split_config=None,
            config_name=None,
            ingestion_config=None,
        )

    @pytest.mark.asyncio
    @patch("src.api.endpoints.run_nim_workflow_dag")
    @patch("src.api.endpoints.get_db")
    @patch("src.api.endpoints.datetime")
    async def test_create_job_with_data_split_config(
        self, mock_datetime, mock_get_db, mock_workflow_dag
    ):
        """Test job creation with custom data split configuration."""
        # Setup mocks
        mock_time = datetime(2024, 1, 15, 10, 30, 0)
        mock_datetime.now.return_value = mock_time
        mock_datetime.utcnow.return_value = mock_time

        mock_db = Mock()
        mock_get_db.return_value = mock_db

        job_id = ObjectId()
        mock_result = Mock()
        mock_result.inserted_id = job_id
        mock_db.flywheel_runs.insert_one.return_value = mock_result

        mock_workflow_dag.delay = Mock()

        # Create request with data split config
        data_split_config = DataSplitConfig(
            min_total_records=50, random_seed=123, eval_size=10, val_ratio=0.2, limit=500
        )
        request = JobRequest(
            workload_id="test_workload_123",
            client_id="test_client_456",
            data_split_config=data_split_config,
            job_type="llm_job",
        )

        # Call endpoint
        response = await create_job(request)

        # Verify response
        assert isinstance(response, JobResponse)
        assert response.id == str(job_id)
        assert response.status == "queued"
        assert response.message == "LLM_JOB workflow started"

        # Verify workflow task was called with config
        mock_workflow_dag.delay.assert_called_once_with(
            workload_id="test_workload_123",
            flywheel_run_id=str(job_id),
            client_id="test_client_456",
            job_type="llm_job",
            data_split_config=data_split_config.model_dump(),
            config_name=None,
            ingestion_config=None,
        )

    @pytest.mark.asyncio
    @patch("src.api.endpoints.run_nim_workflow_dag")
    @patch("src.api.endpoints.get_db")
    @patch("src.api.endpoints.datetime")
    async def test_create_job_database_error(self, mock_datetime, mock_get_db, mock_workflow_dag):
        """Test job creation when database insertion fails."""
        # Setup mocks
        mock_time = datetime(2024, 1, 15, 10, 30, 0)
        mock_datetime.now.return_value = mock_time
        mock_datetime.utcnow.return_value = mock_time

        mock_db = Mock()
        mock_get_db.return_value = mock_db
        mock_db.flywheel_runs.insert_one.side_effect = Exception("Database connection failed")

        # Create request
        request = JobRequest(workload_id="test_workload_123", client_id="test_client_456")

        # Call endpoint and expect exception
        with pytest.raises(Exception, match="Database connection failed"):
            await create_job(request)

        # Verify workflow task was not called
        mock_workflow_dag.delay.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.api.endpoints.run_nim_workflow_dag")
    @patch("src.api.endpoints.get_db")
    @patch("src.api.endpoints.datetime")
    async def test_create_embedding_job_with_date_range(
        self, mock_datetime, mock_get_db, mock_workflow_dag
    ):
        """Test that embedding job with from_date/to_date creates EmbeddingDataSplitConfig."""
        from datetime import date

        # Setup mocks
        mock_time = datetime(2024, 1, 15, 10, 30, 0)
        mock_datetime.now.return_value = mock_time
        mock_datetime.utcnow.return_value = mock_time

        mock_db = Mock()
        mock_get_db.return_value = mock_db

        job_id = ObjectId()
        mock_result = Mock()
        mock_result.inserted_id = job_id
        mock_db.flywheel_runs.insert_one.return_value = mock_result

        mock_workflow_dag.delay = Mock()

        # Create request with embedding-specific data split config
        embedding_data_split_config = EmbeddingDataSplitConfig(
            min_total_records=50,
            random_seed=123,
            eval_size=10,
            val_ratio=0.2,
            limit=500,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 1, 31),
            min_negatives=1,
            max_negatives=5,
        )
        request = JobRequest(
            workload_id="test_workload_123",
            client_id="test_client_456",
            data_split_config=embedding_data_split_config,
            job_type="embedding_job",
        )

        # Verify the request's data_split_config is actually an EmbeddingDataSplitConfig
        assert isinstance(request.data_split_config, EmbeddingDataSplitConfig)
        assert request.data_split_config.from_date == date(2024, 1, 1)
        assert request.data_split_config.to_date == date(2024, 1, 31)

        # Call endpoint
        response = await create_job(request)

        # Verify response
        assert isinstance(response, JobResponse)
        assert response.id == str(job_id)
        assert response.status == "queued"
        assert response.message == "EMBEDDING_JOB workflow started"

        # Verify workflow task was called with EmbeddingDataSplitConfig
        mock_workflow_dag.delay.assert_called_once()
        call_kwargs = mock_workflow_dag.delay.call_args[1]
        assert call_kwargs["workload_id"] == "test_workload_123"
        assert call_kwargs["job_type"] == "embedding_job"

        # Verify data_split_config contains embedding-specific fields
        data_split_config_dict = call_kwargs["data_split_config"]
        assert data_split_config_dict["from_date"] == date(2024, 1, 1)
        assert data_split_config_dict["to_date"] == date(2024, 1, 31)
        assert data_split_config_dict["min_negatives"] == 1
        assert data_split_config_dict["max_negatives"] == 5


class TestGetJobs:
    """Test cases for the get_jobs endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.endpoints.get_db")
    async def test_get_jobs_success(self, mock_get_db):
        """Test successful retrieval of jobs list."""
        # Setup mock data
        job1_id = ObjectId()
        job2_id = ObjectId()
        mock_time1 = datetime(2024, 1, 15, 10, 30, 0)
        mock_time2 = datetime(2024, 1, 15, 11, 30, 0)

        mock_docs = [
            {
                "_id": job1_id,
                "workload_id": "workload_1",
                "client_id": "client_1",
                "started_at": mock_time1,
                "finished_at": None,
                "num_records": 100,
                "nims": [],
                "status": FlywheelRunStatus.RUNNING,
                "datasets": [{"name": "dataset1", "num_records": 100, "nmp_uri": "uri1"}],
                "error": None,
            },
            {
                "_id": job2_id,
                "workload_id": "workload_2",
                "client_id": "client_2",
                "started_at": mock_time2,
                "finished_at": mock_time2,
                "num_records": 200,
                "nims": [],
                "status": FlywheelRunStatus.COMPLETED,
                "datasets": [{"name": "dataset2", "num_records": 200, "nmp_uri": "uri2"}],
                "error": None,
            },
        ]

        mock_db = Mock()
        mock_get_db.return_value = mock_db
        mock_db.flywheel_runs.find.return_value = mock_docs

        # Call endpoint
        response = await get_jobs()

        # Verify response
        assert isinstance(response, JobsListResponse)
        assert len(response.jobs) == 2

        # Verify first job
        job1 = response.jobs[0]
        assert job1.id == str(job1_id)
        assert job1.workload_id == "workload_1"
        assert job1.client_id == "client_1"
        assert job1.status == FlywheelRunStatus.RUNNING
        assert job1.started_at == mock_time1
        assert job1.finished_at is None
        assert len(job1.datasets) == 1
        assert job1.error is None

        # Verify second job
        job2 = response.jobs[1]
        assert job2.id == str(job2_id)
        assert job2.workload_id == "workload_2"
        assert job2.client_id == "client_2"
        assert job2.status == FlywheelRunStatus.COMPLETED
        assert job2.started_at == mock_time2
        assert job2.finished_at == mock_time2
        assert len(job2.datasets) == 1
        assert job2.error is None

        # Verify database interaction
        mock_get_db.assert_called_once()
        mock_db.flywheel_runs.find.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.api.endpoints.get_db")
    async def test_get_jobs_empty_list(self, mock_get_db):
        """Test retrieval when no jobs exist."""
        mock_db = Mock()
        mock_get_db.return_value = mock_db
        mock_db.flywheel_runs.find.return_value = []

        # Call endpoint
        response = await get_jobs()

        # Verify response
        assert isinstance(response, JobsListResponse)
        assert len(response.jobs) == 0

    @pytest.mark.asyncio
    @patch("src.api.endpoints.get_db")
    async def test_get_jobs_clamps_negative_runtime(self, mock_get_db):
        """A run whose ``finished_at`` precedes ``started_at`` must not 500 the list.

        A job cancelled immediately after creation can have its two timestamps
        written by different code paths and land out of order by a few
        milliseconds. ``JobListItem.runtime_seconds`` is ``ge=0.0``, so without a
        clamp that single document raises a ValidationError and blanks the
        ENTIRE list — every other job becomes invisible because one row is a few
        ms skewed.
        """
        skewed_id, healthy_id = ObjectId(), ObjectId()
        started = datetime(2024, 1, 15, 10, 30, 0, 918000)

        def _doc(job_id, finished, workload):
            return {
                "_id": job_id,
                "workload_id": workload,
                "client_id": "client_1",
                "started_at": started,
                "finished_at": finished,
                "num_records": 0,
                "nims": [],
                "status": FlywheelRunStatus.CANCELLED,
                "datasets": [],
            }

        mock_db = Mock()
        mock_get_db.return_value = mock_db
        mock_db.flywheel_runs.find.return_value = [
            # finished 22 ms BEFORE it started
            _doc(skewed_id, started - timedelta(milliseconds=22), "skewed"),
            _doc(healthy_id, started + timedelta(seconds=5), "healthy"),
        ]

        response = await get_jobs()

        # The skewed row is clamped to 0 rather than raising ...
        assert len(response.jobs) == 2
        skewed = next(j for j in response.jobs if j.id == str(skewed_id))
        assert skewed.runtime_seconds == 0.0
        # ... and it does not take the rest of the list down with it.
        healthy = next(j for j in response.jobs if j.id == str(healthy_id))
        assert healthy.runtime_seconds == 5.0

    @pytest.mark.asyncio
    @patch("src.api.endpoints.get_db")
    async def test_get_jobs_with_error(self, mock_get_db):
        """Test retrieval of jobs with error status."""
        job_id = ObjectId()
        mock_time = datetime(2024, 1, 15, 10, 30, 0)

        mock_docs = [
            {
                "_id": job_id,
                "workload_id": "workload_1",
                "client_id": "client_1",
                "started_at": mock_time,
                "finished_at": mock_time,
                "num_records": 0,
                "nims": [],
                "status": FlywheelRunStatus.FAILED,
                "datasets": [],
                "error": "Job failed due to timeout",
            }
        ]

        mock_db = Mock()
        mock_get_db.return_value = mock_db
        mock_db.flywheel_runs.find.return_value = mock_docs

        # Call endpoint
        response = await get_jobs()

        # Verify response
        assert isinstance(response, JobsListResponse)
        assert len(response.jobs) == 1

        job = response.jobs[0]
        assert job.id == str(job_id)
        assert job.status == FlywheelRunStatus.FAILED
        assert job.error == "Job failed due to timeout"


class TestGetJob:
    """Test cases for the get_job endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.endpoints.get_job_details")
    async def test_get_job_success(self, mock_get_job_details):
        """Test successful retrieval of job details."""
        job_id = "507f1f77bcf86cd799439011"
        mock_response = JobDetailResponse(
            id=job_id,
            workload_id="test_workload",
            client_id="test_client",
            status="running",
            started_at=datetime(2024, 1, 15, 10, 30, 0),
            finished_at=None,
            runtime_seconds=0.0,
            num_records=100,
            llm_judge=None,
            nims=[],
            datasets=[],
            error=None,
        )
        mock_get_job_details.return_value = mock_response

        # Call endpoint
        response = await get_job(job_id)

        # Verify response
        assert response == mock_response
        mock_get_job_details.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    @patch("src.api.endpoints.get_job_details")
    async def test_get_job_not_found(self, mock_get_job_details):
        """Test job retrieval when job doesn't exist."""
        job_id = "507f1f77bcf86cd799439011"
        mock_get_job_details.side_effect = HTTPException(status_code=404, detail="Job not found")

        # Call endpoint and expect exception
        with pytest.raises(HTTPException) as exc_info:
            await get_job(job_id)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Job not found"
        mock_get_job_details.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    @patch("src.api.endpoints.get_job_details")
    async def test_get_job_invalid_id(self, mock_get_job_details):
        """Test job retrieval with invalid job ID."""
        job_id = "invalid_id"
        mock_get_job_details.side_effect = HTTPException(
            status_code=400, detail="Invalid job_id format"
        )

        # Call endpoint and expect exception
        with pytest.raises(HTTPException) as exc_info:
            await get_job(job_id)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid job_id format"
        mock_get_job_details.assert_called_once_with(job_id)


class TestDeleteJobEndpoint:
    """Test cases for the delete_job_endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.endpoints.delete_job")
    async def test_delete_job_success(self, mock_delete_job):
        """Test successful job deletion."""
        job_id = "507f1f77bcf86cd799439011"
        mock_response = JobDeleteResponse(
            id=job_id,
            message="Job deletion started. Resources will be cleaned up in the background.",
        )
        mock_delete_job.return_value = mock_response

        # Call endpoint
        response = await delete_job_endpoint(job_id)

        # Verify response
        assert response == mock_response
        mock_delete_job.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    @patch("src.api.endpoints.delete_job")
    async def test_delete_job_not_found(self, mock_delete_job):
        """Test job deletion when job doesn't exist."""
        job_id = "507f1f77bcf86cd799439011"
        mock_delete_job.side_effect = HTTPException(
            status_code=404, detail=f"Job with ID {job_id} not found"
        )

        # Call endpoint and expect exception
        with pytest.raises(HTTPException) as exc_info:
            await delete_job_endpoint(job_id)

        assert exc_info.value.status_code == 404
        assert f"Job with ID {job_id} not found" in exc_info.value.detail
        mock_delete_job.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    @patch("src.api.endpoints.delete_job")
    async def test_delete_job_still_running(self, mock_delete_job):
        """Test job deletion when job is still running."""
        job_id = "507f1f77bcf86cd799439011"
        mock_delete_job.side_effect = HTTPException(
            status_code=400, detail="Cannot delete a running job. Please cancel the job first."
        )

        # Call endpoint and expect exception
        with pytest.raises(HTTPException) as exc_info:
            await delete_job_endpoint(job_id)

        assert exc_info.value.status_code == 400
        assert "Cannot delete a running job" in exc_info.value.detail
        mock_delete_job.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    @patch("src.api.endpoints.delete_job")
    async def test_delete_job_invalid_id(self, mock_delete_job):
        """Test job deletion with invalid job ID."""
        job_id = "invalid_id"
        mock_delete_job.side_effect = HTTPException(status_code=400, detail="Invalid job_id format")

        # Call endpoint and expect exception
        with pytest.raises(HTTPException) as exc_info:
            await delete_job_endpoint(job_id)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid job_id format"
        mock_delete_job.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    @patch("src.api.endpoints.delete_job")
    async def test_delete_job_server_error(self, mock_delete_job):
        """Test job deletion when server error occurs."""
        job_id = "507f1f77bcf86cd799439011"
        mock_delete_job.side_effect = HTTPException(
            status_code=500, detail="Failed to initiate job deletion"
        )

        # Call endpoint and expect exception
        with pytest.raises(HTTPException) as exc_info:
            await delete_job_endpoint(job_id)

        assert exc_info.value.status_code == 500
        assert "Failed to initiate job deletion" in exc_info.value.detail
        mock_delete_job.assert_called_once_with(job_id)


class TestCancelJobEndpoint:
    """Test cases for the cancel_job_endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.endpoints.cancel_job")
    async def test_cancel_job_success(self, mock_cancel_job):
        """Test successful job cancellation."""
        job_id = "507f1f77bcf86cd799439011"
        mock_response = JobCancelResponse(
            id=job_id, message="Job cancellation initiated successfully."
        )
        mock_cancel_job.return_value = mock_response

        # Call endpoint
        response = await cancel_job_endpoint(job_id)

        # Verify response
        assert response == mock_response
        mock_cancel_job.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    @patch("src.api.endpoints.cancel_job")
    async def test_cancel_job_not_found(self, mock_cancel_job):
        """Test job cancellation when job doesn't exist."""
        job_id = "507f1f77bcf86cd799439011"
        mock_cancel_job.side_effect = HTTPException(
            status_code=404, detail=f"Job with ID {job_id} not found"
        )

        # Call endpoint and expect exception
        with pytest.raises(HTTPException) as exc_info:
            await cancel_job_endpoint(job_id)

        assert exc_info.value.status_code == 404
        assert f"Job with ID {job_id} not found" in exc_info.value.detail
        mock_cancel_job.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    @patch("src.api.endpoints.cancel_job")
    async def test_cancel_job_already_finished(self, mock_cancel_job):
        """Test job cancellation when job is already finished."""
        job_id = "507f1f77bcf86cd799439011"
        mock_cancel_job.side_effect = HTTPException(
            status_code=400, detail="Cannot cancel a job that has already finished."
        )

        # Call endpoint and expect exception
        with pytest.raises(HTTPException) as exc_info:
            await cancel_job_endpoint(job_id)

        assert exc_info.value.status_code == 400
        assert "Cannot cancel a job that has already finished" in exc_info.value.detail
        mock_cancel_job.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    @patch("src.api.endpoints.cancel_job")
    async def test_cancel_job_already_cancelled(self, mock_cancel_job):
        """Test job cancellation when job is already cancelled."""
        job_id = "507f1f77bcf86cd799439011"
        mock_response = JobCancelResponse(id=job_id, message="Job is already cancelled.")
        mock_cancel_job.return_value = mock_response

        # Call endpoint
        response = await cancel_job_endpoint(job_id)

        # Verify response
        assert response == mock_response
        assert response.message == "Job is already cancelled."
        mock_cancel_job.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    @patch("src.api.endpoints.cancel_job")
    async def test_cancel_job_invalid_id(self, mock_cancel_job):
        """Test job cancellation with invalid job ID."""
        job_id = "invalid_id"
        mock_cancel_job.side_effect = HTTPException(status_code=400, detail="Invalid job_id format")

        # Call endpoint and expect exception
        with pytest.raises(HTTPException) as exc_info:
            await cancel_job_endpoint(job_id)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid job_id format"
        mock_cancel_job.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    @patch("src.api.endpoints.cancel_job")
    async def test_cancel_job_server_error(self, mock_cancel_job):
        """Test job cancellation when server error occurs."""
        job_id = "507f1f77bcf86cd799439011"
        mock_cancel_job.side_effect = HTTPException(status_code=500, detail="Failed to cancel job")

        # Call endpoint and expect exception
        with pytest.raises(HTTPException) as exc_info:
            await cancel_job_endpoint(job_id)

        assert exc_info.value.status_code == 500
        assert "Failed to cancel job" in exc_info.value.detail
        mock_cancel_job.assert_called_once_with(job_id)


class TestEndpointsIntegration:
    """Integration tests for endpoint interactions."""

    @pytest.mark.asyncio
    @patch("src.api.endpoints.run_nim_workflow_dag")
    @patch("src.api.endpoints.get_db")
    @patch("src.api.endpoints.datetime")
    async def test_create_and_list_jobs_integration(
        self, mock_datetime, mock_get_db, mock_workflow_dag
    ):
        """Test creating a job and then listing it."""
        # Setup mocks for create_job
        mock_time = datetime(2024, 1, 15, 10, 30, 0)
        mock_datetime.now.return_value = mock_time
        mock_datetime.utcnow.return_value = mock_time

        job_id = ObjectId()
        mock_result = Mock()
        mock_result.inserted_id = job_id

        mock_db = Mock()
        mock_get_db.return_value = mock_db
        mock_db.flywheel_runs.insert_one.return_value = mock_result
        mock_workflow_dag.delay = Mock()

        # Create job
        request = JobRequest(workload_id="test_workload_123", client_id="test_client_456")
        create_response = await create_job(request)

        # Verify job creation
        assert create_response.id == str(job_id)
        assert create_response.status == "queued"

        # Setup mocks for get_jobs
        mock_docs = [
            {
                "_id": job_id,
                "workload_id": "test_workload_123",
                "client_id": "test_client_456",
                "started_at": mock_time,
                "finished_at": None,
                "num_records": 0,
                "nims": [],
                "status": FlywheelRunStatus.PENDING,
                "datasets": [],
                "error": None,
            }
        ]
        mock_db.flywheel_runs.find.return_value = mock_docs

        # List jobs
        list_response = await get_jobs()

        # Verify job appears in list
        assert len(list_response.jobs) == 1
        job = list_response.jobs[0]
        assert job.id == str(job_id)
        assert job.workload_id == "test_workload_123"
        assert job.client_id == "test_client_456"
        assert job.status == FlywheelRunStatus.PENDING
