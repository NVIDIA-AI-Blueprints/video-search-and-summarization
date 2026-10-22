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

"""Periodic online-eval poll for shadow/canary promotions (Celery beat task).

For every promotion currently serving in shadow or canary, obtain the
backend-owned evaluator through the provider seam
(``get_backend_provider().evaluator(use_case=<record.use_case>)``) and run the
generic ``OnlineEval`` wrapper. The resulting verdict + metrics are persisted on
the record so the controller's next ``advance`` can read them. There is **no**
harness-side evaluator factory: the active backend supplies the evaluator.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.api.promotion.models import has_served
from src.celery_app import celery_app
from src.log_utils import setup_logging

if TYPE_CHECKING:
    from src.config.deft import EvalHarnessConfig

logger = setup_logging("data_flywheel.promotion.online_eval_beat")

#: Rollout roles that produce an online verdict (default/full traffic is terminal).
_OBSERVED_ROLES: tuple[str, ...] = ("shadow", "canary")

#: Recorded as ``decided_by`` when the observation window, not a human, decides.
_TIMEOUT_ACTOR = "policy:on_timeout"


def _eval_policy(doc: dict) -> "EvalHarnessConfig":
    """The record's eval policy, falling back to defaults when absent/invalid."""
    from src.config.deft import EvalHarnessConfig

    raw = (doc.get("promotion_policy") or {}).get("eval_harness") or {}
    try:
        return EvalHarnessConfig(**raw)
    except Exception as exc:
        # warning, not exception: the beat re-reads this record every 60s, so a
        # persistently malformed policy would emit a stack trace a minute,
        # indefinitely. The validation message carries what is wrong.
        logger.warning(
            "Invalid eval_harness policy on %s (%s); using defaults",
            doc.get("promotion_id"),
            exc,
        )
        return EvalHarnessConfig()


def _window_elapsed(doc: dict, window_s: float | None) -> bool:
    """True when this serving phase has outlived ``window_s``.

    ``serving_since`` is stamped on every serving-role transition, so each
    shadow/canary phase gets its own clock. A record written before this field
    existed has none, and must not be treated as instantly expired.

    Only ``None`` disables the window: ``0`` is a legitimate deadline meaning
    "escalate on the first hold", so it must be able to fire.

    Never raises. This runs outside the per-promotion try/except, so an
    unreadable timestamp here would abort the whole sweep and silently skip
    every promotion behind this one.
    """
    if window_s is None:
        return False
    since = doc.get("serving_since")
    if isinstance(since, str):
        try:
            since = datetime.fromisoformat(since)
        except ValueError:
            return False
    if not isinstance(since, datetime):
        return False
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return (datetime.now(UTC) - since).total_seconds() >= float(window_s)


def _apply_timeout(db, doc: dict, action: str, role: str) -> None:
    """Give a timed-out hold somewhere to go.

    ``escalate`` moves the record to ``pending_approval``, which is what makes a
    human decision possible at all: ``advance`` returns on a non-decisive verdict
    BEFORE the HITL pause can fire, and the approve API is atomically guarded on
    ``state == "pending_approval"``, so a held record was previously unreachable
    by both. Approving then means "advance this gate despite an undecided
    verdict" — the controller consumes the approval and skips the verdict check.
    """
    from src.api.promotion.service import PROMOTIONS_COLLECTION

    promotion_id = doc.get("promotion_id")
    logger.warning(
        "Online eval window elapsed for promotion=%s role=%s; applying on_timeout=%s",
        promotion_id,
        role,
        action,
    )
    if action == "escalate":
        db[PROMOTIONS_COLLECTION].update_one(
            {"promotion_id": promotion_id, "state": "promoting"},
            {"$set": {"state": "pending_approval", "timed_out_at": datetime.now(UTC)}},
        )
        return
    if action == "rollback":
        from src.tasks.promotion.tasks import rollback_promotion

        rollback_promotion.delay(promotion_id)
        return
    if action == "advance":
        from src.tasks.promotion.tasks import advance_promotion

        # Enqueueing alone is a no-op: the record still carries verdict="hold",
        # and advance() rejects a non-decisive verdict at the canary/full gates.
        # Nothing would latch either, so every tick would re-enqueue forever.
        #
        # Reuse the escape hatch advance() already honours — state="approved"
        # skips the verdict check — rather than fabricating an "advance" verdict
        # the evaluator never produced. The verdict stays honestly "hold";
        # decided_by/timed_out_at record that policy, not a human, decided.
        # The state filter mirrors the escalate branch so concurrent ticks race
        # safely: only one can move the record out of "promoting".
        now = datetime.now(UTC)
        moved = db[PROMOTIONS_COLLECTION].update_one(
            {"promotion_id": promotion_id, "state": "promoting"},
            {
                "$set": {
                    "state": "approved",
                    "decided_by": _TIMEOUT_ACTOR,
                    "decided_at": now,
                    "timed_out_at": now,
                }
            },
        )
        if getattr(moved, "matched_count", 1):
            advance_promotion.delay(promotion_id)
        return
    logger.error("Unknown on_timeout=%r for promotion=%s; holding", action, promotion_id)


