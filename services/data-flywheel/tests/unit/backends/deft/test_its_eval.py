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
"""``DeftITSEvaluator`` — an OFFLINE gate, and an unconditional online ``hold``.

ITS is its own JOB TYPE (``its_job``) running the SAME DEFT pipeline as
``aoi_job``; ``use_case: "its"`` is the orthogonal RUNTIME key, and it is the one
that selects the evaluator this module tests. The evaluator has exactly two
jobs, and the second one is to say no:

* **OFFLINE (``kpi_eval``) — the shipped gate, and v1's only automatic
  judgement.** DEFT reports free-form ``{name: number}`` metrics and the
  shipped ``its_job_config`` states one constraint (``macro_ap >= 0.55``). This is
  ITS's OWN implementation, deliberately not shared with ``DeftAOIEvaluator``:
  the two use cases must not be able to edit each other's promotion decisions.

* **ONLINE (``evaluate``) — no metric contract, so an unconditional ``hold``
  with empty metrics, logged at WARNING.** There is nothing to compute; a
  plausible-looking number would gate a real rollout on a guess. It refuses by
  HOLDING rather than by raising, because a raise escapes before the verdict is
  written and the beat's blanket ``except Exception: continue`` then strands the
  promotion in ``promoting`` forever, holding the concurrency-1 parent queue
  slot — a config that merely omits ``eval_harness.enabled`` (it defaults to
  ON) would wedge every run in the deployment. A ``hold`` costs nothing: it
  never promotes, and it keeps the observation-window timeout reachable so
  ``on_timeout: escalate`` still fires. The shipped ``its_job_config`` sets
  ``eval_harness.enabled: false``, so no ITS promotion should reach the call at
  all — ``tests/unit/promotion/test_its_e2e_promotion.py`` pins that end of it,
  and :class:`TestOnlineEvaluationAlwaysHolds` pins this end.

The offline gate is where every assertion about NUMBERS lives, because it is the
only place ITS produces one. Note in particular what fail-closed means for the
shipped ``macro_ap`` constraint. Its key is read off deft-service's
``_collect_kpi_metrics``, but that MR is still a draft, so the key can still
move: an unemitted key rejects every candidate forever, so the rejection says
``reason=metric-not-reported`` rather than looking like a bad model.
"""

from __future__ import annotations

import pytest

from src.backends.deft import eval as eval_mod
from src.backends.deft.eval import (
    DeftAOIEvaluator,
    DeftEvaluator,
    DeftITSEvaluator,
    evaluator_for,
    get_evaluator,
)
from src.config import EvalHarnessConfig
from src.config.deft import KpiConstraint
from src.lib.eval_harness import BaseEvaluator, OnlineEval


def _cfg(*constraints: dict) -> EvalHarnessConfig:
    return EvalHarnessConfig(kpi_target=list(constraints))


# --------------------------------------------------------------------------- #
# registry / seam
# --------------------------------------------------------------------------- #


class TestRegistration:
    """``use_case`` -> evaluator is on the live path.

    ``initialize_deft_workflow`` resolves the evaluator through the provider seam
    before any DB write, so this is what makes a typo'd use case cost seconds
    instead of a fine-tune. It runs on every ITS job.
    """

    def test_its_is_registered_as_its_own_evaluator(self):
        ev = get_evaluator("its")
        assert isinstance(ev, DeftITSEvaluator)
        assert isinstance(ev, DeftEvaluator)
        assert isinstance(ev, BaseEvaluator)

    def test_aoi_registration_is_untouched(self):
        assert isinstance(get_evaluator("aoi"), DeftAOIEvaluator)
        assert not isinstance(get_evaluator("aoi"), DeftITSEvaluator)

    def test_no_arg_construction_works(self):
        # The provider seam builds with no args; every field must default.
        ev = DeftITSEvaluator()
        assert ev.online_target == []
        assert ev.thresholds == {}
        assert ev._results_source is None

    def test_results_source_is_forwarded(self):
        ev = get_evaluator("its", results_source=lambda w, r: [])
        assert ev._results_source is not None


