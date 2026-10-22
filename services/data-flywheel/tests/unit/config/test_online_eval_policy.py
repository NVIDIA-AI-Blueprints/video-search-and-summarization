"""Online-eval policy: enabled / observation window / timeout action.

Three states have to stay distinguishable at config load:

* ``enabled: false``            -> skip online eval, promote on the offline gate
* ``enabled: true`` + criteria  -> gate on FAR
* ``enabled: true``, no criteria -> a misconfiguration, rejected here

The third is the important one. Skipping online eval is fail-OPEN, so it must
never be inferred from an absent field: a dropped config line would then be
indistinguishable from an honest intent to skip, and would silently auto-promote.
"""

import pytest
from pydantic import ValidationError

from src.config.deft import EvalHarnessConfig


def _cfg(**kw):
    return EvalHarnessConfig(**kw)


# --- defaults ---------------------------------------------------------------


def test_online_eval_is_enabled_by_default():
    """Opting OUT is the deliberate act; the safe posture is the default."""
    cfg = _cfg(online_target=[{"metric": "FAR_pct", "op": "<", "threshold": 0.1}])
    assert cfg.online_enabled is True


def test_no_deadline_by_default_preserves_current_behaviour():
    cfg = _cfg(online_target=[{"metric": "FAR_pct", "op": "<", "threshold": 0.1}])
    assert cfg.max_observation_window_s is None
    assert cfg.on_timeout == "escalate"


# --- the misconfiguration guard ---------------------------------------------


def test_explicitly_enabled_without_any_criteria_is_rejected_at_load():
    """An explicit `enabled: true` with nothing to gate on contradicts itself."""
    with pytest.raises(ValidationError, match="online_target"):
        _cfg(enabled=True, online_target=[], thresholds={})


def test_unstated_enabled_without_criteria_is_left_alone():
    """Ambiguous but not dangerous — the evaluator holds on missing criteria.

    Rejecting here would break every default construction, and would buy nothing:
    a dropped `online_target` line stalls the promotion, it does not promote it.
    """
    cfg = _cfg()
    assert cfg.online_enabled is True
    assert cfg.online_target == []


def test_disabled_without_criteria_is_fine():
    """Skipping is explicit, so no criteria are needed."""
    cfg = _cfg(enabled=False, online_target=[], thresholds={})
    assert cfg.online_enabled is False


def test_legacy_thresholds_satisfy_the_criteria_requirement():
    """An un-migrated deployment still gates via the legacy FAR entry.

    Rejecting these would break every deployment that has not moved to
    online_target, even though the evaluator still honours them.
    """
    cfg = _cfg(enabled=True, online_target=[], thresholds={"false_accept_rate": 0.001})
    assert cfg.online_enabled is True


def test_legacy_non_far_thresholds_do_not_satisfy_it():
    """Only a FAR entry gates, so only a FAR entry counts as criteria."""
    with pytest.raises(ValidationError, match="online_target"):
        _cfg(enabled=True, online_target=[], thresholds={"recall": 0.99})


def test_empty_eval_harness_block_still_loads():
    """`eval_harness: {}` is what most deployments and tests actually build."""
    assert EvalHarnessConfig(**{}).online_enabled is True


def test_dump_round_trip_does_not_trip_the_validator():
    """model_dump() makes defaults explicit; the tri-state must survive that.

    With a plain `bool = True` default, a round trip would turn every unstated
    config into an explicit `enabled: true` and reject it.
    """
    dumped = EvalHarnessConfig(**{}).model_dump()
    assert EvalHarnessConfig(**dumped).online_enabled is True


# --- timeout policy ---------------------------------------------------------


@pytest.mark.parametrize("action", ["escalate", "rollback", "advance"])
def test_on_timeout_accepts_the_three_actions(action):
    cfg = _cfg(
        online_target=[{"metric": "FAR_pct", "op": "<", "threshold": 0.1}],
        max_observation_window_s=3600,
        on_timeout=action,
    )
    assert cfg.on_timeout == action


def test_unknown_timeout_action_is_rejected():
    with pytest.raises(ValidationError):
        _cfg(
            online_target=[{"metric": "FAR_pct", "op": "<", "threshold": 0.1}],
            on_timeout="carry_on",
        )


def test_negative_observation_window_is_rejected():
    with pytest.raises(ValidationError):
        _cfg(
            online_target=[{"metric": "FAR_pct", "op": "<", "threshold": 0.1}],
            max_observation_window_s=-1,
        )
