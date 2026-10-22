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

"""DEFT-loop task handlers — the business logic of the pipeline stages.

Shared by the whole ``aoi_job`` / ``its_job`` family; the AOI-prefixed names
here are historical, not a job-type restriction. Mirrors the LLM/embedding
handler pattern (``BaseTaskHandler``): the Celery tasks in
``src/tasks/deft_tasks.py`` are thin wrappers that construct and call these.
Handlers drive the external services through the neutral seam
(``get_backend_provider()`` → DEFT backend), the Eval Harness, MLflow, and the
Promotion Controller; backend/config modules are imported lazily inside
``execute`` so this module never eagerly pulls a backend SDK.

Each handler receives/returns the plain ``dict`` run context threaded through
the Celery chain (see ``initialize`` for the canonical keys).
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime
from typing import Any

from src.api.db_manager import TaskDBManager
from src.log_utils import setup_logging
from src.tasks.base import BaseTaskHandler
from src.tasks.promotion.serving_version import ObservedServing

logger = setup_logging("data_flywheel.tasks.handlers.deft.handlers")

# Bounded polling knobs for the snapshot / DEFT-run waits. Deliberately small
# attempt/interval defaults so a stuck upstream fails loudly rather than a
# stage busy-looping forever.
# The snapshot pair is only the DEFAULT: a run may raise it per-job via
# ``job_config.snapshot_poll_max_attempts`` / ``snapshot_poll_interval_s`` (see
# ``_snapshot_poll_budget``), because an ITS snapshot decodes video into sampled
# frames and legitimately outlasts the 60 x 5s that suffices for AOI.
SNAPSHOT_POLL_MAX_ATTEMPTS = 60
SNAPSHOT_POLL_INTERVAL_S = 5.0
TRAINING_POLL_INTERVAL_S = 15.0

# Default for ``job_config.min_snapshot_records`` — see DeftJobConfig.
MIN_SNAPSHOT_RECORDS = 1


# Training has NO hard duration cap (a legitimate fine-tune can run for
# hours): the timeout is NO-PROGRESS-based — the timer resets whenever DEFT
# reports progress (NMP wait-loop pattern), and only a run that reports
# nothing new for this long is failed (and remotely cancelled).
# OPERATOR NOTE: by design there is no absolute wall-clock limit — a run that
# keeps reporting progress runs for as long as it needs; the only backstops
# are this no-progress window and user cancellation.
#
# SIZING THIS WINDOW: DEFT reports progress per STEP, not per epoch, so the
# window must exceed the LONGEST SINGLE STEP — in practice the per-iteration
# `train`. That is a function of epochs x pool size x GPU count, so there is no
# universally safe constant; a 50-epoch AOI iteration measured ~57 min on 4xH100
# and was killed by the previous 3600s default *after* completing every epoch.
# The default is therefore generous (3h) and overridable, because the failure
# mode of being too tight is discarding finished GPU work, while being too loose
# only delays noticing a genuinely hung run.
def _training_no_progress_timeout_s() -> float:
    """Parse the no-progress window from the environment, failing loudly.

    Deliberately RAISES on a bad value rather than falling back to the default.
    An operator only sets this because their training legitimately outlasts the
    default; silently substituting 3h would kill exactly the runs the setting
    exists to protect, hours later and with a message blaming DEFT. A refusal at
    startup names the mistake immediately.
    """
    raw = os.getenv("DFW_TRAINING_NO_PROGRESS_TIMEOUT_S", "10800")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"DFW_TRAINING_NO_PROGRESS_TIMEOUT_S must be a number of seconds, got {raw!r}"
        ) from exc
    # nan/inf parse cleanly and would DISABLE the watchdog rather than trip it:
    # every comparison against nan is False, and `elapsed > inf` never holds, so
    # the timeout branch would never fire and a genuinely hung run would poll
    # forever. Checked before the > 0 test, which nan also silently passes.
    if not math.isfinite(value):
        raise ValueError(
            f"DFW_TRAINING_NO_PROGRESS_TIMEOUT_S must be a finite number of seconds, got {raw!r}"
        )
    if value <= 0:
        raise ValueError(f"DFW_TRAINING_NO_PROGRESS_TIMEOUT_S must be > 0 seconds, got {value!r}")
    return value


TRAINING_NO_PROGRESS_TIMEOUT_S = _training_no_progress_timeout_s()
# Consecutive failed status polls tolerated before giving up — a brief network
# blip during an hour-long training must not kill a healthy run.
TRAINING_STATUS_ERROR_TOLERANCE = 5
# Poll cadence for the terminal-promotion wait. This stage holds the
# concurrency-1 parent slot for the entire rollout, so the interval is coarse
# (a promotion moves on the order of minutes/hours via the beats, not seconds)
# to keep DB pressure low during long HITL/shadow holds.
PROMOTION_POLL_INTERVAL_S = 30.0
# Consecutive failed promotion-record reads tolerated before failing the stage
# — a transient Mongo blip must not fail a run that is otherwise waiting fine.
PROMOTION_STATUS_ERROR_TOLERANCE = 5


class AOIStageError(RuntimeError):
    """Raised when a DEFT pipeline stage fails (snapshot / training / gate).

    Failed-stage stop mechanism (NMP-parity): the handler raises this, the
    ``_run_stage`` wrapper in ``deft_tasks.py`` catches it, marks the
    ``FlywheelRun`` FAILED and stamps ``error`` on the chain context — the
    exception NEVER propagates out of the Celery task. Later stages see the
    error and skip (graceful pass-through), so the chain always completes and
    the parent dispatcher's blocking ``get()`` returns — e.g. a failed offline
    gate means register/promote SKIP, not an aborted chain. Subclasses
    ``RuntimeError`` so pre-existing ``RuntimeError`` handling keeps working.
    """


def honor_cancellation(flywheel_run_id: str | None) -> None:
    """Raise ``FlywheelCancelledError`` if the run was cancelled by the user.

    Mirrors the NMP wait-loop discipline: called between stages AND on every
    iteration of the long poll loops (snapshot / DEFT training). Fail-open on
    infrastructure: if the cancellation flag cannot be read (no DB in
    unit/e2e contexts, transient Mongo hiccup) the run proceeds — an
    unavailable check must never kill a healthy run.
    """
    if not flywheel_run_id:
        return
    from src.lib.flywheel.cancellation import FlywheelCancelledError, check_cancellation

    try:
        check_cancellation(flywheel_run_id)
    except FlywheelCancelledError:
        raise
    except Exception:  # — deliberate fail-open (see docstring)
        pass


def _snapshot_poll_budget(job_config: dict[str, Any]) -> tuple[int, float]:
    """``(max_attempts, interval_s)`` for the storage readiness polls.

    Read from the RAW job-config dict, which is what the chain context carries;
    an absent key reproduces the ``60 x 5s`` default exactly. No re-validation:
    ``DeftJobConfig`` types and bounds both fields (``ge=1`` / ``gt=0``) and the
    context is built from its ``model_dump()``, so an unusable value cannot get
    this far.
    """
    return (
        job_config.get("snapshot_poll_max_attempts") or SNAPSHOT_POLL_MAX_ATTEMPTS,
        job_config.get("snapshot_poll_interval_s") or SNAPSHOT_POLL_INTERVAL_S,
    )


def _snapshot_record_count(snapshot: dict[str, Any]) -> int | None:
    """Records in a READY snapshot, or None when the backend did not say.

    ``total_records`` and each manifest's ``record_count`` are both optional in
    the contract, so "no counts anywhere" is UNKNOWN, not zero — summing a list
    of nulls to 0 would fail a perfectly good snapshot. An EMPTY ``manifests``
    list is the one definite zero: a ready snapshot with nothing to retrieve.
    """
    total = snapshot.get("total_records")
    if isinstance(total, int):
        return total
    manifests = snapshot.get("manifests")
    if manifests == []:
        return 0
    if not isinstance(manifests, list):
        return None
    counts = [m.get("record_count") for m in manifests if isinstance(m, dict)]
    if any(isinstance(c, int) for c in counts):
        return sum(c for c in counts if isinstance(c, int))
    return None


class _AOIHandler(BaseTaskHandler):
    """Base for AOI handlers.

    Most AOI stages drive the DEFT-loop services rather than Mongo, so
    ``db_manager`` is optional — only the run-state stages (initialize /
    offline gate / finalize) need it.
    """

    def __init__(self, db_manager: TaskDBManager | None = None) -> None:
        super().__init__(db_manager)

    def _record_deft(self, ctx: dict[str, Any], fields: dict[str, Any]) -> None:
        """Publish part of the ``deft`` block for whichever stage owns it.

        BEST EFFORT, like every write on this path: observability must never
        fail a run. Each stage writes only what it already holds, so nothing
        here reaches out to DEFT, the Storage Backend or MLflow.
        """
        flywheel_run_id = ctx.get("flywheel_run_id")
        if not flywheel_run_id:
            return
        try:
            from src.api.db_manager import get_db_manager

            get_db_manager().record_deft_fields(flywheel_run_id, fields)
        except Exception:  # pragma: no cover - observability must not break a run
            self.logger.warning("Could not persist DEFT detail %s", sorted(fields), exc_info=True)


class InitializeDEFTWorkflowHandler(_AOIHandler):
    """Set up the DEFT FlywheelRun and build the initial run context.

    Also the run's fail-fast gate on ``use_case``: every later stage trusts the
    key this handler puts on the context (which store to snapshot, which
    evaluator grades the gate), so an unservable use case must stop the run here.
    """

    def _assert_use_case_is_known(self, use_case: str) -> None:
        """Resolve the evaluator for ``use_case`` now, so a typo dies in seconds.

        The use case is a registry key; an unregistered one otherwise surfaces at
        the offline gate, an entire DEFT fine-tune later. Probing through the
        provider seam hits exactly the registry the gate will use, so the two
        cannot disagree. Fail-closed on ANY failure (unlike the mid-flight
        ``honor_cancellation``): nothing has been spent yet at initialize.
        """
        from src.backends.factory import get_backend_provider

        try:
            evaluator = get_backend_provider().evaluator(use_case=use_case)
        except Exception as e:
            raise AOIStageError(
                f"Cannot start a DEFT run for use_case={use_case!r}: its evaluator "
                f"could not be resolved ({e}). Failing at initialize — this used to "
                "surface at the offline gate, an entire DEFT fine-tune later."
            ) from e
        if evaluator is None:
            # The seam answers None for "no evaluation to operate on" (the
            # cleanup/cancel path). A pipeline run with no evaluator would sail
            # past the gate ungraded, so treat it as a misconfiguration.
            raise AOIStageError(
                f"Cannot start a DEFT run for use_case={use_case!r}: the backend "
                "provides no evaluator for it, so the offline gate could never "
                "grade the candidate."
            )

    def execute(
        self,
        workload_id: str | None,
        flywheel_run_id: str,
        client_id: str | None,
        job_type: str,
        job_config: dict[str, Any],
    ) -> dict[str, Any]:
        from src.api.schemas import FlywheelRunStatus
        from src.config.deft import DeftJobConfig

        # Rebuilt from the RAW chain-context dict, so it carries no
        # ``config_type`` stamp. Fine here: ``cfg`` is only read for
        # ``use_case`` / ``model_name`` and never handed to ``config_type_for``.
        cfg = DeftJobConfig.from_dict(job_config)
        self._assert_use_case_is_known(cfg.use_case)

        self.db_manager.update_flywheel_run_status(flywheel_run_id, FlywheelRunStatus.RUNNING)
        self.db_manager.set_flywheel_started(flywheel_run_id)

        self.logger.info(
            "Initialized AOI run %s (use_case=%s, model=%s)",
            flywheel_run_id,
            cfg.use_case,
            cfg.model_name,
        )

        return {
            "flywheel_run_id": flywheel_run_id,
            "workload_id": workload_id,
            "client_id": client_id,
            "use_case": cfg.use_case,
            "model_name": cfg.model_name,
            # Carry the full config forward so later stages can read selection /
            # deft / eval params without re-loading it.
            "job_config": job_config,
            "baseline_version": job_config.get("baseline_version"),
        }


class SelectAndSnapshotHandler(_AOIHandler):
    """Request an immutable training snapshot from the Storage Backend.

    Registers a snapshot via the datastore seam, then polls until it is
    ``ready`` (bounded). AOI has no train/val/eval split.

    The snapshot is taken from THIS RUN'S store: sibling use cases do not share
    object stores, so the use case is threaded into the datastore seam here and
    ``_resolve_store`` refuses to snapshot at all when the deployment declares
    per-use-case stores but not one for this run.
    """

    def _resolve_store(self, use_case: str) -> Any:
        """Fail CLOSED unless this run's use case has a declared store, and log it.

        Returns the resolved ``DEFTConfig`` (``None`` when there is no DEFT
        block). Without this guard a use case with no entry silently INHERITS
        the top-level endpoints — an undeclared run would train on another use
        case's images and be graded on its records, which is not a crash but a
        wrong number gating a promotion. An entry that overrides nothing
        (``its: {}``) is a valid statement of "inherits" and passes: the guard
        demands a DECLARATION, not an override, which is the whole reason the
        shipped map is a set of empty entries rather than an empty map.

        Needed despite YAML startup validation because named job configs come
        from Mongo and never pass through it — and that is how an ITS run is
        launched.
        """
        from src.config import settings

        deft_config = getattr(settings.system_config, "deft_config", None)
        if deft_config is None:
            # Bare/NMP settings: exactly one store, so nothing to cross.
            return None

        declared = getattr(deft_config, "use_case_endpoints", None) or {}
        if declared and use_case not in declared:
            raise AOIStageError(
                f"Refusing to snapshot: use_case={use_case!r} has no entry in "
                f"deft_config.use_case_endpoints (declared: {sorted(declared)}). "
                "This deployment declares per-use-case stores, so an undeclared "
                "use case would silently inherit the top-level endpoints and take "
                "its training snapshot from another use case's store. Add an entry "
                "(an empty one means 'inherits, deliberately')."
            )

        resolved = deft_config.for_use_case(use_case)
        # Log the RESOLVED endpoints: when a run is later found to have trained
        # on the wrong data, this line is the record of what it actually read.
        self.logger.info(
            "Snapshotting use_case=%r from storage_backend_url=%s (object store %s)",
            use_case,
            getattr(resolved, "storage_backend_url", None),
            getattr(resolved, "object_store_uri", None),
        )
        return resolved

    def execute(self, previous_result: dict[str, Any]) -> dict[str, Any]:
        from src.backends.factory import get_backend_provider
        from src.config.deft import SnapshotSelectionConfig

        ctx = dict(previous_result or {})
        job_config = ctx.get("job_config") or {}
        selection = SnapshotSelectionConfig(**(job_config.get("selection") or {}))
        # Subscript, not .get(): initialize guarantees this key, and guessing a
        # store is the failure this whole stage is guarding against.
        use_case = ctx["use_case"]
        self._resolve_store(use_case)

        max_attempts, interval_s = _snapshot_poll_budget(job_config)

        datastore = get_backend_provider().datastore(ctx["model_name"], use_case=use_case)
        snapshot = datastore.register(selection, reason=job_config.get("snapshot_reason"))
        snapshot_id = snapshot.get("snapshot_id")

        status = snapshot.get("status")
        attempts = 0
        # "deleted" is terminal too (snapshot GC'd/removed under us) — treat it
        # as an immediate failure instead of polling to the timeout.
        while status not in ("ready", "failed", "deleted") and attempts < max_attempts:
            # Same discipline as the NMP wait loops: honor a user cancel on
            # EVERY poll iteration, not just between stages.
            honor_cancellation(ctx.get("flywheel_run_id"))
            time.sleep(interval_s)
            snapshot = datastore.get_snapshot(snapshot_id)
            status = snapshot.get("status")
            attempts += 1

        if status != "ready":
            # Elapsed seconds alongside the poll count, so an operator deciding
            # whether to raise the ceiling can see how long DFW actually waited.
            raise AOIStageError(
                f"Snapshot {snapshot_id} did not become ready "
                f"(status={status!r} after {attempts} polls / {attempts * interval_s:.0f}s)"
            )

        # A selection whose facets match nothing still comes back READY, just
        # empty — training on it burns a full run and its manifests give
        # derive_storage_prefix nothing to work with. Fail here, naming the
        # filters, because they are what an operator has to fix.
        min_records = job_config.get("min_snapshot_records")
        if min_records is None:
            min_records = MIN_SNAPSHOT_RECORDS
        record_count = _snapshot_record_count(snapshot)
        if min_records and record_count is not None and record_count < min_records:
            raise AOIStageError(
                f"Snapshot {snapshot_id} is ready but holds {record_count} records "
                f"(min_snapshot_records={min_records}); selection.filters={selection.filters!r} "
                "matched nothing — check the filter VALUES against the catalog"
            )

        ctx["snapshot_id"] = snapshot_id
        # Contract v2: the READY snapshot detail carries no storage_prefix —
        # the prefix (what DEFT receives as data_pool.path) is derived from
        # the manifests' absolute URIs.
        from src.backends.deft.storage_backend_client import derive_storage_prefix

        ctx["snapshot_uri"] = derive_storage_prefix(snapshot) or datastore.get_uri()
        self.logger.info("Snapshot %s ready at %s", snapshot_id, ctx["snapshot_uri"])

        # Optionally pull a dedicated KPI set from storage (DEFT's kpi/testing eval
        # set — training-time data, NOT DFW's eval-harness gate). train/val/mining
        # still come from the snapshot; DEFT materializes this prefix as kpi/testing.
        if (job_config.get("kpi") or {}).get("pull_from_storage"):
            ctx["kpi_set_uri"] = self._pull_kpi_set(datastore, ctx)
        # Lineage: what data produced this model. URIs only -- see DeftDataDetail.
        self._record_deft(
            ctx,
            {
                "use_case": ctx.get("use_case"),
                "model_name": ctx.get("model_name"),
                "data": {
                    "snapshot_id": ctx.get("snapshot_id"),
                    "snapshot_uri": ctx.get("snapshot_uri"),
                    # The EFFECTIVE value, mirroring what start_run passes: a
                    # KPI set pulled this run wins, else the static config uri.
                    # Recording only the ctx value would report null for a
                    # static-KPI run that did use a KPI set.
                    "kpi_set_uri": ctx.get("kpi_set_uri")
                    or (job_config.get("kpi") or {}).get("uri")
                    or None,
                },
            },
        )
        return ctx

    def _pull_kpi_set(self, datastore: Any, ctx: dict[str, Any]) -> str:
        """Create a KPI set via the Storage Backend and poll it to READY, returning
        its object-store prefix (handed to DEFT as kpi.path). Mirrors the snapshot
        create-then-poll discipline (honors cancellation each iteration)."""
        job_config = ctx.get("job_config") or {}
        # Distinct resource from the snapshot — use kpi_reason so storage/audit logs
        # don't tag a KPI set with the snapshot's reason (falls back to it).
        reason = job_config.get("kpi_reason") or job_config.get("snapshot_reason")
        kpi = datastore.register_kpi_set(reason=reason)
        kpi_id = kpi.get("kpi_id")
        if not kpi_id:
            # Fail fast: without an id the poll would call get_kpi_set(None) and
            # surface a misleading error instead of the real registration failure.
            raise AOIStageError(
                f"KPI-set registration returned no kpi_id (status={kpi.get('status')!r})"
            )
        # Same budget as the snapshot poll: the same backend does the same work
        # (including the video decode), so a deployment that had to raise the
        # snapshot ceiling would otherwise still time out here.
        max_attempts, interval_s = _snapshot_poll_budget(job_config)
        status, attempts = kpi.get("status"), 0
        while status not in ("ready", "failed", "deleted") and attempts < max_attempts:
            honor_cancellation(ctx.get("flywheel_run_id"))
            time.sleep(interval_s)
            kpi = datastore.get_kpi_set(kpi_id)
            status = kpi.get("status")
            attempts += 1
        if status != "ready":
            raise AOIStageError(
                f"KPI set {kpi_id} did not become ready (status={status!r} after "
                f"{attempts} polls / {attempts * interval_s:.0f}s)"
            )
        from src.backends.deft.storage_backend_client import derive_storage_prefix

        uri = derive_storage_prefix(kpi)
        if not uri:
            raise AOIStageError(f"KPI set {kpi_id} ready but no storage prefix could be derived")
        self.logger.info("KPI set %s ready at %s", kpi_id, uri)
        return uri


class RunDeftTrainingHandler(_AOIHandler):
    """Trigger a DEFT fine-tune on the snapshot and wait for the candidate.

    Identity (F3): this stage records ONLY the DEFT run id as ``deft_run_id`` —
    lineage that ties the candidate back to its fine-tune. It does NOT set
    ``candidate_version``: the serving/Triton version must be an integer minted
    by DFW's Mongo-owned serving-version counter, so ``candidate_version`` is
    assigned by ``RegisterCandidateHandler`` from that counter.
    """

    def _resolve_baseline_checkpoint(self, ctx: dict[str, Any]) -> str | None:
        """Resolve the checkpoint DEFT fine-tunes FROM (``config.model.checkpoint``).

        Iterative fine-tuning policy — each run fine-tunes on the previously
        PROMOTED model so gains compound; the config's
        ``deft.config.model.checkpoint`` is only the DAY-0 SEED. Resolution
        order:

        1. MLflow ``@production`` version's artifact source (the registry
           trail, when MLflow is enabled and reachable).
        2. The latest ``promoted`` promotion record's ``artifact_uri`` — the
           promotion system of record is Mongo, and every record carries the
           candidate's artifact locator, so continuity survives a disabled or
           unreachable MLflow (registration is best-effort by design).
        3. ``None`` → the config seed (first rollout); when the config has no
           seed either, the request builder fails loud with guidance instead
           of the real service's opaque 422.
        """
        deft_cfg = (ctx.get("job_config") or {}).get("deft") or {}
        explicit = ((deft_cfg.get("config") or {}).get("model") or {}).get("checkpoint")
        model_name = ctx.get("model_name")

        source = self._baseline_from_mlflow(model_name)
        if not source:
            source = self._baseline_from_promotion_records(model_name)
        if source:
            if explicit and source != explicit:
                self.logger.info(
                    "Baseline continuity: fine-tuning from the promoted model (%s), "
                    "superseding the config's day-0 seed",
                    source,
                )
        else:
            # Distinguish an expected first-rollout from a lost-continuity fall
            # through: the tiers above already log a WARNING with exc_info when
            # they *error*, so a clean None here means "nothing promoted yet".
            self.logger.info(
                "No promoted baseline resolved for %s (first rollout, or none "
                "registered yet); using the config day-0 seed",
                model_name,
            )
        return source

    def _baseline_from_mlflow(self, model_name: str | None) -> str | None:
        """The ``@production`` version's artifact source, or None."""
        if not model_name:
            self.logger.warning("Baseline resolution skipped the MLflow tier: model_name is unset")
            return None

        from src.config import settings
        from src.lib.integration.mlflow_client import MLflowClient

        try:
            client = MLflowClient(settings.system_config.mlflow_config)
            production = client.get_version_by_alias(model_name, "production")
            if not production:
                return None
            source = client.get_version_source(model_name, production)
            if source:
                self.logger.info(
                    "Baseline continuity via MLflow @production (version %s) for %s: %s",
                    production,
                    model_name,
                    source,
                )
            return source
        except Exception:
            self.logger.warning(
                "Could not resolve the baseline checkpoint from MLflow @production "
                "for %s; trying the promotion records",
                model_name,
                exc_info=True,
            )
            return None

    def _baseline_from_promotion_records(self, model_name: str | None) -> str | None:
        """The most recently promoted candidate's ``artifact_uri``, or None.

        MLflow-independent fallback: promotion state lives in Mongo, and the
        record's ``artifact_uri`` is the same locator the Promotion API's
        ``pull`` used to deploy the model — if it was deployable, it is
        trainable-from.
        """
        if not model_name:
            self.logger.warning(
                "Baseline resolution skipped the promotion-record tier: model_name is unset"
            )
            return None

        from src.api.promotion.service import PROMOTIONS_COLLECTION
        from src.tasks.promotion.controller import _get_db

        try:
            doc = _get_db()[PROMOTIONS_COLLECTION].find_one(
                # Only promoted records that actually carry a locator: a newest
                # record with a missing/empty artifact_uri must NOT silently drop
                # us to the day-0 seed — skip to the latest one that has it.
                # ``$nin: [None, ""]`` also excludes docs where the field is absent
                # (absent ~ null in Mongo). Secondary ``_id`` sort makes the
                # "latest" deterministic when created_at ties or is coarse.
                {
                    "model_name": model_name,
                    "state": "promoted",
                    "artifact_uri": {"$nin": [None, ""]},
                },
                sort=[("created_at", -1), ("_id", -1)],
            )
            if doc and doc.get("artifact_uri"):
                self.logger.info(
                    "Baseline continuity via promotion record %s (MLflow "
                    "unavailable or unregistered): %s",
                    doc.get("promotion_id"),
                    doc.get("artifact_uri"),
                )
                return str(doc["artifact_uri"])
            return None
        except Exception:
            self.logger.warning(
                "Could not resolve the baseline checkpoint from promotion records "
                "for %s; falling back to the config's checkpoint seed",
                model_name,
                exc_info=True,
            )
            return None

    def _record_deft_run_id(self, ctx: dict[str, Any], deft_run_id: str) -> None:
        """Persist DEFT's run id as soon as it exists, independent of progress.

        BEST EFFORT, same as :meth:`_record_deft_progress`: this is
        observability, never control flow.
        """
        flywheel_run_id = ctx.get("flywheel_run_id")
        if not flywheel_run_id:
            return
        try:
            from src.api.db_manager import get_db_manager

            get_db_manager().record_deft_run_id(flywheel_run_id, deft_run_id)
        except Exception:  # pragma: no cover - observability must not break a run
            self.logger.warning(
                "Could not persist DEFT run id for run %s", deft_run_id, exc_info=True
            )

    def _record_deft_progress(self, ctx: dict[str, Any], deft_run_id: str, progress: Any) -> None:
        """Persist DEFT's run id + last-changed progress onto the FlywheelRun.

        BEST EFFORT: a failure here must never fail a healthy training run. This
        is observability, not control flow -- the watchdog and the terminal-state
        checks are driven by the in-memory values regardless of whether this
        write lands.

        Called only on CHANGE, not per poll: the loop polls every 15s for hours,
        so a write per poll would be thousands of no-op updates per run.
        """
        flywheel_run_id = ctx.get("flywheel_run_id")
        if not flywheel_run_id:
            return
        if not isinstance(progress, dict):
            # The TERMINAL status carries no `progress` key. Recording that None
            # would erase the last known iteration/step at exactly the moment an
            # operator inspects a finished or failed run. Keep the last real one.
            return
        try:
            # Resolved lazily rather than injected: this handler is deliberately
            # constructed WITHOUT a db_manager (it drives DEFT, not Mongo), and
            # widening its contract for one observability write is not worth it.
            from src.api.db_manager import get_db_manager

            get_db_manager().record_deft_progress(
                flywheel_run_id,
                deft_run_id,
                progress,
                datetime.utcnow(),
            )
        except Exception:  # pragma: no cover - observability must not break a run
            self.logger.warning(
                "Could not persist DEFT progress for run %s", deft_run_id, exc_info=True
            )

    def execute(self, previous_result: dict[str, Any]) -> dict[str, Any]:
        from src.backends.factory import get_backend_provider

        ctx = dict(previous_result or {})
        job_config = ctx.get("job_config") or {}

        training = get_backend_provider().training()
        baseline_checkpoint = self._resolve_baseline_checkpoint(ctx)
        handle = training.start_run(
            snapshot_uri=ctx.get("snapshot_uri"),
            model_name=ctx.get("model_name"),
            deft_params=job_config.get("deft") or {},
            baseline_checkpoint=baseline_checkpoint,
            # The KPI set DEFT judges the candidate on (config.kpi.path): a KPI set
            # pulled from storage this run (ctx) wins, else the static kpi.uri; unset
            # (or empty) -> DEFT carves it from the training snapshot. The trailing
            # `or None` maps an empty-string uri to unset (start_run guards on
            # `is not None`, so "" would otherwise inject an empty kpi.path).
            kpi_set_uri=(ctx.get("kpi_set_uri") or (job_config.get("kpi") or {}).get("uri"))
            or None,
        )
        run_id = handle["id"]
        self.logger.info("Started DEFT run %s for snapshot %s", run_id, ctx.get("snapshot_id"))
        # Identity before progress, deliberately. A run that hangs or dies
        # before reporting anything is precisely when an operator needs this id
        # to go look at DEFT -- and it is the case the progress writer never
        # covers, because it declines to persist a non-dict payload.
        self._record_deft_run_id(ctx, run_id)
        # The checkpoint this run fine-tunes FROM, and the window the watchdog
        # measures. Both are known here and neither is recoverable afterwards:
        # a silent fall back to the day-0 seed looks like a weak generation, not
        # a lineage break.
        # The EFFECTIVE checkpoint. `_resolve_baseline_checkpoint` returns None
        # on a first rollout (or when continuity is lost) and the request
        # builder then applies the config seed, so recording its return alone
        # would report null in EXACTLY the case this field exists to reveal.
        seed = (((job_config.get("deft") or {}).get("config") or {}).get("model") or {}).get(
            "checkpoint"
        )
        self._record_deft(
            ctx,
            {
                "baseline": {"checkpoint": baseline_checkpoint or seed},
                "timeout_s": TRAINING_NO_PROGRESS_TIMEOUT_S,
            },
        )

        def _cancel_remote(reason: str) -> None:
            # Best-effort remote cancel so external GPU time isn't burned on a
            # run DFW has abandoned (user cancel, no-progress timeout, dead poll).
            try:
                training.cancel(run_id)
                self.logger.info("Cancelled remote DEFT run %s (%s)", run_id, reason)
            except Exception:
                self.logger.warning("Could not cancel remote DEFT run %s", run_id)

        status = training.get_status(run_id)
        self._record_deft_progress(ctx, run_id, status.get("progress"))
        # The first poll counts: a run already terminal here would otherwise
        # never record a `last_poll_at` at all.
        self._record_deft(ctx, {"last_poll_at": datetime.utcnow(), "poll_errors": 0})
        # NO-PROGRESS timeout (NMP wait-loop pattern): the timer resets whenever
        # DEFT reports new progress, so a legitimately long fine-tune is never
        # killed — only a run that reports nothing new for the timeout window.
        last_progress = status.get("progress")
        last_progress_at = time.monotonic()
        consecutive_errors = 0
        while not status.get("terminal"):
            # Same discipline as the NMP wait loops: honor a user cancel on
            # EVERY poll iteration.
            try:
                honor_cancellation(ctx.get("flywheel_run_id"))
            except Exception:
                _cancel_remote("user cancel")
                raise

            if time.monotonic() - last_progress_at > TRAINING_NO_PROGRESS_TIMEOUT_S:
                _cancel_remote("no progress")
                raise AOIStageError(
                    f"Data Flywheel cancelled DEFT run {run_id}: no new progress for "
                    f"{TRAINING_NO_PROGRESS_TIMEOUT_S:.0f}s "
                    f"(DFW_TRAINING_NO_PROGRESS_TIMEOUT_S). DEFT last reported: "
                    f"{last_progress!r}. NOTE: DEFT reports progress per STEP, so a "
                    f"single step longer than this window is indistinguishable from a "
                    f"hang -- raise the variable if this step is legitimately slow."
                )

            time.sleep(TRAINING_POLL_INTERVAL_S)
            try:
                status = training.get_status(run_id)
                consecutive_errors = 0
                # Every successful poll, NOT only on a progress change. Riding
                # the change branch would make this identical to `progress_at`
                # and so useless for its one purpose: telling a dead worker from
                # a slow step. It is also what clears `poll_errors` after a
                # recovery. One small write per poll interval is the cost of the
                # signal meaning anything.
                self._record_deft(
                    ctx,
                    {"last_poll_at": datetime.utcnow(), "poll_errors": consecutive_errors},
                )
            except Exception as e:
                # Tolerate brief status-poll blips during a healthy training;
                # only a persistently unreachable service fails the stage.
                consecutive_errors += 1
                # Published from the FAILURE branch, which is the only place the
                # count is ever non-zero: writing it beside the success reset
                # would pin it at 0 forever and make a degrading DEFT service
                # exactly as invisible as it was before the field existed.
                # `last_poll_at` deliberately does NOT advance here -- a failed
                # poll is not evidence the run is being watched successfully.
                self._record_deft(ctx, {"poll_errors": consecutive_errors})
                self.logger.warning(
                    "DEFT status poll failed for %s (%d/%d consecutive): %s",
                    run_id,
                    consecutive_errors,
                    TRAINING_STATUS_ERROR_TOLERANCE,
                    e,
                )
                if consecutive_errors >= TRAINING_STATUS_ERROR_TOLERANCE:
                    _cancel_remote("status polling dead")
                    raise AOIStageError(
                        f"DEFT status polling for {run_id} failed "
                        f"{consecutive_errors} consecutive times: {e}"
                    ) from e
                continue
            progress = status.get("progress")
            # ANY change counts as progress (not a monotone advance) — NMP
            # parity, and deliberately shape-agnostic: DEFT's Progress payload
            # may report iterations, steps, or both. Don't "fix" this to >.
            #
            # This is also what makes intra-step liveness work with NO change
            # here: when DEFT adds a field that moves DURING a step (an
            # `updated_at` heartbeat, deft-service !16), the whole-payload
            # comparison already sees it and resets the timer. If it is absent
            # the behaviour is exactly today's. Do not narrow this to a fixed
            # set of keys — that would silently opt out of every future signal.
            if progress != last_progress:
                last_progress = progress
                last_progress_at = time.monotonic()
                # Same branch as the watchdog reset, deliberately: what an
                # operator sees is then exactly what the timer is measuring.
                self._record_deft_progress(ctx, run_id, progress)

        if not status.get("succeeded"):
            # Include DEFT's own failure reason so the run error is actionable,
            # not a bare state string.
            detail = status.get("error") or status.get("convergence_reason")
            raise AOIStageError(
                f"DEFT run {run_id} ended non-successfully (status={status.get('status')!r}"
                + (f", reason: {detail}" if detail else "")
                + ")"
            )

        artifact = training.fetch_artifact(run_id)

        # Lineage only — the DEFT run id ties the candidate to its fine-tune.
        # candidate_version is NOT set here: it is the serving/Triton integer
        # minted from DFW's Mongo counter, allocated by RegisterCandidateHandler
        # (F3).
        ctx["deft_run_id"] = run_id
        ctx["artifact_uri"] = artifact.get("artifact_uri")
        ctx["checkpoint"] = artifact.get("checkpoint")
        # The checkpoint's content digest, threaded to the promotion record so the
        # controller hands the Model Manager a verifiable sha256 on `pull` (MM
        # requires it and rejects the pull with 409 checksum_mismatch otherwise).
        ctx["sha256"] = artifact.get("sha256")
        # The training/inference spec published alongside the checkpoint —
        # the serving stack stages it as spec.yaml next to the weights.
        ctx["training_spec_uri"] = artifact.get("training_spec")
        # DEFT's per-model best decision threshold — optional; threaded through
        # to the promotion record and staged as a metadata sidecar by serving.
        ctx["threshold"] = artifact.get("threshold")
        ctx["eval_metrics"] = artifact.get("metrics") or {}
        # Why DEFT's loop stopped (kpi_met / max_iterations / ...). Kept on the
        # ctx for later stages that read it; the durable record is
        # `result.convergence_reason`, written below. The previous comment here
        # claimed register/finalize persisted it, and neither did.
        if status.get("convergence_reason"):
            ctx["convergence_reason"] = status["convergence_reason"]
        # What the loop produced. `best_iteration` is returned by the client and
        # was never read: well below `total_iterations` means the model peaked
        # early and later iterations made it worse.
        self._record_deft(
            ctx,
            {
                "result": {
                    "convergence_reason": status.get("convergence_reason"),
                    "best_iteration": artifact.get("best_iteration"),
                    "eval_metrics": ctx.get("eval_metrics") or {},
                    "threshold": ctx.get("threshold"),
                    "checkpoint": ctx.get("checkpoint"),
                }
            },
        )
        self.logger.info("DEFT run %s produced checkpoint %s", run_id, ctx["checkpoint"])
        return ctx


