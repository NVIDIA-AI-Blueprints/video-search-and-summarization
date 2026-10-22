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

"""Service layer for trigger operations."""

import logging
import time
from datetime import datetime, timedelta

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from src.api.schemas import FlywheelRunStatus
from src.api.triggers.models import RetrainingTrigger, TriggerExecution
from src.api.triggers.schemas import TriggerCreateRequest, TriggerUpdateRequest

logger = logging.getLogger(__name__)


def mark_execution_as_failed(db: Database, execution: dict, error_message: str) -> None:
    """
    Mark an execution as failed with proper timestamps.

    Args:
        db: MongoDB database instance
        execution: Execution document dict
        error_message: Error message to record
    """
    now = datetime.utcnow()
    duration = None
    start = execution.get("execution_time")
    if start:
        duration = (now - start).total_seconds()

    db.trigger_executions.update_one(
        {"_id": execution["_id"]},
        {
            "$set": {
                "status": "failed",
                "error": error_message,
                "completed_at": now,
                "total_duration_seconds": duration,
            }
        },
    )
    logger.warning(f"Marked execution {execution['_id']} as failed: {error_message}")


def is_execution_stale(db: Database, execution: dict) -> bool:
    """
    Check if an execution is stale (orphaned after service restart).

    An execution is considered stale if:
    - It has a linked job that has completed/failed/cancelled (and is the final job)
    - It's been pending for > 3 hours with no job IDs

    Handles scenarios:
    - Same job for eval and training: Check that single job
    - Separate eval and training jobs: Wait for training job to start before marking stale
    - Only eval job: Wait for training to potentially start (e.g., metric degradation triggers)

    Args:
        db: MongoDB database instance
        execution: Execution document dict

    Returns:
        True if execution is stale and should be marked as failed.
    """
    terminal_statuses = [
        FlywheelRunStatus.COMPLETED,
        FlywheelRunStatus.FAILED,
        FlywheelRunStatus.CANCELLED,
    ]

    eval_job_id = execution.get("eval_job_id")
    training_job_id = execution.get("training_job_id")
    jobs_are_same = eval_job_id and training_job_id and eval_job_id == training_job_id

    def check_job_status(job_id: str) -> tuple[bool, datetime | None]:
        """Check if a job is in terminal status and return (is_terminal, finished_at)."""
        try:
            job = db.flywheel_runs.find_one({"_id": ObjectId(job_id)})
            if not job:
                return (False, None)
            status = job.get("status")
            finished_at = job.get("finished_at")
            if isinstance(finished_at, datetime):
                return (status in terminal_statuses, finished_at)
            else:
                # Terminal status but no valid finished_at
                return (status in terminal_statuses, None)
        except (InvalidId, TypeError) as e:
            logger.warning(f"Error checking job {job_id} for stale execution: {e}")
            return (False, None)
        except Exception as e:
            logger.error(f"Unexpected error checking job {job_id}: {e}", exc_info=True)
            return (False, None)

    # Case 1: Same job for eval and training
    if jobs_are_same:
        job_id = eval_job_id  # Same as training_job_id
        is_terminal, _ = check_job_status(job_id)
        if is_terminal:
            return True  # Single job finished but execution still active = stale

    # Case 2: Separate eval and training jobs
    elif eval_job_id and training_job_id:
        # Training job is the final step — check it
        is_terminal, _ = check_job_status(training_job_id)
        if is_terminal:
            return True  # Training finished but execution still active = stale
        # Training not terminal yet — execution is still active, not stale

    # Case 3: Only eval job exists (training may start later)
    elif eval_job_id:
        is_terminal, finished_at = check_job_status(eval_job_id)
        if is_terminal:
            if finished_at and datetime.utcnow() - finished_at < timedelta(minutes=5):
                # If eval finished recently (< 5 min), training might be starting — don't mark stale
                return False  # Too recent, training might be starting
            # If eval finished > 5 min ago and still no training job, likely stale
            # Terminal status but no finished_at timestamp — mark as stale
            return True  # Eval finished long ago, no training = stale

    # Case 4: Only training job exists (unusual but possible)
    elif training_job_id:
        is_terminal, _ = check_job_status(training_job_id)
        if is_terminal:
            return True  # Training finished but execution still active = stale

    # Case 5: No jobs assigned yet — stale if pending for > 3 hours
    else:
        execution_time = execution.get("execution_time")
        if execution_time and datetime.utcnow() - execution_time > timedelta(hours=3):
            return True  # Pending for > 3 hours with no job = likely orphaned

    return False