class TestEvaluatorFor:
    """``evaluator_for`` is the public form of the registry question.

    It exists so callers outside this module (the provider's AOI-family guard)
    need not read the private ``_EVALUATORS`` dict. Unlike ``get_evaluator`` it
    answers with a CLASS and never raises — an unregistered use case is a
    legitimate None, and callers must read that None as "not this family".
    """

    def test_returns_the_registered_class_not_an_instance(self):
        assert evaluator_for("its") is DeftITSEvaluator
        assert evaluator_for("aoi") is DeftAOIEvaluator

    def test_unregistered_use_case_is_none_and_does_not_raise(self):
        # Fail-closed: the AOI-family guard turns this None into False rather
        # than grading a rollout on another use case's collected data.
        assert evaluator_for("no_such_use_case") is None

    def test_reads_the_registry_at_call_time(self):
        eval_mod._EVALUATORS["aoi_lookalike"] = DeftAOIEvaluator
        try:
            assert evaluator_for("aoi_lookalike") is DeftAOIEvaluator
        finally:
            eval_mod._EVALUATORS.pop("aoi_lookalike", None)
        assert evaluator_for("aoi_lookalike") is None


# --------------------------------------------------------------------------- #
# the online gate: there isn't one, and it declines in-band
# --------------------------------------------------------------------------- #


class TestOnlineEvaluationAlwaysHolds:
    """``evaluate`` returns ``hold`` with no metrics, and says so at WARNING.

    It used to raise ``NotImplementedError``, on the theory that refusing is a
    more honest signal than an endless hold. The theory was right about the
    signal and wrong about the cost: ``eval_harness.enabled`` defaults to ON
    when a config simply omits it, and the raise escaped the poll BEFORE the
    verdict write, so the beat's blanket ``except Exception: continue`` left the
    promotion in ``state=promoting`` with no verdict — invisible to the
    reconciliation sweep, unreachable by the observation-window timeout, and
    holding the concurrency-1 parent queue slot against every other run in the
    deployment until someone hand-edited Mongo.

    ``hold`` is the same non-promotion delivered in-band: the beat only enqueues
    ``advance``/``rollback``, so nothing is auto-promoted, but the timeout branch
    becomes reachable and ``on_timeout: escalate`` fires as configured. The
    WARNING carries the signal the exception used to.
    """

    def test_evaluate_holds_with_no_metrics(self):
        out = DeftITSEvaluator().evaluate(None, None, [])
        assert out == {"metrics": {}, "verdict": "hold"}

    def test_it_holds_even_on_a_fully_configured_gate_with_real_samples(self):
        # Not an accident of the empty-target/empty-sample fail-closed paths:
        # there is no input for which ITS produces a promoting online verdict,
        # and no input for which it invents a metric.
        ev = DeftITSEvaluator(min_sample_size=1)
        ev.online_target = [{"metric": "precision_pct", "op": ">=", "threshold": 90.0}]
        out = ev.evaluate(None, None, [{"tp": 9, "fp": 0, "fn": 1, "frames": 300}])
        assert out == {"metrics": {}, "verdict": "hold"}

    def test_the_warning_says_which_use_case_and_why(self, caplog):
        # The log line is now the only thing an operator sees when a deployment
        # turns the gate on, so it has to name the use case and the config that
        # governs it — and be at WARNING, not debug, or it is invisible.
        with caplog.at_level("WARNING", logger=eval_mod.logger.name):
            DeftITSEvaluator().evaluate(None, None, [])
        records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert records, "the refusal must be logged at WARNING"
        message = records[-1].getMessage()
        assert "ITS" in message
        assert "eval_harness.enabled=false" in message

    def test_the_harness_wrapper_passes_the_hold_through(self):
        # OnlineEval validates verdicts and defaults unknown ones to "hold", so
        # this also proves the hold is a RECOGNISED verdict rather than a value
        # that merely happens to be coerced into one.
        ev = DeftITSEvaluator()
        ev._results_source = lambda window, role: [{"tp": 1, "fp": 0, "fn": 0}]
        out = OnlineEval(ev).evaluate(None, None, {"model": "m", "version": "v"}, "shadow")
        assert out == {"metrics": {}, "verdict": "hold"}

    def test_it_never_advances(self):
        # The one thing the hold must never become: an ITS model promoted on
        # zero evaluation. Cheap to assert, and the whole point of not using
        # "advance" as the stand-in verdict.
        ev = DeftITSEvaluator(min_sample_size=0)
        for sample in ([], [{"tp": 100, "fp": 0, "fn": 0, "frames": 1000}] * 50):
            assert ev.evaluate(None, None, sample)["verdict"] == "hold"

    def test_aoi_still_evaluates_online(self):
        # The blanket hold is ITS's alone — it must not have been inherited
        # upward onto the use case that does have a metric contract.
        out = DeftAOIEvaluator(min_sample_size=1).evaluate(
            None, None, [{"ai_classification": "Good", "confirmed_label": "Good"}]
        )
        assert out["verdict"] == "hold"  # no criteria configured, not a crash
        assert out["metrics"], "AOI still computes real metrics"


