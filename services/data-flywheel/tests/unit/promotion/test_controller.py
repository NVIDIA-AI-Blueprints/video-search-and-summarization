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

"""State-machine unit tests for the Promotion Controller (design §3/§6/§10)."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.api.promotion.models import PromotionRecord
from src.api.promotion.service import PROMOTIONS_COLLECTION, PromotionService
from src.config import (
    CanaryStepsConfig,
    DeploymentStagesConfig,
    EvalHarnessConfig,
    HITLConfig,
    PromotionPolicyConfig,
)
from src.lib.promotion.deployment_backend.base import (
    ApplyResult,
    BackendError,
    ConvergenceReason,
    ConvergenceReport,
    DeploymentBackend,
    PromotionControl,
    PullResult,
    ServingState,
)
from src.tasks.promotion import controller as controller_mod
from src.tasks.promotion.controller import PromotionController, expectation_for

# The token apply hands back; the controller echoes it verbatim at convergence.
_APPLIED = {
    "model_name": "m",
    "to_version": "v2",
    "control": "shadow",
    "from_version": "v1",
    "canary_weight": None,
    "key": "k-1",
}


def _not_converged(code: str = "shadow_absent", *, terminal: bool = False) -> ConvergenceReport:
    """A gate that has not landed. Non-terminal by default, so the loop keeps
    spending its budget instead of condemning the swap on the first "not yet".
    """
    return ConvergenceReport(
        converged=False,
        reason=ConvergenceReason(code=code, message=f"{code} at poll", terminal=terminal),
    )


def _serving_state(*ready: str, known_available: bool = True) -> ServingState:
    """What the stack says it can serve. ``_serving_state()`` is an EMPTY ready set
    -- the post-full-switch view of the baseline -- not the unknown that
    ``ready_versions=None`` means.
    """
    return ServingState(model_name="m", ready_versions=list(ready), known_available=known_available)


def _make_backend() -> MagicMock:
    # spec= is required, or the mock answers truthy and the tests test nothing.
    backend = MagicMock(spec=DeploymentBackend)
    # Every version this file names, so the rollback precheck passes unless a
    # test says otherwise. Without it a bare mock answers "not servable".
    backend.serving_state.return_value = _serving_state("v1", "v2", "1", "2")
    backend.pull.return_value = PullResult("m", "v2", "/models/m/v2/", "staged")
    backend.apply.return_value = ApplyResult(
        applied=dict(_APPLIED),
        to_version="v2",
        control=PromotionControl.SHADOW,
        started_at=datetime.now(UTC).isoformat(),
        from_version="v1",
        swap_id="swap-1",
    )
    backend.rollback.return_value = ApplyResult(
        applied={**_APPLIED, "to_version": "v1", "control": "full_switch", "key": "k-rb"},
        to_version="v1",
        control=PromotionControl.FULL_SWITCH,
        started_at=datetime.now(UTC).isoformat(),
        from_version="v2",
        swap_id="swap-rb",
    )
    backend.convergence.return_value = ConvergenceReport(converged=True)
    backend.unload.return_value = {"version": "v1", "status": "removed"}
    backend.cleanup.return_value = {"version": "v1", "status": "removed", "disk_reclaimed": True}
    return backend


def _policy(
    hitl: HITLConfig,
    steps: list[int],
    stages: DeploymentStagesConfig | None = None,
) -> PromotionPolicyConfig:
    return PromotionPolicyConfig(
        hitl=hitl,
        deployment_stages=stages or DeploymentStagesConfig(),
        canary_steps=CanaryStepsConfig(steps=steps),
        eval_harness=EvalHarnessConfig(),
        poll_interval_s=0.0,
        poll_max_attempts=1,
        auto_rollback_on_regression=True,
    )


@pytest.fixture
def controller_factory(fake_db):
    """Build a controller wired to the fake DB + a mock backend/mlflow."""
    created = {}

    def _make(
        hitl: HITLConfig | None = None,
        steps: list[int] | None = None,
        stages: DeploymentStagesConfig | None = None,
    ):
        backend = _make_backend()
        mlflow = MagicMock()
        mlflow.get_version_by_alias.return_value = None
        with (
            patch.object(controller_mod, "_get_db", return_value=fake_db),
            patch.object(PromotionController, "_build_backend", return_value=backend),
            patch.object(PromotionController, "_build_mlflow", return_value=mlflow),
        ):
            ctrl = PromotionController()
        # An explicit [] is preserved so a test can exercise CanaryStepsConfig
        # validation rather than silently get the default.
        ctrl.policy = _policy(
            hitl if hitl is not None else HITLConfig(),
            steps if steps is not None else [10, 100],
            stages,
        )
        created["backend"] = backend
        created["mlflow"] = mlflow
        return ctrl

    _make.state = created
    return _make


@pytest.fixture(autouse=True)
def _patch_enqueue():
    """Prevent Celery broker access; capture enqueued advance/rollback ids."""
    adv = MagicMock()
    rb = MagicMock()
    with (
        patch("src.tasks.promotion.tasks.advance_promotion", adv),
        patch("src.tasks.promotion.tasks.rollback_promotion", rb),
    ):
        yield {"advance": adv, "rollback": rb}


def _new_record(ctrl: PromotionController, **overrides) -> PromotionRecord:
    fields = {
        "model_name": "m",
        "candidate_version": "v2",
        "baseline_version": "v1",
        "use_case": "aoi",
        "state": "promoting",
        "pending_gate": "pull",
        "artifact_uri": "mlflow://m/v2",
        "model_format": "onnx",
        "sha256": "abc",
    }
    fields.update(overrides)
    record = PromotionRecord(**fields)
    PromotionService(ctrl.db).create(record)
    return record


def _get(ctrl: PromotionController, promotion_id: str) -> dict:
    return ctrl.db[PROMOTIONS_COLLECTION].find_one({"promotion_id": promotion_id})


def _set(ctrl: PromotionController, promotion_id: str, **fields) -> None:
    ctrl.db[PROMOTIONS_COLLECTION].update_one({"promotion_id": promotion_id}, {"$set": fields})


class TestRecordPolicyOverride:
    def test_record_policy_overrides_the_default(self, controller_factory, _patch_enqueue):
        # The default policy has HITL off everywhere; this promotion's named
        # config requires approval at full, and the record's must win. (A
        # deployment test found named-config policies were silently ignored.)
        ctrl = controller_factory(
            hitl=HITLConfig(pull=False, deploy=False, canary=False, full=False)
        )
        record = _new_record(
            ctrl,
            pending_gate="full",
            verdict="advance",
            promotion_policy={
                "hitl": {"pull": False, "deploy": False, "canary": False, "full": True},
                "canary_steps": {"steps": [5]},
                "poll_interval_s": 0.0,
                "poll_max_attempts": 1,
                "auto_rollback_on_regression": True,
            },
        )
        ctrl.advance(record.promotion_id)
        assert _get(ctrl, record.promotion_id)["state"] == "pending_approval"

    def test_absent_record_policy_uses_default(self, controller_factory, _patch_enqueue):
        ctrl = controller_factory(
            hitl=HITLConfig(pull=False, deploy=False, canary=False, full=True)
        )
        record = _new_record(ctrl, pending_gate="full", verdict="advance")
        ctrl.advance(record.promotion_id)
        assert _get(ctrl, record.promotion_id)["state"] == "pending_approval"


class TestApprovalOverridesEvalGate:
    def test_approved_transition_bypasses_the_verdict_requirement(self, controller_factory):
        # The only escape when no results source exists: approval unlocks the
        # escalation gate with no online verdict.
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl, state="approved", pending_gate="full", serving_role="canary", verdict=None
        )
        ctrl.advance(rec.promotion_id)
        backend.apply.assert_called_once()  # full switch ran despite verdict=None
        assert _get(ctrl, rec.promotion_id)["state"] == "promoted"

    def test_approved_transition_does_not_re_pause_on_hitl(self, controller_factory):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=True), steps=[100])
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl, state="approved", pending_gate="full", serving_role="canary", verdict=None
        )
        ctrl.advance(rec.promotion_id)
        backend.apply.assert_called_once()
        assert _get(ctrl, rec.promotion_id)["state"] == "promoted"


class TestCancelledRunStopsPromotion:
    def _cancelled_db(self):
        db = MagicMock()
        db.is_flywheel_run_cancelled.return_value = True
        return db

    def test_pre_serving_promotion_is_rejected(self, controller_factory):
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, flywheel_run_id="507f1f77bcf86cd799439011")
        with patch("src.api.db_manager.get_db_manager", return_value=self._cancelled_db()):
            ctrl.advance(rec.promotion_id)
        assert _get(ctrl, rec.promotion_id)["state"] == "rejected"
        backend.pull.assert_not_called()

    def test_serving_promotion_rolls_back(self, controller_factory):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            flywheel_run_id="507f1f77bcf86cd799439011",
            pending_gate="canary",
            serving_role="canary",
            verdict="advance",
        )
        with patch("src.api.db_manager.get_db_manager", return_value=self._cancelled_db()):
            ctrl.advance(rec.promotion_id)
        backend.rollback.assert_called_once()
        assert _get(ctrl, rec.promotion_id)["state"] == "rolled_back"

    def test_unreadable_cancel_flag_fails_open(self, controller_factory):
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, flywheel_run_id="507f1f77bcf86cd799439011")
        db = MagicMock()
        db.is_flywheel_run_cancelled.side_effect = ConnectionError("mongo down")
        with patch("src.api.db_manager.get_db_manager", return_value=db):
            ctrl.advance(rec.promotion_id)
        backend.pull.assert_called_once()  # rollout proceeds


class TestNextGate:
    def test_default_ladder_order(self, controller_factory):
        ctrl = controller_factory()
        assert ctrl._next_gate(None) == "pull"
        assert ctrl._next_gate("pull") == "deploy"
        assert ctrl._next_gate("deploy") == "canary"
        assert ctrl._next_gate("canary") == "full"
        assert ctrl._next_gate("full") is None
        assert ctrl._next_gate("bogus") is None

    def test_walks_over_a_disabled_gate(self, controller_factory):
        # Stepped OVER, not treated as unknown: a record parked on "canary" after
        # the stage was turned off must still reach "full".
        ctrl = controller_factory(stages=DeploymentStagesConfig(shadow=True, canary=False))
        assert ctrl._next_gate("deploy") == "full"
        assert ctrl._next_gate("canary") == "full"


class TestDeploymentStages:
    """The gate ladder is derived from promotion_policy.deployment_stages.

    A serving backend without canary routing (RTVI-CV implements single +
    shadow only) must be able to promote shadow -> full instead of failing at a
    canary gate it can never satisfy.
    """

    @pytest.mark.parametrize(
        ("shadow", "canary", "expected"),
        [
            (True, True, ["pull", "deploy", "canary", "full"]),
            (True, False, ["pull", "deploy", "full"]),
            (False, True, ["pull", "canary", "full"]),
            (False, False, ["pull", "full"]),
        ],
    )
    def test_gate_order_follows_the_stage_flags(self, controller_factory, shadow, canary, expected):
        ctrl = controller_factory(stages=DeploymentStagesConfig(shadow=shadow, canary=canary))
        assert ctrl.gate_order() == expected

    def test_canary_disabled_promotes_shadow_then_full(self, controller_factory, _patch_enqueue):
        ctrl = controller_factory(
            hitl=HITLConfig(canary=False, full=False),
            stages=DeploymentStagesConfig(shadow=True, canary=False),
        )
        backend = controller_factory.state["backend"]
        pid = _new_record(ctrl).promotion_id

        ctrl.advance(pid)
        assert _get(ctrl, pid)["pending_gate"] == "deploy"

        # deploy -> shadow, and the ladder skips canary entirely
        ctrl.advance(pid)
        doc = _get(ctrl, pid)
        assert doc["serving_role"] == "shadow"
        assert doc["pending_gate"] == "full"
        assert backend.apply.call_args.args[2] == PromotionControl.SHADOW

        _set(ctrl, pid, verdict="advance")
        ctrl.advance(pid)
        doc = _get(ctrl, pid)
        assert doc["state"] == "promoted"
        assert doc["pending_gate"] is None
        assert backend.apply.call_args.args[2] == PromotionControl.FULL_SWITCH

        controls = [c.args[2] for c in backend.apply.call_args_list]
        assert PromotionControl.CANARY not in controls
        assert controls == [PromotionControl.SHADOW, PromotionControl.FULL_SWITCH]

    def test_shadow_disabled_runs_canary_without_a_prior_verdict(
        self, controller_factory, _patch_enqueue
    ):
        # Canary is the FIRST serving stage here, so no verdict exists yet and
        # demanding one would deadlock the rollout.
        ctrl = controller_factory(
            hitl=HITLConfig(canary=False, full=False),
            steps=[25],
            stages=DeploymentStagesConfig(shadow=False, canary=True),
        )
        backend = controller_factory.state["backend"]
        pid = _new_record(ctrl).promotion_id

        ctrl.advance(pid)
        assert _get(ctrl, pid)["pending_gate"] == "canary"

        ctrl.advance(pid)
        doc = _get(ctrl, pid)
        assert doc["serving_role"] == "canary"
        assert doc["pending_gate"] == "full"
        assert backend.apply.call_args.args[2] == PromotionControl.CANARY
        assert backend.apply.call_args.kwargs["canary_weight"] == pytest.approx(0.25)

    def test_both_stages_disabled_switches_straight_to_full(
        self, controller_factory, _patch_enqueue
    ):
        ctrl = controller_factory(
            hitl=HITLConfig(full=False),
            stages=DeploymentStagesConfig(shadow=False, canary=False),
        )
        backend = controller_factory.state["backend"]
        pid = _new_record(ctrl).promotion_id

        ctrl.advance(pid)
        assert _get(ctrl, pid)["pending_gate"] == "full"

        ctrl.advance(pid)
        doc = _get(ctrl, pid)
        assert doc["state"] == "promoted"
        controls = [c.args[2] for c in backend.apply.call_args_list]
        assert controls == [PromotionControl.FULL_SWITCH]

    def test_in_flight_record_on_a_now_disabled_gate_skips_forward(
        self, controller_factory, _patch_enqueue
    ):
        # A policy edit can park a live promotion on a gate the ladder no longer
        # contains. It must step forward, not stall.
        ctrl = controller_factory(
            hitl=HITLConfig(canary=False, full=False),
            stages=DeploymentStagesConfig(shadow=True, canary=False),
        )
        backend = controller_factory.state["backend"]
        pid = _new_record(
            ctrl, pending_gate="canary", serving_role="shadow", verdict="advance"
        ).promotion_id

        ctrl.advance(pid)
        assert _get(ctrl, pid)["pending_gate"] == "full"
        backend.apply.assert_not_called()  # nothing applied while skipping
        _patch_enqueue["advance"].delay.assert_called_with(pid)

        ctrl.advance(pid)
        assert _get(ctrl, pid)["state"] == "promoted"

    def test_exhausted_canary_ladder_escalates_to_full(self, controller_factory, _patch_enqueue):
        # A shortened steps list leaves the index past the ladder; it must
        # escalate rather than index off the end.
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[10])
        backend = controller_factory.state["backend"]
        pid = _new_record(
            ctrl,
            pending_gate="canary",
            serving_role="canary",
            canary_step_index=5,
            verdict="advance",
        ).promotion_id

        ctrl.advance(pid)
        doc = _get(ctrl, pid)
        assert doc["pending_gate"] == "full"
        assert doc["verdict"] is None
        backend.apply.assert_not_called()

    def test_record_policy_carries_the_stage_flags(self, controller_factory, _patch_enqueue):
        # The record's policy wins over the deployment default, so one deployment
        # serves both AOI and ITS.
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        assert ctrl.gate_order() == ["pull", "deploy", "canary", "full"]
        pid = _new_record(
            ctrl,
            promotion_policy={
                "hitl": {"pull": False, "deploy": False, "canary": False, "full": False},
                "deployment_stages": {"shadow": True, "canary": False},
                "canary_steps": {"steps": [5]},
                "poll_interval_s": 0.0,
                "poll_max_attempts": 1,
            },
        ).promotion_id

        ctrl.advance(pid)  # pull
        ctrl.advance(pid)  # deploy
        assert _get(ctrl, pid)["pending_gate"] == "full"

    def test_absent_stage_config_keeps_the_full_ladder(self, controller_factory, _patch_enqueue):
        # Pre-deployment_stages records round-trip to the previous behaviour.
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        rec = _new_record(
            ctrl,
            promotion_policy={"hitl": {"canary": False, "full": False}, "poll_max_attempts": 1},
        )
        ctrl._apply_record_policy(rec)
        assert ctrl.gate_order() == ["pull", "deploy", "canary", "full"]


class TestHasServedNotGateNames:
    """ "Is anything live?" must key on serving_role, not on gate names.

    ``gate in ("pull","deploy")`` was a valid proxy for "nothing serving" only
    while the shadow deploy gate was mandatory. With it optional, the first
    SERVING gate is ``canary`` or even ``full``, so every site that branches on
    "has it served?" has to ask the record.
    """

    def test_reject_at_a_first_serving_gate_archives_instead_of_rolling_back(
        self, controller_factory, _patch_enqueue
    ):
        # Shadow off + the shipped hitl.canary=True: rejecting at canary rejects
        # a candidate that never took traffic.
        ctrl = controller_factory(stages=DeploymentStagesConfig(shadow=False, canary=True))
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, state="pending_approval", pending_gate="canary", serving_role=None)
        ctrl.on_approval(rec.promotion_id, "canary", "rejected")

        assert _get(ctrl, rec.promotion_id)["state"] == "rejected"
        _patch_enqueue["rollback"].delay.assert_not_called()
        backend.rollback.assert_not_called()

    def test_swap_failure_before_anything_served_does_not_roll_back(
        self, controller_factory, _patch_enqueue
    ):
        # A failed FIRST canary apply never moved traffic, so a rollback is a
        # needless write that also overwrites "failed" with "rolled_back".
        ctrl = controller_factory(
            hitl=HITLConfig(canary=False, full=False),
            stages=DeploymentStagesConfig(shadow=False, canary=True),
        )
        backend = controller_factory.state["backend"]
        backend.convergence.return_value = _not_converged()
        rec = _new_record(ctrl, pending_gate="canary", serving_role=None)

        ctrl.advance(rec.promotion_id)

        assert _get(ctrl, rec.promotion_id)["state"] == "failed"
        _patch_enqueue["rollback"].delay.assert_not_called()

    def test_swap_failure_once_serving_still_rolls_back(self, controller_factory, _patch_enqueue):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        backend.convergence.return_value = _not_converged()
        rec = _new_record(ctrl, pending_gate="canary", serving_role="shadow", verdict="advance")

        ctrl.advance(rec.promotion_id)

        assert _get(ctrl, rec.promotion_id)["state"] == "failed"
        _patch_enqueue["rollback"].delay.assert_called_once_with(rec.promotion_id)


class TestSkippedGateDropsItsApproval:
    def test_approval_is_not_carried_onto_the_gate_it_skips_to(
        self, controller_factory, _patch_enqueue
    ):
        # An approval granted for a 5% canary must not be spent on a 100% switch,
        # so the skip re-arms the record for the new gate.
        ctrl = controller_factory(
            hitl=HITLConfig(canary=True, full=True),
            stages=DeploymentStagesConfig(shadow=True, canary=False),
        )
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            state="approved",
            pending_gate="canary",
            serving_role="shadow",
            verdict="advance",
        )

        ctrl.advance(rec.promotion_id)
        doc = _get(ctrl, rec.promotion_id)
        assert doc["pending_gate"] == "full"
        assert doc["state"] == "promoting"  # approval dropped, not carried
        backend.apply.assert_not_called()

        # The full gate demands its OWN approval; with no verdict it would hold
        # instead. Either way the switch does not happen unreviewed.
        ctrl.advance(rec.promotion_id)
        assert _get(ctrl, rec.promotion_id)["state"] == "pending_approval"
        backend.apply.assert_not_called()


class TestPendingApprovalIsNeverBypassed:
    """A record awaiting a human must not have its gate run by a stray advance.

    The pause used to be gated on ``state == "promoting"``, so a record already
    in ``pending_approval`` fell straight through to ``_run_gate``. The
    reconciliation sweep enqueues without deduping, so a duplicate advance
    draining after the pause was written is a real way to reach it.
    """

    def test_duplicate_advance_re_pauses_instead_of_running_the_gate(
        self, controller_factory, _patch_enqueue
    ):
        ctrl = controller_factory(hitl=HITLConfig(full=True))
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, pending_gate="full", serving_role="canary", verdict="advance")

        ctrl.advance(rec.promotion_id)  # first advance pauses
        assert _get(ctrl, rec.promotion_id)["state"] == "pending_approval"
        backend.apply.assert_not_called()

        ctrl.advance(rec.promotion_id)  # the duplicate must NOT promote
        assert _get(ctrl, rec.promotion_id)["state"] == "pending_approval"
        backend.apply.assert_not_called()

    def test_a_real_approval_still_advances(self, controller_factory, _patch_enqueue):
        # The pause must stay escapable — this is the guard against over-fixing.
        ctrl = controller_factory(hitl=HITLConfig(full=True))
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, state="approved", pending_gate="full", serving_role="canary")
        ctrl.advance(rec.promotion_id)
        assert _get(ctrl, rec.promotion_id)["state"] == "promoted"
        assert backend.apply.call_args.args[2] == PromotionControl.FULL_SWITCH

    def test_skip_re_arms_a_pending_approval(self, controller_factory, _patch_enqueue):
        # A PENDING approval is dropped too: the human was asked about canary.
        ctrl = controller_factory(
            hitl=HITLConfig(canary=True, full=True),
            stages=DeploymentStagesConfig(shadow=True, canary=False),
        )
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            state="pending_approval",
            pending_gate="canary",
            serving_role="shadow",
            verdict="advance",
        )

        ctrl.advance(rec.promotion_id)
        doc = _get(ctrl, rec.promotion_id)
        assert doc["pending_gate"] == "full"
        assert doc["state"] == "promoting"

        ctrl.advance(rec.promotion_id)  # re-pauses, now against the full gate
        assert _get(ctrl, rec.promotion_id)["state"] == "pending_approval"
        backend.apply.assert_not_called()


class TestCanaryStepsValidation:
    def test_empty_steps_are_rejected_at_config_load(self):
        # An empty ladder used to fall back to a single 100% step, which every
        # backend rejects (0 < canary_percent < 100) — a runtime failure instead
        # of a config-load one.
        with pytest.raises(ValueError):
            CanaryStepsConfig(steps=[])

    def test_legacy_empty_steps_on_a_record_keeps_the_rest_of_its_policy(self, controller_factory):
        # An old `steps: []` opt-out no longer validates, and discarding the whole
        # policy would swap in the DEPLOYMENT DEFAULT and lose this run's HITL
        # gates. The legacy shape is translated instead.
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        rec = _new_record(
            ctrl,
            promotion_policy={
                "hitl": {"pull": False, "deploy": False, "canary": False, "full": True},
                "canary_steps": {"steps": []},
                "poll_max_attempts": 1,
            },
        )
        ctrl._apply_record_policy(rec)

        assert ctrl.policy.hitl.full is True  # the run's own gate survived
        assert ctrl.gate_order() == ["pull", "deploy", "full"]  # read as canary off

    def test_malformed_policy_falls_back_instead_of_raising(self, controller_factory):
        # promotion_policy is dict[str, Any], so its VALUES are unvalidated and
        # the legacy-shape migration must not escape the guard every other
        # unparseable policy falls into.
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=True))
        rec = _new_record(ctrl, promotion_policy={"canary_steps": "not-a-dict"})

        ctrl._apply_record_policy(rec)  # must not raise

        assert ctrl.policy.hitl.full is True  # fell back to the default policy


class TestPullThreadsSpecUri:
    def test_pull_passes_record_spec_uri_to_backend(self, controller_factory, _patch_enqueue):
        # The spec locator rides the pull so spec.yaml is staged with the weights.
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, spec_uri="s3://aoi-artifacts/runs/v2/baseline_spec.yaml")
        ctrl.advance(rec.promotion_id)
        assert (
            backend.pull.call_args.kwargs["spec_uri"]
            == "s3://aoi-artifacts/runs/v2/baseline_spec.yaml"
        )

    def test_pull_passes_none_spec_uri_when_unset(self, controller_factory, _patch_enqueue):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl)
        ctrl.advance(rec.promotion_id)
        assert backend.pull.call_args.kwargs["spec_uri"] is None


class TestPullRePresign:
    """The pull gate re-mints presigned URLs from the DURABLE raw locators on
    every attempt, so a slow/retrying pull can never 403 on an expired URL."""

    @staticmethod
    def _settings_with_deft():
        from src.config.deft import DEFTConfig

        fake = MagicMock()
        fake.system_config.deft_config = DEFTConfig()
        return fake

    def test_repres_from_raw_locator_and_passes_fresh_url_to_pull(
        self, controller_factory, _patch_enqueue
    ):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            artifact_uri="https://store/OLD?X-Amz-Signature=stale",
            artifact_locator="s3://aoi-artifacts/runs/v2/weights.onnx",
        )
        fresh = {"artifact": "https://store/FRESH?X-Amz-Signature=new", "spec": None}
        with (
            patch.object(controller_mod, "settings", self._settings_with_deft()),
            patch(
                "src.backends.deft.presign.presign_pull_locators", return_value=fresh
            ) as mock_presign,
        ):
            ctrl.advance(rec.promotion_id)

        mock_presign.assert_called_once()
        # The raw durable locator, not the stale presigned URL.
        assert mock_presign.call_args.args[1]["artifact"] == (
            "s3://aoi-artifacts/runs/v2/weights.onnx"
        )
        # The FRESH url reached the pull and was persisted for observability.
        assert backend.pull.call_args.args[2] == "https://store/FRESH?X-Amz-Signature=new"
        assert (
            _get(ctrl, rec.promotion_id)["artifact_uri"]
            == "https://store/FRESH?X-Amz-Signature=new"
        )

    def test_repres_also_signs_the_spec_locator(self, controller_factory, _patch_enqueue):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            artifact_uri="https://store/OLD_A",
            spec_uri="https://store/OLD_S",
            artifact_locator="s3://aoi-artifacts/runs/v2/weights.onnx",
            spec_locator="s3://aoi-artifacts/runs/v2/spec.yaml",
        )
        fresh = {"artifact": "https://store/FRESH_A", "spec": "https://store/FRESH_S"}
        with (
            patch.object(controller_mod, "settings", self._settings_with_deft()),
            patch("src.backends.deft.presign.presign_pull_locators", return_value=fresh),
        ):
            ctrl.advance(rec.promotion_id)
        assert backend.pull.call_args.args[2] == "https://store/FRESH_A"
        assert backend.pull.call_args.kwargs["spec_uri"] == "https://store/FRESH_S"

    def test_passthrough_http_locator_is_a_noop(self, controller_factory, _patch_enqueue):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            artifact_uri="https://dev/model.onnx",
            artifact_locator="https://dev/model.onnx",
        )
        with (
            patch.object(controller_mod, "settings", self._settings_with_deft()),
            patch("src.backends.deft.presign.presign_pull_locators") as mock_presign,
        ):
            ctrl.advance(rec.promotion_id)
        mock_presign.assert_not_called()  # pass-through URL needs no signing
        assert backend.pull.call_args.args[2] == "https://dev/model.onnx"

    def test_falls_back_to_stored_url_and_logs_when_repres_raises(
        self, controller_factory, _patch_enqueue, caplog
    ):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            artifact_uri="https://store/STORED?X-Amz-Signature=x",
            artifact_locator="s3://aoi-artifacts/runs/v2/weights.onnx",
        )
        with (
            patch.object(controller_mod, "settings", self._settings_with_deft()),
            patch(
                "src.backends.deft.presign.presign_pull_locators",
                side_effect=RuntimeError("storage backend down"),
            ),
            caplog.at_level("WARNING"),
        ):
            ctrl.advance(rec.promotion_id)
        # A presign hiccup must NOT hard-fail the pull: the stored URL is used.
        assert backend.pull.call_args.args[2] == "https://store/STORED?X-Amz-Signature=x"
        assert any("Re-presign before pull failed" in r.message for r in caplog.records)

    def test_noop_when_no_deft_config(self, controller_factory, _patch_enqueue):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            artifact_uri="https://store/STORED",
            artifact_locator="s3://aoi-artifacts/runs/v2/weights.onnx",
        )
        fake = MagicMock()
        fake.system_config.deft_config = None
        with (
            patch.object(controller_mod, "settings", fake),
            patch("src.backends.deft.presign.presign_pull_locators") as mock_presign,
        ):
            ctrl.advance(rec.promotion_id)
        mock_presign.assert_not_called()  # non-AOI backends unaffected
        assert backend.pull.call_args.args[2] == "https://store/STORED"


class TestHappyPath:
    def test_full_rollout_all_hitl_off(self, controller_factory, _patch_enqueue):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[10, 100])
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl)
        pid = rec.promotion_id

        ctrl.advance(pid)
        assert _get(ctrl, pid)["pending_gate"] == "deploy"
        backend.pull.assert_called_once()
        _patch_enqueue["advance"].delay.assert_called_with(pid)

        ctrl.advance(pid)
        doc = _get(ctrl, pid)
        assert doc["serving_role"] == "shadow"
        assert doc["pending_gate"] == "canary"
        assert backend.apply.call_args.args[2] == PromotionControl.SHADOW

        _set(ctrl, pid, verdict="advance")
        ctrl.advance(pid)
        doc = _get(ctrl, pid)
        assert doc["serving_role"] == "canary"
        assert doc["canary_step_index"] == 1
        assert doc["pending_gate"] == "canary"
        assert backend.apply.call_args.args[2] == PromotionControl.CANARY
        assert backend.apply.call_args.kwargs["canary_weight"] == pytest.approx(0.1)

        # Step 1 is 100%, so this escalates to the full gate.
        _set(ctrl, pid, verdict="advance")
        ctrl.advance(pid)
        doc = _get(ctrl, pid)
        assert doc["canary_step_index"] == 2
        assert doc["pending_gate"] == "full"

        _set(ctrl, pid, verdict="advance")
        ctrl.advance(pid)
        doc = _get(ctrl, pid)
        assert doc["state"] == "promoted"
        assert doc["pending_gate"] is None
        assert backend.apply.call_args.args[2] == PromotionControl.FULL_SWITCH
        backend.unload.assert_called_once_with("m", "v1")
        backend.cleanup.assert_not_called()


class TestServingVersionIntegerFlow:
    """F3: candidate_version/baseline_version are the MLflow/serving INTEGER
    versions, and the controller threads them verbatim to the Model Manager on
    pull/apply/unload/rollback (Triton requires integer version dirs). The DEFT
    run id is lineage on the record only and never reaches the backend."""

    def test_integer_versions_reach_pull_apply_unload(self, controller_factory, _patch_enqueue):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            candidate_version="2",
            baseline_version="1",
            deft_run_id="run-777",
        )
        pid = rec.promotion_id

        ctrl.advance(pid)  # pull
        assert backend.pull.call_args.args[1] == "2"  # the serving integer, not the run id

        ctrl.advance(pid)  # deploy(shadow)
        assert backend.apply.call_args.args[1] == "2"

        _set(ctrl, pid, verdict="advance")
        ctrl.advance(pid)  # canary (single 100% step -> escalate to full)

        _set(ctrl, pid, verdict="advance")
        ctrl.advance(pid)  # full switch + unload baseline
        assert backend.apply.call_args.args[1] == "2"

        backend.unload.assert_called_once_with("m", "1")
        assert _get(ctrl, pid)["state"] == "promoted"
        # The DEFT run id stayed on the record; the backend never saw it.
        assert _get(ctrl, pid)["deft_run_id"] == "run-777"

    def test_rollback_uses_baseline_integer(self, controller_factory):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            candidate_version="2",
            baseline_version="1",
            pending_gate="canary",
            serving_role="shadow",
            verdict="rollback",
        )
        ctrl.advance(rec.promotion_id)
        backend.rollback.assert_called_once_with(
            "m", "1", idempotency_key=f"{rec.promotion_id}:rollback"
        )


class TestTheFullSwitchDoesNotCloseTheRollbackWindow:
    """A full switch EVICTS the baseline, it does not delete it. ``cleanup`` reclaims
    disk since MR !616, so calling it here destroyed the retired baseline at the exact
    moment a rollback is most likely to be wanted, turning rollback from a routing
    change into a re-download with nothing to download.
    """

    def test_full_switch_evicts_the_baseline_via_unload(self, controller_factory, _patch_enqueue):
        """Mutation: revert the call site to ``self.backend.cleanup(...)`` -> red on
        BOTH assertions. The ``cleanup.assert_not_called`` half is the load-bearing
        one: asserting only that unload was called stays green if the code calls
        both."""
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, pending_gate="full", verdict="advance", serving_role="canary")

        ctrl.advance(rec.promotion_id)

        assert _get(ctrl, rec.promotion_id)["state"] == "promoted"
        backend.unload.assert_called_once_with("m", "v1")
        backend.cleanup.assert_not_called()

    def test_a_failed_baseline_unload_does_not_fail_the_promotion(
        self, controller_factory, _patch_enqueue
    ):
        """Mutation: remove the ``except BackendError`` around the unload -> the
        BackendError escapes ``advance`` and the record never reaches 'promoted'."""
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        backend = controller_factory.state["backend"]
        backend.unload.side_effect = BackendError(
            code="model_in_use", message="still routed", retryable=False, http_status=409
        )
        rec = _new_record(ctrl, pending_gate="full", verdict="advance", serving_role="canary")

        ctrl.advance(rec.promotion_id)

        assert _get(ctrl, rec.promotion_id)["state"] == "promoted"

    def test_a_successful_baseline_unload_stamps_the_eviction(
        self, controller_factory, _patch_enqueue
    ):
        """The stamp the disk-reclaim sweep nominates against (design §4.1).

        Mutation: drop the ``_update`` beside the unload -> red, and the sweep can
        never collect this baseline's directory for the rest of time.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        rec = _new_record(ctrl, pending_gate="full", verdict="advance", serving_role="canary")

        ctrl.advance(rec.promotion_id)

        doc = _get(ctrl, rec.promotion_id)
        assert doc["evicted_at"] is not None
        # The sweep ignores a stamp disagreeing with the record's own baseline,
        # so a stamp naming the candidate would be silently inert.
        assert doc["evicted_versions"] == ["v1"]
        assert doc["baseline_version"] == "v1"

    def test_a_failed_baseline_unload_leaves_no_stamp(self, controller_factory, _patch_enqueue):
        """A stamp on the failure path is worse than no stamp: it nominates a version
        still resident on a GPU for deletion, with nothing downstream able to tell.

        Mutation: move the stamp below the ``except BackendError``, or into a
        ``finally`` -> red.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        backend = controller_factory.state["backend"]
        backend.unload.side_effect = BackendError(
            code="model_in_use", message="still routed", retryable=False, http_status=409
        )
        rec = _new_record(ctrl, pending_gate="full", verdict="advance", serving_role="canary")

        ctrl.advance(rec.promotion_id)

        doc = _get(ctrl, rec.promotion_id)
        # The promotion still landed, so only the eviction failed.
        assert doc["state"] == "promoted"
        assert doc.get("evicted_at") is None
        assert not doc.get("evicted_versions")

    def test_a_baseline_unload_that_raises_a_bare_keyerror_is_still_swallowed(
        self, controller_factory, _patch_enqueue
    ):
        """``BackendError`` is only what the TRANSPORT raises. ``unload`` indexes
        ``data["version"]``/``data["status"]`` on a 200 body, so an unfamiliar Model
        Manager contract raises a bare ``KeyError``.

        Mutation: narrow the catch in ``_evict_and_stamp`` back to
        ``except BackendError`` -> the KeyError escapes ``advance``: the record is
        already ``promoted`` but ``_finalize_flywheel_run`` is never reached, so a
        step that only frees a GPU strands the parent run.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        backend = controller_factory.state["backend"]
        backend.unload.side_effect = KeyError("status")
        rec = _new_record(ctrl, pending_gate="full", verdict="advance", serving_role="canary")

        with patch.object(
            ctrl, "_finalize_flywheel_run", wraps=ctrl._finalize_flywheel_run
        ) as finalize:
            ctrl.advance(rec.promotion_id)

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "promoted"
        # Reached, so the eviction fell through rather than aborting the gate's
        # tail; unstamped, so nothing reads the failed unload as an eviction.
        assert finalize.call_args.args[1] == "promoted"
        assert doc.get("evicted_at") is None

    def test_a_failed_stamp_write_does_not_fail_the_promotion(
        self, controller_factory, _patch_enqueue, caplog
    ):
        """The stamp is written AFTER the terminal state is already in Mongo, so a
        raising ``_update`` would skip ``_finalize_flywheel_run`` and leave the parent
        run ``promoting`` for ever; on the rejection path, which runs inline under the
        approval API, it would turn a rejection Mongo already accepted into a 500.

        Mutation: move the stamp write back outside the ``try`` in
        ``_evict_and_stamp`` -> the RuntimeError escapes ``advance``. The log is
        asserted as well as the outcome, for the asymmetry with the failed unload
        above: there the slot was NOT freed, here it was.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, pending_gate="full", verdict="advance", serving_role="canary")
        col = ctrl.db[PROMOTIONS_COLLECTION]
        real_update_one = col.update_one

        def _fail_only_the_stamp(query, update):
            # The stamp is the only ``$addToSet`` the controller writes.
            if isinstance(update, dict) and "$addToSet" in update:
                raise RuntimeError("mongo write failed")
            return real_update_one(query, update)

        col.update_one = _fail_only_the_stamp
        try:
            with (
                patch.object(
                    ctrl, "_finalize_flywheel_run", wraps=ctrl._finalize_flywheel_run
                ) as finalize,
                caplog.at_level(logging.ERROR, logger="data_flywheel.promotion.controller"),
            ):
                ctrl.advance(rec.promotion_id)
        finally:
            col.update_one = real_update_one

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "promoted"
        # Reached, so the failed stamp fell through.
        assert finalize.call_args.args[1] == "promoted"
        backend.unload.assert_called_once_with("m", "v1")
        assert doc.get("evicted_at") is None
        # Which way round it failed: slot free, directory stranded.
        assert any(
            "the unload DID happen and the GPU slot is free" in record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.ERROR
        )


class TestTerminalTransitionsStampTheirSettleTime:
    """Nothing recorded WHEN a promotion finished, so the disk-reclaim sweep aged
    records from ``created_at``: one created eight days ago that fails today read as
    settled eight days ago and cleared the retention cutoff on the next tick.
    ``settled_at`` is that missing fact, in a field of its own -- ``decided_at`` is
    the approver's decision time and is not touched here.
    """

    def test_a_failure_stamps_its_own_settled_time(self, controller_factory, _patch_enqueue):
        """Mutation: drop the ``settled_at`` stamp from ``_update`` -> red, and the
        record's settle time stays the eight-day-old ``created_at``."""
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        backend = controller_factory.state["backend"]
        backend.convergence.return_value = ConvergenceReport(
            converged=False,
            reason=ConvergenceReason(code="failed", message="no", terminal=True),
        )
        long_ago = datetime.now(UTC) - timedelta(days=8)
        rec = _new_record(ctrl, pending_gate="full", verdict="advance", serving_role="canary")
        _set(ctrl, rec.promotion_id, created_at=long_ago)

        ctrl.advance(rec.promotion_id)

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "failed"
        assert doc["settled_at"] > long_ago

    def test_a_promotion_stamps_its_own_settled_time(self, controller_factory, _patch_enqueue):
        """The same stamp on the success side. Mutation: narrow the ``_update``
        condition to ``failed`` -> red, and every promoted record -- the ones that
        pin the live primary and the rollback target -- ages from ``created_at``."""
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        long_ago = datetime.now(UTC) - timedelta(days=8)
        rec = _new_record(ctrl, pending_gate="full", verdict="advance", serving_role="canary")
        _set(ctrl, rec.promotion_id, created_at=long_ago)

        ctrl.advance(rec.promotion_id)

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "promoted"
        assert doc["settled_at"] > long_ago

    def test_an_approvers_decision_time_is_not_the_settle_time(
        self, controller_factory, _patch_enqueue
    ):
        """A HITL promotion is approved at T1 and finishes at T2, so it ALREADY has a
        ``decided_at`` -- the approval's. Backfilling that field only where it is
        absent fixes nothing for exactly these promotions, and overwriting it would
        have the record claim the approver acted at a moment they were not involved
        in. Both timestamps have to exist, and they have to disagree.

        Mutation: stamp ``decided_at`` (fill-only or not) instead of ``settled_at``
        -> red, and the failure names which one moved.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        approved_at = datetime.now(UTC) - timedelta(days=8)
        rec = _new_record(ctrl, pending_gate="full", verdict="advance", serving_role="canary")
        _set(ctrl, rec.promotion_id, decided_by="alice", decided_at=approved_at)

        ctrl.advance(rec.promotion_id)

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "promoted"
        # The approver's record is untouched; the settle time is the rollout's.
        assert doc["decided_at"] == approved_at
        assert doc["decided_by"] == "alice"
        assert doc["settled_at"] > approved_at

    def test_repeating_a_terminal_state_keeps_the_stamp_it_already_has(
        self, controller_factory, _patch_enqueue
    ):
        """Writing the same terminal state twice is a retry, not a new ending -- and a
        record whose retention window is about to expire must not get a fresh lease out
        of one.

        Mutation: stamp unconditionally -> red.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        rec = _new_record(ctrl, pending_gate="full")

        ctrl._update(rec.promotion_id, state="failed")
        first = _get(ctrl, rec.promotion_id)["settled_at"]
        ctrl._update(rec.promotion_id, state="failed", pending_gate=None)

        doc = _get(ctrl, rec.promotion_id)
        assert doc["pending_gate"] is None  # the second write did land
        assert doc["settled_at"] == first

    def test_a_record_that_reaches_a_second_terminal_state_settles_again(
        self, controller_factory, _patch_enqueue
    ):
        """A record promoted in January and rolled back in March ended TWICE, and the
        retention window measured from here is what keeps the rollback's own target on
        disk. Dating it from the promotion starts that clock early.

        Mutation: the fill-only ``$ifNull`` -> red, March's rollback settles in January.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        rec = _new_record(ctrl, pending_gate="full")
        january = datetime.now(UTC) - timedelta(days=60)

        ctrl._update(rec.promotion_id, state="promoted")
        _set(ctrl, rec.promotion_id, settled_at=january)
        ctrl._update(rec.promotion_id, state="rolled_back")

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "rolled_back"
        assert doc["settled_at"] > january

    def test_the_state_and_its_stamp_land_in_one_write(self, controller_factory):
        """Two round trips left a window: the state committed, the stamp failed, and
        the record went terminal with no ``settled_at``. ``_settled_at`` has no
        fallback chain, so the sweep would never nominate that version again and its
        directory leaks for ever. The collection fails every write after the first,
        which is what a dropped connection between the two looked like.

        Mutation: split the stamp back into a second predicated ``update_one`` ->
        red twice over: the write count is 2, and the ``RuntimeError`` escapes
        ``_update`` leaving exactly the half-finalized record described above.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        rec = _new_record(ctrl, pending_gate="full")
        col = ctrl.db[PROMOTIONS_COLLECTION]
        real_update_one = col.update_one
        writes: list[dict] = []

        def _only_the_first_write_lands(query, update):
            writes.append(query)
            if len(writes) > 1:
                raise RuntimeError("connection lost between the two round trips")
            return real_update_one(query, update)

        col.update_one = _only_the_first_write_lands
        try:
            ctrl._update(rec.promotion_id, state="failed")
        finally:
            col.update_one = real_update_one

        assert len(writes) == 1
        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "failed"
        assert doc["settled_at"] is not None

    def test_a_terminal_write_stores_the_callers_values_verbatim(self, controller_factory):
        """The cost of the pipeline form: a pipeline ``$set`` EVALUATES what it is
        given, so a value must be wrapped in ``$literal`` or a string beginning with
        ``$`` is read as a field path and stored as that OTHER field's value. No
        terminal call site passes one today; this pins the guard for the one added
        later.

        Mutation: drop the ``$literal`` wrapping in ``_update`` -> red, and
        ``reason`` comes back as the candidate version rather than the text.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        rec = _new_record(ctrl, pending_gate="full", candidate_version="v2")

        ctrl._update(
            rec.promotion_id, state="failed", pending_gate=None, reason="$candidate_version"
        )

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "failed"
        assert doc["pending_gate"] is None
        assert doc["reason"] == "$candidate_version"
        assert doc["settled_at"] is not None


class TestRollbackRefusesAReclaimedTarget:
    def test_rollback_fails_fast_when_its_target_was_reclaimed(self, controller_factory):
        """The disk-reclaim sweep's one promotion-record write, earning its keep.

        Mutation: drop the ``rollback_target_reclaimed`` precheck in
        ``PromotionController.rollback`` -> ``backend.rollback`` IS called and the
        failure resurfaces later as an opaque convergence timeout instead.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            candidate_version="2",
            baseline_version="1",
            state="promoting",
            serving_role="shadow",
            rollback_target_reclaimed=True,
        )

        ctrl.rollback(rec.promotion_id)

        backend.rollback.assert_not_called()
        assert _get(ctrl, rec.promotion_id)["state"] == "failed"

    def test_an_unreclaimed_target_still_rolls_back(self, controller_factory):
        """The converse, so the precheck cannot be satisfied by refusing everything.

        Mutation: make the precheck unconditional -> red.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            candidate_version="2",
            baseline_version="1",
            state="promoting",
            serving_role="shadow",
        )

        ctrl.rollback(rec.promotion_id)

        backend.rollback.assert_called_once()


class TestRollbackAfterAFullSwitch:
    """The case no other rollback test reaches: they all start from a record that never
    ran ``full``, so the baseline is still loaded. After the switch the weights are on
    disk but nothing has them loaded, and memory is the only half left.
    """

    @staticmethod
    def _promote(ctrl, backend) -> PromotionRecord:
        """Drive the full switch, which unloads the baseline, and mirror that on the
        fake stack."""
        rec = _new_record(ctrl, pending_gate="full", verdict="advance", serving_role="canary")
        ctrl.advance(rec.promotion_id)
        assert _get(ctrl, rec.promotion_id)["state"] == "promoted"
        backend.unload.assert_called_once_with("m", "v1")
        backend.serving_state.return_value = _serving_state("v2")
        backend.rollback.reset_mock()
        return rec

    def test_a_rollback_after_the_full_switch_fails_fast_on_the_eviction(
        self, controller_factory, _patch_enqueue, caplog
    ):
        """The full switch leaves the WEIGHTS, not the rollback window: the baseline is
        unloaded and nothing reloads it, so in production routing back to it spends the
        whole poll budget and lands on ``failed``.

        Mutation: drop the ``_baseline_unloaded`` precheck -> the rollback is issued
        and nothing names the cause (this fake backend even reports it converged).
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        backend = controller_factory.state["backend"]
        rec = self._promote(ctrl, backend)

        with caplog.at_level(logging.ERROR, logger="data_flywheel.promotion.controller"):
            ctrl.rollback(rec.promotion_id)

        backend.rollback.assert_not_called()
        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "failed"
        # The reason, not just the outcome: a timeout would fail the same way.
        assert any(
            "does not list baseline m@v1" in record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.ERROR
        )

    @pytest.mark.parametrize(
        "unknown",
        [
            None,
            ServingState(model_name="m", ready_versions=None),
            _serving_state("v2", known_available=False),
        ],
        ids=["no-serving-state-route", "no-ready-versions-field", "loaded-state-unavailable"],
    )
    def test_an_unknown_read_never_refuses_a_rollback(self, controller_factory, unknown):
        """UNKNOWN IS NOT EVIDENCE. A Model Manager with no serving-state route, or one
        too old to report ``ready_versions``, would otherwise have every rollback in the
        deployment refused on an answer nobody gave.

        Mutation: drop any of the three unknown clauses in ``_baseline_unloaded`` ->
        red on that id, and rollback stops working against that Model Manager.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        backend.serving_state.return_value = unknown
        rec = _new_record(ctrl, pending_gate="canary", serving_role="canary")

        ctrl.rollback(rec.promotion_id)

        backend.rollback.assert_called_once()
        assert _get(ctrl, rec.promotion_id)["state"] == "rolled_back"

    def test_a_serving_state_read_that_raises_does_not_abort_the_rollback(self, controller_factory):
        """The precheck must not become a new way to lose a rollback: it runs outside
        ``rollback``'s ``try``, so an out-of-tree backend raising here would escape into
        the Celery task.

        Mutation: drop the ``except`` in ``_baseline_unloaded`` -> the RuntimeError
        escapes and the regressed candidate keeps serving.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        backend.serving_state.side_effect = RuntimeError("no")
        rec = _new_record(ctrl, pending_gate="canary", serving_role="canary")

        ctrl.rollback(rec.promotion_id)

        assert _get(ctrl, rec.promotion_id)["state"] == "rolled_back"

    def test_a_record_that_evicts_twice_keeps_both_stamps(self, controller_factory, _patch_enqueue):
        """One record CAN evict twice: the full switch takes the baseline, a later
        rollback the candidate. A single slot loses the baseline's stamp, the sweep
        stops nominating it, and that directory is stranded for good.

        Reachable only because the baseline came back into memory; nothing in the
        promotion path reloads it, so this stands in for the operator who did.

        Mutation: ``$set`` the stamp instead of ``$addToSet`` -> red, ``v1`` is gone.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False), steps=[100])
        backend = controller_factory.state["backend"]
        rec = self._promote(ctrl, backend)
        assert _get(ctrl, rec.promotion_id)["evicted_versions"] == ["v1"]
        backend.serving_state.return_value = _serving_state("v1", "v2")

        ctrl.rollback(rec.promotion_id)

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "rolled_back"
        backend.unload.assert_called_with("m", "v2")
        assert doc["evicted_versions"] == ["v1", "v2"]


class TestEscalationGating:
    def test_hold_verdict_is_noop(self, controller_factory):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, pending_gate="canary", serving_role="shadow", verdict="hold")
        backend.apply.reset_mock()
        ctrl.advance(rec.promotion_id)
        backend.apply.assert_not_called()
        assert _get(ctrl, rec.promotion_id)["pending_gate"] == "canary"

    def test_missing_verdict_is_noop(self, controller_factory):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, pending_gate="canary", serving_role="shadow", verdict=None)
        backend.apply.reset_mock()
        ctrl.advance(rec.promotion_id)
        backend.apply.assert_not_called()

    def test_regression_verdict_rolls_back(self, controller_factory):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, pending_gate="canary", serving_role="shadow", verdict="rollback")
        ctrl.advance(rec.promotion_id)
        backend.rollback.assert_called_once_with(
            "m", "v1", idempotency_key=f"{rec.promotion_id}:rollback"
        )
        assert _get(ctrl, rec.promotion_id)["state"] == "rolled_back"


class TestHITL:
    def test_canary_gate_pauses_for_approval(self, controller_factory):
        ctrl = controller_factory(hitl=HITLConfig(canary=True, full=True))
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, pending_gate="canary", serving_role="shadow", verdict="advance")
        backend.apply.reset_mock()
        ctrl.advance(rec.promotion_id)
        assert _get(ctrl, rec.promotion_id)["state"] == "pending_approval"
        backend.apply.assert_not_called()

    def test_approval_then_advance_applies(self, controller_factory, _patch_enqueue):
        ctrl = controller_factory(hitl=HITLConfig(canary=True, full=True))
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            state="pending_approval",
            pending_gate="canary",
            serving_role="shadow",
            # No online verdict ON PURPOSE: approval alone must move the gate, and
            # a verdict here would mask a bypass regression.
            verdict=None,
        )
        pid = rec.promotion_id
        ctrl.on_approval(pid, "canary", "approved")
        assert _get(ctrl, pid)["state"] == "approved"
        _patch_enqueue["advance"].delay.assert_called_with(pid)
        ctrl.advance(pid)
        backend.apply.assert_called()
        assert _get(ctrl, pid)["state"] in ("promoting", "promoted")


class TestRejects:
    def test_reject_pre_serving_archives(self, controller_factory):
        ctrl = controller_factory()
        rec = _new_record(ctrl, state="pending_approval", pending_gate="deploy")
        ctrl.on_approval(rec.promotion_id, "deploy", "rejected")
        assert _get(ctrl, rec.promotion_id)["state"] == "rejected"

    def test_reject_serving_rolls_back(self, controller_factory, _patch_enqueue):
        ctrl = controller_factory()
        rec = _new_record(
            ctrl, state="pending_approval", pending_gate="canary", serving_role="canary"
        )
        ctrl.on_approval(rec.promotion_id, "canary", "rejected")
        _patch_enqueue["rollback"].delay.assert_called_with(rec.promotion_id)


class TestFailureHandling:
    def test_swap_failure_marks_failed_and_autorollback(self, controller_factory, _patch_enqueue):
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        backend.convergence.return_value = _not_converged()
        rec = _new_record(ctrl, pending_gate="canary", serving_role="shadow", verdict="advance")
        ctrl.advance(rec.promotion_id)
        # The record is already terminal, so the auto-rollback needs its own task
        # and autoretry: an inline call could not be re-driven.
        _patch_enqueue["rollback"].delay.assert_called_once_with(rec.promotion_id)

        ctrl.rollback(rec.promotion_id)
        backend.rollback.assert_called_once()

    def test_terminal_state_is_noop(self, controller_factory):
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, state="promoted", pending_gate=None)
        ctrl.advance(rec.promotion_id)
        backend.apply.assert_not_called()
        backend.pull.assert_not_called()

    def test_retryable_backend_error_reraises_for_task_retry(self, controller_factory):
        # The old behaviour logged "will retry" and returned, retrying nothing.
        from src.lib.promotion.deployment_backend.base import BackendError

        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        err = BackendError(code="internal", message="503", retryable=True, http_status=503)
        backend.pull.side_effect = err
        rec = _new_record(ctrl)  # pending_gate=pull
        with pytest.raises(BackendError):
            ctrl.advance(rec.promotion_id)
        # Untouched state -> the retry (or the beat sweep) re-drives it.
        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "promoting" and doc["pending_gate"] == "pull"

    def test_non_retryable_backend_error_enqueues_rollback(
        self, controller_factory, _patch_enqueue
    ):
        # Same stranding hazard as _on_swap_failure: the record goes terminal
        # BEFORE the auto-rollback, so an inline call hitting a 503 re-raises
        # into a retry that exits on the terminal state.
        from src.lib.promotion.deployment_backend.base import BackendError

        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        err = BackendError(code="invalid", message="bad request", retryable=False)
        backend.apply.side_effect = err
        rec = _new_record(ctrl, pending_gate="canary", serving_role="shadow", verdict="advance")
        ctrl.advance(rec.promotion_id)
        assert _get(ctrl, rec.promotion_id)["state"] == "failed"
        _patch_enqueue["rollback"].delay.assert_called_once_with(rec.promotion_id)
        backend.rollback.assert_not_called()  # nothing ran inline

    def test_rollback_retryable_backend_error_reraises(self, controller_factory):
        # The SAFETY-NET path must retry too: a failed record is invisible to the
        # reconciliation sweep.
        from src.lib.promotion.deployment_backend.base import BackendError

        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        err = BackendError(code="internal", message="503", retryable=True, http_status=503)
        backend.rollback.side_effect = err
        rec = _new_record(ctrl, pending_gate="canary", serving_role="canary")
        with pytest.raises(BackendError):
            ctrl.rollback(rec.promotion_id)
        # Untouched state -> the task retry re-drives the rollback.
        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "promoting"

    def test_start_never_resurrects_a_terminal_promotion(self, controller_factory, _patch_enqueue):
        # start() must not flip a concluded rollout back to "promoting".
        ctrl = controller_factory()
        rec = _new_record(ctrl, state="promoted", pending_gate=None)
        ctrl.start(rec.promotion_id)
        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "promoted" and doc.get("pending_gate") is None
        _patch_enqueue["advance"].delay.assert_not_called()

    def test_rollback_swap_failure_marks_failed_not_rolled_back(self, controller_factory):
        # The regressed candidate may still be serving, so 'rolled_back' is a lie.
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        backend.convergence.return_value = _not_converged()
        rec = _new_record(ctrl, pending_gate="canary", serving_role="canary")
        ctrl.rollback(rec.promotion_id)
        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "failed"


class TestDeploymentDefaultPolicy:
    """``_load_policy`` is the last resort, and it stays AOI's.

    It reads the literal ``job_configs["aoi_job"]``. A deployment that also
    declares ``its_job`` must not move it — a record with no policy of its own
    keeps resolving exactly what it resolved before ITS existed.
    """

    @staticmethod
    def _settings(job_configs: dict) -> MagicMock:
        fake = MagicMock()
        fake.job_configs = job_configs
        return fake

    @staticmethod
    def _cfg(policy: PromotionPolicyConfig) -> MagicMock:
        cfg = MagicMock()
        cfg.promotion_policy = policy
        return cfg

    def test_an_aoi_deployment_resolves_its_declared_policy(self):
        declared = PromotionPolicyConfig(poll_max_attempts=42)
        with patch.object(
            controller_mod, "settings", self._settings({"aoi_job": self._cfg(declared)})
        ):
            assert PromotionController._load_policy() is declared

    def test_a_co_resident_its_job_config_does_not_move_it(self):
        aoi = PromotionPolicyConfig(poll_max_attempts=1)
        its = PromotionPolicyConfig(poll_max_attempts=2)
        job_configs = {"its_job": self._cfg(its), "aoi_job": self._cfg(aoi)}
        with patch.object(controller_mod, "settings", self._settings(job_configs)):
            assert PromotionController._load_policy() is aoi

    def test_no_deft_job_config_at_all_falls_back_to_defaults(self):
        with patch.object(controller_mod, "settings", self._settings({})):
            assert PromotionController._load_policy() == PromotionPolicyConfig()

    def test_the_shipped_config_resolves_a_real_declared_policy(self):
        """End to end against the SHIPPED YAML, with nothing patched out.

        The e2e harness patches ``_load_policy`` away entirely, so without this
        the real resolution never runs against the real config in any test.
        """
        from pathlib import Path

        from src.config.base import Settings

        # AOI_SELECTION_TAG has no `:-` default, so the renderer refuses without it.
        with patch.dict(os.environ, {"AOI_SELECTION_TAG": "aoi1"}):
            shipped = Settings.from_yaml(
                Path(__file__).resolve().parents[3] / "config" / "config.deft.yaml"
            )
        assert "aoi_job" in shipped.job_configs
        with patch.object(controller_mod, "settings", shipped):
            resolved = PromotionController._load_policy()
        assert resolved is shipped.job_configs["aoi_job"].promotion_policy


class TestConvergencePolling:
    """The stateless poll: what ends a gate, and what only spends a tick.

    The Model Manager says whether it landed and whether waiting can still help.
    The controller decides how long to wait.
    """

    @staticmethod
    def _budget(ctrl, attempts: int):
        """Widen the poll budget; the shared policy fixture uses 1 attempt."""
        ctrl.policy = ctrl.policy.model_copy(update={"poll_max_attempts": attempts})

    def test_an_unreachable_model_manager_spends_a_poll_rather_than_failing_the_swap(
        self, controller_factory, _patch_enqueue
    ):
        """A poll we could not read costs a tick, not the swap.

        A 404 from a replica that has not rolled yet tells us nothing about the
        backend, so it must not condemn the swap.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        self._budget(ctrl, 3)
        backend = controller_factory.state["backend"]
        backend.convergence.side_effect = [
            BackendError("not_found", "unknown", retryable=False, http_status=404),
            ConvergenceReport(converged=True),
        ]
        rec = _new_record(ctrl, pending_gate="deploy", serving_role=None)

        ctrl.advance(rec.promotion_id)

        assert backend.convergence.call_count == 2, "the 404 must cost a tick, not the gate"
        assert _get(ctrl, rec.promotion_id)["state"] != "failed"
        _patch_enqueue["rollback"].delay.assert_not_called()

    def test_a_single_attempt_budget_still_does_not_condemn_an_unreadable_poll(
        self, controller_factory, _patch_enqueue
    ):
        """The tolerance has to hold at every budget, including 1.

        The three-strike escape is out of reach below three attempts, so the
        rule is about why the loop stopped: a budget that expires while the
        control plane is still unreadable is retryable, not a failed swap.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        self._budget(ctrl, 1)
        backend = controller_factory.state["backend"]
        backend.convergence.side_effect = BackendError(
            "not_found", "unknown", retryable=False, http_status=404
        )
        rec = _new_record(ctrl, pending_gate="deploy", serving_role=None)

        with pytest.raises(BackendError) as excinfo:
            ctrl.advance(rec.promotion_id)

        assert excinfo.value.retryable is True
        assert _get(ctrl, rec.promotion_id)["state"] != "failed"
        _patch_enqueue["rollback"].delay.assert_not_called()

    def test_a_terminal_reason_fails_the_swap_without_burning_the_budget(
        self, controller_factory, _patch_enqueue
    ):
        """Terminality is the Model Manager's call and is believed at once.

        The budget bounds waiting on something that might still happen, so there
        is nothing to spend once we are told it cannot.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        self._budget(ctrl, 5)
        backend = controller_factory.state["backend"]
        backend.convergence.return_value = _not_converged("primary_moved", terminal=True)
        rec = _new_record(ctrl, pending_gate="deploy", serving_role=None)

        ctrl.advance(rec.promotion_id)

        assert backend.convergence.call_count == 1, "a terminal reason must stop the loop at once"
        assert _get(ctrl, rec.promotion_id)["state"] == "failed"

    def test_three_unreadable_polls_escape_as_a_retryable_error(
        self, controller_factory, _patch_enqueue
    ):
        """A dead control plane routes to the Celery backoff, not a false timeout.

        Reporting "readiness_timeout" after a run of connection errors would
        blame the swap for the control plane being down.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        self._budget(ctrl, 10)
        backend = controller_factory.state["backend"]
        backend.convergence.side_effect = BackendError(
            "internal", "connection refused", retryable=False, http_status=503
        )
        rec = _new_record(ctrl, pending_gate="deploy", serving_role=None)

        with pytest.raises(BackendError) as excinfo:
            ctrl.advance(rec.promotion_id)

        assert excinfo.value.retryable is True, "a down control plane must be retried, not fatal"
        assert backend.convergence.call_count == 3
        assert _get(ctrl, rec.promotion_id)["state"] != "failed"

    def test_a_rejected_expectation_is_terminal_because_waiting_cannot_fix_a_schema(
        self, controller_factory, _patch_enqueue
    ):
        """422 is the one unreadable poll that must not be tolerated.

        Other transport failures might clear. A contract rejection means this
        Model Manager will never accept this expectation.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        self._budget(ctrl, 5)
        backend = controller_factory.state["backend"]
        backend.convergence.side_effect = BackendError(
            "bad_request", "unknown field", retryable=False, http_status=422
        )
        rec = _new_record(ctrl, pending_gate="deploy", serving_role=None)

        ctrl.advance(rec.promotion_id)

        assert backend.convergence.call_count == 1
        assert _get(ctrl, rec.promotion_id)["state"] == "failed"

    def test_the_expectation_is_persisted_before_the_wait_and_echoed_verbatim(
        self, controller_factory, _patch_enqueue
    ):
        """A worker that dies inside the wait must resume the same expectation.

        Persisting after the poll would make a restart re-derive the token from
        a replayed apply, whose from_version is read after the write landed.
        """
        ctrl = controller_factory(hitl=HITLConfig(canary=False, full=False))
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, pending_gate="deploy", serving_role=None)

        ctrl.advance(rec.promotion_id)

        stored = _get(ctrl, rec.promotion_id)["applied"]
        assert stored == _APPLIED, "the token must be stored verbatim"
        polled = backend.convergence.call_args[0][0]
        assert polled == _APPLIED, "the token must be handed back verbatim"