def check_execution_stale_with_retry(
    db: Database, execution: dict, max_wait_seconds: int = 60, retry_interval_seconds: int = 2
) -> bool:
    """
    Check if an execution is stale, with retry logic to handle race conditions.

    When a flywheel run completes, there's a brief window where the execution worker
    is updating the execution status. This function waits and retries to see if the
    execution gets updated before marking it as stale.

    The wait time accounts for:
    - Poll interval (10s): executor polls every 10s to check job status
    - Processing time: time to process results and update execution status
    - Buffer: additional safety margin

    Args:
        db: MongoDB database instance
        execution: Execution document dict
        max_wait_seconds: Maximum time to wait for execution to update (default: 60s)
                         Should be >= poll_interval_seconds (10s) + processing buffer
        retry_interval_seconds: Time between retry checks (default: 2s)

    Returns:
        True if execution is stale and should be marked as failed.
    """

    execution_id = execution.get("_id")
    initial_status = execution.get("status")
    initial_updated = execution.get("completed_at") or execution.get("execution_time")

    # First check: is it potentially stale?
    if not is_execution_stale(db, execution):
        return False  # Not stale, no need to wait

    # Job is completed but execution still "running" - wait to see if it gets updated
    logger.info(
        f"Execution {execution_id} appears stale (job completed but status={initial_status}). "
        f"Waiting up to {max_wait_seconds}s to see if execution gets updated..."
    )

    start_time = time.time()
    retry_count = 0

    while time.time() - start_time < max_wait_seconds:
        time.sleep(retry_interval_seconds)
        retry_count += 1

        # Re-fetch execution from database to check if it was updated
        updated_execution = db.trigger_executions.find_one({"_id": execution_id})
        if not updated_execution:
            # Execution was deleted - consider it handled
            logger.info(f"Execution {execution_id} was deleted, considering it handled")
            return False

        updated_status = updated_execution.get("status")
        updated_completed = updated_execution.get("completed_at")

        # Check if status changed (no longer "running"/"pending")
        if updated_status not in ["running", "pending"]:
            logger.info(
                f"Execution {execution_id} was updated to status '{updated_status}' "
                f"(was '{initial_status}'). Not stale."
            )
            return False  # Execution was updated, not stale

        # Check if completed_at was set (indicates progress)
        if updated_completed and not initial_updated:
            logger.info(
                f"Execution {execution_id} was updated (completed_at set). "
                f"Status may be updating, not stale yet."
            )
            # Continue waiting a bit more to see if status changes
            continue

        # Check if execution is no longer stale (job might have been restarted?)
        if not is_execution_stale(db, updated_execution):
            logger.info(f"Execution {execution_id} is no longer stale after {retry_count} retries")
            return False

        # Log progress every 5 retries
        if retry_count % 5 == 0:
            elapsed = time.time() - start_time
            logger.debug(
                f"Execution {execution_id} still appears stale after {retry_count} retries "
                f"({elapsed:.1f}s elapsed). Status: {updated_status}"
            )

    # After waiting, check one more time
    final_execution = db.trigger_executions.find_one({"_id": execution_id})
    if not final_execution:
        return False  # Execution was deleted

    final_status = final_execution.get("status")
    if final_status not in ["running", "pending"]:
        logger.info(
            f"Execution {execution_id} was updated to '{final_status}' during wait. Not stale."
        )
        return False

    # Still stale after waiting
    logger.warning(
        f"Execution {execution_id} is still stale after {max_wait_seconds}s wait "
        f"(status: {final_status}). Marking as orphaned."
    )
    return True


def _validate_merged_trigger(trigger: RetrainingTrigger, update_fields: dict) -> None:
    """
    Validate that the merged trigger (existing + updates) is valid.

    This prevents partial updates from creating an invalid trigger state.
    For example, changing trigger_type without providing required fields.
    """
    merged_data = trigger.model_dump()
    merged_data.update(update_fields)

    # Remove fields not in RetrainingTrigger constructor
    for field in ["updated_at", "created_at", "id"]:
        merged_data.pop(field, None)

    try:
        RetrainingTrigger(**merged_data)
    except Exception as e:
        raise ValueError(f"Invalid trigger configuration: {e}") from e