class RunOfflineEvalGateHandler(_AOIHandler):
    """Offline KPI gate: verify the candidate's DEFT metrics — the gate to shadow.

    Runs the backend-owned evaluator through ``OfflineEval``. The gate is a
    CONFIRMATION of the metrics DEFT reported against DFW's structured
    ``kpi_target`` constraints — there is no DFW-side baseline comparison: the
    DEFT contract returns candidate-only metrics, and DEFT's own workflow runs
    a ``baseline_eval`` pre-loop step (baseline consideration is DEFT's job
    inside the loop). A failed gate marks the ``FlywheelRun`` FAILED and raises
    ``AOIStageError`` to abort the chain before register/promote.
    """

    def execute(self, previous_result: dict[str, Any]) -> dict[str, Any]:
        from src.backends.factory import get_backend_provider
        from src.config import EvalHarnessConfig
        from src.lib.eval_harness import OfflineEval

        ctx = dict(previous_result or {})
        job_config = ctx.get("job_config") or {}
        use_case = ctx["use_case"]

        evaluator = get_backend_provider().evaluator(use_case=use_case)
        # kpi_set = the AOI eval config (the structured kpi_target constraints).
        kpi_set = EvalHarnessConfig(**(job_config.get("eval") or {}))
        # candidate = the DEFT-reported metrics + artifact ref.
        candidate = {
            "metrics": ctx.get("eval_metrics") or {},
            "artifact_uri": ctx.get("artifact_uri"),
        }

        result = OfflineEval(evaluator).run(baseline=None, candidate=candidate, kpi_set=kpi_set)

        unmet = result.get("unmet") or []
        # Recorded BEFORE the raise: a blocked promotion is the case where the
        # verdict matters most, and the stage is about to abort the pipeline.
        self._record_deft(
            ctx,
            {
                "gate": {
                    "passed": bool(result.get("passed")),
                    "metrics": result.get("metrics") or {},
                    "unmet": [str(u) for u in unmet],
                }
            },
        )

        if not result.get("passed"):
            self.logger.warning(
                "Offline gate FAILED for run %s (metrics=%s, unmet=%s); aborting pipeline",
                ctx["flywheel_run_id"],
                result.get("metrics"),
                unmet,
            )
            # The task-layer wrapper (deft_tasks._run_stage) marks the run FAILED
            # with this message — one marking site for every stage failure.
            # `unmet` is named here because the previous wording said only that
            # the gate failed: WHICH constraint missed, and by how much, was
            # computed in eval.py and then dropped on the floor.
            raise AOIStageError(
                f"Offline KPI gate failed for candidate (deft_run_id={ctx.get('deft_run_id')}): "
                f"{'; '.join(str(u) for u in unmet) or 'no constraint detail reported'}. "
                "Candidate not promoted."
            )

        ctx["offline_metrics"] = result.get("metrics") or {}
        ctx["gate_passed"] = True
        self.logger.info("Offline gate PASSED for run %s", ctx["flywheel_run_id"])
        return ctx


