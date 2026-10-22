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

"""API endpoints for trigger management."""

import logging
from typing import Annotated

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query

from src.api.backend_gate import assert_supported_type
from src.api.db import get_db
from src.api.triggers.models import TriggerExecution
from src.api.triggers.schemas import (
    ExecutionListResponse,
    ExecutionResponse,
    NIMExecutionResultResponse,
    TriggerCreateRequest,
    TriggerExecuteResponse,
    TriggerListResponse,
    TriggerResponse,
    TriggerUpdateRequest,
)
from src.api.triggers.service import (
    TriggerService,
    check_execution_stale_with_retry,
    mark_execution_as_failed,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/triggers", tags=["triggers"])


def _trigger_to_response(trigger) -> TriggerResponse:
    """Convert trigger model to API response."""
    return TriggerResponse(
        id=str(trigger.id),
        name=trigger.name,
        description=trigger.description,
        workload_id=trigger.workload_id,
        client_id=trigger.client_id,
        job_type=trigger.job_type,
        trigger_type=trigger.trigger_type,
        eval_schedule=trigger.eval_schedule,
        metric_thresholds=trigger.metric_thresholds,
        min_new_records=trigger.min_new_records,
        enable_data_ingestion=trigger.enable_data_ingestion,
        ingestion_timeout_minutes=trigger.ingestion_timeout_minutes,
        ingestion_config=trigger.ingestion_config.model_dump()
        if trigger.ingestion_config
        else None,
        data_window_months=trigger.data_window_months,
        config_name=trigger.config_name,
        reference_job_id=trigger.reference_job_id,
        model_source=trigger.model_source,
        latest_fine_tuned_models=trigger.latest_fine_tuned_models or {},
        enabled=trigger.enabled,
        max_consecutive_failures=trigger.max_consecutive_failures,
        created_at=trigger.created_at,
        updated_at=trigger.updated_at,
        last_execution_time=trigger.last_execution_time,
        last_execution_status=trigger.last_execution_status,
        consecutive_failures=trigger.consecutive_failures,
    )


def _execution_to_response(execution) -> ExecutionResponse:
    """Convert execution model to API response."""
    # Convert per-NIM results
    nim_results = [
        NIMExecutionResultResponse(
            model_name=r.model_name,
            model_used=r.model_used,
            metrics=r.metrics,
            thresholds_checked=r.thresholds_checked,
            passed_thresholds=r.passed_thresholds,
            needs_retraining=r.needs_retraining,
            training_triggered=r.training_triggered,
            customization_id=r.customization_id,
            fine_tuned_model=r.fine_tuned_model,
            post_training_metrics=r.post_training_metrics,
            incomplete_evaluation=r.incomplete_evaluation,
            eval_error=r.eval_error,
            customization_error=r.customization_error,
            post_eval_error=r.post_eval_error,
            skip_reason=r.skip_reason,
        )
        for r in execution.nim_results
    ]

    return ExecutionResponse(
        id=str(execution.id),
        trigger_id=execution.trigger_id,
        trigger_name=execution.trigger_name,
        trigger_type=execution.trigger_type,
        execution_time=execution.execution_time,
        completed_at=execution.completed_at,
        new_records_ingested=execution.new_records_ingested,
        ingestion_duration_seconds=execution.ingestion_duration_seconds,
        eval_job_id=execution.eval_job_id,
        eval_job_status=execution.eval_job_status,
        training_job_id=execution.training_job_id,
        training_job_status=execution.training_job_status,
        nim_results=nim_results,
        total_nims=execution.total_nims,
        nims_passed=execution.nims_passed,
        nims_failed=execution.nims_failed,
        nims_retrained=execution.nims_retrained,
        all_thresholds_met=execution.all_thresholds_met,
        triggered_training=execution.triggered_training,
        decision_reason=execution.decision_reason,
        skip_reason=execution.skip_reason,
        data_window_used=execution.data_window_used,
        status=execution.status,
        error=execution.error,
        eval_duration_seconds=execution.eval_duration_seconds,
        total_duration_seconds=execution.total_duration_seconds,
    )


@router.post("", response_model=TriggerResponse, status_code=201)
async def create_trigger(request: TriggerCreateRequest) -> TriggerResponse:
    """Create a new automatic retraining trigger."""
    # Reject triggers whose job_type the active backend can't run (a trigger fires
    # run_nim_workflow_dag with this job_type). NOTE: how DEFT triggers retraining
    # is still to be designed; for now this just blocks unsupported job types.
    assert_supported_type(request.job_type, field_name="job_type")
    try:
        db = get_db()
        service = TriggerService(db)
        trigger = service.create_trigger(request)
        return _trigger_to_response(trigger)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to create trigger: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("", response_model=TriggerListResponse)
async def list_triggers(
    workload_id: Annotated[str | None, Query()] = None,
    client_id: Annotated[str | None, Query()] = None,
    job_type: Annotated[str | None, Query()] = None,
    trigger_type: Annotated[str | None, Query()] = None,
    config_name: Annotated[str | None, Query()] = None,
    model_source: Annotated[str | None, Query()] = None,
    last_execution_status: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> TriggerListResponse:
    """
    List all triggers with optional filtering.

    Filters:
    - workload_id: Filter by workload
    - client_id: Filter by client
    - job_type: Filter by job type (embedding_job, llm_job, aoi_job, its_job)
    - trigger_type: Filter by trigger type (data_threshold, metric_degradation, schedule)
    - config_name: Filter by configuration name
    - model_source: Filter by model source (base, fine_tuned)
    - last_execution_status: Filter by last execution status (success, failure, triggered_retraining)
    - enabled: Filter by enabled status (true/false)
    - skip: Pagination offset
    - limit: Pagination limit (1-1000)
    """
    db = get_db()
    service = TriggerService(db)
    triggers, total = service.list_triggers(
        workload_id=workload_id,
        client_id=client_id,
        job_type=job_type,
        trigger_type=trigger_type,
        config_name=config_name,
        model_source=model_source,
        last_execution_status=last_execution_status,
        enabled=enabled,
        skip=skip,
        limit=limit,
    )

    return TriggerListResponse(
        triggers=[_trigger_to_response(t) for t in triggers],
        total=total,
    )


@router.get("/{trigger_id}", response_model=TriggerResponse)
async def get_trigger(trigger_id: str) -> TriggerResponse:
    """Get trigger by ID."""
    db = get_db()
    service = TriggerService(db)
    trigger = service.get_trigger(trigger_id)

    if not trigger:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")

    return _trigger_to_response(trigger)


@router.put("/{trigger_id}", response_model=TriggerResponse)
async def update_trigger(trigger_id: str, request: TriggerUpdateRequest) -> TriggerResponse:
    """Update trigger."""
    try:
        db = get_db()
        service = TriggerService(db)
        trigger = service.update_trigger(trigger_id, request)

        if not trigger:
            raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")

        return _trigger_to_response(trigger)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update trigger: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.delete("/{trigger_id}", status_code=204)
async def delete_trigger(trigger_id: str):
    """Delete trigger."""
    db = get_db()
    service = TriggerService(db)
    deleted = service.delete_trigger(trigger_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")


@router.post("/{trigger_id}/execute", response_model=TriggerExecuteResponse)
async def execute_trigger(trigger_id: str) -> TriggerExecuteResponse:
    """Manually execute a trigger (for testing)."""
    from datetime import datetime

    from pymongo.errors import DuplicateKeyError

    from src.api.triggers.models import TriggerExecution
    from src.tasks.tasks import execute_trigger as execute_trigger_task

    db = get_db()
    service = TriggerService(db)

    # Verify trigger exists
    trigger = service.get_trigger(trigger_id)
    if not trigger:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")

    # Check if trigger is enabled (fail-fast before creating execution record)
    if not trigger.enabled:
        raise HTTPException(
            status_code=400,
            detail=f"Trigger '{trigger.name}' is disabled. Enable it first via POST /api/triggers/{trigger_id}/enable",
        )

    # Check if there's already a running or pending execution for this trigger
    # This prevents rapid-fire manual executions from creating duplicates
    existing_execution = db.trigger_executions.find_one(
        {"trigger_id": trigger_id, "status": {"$in": ["running", "pending"]}}
    )
    if existing_execution:
        # Check if the linked job is actually still running (handles restart scenarios)
        # Use retry logic to handle race conditions where job completed but execution
        # is still being updated by the worker
        if check_execution_stale_with_retry(db, existing_execution):
            # Mark stale execution as failed and allow new execution
            mark_execution_as_failed(
                db, existing_execution, "Orphaned execution after service restart"
            )
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Trigger already has a {existing_execution['status']} execution: {existing_execution['_id']}",
            )

    # Create a pending execution record BEFORE dispatching to Celery
    # This ensures the next API call will see it and be blocked
    # The partial unique index on (trigger_id, status=running/pending) prevents duplicates
    execution = TriggerExecution(
        id=ObjectId(),
        trigger_id=trigger_id,
        trigger_name=trigger.name,
        trigger_type=trigger.trigger_type,
        execution_time=datetime.utcnow(),
        status="pending",
    )

    try:
        db.trigger_executions.insert_one(execution.to_mongo())
    except DuplicateKeyError:
        # Race condition: another request created an execution between our check and insert
        # Re-fetch the existing execution and return 409
        existing = db.trigger_executions.find_one(
            {"trigger_id": trigger_id, "status": {"$in": ["running", "pending"]}}
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Trigger already has a {existing['status']} execution: {existing['_id']}",
            ) from None
        # If we can't find it, it completed between operations - unlikely but possible
        raise HTTPException(status_code=409, detail="Execution conflict detected") from None

    execution_id = str(execution.id)

    # Execute trigger task asynchronously via Celery, passing the execution_id
    try:
        execute_trigger_task.delay(trigger_id, execution_id)
    except Exception as e:
        # If Celery dispatch fails, mark the execution as failed to prevent orphaned pending records
        logger.error(f"Failed to dispatch Celery task for trigger {trigger_id}: {e}", exc_info=True)
        db.trigger_executions.update_one(
            {"_id": execution.id},
            {"$set": {"status": "failed", "error": f"Failed to dispatch task: {e!s}"}},
        )
        raise HTTPException(status_code=500, detail="Failed to dispatch execution task") from e

    return TriggerExecuteResponse(
        message=f"Trigger {trigger.name} execution started",
        execution_id=execution_id,
        status="pending",
    )


@router.post("/{trigger_id}/enable", response_model=TriggerResponse)
async def enable_trigger(trigger_id: str) -> TriggerResponse:
    """Enable a trigger."""
    try:
        db = get_db()
        service = TriggerService(db)
        trigger = service.update_trigger(trigger_id, TriggerUpdateRequest(enabled=True))

        if not trigger:
            raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")

        return _trigger_to_response(trigger)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to enable trigger: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/{trigger_id}/disable", response_model=TriggerResponse)
async def disable_trigger(trigger_id: str) -> TriggerResponse:
    """Disable a trigger."""
    try:
        db = get_db()
        service = TriggerService(db)
        trigger = service.update_trigger(trigger_id, TriggerUpdateRequest(enabled=False))

        if not trigger:
            raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")

        return _trigger_to_response(trigger)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to disable trigger: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/{trigger_id}/executions", response_model=ExecutionListResponse)
async def get_trigger_executions(
    trigger_id: str,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> ExecutionListResponse:
    """Get execution history for a trigger."""
    db = get_db()
    service = TriggerService(db)

    # Verify trigger exists
    trigger = service.get_trigger(trigger_id)
    if not trigger:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")

    executions, total = service.get_executions(trigger_id=trigger_id, skip=skip, limit=limit)

    return ExecutionListResponse(
        executions=[_execution_to_response(e) for e in executions],
        total=total,
    )


@router.get("/executions/all", response_model=ExecutionListResponse)
async def get_all_executions(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> ExecutionListResponse:
    """Get all trigger executions (across all triggers)."""
    db = get_db()
    service = TriggerService(db)
    executions, total = service.get_executions(trigger_id=None, skip=skip, limit=limit)

    return ExecutionListResponse(
        executions=[_execution_to_response(e) for e in executions],
        total=total,
    )


@router.get("/executions/{execution_id}", response_model=ExecutionResponse)
async def get_execution(execution_id: str) -> ExecutionResponse:
    """Get a specific trigger execution by ID."""
    db = get_db()
    try:
        execution_doc = db.trigger_executions.find_one({"_id": ObjectId(execution_id)})
    except (InvalidId, TypeError) as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid execution ID format: {execution_id}"
        ) from e

    if not execution_doc:
        raise HTTPException(status_code=404, detail="Execution not found")

    # Convert MongoDB document to TriggerExecution object
    execution = TriggerExecution.from_mongo(execution_doc)
    return _execution_to_response(execution)
