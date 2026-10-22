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

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from src.api.db import get_db
from src.api.db_manager import get_db_manager
from src.api.models import FlywheelRun
from src.api.schemas import (
    Customization,
    DeftRunDetailResponse,
    DeploymentStatus,
    Evaluation,
    FlywheelRunStatus,
    JobCancelResponse,
    JobDeleteResponse,
    JobDetailResponse,
    LLMJudgeResponse,
    NIMResponse,
    NIMRunStatus,
)
from src.log_utils import setup_logging
from src.tasks.tasks import cancel_job_resources, delete_job_resources

logger = setup_logging("data_flywheel.job_service")


def validate_object_id(id_str: str, param_name: str = "id") -> ObjectId:
    """
    Validate and convert a string ID to ObjectId.

    Args:
        id_str: The string ID to validate
        param_name: Name of the parameter for error messages

    Returns:
        ObjectId: The validated MongoDB ObjectId

    Raises:
        HTTPException: If the ID is invalid
    """
    try:
        if not ObjectId.is_valid(id_str):
            raise ValueError(f"Invalid {param_name} format")
        return ObjectId(id_str)
    except (InvalidId, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def validate_object_id_list(ids: list[ObjectId]) -> list[ObjectId]:
    """
    Validate a list of ObjectIds to ensure they are safe for querying.

    Args:
        ids: List of ObjectIds to validate

    Returns:
        List[ObjectId]: The validated list of ObjectIds

    Raises:
        HTTPException: If any ID is invalid
    """
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="Expected a list of IDs")

    # Ensure all IDs are valid ObjectIds
    for id_obj in ids:
        if not isinstance(id_obj, ObjectId):
            raise HTTPException(status_code=400, detail=f"Invalid ID in list: {id_obj}")

    return ids


