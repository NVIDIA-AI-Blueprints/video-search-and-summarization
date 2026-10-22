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
"""DEFT-loop pipeline stage tasks — thin Celery wrappers.

The Celery stages of the DEFT flywheel, shared by the whole ``aoi_job`` /
``its_job`` family: select → snapshot → DEFT train → offline eval gate →
register → promote → wait for promotion → finalize. Nothing here branches on
``job_type``; every AOI-vs-ITS difference is resolved from ``use_case`` inside
the handlers (``src/tasks/handlers/deft/handlers.py``), which hold the business
logic. Each task only constructs its handler and forwards the ``dict`` run
context threaded through the chain.

The ``wait_for_promotion`` stage blocks the chain until the promotion
terminates, which — because the parent dispatcher blocks on the whole chain
(concurrency-1 ``parent_queue``) — serializes entire flywheel runs behind the
current rollout.

The module is ``deft_tasks`` but the 8 registered task names are still
``tasks.aoi.*`` — see the ``name=`` decorators below.
"""

from __future__ import annotations

from typing import Any

from src.celery_app import celery_app
from src.log_utils import setup_logging
from src.tasks.handlers.deft import (
    AOIStageError,
    FinalizeAOIRunHandler,
    InitializeDEFTWorkflowHandler,
    RegisterCandidateHandler,
    RunDeftTrainingHandler,
    RunOfflineEvalGateHandler,
    SelectAndSnapshotHandler,
    StartPromotionHandler,
    WaitForPromotionHandler,
)

__all__ = [
    "AOIStageError",
    "finalize_aoi_run",
    "initialize_aoi_workflow",
    "register_candidate",
    "run_deft_training",
    "run_offline_eval_gate",
    "select_and_snapshot",
    "start_promotion",
    "wait_for_promotion",
]

# Namespace stays "…tasks.aoi" for the same reason the task names do: a log
# filter keyed on it would silently stop matching. Renamed with them, later.
logger = setup_logging("data_flywheel.tasks.aoi")


def _db_manager():
    """Return the shared TaskDBManager (imported lazily to avoid import-time DB)."""
    from src.api.db_manager import get_db_manager

    return get_db_manager()


def _record_stage(flywheel_run_id: Any, stage_name: str) -> None:
    """Publish which stage a DEFT run just entered.

    BEST EFFORT, like every other write on this path: observability must never
    break a run. Written here rather than in each handler because this wrapper
    already knows the stage name for all of them -- one call site, no way for a
    new stage to forget.

    Recorded on ENTRY, not on completion: the value of the field is telling an
    operator where a run is stuck, which is precisely the case where the stage
    never completes.
    """
    if not flywheel_run_id:
        return
    try:
        from datetime import datetime

        from src.api.db_manager import get_db_manager

        get_db_manager().record_deft_stage(flywheel_run_id, stage_name, datetime.utcnow())
    except Exception:  # pragma: no cover - observability must not break a run
        logger.warning("Could not persist DEFT stage %s", stage_name, exc_info=True)


def _run_stage(stage_name: str, handler, previous_result: dict[str, Any]) -> dict[str, Any]:
    """Run one DEFT stage with run-state discipline — and NEVER break the chain.

    Same contract as the NMP stages: a task never raises out of the chain.
    A raised exception would abort the Celery chain and the tail task's result
    the parent dispatcher blocks on may never resolve — wedging the
    concurrency-1 parent worker. Instead the error is stamped on the context
    and every later stage SKIPS (pass-through), so the chain always propagates
    to ``finalize`` and the dispatcher's ``get()`` returns.

    Three guarantees every stage honors (found by deployment testing):

    1. **Skip-on-error** — if a previous stage recorded ``error``, this stage
       is a graceful pass-through (mirrors ``should_skip_stage`` on the NMP
       handlers).
    2. **Cancellation is honored between stages** — ``POST /jobs/{id}/cancel``
       marks the run; the remaining stages then skip (the run doc is already
       terminal via the cancel path).
    3. **ANY stage failure marks the FlywheelRun FAILED** — previously only the
       offline gate marked the run, so a snapshot/training/service failure left
       the run stuck in RUNNING forever. ``mark_flywheel_run_error`` is
       first-write-wins, so a stage that already recorded a specific message
       (the gate) keeps it.
    """
    from src.lib.flywheel.cancellation import FlywheelCancelledError
    from src.tasks.handlers.deft.handlers import honor_cancellation

    ctx = dict(previous_result or {})
    run_id = ctx.get("flywheel_run_id")

    # Deliberate parallel of BaseTaskHandler.should_skip_stage (src/tasks/base.py)
    # for the DEFT dict-shaped context (NMP stages carry a typed TaskResult) —
    # unify here first if the two context shapes ever converge.
    if ctx.get("error"):
        logger.warning("Skipping %s because a previous stage failed: %s", stage_name, ctx["error"])
        return ctx

    try:
        honor_cancellation(run_id)
        _record_stage(run_id, stage_name)
        return handler.execute(ctx)
    except FlywheelCancelledError as e:
        # The cancel path (API/cleanup) already marked the run doc terminal —
        # just record the skip reason so the remaining stages pass through.
        logger.info("DEFT stage %s stopped: run %s was cancelled", stage_name, run_id)
        ctx["error"] = f"Flywheel run cancelled: {e}"
        return ctx
    except Exception as e:
        error_msg = f"DEFT stage '{stage_name}' failed: {e}"
        if run_id:
            try:
                from datetime import datetime

                _db_manager().mark_flywheel_run_error(
                    run_id, error_msg, finished_at=datetime.utcnow()
                )
            except Exception:
                logger.exception("Could not mark FlywheelRun %s failed", run_id)
        logger.exception("DEFT stage %s failed; remaining stages will skip", stage_name)
        ctx["error"] = error_msg
        return ctx


