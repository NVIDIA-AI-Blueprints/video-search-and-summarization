"""Online-eval policy in the beat: disable, observation window, timeout action.

Covers the three ways a promotion leaves the online gate other than by a
decisive verdict:

* the gate is turned off, so it never enters online eval at all;
* the window elapses on a persistent hold, and ``on_timeout`` decides;
* ``escalate`` hands a held promotion to the existing HITL surface.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.api.promotion.models import PromotionRecord
from src.api.promotion.service import PROMOTIONS_COLLECTION, PromotionService
from src.tasks.promotion import online_eval_beat

FAR_TARGET = [{"metric": "FAR_pct", "op": "<", "threshold": 5.0}]


def _seed(fake_db, *, eval_harness: dict | None = None, serving_since=None, **overrides):
    fields = {
        "model_name": "m",
        "candidate_version": "2",
        "baseline_version": "1",
        "state": "promoting",
        "serving_role": "shadow",
        "pending_gate": "canary",
        "use_case": "aoi",
    }
    fields.update(overrides)
    record = PromotionRecord(**fields)
    if eval_harness is not None:
        record.promotion_policy = {"eval_harness": eval_harness}
    PromotionService(fake_db).create(record)
    if serving_since is not None:
        fake_db[PROMOTIONS_COLLECTION].update_one(
            {"promotion_id": record.promotion_id}, {"$set": {"serving_since": serving_since}}
        )
    return record


def _run(fake_db, evaluator):
    """Run the beat with a stubbed provider, capturing enqueued follow-ups."""
    provider = MagicMock()
    provider.evaluator.return_value = evaluator
    advance, rollback = MagicMock(), MagicMock()
    with (
        patch("src.backends.factory.get_backend_provider", return_value=provider),
        patch("src.tasks.promotion.controller._get_db", return_value=fake_db),
        patch("src.tasks.promotion.tasks.advance_promotion.delay", advance),
        patch("src.tasks.promotion.tasks.rollback_promotion.delay", rollback),
    ):
        result = online_eval_beat.online_eval_poll()
    return result, advance, rollback


def _evaluator(verdict="hold", metrics=None):
    ev = MagicMock()
    ev.collect.return_value = []
    ev.evaluate.return_value = {"verdict": verdict, "metrics": metrics or {}}
    ev.online_target = []
    ev.thresholds = {}
    ev.min_sample_size = 100
    return ev


def _state(fake_db, promotion_id):
    return fake_db[PROMOTIONS_COLLECTION].find_one({"promotion_id": promotion_id})


def _ago(seconds: float) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


# --- Gap 1: the gate can be turned off --------------------------------------


def test_disabled_gate_never_collects_or_evaluates(fake_db):
    """Enforced high: a disabled use case must not enter the eval loop at all.

    Faking an `advance` verdict inside evaluate() would look equivalent but
    would still pull samples and record a verdict that was never measured.
    """
    rec = _seed(fake_db, eval_harness={"enabled": False})
    ev = _evaluator()
    _result, advance, _rollback = _run(fake_db, ev)

    ev.collect.assert_not_called()
    ev.evaluate.assert_not_called()
    advance.assert_called_once_with(rec.promotion_id)


def test_disabled_gate_records_no_fabricated_verdict(fake_db):
    rec = _seed(fake_db, eval_harness={"enabled": False})
    _run(fake_db, _evaluator())
    assert _state(fake_db, rec.promotion_id).get("verdict") is None


def test_enabled_gate_still_evaluates(fake_db):
    rec = _seed(fake_db, eval_harness={"enabled": True, "online_target": FAR_TARGET})
    ev = _evaluator(verdict="advance", metrics={"FAR_pct": 1.0})
    _result, advance, _rollback = _run(fake_db, ev)

    ev.evaluate.assert_called_once()
    assert _state(fake_db, rec.promotion_id)["verdict"] == "advance"
    advance.assert_called_once_with(rec.promotion_id)


def test_unstated_enabled_behaves_as_enabled(fake_db):
    _seed(fake_db, eval_harness={"online_target": FAR_TARGET})
    ev = _evaluator()
    _run(fake_db, ev)
    ev.evaluate.assert_called_once()


# --- Gap 2: the observation window ------------------------------------------


def test_hold_inside_the_window_just_holds(fake_db):
    rec = _seed(
        fake_db,
        eval_harness={"online_target": FAR_TARGET, "max_observation_window_s": 3600},
        serving_since=_ago(60),
    )
    _result, advance, rollback = _run(fake_db, _evaluator("hold"))

    assert _state(fake_db, rec.promotion_id)["state"] == "promoting"
    advance.assert_not_called()
    rollback.assert_not_called()


def test_no_window_configured_holds_forever(fake_db):
    """Prior behaviour has to survive: no deadline unless one is asked for."""
    rec = _seed(fake_db, eval_harness={"online_target": FAR_TARGET}, serving_since=_ago(10_000_000))
    _result, advance, rollback = _run(fake_db, _evaluator("hold"))

    assert _state(fake_db, rec.promotion_id)["state"] == "promoting"
    advance.assert_not_called()
    rollback.assert_not_called()


def test_a_decisive_verdict_is_unaffected_by_an_elapsed_window(fake_db):
    """The window only rescues a HOLD; it must not override a real verdict."""
    rec = _seed(
        fake_db,
        eval_harness={
            "online_target": FAR_TARGET,
            "max_observation_window_s": 1,
            "on_timeout": "advance",
        },
        serving_since=_ago(9999),
    )
    _result, advance, rollback = _run(fake_db, _evaluator("rollback"))

    assert _state(fake_db, rec.promotion_id)["verdict"] == "rollback"
    # A decisive verdict takes the normal path (advance() routes the rollback),
    # NOT the on_timeout=advance branch the elapsed window would have taken.
    advance.assert_called_once_with(rec.promotion_id)
    rollback.assert_not_called()


def test_missing_serving_since_does_not_time_out(fake_db):
    """Records predating this feature have no clock; they must not instantly expire."""
    rec = _seed(
        fake_db,
        eval_harness={"online_target": FAR_TARGET, "max_observation_window_s": 1},
    )
    _result, advance, rollback = _run(fake_db, _evaluator("hold"))

    assert _state(fake_db, rec.promotion_id)["state"] == "promoting"
    advance.assert_not_called()
    rollback.assert_not_called()


def test_zero_window_expires_immediately(fake_db):
    """0 is a valid deadline ("escalate on the first hold"), not "disabled".

    Only None disables the window; the config accepts ``ge=0``, so a zero-second
    deadline has to be able to fire.
    """
    rec = _seed(
        fake_db,
        eval_harness={
            "online_target": FAR_TARGET,
            "max_observation_window_s": 0,
            "on_timeout": "escalate",
        },
        serving_since=_ago(0),
    )
    _run(fake_db, _evaluator("hold"))
    assert _state(fake_db, rec.promotion_id)["state"] == "pending_approval"


def test_corrupt_serving_since_does_not_abort_the_whole_pass(fake_db):
    """One bad record must not cost every other promotion its evaluation.

    The timeout check runs OUTSIDE the per-promotion try/except, so an exception
    there escapes the loop and silently skips every promotion behind it.

    The verdict must be ``hold`` for this to bite: the check short-circuits on
    ``verdict == "hold" and _window_elapsed(...)``, so a decisive verdict never
    reaches the corrupt value at all.
    """
    broken = _seed(
        fake_db,
        eval_harness={"online_target": FAR_TARGET, "max_observation_window_s": 60},
        serving_since={"not": "a datetime"},
    )
    healthy = _seed(
        fake_db,
        eval_harness={"online_target": FAR_TARGET, "max_observation_window_s": 60},
        serving_since=_ago(1),
    )

    ev = _evaluator("hold")
    _run(fake_db, ev)

    # The pass reached BOTH promotions; the bad one did not abort the sweep.
    assert ev.evaluate.call_count == 2
    # And the unreadable clock is skipped, not escalated on a value we cannot read.
    assert _state(fake_db, broken.promotion_id)["state"] == "promoting"
    assert _state(fake_db, healthy.promotion_id)["state"] == "promoting"


def test_unparseable_serving_since_string_is_skipped(fake_db):
    rec = _seed(
        fake_db,
        eval_harness={
            "online_target": FAR_TARGET,
            "max_observation_window_s": 1,
            "on_timeout": "escalate",
        },
        serving_since="not-a-timestamp",
    )
    _run(fake_db, _evaluator("hold"))
    assert _state(fake_db, rec.promotion_id)["state"] == "promoting"


# --- Gap 3: what happens when the window elapses ----------------------------


def test_timeout_escalate_hands_the_decision_to_a_human(fake_db):
    rec = _seed(
        fake_db,
        eval_harness={
            "online_target": FAR_TARGET,
            "max_observation_window_s": 1,
            "on_timeout": "escalate",
        },
        serving_since=_ago(3600),
    )
    _result, advance, rollback = _run(fake_db, _evaluator("hold"))

    doc = _state(fake_db, rec.promotion_id)
    assert doc["state"] == "pending_approval"
    assert doc["pending_gate"] == "canary"
    advance.assert_not_called()
    rollback.assert_not_called()


def test_escalated_record_is_approvable_through_the_existing_api(fake_db):
    """The whole point of escalate: reuse the HITL surface, add no new one.

    The approve endpoint is atomically guarded on state == pending_approval, so
    a held record was previously unreachable by it.
    """
    rec = _seed(
        fake_db,
        eval_harness={
            "online_target": FAR_TARGET,
            "max_observation_window_s": 1,
            "on_timeout": "escalate",
        },
        serving_since=_ago(3600),
    )
    _run(fake_db, _evaluator("hold"))

    with patch("src.api.promotion.service.PromotionService._dispatch_decision"):
        approved = PromotionService(fake_db).approve(rec.promotion_id, approved_by="alice")
    assert approved is not None
    assert approved.state == "approved"


def test_timeout_rollback_gives_up_fail_closed(fake_db):
    rec = _seed(
        fake_db,
        eval_harness={
            "online_target": FAR_TARGET,
            "max_observation_window_s": 1,
            "on_timeout": "rollback",
        },
        serving_since=_ago(3600),
    )
    _result, advance, rollback = _run(fake_db, _evaluator("hold"))

    rollback.assert_called_once_with(rec.promotion_id)
    advance.assert_not_called()


def test_timeout_advance_unblocks_the_verdict_gate(fake_db):
    """Enqueueing is not enough — advance() rejects a record still on `hold`.

    The record has to leave the verdict gate's way, which it does by taking the
    same `state == "approved"` escape a human approval uses. Effect, not queue
    traffic, is what the E2E test in test_aoi_e2e_promotion.py then confirms.
    """
    rec = _seed(
        fake_db,
        eval_harness={
            "online_target": FAR_TARGET,
            "max_observation_window_s": 1,
            "on_timeout": "advance",
        },
        serving_since=_ago(3600),
    )
    _result, advance, rollback = _run(fake_db, _evaluator("hold"))

    doc = _state(fake_db, rec.promotion_id)
    assert doc["state"] == "approved", "advance() would reject this record"
    # Nothing fabricates a verdict the evaluator never produced.
    assert doc["verdict"] == "hold"
    # The audit trail says policy decided, not a person.
    assert doc["decided_by"] == "policy:on_timeout"
    assert doc.get("timed_out_at") is not None
    advance.assert_called_once_with(rec.promotion_id)
    rollback.assert_not_called()


def test_timeout_advance_latches_and_does_not_refire(fake_db):
    """Without latching this re-enqueues a no-op advance once per tick, forever."""
    rec = _seed(
        fake_db,
        eval_harness={
            "online_target": FAR_TARGET,
            "max_observation_window_s": 1,
            "on_timeout": "advance",
        },
        serving_since=_ago(3600),
    )
    _run(fake_db, _evaluator("hold"))
    ev = _evaluator("hold")
    _result, advance, _rollback = _run(fake_db, ev)

    # The record left the `state: promoting` query, so the beat never sees it.
    ev.evaluate.assert_not_called()
    advance.assert_not_called()
    assert _state(fake_db, rec.promotion_id)["state"] == "approved"


def test_malformed_policy_never_reaches_the_evaluator(fake_db):
    """Validation falling back to defaults must apply to the CRITERIA too.

    Reading the raw dict here would configure the evaluator from the very values
    validation just refused — one record running two policies.
    """
    ev = _evaluator("hold")
    _seed(
        fake_db,
        eval_harness={
            "on_timeout": "carry_on",  # invalid -> whole block falls back
            "online_target": [{"metric": "FAR_pct", "op": "<", "threshold": 99.0}],
            "min_sample_size": 3,
        },
        serving_since=_ago(1),
    )
    _run(fake_db, ev)

    assert ev.online_target == [], "criteria from a rejected policy leaked through"
    assert ev.min_sample_size != 3, "min_sample_size from a rejected policy leaked through"


def test_escalation_happens_once_not_every_tick(fake_db):
    """A pending_approval record leaves the observed set, so the beat stops acting."""
    rec = _seed(
        fake_db,
        eval_harness={
            "online_target": FAR_TARGET,
            "max_observation_window_s": 1,
            "on_timeout": "escalate",
        },
        serving_since=_ago(3600),
    )
    _run(fake_db, _evaluator("hold"))
    ev = _evaluator("hold")
    _run(fake_db, ev)

    ev.evaluate.assert_not_called()
    assert _state(fake_db, rec.promotion_id)["state"] == "pending_approval"


@pytest.mark.parametrize("role", ["shadow", "canary"])
def test_window_applies_to_both_observed_roles(fake_db, role):
    rec = _seed(
        fake_db,
        serving_role=role,
        eval_harness={
            "online_target": FAR_TARGET,
            "max_observation_window_s": 1,
            "on_timeout": "escalate",
        },
        serving_since=_ago(3600),
    )
    _run(fake_db, _evaluator("hold"))
    assert _state(fake_db, rec.promotion_id)["state"] == "pending_approval"


class TestSweepUsesServingRoleNotGateNames:
    """The reconciliation sweep must key on "has it served?", not gate names.

    With ``deployment_stages.shadow: false`` the FIRST serving gate is
    ``canary``; with both stages off it is ``full``. A record parked there has
    no ``serving_role`` and no verdict, so it is invisible to the verdict loop
    (which filters on ``serving_role``) — if the sweep also skipped it because
    the gate is not literally pull/deploy, the promotion would stall forever.
    That is the exact stranding the sweep exists to prevent, and the retryable
    -error path in ``tasks.py`` documents the sweep as its recovery contract.
    """

    @pytest.mark.parametrize("gate", ["canary", "full"])
    def test_never_served_record_is_swept_whatever_its_gate(self, fake_db, gate):
        rec = _seed(
            fake_db,
            serving_role=None,  # optional stage disabled -> never served
            pending_gate=gate,
            verdict=None,
        )
        ev = _evaluator()
        result, advance, _rollback = _run(fake_db, ev)

        ev.evaluate.assert_not_called()  # the verdict loop cannot see it
        advance.assert_called_once_with(rec.promotion_id)
        assert result["swept"] == 1

    def test_serving_record_without_a_decisive_verdict_is_left_alone(self, fake_db):
        # Still the normal case: something IS serving and is being observed, so
        # the sweep must not pile advances onto a healthy rollout.
        rec = _seed(fake_db, serving_role="shadow", pending_gate="canary", verdict="hold")
        result, advance, _rollback = _run(fake_db, _evaluator("hold"))

        assert result["swept"] == 0
        advance.assert_not_called()
        assert _state(fake_db, rec.promotion_id)["verdict"] == "hold"
