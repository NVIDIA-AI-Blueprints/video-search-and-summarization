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

from fastapi import APIRouter, HTTPException

from src.api.backend_gate import assert_supported_type
from src.api.config_manager.exceptions import (
    ConfigurationAlreadyExistsError,
    ConfigurationNotFoundError,
    ConfigurationOperationError,
    ConfigurationUpdateError,
    ConfigurationValidationError,
)
from src.api.config_manager.manager import ConfigurationManager
from src.api.config_manager.schemas import (
    ConfigurationDeleteResponse,
    ConfigurationListResponse,
    ConfigurationRequest,
    ConfigurationResponse,
)
from src.api.db import get_db
from src.api.job_service import cancel_job, delete_job, get_job_details
from src.api.models import FlywheelRun
from src.api.schemas import (
    FlywheelRunStatus,
    JobCancelResponse,
    JobDeleteResponse,
    JobDetailResponse,
    JobListItem,
    JobRequest,
    JobResponse,
    JobsListResponse,
)
from src.api.triggers.endpoints import router as triggers_router
from src.log_utils import setup_logging
from src.tasks.tasks import run_nim_workflow_dag

logger = setup_logging("data_flywheel.api.endpoints")

# Initialize configuration manager
config_manager = ConfigurationManager()

router = APIRouter()

# Include trigger routes
router.include_router(triggers_router)


@router.post("/jobs", response_model=JobResponse)
async def create_job(request: JobRequest) -> JobResponse:
    """
    Create a new job that runs the workflow based on job type.
    """

    # Reject job types the active backend can't run (e.g. an LLM/embedding job on
    # a DEFT image). Fail fast — before any DB write or Celery enqueue.
    assert_supported_type(request.job_type, field_name="job_type")

    # Validate config_name if provided
    if request.config_name:
        try:
            if not config_manager.get_configuration(request.config_name):
                raise HTTPException(
                    status_code=404,
                    detail=f"Configuration '{request.config_name}' not found. Please create the configuration first or use the default configuration by omitting the config_name parameter.",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error validating configuration: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error validating configuration: {e!s}",
            ) from e

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"Request received at {current_time} for {request.job_type} job, workload_id {request.workload_id} and client_id {request.client_id}"
    logger.info(entry)

    # Create FlywheelRun document
    flywheel_run = FlywheelRun(
        workload_id=request.workload_id,
        client_id=request.client_id,
        job_type=request.job_type,  # Store job type
        started_at=None,
        config_name=request.config_name,
        num_records=0,  # Will be updated when datasets are created
        nims=[],
        status=FlywheelRunStatus.PENDING,
    )

    # Save to MongoDB
    db = get_db()
    result = db.flywheel_runs.insert_one(flywheel_run.to_mongo())
    flywheel_run.id = str(result.inserted_id)

    run_nim_workflow_dag.delay(
        workload_id=request.workload_id,
        flywheel_run_id=flywheel_run.id,
        client_id=request.client_id,
        job_type=request.job_type,
        data_split_config=request.data_split_config.model_dump()
        if request.data_split_config
        else None,
        config_name=request.config_name,
        ingestion_config=request.ingestion_config.model_dump()
        if request.ingestion_config
        else None,
    )

    return JobResponse(
        id=flywheel_run.id,
        status="queued",
        message=f"{request.job_type.upper()} workflow started",
    )


@router.get("/jobs", response_model=JobsListResponse)
async def get_jobs() -> JobsListResponse:
    """
    Get a list of all active and recent jobs.
    """
    db = get_db()
    jobs: list[JobListItem] = []

    # Get all FlywheelRun documents
    for doc in db.flywheel_runs.find():
        flywheel_run = FlywheelRun.from_mongo(doc)

        # Calculate runtime_seconds.
        #
        # Clamped at 0: ``JobListItem.runtime_seconds`` is declared ``ge=0.0``,
        # and a run can legitimately end up with ``finished_at`` marginally
        # BEFORE ``started_at`` — a job cancelled immediately after creation has
        # its two timestamps written by different code paths, so they can land
        # out of order by a few milliseconds. Without the clamp that single
        # document raises a ValidationError here and blanks the ENTIRE job list
        # (the whole endpoint 500s because one row is a few ms skewed).
        if flywheel_run.started_at:
            if flywheel_run.finished_at:
                runtime_seconds = (
                    flywheel_run.finished_at - flywheel_run.started_at
                ).total_seconds()
            else:
                runtime_seconds = (datetime.utcnow() - flywheel_run.started_at).total_seconds()
            runtime_seconds = max(0.0, runtime_seconds)
        else:
            runtime_seconds = 0.0

        job = JobListItem(
            id=str(flywheel_run.id),
            workload_id=flywheel_run.workload_id,
            client_id=flywheel_run.client_id,
            status=flywheel_run.status,
            started_at=flywheel_run.started_at,
            finished_at=flywheel_run.finished_at,
            runtime_seconds=runtime_seconds,
            datasets=flywheel_run.datasets,
            error=flywheel_run.error,
        )
        jobs.append(job)

    return JobsListResponse(jobs=jobs)


