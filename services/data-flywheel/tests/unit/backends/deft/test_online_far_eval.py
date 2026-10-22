"""Online eval: FAR only, with the same metric/op/threshold semantics as offline.

DEFT's KPI (``app/execution/kpi.py``) defines FAR as the **false-alarm** rate:
of the PASS population, the fraction the model flagged as a defect. Online eval
measures the same quantity at the deployed operating point, reports it under the
same name and units the offline gate uses (``FAR_pct``, a percentage), and gates
on it with the same declarative constraints.
"""

import pytest

from src.backends.deft.eval import DeftAOIEvaluator
from src.config.deft import KpiConstraint


def _sample(*, ai, truth, confirmed=None):
    """One scored target. ``truth`` is the reference label; ``confirmed`` the operator's."""
    return {
        "target_id": "t",
        "ai_classification": ai,
        "ai_confidence": 0.5,
        "reference_label": truth,
        "confirmed_label": confirmed,
    }


def _population(*, n_pass, n_false_alarm, n_defect=0, n_caught=0):
    """``n_pass`` good units of which ``n_false_alarm`` are flagged, plus defects."""
    samples = []
    for i in range(n_pass):
        samples.append(_sample(ai="NotGood" if i < n_false_alarm else "Good", truth="Good"))
    for i in range(n_defect):
        samples.append(_sample(ai="NotGood" if i < n_caught else "Good", truth="NotGood"))
    return samples


def _evaluator(*, threshold=0.1, min_sample_size=10, op="<"):
    ev = DeftAOIEvaluator(min_sample_size=min_sample_size)
    ev.online_target = [KpiConstraint(metric="FAR_pct", op=op, threshold=threshold)]
    return ev


# --- the metric itself ------------------------------------------------------


def test_far_is_false_alarms_over_pass_population_as_a_percentage():
    ev = _evaluator()
    m = ev.evaluate(None, None, _population(n_pass=1000, n_false_alarm=25))["metrics"]
    assert m["FAR_pct"] == pytest.approx(2.5)
    assert m["n_pass"] == 1000
    assert m["n_false_alarms"] == 25


def test_far_ignores_defect_units_entirely():
    """Defects belong to recall, not FAR. Adding them must not move FAR."""
    ev = _evaluator()
    a = ev.evaluate(None, None, _population(n_pass=200, n_false_alarm=10))["metrics"]
    b = ev.evaluate(
        None, None, _population(n_pass=200, n_false_alarm=10, n_defect=50, n_caught=25)
    )["metrics"]
    assert a["FAR_pct"] == b["FAR_pct"] == pytest.approx(5.0)


def test_far_is_not_the_defect_miss_rate():
    """Guards the semantic this change corrects.

    A candidate that flags every PASS unit and misses every defect has FAR=100,
    not FAR=0. The previous implementation computed the miss rate and would have
    reported ~100 for the opposite population.
    """
    ev = _evaluator()
    m = ev.evaluate(
        None, None, _population(n_pass=100, n_false_alarm=100, n_defect=10, n_caught=0)
    )["metrics"]
    assert m["FAR_pct"] == pytest.approx(100.0)


def test_operator_confirmation_overrides_the_reference_label():
    ev = _evaluator(min_sample_size=1)
    # Reference says defect, operator says good -> it joins the PASS population,
    # and the candidate's NotGood call becomes a false alarm.
    samples = [_sample(ai="NotGood", truth="NotGood", confirmed="Good")]
    m = ev.evaluate(None, None, samples)["metrics"]
    assert m["n_pass"] == 1
    assert m["FAR_pct"] == pytest.approx(100.0)


def test_targets_without_ground_truth_are_excluded():
    ev = _evaluator(min_sample_size=1)
    samples = [
        *_population(n_pass=10, n_false_alarm=1),
        {"target_id": "x", "ai_classification": "NotGood"},
    ]
    m = ev.evaluate(None, None, samples)["metrics"]
    assert m["n"] == 11
    assert m["n_pass"] == 10
    assert m["FAR_pct"] == pytest.approx(10.0)


# --- verdicts ---------------------------------------------------------------


def test_advance_when_far_meets_the_constraint():
    ev = _evaluator(threshold=5.0)
    r = ev.evaluate(None, None, _population(n_pass=1000, n_false_alarm=10))  # 1.0%
    assert r["verdict"] == "advance"


def test_rollback_when_far_violates_the_constraint():
    ev = _evaluator(threshold=5.0)
    r = ev.evaluate(None, None, _population(n_pass=1000, n_false_alarm=90))  # 9.0%
    assert r["verdict"] == "rollback"
    assert r["unmet"]


