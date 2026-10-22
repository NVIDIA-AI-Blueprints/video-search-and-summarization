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

"""Trigger executor - orchestrates trigger execution with type-specific logic.

This is the SYNCHRONOUS version for use in Celery tasks.
"""

import logging
from datetime import UTC, datetime, timedelta

from bson import ObjectId
from pymongo.database import Database

from src.api.triggers.models import NIMExecutionResult, RetrainingTrigger, TriggerExecution
from src.api.triggers.service import (
    check_execution_stale_with_retry,
    mark_execution_as_failed,
    validate_config_for_iterative_finetuning,
)
from src.job_types import is_deft_job_type
from src.lib.integration.es_client import get_available_records_count
from src.tasks.ingestion import run_data_ingestion
from src.tasks.triggers.date_window import DataWindowCalculator
from src.tasks.triggers.model_resolver import ModelResolver, TargetModels

logger = logging.getLogger(__name__)


# AOI runs are fire-and-forget (no ``_wait_for_job``), so the trigger-execution
# staleness logic never runs against them. Without a reclaim, a run wedged in
# PENDING (a worker never claimed it) or RUNNING/PROMOTING (worker crash, or a
# rollout that never terminates) would block EVERY future AOI trigger for that
# workload forever. These are backstop ceilings, not tight SLAs.
#
# CAVEAT — the active ceiling is deliberately GENEROUS because a legitimate
# rollout spans shadow→canary→full and can legitimately sit in PROMOTING for a
# long time awaiting a HITL approval gate. Age here is measured from creation,
# not a progress heartbeat, so a ceiling shorter than the max legit rollout
# (incl. human approval latency) would wrongly reclaim a healthy run. Deployments
# with long HITL SLAs should raise this; the proper long-term fix is a
# progress-heartbeat field on the run. A PENDING run, by contrast, should be
# claimed within seconds, so its ceiling is short and safe.
AOI_PENDING_RUN_STALE_AFTER_S = 3600.0  # 1h — never claimed off the queue
AOI_ACTIVE_RUN_STALE_AFTER_S = 7 * 24 * 3600.0  # 7d — crash backstop, > max legit rollout