# --------------------------------------------------------------------------- #
# the offline gate: ITS's own, and v1's only automatic judgement
# --------------------------------------------------------------------------- #


class TestOfflineGateIsITSsOwn:
    """Ownership, not inheritance: neither use case can edit the other's gate."""

    def test_its_defines_its_own_kpi_eval(self):
        assert "kpi_eval" in vars(DeftITSEvaluator)
        assert DeftITSEvaluator.kpi_eval is not DeftAOIEvaluator.kpi_eval

    def test_it_does_not_borrow_the_aoi_constraint_engine(self):
        # AOI's helpers are private to AOI; ITS reaching for them is how the two
        # gates would start to drift into one.
        for name in ("_unmet_constraints", "_lookup_metric", "_extract_deft_metrics"):
            assert not hasattr(DeftITSEvaluator, name)


class TestOfflineGateVerdicts:
    """The gate that actually runs on a shipped ITS promotion."""

    def test_map50_constraint_passes_and_fails_on_merit(self):
        ev = get_evaluator("its")
        cfg = _cfg({"metric": "mAP50", "op": ">=", "threshold": 0.85})
        assert ev.kpi_eval(None, {"metrics": {"mAP50": 0.91}}, cfg) == {
            "metrics": {"mAP50": 0.91},
            "passed": True,
        }
        failed = ev.kpi_eval(None, {"metrics": {"mAP50": 0.62}}, cfg)
        assert failed["passed"] is False
        assert failed["unmet"] == ["mAP50 >= 0.85 (actual=0.62)"]

    def test_per_class_offline_constraints_need_no_new_metric_code(self):
        # The metrics dict is free-form, so a per-class AP constraint is checked
        # by exactly the code that checks the fleet-wide one.
        ev = get_evaluator("its")
        cfg = _cfg(
            {"metric": "AP50_car", "op": ">=", "threshold": 0.9},
            {"metric": "AP50_person", "op": ">=", "threshold": 0.8},
        )
        metrics = {"mAP50": 0.9, "AP50_car": 0.95, "AP50_person": 0.72}
        out = ev.kpi_eval(None, {"metrics": metrics}, cfg)
        assert out["passed"] is False
        assert out["unmet"] == ["AP50_person >= 0.8 (actual=0.72)"]

    def test_baseline_is_ignored(self):
        # DEFT returns candidate-only metrics and runs its own baseline_eval
        # inside the loop; a DFW-side comparison would compare numbers possibly
        # measured on different KPI-set versions.
        ev = get_evaluator("its")
        cfg = _cfg({"metric": "mAP50", "op": ">=", "threshold": 0.55})
        candidate = {"metrics": {"mAP50": 0.6}}
        assert ev.kpi_eval({"metrics": {"mAP50": 0.99}}, candidate, cfg) == ev.kpi_eval(
            None, candidate, cfg
        )

    def test_partial_constraint_is_skipped_not_counted(self):
        # A row missing metric/op/threshold is unusable, not "unmet" — a
        # malformed entry must not silently block every candidate.
        out = get_evaluator("its").kpi_eval(
            None, {"metrics": {"mAP50": 0.6}}, {"kpi_target": [{"metric": "mAP50", "op": ">="}]}
        )
        assert out["passed"] is True
        assert "unmet" not in out

    def test_kpi_set_may_be_a_model_a_dict_or_none(self):
        ev = get_evaluator("its")
        payload = {"metrics": {"mAP50": 0.6}}
        assert ev.kpi_eval(None, payload, None)["passed"] is True
        assert (
            ev.kpi_eval(
                None, payload, {"kpi_target": [{"metric": "mAP50", "op": ">=", "threshold": 0.55}]}
            )["passed"]
            is True
        )
        assert ev.kpi_eval(None, payload, _cfg())["passed"] is True

    def test_candidate_may_be_a_payload_a_bare_dict_or_none(self):
        ev = get_evaluator("its")
        assert ev.kpi_eval(None, {"metrics": {"mAP50": 0.9}}, None)["metrics"] == {"mAP50": 0.9}
        assert ev.kpi_eval(None, {"mAP50": 0.9}, None)["metrics"] == {"mAP50": 0.9}
        assert ev.kpi_eval(None, None, None)["metrics"] == {}
        assert ev.kpi_eval(None, "not-a-payload", None)["metrics"] == {}

    def test_non_numeric_metrics_are_dropped(self):
        out = get_evaluator("its").kpi_eval(
            None, {"metrics": {"mAP50": 0.9, "note": "ok", "converged": True}}, None
        )
        # `converged: True` is dropped rather than read as 1.0: a bool is a
        # flag, and float(True) would be a number a constraint could compare.
        assert out["metrics"] == {"mAP50": 0.9}