def _observed_serving(model_name: str) -> ObservedServing | None:
    """What the serving stack holds for ``model_name``, or ``None`` if unknown.

    The floor is taken over the union of every version in the model repository
    and every version named by routing. The repo view is the important half: a
    version directory that exists but is unloaded still owns its slot, and
    staging into it overwrites the weights already there. Routing is folded in
    only to close the window where it references a version the repo index has not
    caught up to yet.

    Routing is deliberately NOT used on its own. It names at most three versions,
    and after a rollback it names a *lower* one than the repo holds — a floor
    taken from routing would then re-mint onto the retired candidate.

    ``None`` means "could not find out", which the caller treats very differently
    from "holds nothing": the first refuses to guess, the second is a legitimate
    day-0 deployment.
    """
    from src.lib.promotion.deployment_backend import build_deployment_backend
    from src.tasks.promotion.serving_version import observed_serving_floor

    try:
        state = build_deployment_backend().serving_state(model_name)
    except Exception:
        logger.warning(
            "Could not read serving state for %s; treating it as unknown",
            model_name,
            exc_info=True,
        )
        return None

    if state is None or not state.known_available:
        logger.info("Serving state for %s is unknown", model_name)
        return None

    # The two flags come from two different services and fail independently:
    # routing from the AOI Client, the repo view from AOI Inference. The repo
    # view is the authoritative half, so an unreadable routing lookup is not
    # worth failing closed over — but it must not pass silently either. Routing
    # is in the union to close the window where it names a version the repo
    # index has not caught up to, and that window reopens here.
    if not state.routing_available:
        logger.warning(
            "Routing state unavailable for %s; floor computed from the repo view alone",
            model_name,
        )

    versions = [*state.known_versions, *(entry.version for entry in state.routing)]
    floor = observed_serving_floor(versions)
    logger.info(
        "Serving stack for %s holds %s; floor=%s",
        model_name,
        sorted(set(versions)) or "nothing",
        floor,
    )
    return ObservedServing(floor=floor)