class TestExpectationFor:
    """``expectation_for`` -- which token to poll when an apply was a no-op."""

    @staticmethod
    def _result(applied, *, noop):
        return ApplyResult(
            applied=applied,
            to_version=applied["to_version"],
            control=PromotionControl.SHADOW,
            started_at=datetime.now(UTC).isoformat(),
            noop=noop,
        )

    def test_an_ordinary_apply_takes_the_new_token(self):
        fresh = {**_APPLIED, "key": "k-2"}
        assert expectation_for(_APPLIED, self._result(fresh, noop=False)) == fresh

    def test_a_noop_replay_keeps_the_persisted_token(self):
        """A noop issued no routing write, so its from_version was read after the
        original landed and is not the version that was displaced."""
        replay = {**_APPLIED, "from_version": "v2"}  # misleading: read post-write
        assert expectation_for(_APPLIED, self._result(replay, noop=True)) == _APPLIED

    def test_a_noop_for_a_different_gate_does_not_keep_the_old_token(self):
        """Matched on key, not just on noop.

        A record part-way up the ladder holds the previous gate's token. Keeping
        it would poll a shadow expectation while a canary is being applied.
        """
        other_gate = {**_APPLIED, "control": "canary", "key": "k-canary-0"}
        assert expectation_for(_APPLIED, self._result(other_gate, noop=True)) == other_gate

    def test_no_previous_token_always_takes_the_new_one(self):
        assert expectation_for(None, self._result(_APPLIED, noop=True)) == _APPLIED