class TestOfflineGateIsFailClosed:
    """Every way a constraint can fail to resolve is UNMET, and says which."""

    def test_missing_offline_metric_fails_closed_and_names_the_reason(self):
        # THE failure mode of the shipped config: `mAP50` is a guess at DEFT's
        # spelling. If it is wrong, this rejects every candidate forever while
        # every stage of every run reports success — so the rejection must not
        # read as "the model missed the bar".
        ev = get_evaluator("its")
        cfg = _cfg({"metric": "mAP50", "op": ">=", "threshold": 0.85})
        out = ev.kpi_eval(None, {"metrics": {"recall": 1.0}}, cfg)
        assert out["passed"] is False
        assert out["unmet"] == [
            f"mAP50 >= 0.85 (actual=None, reason={eval_mod._REASON_METRIC_NOT_REPORTED})"
        ]

    def test_an_unreported_metric_is_distinguishable_from_a_missed_bar(self):
        ev = get_evaluator("its")
        cfg = _cfg(
            {"metric": "mAP50", "op": ">=", "threshold": 0.85},  # reported, and lost
            {"metric": "AP50_person", "op": ">=", "threshold": 0.8},  # never reported
        )
        missed, unreported = ev.kpi_eval(None, {"metrics": {"mAP50": 0.62}}, cfg)["unmet"]
        # A real comparison against a real number renders bare — no reason.
        assert missed == "mAP50 >= 0.85 (actual=0.62)"
        assert "reason=" not in missed
        assert f"reason={eval_mod._REASON_METRIC_NOT_REPORTED}" in unreported

    def test_a_metric_reported_as_a_non_number_is_also_not_reported(self):
        out = get_evaluator("its").kpi_eval(
            None,
            {"metrics": {"mAP50": "0.91"}},
            _cfg({"metric": "mAP50", "op": ">=", "threshold": 0.85}),
        )
        assert out["passed"] is False
        assert f"reason={eval_mod._REASON_METRIC_NOT_REPORTED}" in out["unmet"][0]

    def test_unknown_dict_operator_is_unmet_not_a_crash(self):
        out = get_evaluator("its").kpi_eval(
            None,
            {"metrics": {"mAP50": 0.9}},
            {"kpi_target": [{"metric": "mAP50", "op": "~=", "threshold": 0.85}]},
        )
        assert out["passed"] is False
        # Its own reason, and the REAL value: the metric was found, there was
        # just no comparator for it. `actual=None` here would send the reader
        # hunting for a metric that was sitting right there.
        assert out["unmet"] == [
            f"mAP50 ~= 0.85 (actual=0.9, reason={eval_mod._REASON_UNKNOWN_OPERATOR})"
        ]

    def test_kpi_constraint_check_is_preferred_over_the_op_table(self):
        # Same precedence AOI's engine uses. Today every declared `op` is in
        # `_OPS` so the two agree, but a KpiConstraint subclass that overrides
        # `check` must not be silently answered by the op string it carries —
        # that is the two gates disagreeing about the same config.
        class _Contrary(KpiConstraint):
            def check(self, value: float) -> bool:  # type: ignore[override]
                return False

        cfg = EvalHarnessConfig(kpi_target=[_Contrary(metric="mAP50", op=">=", threshold=0.5)])
        # `0.9 >= 0.5` would pass via the op table; `check` says no and wins.
        out = get_evaluator("its").kpi_eval(None, {"metrics": {"mAP50": 0.9}}, cfg)
        assert out["passed"] is False
        assert out["unmet"] == ["mAP50 >= 0.5 (actual=0.9)"]

    def test_no_constraints_at_all_passes(self):
        # The offline gate is the only automatic judgement, but it judges what
        # the policy asked for — an empty kpi_target is not a rejection.
        assert get_evaluator("its").kpi_eval(None, {"metrics": {"mAP50": 0.1}}, _cfg())["passed"]