class RegisterCandidateHandler(_AOIHandler):
    """Mint the serving version from the Mongo counter; record MLflow best-effort (F3).

    The serving/Triton version is a monotonic INTEGER minted here from DFW's
    Mongo-owned counter (``serving_version_counters``) — the single allocation
    point, and it does NOT depend on MLflow. That integer becomes
    ``candidate_version``. The DEFT run id stays as ``deft_run_id`` lineage.

    MLflow registration is BEST-EFFORT: it is only the tracking/registry trail
    (it records the serving integer on ``promotion.version_label`` and the run
    id on ``promotion.deft_run_id``). If MLflow is disabled or registration
    fails, the promotion still proceeds on the Mongo-assigned integer — Mongo is
    the system of record (this preserves the MR !550 continuity guarantee that a
    disabled/unreachable MLflow never blocks a promotion).
    """

    def execute(self, previous_result: dict[str, Any]) -> dict[str, Any]:
        """Mint ``candidate_version`` from the Mongo serving-version counter, then
        record the candidate in MLflow best-effort, tagging its lineage and
        provenance (snapshot, spec, checkpoint digest, and decision threshold)."""
        from src.config import settings
        from src.lib.integration.mlflow_client import MLflowClient
        from src.tasks.promotion.controller import _get_db
        from src.tasks.promotion.serving_version import next_serving_version

        ctx = dict(previous_result or {})

        deft_run_id = ctx.get("deft_run_id")
        if not deft_run_id:
            raise AOIStageError(
                "register_candidate requires deft_run_id in the chain context "
                "(set by run_deft_training); none found."
            )

        # Single allocation point: mint the serving/Triton integer from DFW's
        # Mongo-owned counter, floored by what the serving stack says it already
        # holds. This does NOT depend on MLflow — the version authority lives in
        # Mongo (the promotion system of record); the live read only stops the
        # counter from handing out a number the stack is already using.
        db = _get_db()
        ctx["candidate_version"] = str(
            next_serving_version(
                db,
                ctx["model_name"],
                observed=_observed_serving(ctx["model_name"]),
            )
        )

        # MLflow is best-effort: record the tracking trail, but never block the
        # promotion on it. A disabled/unreachable registry is fine — the serving
        # version is already minted above.
        tags = {
            "snapshot_id": str(ctx.get("snapshot_id")),
            "use_case": str(ctx.get("use_case")),
            "promotion.state": "candidate",
        }
        if ctx.get("training_spec_uri"):
            # Locator of the training/inference spec published next to the
            # checkpoint — same registry-trail role as checkpoint_uri.
            tags["spec_uri"] = str(ctx["training_spec_uri"])
        checkpoint_sha256 = ctx.get("sha256") or ctx.get("checkpoint_sha256")
        if checkpoint_sha256:
            # Checkpoint content digest — same registry-trail role as spec_uri:
            # makes a registered version self-verifiable from the registry alone,
            # without joining back to the Mongo promotion record.
            tags["checkpoint_sha256"] = str(checkpoint_sha256)
        if ctx.get("threshold") is not None:
            # DEFT's per-model decision threshold — recorded for provenance so the
            # registered version carries the value the serving stack applies.
            tags["threshold"] = str(ctx["threshold"])

        registered = False
        try:
            client = MLflowClient(settings.system_config.mlflow_config)
            registered = bool(
                client.register_candidate(
                    model_name=ctx["model_name"],
                    deft_run_id=deft_run_id,
                    serving_version=ctx["candidate_version"],
                    artifact_uri=ctx.get("artifact_uri") or ctx.get("checkpoint") or "",
                    metrics=ctx.get("eval_metrics") or {},
                    tags=tags,
                )
            )
        except Exception:
            # Best-effort: MLflow is a tracking trail, not the version authority.
            # Log and proceed on the Mongo-assigned integer (MR !550 continuity).
            self.logger.warning(
                "MLflow candidate registration failed for %s v%s (deft_run_id=%s); "
                "proceeding on the Mongo-assigned serving version (MLflow is best-effort)",
                ctx["model_name"],
                ctx["candidate_version"],
                deft_run_id,
                exc_info=True,
            )

        ctx["registered"] = registered
        # `registered=False` is the quiet one: no @production alias means the
        # NEXT run resolves a staler baseline and silently regresses, so the
        # damage shows up a run later than the cause.
        self._record_deft(
            ctx,
            {
                "candidate": {
                    "version": ctx.get("candidate_version"),
                    "registered": registered,
                }
            },
        )
        self.logger.info(
            "Registered candidate %s v%s (deft_run_id=%s, snapshot=%s, mlflow=%s)",
            ctx["model_name"],
            ctx["candidate_version"],
            deft_run_id,
            ctx.get("snapshot_id"),
            "recorded" if registered else "skipped",
        )
        return ctx