def get_job_details(job_id: str) -> JobDetailResponse:
    """
    Get the status and result of a job, including detailed information about all tasks in the workflow.
    """
    db = get_db()

    # Validate job_id and convert to ObjectId
    job_object_id = validate_object_id(job_id, "job_id")

    # Get the flywheel run document
    doc = db.flywheel_runs.find_one({"_id": job_object_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Job not found")

    flywheel_run = FlywheelRun.from_mongo(doc)

    # Get all NIMs for this flywheel run using the validated job_id
    nims = list(db.nims.find({"flywheel_run_id": job_object_id}))

    # Extract and validate NIM IDs
    nim_ids = validate_object_id_list([nim["_id"] for nim in nims])

    # Get all evaluations for these NIMs using validated IDs
    evaluations = list(db.evaluations.find({"nim_id": {"$in": nim_ids}}))

    # Group evaluations by NIM
    nim_evaluations: dict[ObjectId, list[Evaluation]] = {}
    for eval in evaluations:
        if eval["nim_id"] not in nim_evaluations:
            nim_evaluations[eval["nim_id"]] = []
        nim_evaluations[eval["nim_id"]].append(
            Evaluation(
                eval_type=eval["eval_type"],
                scores=eval["scores"],
                dataset=eval["dataset"],
                eval_name=eval.get("eval_name"),
                started_at=eval["started_at"],
                finished_at=eval["finished_at"],
                runtime_seconds=eval["runtime_seconds"],
                progress=eval["progress"],
                nmp_uri=eval["nmp_uri"],
                mlflow_uri=eval.get("mlflow_uri", None),
                error=eval.get("error", None),
            )
        )

    # Get customizations using validated NIM IDs
    customizations = list(db.customizations.find({"nim_id": {"$in": nim_ids}}))
    nim_customizations: dict[ObjectId, list[Customization]] = {}
    for custom in customizations:
        if custom["nim_id"] not in nim_customizations:
            nim_customizations[custom["nim_id"]] = []
        nim_customizations[custom["nim_id"]].append(
            Customization(
                started_at=custom["started_at"],
                finished_at=custom["finished_at"],
                runtime_seconds=custom["runtime_seconds"],
                progress=custom["progress"],
                epochs_completed=custom["epochs_completed"],
                steps_completed=custom["steps_completed"],
                nmp_uri=custom["nmp_uri"],
                customized_model=custom.get("customized_model", None),
                error=custom.get("error", None),
            )
        )

    # Get LLM judge data using validated job_id
    llm_judge = db.llm_judge_runs.find_one({"flywheel_run_id": job_object_id})
    if llm_judge:
        llm_judge_response = LLMJudgeResponse(
            model_name=llm_judge["model_name"],
            type=llm_judge["deployment_type"],
            deployment_status=DeploymentStatus(
                llm_judge["deployment_status"] or DeploymentStatus.PENDING
            ),
            error=llm_judge.get("error", None),
        )
    else:
        llm_judge_response = None

    # Calculate runtime_seconds
    if flywheel_run.started_at:
        if flywheel_run.finished_at:
            runtime_seconds = (flywheel_run.finished_at - flywheel_run.started_at).total_seconds()
        else:
            runtime_seconds = (datetime.utcnow() - flywheel_run.started_at).total_seconds()
    else:
        runtime_seconds = 0.0

    return JobDetailResponse(
        id=str(flywheel_run.id),
        workload_id=flywheel_run.workload_id,
        client_id=flywheel_run.client_id,
        config_name=flywheel_run.config_name if flywheel_run.config_name else None,
        status=flywheel_run.status,
        started_at=flywheel_run.started_at,
        finished_at=flywheel_run.finished_at,
        runtime_seconds=runtime_seconds,
        deft=(
            # Built from the stored model rather than field-by-field: the two
            # shapes are mirrors, and an explicit copy silently drops whatever
            # the writer added and the copy forgot. That is not hypothetical --
            # this mapping carried three fields while Mongo held sixteen, so an
            # entire visibility change reached the database and never reached
            # the API. `test_deft_detail_response_mirrors_the_stored_model`
            # keeps the two in step.
            DeftRunDetailResponse(**flywheel_run.deft.model_dump()) if flywheel_run.deft else None
        ),
        num_records=flywheel_run.num_records or 0,
        llm_judge=llm_judge_response,
        nims=[
            NIMResponse(
                model_name=nim["model_name"],
                status=NIMRunStatus(nim.get("status", NIMRunStatus.PENDING)).value,
                deployment_status=DeploymentStatus(
                    nim["deployment_status"] or DeploymentStatus.PENDING
                ),
                runtime_seconds=nim["runtime_seconds"],
                evaluations=nim_evaluations.get(nim["_id"], []),
                customizations=nim_customizations.get(nim["_id"], []),
                error=nim.get("error", None),
            )
            for nim in nims
        ],
        datasets=flywheel_run.datasets,
        error=flywheel_run.error,
    )


def delete_job(job_id: str) -> JobDeleteResponse:
    """
    Delete a job and all its associated resources from the database.
    This is an asynchronous operation that starts the deletion process in the background.

    Args:
        job_id: ID of the job to delete

    Returns:
        JobResponse: Response indicating the deletion has started

    Raises:
        HTTPException:
            - 404 if job not found
            - 400 if job is still running or job_id format is invalid
            - 500 if task initiation fails
    """
    logger.info(f"Request received to delete job with ID: {job_id}")

    try:
        # Validate job_id and convert to ObjectId
        job_object_id = validate_object_id(job_id, "job_id")

        # Get the flywheel run document
        db = get_db()
        flywheel_run = db.flywheel_runs.find_one({"_id": job_object_id})
        if not flywheel_run:
            raise HTTPException(status_code=404, detail=f"Job with ID {job_id} not found")

        # Convert to model for type-safe access
        flywheel_run = FlywheelRun.from_mongo(flywheel_run)

        # Check if job is still running
        if not flywheel_run.finished_at:
            raise HTTPException(
                status_code=400, detail="Cannot delete a running job. Please cancel the job first."
            )

        # Fire off the Celery task with validated job_id
        delete_job_resources.delay(str(job_object_id))

        return JobDeleteResponse(
            id=str(job_object_id),
            message="Job deletion started. Resources will be cleaned up in the background.",
        )

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to initiate job deletion for {job_id}: {e!s}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg) from e


def cancel_job(job_id: str) -> JobCancelResponse:
    """
    Cancel a running job and mark it as cancelled.
    This will stop the job execution and clean up resources.

    Args:
        job_id: ID of the job to cancel

    Returns:
        JobCancelResponse: Response indicating the cancellation status

    Raises:
        HTTPException:
            - 404 if job not found
            - 400 if job is already finished or job_id format is invalid
            - 500 if cancellation fails
    """
    logger.info(f"Request received to cancel job with ID: {job_id}")

    try:
        # Validate job_id and convert to ObjectId
        job_object_id = validate_object_id(job_id, "job_id")

        # Get the flywheel run document
        flywheel_run = get_db_manager().get_flywheel_run(job_object_id)
        if not flywheel_run:
            raise HTTPException(status_code=404, detail=f"Job with ID {job_id} not found")

        # Convert to model for type-safe access
        flywheel_run = FlywheelRun.from_mongo(flywheel_run)

        # Check if job is already finished
        if flywheel_run.finished_at:
            raise HTTPException(
                status_code=400, detail="Cannot cancel a job that has already finished."
            )

        # Check if job is already cancelled
        if flywheel_run.status == FlywheelRunStatus.CANCELLED.value:
            return JobCancelResponse(
                id=str(job_object_id),
                message="Job is already cancelled.",
            )

        # Fire off the Celery task with validated job_id
        cancel_job_resources.delay(str(job_object_id))

        return JobCancelResponse(
            id=str(job_object_id),
            message="Job cancellation initiated successfully.",
        )

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to cancel job {job_id}: {e!s}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg) from e