def validate_config_for_iterative_finetuning(db: Database, config_name: str, job_type: str) -> None:
    """
    Validate that the configuration supports iterative fine-tuning.

    For model_source='fine_tuned', the configuration must have:
    - job_type='embedding_job' (LLM jobs are not supported)
    - finetuning_type='all_weights' (only supported type for iterative training)

    Args:
        db: MongoDB database instance
        config_name: Name of the configuration to validate
        job_type: Type of job (embedding_job or llm_job)

    Raises:
        ValueError: If configuration doesn't support iterative fine-tuning
    """
    # Validate job_type: iterative fine-tuning is only supported for embedding jobs
    if job_type != "embedding_job":
        raise ValueError(
            f"model_source='fine_tuned' is only supported for embedding_job, "
            f"not for {job_type}. For llm_job, use model_source='base'."
        )

    # Get configuration from database
    config = db.configurations.find_one({"name": config_name})
    if not config:
        raise ValueError(
            f"Configuration '{config_name}' not found. "
            "Please create the configuration before creating a trigger."
        )

    # Get training_config from config_data
    config_data = config.get("config_data") or {}
    training_config = (
        config_data.get("training_config", {}) if isinstance(config_data, dict) else {}
    )
    finetuning_type = training_config.get("finetuning_type")

    # Validate finetuning_type for iterative training
    if finetuning_type != "all_weights":
        raise ValueError(
            f"Configuration '{config_name}' has finetuning_type='{finetuning_type}'. "
            "Iterative fine-tuning (model_source='fine_tuned') currently only supports "
            "finetuning_type='all_weights'. Please update the configuration or use "
            "model_source='base' instead."
        )