class StartPromotionHandler(_AOIHandler):
    """Hand the registered version to the Promotion Controller (shadow→canary→full)."""

    def _baseline_from_promotion_records(self, model_name: str | None) -> str | None:
        """The most recently promoted candidate's ``candidate_version``, or None.

        PRIMARY baseline source (F3): promotion state lives in Mongo, and every
        promoted record's ``candidate_version`` is the serving/Triton integer
        that was rolled out — so the latest one IS the current baseline. This is
        MLflow-independent: continuity survives a disabled/unreachable MLflow
        (the MR !550 promotion-record fallback).
        """
        if not model_name:
            return None

        from src.api.promotion.service import PROMOTIONS_COLLECTION
        from src.tasks.promotion.controller import _get_db

        try:
            doc = _get_db()[PROMOTIONS_COLLECTION].find_one(
                # Only promoted records that carry a serving version. Secondary
                # ``_id`` sort makes "latest" deterministic when created_at ties.
                {
                    "model_name": model_name,
                    "state": "promoted",
                    "candidate_version": {"$nin": [None, ""]},
                },
                sort=[("created_at", -1), ("_id", -1)],
            )
            if doc and doc.get("candidate_version"):
                self.logger.info(
                    "Baseline resolved from promotion record %s: serving version %s",
                    doc.get("promotion_id"),
                    doc.get("candidate_version"),
                )
                return str(doc["candidate_version"])
            return None
        except Exception:
            self.logger.warning(
                "Could not resolve the baseline from promotion records for %s; "
                "trying MLflow @production as a fallback",
                model_name,
                exc_info=True,
            )
            return None

    def _baseline_from_mlflow(self, model_name: str | None) -> str | None:
        """The @production candidate's SERVING integer, or None.

        SECONDARY / fallback source, used only when Mongo has no promoted record
        yet. MLflow aliases bind to MLflow's own auto-assigned version *number*,
        NOT the serving/Triton integer — the serving integer lives on the
        ``promotion.version_label`` tag (F3). So resolve the alias to that tag
        (via ``get_serving_version_by_alias``); returning the raw alias number
        would hand the controller a wrong rollback/cleanup target once the Mongo
        counter and MLflow's numbering diverge. Returns None when the tag is
        absent — under F3 a wrong baseline is worse than none.
        """
        if not model_name:
            return None

        from src.config import settings
        from src.lib.integration.mlflow_client import MLflowClient

        try:
            client = MLflowClient(settings.system_config.mlflow_config)
            return client.get_serving_version_by_alias(model_name, "production")
        except Exception:
            self.logger.warning(
                "Could not resolve baseline from MLflow @production for %s; "
                "promotion proceeds without a rollback target",
                model_name,
            )
            return None

    def _presign_pull_locators(
        self, deft_config: Any, locators: dict[str, str | None]
    ) -> dict[str, str | None]:
        """Resolve object-store pull locators to short-lived presigned HTTPS URLs.

        Delegates to the shared module-level helper
        (:func:`src.backends.deft.presign.presign_pull_locators`) so the
        Promotion Controller can re-presign per pull attempt WITHOUT importing
        this handler module. Behavior is unchanged: only ``s3://``-or-bare-key
        locators are signed; ``http``/``https``/``file`` pass through untouched;
        a locator that needs signing but cannot be signed fails CLOSED — the
        helper's :class:`PresignError` is surfaced as ``AOIStageError`` so the
        promotion aborts before the Model Manager pull (which cannot proceed
        without a signed URL).
        """
        from src.backends.deft.presign import PresignError, presign_pull_locators

        try:
            return presign_pull_locators(deft_config, locators)
        except PresignError as e:
            raise AOIStageError(str(e)) from e

    def execute(self, previous_result: dict[str, Any]) -> dict[str, Any]:
        """Create the promotion record and hand it to the controller. Fails fast
        if the candidate has no checkpoint digest, since the Model Manager pull
        gate requires a verifiable sha256."""
        from src.api.promotion.models import PromotionRecord
        from src.api.promotion.service import PromotionService
        from src.tasks.promotion.controller import _get_db
        from src.tasks.promotion.tasks import advance_promotion

        ctx = previous_result or {}
        # Subscript, not .get("use_case", "aoi"): the old default made every
        # context that lost its use case an AOI promotion, so an ITS record was
        # graded by the AOI evaluator on a meaningless number.
        use_case = ctx["use_case"]
        model_name = ctx.get("model_name")
        candidate_version = ctx.get("candidate_version") or ctx.get("version")
        if not model_name or not candidate_version:
            raise ValueError(
                "start_promotion requires model_name + candidate_version in the chain "
                f"context; got model_name={model_name!r} candidate_version={candidate_version!r}"
            )

        # Baseline = what's serving today (its serving/Triton integer, the
        # rollback/cleanup target). Resolution order (F3 — Mongo is the system
        # of record; MLflow is best-effort):
        #   1. An explicit config override wins.
        #   2. PRIMARY: the last promoted promotion record in Mongo. Its
        #      candidate_version is already the serving integer, so it IS the
        #      current baseline — and this survives a disabled/unreachable
        #      MLflow (the MR !550 continuity guarantee).
        #   3. SECONDARY: the MLflow @production alias, used only as an
        #      observability/cross-check signal when Mongo has no promoted
        #      record yet.
        # Without a baseline the controller cannot roll back (and skips baseline
        # cleanup), so this resolution is what makes the rollback path reachable.
        baseline_version = ctx.get("baseline_version")
        if not baseline_version:
            baseline_version = self._baseline_from_promotion_records(model_name)
        if not baseline_version:
            baseline_version = self._baseline_from_mlflow(model_name)

        # Fail fast on a missing checkpoint digest. The Model Manager REQUIRES
        # a content digest on pull (PullRequest.sha256) and verifies it after
        # download; DEFT surfaces it via get_results -> ctx["sha256"]
        # ("checkpoint_sha256" is a legacy alias). Without it the controller
        # would hand MM an empty sha256 and the promotion could only die late
        # with an opaque 409 checksum_mismatch at the pull gate — so stop here
        # with an actionable error instead. (Extraction stays optional upstream:
        # a digest-less DEFT is a config/contract problem surfaced right here.)
        sha256 = ctx.get("sha256") or ctx.get("checkpoint_sha256")
        if not sha256:
            raise AOIStageError(
                f"No checkpoint sha256 for {model_name}@{candidate_version}: DEFT "
                "results did not surface a digest (best_checkpoint_sha256). The "
                "Model Manager pull gate requires it; aborting before promotion."
            )

        job_config = ctx.get("job_config") or {}

        # Resolve the pull locators to short-lived presigned HTTPS URLs right
        # before handing them to the Model Manager, so the credential-free link
        # it downloads is fresh. Only object-store locators (s3://, bare key)
        # are signed; http(s)/file locators pass through unchanged.
        #
        # ORDERING NOTE: register_candidate (MLflow) already ran BEFORE this
        # handler, so MLflow keeps the DURABLE s3:// checkpoint_uri for lineage;
        # only the EPHEMERAL presigned URL goes on the promotion record here. Do
        # NOT change register/MLflow — the registry trail must stay durable.
        from src.config import settings
        from src.config.deft import DEFTConfig

        # THIS RUN'S view of the DEFT endpoints. The artifact only exists in the
        # store its own training run wrote to, and presigning an ITS checkpoint
        # against AOI's Storage Backend does not fail here — it fails later at
        # the Model Manager pull, after the promotion record has been created.
        # ``for_use_case`` returns self when nothing overrides, so AOI's presign
        # is unchanged.
        deft_config = (
            getattr(settings.system_config, "deft_config", None) or DEFTConfig()
        ).for_use_case(use_case)
        # The DURABLE raw object-store locators (s3:// or bare key, or a
        # pass-through http/file dev URL). These are persisted on the record so
        # the Promotion Controller can RE-MINT a fresh presigned URL on every
        # pull attempt — a slow/retrying pull must never outlive the presigned
        # URL's TTL (deft_config.presign_expires_s). The presign below still
        # produces the FIRST attempt's URL / preserves backward compat.
        raw_artifact = ctx.get("artifact_uri") or ctx.get("checkpoint")
        raw_spec = ctx.get("training_spec_uri")
        resolved = self._presign_pull_locators(
            deft_config,
            {
                "artifact": raw_artifact,
                "spec": raw_spec,
            },
        )

        record = PromotionRecord(
            model_name=model_name,
            candidate_version=candidate_version,
            baseline_version=baseline_version,
            # Lineage: the DEFT run id that produced this candidate (the serving
            # version is candidate_version, the Mongo-minted integer — F3).
            deft_run_id=ctx.get("deft_run_id"),
            # Never defaulted — the online gate picks this record's evaluator
            # from it.
            use_case=use_case,
            # Linkage back to the run: the FlywheelRun stays in status
            # 'promoting' until this promotion reaches a terminal state; the
            # Promotion Controller then finalizes the run (completed/failed).
            flywheel_run_id=ctx.get("flywheel_run_id"),
            # The RUN'S policy (named configs differ from the default!) — the
            # controller applies this record's policy, not the global default.
            promotion_policy=job_config.get("promotion_policy"),
            state="promoting",
            pending_gate="pull",
            eval_metrics=ctx.get("eval_metrics"),
            # Artifact locator the controller hands to the Deployment Backend
            # pull — the presigned HTTPS URL (or a passed-through direct URL).
            artifact_uri=resolved["artifact"],
            # The published training/inference spec (staged as spec.yaml beside
            # the weights), also resolved to a presigned HTTPS URL. Recorded on
            # the promotion record now; handed to the Model Promotion API `pull`
            # once the contract adds its optional spec_uri field (PullRequest is
            # additionalProperties:false today).
            spec_uri=resolved["spec"],
            # DURABLE raw locators (NOT the ephemeral presigned URL): the
            # controller re-presigns from these on each pull attempt so a
            # slow/retrying pull cannot 403 on an expired URL. None for
            # pass-through http/file locators and older records (backward compat).
            artifact_locator=raw_artifact,
            spec_locator=raw_spec,
            # DEFT's per-model best decision threshold (optional); the controller
            # hands it to the Deployment Backend pull for the serving stack.
            threshold=ctx.get("threshold"),
            model_format=ctx.get("model_format") or job_config.get("model_format") or "onnx",
            # The checkpoint digest surfaced by DEFT (validated non-empty above);
            # the controller hands it to the Model Manager `pull` for post-download
            # integrity verification.
            sha256=sha256,
        )

        # Advancement past shadow needs online verdicts or a human (HITL / the
        # approve API). Online samples come from the Storage Backend eval-set
        # flow by default (deft_config.storage_backend_url; design §4.3) or the
        # legacy aoi_results_url override — if that source yields no samples
        # AND HITL is off everywhere the rollout holds in shadow, so make the
        # advancement path visible at handoff. (settings / DEFTConfig imported above.)
        deft_cfg = getattr(settings.system_config, "deft_config", None)
        if deft_cfg is not None:
            # Use-case-resolved so the handoff log names the store this run's
            # online samples come from. Kept None-preserving rather than reusing
            # ``deft_config`` above, whose ``or DEFTConfig()`` fallback would
            # push a DEFT-less system config off the "nothing configured" branch.
            deft_cfg = deft_cfg.for_use_case(use_case)
        results_url = getattr(deft_cfg, "aoi_results_url", None) or getattr(
            deft_cfg, "storage_backend_url", None
        )
        # storage_backend_url always HAS a value (a compile-time default), so a
        # deployment that never configured it is otherwise indistinguishable at
        # handoff — surface that misconfiguration risk explicitly.
        default_storage_url = DEFTConfig.model_fields["storage_backend_url"].default
        if not getattr(deft_cfg, "aoi_results_url", None) and results_url == default_storage_url:
            self.logger.warning(
                "Online samples will be collected from the COMPILE-TIME default "
                "storage_backend_url (%s) — if this deployment did not intend that, "
                "configure deft_config.storage_backend_url or the rollout will hold "
                "in shadow on empty collections.",
                results_url,
            )
        policy = job_config.get("promotion_policy") or {}
        # Only HITL at the ESCALATION gates provides an advancement path past
        # shadow — HITL at pull/deploy alone still leaves the rollout stalled.
        hitl_any = any((policy.get("hitl") or {}).get(g, False) for g in ("canary", "full"))
        if not hitl_any:
            self.logger.info(
                "Promotion %s@%s advances on online verdicts only (HITL off at the "
                "escalation gates); online samples from %s. If that source yields no "
                "samples the rollout holds in shadow until approved via "
                "POST /api/promotions/{id}/approve.",
                model_name,
                candidate_version,
                results_url,
            )

        service = PromotionService(_get_db())
        service.create(record)

        # Move the run into the intermediate PROMOTING status BEFORE enqueuing
        # the first advance, so the controller (which may finish very fast, e.g.
        # eager/test mode) can never race the status write.
        if ctx.get("flywheel_run_id"):
            from src.api.schemas import FlywheelRunStatus

            self.db_manager.update_flywheel_run_status(
                ctx["flywheel_run_id"], FlywheelRunStatus.PROMOTING
            )

        advance_promotion.delay(record.promotion_id)
        self.logger.info(
            "Started promotion %s for %s@%s; enqueued advance",
            record.promotion_id,
            model_name,
            candidate_version,
        )

        # "What is this run waiting for?" -- the most common real question,
        # since HITL defaults to true. `state == "pending_approval"` with a
        # `pending_gate` already answers it; no derived boolean, which could
        # only drift from the state it derives from.
        self._record_deft(
            ctx,
            {
                "promotion": {
                    "id": record.promotion_id,
                    "state": record.state,
                    "pending_gate": record.pending_gate,
                }
            },
        )
        return {**ctx, "promotion_id": record.promotion_id, "promotion_state": "promoting"}