class TriggerExecutor:
    """Execute triggers with type-specific behavior (sync version for Celery)."""

    def __init__(self, db: Database):
        self.db = db
        self.model_resolver = ModelResolver(db)
        self.date_window_calc = DataWindowCalculator()

    def _get_last_successful_training_date(self, trigger_id: str) -> datetime | None:
        """Get the completed_at timestamp from the last successful training execution."""
        execution = self.db.trigger_executions.find_one(
            {
                "trigger_id": trigger_id,
                "status": "completed",
                "triggered_training": True,
            },
            sort=[("completed_at", -1)],
        )
        if execution and execution.get("completed_at"):
            return execution["completed_at"]
        return None

    def _check_existing_execution(self, trigger_id: str) -> dict | None:
        """
        Check if there's already a running or pending execution for this trigger.

        Also handles stale executions (orphaned after service restart) by checking
        if the linked job has completed/failed.
        """
        existing = self.db.trigger_executions.find_one(
            {"trigger_id": trigger_id, "status": {"$in": ["running", "pending"]}}
        )

        if existing:
            # Use retry logic to handle race conditions where job completed but execution
            # is still being updated by the worker
            if check_execution_stale_with_retry(self.db, existing):
                # Mark stale execution as failed
                mark_execution_as_failed(
                    self.db, existing, "Orphaned execution after service restart"
                )
                return None  # Allow new execution

        return existing

    def _create_skipped_execution(
        self, trigger: RetrainingTrigger, skip_reason: str
    ) -> TriggerExecution:
        """Create and save a skipped execution record."""
        execution = TriggerExecution(
            trigger_id=str(trigger.id),
            trigger_name=trigger.name,
            trigger_type=trigger.trigger_type,
            execution_time=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            status="skipped",
            skip_reason=skip_reason,
            triggered_training=False,
        )
        self._save_execution(execution)
        return execution

    def execute(
        self, trigger: RetrainingTrigger, execution_id: str | None = None
    ) -> TriggerExecution:
        """
        Execute trigger based on its type.

        Type-specific behavior:
        - data_threshold: Check min records → if met: eval + train
        - metric_degradation: Ingest → eval → compare → train if metrics fail
        - schedule: Ingest → eval + train (always)

        Args:
            trigger: The trigger to execute
            execution_id: Optional pre-created execution ID (for API-triggered executions)

        Returns:
            TriggerExecution with status "skipped" if previous execution is still running
        """
        from bson import ObjectId

        start_time = datetime.utcnow()

        # If execution_id is provided, load and update the existing execution record
        if execution_id:
            execution_doc = self.db.trigger_executions.find_one({"_id": ObjectId(execution_id)})
            if execution_doc:
                execution = TriggerExecution.from_mongo(execution_doc)
                execution.status = "running"
                execution.execution_time = start_time
            else:
                # Execution record not found, create a new one
                execution = TriggerExecution(
                    trigger_id=str(trigger.id),
                    trigger_name=trigger.name,
                    trigger_type=trigger.trigger_type,
                    execution_time=start_time,
                    status="running",
                )
        else:
            # No pre-created execution (Celery Beat path)
            # Check if there's already a running or pending execution for this trigger
            existing = self._check_existing_execution(str(trigger.id))
            if existing:
                skip_reason = f"Previous execution {existing['_id']} is still {existing['status']}"
                logger.warning(f"Skipping trigger '{trigger.name}': {skip_reason}")
                return self._create_skipped_execution(trigger, skip_reason)

            # Create new execution record
            execution = TriggerExecution(
                trigger_id=str(trigger.id),
                trigger_name=trigger.name,
                trigger_type=trigger.trigger_type,
                execution_time=start_time,
                status="running",
            )

        try:
            # Save execution record immediately so it's visible to other triggers
            self._save_execution(execution)

            # Step 1: Data ingestion (all types)
            new_records_count = 0
            if trigger.enable_data_ingestion:
                ingestion_start = datetime.utcnow()
                new_records_count = self._trigger_data_ingestion(trigger)
                ingestion_duration = (datetime.utcnow() - ingestion_start).total_seconds()
                execution.new_records_ingested = new_records_count
                execution.ingestion_duration_seconds = ingestion_duration
                self._save_execution(execution)  # Persist ingestion results immediately

            # Step 2: Type-specific execution
            if trigger.trigger_type == "data_threshold":
                # data_threshold: Check records since last training (handles failed training retry)
                self._execute_data_threshold(trigger, execution)

            elif trigger.trigger_type == "metric_degradation":
                self._execute_metric_degradation(trigger, execution)

            elif trigger.trigger_type == "schedule":
                self._execute_schedule(trigger, execution)

            else:
                raise ValueError(f"Unknown trigger type: {trigger.trigger_type}")

            # Mark status based on whether skipped or completed
            if execution.skip_reason:
                execution.status = "skipped"
            else:
                execution.status = "completed"
            execution.completed_at = datetime.utcnow()
            execution.total_duration_seconds = (execution.completed_at - start_time).total_seconds()

            # Update trigger's latest_fine_tuned_models if training was triggered
            if execution.triggered_training and execution.training_job_id:
                self._update_trigger_fine_tuned_models(
                    trigger, execution, execution.training_job_id
                )

        except Exception as e:
            logger.error(f"Trigger execution failed: {e}", exc_info=True)
            execution.status = "failed"
            execution.error = str(e)
            execution.completed_at = datetime.utcnow()
            execution.total_duration_seconds = (execution.completed_at - start_time).total_seconds()
            # Don't re-raise - return the failed execution so trigger status gets updated

        finally:
            # Save execution record
            self._save_execution(execution)

        return execution

    # ------------------------------------------------------------------
    # DEFT-family (``aoi_job`` / ``its_job``) trigger support
    #
    # No ingestion-plugin path: production data lands in the Storage Backend
    # catalog (AL-Archiver side) and DFW only *requests* snapshots, so
    # data_threshold reads the catalog's live stats instead of counting
    # ingested ES records. Both trigger types dispatch WITHOUT waiting for
    # completion — a DEFT run spans the whole promotion rollout (shadow →
    # canary → full), which can take days, and the pipeline already
    # serializes runs.
    # ------------------------------------------------------------------
    @staticmethod
    def _is_deft(trigger: RetrainingTrigger) -> bool:
        """Whether this trigger takes the DEFT branch (``aoi_job`` / ``its_job``).

        A family test, not an equality check: getting it wrong fails silently —
        ``_execute_data_threshold`` would count DEFT records in Elasticsearch,
        which holds none, so the trigger reports "skipped" forever.
        """
        return is_deft_job_type(trigger.job_type)

    def _trigger_use_case(self, trigger: RetrainingTrigger) -> str | None:
        """The ``DeftJobConfig.use_case`` the run this trigger dispatches will use.

        ``RetrainingTrigger`` carries no ``use_case`` field, so it can only come
        from the job config — resolved through the SAME path the dispatched run
        takes (``job_type`` + optional ``config_name`` → ``load_job_configuration``),
        because any other path could name a different store than the run itself
        snapshots. ``config_name`` is optional for DEFT triggers; the loader then
        falls back to the YAML ``<job_type>_config`` block, which is precisely the
        config the run will load too.

        Returns ``None`` when the use case cannot be determined — a named config
        renamed or deleted in Mongo makes the loader RAISE, and that must not be
        mistaken for "no override declared" (see ``_get_aoi_available_records``).
        """
        from src.tasks.tasks import load_job_configuration

        try:
            job_config = load_job_configuration(trigger.job_type, trigger.config_name)
        except Exception as e:
            # Config renamed/deleted out from under the trigger. Don't fail here:
            # the caller decides, using the same fail-closed rule as the snapshot.
            logger.warning(
                "Could not load job config for trigger %s (job_type=%s, config_name=%s): %s",
                trigger.name,
                trigger.job_type,
                trigger.config_name,
                e,
            )
            return None
        return getattr(job_config, "use_case", None)

    def _get_aoi_available_records(self, trigger: RetrainingTrigger) -> int:
        """New-record count from THIS TRIGGER'S Storage Backend catalog stats.

        ``GET /v1/snapshot/stats`` → ``records_since_last_snapshot``: records
        that arrived after the last materialized snapshot. Raises on an
        unreachable backend so the execution fails visibly instead of silently
        skipping.

        The client is built from ``deft_config.for_use_case(<this trigger's use
        case>)``, not from ``DEFTConfig()`` field defaults: a deployment MAY
        point a use case at its own store, and a default-configured client would
        then let the trigger *decide* off one store's record count while the run
        it dispatches snapshots another. The shipped config happens to share one
        store, so this resolves to the same endpoints today — it is the routing
        that must be correct, not the current values. Same fail-closed rule as
        ``SelectAndSnapshotHandler._resolve_store``: an undeclared use case
        raises rather than quietly inheriting.

        KNOWN LIMITATION (not fixed here) — ``records_since_last_snapshot`` is a
        CATALOG-WIDE counter with no use_case/job_type facet, so every DEFT job
        type sharing a Storage Backend shares one counter: an AOI run's snapshot
        zeroes the number the ITS trigger reads, and vice versa, which starves
        the other trigger silently. It reads as "new data since the last run
        started" only while exactly ONE job type uses that counter. A real fix
        needs a per-use-case facet on the Storage Backend stats API (outside this
        repo). Routing per use case, below, at least keeps job types on separate
        stores from sharing a counter at all.
        """
        # Lazy import: constructed per execution; NMP deployments never hit it.
        from src.backends.deft.storage_backend_client import StorageBackendClient
        from src.config import settings

        deft_config = getattr(settings.system_config, "deft_config", None)
        use_case = None
        if deft_config is None:
            # Bare/NMP settings: exactly one store, so nothing to route. Leave the
            # client on its own default, exactly as before this routing existed --
            # including the catalog, which stays unprefixed on a None use case.
            resolved = None
        else:
            use_case = self._trigger_use_case(trigger)
            if use_case is None:
                # Unresolvable use case is now fatal, where it used to be
                # harmless. It selects the STORAGE CATALOG as well as the
                # endpoints, and an empty `use_case_endpoints` map -- the
                # supported default -- means the declared-membership check below
                # never fires. An ITS trigger would then read
                # `/v1/snapshot/stats`, decide retraining off AOI's record
                # count, and report success doing it.
                raise ValueError(
                    f"Refusing to read catalog stats: could not resolve a use_case for "
                    f"trigger {trigger.name!r} (its job configuration could not be "
                    "loaded). The use case selects the Storage Backend catalog, so "
                    "reading on without one would count another catalog's records."
                )
            declared = getattr(deft_config, "use_case_endpoints", None) or {}
            if declared and use_case not in declared:
                raise ValueError(
                    f"Refusing to read catalog stats: trigger {trigger.name!r} resolved "
                    f"use_case={use_case!r}, which has no entry in "
                    f"deft_config.use_case_endpoints (declared: {sorted(declared)}). "
                    "This deployment declares per-use-case stores, so an undeclared use "
                    "case would silently inherit the top-level endpoints and decide "
                    "retraining off another use case's record count. Add an entry (an "
                    "empty one means 'inherits, deliberately')."
                )
            resolved = deft_config.for_use_case(use_case)

        stats = StorageBackendClient(resolved, use_case=use_case).get_stats()
        return int(stats.get("records_since_last_snapshot") or 0)

    def _find_active_aoi_run(self, trigger: RetrainingTrigger) -> dict | None:
        """An AOI run for this workload that is still in flight, or None.

        Guards double-dispatch in the window before a newly-started run
        materializes its snapshot (which is what resets the
        records_since_last_snapshot counter). A run found to be *stale* (see
        :meth:`_reclaim_if_stale`) is reclaimed and treated as not-in-flight,
        so a crashed/wedged run can never block this trigger forever.
        """
        from src.api.schemas import FlywheelRunStatus

        run = self.db.flywheel_runs.find_one(
            {
                "workload_id": trigger.workload_id,
                "client_id": trigger.client_id,
                "job_type": trigger.job_type,
                "status": {
                    "$in": [
                        FlywheelRunStatus.PENDING.value,
                        FlywheelRunStatus.RUNNING.value,
                        FlywheelRunStatus.PROMOTING.value,
                    ]
                },
            }
        )
        if run is not None and self._reclaim_if_stale(run):
            return None
        return run

    def _reclaim_if_stale(self, run: dict) -> bool:
        """Mark a wedged active run FAILED and return True if it was reclaimed.

        Age is measured from the ObjectId's generation time (a reliable
        creation timestamp with no field dependency), except a RUNNING/PROMOTING
        run also considers ``started_at`` — whichever is more recent. PENDING
        runs use the short ceiling (should be claimed in seconds); active runs
        use the generous rollout ceiling. The status-guarded update makes the
        reclaim itself race-safe.
        """
        from src.api.schemas import FlywheelRunStatus

        status = run.get("status")
        oid = run.get("_id")
        created = oid.generation_time if isinstance(oid, ObjectId) else None
        ref = created
        if status == FlywheelRunStatus.PENDING.value:
            limit = AOI_PENDING_RUN_STALE_AFTER_S
        else:
            limit = AOI_ACTIVE_RUN_STALE_AFTER_S
            started = run.get("started_at")
            if isinstance(started, datetime):
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                ref = max(ref, started) if ref is not None else started
        if ref is None:
            return False  # can't age it → treat as live (safe default)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=UTC)
        if datetime.now(UTC) - ref <= timedelta(seconds=limit):
            return False

        result = self.db.flywheel_runs.update_one(
            {
                "_id": oid,
                "status": {
                    "$in": [
                        FlywheelRunStatus.PENDING.value,
                        FlywheelRunStatus.RUNNING.value,
                        FlywheelRunStatus.PROMOTING.value,
                    ]
                },
            },
            {
                "$set": {
                    "status": FlywheelRunStatus.FAILED.value,
                    "error": (
                        f"Reclaimed: AOI run stale (status={status}, no terminal state "
                        f"within {int(limit)}s); unblocking the trigger"
                    ),
                    "finished_at": datetime.utcnow(),
                }
            },
        )
        if result.modified_count:
            logger.warning("Reclaimed stale AOI run %s (was %s)", oid, status)
            return True
        return False  # someone else transitioned it first — respect that

    def _earlier_active_aoi_run(self, trigger: RetrainingTrigger, my_id) -> dict | None:
        """An OTHER active AOI run for this workload created before ``my_id``."""
        from src.api.schemas import FlywheelRunStatus

        return self.db.flywheel_runs.find_one(
            {
                "workload_id": trigger.workload_id,
                "client_id": trigger.client_id,
                "job_type": trigger.job_type,
                "status": {
                    "$in": [
                        FlywheelRunStatus.PENDING.value,
                        FlywheelRunStatus.RUNNING.value,
                        FlywheelRunStatus.PROMOTING.value,
                    ]
                },
                "_id": {"$lt": my_id},
            }
        )

    def _dispatch_aoi_job(
        self, trigger: RetrainingTrigger, execution: TriggerExecution
    ) -> str | None:
        """Create the FlywheelRun and dispatch the AOI workflow — fire and forget.

        No target-model resolution (the AOI config names its model), no
        runtime overrides (the AOI pipeline takes none), and no completion
        wait: the run's outcome is owned by the pipeline + Promotion
        Controller and visible via /jobs and /promotions.

        Returns the new job id, or ``None`` if the best-effort active-run guard
        (below) caught a concurrent dispatch — callers must treat ``None`` as
        "skipped, did not dispatch".
        """
        from src.api.models import FlywheelRun
        from src.api.schemas import FlywheelRunStatus
        from src.tasks.tasks import run_nim_workflow_dag

        flywheel_run = FlywheelRun(
            workload_id=trigger.workload_id,
            client_id=trigger.client_id,
            job_type=trigger.job_type,
            started_at=None,
            config_name=trigger.config_name,
            num_records=0,
            nims=[],
            status=FlywheelRunStatus.PENDING,
        )
        result = self.db.flywheel_runs.insert_one(flywheel_run.to_mongo())
        job_id = str(result.inserted_id)

        # Best-effort TOCTOU narrowing (NOT a hard guarantee — see below).
        # _find_active_aoi_run→insert is not atomic and there is no unique
        # index, so two near-simultaneous fires (cron + manual, or a retry) can
        # both pass the guard. Re-check for any OTHER active AOI run with an
        # earlier _id; the _id tiebreak is deterministic (never deletes both),
        # so this catches the common interleaving and can only ever help.
        #
        # RESIDUAL WINDOW — accepted, by design. It does NOT fully close the
        # race: FlywheelRun._id is minted client-side at construction, so _id
        # order is independent of *commit* order. If the smaller-_id run commits
        # second, neither verify sees the other and both dispatch. That is
        # tolerated here because it is NOT a correctness bug: AOI execution is
        # already serialized downstream (parent_queue concurrency=1 +
        # wait_for_promotion holds the slot for the whole rollout), so a double
        # dispatch becomes one *deferred, redundant* training generation — wasted
        # compute, never concurrent runs or corrupted state. A real fix (DB
        # unique index / lock) would trade this rare, bounded waste for a
        # standing wedge risk (a missed lock release blocks the trigger), and
        # even an atomic lock can't distinguish a crash from a legitimately long
        # HITL hold without a run heartbeat. REVISIT (with heartbeat-based
        # liveness) only if redundant runs are actually observed, or if AOI
        # dispatch ever becomes correctness-sensitive.
        if self._earlier_active_aoi_run(trigger, result.inserted_id) is not None:
            self.db.flywheel_runs.delete_one({"_id": result.inserted_id})
            logger.info(
                "AOI dispatch for trigger %s lost the active-run race; skipping", trigger.name
            )
            return None

        try:
            run_nim_workflow_dag.delay(
                workload_id=trigger.workload_id,
                flywheel_run_id=job_id,
                client_id=trigger.client_id,
                job_type=trigger.job_type,
                config_name=trigger.config_name,
            )
        except Exception:
            # Never leave an orphaned PENDING run behind: it would wedge
            # _find_active_aoi_run for every future fire of this trigger.
            self.db.flywheel_runs.update_one(
                {"_id": result.inserted_id},
                {
                    "$set": {
                        "status": FlywheelRunStatus.FAILED.value,
                        "error": "Failed to enqueue the AOI workflow task",
                        "finished_at": datetime.utcnow(),
                    }
                },
            )
            raise

        logger.info(f"Dispatched AOI job {job_id} for trigger {trigger.name}")
        execution.eval_job_id = job_id
        execution.training_job_id = job_id
        execution.triggered_training = True
        self._save_execution(execution)
        return job_id

    def _execute_aoi_data_threshold(self, trigger: RetrainingTrigger, execution: TriggerExecution):
        """data_threshold for aoi_job: catalog stats decide, then dispatch."""
        # In-flight guard FIRST — it is a cheap local Mongo read, and during a
        # (potentially days-long) rollout the correct outcome is "skip"
        # regardless of the catalog count. Checking stats first would also
        # turn a transient Storage Backend outage into failed executions that
        # burn max_consecutive_failures while a run is legitimately in flight.
        active = self._find_active_aoi_run(trigger)
        if active:
            execution.skip_reason = (
                f"AOI run {active['_id']} is still in flight "
                f"(status={active.get('status')}); not dispatching another"
            )
            execution.decision_reason = execution.skip_reason
            return

        available_records = self._get_aoi_available_records(trigger)
        execution.available_records_since_last_training = available_records

        logger.info(
            f"AOI data_threshold trigger {trigger.name}: "
            f"records_since_last_snapshot={available_records}, "
            f"threshold={trigger.min_new_records}"
        )

        if available_records < trigger.min_new_records:
            execution.skip_reason = (
                f"Insufficient new catalog records: {available_records} < {trigger.min_new_records}"
            )
            execution.decision_reason = execution.skip_reason
            return

        if self._dispatch_aoi_job(trigger, execution) is None:
            execution.skip_reason = (
                "Lost the active-run race to a concurrent AOI dispatch; "
                "skipped to avoid double-dispatch"
            )
            execution.decision_reason = execution.skip_reason
            return
        execution.decision_reason = (
            f"Data threshold met: {available_records} new catalog records "
            f">= {trigger.min_new_records}"
        )

    def _execute_aoi_schedule(self, trigger: RetrainingTrigger, execution: TriggerExecution):
        """schedule for aoi_job: dispatch unconditionally (unless one is in flight)."""
        active = self._find_active_aoi_run(trigger)
        if active:
            execution.skip_reason = (
                f"AOI run {active['_id']} is still in flight "
                f"(status={active.get('status')}); not dispatching another"
            )
            execution.decision_reason = execution.skip_reason
            return

        if self._dispatch_aoi_job(trigger, execution) is None:
            execution.skip_reason = (
                "Lost the active-run race to a concurrent AOI dispatch; "
                "skipped to avoid double-dispatch"
            )
            execution.decision_reason = execution.skip_reason
            return
        execution.decision_reason = "Scheduled AOI retraining"

    def _execute_data_threshold(self, trigger: RetrainingTrigger, execution: TriggerExecution):
        """
        Execute data threshold trigger.
        Check records since last successful training (handles failed training retry).
        If available_records >= min_new_records → run eval + training.
        """
        if self._is_deft(trigger):
            self._execute_aoi_data_threshold(trigger, execution)
            return

        # Validate config
        if not trigger.enable_data_ingestion:
            raise ValueError(
                f"Trigger '{trigger.name}' is a data_threshold trigger but has "
                "enable_data_ingestion=False."
            )

        if not trigger.ingestion_config:
            raise ValueError(
                f"Trigger '{trigger.name}' is a data_threshold trigger but has no ingestion_config."
            )

        # Get records since last successful training
        last_training_date = self._get_last_successful_training_date(str(trigger.id))
        available_records = get_available_records_count(
            trigger.workload_id, trigger.client_id, last_training_date, trigger.job_type
        )
        execution.available_records_since_last_training = available_records

        logger.info(
            f"data_threshold trigger {trigger.name}: last_training={last_training_date}, "
            f"available_records={available_records}, threshold={trigger.min_new_records}"
        )

        # Check threshold
        if available_records < trigger.min_new_records:
            execution.skip_reason = f"Insufficient records since last training: {available_records} < {trigger.min_new_records}"
            execution.decision_reason = execution.skip_reason
            logger.info(f"Skipping data_threshold trigger {trigger.name}: {execution.skip_reason}")
            return

        # Sufficient data → run single job with eval + training enabled
        target_models = self.model_resolver.resolve(trigger)
        self._populate_nim_results(execution, target_models)
        self._save_execution(execution)  # Persist NIM list immediately

        # Run combined eval + training job for all NIMs
        job_id = self._run_job(trigger, target_models, eval_enabled=True, training_enabled=True)
        execution.eval_job_id = job_id
        execution.training_job_id = job_id  # Same job handles both
        execution.triggered_training = True
        self._save_execution(execution)  # Persist job IDs immediately

        # Wait for job completion - get both eval and training results (combined job)
        job_result = self._wait_for_job(
            job_id,
            target_models=target_models,
            include_eval_results=True,
            include_training_results=True,
        )
        self._update_nim_results_with_metrics(
            execution, job_result["eval_results"], trigger.metric_thresholds
        )
        self._save_execution(execution)  # Persist eval metrics

        # For data_threshold, all NIMs are always retrained
        for nim_result in execution.nim_results:
            nim_result.training_triggered = True

        # Update NIM results with training outcomes
        self._update_nim_results_with_training(execution, job_result["training_results"])

        execution.update_summary_from_nim_results()
        execution.decision_reason = f"Data threshold met: {available_records} records since last training >= {trigger.min_new_records}"

    def _execute_metric_degradation(self, trigger: RetrainingTrigger, execution: TriggerExecution):
        """
        Execute metric degradation trigger.
        Run eval, compare thresholds, train only failed NIMs.
        """
        if self._is_deft(trigger):
            # Defense in depth — model validation already rejects this combination.
            raise ValueError(
                f"metric_degradation triggers are not supported for {trigger.job_type} — "
                "online evaluation drives DEFT promotion verdicts instead"
            )

        # Resolve models for all NIMs
        target_models = self.model_resolver.resolve(trigger)
        self._populate_nim_results(execution, target_models)
        self._save_execution(execution)  # Persist NIM list immediately

        # Run evaluation-only job first for all NIMs
        eval_job_id = self._run_job(
            trigger, target_models, eval_enabled=True, training_enabled=False
        )
        execution.eval_job_id = eval_job_id
        self._save_execution(execution)  # Persist eval job ID immediately

        # Wait for evaluation (eval results only, no training)
        eval_result = self._wait_for_job(
            eval_job_id,
            target_models=target_models,
            include_eval_results=True,
            include_training_results=False,
        )
        self._update_nim_results_with_metrics(
            execution, eval_result["eval_results"], trigger.metric_thresholds
        )
        self._save_execution(execution)  # Persist eval metrics

        # Check which NIMs failed thresholds
        execution.update_summary_from_nim_results()

        if execution.nims_failed > 0:
            # Some NIMs failed → trigger a training job for ONLY the failed NIMs
            # Filter target_models to include only NIMs that didn't pass thresholds
            failed_target_models = {
                model_name: target
                for model_name, target in target_models.items()
                if any(
                    nim_result.model_name == model_name and not nim_result.passed_thresholds
                    for nim_result in execution.nim_results
                )
            }

            logger.info(
                f"Training only failed NIMs: {list(failed_target_models.keys())} "
                f"(out of {len(target_models)} total NIMs)"
            )

            training_job_id = self._run_job(
                trigger, failed_target_models, eval_enabled=False, training_enabled=True
            )
            execution.training_job_id = training_job_id
            execution.triggered_training = True
            self._save_execution(execution)  # Persist training job ID immediately

            # Mark only the failed NIMs as training_triggered
            for nim_result in execution.nim_results:
                if not nim_result.passed_thresholds:
                    nim_result.training_triggered = True

            # Wait for training to complete (training results only, no pre-training eval)
            training_result = self._wait_for_job(
                training_job_id,
                target_models=failed_target_models,
                include_eval_results=False,
                include_training_results=True,
            )
            self._update_nim_results_with_training(execution, training_result["training_results"])
            self._save_execution(execution)  # Persist training results

            execution.update_summary_from_nim_results()
            failed_nims = [r.model_name for r in execution.nim_results if not r.passed_thresholds]
            execution.decision_reason = f"NIMs below threshold: {', '.join(failed_nims)}"
        else:
            # All NIMs passed
            execution.skip_reason = "All NIMs passed thresholds"
            execution.decision_reason = "All models performing well, no retraining needed"

    def _execute_schedule(self, trigger: RetrainingTrigger, execution: TriggerExecution):
        """
        Execute schedule trigger.
        Always run eval + training on schedule.
        """
        if self._is_deft(trigger):
            self._execute_aoi_schedule(trigger, execution)
            return

        # Resolve models for all NIMs
        target_models = self.model_resolver.resolve(trigger)
        self._populate_nim_results(execution, target_models)
        self._save_execution(execution)  # Persist NIM list immediately

        # Run combined eval + training job for all NIMs
        job_id = self._run_job(trigger, target_models, eval_enabled=True, training_enabled=True)
        execution.eval_job_id = job_id
        execution.training_job_id = job_id  # Same job handles both
        execution.triggered_training = True
        self._save_execution(execution)  # Persist job IDs immediately

        # Wait for job completion - get both eval and training results (combined job)
        job_result = self._wait_for_job(
            job_id,
            target_models=target_models,
            include_eval_results=True,
            include_training_results=True,
        )
        self._update_nim_results_with_metrics(
            execution, job_result["eval_results"], trigger.metric_thresholds
        )
        self._save_execution(execution)  # Persist eval metrics

        # For schedule trigger, all NIMs are always retrained
        for nim_result in execution.nim_results:
            nim_result.training_triggered = True

        # Update NIM results with training outcomes
        self._update_nim_results_with_training(execution, job_result["training_results"])

        execution.update_summary_from_nim_results()
        execution.decision_reason = "Scheduled retraining"

    def _trigger_data_ingestion(self, trigger: RetrainingTrigger) -> int:
        """
        Trigger data ingestion via plugin container and return count of new records.

        Uses the common DataIngestionService to ensure consistent behavior
        between triggers and direct job API calls.

        Args:
            trigger: Trigger configuration

        Returns:
            Number of new records loaded to ES
        """
        if not trigger.ingestion_config:
            raise ValueError(
                f"Trigger '{trigger.name}' has enable_data_ingestion=True but no ingestion_config"
            )
        if not (trigger.ingestion_config.dataset_type or "").strip():
            raise ValueError(f"Trigger '{trigger.name}' ingestion_config has no dataset_type")

        try:
            result = run_data_ingestion(
                db=self.db,
                workload_id=trigger.workload_id,
                client_id=trigger.client_id,
                plugin_name=trigger.ingestion_config.plugin_name,
                config=trigger.ingestion_config.config,
                job_type=trigger.job_type,
                dataset_type=trigger.ingestion_config.dataset_type,
                trigger_id=str(trigger.id),
            )

            if result.errors:
                raise RuntimeError(f"Ingestion failed: {result.errors}")

            return result.records_loaded

        except Exception as e:
            logger.error(f"Ingestion failed for trigger {trigger.name}: {e}")
            raise

    def _run_job(
        self,
        trigger: RetrainingTrigger,
        target_models: TargetModels,
        eval_enabled: bool = True,
        training_enabled: bool = False,
    ) -> str:
        """
        Run a job with the specified configuration.

        Args:
            trigger: Trigger configuration
            target_models: Dict mapping model_name → ModelInfo (with target and is_fine_tuned flag)
            eval_enabled: If True, enable evaluation phase
            training_enabled: If True, enable training phase

        Returns:
            Job ID
        """
        from src.api.models import FlywheelRun
        from src.api.schemas import FlywheelRunStatus
        from src.tasks.tasks import run_nim_workflow_dag

        # Validate configuration for iterative fine-tuning if using fine_tuned model_source
        if trigger.model_source == "fine_tuned":
            validate_config_for_iterative_finetuning(self.db, trigger.config_name, trigger.job_type)

        mode = f"eval={eval_enabled}, training={training_enabled}"
        logger.info(f"Running job for trigger {trigger.name}, nims={len(target_models)}, {mode}")

        # Build runtime overrides for pipeline
        # Pass full ModelInfo (with target and is_fine_tuned flag) to pipeline
        runtime_overrides = {
            "eval_enabled": eval_enabled,
            "training_enabled": training_enabled,
            "target_models": target_models,  # Now contains ModelInfo with target + is_fine_tuned
        }

        # Add date range override for embedding jobs with data_window_months
        if trigger.job_type == "embedding_job" and trigger.data_window_months:
            date_range = self.date_window_calc.calculate_date_range(trigger.data_window_months)
            if date_range:
                runtime_overrides["from_date"] = date_range.get("from")
                runtime_overrides["to_date"] = date_range.get("to")

        # Create FlywheelRun document
        flywheel_run = FlywheelRun(
            workload_id=trigger.workload_id,
            client_id=trigger.client_id,
            job_type=trigger.job_type,
            started_at=None,
            config_name=trigger.config_name,
            num_records=0,
            nims=[],
            status=FlywheelRunStatus.PENDING,
        )

        # Save to MongoDB
        result = self.db.flywheel_runs.insert_one(flywheel_run.to_mongo())
        job_id = str(result.inserted_id)

        logger.info(f"Created FlywheelRun {job_id} for trigger {trigger.name}")

        # Dispatch the workflow task asynchronously
        run_nim_workflow_dag.delay(
            workload_id=trigger.workload_id,
            flywheel_run_id=job_id,
            client_id=trigger.client_id,
            job_type=trigger.job_type,
            config_name=trigger.config_name,
            runtime_overrides=runtime_overrides,
        )

        logger.info(f"Dispatched {mode} job: {job_id}")
        return job_id

    def _poll_job_completion(
        self,
        job_id: str,
        poll_interval_seconds: int = 10,
        max_wait_hours: int = 24,
        log_interval_seconds: int = 300,
    ):
        """
        Poll for job completion using time-based timeout.

        Args:
            job_id: Job ID to poll
            poll_interval_seconds: How often to poll for job status (default: 10s)
            max_wait_hours: Maximum time to wait before timing out
            log_interval_seconds: How often to log progress (default: 5 minutes)

        Raises:
            ValueError: If job not found
            TimeoutError: If job doesn't complete within timeout
            RuntimeError: If job failed or was cancelled
        """
        import time

        from src.api.schemas import FlywheelRunStatus

        timeout_seconds = max_wait_hours * 3600
        start_time = time.time()
        job_object_id = ObjectId(job_id)
        last_log_time = start_time

        while time.time() - start_time < timeout_seconds:
            job = self.db.flywheel_runs.find_one({"_id": job_object_id})
            if not job:
                raise ValueError(f"Job {job_id} not found")

            status = job.get("status")
            if status in [
                FlywheelRunStatus.COMPLETED,
                FlywheelRunStatus.FAILED,
                FlywheelRunStatus.CANCELLED,
            ]:
                elapsed = time.time() - start_time
                logger.info(f"Job {job_id} finished with status: {status} (took {elapsed:.1f}s)")

                # Check for failures
                if status == FlywheelRunStatus.FAILED:
                    raise RuntimeError(f"Job {job_id} failed: {job.get('error', 'Unknown error')}")
                if status == FlywheelRunStatus.CANCELLED:
                    raise RuntimeError(f"Job {job_id} was cancelled")

                # Job completed successfully
                return

            # Log progress periodically
            if time.time() - last_log_time > log_interval_seconds:
                elapsed = time.time() - start_time
                logger.info(
                    f"Job {job_id} still running (status: {status}, elapsed: {elapsed:.1f}s)"
                )
                last_log_time = time.time()

            time.sleep(poll_interval_seconds)

        raise TimeoutError(f"Job {job_id} did not complete within {max_wait_hours} hours")

    def _wait_for_job(
        self,
        job_id: str,
        target_models: TargetModels,
        include_eval_results: bool = True,
        include_training_results: bool = False,
        poll_interval_seconds: int = 10,
        max_wait_hours: int = 24,
    ) -> dict:
        """
        Wait for job completion and extract results.

        Flexible extraction based on job type:
        - Eval-only job: include_eval_results=True, include_training_results=False
        - Training-only job: include_eval_results=False, include_training_results=True
        - Combined job: include_eval_results=True, include_training_results=True

        Args:
            job_id: Job ID to wait for
            target_models: TargetModels dict with model_name → ModelInfo (target + is_fine_tuned)
                Used to map runtime model names back to base model names for matching
            include_eval_results: If True, extract pre-training metrics (base-eval/icl-eval)
            include_training_results: If True, extract training results
                (customization_id, fine_tuned_model, post_training_metrics from customized-eval)
            poll_interval_seconds: How often to poll for job status
            max_wait_hours: Maximum time to wait before timing out

        Returns:
            Dict with:
            - "eval_results": Per-model evaluation metrics (if include_eval_results=True)
              {model_name: {metric: value, model_used: str}}
              - model_name: Base model name (for matching/grouping)
              - model_used: Model that was evaluated on (base model or previous fine-tuned model)
            - "training_results": Per-model training results (if include_training_results=True)
              {model_name: {customization_id, fine_tuned_model, model_used, post_training_metrics}}
              - model_name: Base model name (for matching/grouping)
              - model_used: Model that was used for training (base model or previous fine-tuned model)
              - fine_tuned_model: Newly created fine-tuned model from this training
        """
        logger.info(
            f"Waiting for job {job_id} to complete "
            f"(eval={include_eval_results}, training={include_training_results})"
        )

        # Wait for job to complete
        self._poll_job_completion(job_id, poll_interval_seconds, max_wait_hours)
        job_object_id = ObjectId(job_id)

        # Extract results
        result = {"eval_results": {}, "training_results": {}}

        # Build reverse mapping: fine-tuned target → base model
        target_to_base_mapping = self._build_target_to_base_mapping(target_models)

        # Get all NIMs for this job
        nims = list(self.db.nims.find({"flywheel_run_id": job_object_id}))
        logger.info(f"Found {len(nims)} NIMs for job {job_id}")

        for nim in nims:
            nim_id = nim["_id"]
            runtime_model_name = nim.get("model_name")

            # Skip NIMs without model_name to avoid ambiguous keys
            if not runtime_model_name:
                logger.warning(
                    f"NIM {nim_id} has no model_name. Skipping result extraction. "
                    "This indicates a pipeline data inconsistency."
                )
                continue

            # Determine the base model name for matching with execution.nim_results
            # If runtime_model_name is in target_to_base_mapping (it's a fine-tuned model),
            # use the mapped base model name. Otherwise, use runtime_model_name as-is.
            if runtime_model_name in target_to_base_mapping:
                model_name = target_to_base_mapping[runtime_model_name]
                logger.info(
                    f"NIM runtime name: {runtime_model_name} (fine-tuned), "
                    f"mapped to base: {model_name} for matching"
                )
            else:
                model_name = runtime_model_name
                logger.info(f"NIM model_name: {model_name} (using as-is)")

            # Extract pre-training evaluation metrics
            if include_eval_results:
                # Look for base-eval (embedding) or icl-eval (LLM)
                base_eval = self.db.evaluations.find_one(
                    {"nim_id": nim_id, "eval_type": {"$in": ["base-eval", "icl-eval"]}}
                )
                if base_eval:
                    # Check for evaluation errors
                    if base_eval.get("error"):
                        logger.warning(
                            f"NIM {model_name}: evaluation failed with error: {base_eval['error']}"
                        )
                        result["eval_results"][model_name] = {
                            "error": base_eval["error"],
                            "scores": {},
                            "model_used": runtime_model_name,
                        }
                    elif base_eval.get("scores"):
                        result["eval_results"][model_name] = {
                            **base_eval["scores"],
                            "model_used": runtime_model_name,
                        }
                        logger.info(
                            f"NIM {model_name}: eval scores = {base_eval['scores']}, "
                            f"model_used = {runtime_model_name}"
                        )

            # Extract training results
            if include_training_results:
                customization = self.db.customizations.find_one(
                    {"nim_id": nim_id, "customized_model": {"$exists": True, "$ne": None}}
                )
                if customization:
                    # Check for customization errors
                    customization_error = customization.get("error")

                    # Get post-training metrics from customized-eval
                    customized_eval = self.db.evaluations.find_one(
                        {"nim_id": nim_id, "eval_type": "customized-eval"}
                    )

                    # Check for post-training evaluation errors
                    post_metrics = {}
                    eval_error = None
                    if customized_eval:
                        if customized_eval.get("error"):
                            eval_error = customized_eval["error"]
                            logger.warning(
                                f"NIM {model_name}: post-training evaluation failed: {eval_error}"
                            )
                        else:
                            post_metrics = customized_eval.get("scores", {})

                    latest_finetuned_model = customization.get("customized_model")
                    result["training_results"][model_name] = {
                        "customization_id": str(customization["_id"]),
                        "fine_tuned_model": latest_finetuned_model,
                        "model_used": runtime_model_name,
                        "post_training_metrics": post_metrics,
                        "customization_error": customization_error,
                        "eval_error": eval_error,
                    }

                    if customization_error:
                        logger.warning(
                            f"NIM {model_name}: customization failed with error: {customization_error}"
                        )
                    else:
                        logger.info(
                            f"NIM {model_name}: model_used = {runtime_model_name}, "
                            f"fine_tuned_model = {latest_finetuned_model}, "
                            f"post_training_metrics = {post_metrics}"
                        )

        logger.info(
            f"Job {job_id} results: eval={len(result['eval_results'])} NIMs, "
            f"training={len(result['training_results'])} NIMs"
        )
        return result

    def _build_target_to_base_mapping(self, target_models: TargetModels) -> dict[str, str]:
        """
        Build reverse mapping from fine-tuned target to base model name.

        This is critical for matching when runtime_overrides changed model_name
        to the fine-tuned model target. The mapping allows us to translate the
        runtime model name back to the original base model name for result matching.

        Args:
            target_models: Dict mapping base_model_name → ModelInfo with:
                - target: The model target (fine-tuned model name or base model)
                - is_fine_tuned: Boolean indicating if this is a fine-tuned model

        Returns:
            Dict mapping fine-tuned target → base model name
            Example: {"dfwbp/9tg@cust-xyz": "nvidia/llama-3.2-nv-embedqa-1b-v2"}

        Example:
            Input target_models:
            {
                "nvidia/llama-3.2-nv-embedqa-1b-v2": {
                    "target": "dfwbp/9tg@cust-ExubXebRVjynhoJkGSNRkx",
                    "is_fine_tuned": True
                }
            }

            Output:
            {
                "dfwbp/9tg@cust-ExubXebRVjynhoJkGSNRkx": "nvidia/llama-3.2-nv-embedqa-1b-v2"
            }
        """
        target_to_base_mapping = {}

        for base_model_name, model_info in target_models.items():
            # Extract target from dict or object
            target = (
                model_info.get("target")
                if isinstance(model_info, dict)
                else getattr(model_info, "target", None)
            )
            # Extract is_fine_tuned flag from dict or object
            is_fine_tuned = (
                model_info.get("is_fine_tuned")
                if isinstance(model_info, dict)
                else getattr(model_info, "is_fine_tuned", False)
            )

            # Only create mapping for fine-tuned models
            if target and is_fine_tuned:
                target_to_base_mapping[target] = base_model_name
                logger.info(
                    f"Built target mapping: {target} → {base_model_name} "
                    f"(for runtime model name translation)"
                )

        return target_to_base_mapping

    def _update_nim_results_with_training(
        self,
        execution: TriggerExecution,
        training_results: dict[str, dict],
    ):
        """
        Update NIM results with training outcomes and post-training metrics.

        Args:
            execution: Execution record to update
            training_results: Dict mapping base_model to training results including:
                - model_used: Model that was used for training (base or previous fine-tuned)
                - customization_id: ID of the customization record
                - fine_tuned_model: Name of the fine-tuned model
                - post_training_metrics: Evaluation metrics from customized-eval
                - customization_error: Error if customization failed
                - eval_error: Error if post-training evaluation failed
        """
        for nim_result in execution.nim_results:
            if nim_result.training_triggered:
                # Look up training results by model_name (which is the base model for training)
                result = training_results.get(nim_result.model_name)
                if result:
                    # Extract model_used if present (overrides any value from eval)
                    if "model_used" in result:
                        nim_result.model_used = result["model_used"]
                    nim_result.customization_id = result.get("customization_id")
                    nim_result.fine_tuned_model = result.get("fine_tuned_model")
                    nim_result.post_training_metrics = result.get("post_training_metrics", {})
                    nim_result.customization_error = result.get("customization_error")
                    nim_result.post_eval_error = result.get("eval_error")

                    if nim_result.customization_error:
                        logger.warning(
                            f"NIM {nim_result.model_name}: customization failed with error: "
                            f"{nim_result.customization_error}"
                        )
                    elif nim_result.post_eval_error:
                        logger.warning(
                            f"NIM {nim_result.model_name}: post-training evaluation failed with error: "
                            f"{nim_result.post_eval_error}"
                        )
                    else:
                        logger.info(
                            f"NIM {nim_result.model_name}: trained → {nim_result.fine_tuned_model}, "
                            f"post-training metrics: {nim_result.post_training_metrics}"
                        )

    def _populate_nim_results(
        self,
        execution: TriggerExecution,
        target_models: TargetModels,
    ):
        """
        Initialize NIMExecutionResult entries for each NIM.

        Args:
            execution: Execution record to populate
            target_models: Dict mapping model_name → ModelInfo (with target and is_fine_tuned flag)
        """
        execution.nim_results = []
        for model_name in target_models:
            nim_result = NIMExecutionResult(
                model_name=model_name,
                metrics={},
                thresholds_checked={},
                passed_thresholds=True,
                needs_retraining=False,
                training_triggered=False,
            )
            execution.nim_results.append(nim_result)

        execution.total_nims = len(target_models)

    def _update_nim_results_with_metrics(
        self,
        execution: TriggerExecution,
        eval_results: dict[str, dict],
        thresholds: dict[str, float] | None,
    ):
        """
        Update NIM results with evaluation metrics and threshold checks.

        Args:
            execution: Execution record to update
            eval_results: Per-model results: {model_name: {scores: {...}, model_used: str} or {error: "...", model_used: str}}
            thresholds: Metric thresholds for checking
        """
        for nim_result in execution.nim_results:
            # Get results for this NIM (by model_name)
            result = eval_results.get(nim_result.model_name, {})

            # Extract model_used if present
            if "model_used" in result:
                nim_result.model_used = result["model_used"]

            # Check if evaluation failed
            if result.get("error"):
                nim_result.eval_error = result["error"]
                nim_result.metrics = {}
                nim_result.passed_thresholds = False
                logger.warning(
                    f"NIM {nim_result.model_name}: evaluation failed with error: {result['error']}"
                )
                continue

            # Get metrics (handle both dict format and direct scores)
            # Remove model_used from metrics if it was included
            metrics = result.get("scores", result) if isinstance(result, dict) else result
            if isinstance(metrics, dict) and "model_used" in metrics:
                metrics = {k: v for k, v in metrics.items() if k != "model_used"}
            nim_result.metrics = metrics.copy() if isinstance(metrics, dict) else {}

            # Check thresholds if provided
            if thresholds:
                all_passed = True
                has_missing_metrics = False

                for metric_name, threshold in thresholds.items():
                    actual = metrics.get(metric_name)
                    if actual is None:
                        # Metric defined in thresholds but missing from results
                        # Mark as incomplete evaluation and record as failed check
                        logger.warning(
                            f"NIM {nim_result.model_name}: metric '{metric_name}' not found in "
                            f"evaluation results. Recording as failed threshold check."
                        )
                        nim_result.thresholds_checked[metric_name] = {
                            "threshold": threshold,
                            "actual": None,
                            "passed": False,
                        }
                        all_passed = False
                        has_missing_metrics = True
                        continue

                    passed = actual >= threshold
                    nim_result.thresholds_checked[metric_name] = {
                        "threshold": threshold,
                        "actual": actual,
                        "passed": passed,
                    }
                    if not passed:
                        all_passed = False

                nim_result.passed_thresholds = all_passed
                nim_result.needs_retraining = not all_passed
                nim_result.incomplete_evaluation = has_missing_metrics

    def _save_execution(self, execution: TriggerExecution):
        """Save or update execution record in database."""
        self.db.trigger_executions.replace_one(
            {"_id": execution.id}, execution.to_mongo(), upsert=True
        )
        logger.info(f"Saved execution record: {execution.id}")

    def _update_trigger_fine_tuned_models(
        self,
        trigger: RetrainingTrigger,
        execution: TriggerExecution,
        job_id: str,  # - kept for future use with real job lookup
    ):
        """
        Update trigger's latest_fine_tuned_models after successful training.

        Called after training job completes to store the newly fine-tuned models.
        This eliminates the need to search through job history for latest models.

        Uses execution.nim_results which already has the fine-tuned model names
        populated by _update_nim_results_with_training.

        Args:
            trigger: Trigger configuration to update
            execution: Execution record with NIM results
            job_id: Job ID that completed training (for future real job lookup)
        """
        if not execution.triggered_training:
            return

        try:
            # Extract fine-tuned models from execution.nim_results
            # Key is model_name (NIM identifier), value is fine-tuned model
            # Only include models where customization succeeded (no customization_error)
            updates = {}
            for nim_result in execution.nim_results:
                if (
                    nim_result.training_triggered
                    and nim_result.fine_tuned_model
                    and not nim_result.customization_error
                ):
                    # Use model_name as key, fine-tuned model as value
                    updates[nim_result.model_name] = nim_result.fine_tuned_model
                elif nim_result.training_triggered and nim_result.customization_error:
                    logger.warning(
                        f"Skipping update for {nim_result.model_name}: customization failed "
                        f"with error: {nim_result.customization_error}"
                    )

            if updates:
                # Merge with existing models (update only the ones that were trained)
                current_models = dict(trigger.latest_fine_tuned_models or {})
                current_models.update(updates)

                # Update trigger in database
                self.db.retraining_triggers.update_one(
                    {"_id": trigger.id},
                    {"$set": {"latest_fine_tuned_models": current_models}},
                )
                logger.info(
                    f"Updated trigger {trigger.name} with {len(updates)} fine-tuned models: "
                    f"{updates}"
                )

        except Exception as e:
            logger.error(f"Failed to update trigger fine-tuned models: {e}")