def test_boundary_is_governed_by_the_operator():
    exactly_five = _population(n_pass=1000, n_false_alarm=50)  # 5.0%
    assert _evaluator(threshold=5.0, op="<").evaluate(None, None, exactly_five)["verdict"] == (
        "rollback"
    )
    assert _evaluator(threshold=5.0, op="<=").evaluate(None, None, exactly_five)["verdict"] == (
        "advance"
    )


def test_hold_when_pass_population_is_too_small():
    """The FAR denominator, not the raw sample count, is what must be sufficient."""
    ev = _evaluator(min_sample_size=100)
    samples = _population(n_pass=99, n_false_alarm=0) + _population(
        n_pass=0, n_false_alarm=0, n_defect=500, n_caught=500
    )
    r = ev.evaluate(None, None, samples)
    assert r["verdict"] == "hold"


def test_hold_on_empty_sample_set():
    assert _evaluator().evaluate(None, None, [])["verdict"] == "hold"
    assert _evaluator().evaluate(None, None, None)["verdict"] == "hold"


def test_hold_when_no_criteria_are_configured():
    """Fail-closed: without criteria a promotion must never advance."""
    ev = DeftAOIEvaluator(min_sample_size=1)
    ev.online_target = []
    ev.thresholds = {}
    r = ev.evaluate(None, None, _population(n_pass=100, n_false_alarm=0))
    assert r["verdict"] == "hold"


# --- parity with the offline gate -------------------------------------------


def test_same_constraint_object_drives_both_gates():
    """One `{metric, op, threshold}` must mean the same thing on both sides."""
    constraint = KpiConstraint(metric="FAR_pct", op="<", threshold=5.0)

    ev = DeftAOIEvaluator(min_sample_size=10)
    ev.online_target = [constraint]
    online = ev.evaluate(None, None, _population(n_pass=1000, n_false_alarm=90))

    offline = ev.kpi_eval(None, {"metrics": {"FAR_pct": 9.0}}, {"kpi_target": [constraint]})

    assert online["verdict"] == "rollback"
    assert offline["passed"] is False
    assert online["metrics"]["FAR_pct"] == pytest.approx(9.0)


def test_legacy_fraction_thresholds_are_converted_to_percent():
    """The legacy dict held FRACTIONS; FAR_pct is a percentage.

    The shipped default was ``false_accept_rate: 0.001`` meaning 0.1%. Reading
    that as ``FAR_pct < 0.001`` would be 100x stricter and roll back healthy
    candidates on every deployment that has not migrated.
    """
    ev = DeftAOIEvaluator(min_sample_size=10, thresholds={"false_accept_rate": 0.001})
    ev.online_target = []
    # 0.05% FAR is inside the intended 0.1% bar.
    assert ev.evaluate(None, None, _population(n_pass=10000, n_false_alarm=5))["verdict"] == (
        "advance"
    )
    # 0.2% is outside it.
    assert ev.evaluate(None, None, _population(n_pass=10000, n_false_alarm=20))["verdict"] == (
        "rollback"
    )


def test_legacy_percent_key_is_not_rescaled():
    """A ``FAR_pct`` key is already a percentage and must be taken as-is."""
    ev = DeftAOIEvaluator(min_sample_size=10, thresholds={"FAR_pct": 5.0})
    ev.online_target = []
    assert ev.evaluate(None, None, _population(n_pass=1000, n_false_alarm=40))["verdict"] == (
        "advance"
    )
    assert ev.evaluate(None, None, _population(n_pass=1000, n_false_alarm=60))["verdict"] == (
        "rollback"
    )


def test_legacy_thresholds_dict_still_gates_far():
    """Back-compat: deployments configured with the old flat dict keep working."""
    ev = DeftAOIEvaluator(min_sample_size=10, thresholds={"FAR_pct": 5.0})
    ev.online_target = []
    assert ev.evaluate(None, None, _population(n_pass=1000, n_false_alarm=90))["verdict"] == (
        "rollback"
    )
    assert ev.evaluate(None, None, _population(n_pass=1000, n_false_alarm=10))["verdict"] == (
        "advance"
    )


def test_recall_is_reported_but_never_gates():
    """DEFT validates FAR only; recall is context for the operator, not a gate."""
    ev = _evaluator(threshold=5.0)
    r = ev.evaluate(
        None, None, _population(n_pass=1000, n_false_alarm=10, n_defect=100, n_caught=0)
    )
    assert r["metrics"]["recall_pct"] == pytest.approx(0.0)
    assert r["verdict"] == "advance"