# The ``name=`` strings below stay ``tasks.aoi.*`` even though this module is
# ``deft_tasks`` and the stages serve ``its_job`` too. That asymmetry is
# deliberate, and it is what explicit ``name=`` is for: a task name is a Celery
# WIRE identifier, so renaming one is not backward compatible — a message
# enqueued before the deploy is ``NotRegistered`` after it, and the chain dies
# leaving its FlywheelRun non-terminal. Draining first is not a cheap
# precondition here: ``wait_for_promotion`` holds the chain for the whole
# shadow→canary→full rollout and can park indefinitely in HITL. Renaming to
# ``tasks.deft.*`` is deferred to its own MR, which can register aliases and
# roll workers before the API.


@celery_app.task(name="tasks.aoi.initialize_aoi_workflow", pydantic=True)
def initialize_aoi_workflow(
    workload_id: str,
    flywheel_run_id: str,
    client_id: str,
    job_type: str,
    job_config: dict[str, Any],
) -> dict[str, Any]:
    """Set up the DEFT FlywheelRun and build the initial run context.

    Like every DEFT stage, this never raises out of the chain: a failure marks
    the run FAILED and returns a context carrying ``error`` so the remaining
    stages skip and the chain still completes (see ``_run_stage``).
    """
    # Recorded here rather than by `_run_stage`: this stage takes explicit
    # kwargs instead of a ctx, so it is the one DEFT stage that wrapper does not
    # cover. Without this a failure or hang in initialize reads as
    # `status=running` with no stage at all -- the exact blind spot the field
    # exists to close. The FlywheelRun document is created before this task is
    # dispatched, so the write lands.
    _record_stage(flywheel_run_id, "initialize_aoi_workflow")
    try:
        return InitializeDEFTWorkflowHandler(_db_manager()).execute(
            workload_id=workload_id,
            flywheel_run_id=flywheel_run_id,
            client_id=client_id,
            job_type=job_type,
            job_config=job_config,
        )
    except Exception as e:
        error_msg = f"DEFT initialize failed: {e}"
        try:
            from datetime import datetime

            _db_manager().mark_flywheel_run_error(
                flywheel_run_id, error_msg, finished_at=datetime.utcnow()
            )
        except Exception:
            logger.exception("Could not mark FlywheelRun %s failed", flywheel_run_id)
        logger.exception("DEFT initialize failed; remaining stages will skip")
        return {
            "flywheel_run_id": flywheel_run_id,
            "workload_id": workload_id,
            "client_id": client_id,
            "job_type": job_type,
            "job_config": job_config,
            "error": error_msg,
        }


@celery_app.task(name="tasks.aoi.select_and_snapshot")
def select_and_snapshot(previous_result: dict[str, Any]) -> dict[str, Any]:
    """Request an immutable training snapshot from the Storage Backend."""
    return _run_stage("select_and_snapshot", SelectAndSnapshotHandler(), previous_result)


@celery_app.task(name="tasks.aoi.run_deft_training")
def run_deft_training(previous_result: dict[str, Any]) -> dict[str, Any]:
    """Trigger a DEFT fine-tune on the snapshot and wait for the candidate."""
    return _run_stage("run_deft_training", RunDeftTrainingHandler(), previous_result)


@celery_app.task(name="tasks.aoi.run_offline_eval_gate")
def run_offline_eval_gate(previous_result: dict[str, Any]) -> dict[str, Any]:
    """Offline KPI gate: baseline vs candidate — the gate to enter shadow."""
    return _run_stage("run_offline_eval_gate", RunOfflineEvalGateHandler(), previous_result)


@celery_app.task(name="tasks.aoi.register_candidate")
def register_candidate(previous_result: dict[str, Any]) -> dict[str, Any]:
    """Register the passing candidate in MLflow with its snapshot lineage."""
    return _run_stage("register_candidate", RegisterCandidateHandler(), previous_result)


@celery_app.task(name="tasks.aoi.start_promotion")
def start_promotion(previous_result: dict[str, Any]) -> dict[str, Any]:
    """Hand the registered version to the Promotion Controller (shadow→canary→full)."""
    return _run_stage("start_promotion", StartPromotionHandler(_db_manager()), previous_result)


@celery_app.task(name="tasks.aoi.wait_for_promotion")
def wait_for_promotion(previous_result: dict[str, Any]) -> dict[str, Any]:
    """Block the chain until the promotion terminates — serializes runs.

    Holding here holds the concurrency-1 parent slot for the whole rollout, so
    no new flywheel run starts until the current promotion reaches a terminal
    state (or the run is cancelled). Delegates through ``_run_stage`` so it
    inherits skip-on-error / cancellation / mark-failed like every other stage.
    """
    return _run_stage("wait_for_promotion", WaitForPromotionHandler(), previous_result)


@celery_app.task(name="tasks.aoi.finalize_aoi_run")
def finalize_aoi_run(previous_result: dict[str, Any]) -> dict[str, Any]:
    """Close out the chain; the run completes when the promotion does."""
    return _run_stage("finalize_aoi_run", FinalizeAOIRunHandler(), previous_result)