class TestACandidateIsEvictedWhenItsPromotionEnds:
    """The GPU-slot leak that made the reclaim sweep near-useless. DFW evicted in one
    place only: the full switch's unload of the outgoing baseline. Nothing unloaded a
    regressed candidate after a rollback, or one rejected before it ever routed -- so
    the flywheel's dominant output held a slot for ever, and stayed uncollectable,
    since a resident version must never be deleted.
    """

    # The rollback path: routing is WITHDRAWN first.
    def test_a_converged_rollback_evicts_the_regressed_candidate(self, controller_factory):
        """Mutation: drop the ``_evict_and_stamp`` call from ``rollback`` -> red, and
        the regressed candidate holds its slot for the life of the box.

        The baseline half is load-bearing: v1 is the version the rollback just
        routed traffic BACK to, so an unload naming it evicts what production serves.
        """
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, pending_gate="canary", serving_role="canary")

        ctrl.rollback(rec.promotion_id)

        assert _get(ctrl, rec.promotion_id)["state"] == "rolled_back"
        backend.unload.assert_called_once_with("m", "v2")

    def test_a_converged_rollback_stamps_the_candidate_not_the_baseline(self, controller_factory):
        """The stamp names the version it is about. Mutation: stamp
        ``baseline_version`` instead -> red here, and in production the sweep would
        be handed authority over the live rollback target's directory.
        """
        ctrl = controller_factory()
        rec = _new_record(ctrl, pending_gate="canary", serving_role="canary")

        ctrl.rollback(rec.promotion_id)

        doc = _get(ctrl, rec.promotion_id)
        assert doc["evicted_versions"] == ["v2"]
        assert doc["evicted_at"] is not None
        assert doc["baseline_version"] == "v1"

    def test_a_rollback_that_did_not_converge_evicts_nothing(self, controller_factory):
        """CONVERGENCE FIRST: the non-convergence branch refuses to record
        'rolled_back' because the regressed candidate may still be serving, and the
        same reasoning forbids evicting it.

        Mutation: hoist the eviction above the ``if not ok`` branch -> red. Asserted
        as "never ISSUED", not "refused": a test that let the call reach the Model
        Manager and leaned on its 409 would pass with the guard gone.
        """
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        backend.convergence.return_value = _not_converged()
        rec = _new_record(ctrl, pending_gate="canary", serving_role="canary")

        ctrl.rollback(rec.promotion_id)

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "failed"
        backend.unload.assert_not_called()
        assert doc.get("evicted_at") is None

    def test_a_rollback_that_moved_traffic_and_then_stalled_evicts_nothing(
        self, controller_factory, caplog
    ):
        """The dangerous half of CONVERGENCE FIRST. Live (report F4) the failed
        rollback full-switched to a version that was ALREADY the default, so no
        routing write went out and no traffic moved. Here it really re-routes
        v2 -> v1 and THEN convergence runs out, so the withdrawal is unacknowledged
        and the candidate may still carry traffic.

        Mutation: hoist the ``_evict_and_stamp`` call above the ``if not ok`` branch
        -> red. The rollback call, persisted token and poll are asserted too, or
        this stays green for a controller that returned before issuing any routing
        write at all.
        """
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        # A REAL routing write: ``noop=False`` with its own idempotency key, so
        # ``expectation_for`` polls the rollback's token, not the canary one.
        backend.rollback.return_value = ApplyResult(
            applied={
                "model_name": "m",
                "to_version": "v1",
                "control": "full_switch",
                "from_version": "v2",
                "canary_weight": None,
                "key": "k-rb",
            },
            to_version="v1",
            control=PromotionControl.FULL_SWITCH,
            started_at=datetime.now(UTC).isoformat(),
            from_version="v2",
            noop=False,
            swap_id="swap-rb",
        )
        backend.convergence.return_value = _not_converged("routing_absent")
        rec = _new_record(
            ctrl,
            pending_gate="canary",
            serving_role="canary",
            applied={**_APPLIED, "control": "canary", "key": "k-canary-0"},
        )

        with caplog.at_level(logging.ERROR, logger="data_flywheel.promotion.controller"):
            ctrl.rollback(rec.promotion_id)

        doc = _get(ctrl, rec.promotion_id)
        # Traffic really moved: the write was issued, its token replaced the canary
        # one, and the poll that timed out was on THAT token.
        backend.rollback.assert_called_once_with(
            "m", "v1", idempotency_key=f"{rec.promotion_id}:rollback"
        )
        assert doc["applied"]["key"] == "k-rb"
        assert (doc["applied"]["from_version"], doc["applied"]["to_version"]) == ("v2", "v1")
        assert backend.convergence.call_args.args[0]["key"] == "k-rb"
        # The withdrawal is unacknowledged, so nothing is evicted.
        backend.unload.assert_not_called()
        assert doc["state"] == "failed"
        assert doc.get("evicted_at") is None
        assert not doc.get("evicted_versions")
        # 'rolled_back' and serving_role='default' are written together, so state
        # alone would miss a half-write claiming the baseline is live.
        assert doc["serving_role"] == "canary"
        # Names the expectation that did not land, so another branch's failure
        # cannot pass here.
        assert any(
            "Rollback did not converge" in record.getMessage() and "k-rb" in record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.ERROR
        )

    def test_a_failed_candidate_unload_does_not_re_fail_the_rollback(self, controller_factory):
        """A promotion that has already ended must never be re-failed by housekeeping.

        Mutation: drop the ``except BackendError`` from ``_evict_and_stamp`` -> the
        error escapes into ``rollback``'s own ``except BackendError``, which writes
        ``state="failed"``, so a rollback that fully succeeded is recorded as a
        failure because a GPU did not free.
        """
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        backend.unload.side_effect = BackendError(
            code="model_in_use", message="still routed", retryable=False, http_status=409
        )
        rec = _new_record(ctrl, pending_gate="canary", serving_role="canary")

        ctrl.rollback(rec.promotion_id)

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "rolled_back"
        # No stamp, so the sweep never nominates a still-resident version.
        assert doc.get("evicted_at") is None

    def test_a_retryable_unload_error_does_not_re_drive_the_rollback(self, controller_factory):
        """``rollback`` re-raises RETRYABLE backend errors so the Celery task retries
        the swap. A housekeeping unload must not borrow that budget: the swap already
        converged, and the GPU is merely slow to free.

        Mutation: re-raise retryable errors from ``_evict_and_stamp`` -> red.
        """
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        backend.unload.side_effect = BackendError(
            code="unavailable", message="503", retryable=True, http_status=503
        )
        rec = _new_record(ctrl, pending_gate="canary", serving_role="canary")

        ctrl.rollback(rec.promotion_id)  # must not raise

        assert _get(ctrl, rec.promotion_id)["state"] == "rolled_back"

    # The never-routed path: there is no routing to withdraw.
    def test_a_rejected_candidate_that_was_only_ever_loaded_is_evicted(
        self, controller_factory, _patch_enqueue
    ):
        """The pull gate LOADS, so a rejection at the deploy gate leaves a candidate
        resident that nothing ever routed. No rollback runs here, so before this
        nothing freed it.

        Mutation: drop the ``_evict_never_routed_candidate`` call from
        ``on_approval`` -> red.
        """
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, state="pending_approval", pending_gate="deploy")

        ctrl.on_approval(rec.promotion_id, "deploy", "rejected")

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "rejected"
        backend.unload.assert_called_once_with("m", "v2")
        assert doc["evicted_versions"] == ["v2"]
        backend.rollback.assert_not_called()

    def test_a_cancelled_run_evicts_the_candidate_its_pull_loaded(self, controller_factory):
        """The same hole on the cancellation path: a run cancelled between gates
        rejects the promotion and used to leave the pulled candidate resident.

        Mutation: drop the ``_evict_never_routed_candidate`` call from ``advance``'s
        cancellation branch -> red.
        """
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, pending_gate="deploy", flywheel_run_id="507f1f77bcf86cd799439011")
        db = MagicMock()
        db.is_flywheel_run_cancelled.return_value = True
        with patch("src.api.db_manager.get_db_manager", return_value=db):
            ctrl.advance(rec.promotion_id)

        assert _get(ctrl, rec.promotion_id)["state"] == "rejected"
        backend.unload.assert_called_once_with("m", "v2")

    def test_a_candidate_rejected_before_its_pull_is_never_unloaded(
        self, controller_factory, _patch_enqueue
    ):
        """``pull`` is mandatory, always first, and is where the load happens. Before
        it completes nothing is resident, and what is on disk may be a partial
        download -- which an unload answering 200 would have us STAMP as evicted,
        handing the sweep authority over a directory a retry could still write into.

        Mutation: drop the ``pending_gate`` check in
        ``_evict_never_routed_candidate`` -> red. Asserted as "never issued", so the
        Model Manager's own refusal cannot stand in for this guard.
        """
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, state="pending_approval", pending_gate="pull")

        ctrl.on_approval(rec.promotion_id, "pull", "rejected")

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "rejected"
        backend.unload.assert_not_called()
        assert doc.get("evicted_at") is None

    def test_a_candidate_whose_serving_swap_was_issued_is_never_unloaded(
        self, controller_factory, _patch_enqueue
    ):
        """``serving_role`` is stamped only AFTER a swap converges, so it cannot tell
        "no swap was ever issued" from "a swap was issued and its fate is unknown".
        ``applied`` is persisted immediately after every serving apply returns, so its
        PRESENCE is the durable answer: a worker that died mid-deploy leaves this
        record, and unloading there evicts a version whose shadow route may have
        landed.

        Mutation: drop the ``applied is not None`` check -> red.
        """
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        rec = _new_record(
            ctrl,
            state="pending_approval",
            pending_gate="canary",
            serving_role=None,
            applied=dict(_APPLIED),
        )

        ctrl.on_approval(rec.promotion_id, "canary", "rejected")

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "rejected"
        backend.unload.assert_not_called()
        assert doc.get("evicted_at") is None

    def test_a_serving_candidate_is_never_unloaded_by_the_unrouted_path(self, controller_factory):
        """Asserted on the helper directly: both call sites already sit inside a
        ``not has_served`` branch, so no drive through the machine can turn this red
        -- and a guard whose absence nothing notices is how a canary carrying 5% of
        production gets unloaded.

        Mutation: drop the ``has_served`` check in ``_evict_never_routed_candidate``
        -> red.
        """
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        rec = _new_record(ctrl, pending_gate="canary", serving_role="canary")

        ctrl._evict_never_routed_candidate(rec)

        backend.unload.assert_not_called()

    def test_a_failed_eviction_never_fails_a_rejection(self, controller_factory, _patch_enqueue):
        """Same posture as the baseline unload: logged, not acted on.

        Mutation: let ``_evict_and_stamp`` raise -> the BackendError escapes
        ``on_approval`` into the API request that called it, and a rejection that was
        fully recorded reports as a server error.
        """
        ctrl = controller_factory()
        backend = controller_factory.state["backend"]
        backend.unload.side_effect = BackendError(
            code="not_found", message="never loaded", retryable=False, http_status=404
        )
        rec = _new_record(ctrl, state="pending_approval", pending_gate="deploy")

        ctrl.on_approval(rec.promotion_id, "deploy", "rejected")

        doc = _get(ctrl, rec.promotion_id)
        assert doc["state"] == "rejected"
        assert doc.get("evicted_at") is None
