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
"""High-level repository for all database writes performed inside Celery tasks.

This indirection gives the tasks an intention-revealing API while
centralising MongoDB specifics in one place.  It also enables unit tests that
mock **one** narrow surface instead of many scattered ``db.<collection>``
calls.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from bson import ObjectId

from src.api.db import get_db, init_db
from src.api.models import (
    FlywheelRunStatus,
    LLMJudgeRun,
    NIMCustomization,
    NIMEvaluation,
    NIMRun,
    NIMRunStatus,
)
from src.api.schemas import DeploymentStatus

# NOTE: we only import the enum for typing -the collections still store raw
# dictionaries produced by ``model.to_mongo()`` so they stay backward
# compatible with existing data.

_db_manager: TaskDBManager | None = None


def get_db_manager() -> TaskDBManager:
    """Get the database manager instance, creating it if necessary."""
    global _db_manager
    if _db_manager is None:
        _db_manager = TaskDBManager()
    return _db_manager


def _deft_merge(fields: dict[str, Any]) -> list[dict[str, Any]]:
    """Update pipeline that merges ``fields`` into the ``deft`` subdocument.

    Written as a pipeline rather than a plain ``$set`` because neither simple
    form is correct here:

    * Dotted paths (``deft.run_id``) fail outright when the parent is null --
      "Cannot create field 'run_id' in element {deft: null}" -- and
      ``FlywheelRun.to_mongo()`` is a plain ``model_dump()``, so every run is
      inserted with an explicit ``deft: null``. Documents already in Mongo carry
      it too, so no serialization change can fix them.
    * Replacing the whole subdocument (``{"$set": {"deft": {...}}}``) works on a
      null parent but silently drops every sibling -- including the
      per-iteration metrics this block is meant to accumulate, and any progress
      already recorded when only the run id is being written.

    ``$mergeObjects`` over a null-safe base does both: it treats a null or
    missing parent as ``{}`` and preserves keys it is not writing. ``$literal``
    keeps the payload as data -- DEFT's ``detail`` is free-form, and an
    unwrapped key beginning with ``$`` would otherwise be read as an operator.
    """
    return [
        {
            "$set": {
                "deft": {
                    "$mergeObjects": [
                        {"$cond": [{"$eq": [{"$type": "$deft"}, "object"]}, "$deft", {}]},
                        {"$literal": fields},
                    ]
                }
            }
        }
    ]


class TaskDBManager:
    """Lightweight façade over Mongo collections used by Celery tasks."""

    def __init__(self) -> None:
        # Lazily resolve the singleton Database instance - init_db() must have
        # been called from the worker bootstrap code before any method is used.
        try:
            db = get_db()
        except RuntimeError:
            # Database not yet initialised (e.g. during unit-test import). Do it now.
            db = init_db()
        self._db = db
        self._flywheel_runs = db.flywheel_runs
        self._nims = db.nims
        self._evaluations = db.evaluations
        self._customizations = db.customizations
        self.llm_judge_runs = db.llm_judge_runs

    @property
    def db(self):
        """Access to the database instance for direct queries when needed."""
        return self._db

    # ---------------------------------------------------------------------
    # Flywheel-run helpers
    # ---------------------------------------------------------------------

    def update_flywheel_run_status(
        self, flywheel_run_id: str | ObjectId, status: FlywheelRunStatus
    ) -> None:
        """Update the status of a FlywheelRun document."""
        # Only update if there is no error on the flywheel run document.
        self._flywheel_runs.update_one(
            {"_id": ObjectId(flywheel_run_id), "error": None},
            {"$set": {"status": status}},
        )

    def record_deft_progress(
        self,
        flywheel_run_id: str | ObjectId,
        deft_run_id: str,
        progress: dict[str, Any] | None,
        progress_at: datetime,
    ) -> None:
        """Store the DEFT run id and its last-changed progress on a FlywheelRun.

        Called only when the payload actually CHANGES, not on every poll: the
        training wait loop polls every 15s for hours, and a write per poll would
        be thousands of no-op updates for one run.
        """
        self._flywheel_runs.update_one(
            {"_id": ObjectId(flywheel_run_id)},
            _deft_merge(
                {
                    "run_id": deft_run_id,
                    "progress": progress,
                    "progress_at": progress_at,
                }
            ),
        )

    def record_deft_fields(self, flywheel_run_id: str | ObjectId, fields: dict[str, Any]) -> None:
        """Merge arbitrary keys into the ``deft`` block.

        One writer for every stage rather than a method per bucket: each stage
        owns different keys, they all merge the same way, and a new field should
        not need a new method here. The named helpers below stay because their
        call sites carry semantics worth naming (identity, progress, stage);
        anything else uses this.
        """
        if not fields:
            return
        self._flywheel_runs.update_one(
            {"_id": ObjectId(flywheel_run_id)},
            _deft_merge(fields),
        )

    def record_deft_stage(
        self, flywheel_run_id: str | ObjectId, stage: str, stage_started_at: datetime
    ) -> None:
        """Record which pipeline stage a DEFT run has entered.

        `status` alone cannot answer "where is this run?": snapshot, training,
        the offline gate and registration all render as ``running``, so four
        very differently-shaped waits are indistinguishable from the API. The
        stage name is the one thing the task layer always knows and never
        published.

        ``stage_started_at`` makes a stuck stage visible without a second
        request -- age is computable client-side, where ``progress_at`` only
        moves for the training stage.
        """
        self._flywheel_runs.update_one(
            {"_id": ObjectId(flywheel_run_id)},
            _deft_merge({"stage": stage, "stage_started_at": stage_started_at}),
        )

    def record_deft_run_id(self, flywheel_run_id: str | ObjectId, deft_run_id: str) -> None:
        """Store DEFT's run id alone, before any progress has been reported.

        Identity must not depend on progress. A run that hangs or dies before
        its first progress report is exactly when an operator needs the run id
        to go and look at DEFT -- and it is the case where the progress writer
        never fires, because it declines to persist a non-dict payload.

        Merged rather than replacing the block, so it creates or updates
        ``deft.run_id`` without disturbing a ``progress`` already recorded.
        See :func:`_deft_merge` for why a dotted path cannot be used here.
        """
        self._flywheel_runs.update_one(
            {"_id": ObjectId(flywheel_run_id)},
            _deft_merge({"run_id": deft_run_id}),
        )

    def mark_flywheel_run_completed(
        self, flywheel_run_id: str | ObjectId, finished_at: datetime
    ) -> None:
        """Mark a FlywheelRun as completed.
        Only update if there is no error on the flywheel run document.
        """
        self._flywheel_runs.update_one(
            {"_id": ObjectId(flywheel_run_id), "error": None},
            {"$set": {"finished_at": finished_at, "status": FlywheelRunStatus.COMPLETED}},
        )

    def mark_flywheel_run_error(
        self, flywheel_run_id: str | ObjectId, error_msg: str, finished_at: datetime | None = None
    ) -> None:
        """Save an *error* status onto a FlywheelRun document.

        Only update if there is no error on the flywheel run document.
        """
        self._flywheel_runs.update_one(
            {
                "_id": ObjectId(flywheel_run_id),
                "error": None,
            },
            {
                "$set": {
                    "error": error_msg,
                    "status": FlywheelRunStatus.FAILED,
                    "finished_at": finished_at,
                }
            },
        )

    def mark_flywheel_run_cancelled(
        self,
        flywheel_run_id: str | ObjectId,
        error_msg: str,
    ) -> None:
        """Mark a FlywheelRun as cancelled.

        Only update if there is no error on the flywheel run document.
        """
        update_fields = {"status": FlywheelRunStatus.CANCELLED}
        update_fields["finished_at"] = datetime.utcnow()
        if error_msg:
            update_fields["error"] = error_msg
        self._flywheel_runs.update_one(
            {"_id": ObjectId(flywheel_run_id), "finished_at": None},
            {"$set": update_fields},
        )

    def is_flywheel_run_cancelled(self, flywheel_run_id: str | ObjectId) -> bool:
        """Check if a FlywheelRun is cancelled."""
        doc = self._flywheel_runs.find_one({"_id": ObjectId(flywheel_run_id)}, {"status": 1})
        return doc and doc.get("status") == FlywheelRunStatus.CANCELLED

    def set_flywheel_started(self, flywheel_run_id: str | ObjectId) -> None:
        """Set the started_at timestamp for a Flywheel run if not already set."""
        self._flywheel_runs.update_one(
            {"_id": ObjectId(flywheel_run_id), "started_at": None},
            {"$set": {"started_at": datetime.utcnow()}},
        )

    # ---------------------------------------------------------------------
    # NIM-run helpers
    # ---------------------------------------------------------------------
    def create_nim_run(self, nim_run: NIMRun) -> ObjectId:
        """Insert a new NIMRun and return its ``ObjectId``."""
        result = self._nims.insert_one(nim_run.to_mongo())
        return result.inserted_id

    def set_nim_status(
        self,
        nim_id: ObjectId,
        status: NIMRunStatus,
        *,
        error: str | None = None,
        deployment_status: DeploymentStatus | None = None,
    ) -> None:
        """Update the status (and optional error/deployment info) of a NIM run."""
        update: dict[str, Any] = {"status": status}
        if error is not None:
            update["error"] = error
        if deployment_status is not None:
            update["deployment_status"] = deployment_status
        self._nims.update_one({"_id": nim_id}, {"$set": update})

    def update_nim_deployment_status(
        self, nim_id: ObjectId, deployment_status: DeploymentStatus, runtime_seconds: float
    ) -> None:
        """Update the deployment status of a NIM run."""
        self._nims.update_one(
            {"_id": nim_id},
            {"$set": {"deployment_status": deployment_status, "runtime_seconds": runtime_seconds}},
        )

    def set_nim_started(self, nim_id: ObjectId, started_at: datetime) -> None:
        """Set the started_at timestamp for a NIM run if not already set."""
        self._nims.update_one(
            {"_id": nim_id, "started_at": None},
            {"$set": {"started_at": started_at}},
        )

    def mark_nim_completed(self, nim_id: ObjectId, started_at: datetime) -> None:
        """Mark a NIM run as completed once the deployment has been shut down.

        Only update if there is no error on the NIM run document.
        """
        finished_time = datetime.utcnow()
        runtime_seconds: float = 0.0
        if started_at:
            runtime_seconds = (finished_time - started_at).total_seconds()
        self._nims.update_one(
            {"_id": nim_id, "error": None},
            {
                "$set": {
                    "status": NIMRunStatus.COMPLETED,
                    "deployment_status": DeploymentStatus.COMPLETED,
                    "finished_at": finished_time,
                    "runtime_seconds": runtime_seconds,
                }
            },
        )

    def mark_nim_cancelled(self, nim_id: ObjectId, error_msg: str) -> None:
        """Mark a NIM run as cancelled.

        Only update if there is no error on the NIM run document.
        """
        finished_time = datetime.utcnow()
        self._nims.update_one(
            {"_id": nim_id, "error": None, "finished_at": None},
            {
                "$set": {
                    "status": NIMRunStatus.CANCELLED,
                    "deployment_status": DeploymentStatus.CANCELLED,
                    "finished_at": finished_time,
                    "error": error_msg,
                }
            },
        )

    def mark_nim_error(self, nim_id: ObjectId, error_msg: str) -> None:
        """Mark a NIM run as failed.

        Only update if there is no error on the NIM run document.
        """
        self._nims.update_one(
            {
                "_id": nim_id,
                "error": None,  # Only match documents where error is None
            },
            {
                "$set": {
                    "error": error_msg,
                    "status": NIMRunStatus.FAILED,
                    "deployment_status": DeploymentStatus.FAILED,
                    "finished_at": datetime.utcnow(),
                }
            },
        )

    def find_nim_run(self, flywheel_run_id: str, model_name: str) -> Mapping[str, Any] | None:
        """Find a NIM run by its associated flywheel run ID and model name."""
        return self._nims.find_one(
            {"flywheel_run_id": ObjectId(flywheel_run_id), "model_name": model_name}
        )

    def mark_all_nims_status(
        self, flywheel_run_id: str | ObjectId, status: NIMRunStatus, error_msg: str | None = None
    ) -> None:
        """Mark all NIMs associated with a flywheel run as failed.

        Only update if there is no error on the NIM run document.
        Args:
            flywheel_run_id: ID of the flywheel run whose NIMs should be marked as failed
            error_msg: Error message to set on the NIMs
        """
        self._nims.update_many(
            {
                "flywheel_run_id": ObjectId(flywheel_run_id),
                "error": None,  # Only update NIMs that don't already have an error
            },
            {
                "$set": {
                    "error": error_msg,
                    "status": status,
                    "finished_at": datetime.utcnow(),
                }
            },
        )

    # ------------------------------------------------------------------
    # Evaluation helpers
    # ------------------------------------------------------------------
    def insert_evaluation(self, evaluation: NIMEvaluation) -> ObjectId:
        """Insert a new NIMEvaluation and return its ObjectId."""
        self._evaluations.insert_one(evaluation.to_mongo())
        return evaluation.id  # type: ignore[return-value]

    def update_evaluation(self, eval_id: ObjectId, update_fields: dict[str, Any]) -> None:
        """Update a NIMEvaluation."""
        self._evaluations.update_one({"_id": eval_id}, {"$set": update_fields})

    # ------------------------------------------------------------------
    # Customization helpers
    # ------------------------------------------------------------------
    def insert_customization(self, customization: NIMCustomization) -> ObjectId:
        self._customizations.insert_one(customization.to_mongo())
        return customization.id  # type: ignore[return-value]

    def update_customization(self, custom_id: ObjectId, update_fields: dict[str, Any]) -> None:
        """Update a NIMCustomization."""
        self._customizations.update_one({"_id": custom_id}, {"$set": update_fields})

    def find_customization(self, workload_id: str, model_name: str):
        """Find a NIMCustomization by its workload ID and model name."""
        return self._customizations.find_one(
            {"workload_id": workload_id, "customized_model": model_name}
        )

    # ------------------------------------------------------------------
    # LLM Judge helpers
    # ------------------------------------------------------------------
    def create_llm_judge_run(self, llm_judge_run: LLMJudgeRun) -> ObjectId:
        """Insert a new LLMJudgeRun and return its ObjectId."""
        result = self.llm_judge_runs.insert_one(llm_judge_run.to_mongo())
        return result.inserted_id

    def update_llm_judge_deployment_status(
        self, llm_judge_id: ObjectId, deployment_status: DeploymentStatus
    ) -> None:
        """Update the deployment status of an LLM judge run."""
        self.llm_judge_runs.update_one(
            {"_id": llm_judge_id},
            {"$set": {"deployment_status": deployment_status}},
        )

    def mark_llm_judge_error(self, llm_judge_id: ObjectId, error_msg: str) -> None:
        """Mark an LLM judge run as having encountered an error."""
        self.llm_judge_runs.update_one(
            {"_id": llm_judge_id},
            {
                "$set": {
                    "error": error_msg,
                    "deployment_status": DeploymentStatus.FAILED,
                }
            },
        )

    def find_llm_judge_run(self, flywheel_run_id: str | ObjectId) -> Mapping[str, Any] | None:
        """Find an LLM judge run by its associated flywheel run ID."""
        return self.llm_judge_runs.find_one({"flywheel_run_id": ObjectId(flywheel_run_id)})

    # ------------------------------------------------------------------
    # Job deletion helpers
    # ------------------------------------------------------------------
    def get_flywheel_run(self, job_id: str | ObjectId) -> Mapping[str, Any] | None:
        """Get a flywheel run by ID.

        Args:
            job_id: ID of the flywheel run to retrieve

        Returns:
            The flywheel run document if found, None otherwise
        """
        return self._flywheel_runs.find_one({"_id": ObjectId(job_id)})

    def find_running_flywheel_runs(self) -> list[Mapping[str, Any]]:
        """Find all flywheel runs that are currently in a non-terminal state.

        Returns:
            List of flywheel run documents with PENDING, RUNNING or PROMOTING
            status (PROMOTING = AOI run whose candidate is mid-rollout).
        """
        running_statuses = [
            FlywheelRunStatus.PENDING.value,
            FlywheelRunStatus.RUNNING.value,
            FlywheelRunStatus.PROMOTING.value,
        ]
        return list(self._flywheel_runs.find({"status": {"$in": running_statuses}}))

    def find_running_nims_for_flywheel(self, flywheel_run_id: ObjectId) -> list[Mapping[str, Any]]:
        """Find all NIMs with RUNNING or PENDING status for a flywheel run.

        Args:
            flywheel_run_id: ID of the flywheel run to find NIMs for

        Returns:
            List of NIM documents with RUNNING or PENDING status
        """
        return list(
            self._nims.find(
                {
                    "flywheel_run_id": flywheel_run_id,
                    "status": {
                        "$in": [
                            NIMRunStatus.RUNNING.value,
                            NIMRunStatus.PENDING.value,
                        ]
                    },
                }
            )
        )

    def mark_llm_judge_cancelled(self, flywheel_run_id: ObjectId, error_msg: str) -> None:
        """Mark an LLM judge run as cancelled.

        Args:
            flywheel_run_id: ID of the flywheel run associated with the LLM judge
            error_msg: Optional error message to set
        """
        update_fields = {"deployment_status": DeploymentStatus.CANCELLED.value}
        update_fields["error"] = error_msg

        self.llm_judge_runs.update_one(
            {"flywheel_run_id": flywheel_run_id},
            {"$set": update_fields},
        )

    def find_nims_for_job(self, job_id: ObjectId) -> list[Mapping[str, Any]]:
        """Find all NIMs for a given job.

        Args:
            job_id: ID of the job to find NIMs for

        Returns:
            List of NIM documents associated with the job
        """
        return list(self._nims.find({"flywheel_run_id": job_id}))

    def find_customizations_for_nim(self, nim_id: ObjectId) -> list[Mapping[str, Any]]:
        """Find all customizations for a given NIM.

        Args:
            nim_id: ID of the NIM to find customizations for

        Returns:
            List of customization documents associated with the NIM
        """
        return list(self._customizations.find({"nim_id": nim_id}))

    def find_evaluations_for_nim(self, nim_id: ObjectId) -> list[Mapping[str, Any]]:
        """Find all evaluations for a given NIM.

        Args:
            nim_id: ID of the NIM to find evaluations for

        Returns:
            List of evaluation documents associated with the NIM
        """
        return list(self._evaluations.find({"nim_id": nim_id}))

    def delete_job_records(self, job_id: ObjectId) -> None:
        """Delete all MongoDB records related to a job.

        This deletes all associated records in the following collections:
        - evaluations
        - customizations
        - nims
        - llm_judge_runs
        - flywheel_runs

        Args:
            job_id: ID of the job to delete records for
        """
        # First get all NIM IDs for this job (evaluations and customizations use nim_id as foreign key)
        nim_records = list(self._nims.find({"flywheel_run_id": job_id}, {"_id": 1}))
        nim_ids = [nim["_id"] for nim in nim_records]

        # Delete evaluations and customizations using nim_id
        if nim_ids:
            self._evaluations.delete_many({"nim_id": {"$in": nim_ids}})
            self._customizations.delete_many({"nim_id": {"$in": nim_ids}})

        # Delete remaining records using flywheel_run_id
        self._nims.delete_many({"flywheel_run_id": job_id})
        self.llm_judge_runs.delete_many({"flywheel_run_id": job_id})
        self._flywheel_runs.delete_one({"_id": job_id})