class WaitForPromotionHandler(_AOIHandler):
    """Block the chain until the promotion reaches a terminal state.

    This is what serializes whole flywheel runs. The parent dispatcher
    (``run_nim_workflow_dag``, ``queue="parent_queue"``, concurrency=1) blocks
    on the entire chain's result (``src/tasks/tasks.py`` — ``apply_async()``
    then ``.get()``), so holding inside THIS stage holds the single parent slot
    for the whole rollout: no new flywheel run of any type starts until the
    current promotion terminates.

    Discipline mirrors the in-chain polling waits
    (``SelectAndSnapshotHandler`` / ``RunDeftTrainingHandler``): honor a user
    cancel on EVERY iteration and tolerate transient DB blips. Per design the
    wait is UNBOUNDED — it ends only on a terminal promotion state or user
    cancellation, with no wall-clock cap. (A promotion parked in shadow/HITL
    therefore blocks new training by intent; the cancel path force-fails
    non-terminal promotions via ``close_out_promotions`` so the wait exits.)

    Run finalization stays owned by the Promotion Controller's
    ``_finalize_flywheel_run``; this stage only waits — it never writes the
    run's terminal status, so there is no double-write with the controller.
    """

    def execute(self, previous_result: dict[str, Any]) -> dict[str, Any]:
        from src.api.promotion.service import TERMINAL_STATES, PromotionService
        from src.tasks.promotion.controller import _get_db

        ctx = dict(previous_result or {})
        promotion_id = ctx.get("promotion_id")
        if not promotion_id:
            # Nothing to wait on (e.g. start_promotion skipped upstream). Do not
            # wedge the chain — just pass through.
            self.logger.info("wait_for_promotion: no promotion_id in context; skipping wait")
            return ctx

        flywheel_run_id = ctx.get("flywheel_run_id")
        consecutive_errors = 0
        # Tracks the last state we published so the poll loop writes on change
        # only (see below).
        last_published: tuple[str | None, str | None] = (None, None)
        while True:
            # Same discipline as the NMP wait loops: honor a user cancel on
            # EVERY poll iteration. Raises FlywheelCancelledError, which
            # _run_stage turns into a graceful skip (the cancel path already
            # force-failed the promotion, so the wait exits promptly).
            honor_cancellation(flywheel_run_id)

            try:
                record = PromotionService(_get_db()).get(promotion_id)
                consecutive_errors = 0
            except Exception as e:
                # Tolerate brief DB blips during a (potentially long) wait; only
                # a persistently unreachable store fails the stage.
                consecutive_errors += 1
                self.logger.warning(
                    "wait_for_promotion: reading promotion %s failed (%d/%d consecutive): %s",
                    promotion_id,
                    consecutive_errors,
                    PROMOTION_STATUS_ERROR_TOLERANCE,
                    e,
                )
                if consecutive_errors >= PROMOTION_STATUS_ERROR_TOLERANCE:
                    raise AOIStageError(
                        f"Reading promotion {promotion_id} failed "
                        f"{consecutive_errors} consecutive times: {e}"
                    ) from e
                time.sleep(PROMOTION_POLL_INTERVAL_S)
                continue

            if record is None:
                # The record vanished under us — don't wedge the chain waiting
                # on something that no longer exists.
                self.logger.warning(
                    "wait_for_promotion: promotion %s not found; ending wait",
                    promotion_id,
                )
                ctx["promotion_state"] = "unknown"
                return ctx

            # Published on CHANGE only: this loop polls every 30s for as long as
            # a human takes to approve, and re-writing an unchanged state would
            # be thousands of no-op updates. The transition into
            # `pending_approval` is the one an operator is actually waiting to
            # see, and today it is re-read every 30s and discarded.
            if (record.state, record.pending_gate) != last_published:
                last_published = (record.state, record.pending_gate)
                self._record_deft(
                    ctx,
                    {
                        "promotion": {
                            "id": promotion_id,
                            "state": record.state,
                            "pending_gate": record.pending_gate,
                        }
                    },
                )

            if record.state in TERMINAL_STATES:
                ctx["promotion_state"] = record.state
                self.logger.info(
                    "wait_for_promotion: promotion %s reached terminal state %s",
                    promotion_id,
                    record.state,
                )
                return ctx

            time.sleep(PROMOTION_POLL_INTERVAL_S)


class FinalizeAOIRunHandler(_AOIHandler):
    """Close out the pipeline chain; the RUN completes with the promotion.

    The chain's job ends at the handoff — the run stays in status ``promoting``
    (set by ``StartPromotionHandler``) and is marked COMPLETED/FAILED by the
    Promotion Controller when the promotion reaches a terminal state
    (promoted / rolled_back / rejected / failed). This stage only records that
    the chain itself finished cleanly.
    """

    def execute(self, previous_result: dict[str, Any]) -> dict[str, Any]:
        ctx = dict(previous_result or {})

        ctx["finalized"] = True
        self.logger.info(
            "AOI chain complete for run %s (candidate=%s); awaiting promotion %s "
            "to reach a terminal state",
            ctx.get("flywheel_run_id"),
            ctx.get("candidate_version"),
            ctx.get("promotion_id"),
        )
        return ctx