class TriggerService:
    """Service for managing triggers."""

    def __init__(self, db: Database):
        self.db = db

    def _validate_config_for_iterative_finetuning(self, config_name: str, job_type: str) -> None:
        """
        Validate that the configuration supports iterative fine-tuning.

        This is a convenience wrapper around the shared validate_config_for_iterative_finetuning function.

        Args:
            config_name: Name of the configuration to validate
            job_type: Type of job (embedding_job or llm_job)

        Raises:
            ValueError: If configuration doesn't support iterative fine-tuning
        """
        validate_config_for_iterative_finetuning(self.db, config_name, job_type)

    def create_trigger(self, request: TriggerCreateRequest) -> RetrainingTrigger:
        """Create a new trigger."""
        trigger = RetrainingTrigger(
            name=request.name,
            description=request.description,
            workload_id=request.workload_id,
            client_id=request.client_id,
            job_type=request.job_type,
            trigger_type=request.trigger_type,
            eval_schedule=request.eval_schedule,
            metric_thresholds=request.metric_thresholds,
            min_new_records=request.min_new_records,
            enable_data_ingestion=request.enable_data_ingestion,
            ingestion_timeout_minutes=request.ingestion_timeout_minutes,
            ingestion_config=request.ingestion_config,
            data_window_months=request.data_window_months,
            config_name=request.config_name,
            reference_job_id=request.reference_job_id,
            model_source=request.model_source,
            enabled=request.enabled,
            max_consecutive_failures=request.max_consecutive_failures,
        )

        # Validate configuration compatibility for fine_tuned model_source
        if trigger.model_source == "fine_tuned":
            self._validate_config_for_iterative_finetuning(trigger.config_name, trigger.job_type)

        # Save to database (unique index on name handles race conditions)
        try:
            result = self.db.retraining_triggers.insert_one(trigger.to_mongo())
            trigger.id = result.inserted_id
        except DuplicateKeyError as e:
            raise ValueError(f"Trigger with name '{request.name}' already exists") from e

        logger.info(f"Created trigger: {trigger.name} (ID: {trigger.id})")
        return trigger

    def get_trigger(self, trigger_id: str) -> RetrainingTrigger | None:
        """Get trigger by ID."""
        try:
            doc = self.db.retraining_triggers.find_one({"_id": ObjectId(trigger_id)})
            return RetrainingTrigger.from_mongo(doc) if doc else None
        except InvalidId:
            return None

    def list_triggers(
        self,
        workload_id: str | None = None,
        client_id: str | None = None,
        job_type: str | None = None,
        trigger_type: str | None = None,
        config_name: str | None = None,
        model_source: str | None = None,
        last_execution_status: str | None = None,
        enabled: bool | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[RetrainingTrigger], int]:
        """
        List triggers with optional filtering.

        Args:
            workload_id: Filter by workload
            client_id: Filter by client
            job_type: Filter by job type (embedding_job, llm_job, aoi_job, its_job)
            trigger_type: Filter by trigger type (data_threshold, metric_degradation, schedule)
            config_name: Filter by configuration name
            model_source: Filter by model source (base, fine_tuned)
            last_execution_status: Filter by last execution status (success, failure, triggered_retraining)
            enabled: Filter by enabled status
            skip: Pagination offset
            limit: Pagination limit

        Returns:
            Tuple of (list of triggers, total count)
        """
        # Build filter
        filter_query = {}
        if workload_id:
            filter_query["workload_id"] = workload_id
        if client_id:
            filter_query["client_id"] = client_id
        if job_type:
            filter_query["job_type"] = job_type
        if trigger_type:
            filter_query["trigger_type"] = trigger_type
        if config_name:
            filter_query["config_name"] = config_name
        if model_source:
            filter_query["model_source"] = model_source
        if last_execution_status:
            filter_query["last_execution_status"] = last_execution_status
        if enabled is not None:
            filter_query["enabled"] = enabled

        # Get total count
        total = self.db.retraining_triggers.count_documents(filter_query)

        # Get triggers
        cursor = (
            self.db.retraining_triggers.find(filter_query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )

        triggers = []
        for doc in cursor:
            trigger = RetrainingTrigger.from_mongo(doc)
            if trigger:
                triggers.append(trigger)

        return triggers, total

    def update_trigger(
        self, trigger_id: str, request: TriggerUpdateRequest
    ) -> RetrainingTrigger | None:
        """Update trigger."""
        try:
            trigger_oid = ObjectId(trigger_id)
        except InvalidId:
            return None

        # Get existing trigger
        trigger = self.get_trigger(trigger_id)
        if not trigger:
            return None

        # Build update dict (only non-None fields)
        update_fields = {}
        if request.name is not None:
            # Check name uniqueness
            existing = self.db.retraining_triggers.find_one(
                {"name": request.name, "_id": {"$ne": trigger_oid}}
            )
            if existing:
                raise ValueError(f"Trigger with name '{request.name}' already exists")
            update_fields["name"] = request.name

        if request.description is not None:
            update_fields["description"] = request.description
        if request.eval_schedule is not None:
            update_fields["eval_schedule"] = request.eval_schedule
        if request.metric_thresholds is not None:
            update_fields["metric_thresholds"] = request.metric_thresholds
        if request.min_new_records is not None:
            update_fields["min_new_records"] = request.min_new_records
        if request.enable_data_ingestion is not None:
            update_fields["enable_data_ingestion"] = request.enable_data_ingestion
        if request.ingestion_timeout_minutes is not None:
            update_fields["ingestion_timeout_minutes"] = request.ingestion_timeout_minutes
        if request.ingestion_config is not None:
            update_fields["ingestion_config"] = request.ingestion_config.model_dump()
        if request.data_window_months is not None:
            update_fields["data_window_months"] = request.data_window_months
        if request.config_name is not None:
            update_fields["config_name"] = request.config_name
        if request.reference_job_id is not None:
            update_fields["reference_job_id"] = request.reference_job_id
        if request.model_source is not None:
            update_fields["model_source"] = request.model_source
        if request.enabled is not None:
            update_fields["enabled"] = request.enabled
        if request.max_consecutive_failures is not None:
            update_fields["max_consecutive_failures"] = request.max_consecutive_failures

        if not update_fields:
            return trigger  # No changes

        update_fields["updated_at"] = datetime.utcnow()

        # Validate merged trigger before persisting
        _validate_merged_trigger(trigger, update_fields)

        # Validate configuration if model_source is being changed to fine_tuned
        new_model_source = update_fields.get("model_source", trigger.model_source)
        new_config_name = update_fields.get("config_name", trigger.config_name)
        if new_model_source == "fine_tuned":
            self._validate_config_for_iterative_finetuning(new_config_name, trigger.job_type)

        # Update in database
        self.db.retraining_triggers.update_one({"_id": trigger_oid}, {"$set": update_fields})

        logger.info(f"Updated trigger: {trigger_id}")
        return self.get_trigger(trigger_id)

    def delete_trigger(self, trigger_id: str) -> bool:
        """Delete trigger."""
        try:
            result = self.db.retraining_triggers.delete_one({"_id": ObjectId(trigger_id)})
        except InvalidId:
            return False

        if result.deleted_count > 0:
            logger.info(f"Deleted trigger: {trigger_id}")
            return True

        return False

    def get_executions(
        self, trigger_id: str | None = None, skip: int = 0, limit: int = 100
    ) -> tuple[list[TriggerExecution], int]:
        """Get trigger executions."""
        filter_query = {}
        if trigger_id:
            filter_query["trigger_id"] = trigger_id

        # Get total count
        total = self.db.trigger_executions.count_documents(filter_query)

        # Get executions
        cursor = (
            self.db.trigger_executions.find(filter_query)
            .sort("execution_time", -1)
            .skip(skip)
            .limit(limit)
        )

        executions = []
        for doc in cursor:
            execution = TriggerExecution.from_mongo(doc)
            if execution:
                executions.append(execution)

        return executions, total