@router.get("/jobs/{job_id}", response_model=JobDetailResponse)
async def get_job(job_id: str) -> JobDetailResponse:
    """
    Get the status and result of a job, including detailed information about all tasks in the workflow.
    """
    return get_job_details(job_id)


@router.delete("/jobs/{job_id}", response_model=JobDeleteResponse)
async def delete_job_endpoint(job_id: str) -> JobDeleteResponse:
    """
    Delete a job and all its associated resources from the database.
    This is an asynchronous operation - the endpoint returns immediately while
    the deletion continues in the background.

    If the job is still running, it must be cancelled first.
    """
    return delete_job(job_id)


@router.post("/jobs/{job_id}/cancel", response_model=JobCancelResponse)
async def cancel_job_endpoint(job_id: str) -> JobCancelResponse:
    """
    Cancel a running job.
    This will stop the job execution and mark it as cancelled.

    The job must be in a running state to be cancelled.
    Already finished jobs cannot be cancelled.
    """
    return cancel_job(job_id)


# ============================================================================
# CONFIGURATION MANAGEMENT ENDPOINTS
# ============================================================================


@router.get("/configs", response_model=ConfigurationListResponse)
async def list_configurations() -> ConfigurationListResponse:
    """
    List all available configurations.

    Returns:
        ConfigurationListResponse with summary information and total count
    """
    try:
        configurations = config_manager.list_configurations()
        total_count = config_manager.get_configuration_count()

        return ConfigurationListResponse(configurations=configurations, total_count=total_count)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list configurations: {e!s}",
        ) from e


@router.post("/configs", response_model=ConfigurationResponse, status_code=201)
async def create_configuration(config: ConfigurationRequest) -> ConfigurationResponse:
    """
    Create a new configuration.

    Args:
        config: The ConfigurationRequest to create

    Returns:
        The created ConfigurationResponse

    Raises:
        HTTPException: If configuration with same name already exists
    """
    assert_supported_type(config.config_type, field_name="config_type")
    try:
        created_config = config_manager.create_configuration(config.model_dump())
        return ConfigurationResponse(
            id=str(created_config.id),
            name=created_config.name,
            description=created_config.description,
            config_type=created_config.config_type,
            config_data=created_config.config_data.model_dump(mode="json"),
            created_at=created_config.created_at,
            updated_at=created_config.updated_at,
        )
    except ConfigurationAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ConfigurationValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create configuration: {e!s}",
        ) from e


@router.get("/configs/{config_name}", response_model=ConfigurationResponse)
async def get_configuration(config_name: str) -> ConfigurationResponse:
    """
    Get a specific configuration by name.

    Args:
        config_name: Name of the configuration to retrieve

    Returns:
        The ConfigurationResponse object

    Raises:
        HTTPException: If configuration is not found
    """
    try:
        config = config_manager.get_configuration(config_name)
        if not config:
            raise HTTPException(
                status_code=404,
                detail=f"Configuration '{config_name}' not found",
            )
        return ConfigurationResponse(
            id=str(config.id),
            name=config.name,
            description=config.description,
            config_type=config.config_type,
            config_data=config.config_data.model_dump(mode="json"),
            created_at=config.created_at,
            updated_at=config.updated_at,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve configuration: {e!s}",
        ) from e


@router.put("/configs/{config_name}", response_model=ConfigurationResponse)
async def update_configuration(
    config_name: str, config: ConfigurationRequest
) -> ConfigurationResponse:
    """
    Update an existing configuration.

    Args:
        config_name: Name of the configuration to update
        config: The updated ConfigurationRequest

    Returns:
        The updated ConfigurationResponse

    Raises:
        HTTPException: If configuration is not found or name mismatch
    """
    assert_supported_type(config.config_type, field_name="config_type")
    try:
        updated_config = config_manager.update_configuration(config_name, config.model_dump())
        return ConfigurationResponse(
            id=str(updated_config.id),
            name=updated_config.name,
            description=updated_config.description,
            config_type=updated_config.config_type,
            config_data=updated_config.config_data.model_dump(mode="json"),
            created_at=updated_config.created_at,
            updated_at=updated_config.updated_at,
        )
    except ConfigurationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (ConfigurationUpdateError, ConfigurationValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ConfigurationOperationError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update configuration: {e!s}",
        ) from e


@router.delete("/configs/{config_name}", response_model=ConfigurationDeleteResponse)
async def delete_configuration(config_name: str) -> ConfigurationDeleteResponse:
    """
    Delete a configuration.

    Args:
        config_name: Name of the configuration to delete

    Returns:
        Confirmation message that the configuration was deleted

    Raises:
        HTTPException: If configuration is not found
    """
    try:
        deleted_config = config_manager.delete_configuration(config_name)
        return ConfigurationDeleteResponse(
            message=f"Configuration '{deleted_config}' successfully deleted",
            deleted_config=deleted_config,
        )
    except ConfigurationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ConfigurationOperationError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete configuration: {e!s}",
        ) from e