class TestOfflineMetricLookupIsCaseInsensitive:
    """The exact ITS metric spelling is unconfirmed against DEFT's output.

    A case mismatch between the config and what DEFT emits would otherwise be
    indistinguishable from an unemitted key, and would reject every candidate
    forever. Case folding removes one whole class of that failure — which is
    also why ``reason=metric-not-reported`` can be read as "the KEY is wrong",
    not "the case is wrong".
    """

    @pytest.mark.parametrize(
        ("configured", "reported"),
        [
            ("mAP50", "map50"),
            ("map50", "mAP50"),
            ("MAP50", "mAp50"),
            ("AP50_Car", "ap50_car"),
        ],
    )
    def test_a_case_difference_still_gates(self, configured, reported):
        out = get_evaluator("its").kpi_eval(
            None,
            {"metrics": {reported: 0.91}},
            _cfg({"metric": configured, "op": ">=", "threshold": 0.85}),
        )
        assert out["passed"] is True

    def test_a_case_folded_hit_still_loses_on_merit(self):
        # Folding must find the metric, not excuse it.
        out = get_evaluator("its").kpi_eval(
            None,
            {"metrics": {"map50": 0.42}},
            _cfg({"metric": "mAP50", "op": ">=", "threshold": 0.85}),
        )
        assert out["passed"] is False
        assert out["unmet"] == ["mAP50 >= 0.85 (actual=0.42)"]

    def test_the_exact_key_wins_over_a_case_folded_one(self):
        out = get_evaluator("its").kpi_eval(
            None,
            {"metrics": {"mAP50": 0.91, "map50": 0.10}},
            _cfg({"metric": "mAP50", "op": ">=", "threshold": 0.85}),
        )
        assert out["passed"] is True


# --------------------------------------------------------------------------- #
# collect
# --------------------------------------------------------------------------- #


class TestCollect:
    """``collect`` hands back the source's records verbatim.

    No per-record normalisation: the ITS record shape is not agreed, so
    flattening would be guessing, and a wrong guess is silent (an unrecognised
    key yields a zero count that reads downstream as a real measurement of zero).
    Nothing consumes these records today — ITS has no online gate — so the only
    contract worth pinning is that the evaluator neither invents evidence nor
    drops it.
    """

    def test_no_results_source_collects_nothing(self):
        assert DeftITSEvaluator().collect(None, "shadow") == []

    def test_a_callable_source_is_invoked_with_the_window_and_role(self):
        seen = []
        records = [{"observation_id": "obs-0", "tp": 9, "fp": 0, "fn": 1}]

        def source(window, role):
            seen.append((window, role))
            return records

        out = DeftITSEvaluator(results_source=source).collect({"model": "m"}, "shadow")
        assert seen == [({"model": "m"}, "shadow")]
        assert out == records

    def test_a_plain_iterable_source_is_used_as_is(self):
        records = [{"observation_id": "obs-0", "tp": 1, "fp": 0, "fn": 0}]
        assert DeftITSEvaluator(results_source=records).collect(None, "shadow") == records