@celery_app.task(name="tasks.promotion.online_eval_poll")
def online_eval_poll() -> dict:
    """For shadow/canary promotions, run OnlineEval and persist the verdict.

    The controller stamps ``serving_role`` on each record after every ``apply``
    (``shadow`` after the deploy gate, ``canary`` during canary steps); this beat
    evaluates exactly those roles and writes back the verdict the controller's
    next ``advance`` consumes.
    """
    from src.api.promotion.service import PROMOTIONS_COLLECTION
    from src.backends.factory import get_backend_provider
    from src.lib.eval_harness import OnlineEval
    from src.tasks.promotion.controller import _get_db

    db = _get_db()
    # Promotions actively serving in shadow/canary and awaiting an eval verdict.
    cursor = db[PROMOTIONS_COLLECTION].find(
        {"state": "promoting", "serving_role": {"$in": list(_OBSERVED_ROLES)}}
    )

    checked = 0
    enqueued: set[str] = set()
    for doc in cursor:
        promotion_id = doc.get("promotion_id")
        role = doc.get("serving_role") or "shadow"
        # No default: sibling use cases share one promotions collection, so
        # falling back to "aoi" here would grade an ITS detection window with the
        # AOI FAR evaluator and write that verdict back as if it were measured.
        # Skip loudly instead — the record keeps its previous verdict and stays
        # `promoting`, so a human sees a stalled promotion plus this ERROR rather
        # than a confidently wrong rollout decision. Skipping before the
        # bookkeeping is correct: `checked` counts records this pass actually
        # evaluated, and `enqueued` exists only to stop the reconciliation sweep
        # double-advancing something we already advanced — leaving this record
        # out of both is exactly the "did nothing to it" state we want. The
        # sweep won't rescue it either (it has served and carries no decisive
        # verdict), which is deliberate: the fix is to write the use case, not
        # to keep re-driving a record nobody can grade.
        use_case = doc.get("use_case")
        if not use_case:
            logger.error(
                "Promotion %s has no use_case; skipping online eval (writer bug)",
                promotion_id,
            )
            continue

        policy = _eval_policy(doc)

        # Gap 1: the gate is off for this use case. Skip HIGH — never collect,
        # never evaluate, and never write a verdict that was not measured — but
        # still drive the state machine, or the promotion stalls in shadow.
        if not policy.online_enabled:
            logger.info(
                "Online eval disabled for promotion=%s; advancing on the offline gate",
                promotion_id,
            )
            from src.tasks.promotion.tasks import advance_promotion

            advance_promotion.delay(promotion_id)
            enqueued.add(promotion_id)
            continue

        try:
            evaluator = get_backend_provider().evaluator(use_case=use_case)
            if evaluator is None:
                logger.warning(
                    "No evaluator for use_case=%r (promotion %s); skipping",
                    use_case,
                    promotion_id,
                )
                continue
            # Apply THIS promotion's eval policy from the VALIDATED object, not
            # the raw dict. When _eval_policy() rejects a malformed block and
            # falls back to defaults, reading the dict here would configure the
            # evaluator from the same values validation just refused — one
            # record running two policies.
            #
            # Set even when empty, so a record that clears its policy cannot
            # inherit a previous evaluator's criteria.
            if hasattr(evaluator, "online_target"):
                evaluator.online_target = list(policy.online_target)
            if policy.thresholds and hasattr(evaluator, "thresholds"):
                evaluator.thresholds = {**evaluator.thresholds, **policy.thresholds}
            # Truthiness on purpose: min_sample_size=0 (like None/absent) falls
            # back to the evaluator's default — a zero floor would let a single
            # sample decide a rollout, which no policy legitimately wants.
            if policy.min_sample_size and hasattr(evaluator, "min_sample_size"):
                evaluator.min_sample_size = int(policy.min_sample_size)
            result = OnlineEval(evaluator).evaluate(
                baseline=doc.get("baseline_version"),
                candidate=doc.get("candidate_version"),
                # The collection window: which model+version's secondary-inference
                # results to read (Storage Backend GET /v1/results params). The
                # results MUST be filtered to THIS candidate — mixed-version
                # samples would corrupt the verdict.
                window={
                    "model": doc.get("model_name"),
                    "version": doc.get("candidate_version"),
                },
                role=role,
            )
        except Exception:
            logger.exception("Online eval failed for promotion %s", promotion_id)
            continue

        db[PROMOTIONS_COLLECTION].update_one(
            {"promotion_id": promotion_id},
            {"$set": {"verdict": result["verdict"], "eval_metrics": result["metrics"]}},
        )
        checked += 1
        logger.info(
            "Online eval promotion=%s role=%s verdict=%s", promotion_id, role, result["verdict"]
        )

        # A decisive verdict IS the advance trigger ("advancement is eval-gated,
        # not time-gated"): the controller's next advance consumes it — advance
        # runs the next gate, rollback triggers the rollback path. Without this
        # enqueue nothing re-drives the state machine after deploy/canary and a
        # promotion would stall in shadow/canary until a human approval.
        #
        # Enqueue only when the verdict CHANGED from what the record carried
        # before this poll: the controller resets ``verdict`` to None after
        # every gate transition, so each fresh decisive verdict enqueues
        # exactly once and repeated polls (while the advance task is still
        # queued/running) don't pile up duplicate executions. (Duplicates are
        # additionally harmless — advance holds without a decisive verdict and
        # backend applies are idempotency-keyed — this just avoids the churn.)
        if result["verdict"] in ("advance", "rollback") and result["verdict"] != doc.get("verdict"):
            from src.tasks.promotion.tasks import advance_promotion

            advance_promotion.delay(promotion_id)
            enqueued.add(promotion_id)
            continue

        # Gap 2/3: a hold that has outlived its observation window. Without this
        # the promotion holds forever — never advancing, rolling back, erroring
        # or alerting — which is the failure mode for low-volume streams.
        if result["verdict"] == "hold" and _window_elapsed(doc, policy.max_observation_window_s):
            _apply_timeout(db, doc, policy.on_timeout, role)
            enqueued.add(promotion_id)

    # ------------------------------------------------------------------
    # Reconciliation sweep: re-drive any promotion the normal flow lost.
    # A `promoting` record that has NOT served yet is stalled at beat time —
    # pre-serving gates normally clear in seconds and have no other re-drive
    # path (serving_role is unset, so the verdict loop above cannot see them).
    # The test is `has_served`, not a gate-name set: with
    # deployment_stages.shadow off the first serving gate is `canary`, and with
    # both stages off it is `full`, so a record parked there with no
    # serving_role and no verdict is invisible to BOTH loops and would stall
    # forever. Likewise a serving record already carrying a decisive verdict
    # whose advance was lost (task crash, exhausted retries, purge).
    # Duplicate advances are safe: gates re-check state/verdict and backend
    # applies are idempotency-keyed.
    #
    # Worst-case fan-out (deliberate, documented): there is no dedupe against
    # tasks already sitting in the queue, so if workers back up for K beat
    # ticks a stalled promotion accumulates K queued advances. Each drains as
    # a cheap state-checked no-op once the first one lands; with the 60s beat
    # and one-promotion-at-a-time AOI runs the pile-up is bounded and safe.
    # ------------------------------------------------------------------
    swept = 0
    for doc in db[PROMOTIONS_COLLECTION].find({"state": "promoting"}):
        promotion_id = doc.get("promotion_id")
        if promotion_id in enqueued:
            continue
        gate = doc.get("pending_gate")
        served = has_served(doc)
        stalled_not_serving = not served
        lost_decisive = served and doc.get("verdict") in ("advance", "rollback")
        if stalled_not_serving or lost_decisive:
            from src.tasks.promotion.tasks import advance_promotion

            advance_promotion.delay(promotion_id)
            swept += 1
            logger.info(
                "Reconciliation: re-enqueued advance for %s (gate=%s, verdict=%s)",
                promotion_id,
                gate,
                doc.get("verdict"),
            )

    return {"task": "online_eval_poll", "checked": checked, "swept": swept}
