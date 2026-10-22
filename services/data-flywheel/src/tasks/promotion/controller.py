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

"""Promotion Controller: the DFW-side promotion state machine (design §3/§6/§10).

Walks a candidate through ``[pull, deploy, canary, full]`` by driving the
Deployment Backend and recording the MLflow alias/tag trail. Escalation gates
advance only on an online ``advance`` verdict; regression rolls back. HITL gates
pause at ``state=pending_approval``. Every transition passes a deterministic
``idempotency_key``.

Rollback never re-downloads: ``backend.rollback`` is ``apply(full_switch,
baseline)``, and the baseline's directory stays on disk for ``disk_reclaim``.
After the full switch it is also unloaded, and nothing reloads it, so
:meth:`PromotionController.rollback` prechecks and refuses.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from time import sleep
from typing import Any

from pymongo.database import Database

from src.api.db import get_db, init_db
from src.api.promotion.models import PromotionRecord, has_served
from src.api.promotion.service import (
    PROMOTIONS_COLLECTION,
    TERMINAL_STATES,
    PromotionService,
)
from src.config import PromotionPolicyConfig, settings
from src.lib.promotion.deployment_backend import build_deployment_backend

# BackendError is imported unconditionally, not behind a try/except stub: the
# poll loop branches on its http_status, which a stub would not have.
from src.lib.promotion.deployment_backend.base import (
    ApplyResult,
    BackendError,
    PromotionControl,
)
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.promotion.controller")

# Full gate vocabulary, in order. ``advance`` walks the subset the promotion's
# policy enables -- see :meth:`PromotionController.gate_order`.
GATE_ORDER: list[str] = ["pull", "deploy", "canary", "full"]

# Optional gates -> the ``deployment_stages`` flag governing each; ``pull`` and
# ``full`` are mandatory. Note the vocabulary shift: the gate is ``deploy`` but
# the serving role it creates, and the flag, are ``shadow``.
GATE_STAGE_FLAGS: dict[str, str] = {"deploy": "shadow", "canary": "canary"}

# TERMINAL_STATES is defined in src/api/promotion/service.py; imported above and
# re-exported here for the existing controller-facing importers.

# "Has anything served yet?" uses ``has_served`` (serving_role), not a gate-name
# set: which gate is the first SERVING one depends on deployment_stages.


def _get_db() -> Database:
    """Get the MongoDB database, initializing it if needed (Celery worker)."""
    try:
        return get_db()
    except RuntimeError:
        init_db()
        return get_db()


# Consecutive unreadable convergence polls before the gate gives up on the
# control plane rather than on the swap. Three rides out a rolling restart.
_MAX_CONSECUTIVE_POLL_READ_FAILURES = 3


def expectation_for(previous: Mapping[str, Any] | None, result: ApplyResult) -> dict[str, Any]:
    """The expectation to poll on, and to persist, for this apply.

    Usually ``result.applied``. A ``noop`` apply is the exception: no routing
    write happened, so its ``from_version`` does not describe the transition.
    The match is on the echoed ``key``, not on ``noop`` alone -- a record
    part-way up the ladder holds the previous gate's token, and keeping that
    would report success for a different apply.
    """
    if result.noop and previous and previous.get("key") == result.applied.get("key"):
        return dict(previous)
    return dict(result.applied)


class PromotionController:
    """State machine driving a single promotion through its gates."""

    def __init__(self):
        self.db = _get_db()
        self.service = PromotionService(self.db)
        self.policy = self._load_policy()
        self.backend = self._build_backend()
        self.mlflow = self._build_mlflow()

    @staticmethod
    def _load_policy() -> PromotionPolicyConfig:
        """Resolve the DEFAULT promotion policy from the active AOI job config.

        Post-``!528`` the policy lives per-job on ``DeftJobConfig.promotion_policy``,
        not as a top-level ``Settings`` field. Only the fallback: each promotion
        carries ITS run's policy on the record -- see :meth:`_apply_record_policy`.
        """
        aoi_job = settings.job_configs.get("aoi_job")
        policy = getattr(aoi_job, "promotion_policy", None)
        return policy if policy is not None else PromotionPolicyConfig()

    def _apply_record_policy(self, record: PromotionRecord) -> None:
        """Use the policy the promotion was created with (per-run > default).

        A named config can carry different HITL gates, canary ladder or polling
        than the deployment default. Unparseable or absent record policy falls
        back to the default loaded at construction.
        """
        raw = getattr(record, "promotion_policy", None)
        if not raw:
            return
        try:
            # Inside the guard: promotion_policy values are unvalidated, so a
            # malformed ``canary_steps`` makes the migration itself raise.
            self.policy = PromotionPolicyConfig(
                **self._migrate_legacy_policy(raw, record.promotion_id)
            )
        except Exception:
            logger.exception(
                "Invalid promotion_policy on record %s; using the default policy",
                record.promotion_id,
            )

    @staticmethod
    def _migrate_legacy_policy(raw: dict[str, Any], promotion_id: str) -> dict[str, Any]:
        """Translate pre-``deployment_stages`` policy shapes so they still parse.

        ``canary_steps.steps: []`` was the old way to ask for "no canary". It
        never worked (the empty ladder fell back to one 100% step the serving
        backend rejects), but in-flight records carry it and ``steps`` is now
        ``min_length=1``. Translating beats failing: the caller discards the
        WHOLE policy on a parse error, so a run that set ``hitl.full: true``
        against a default of ``false`` would silently lose a human gate.
        """
        if (raw.get("canary_steps") or {}).get("steps", [1]) != []:
            return raw
        logger.warning(
            "Promotion %s carries the legacy 'canary_steps.steps: []' opt-out; "
            "reading it as deployment_stages.canary=false",
            promotion_id,
        )
        return {
            **raw,
            "canary_steps": {},
            "deployment_stages": {**(raw.get("deployment_stages") or {}), "canary": False},
        }

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_backend():
        # A seam, so tests can swap the controller's backend wholesale.
        return build_deployment_backend()

    @staticmethod
    def _build_mlflow():
        from src.lib.integration.mlflow_client import MLflowClient

        return MLflowClient(settings.system_config.mlflow_config)

    def gate_order(self) -> list[str]:
        """The gate ladder for the CURRENTLY APPLIED policy.

        ``pull`` and ``full`` are mandatory; ``deploy`` (shadow) and ``canary``
        are toggled by ``deployment_stages`` so a deployment can match its
        serving backend -- RTVI-CV has no canary routing, so ITS runs
        ``canary: false`` rather than failing at the canary gate.

        Callers must have applied the record's policy first; the ladder is
        per-promotion, not global.
        """
        stages = self.policy.deployment_stages
        return [
            gate
            for gate in GATE_ORDER
            if gate not in GATE_STAGE_FLAGS or getattr(stages, GATE_STAGE_FLAGS[gate], True)
        ]

    def _next_gate(self, current_gate: str | None) -> str | None:
        """Next ENABLED gate after ``current_gate``.

        ``current_gate`` may itself be disabled -- a policy edit under an
        in-flight promotion leaves a persisted ``pending_gate`` off the ladder --
        so the walk starts from its position in the full vocabulary.
        """
        enabled = self.gate_order()
        if current_gate is None:
            return enabled[0] if enabled else None
        if current_gate not in GATE_ORDER:
            return None
        for gate in GATE_ORDER[GATE_ORDER.index(current_gate) + 1 :]:
            if gate in enabled:
                return gate
        return None

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _update(self, promotion_id: str, **fields: Any) -> None:
        update: Any = {"$set": fields}
        # ``settled_at`` is stamped centrally so a terminal transition added
        # later cannot forget to. The bug it fixes: with no finish time the sweep
        # aged records from ``created_at``, so a promotion created eight days ago
        # that fails today read as settled eight days ago and cleared retention
        # on the very next tick.
        #
        # Re-stamped on a CHANGE of terminal state (promoted in January, rolled
        # back in March ages from March), not on a repeat: ``$eq`` sees the
        # PRE-update ``state``, as a pipeline ``$set`` evaluates against its input.
        #
        # One write, not two: a worker death between them leaves the record
        # terminal and undated, and ``_settled_at`` has no fallback, so that
        # directory leaks forever. Mongo 4.2+ for pipeline updates; pinned 7.0.
        #
        # ``$literal`` because a pipeline ``$set`` EVALUATES its values: a bare
        # ``"$..."`` would be read as a field path and a dict as an operator.
        if fields.get("state") in TERMINAL_STATES:
            now = datetime.now(UTC)
            update = [
                {
                    "$set": {
                        **{key: {"$literal": value} for key, value in fields.items()},
                        "settled_at": {
                            "$cond": [
                                {"$eq": ["$state", fields["state"]]},
                                {"$ifNull": ["$settled_at", now]},
                                now,
                            ]
                        },
                    }
                }
            ]
        self.db[PROMOTIONS_COLLECTION].update_one({"promotion_id": promotion_id}, update)

    @staticmethod
    def _enqueue_advance(promotion_id: str) -> None:
        from src.tasks.promotion.tasks import advance_promotion

        advance_promotion.delay(promotion_id)

    # ------------------------------------------------------------------
    # MLflow alias/tag trail (best-effort; never blocks the rollout)
    # ------------------------------------------------------------------
    def _resolve_mlflow_version(self, record: PromotionRecord) -> str:
        """Resolve the MLflow model-version for alias moves (falls back to label)."""
        try:
            version = self.mlflow.get_version_by_alias(record.model_name, "candidate")
        except Exception:
            version = None
        return version or record.candidate_version

    def _move_alias(
        self, record: PromotionRecord, alias: str, gate: str, version: str | None = None
    ) -> None:
        try:
            mlflow_version = version or self._resolve_mlflow_version(record)
            self.mlflow.set_alias(record.model_name, alias, mlflow_version)
            self.mlflow.set_promotion_tags(
                record.model_name,
                mlflow_version,
                {
                    "promotion.state": record.state,
                    f"gate.{gate}.at": datetime.utcnow().isoformat(),
                    f"gate.{gate}.eval": str(record.verdict),
                },
            )
        except Exception:
            logger.exception("MLflow alias/tag move failed (%s -> %s)", record.model_name, alias)

    # ------------------------------------------------------------------
    # FlywheelRun closure (the run completes with the promotion)
    # ------------------------------------------------------------------
    def _finalize_flywheel_run(self, record: PromotionRecord, outcome: str) -> None:
        """Close the parent FlywheelRun once the promotion is terminal.

        The AOI chain leaves the run ``promoting``; it turns COMPLETED/FAILED
        only here. ``promoted`` completes it, every other outcome fails it with
        that outcome as the reason. ``mark_flywheel_run_error`` is
        first-write-wins, so a failed->rolled_back sequence keeps the root cause.
        Best-effort: run bookkeeping never blocks the rollout.
        """
        run_id = getattr(record, "flywheel_run_id", None)
        if not run_id:
            return
        from src.api.db_manager import get_db_manager

        try:
            db = get_db_manager()
            if outcome == "promoted":
                db.mark_flywheel_run_completed(run_id, datetime.utcnow())
            else:
                db.mark_flywheel_run_error(
                    run_id,
                    f"Promotion {record.promotion_id} ended '{outcome}' "
                    f"(candidate {record.model_name}@{record.candidate_version}).",
                    finished_at=datetime.utcnow(),
                )
            logger.info("FlywheelRun %s finalized (%s)", run_id, outcome)
        except Exception:
            logger.exception(
                "Failed to finalize FlywheelRun %s after promotion outcome %s", run_id, outcome
            )

    # ------------------------------------------------------------------
    # Swap polling
    # ------------------------------------------------------------------
    def _wait_for_convergence(
        self, applied: Mapping[str, Any], *, what: str
    ) -> tuple[bool, str | None]:
        """Poll until the applied intent is live. ``(True, None)`` once it is.

        Three stopping rules: converged, the Model Manager called the reason
        terminal, or the budget (``poll_max_attempts`` * ``poll_interval_s``,
        polled inline) ran out.

        Any other failure (a 404 from a replica mid-roll, a 503, a refused
        connection) spends one tick: a control plane we could not reach has told
        us nothing about the swap. Two exceptions -- 400/422 means the
        expectation will never be accepted, and several unreadable polls in a row
        escape as a retryable error so the Celery backoff handles an outage.
        """
        attempts = max(1, int(self.policy.poll_max_attempts))
        failures = 0
        last: str | None = None
        for attempt in range(attempts):
            try:
                report = self.backend.convergence(applied)
            except BackendError as exc:
                if exc.http_status in (400, 422):
                    logger.error(
                        "%s: the Model Manager refused the persisted expectation (%s: %s)",
                        what,
                        exc.code,
                        exc.message,
                    )
                    return False, f"expectation rejected ({exc.code})"
                failures += 1
                last = f"poll unreadable: {exc.code} (HTTP {exc.http_status})"
                if failures >= _MAX_CONSECUTIVE_POLL_READ_FAILURES:
                    raise BackendError(
                        "internal",
                        f"{what}: {failures} consecutive convergence polls could not be "
                        f"answered ({exc.code}); retrying the whole gate",
                        retryable=True,
                        http_status=exc.http_status,
                    ) from exc
                logger.warning("%s: %s; spending a poll", what, last)
            else:
                failures = 0
                if report.converged:
                    return True, None
                reason = report.reason
                last = (
                    "not converged (no reason given)"
                    if reason is None
                    else f"{reason.code}: {reason.message}"
                )
                if reason is not None and reason.terminal:
                    logger.error("%s cannot converge: %s", what, last)
                    return False, last
            if attempt < attempts - 1:
                sleep(self.policy.poll_interval_s)

        if failures:
            # We learned nothing about the swap, so this is not a readiness
            # timeout. Keep the branch: with poll_max_attempts=1 (the e2e
            # configs) the loop never reaches the consecutive-failure ceiling
            # and one transient 404 would fail the gate.
            raise BackendError(
                "internal",
                f"{what}: the convergence poll budget ({attempts}) expired with the "
                f"Model Manager still unreadable ({last}); retrying the whole gate",
                retryable=True,
            )

        logger.error(
            "%s did not converge after %d polls (readiness_timeout); last: %s",
            what,
            attempts,
            last,
        )
        return False, last

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------
    def start(self, promotion_id: str) -> None:
        """Begin a promotion at the first gate.

        The AOI pipeline's ``start_promotion`` already creates the record and
        enqueues ``advance``; this is the entry point for other callers.
        """
        record = self.service.get(promotion_id)
        if record is None:
            logger.warning("start: unknown promotion %s", promotion_id)
            return
        if record.state in TERMINAL_STATES:
            # Restarting would flip a promoted/rejected record back to
            # "promoting" and re-drive gates against a concluded rollout.
            logger.info("start: promotion %s already terminal (%s)", promotion_id, record.state)
            return
        fields: dict[str, Any] = {}
        if record.pending_gate is None:
            fields["pending_gate"] = GATE_ORDER[0]
        if record.state != "promoting":
            fields["state"] = "promoting"
        if fields:
            self._update(promotion_id, **fields)
        self._enqueue_advance(promotion_id)

    def _run_cancelled(self, record: PromotionRecord) -> bool:
        """True if the promotion's parent FlywheelRun was cancelled by the user.

        Fail-open: an unreadable flag must never block a healthy rollout.
        """
        run_id = getattr(record, "flywheel_run_id", None)
        if not run_id:
            return False
        try:
            from src.api.db_manager import get_db_manager

            return bool(get_db_manager().is_flywheel_run_cancelled(run_id))
        except Exception:
            # Fail open, but VISIBLY: silent misses are undiagnosable.
            logger.warning(
                "advance: could not read cancellation flag for run %s; failing open",
                run_id,
                exc_info=True,
            )
            return False

    def advance(self, promotion_id: str) -> None:
        """Advance the promotion past its current gate (design §3)."""
        record = self.service.get(promotion_id)
        if record is None:
            logger.warning("advance: unknown promotion %s", promotion_id)
            return
        if record.state in TERMINAL_STATES:
            logger.info("advance: promotion %s already terminal (%s)", promotion_id, record.state)
            return
        self._apply_record_policy(record)
        gate = record.pending_gate
        if gate is None:
            logger.info("advance: promotion %s has no pending gate", promotion_id)
            return

        # 0. A cancelled run must not keep rolling out: archive before serving,
        #    roll the traffic back once serving.
        if self._run_cancelled(record):
            if not has_served(record):
                logger.info("advance: run cancelled; rejecting promotion %s", promotion_id)
                self._update(promotion_id, state="rejected", pending_gate=None)
                record.state = "rejected"
                self._finalize_flywheel_run(record, "rejected")
                # State written first, so this cannot re-fail it; ``record``
                # still carries the gate we stopped at.
                self._evict_never_routed_candidate(record)
            else:
                logger.info("advance: run cancelled while serving; rolling back %s", promotion_id)
                self.rollback(promotion_id)
            return

        # 0b. A policy edit (or a named config) may have disabled this gate
        #     after the promotion started. Skip forward, do not run it.
        if gate not in self.gate_order():
            skipped_to = self._next_gate(gate)
            if skipped_to is None:
                logger.error(
                    "advance: gate %r is disabled and has no successor for %s",
                    gate,
                    promotion_id,
                )
                return
            logger.warning(
                "advance: gate %s disabled by policy; skipping to %s for %s",
                gate,
                skipped_to,
                promotion_id,
            )
            fields: dict[str, Any] = {"pending_gate": skipped_to}
            if record.state in ("approved", "pending_approval"):
                # An approval granted, or being sought, against THIS gate must
                # not carry onto the one we skip to: spending an approval of a
                # 5% canary on a 100% switch is a larger commitment than the
                # approver saw. Re-arm so the new gate is decided on its own.
                fields["state"] = "promoting"
                logger.warning(
                    "advance: dropping the %s at disabled gate %s for %s; %s will be re-gated",
                    "approval granted" if record.state == "approved" else "pending approval",
                    gate,
                    promotion_id,
                    skipped_to,
                )
            self._update(promotion_id, **fields)
            self._enqueue_advance(promotion_id)
            return

        # Consume a prior approval FIRST: it overrides eval gating for this one
        # transition, and is the only escape when no online source is configured.
        approved = record.state == "approved"
        if approved:
            self._update(promotion_id, state="promoting")
            record.state = "promoting"

        # 1. Escalation gates need a fresh online 'advance' verdict, unless a
        #    human just approved or online eval is off. "Escalation" is
        #    ``serving_role is not None``, not a gate list: with shadow off,
        #    canary is the FIRST serving stage, and demanding a verdict there
        #    would deadlock the rollout.
        online_enabled = self.policy.eval_harness.online_enabled
        needs_verdict = has_served(record) and not approved and online_enabled
        if needs_verdict:
            verdict = record.verdict
            if verdict == "rollback":
                logger.info("advance: regression verdict at %s -> rollback %s", gate, promotion_id)
                self.rollback(promotion_id)
                return
            if verdict != "advance":
                logger.info("advance: gate %s holding (verdict=%s)", gate, verdict)
                return

        # 2. HITL pause (once per gate; canary pauses only at step 0). NOT also
        #    gated on ``state == "promoting"``: the only other state reaching
        #    here with ``hitl_on`` is ``pending_approval``, a duplicate advance
        #    draining after the pause was written, and that must re-pause rather
        #    than run the gate unapproved. Re-pausing is idempotent.
        hitl_on = bool(getattr(self.policy.hitl, gate, False)) and not approved
        pause = hitl_on
        if gate == "canary" and record.canary_step_index > 0:
            pause = False
        if pause:
            self._update(promotion_id, state="pending_approval")
            record.state = "pending_approval"
            try:
                self.mlflow.set_promotion_tags(
                    record.model_name,
                    self._resolve_mlflow_version(record),
                    {"promotion.pending_gate": gate, "promotion.state": "pending_approval"},
                )
            except Exception:
                logger.exception("MLflow pending-gate tag failed for %s", promotion_id)
            logger.info("advance: gate %s awaiting HITL approval for %s", gate, promotion_id)
            return

        # 3. Run the gate transition through the Deployment Backend.
        try:
            self._run_gate(record, gate)
        except BackendError as e:
            self._handle_backend_error(record, gate, e)

    # ------------------------------------------------------------------
    # Pull-URL re-presign (design: harden the pull gate for slow/large pulls)
    # ------------------------------------------------------------------
    def _presign_for_pull(self, record: PromotionRecord) -> tuple[str | None, str | None]:
        """Re-mint fresh presigned pull URLs from the record's DURABLE raw locators.

        URLs stored at promotion start have a bounded TTL
        (``deft_config.presign_expires_s``), so a slow or retrying pull 403s and
        surfaces as ``registry_unreachable``. Re-signing per attempt removes that
        race while keeping the Model Manager credential-free.

        Signs against THIS record's use case store -- siblings do not share one.

        NO-OP when there is no ``deft_config``, no presign helper, or the raw
        locator is a pass-through ``http``/``https``/``file`` URL. Any presign
        error falls back to the stored URLs: a presign hiccup must NEVER
        hard-fail the pull.
        """
        artifact_uri = record.artifact_uri
        spec_uri = record.spec_uri

        deft_config = getattr(settings.system_config, "deft_config", None)
        if deft_config is None:
            return artifact_uri, spec_uri
        for_use_case = getattr(deft_config, "for_use_case", None)

        # Lazy import keeps the controller free of an AOI/Storage dependency.
        try:
            from src.backends.deft.presign import needs_signing, presign_pull_locators
        except Exception:  # pragma: no cover - defensive: helper always present
            return artifact_uri, spec_uri

        raw = {
            "artifact": getattr(record, "artifact_locator", None),
            "spec": getattr(record, "spec_locator", None),
        }
        # Nothing durable needs signing: old records, pass-through URLs, empties.
        if not any(uri and needs_signing(uri) for uri in raw.values()):
            return artifact_uri, spec_uri

        # Scope to THIS record's use case first: signing an ITS pull against the
        # AOI store yields a valid signature for the wrong bucket, which 404s --
        # or worse, hits a same-named key and stages AOI weights.
        #
        # INSIDE the try because ``for_use_case`` FAILS CLOSED on an undeclared
        # use case, and no presign problem may fail a pull. Refusing an
        # unresolvable store is the snapshot stage's job.
        scoped: Any = None
        try:
            scoped = for_use_case(record.use_case) if callable(for_use_case) else deft_config
            resolved = presign_pull_locators(scoped, raw)
        except Exception:
            # ``scoped is None`` means ``for_use_case`` refused, so the fix is a
            # config line, not a storage investigation. Say which, in the log.
            if scoped is None:
                logger.warning(
                    "Re-presign before pull failed for %s: use_case %r has no entry in "
                    "deft_config.use_case_endpoints, so its store cannot be resolved; "
                    "falling back to the stored pull URL. Declare the use case (an empty "
                    "entry inherits the top-level endpoints)",
                    record.promotion_id,
                    record.use_case,
                    exc_info=True,
                )
            else:
                logger.warning(
                    "Re-presign before pull failed for %s; falling back to the stored pull URL",
                    record.promotion_id,
                    exc_info=True,
                )
            return artifact_uri, spec_uri

        # Only override a role we actually re-signed; keep the stored URL for a
        # role with no raw locator (backward compat) or a pass-through locator.
        fresh_artifact = resolved.get("artifact") if raw.get("artifact") else None
        fresh_spec = resolved.get("spec") if raw.get("spec") else None
        artifact_uri = fresh_artifact or artifact_uri
        spec_uri = fresh_spec if raw.get("spec") else spec_uri

        updates: dict[str, Any] = {}
        if artifact_uri != record.artifact_uri:
            updates["artifact_uri"] = artifact_uri
        if spec_uri != record.spec_uri:
            updates["spec_uri"] = spec_uri
        if updates:
            try:
                self._update(record.promotion_id, **updates)
            except Exception:  # pragma: no cover - persistence is best-effort
                logger.warning(
                    "Could not persist re-presigned pull URLs for %s", record.promotion_id
                )
        return artifact_uri, spec_uri

    # ------------------------------------------------------------------
    # Gate transitions
    # ------------------------------------------------------------------
    def _run_gate(self, record: PromotionRecord, gate: str) -> None:
        promotion_id = record.promotion_id
        model_name = record.model_name
        candidate = record.candidate_version

        if gate == "pull":
            key = f"{promotion_id}:pull"
            artifact_uri, spec_uri = self._presign_for_pull(record)
            self.backend.pull(
                model_name,
                candidate,
                artifact_uri or "",
                record.model_format,
                record.sha256 or "",
                spec_uri=spec_uri,
                threshold=record.threshold,
                idempotency_key=key,
            )
            # Pull loads; it swaps no traffic.
            next_after_pull = self._next_gate("pull")
            self._update(promotion_id, pending_gate=next_after_pull)
            self._enqueue_advance(promotion_id)
            # deft_run_id is the lineage back to the fine-tune.
            logger.info(
                "pull ok %s v%s (deft_run_id=%s) -> %s gate",
                model_name,
                candidate,
                getattr(record, "deft_run_id", None),
                next_after_pull,
            )
            return

        if gate == "deploy":
            key = f"{promotion_id}:deploy"
            result = self.backend.apply(
                model_name, candidate, PromotionControl.SHADOW, idempotency_key=key
            )
            expectation = expectation_for(record.applied, result)
            # Persist the poll token first: a worker that dies in the wait
            # resumes against the same expectation.
            self._update(promotion_id, applied=expectation, swap_id=result.swap_id)
            record.applied = expectation
            ok, why = self._wait_for_convergence(
                expectation, what=f"{promotion_id} deploy(shadow) {model_name}@{candidate}"
            )
            if not ok:
                self._on_swap_failure(record, gate, reason=why)
                return
            self._update(
                promotion_id,
                serving_role="shadow",
                serving_since=datetime.now(UTC),
                verdict=None,
                pending_gate=self._next_gate("deploy"),
                canary_step_index=0,
            )
            record.state = record.state  # unchanged (promoting)
            self._move_alias(record, "shadow", gate)
            if not self.policy.eval_harness.online_enabled:
                # Nothing will produce the verdict that normally drives the
                # next advance, so chain here rather than one gate per beat.
                logger.info(
                    "deploy(shadow) ok %s@%s; online eval off -> advancing", model_name, candidate
                )
                self._enqueue_advance(promotion_id)
                return
            logger.info("deploy(shadow) ok %s@%s; awaiting shadow verdict", model_name, candidate)
            return

        if gate == "canary":
            steps = list(self.policy.canary_steps.steps)
            idx = record.canary_step_index
            if idx >= len(steps):
                # Nothing left to step; escalate to full.
                escalate_to = self._next_gate("canary")
                self._update(promotion_id, pending_gate=escalate_to, verdict=None)
                logger.info("canary complete for %s; escalating to %s", promotion_id, escalate_to)
                return
            weight = steps[idx] / 100.0
            key = f"{promotion_id}:canary:{idx}"
            result = self.backend.apply(
                model_name,
                candidate,
                PromotionControl.CANARY,
                canary_weight=weight,
                idempotency_key=key,
            )
            expectation = expectation_for(record.applied, result)
            # Persist the poll token first: a worker that dies in the wait
            # resumes against the same expectation.
            self._update(promotion_id, applied=expectation, swap_id=result.swap_id)
            record.applied = expectation
            ok, why = self._wait_for_convergence(
                expectation,
                what=f"{promotion_id} canary[{idx}] {model_name}@{candidate} at {weight}",
            )
            if not ok:
                self._on_swap_failure(record, gate, reason=why)
                return
            if idx == 0:
                self._move_alias(record, "canary", gate)
            next_idx = idx + 1
            fields: dict[str, Any] = {
                "serving_role": "canary",
                # Each canary step is its own observation window.
                "serving_since": datetime.now(UTC),
                "verdict": None,
                "canary_step_index": next_idx,
            }
            if next_idx >= len(steps):
                # Last canary step done; the next advance (post-verdict) runs full.
                fields["pending_gate"] = self._next_gate("canary")
            self._update(promotion_id, **fields)
            logger.info(
                "canary step %d/%d (%.0f%%) ok for %s; %s",
                next_idx,
                len(steps),
                steps[idx],
                promotion_id,
                "online eval off -> advancing"
                if not self.policy.eval_harness.online_enabled
                else "awaiting canary verdict",
            )
            if not self.policy.eval_harness.online_enabled:
                self._enqueue_advance(promotion_id)
            return

        if gate == "full":
            key = f"{promotion_id}:full"
            result = self.backend.apply(
                model_name, candidate, PromotionControl.FULL_SWITCH, idempotency_key=key
            )
            expectation = expectation_for(record.applied, result)
            # Persist the poll token first: a worker that dies in the wait
            # resumes against the same expectation.
            self._update(promotion_id, applied=expectation, swap_id=result.swap_id)
            record.applied = expectation
            ok, why = self._wait_for_convergence(
                expectation, what=f"{promotion_id} full_switch {model_name}@{candidate}"
            )
            if not ok:
                self._on_swap_failure(record, gate, reason=why)
                return
            self._update(
                promotion_id,
                serving_role="default",
                state="promoted",
                pending_gate=None,
            )
            record.state = "promoted"
            self._move_alias(record, "production", gate)
            # ``unload``, not ``cleanup``: the directory stays, so no rollback
            # re-downloads -- but the window closes in MEMORY. State written
            # above first, so this cannot re-fail the promotion.
            if record.baseline_version:
                self._evict_and_stamp(
                    record,
                    record.baseline_version,
                    why="baseline superseded by the full switch",
                )
            self._finalize_flywheel_run(record, "promoted")
            logger.info(
                "promotion %s promoted (%s v%s live, deft_run_id=%s)",
                promotion_id,
                model_name,
                candidate,
                getattr(record, "deft_run_id", None),
            )
            return

        logger.error("advance: unknown gate %r for %s", gate, promotion_id)

    # ------------------------------------------------------------------
    # Eviction (the only place DFW frees a GPU slot)
    # ------------------------------------------------------------------
    def _evict_and_stamp(self, record: PromotionRecord, version: str, *, why: str) -> None:
        """Unload ``version``, and stamp the record only if the unload returned.

        Every DFW eviction comes through here, so the stamp cannot drift from the
        unload it attests to. After the call, never in a ``finally``: a stamp on
        the failure path would nominate a version still resident on a GPU.

        A failed eviction never fails the promotion and never re-raises -- every
        caller has already written its terminal state. Re-raising would be worst
        at the ``rollback`` call site, inside an ``except BackendError`` writing
        ``state="failed"``: it would re-fail a rollback that succeeded.

        Hence ``Exception``, not ``BackendError``: ``unload`` indexes
        ``data["version"]`` and can raise a bare ``KeyError``, and an out-of-tree
        backend may raise anything.
        """
        try:
            self.backend.unload(record.model_name, version)
        except Exception:
            logger.exception(
                "unload of %s@%s (%s) failed for %s; it keeps its GPU slot and, unstamped, "
                "its directory stays uncollectable by the disk-reclaim sweep",
                record.model_name,
                version,
                why,
                record.promotion_id,
            )
            return
        try:
            # Not ``_update``: the stamp APPENDS, because one record can evict
            # twice -- full switch takes the baseline, a later rollback the
            # candidate -- and an overwrite strands the first forever.
            self.db[PROMOTIONS_COLLECTION].update_one(
                {"promotion_id": record.promotion_id},
                {
                    "$set": {"evicted_at": datetime.now(UTC)},
                    "$addToSet": {"evicted_versions": version},
                },
            )
        except Exception:
            # Must not escape: on the full-switch path it would leave the parent
            # run ``promoting`` forever, and on the rejection path, inline under
            # the approval API, it would 500 a rejection already in Mongo. Logged
            # apart from the unload failure -- here the GPU slot WAS freed.
            logger.exception(
                "stamping the eviction of %s@%s (%s) for %s failed; the unload DID happen and "
                "the GPU slot is free, but unstamped its directory stays uncollectable by the "
                "disk-reclaim sweep",
                record.model_name,
                version,
                why,
                record.promotion_id,
            )
            return
        logger.info(
            "unloaded %s@%s (%s) for %s; its directory is deliberately left on disk",
            record.model_name,
            version,
            why,
            record.promotion_id,
        )

    def _evict_never_routed_candidate(self, record: PromotionRecord) -> None:
        """Evict a candidate a terminal promotion leaves loaded but unrouted.

        Nothing ever routed this candidate, so each precondition is checked:

        * ``has_served`` -- without it a rejected canary still carrying 5% of
          production would be unloaded.
        * ``applied is None``. ``serving_role`` is stamped only AFTER a swap
          converges, so ``has_served`` alone cannot tell "no swap issued" from
          "swap issued, fate unknown". ``applied`` is written as soon as an apply
          returns. One-write window: ``backend.apply()`` returns before that
          write lands, so a worker dying inside it leaves a shadow route no
          record can see -- accepted, because ``_refuse_if_in_use`` then answers
          409 and no stamp is written.
        * ``pending_gate`` past ``pull``, where the load happens; before that,
          what is on disk is a partial download. Reading a gate NAME is sound for
          ``pull`` alone -- stage flags can move the first serving gate, not it.

        NOT called from the swap/backend failure paths: there an apply was issued
        and did not land, so routing is unknown and the candidate stays resident.
        """
        if has_served(record) or record.applied is not None:
            return
        gate = record.pending_gate
        # ``None`` is "which gate we stopped at is unknown", not "past pull".
        if gate is None or gate == GATE_ORDER[0]:
            return
        self._evict_and_stamp(
            record,
            record.candidate_version,
            why=f"candidate retired unrouted at the {gate} gate",
        )

    # ------------------------------------------------------------------
    # Failure handling (design §10)
    # ------------------------------------------------------------------
    def _on_swap_failure(
        self, record: PromotionRecord, gate: str, *, reason: str | None = None
    ) -> None:
        """A swap failed / readiness timed out: hold, alert, optional auto-rollback.

        ``reason`` is logged, not persisted: nothing reads a failure string off
        the record.
        """
        promotion_id = record.promotion_id
        self._update(promotion_id, state="failed")
        record.state = "failed"
        logger.error(
            "Swap failed at gate %s for %s%s",
            gate,
            promotion_id,
            "" if reason is None else f": {reason}",
        )
        self._finalize_flywheel_run(record, "failed")
        if has_served(record) and self.policy.auto_rollback_on_regression:
            # ENQUEUE, not inline: the record is already terminal, so an inline
            # rollback raising a retryable error would have the retried
            # advance() exit at the terminal check and never re-drive it. The
            # rollback task carries its own autoretry.
            logger.info("Auto-rollback after swap failure at %s for %s", gate, promotion_id)
            from src.tasks.promotion.tasks import rollback_promotion

            rollback_promotion.delay(promotion_id)

    def _handle_backend_error(self, record: PromotionRecord, gate: str, err: BackendError) -> None:
        promotion_id = record.promotion_id
        retryable = bool(getattr(err, "retryable", False))
        code = getattr(err, "code", "internal")
        if retryable:
            # Leave state intact and RE-RAISE so the task's autoretry retries
            # with backoff (idempotent by idempotency_key). A record that
            # exhausts retries is re-driven by the online beat's sweep.
            logger.warning(
                "Retryable backend error (%s) at gate %s for %s; re-raising for retry",
                code,
                gate,
                promotion_id,
            )
            raise err
        logger.error("Backend error (%s) at gate %s for %s -> failed", code, gate, promotion_id)
        self._update(promotion_id, state="failed")
        record.state = "failed"
        self._finalize_flywheel_run(record, "failed")
        if has_served(record) and self.policy.auto_rollback_on_regression:
            # ENQUEUE, not inline -- same reasoning as _on_swap_failure.
            from src.tasks.promotion.tasks import rollback_promotion

            rollback_promotion.delay(promotion_id)

    # ------------------------------------------------------------------
    # Approvals + rollback
    # ------------------------------------------------------------------
    def on_approval(self, promotion_id: str, gate: str, decision: str) -> None:
        """Handle a human approve/reject decision for a gate (design §4)."""
        record = self.service.get(promotion_id)
        if record is None:
            logger.warning("on_approval: unknown promotion %s", promotion_id)
            return
        decision = decision.lower()
        try:
            self.mlflow.set_promotion_tags(
                record.model_name,
                self._resolve_mlflow_version(record),
                {
                    f"gate.{gate}.decision": decision,
                    f"gate.{gate}.at": datetime.utcnow().isoformat(),
                },
            )
        except Exception:
            logger.exception("MLflow gate-decision tag failed for %s", promotion_id)

        if decision == "approved":
            self._update(promotion_id, state="approved")
            self._enqueue_advance(promotion_id)
            logger.info("on_approval: %s approved at %s -> advance", promotion_id, gate)
            return

        # Rejected: archive if nothing ever served, else roll the traffic back,
        # enqueued so the caller (API request or beat) never blocks.
        if not has_served(record):
            self._update(promotion_id, state="rejected", pending_gate=None)
            record.state = "rejected"
            self._finalize_flywheel_run(record, "rejected")
            # Reads the record's ``pending_gate``, not the caller's ``gate``.
            self._evict_never_routed_candidate(record)
            logger.info("on_approval: %s rejected pre-serving at %s", promotion_id, gate)
        else:
            from src.tasks.promotion.tasks import rollback_promotion

            logger.info(
                "on_approval: %s rejected while serving at %s -> rollback", promotion_id, gate
            )
            rollback_promotion.delay(promotion_id)

    def _baseline_unloaded(self, record: PromotionRecord) -> bool:
        """Does the serving stack POSITIVELY say it cannot serve the baseline?

        The same predicate convergence applies -- the Model Manager marks a swap
        converged only once the target is in ``ready_versions`` -- asked once up
        front rather than once per poll for the whole budget.

        False on every unknown, so a rollback is refused on evidence and never on
        silence; a raising read included, since a precheck that throws would abort
        the rollback it exists to protect.
        """
        try:
            state = self.backend.serving_state(record.model_name)
        except Exception:
            logger.warning(
                "rollback: serving state unreadable for %s; proceeding without the precheck",
                record.model_name,
                exc_info=True,
            )
            return False
        if state is None or not state.known_available or state.ready_versions is None:
            return False
        return str(record.baseline_version) not in {str(v) for v in state.ready_versions}

    def rollback(self, promotion_id: str) -> None:
        """Roll back to the baseline version: a re-route, never a re-load (design §3)."""
        record = self.service.get(promotion_id)
        if record is None:
            logger.warning("rollback: unknown promotion %s", promotion_id)
            return
        if record.state == "rolled_back":
            return
        self._apply_record_policy(record)
        if not record.baseline_version:
            logger.error("rollback: no baseline_version for %s -> failed", promotion_id)
            self._update(promotion_id, state="failed", pending_gate=None)
            record.state = "failed"
            self._finalize_flywheel_run(record, "failed")
            return
        if getattr(record, "rollback_target_reclaimed", False):
            # The sweep deleted this baseline's weights. Without the precheck
            # this resurfaces below as an opaque convergence timeout.
            logger.error(
                "rollback: baseline %s@%s for %s was reclaimed by the disk-reclaim sweep; "
                "its weights are no longer on the shared volume, so this rollback cannot be "
                "a routing change -> failed",
                record.model_name,
                record.baseline_version,
                promotion_id,
            )
            self._update(promotion_id, state="failed", pending_gate=None)
            record.state = "failed"
            self._finalize_flywheel_run(record, "failed")
            return
        if self._baseline_unloaded(record):
            # The disk half's twin. The full switch evicted this baseline from
            # memory and nothing reloads it, so without the precheck this
            # resurfaces below as an opaque convergence timeout.
            logger.error(
                "rollback: the serving stack does not list baseline %s@%s for %s as servable, "
                "and a rollback only re-routes, so this would never converge -> failed. "
                "Expected after a full switch: the eviction closed the window in MEMORY and "
                "nothing reloads it. The weights are still on disk",
                record.model_name,
                record.baseline_version,
                promotion_id,
            )
            self._update(promotion_id, state="failed", pending_gate=None)
            record.state = "failed"
            self._finalize_flywheel_run(record, "failed")
            return
        try:
            result = self.backend.rollback(
                record.model_name,
                record.baseline_version,
                idempotency_key=f"{promotion_id}:rollback",
            )
            expectation = expectation_for(record.applied, result)
            # Persist before the wait, as in the forward gates.
            self._update(promotion_id, applied=expectation, swap_id=result.swap_id)
            record.applied = expectation
            ok, why = self._wait_for_convergence(
                expectation,
                what=f"{promotion_id} rollback {record.model_name}@{record.baseline_version}",
            )
            if not ok:
                # The regressed candidate may still be serving, so recording
                # 'rolled_back' here would be a lie.
                logger.error(
                    "Rollback did not converge for %s (expectation key %s): %s",
                    promotion_id,
                    expectation.get("key"),
                    why,
                )
                self._update(promotion_id, state="failed")
                record.state = "failed"
                self._finalize_flywheel_run(record, "failed")
                return
            self._update(
                promotion_id,
                state="rolled_back",
                serving_role="default",
                pending_gate=None,
            )
            record.state = "rolled_back"
            self._move_alias(record, "production", "rollback", version=record.baseline_version)
            self._finalize_flywheel_run(record, "rolled_back")
            # The placement is the guard: below the non-convergence branch,
            # because unloading before the routing withdrawal lands is the black
            # hole.
            self._evict_and_stamp(
                record,
                record.candidate_version,
                why="regressed candidate, rollback converged",
            )
            logger.info("rolled back %s to baseline %s", promotion_id, record.baseline_version)
        except BackendError as e:
            # Marking the record failed here would swallow rollback_promotion's
            # autoretry and abandon the safety net after one transient 503.
            if getattr(e, "retryable", False):
                logger.warning(
                    "Retryable backend error during rollback for %s; re-raising for retry",
                    promotion_id,
                )
                raise
            logger.exception("rollback failed for %s -> failed", promotion_id)
            self._update(promotion_id, state="failed")
            record.state = "failed"
            self._finalize_flywheel_run(record, "failed")
