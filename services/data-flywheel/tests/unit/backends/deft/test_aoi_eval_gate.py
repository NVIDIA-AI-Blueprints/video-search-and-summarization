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
"""Regression lock for the AOI offline KPI gate and the constraint engine under it.

``kpi_eval`` and its helpers (``_unmet_constraints`` / ``_kpi_constraints`` /
``_constraint_fields`` / ``_lookup_metric`` / ``_extract_deft_metrics``) live on
``DeftAOIEvaluator``, NOT on the shared ``DeftEvaluator`` base. That placement is
deliberate: the base carries only construction and the neutral ``EvalBackend``
shims, and each use case owns the code that decides its own promotions, so one
edit cannot silently change another use case's verdicts. (ITS has its own,
separate offline gate — see ``test_its_eval.py``.)

This file exists because that code decides whether a candidate promotes: a typo
in it does not crash, it promotes a bad model. Every assertion here pins AOI
behaviour that must not change when a new use case is added — if adding one
requires editing this file, the new use case reached into AOI's gate.
"""

from __future__ import annotations

import pytest

from src.backends.deft import eval as eval_mod
from src.backends.deft.eval import (
    DeftAOIEvaluator,
    DeftEvaluator,
)
from src.config import EvalHarnessConfig
from src.config.deft import KpiConstraint


class TestEngineOwnershipIsPerUseCase:
    """Placement: the gate belongs to the use case, not to the shared base."""

    @pytest.mark.parametrize(
        "name",
        [
            "kpi_eval",
            "_extract_deft_metrics",
            "_unmet_constraints",
            "_kpi_constraints",
            "_constraint_fields",
            "_lookup_metric",
            "_online_metrics",
            "_online_constraints",
        ],
    )
    def test_scoring_member_is_owned_by_the_aoi_evaluator(self, name):
        # Nothing that decides a verdict may sit on the base: a member up there
        # is inherited by every use case, so one edit moves two gates.
        assert name in vars(DeftAOIEvaluator)
        assert name not in vars(DeftEvaluator)

    def test_only_construction_and_the_neutral_seam_live_on_the_base(self):
        # The base's whole job: build the evaluator and satisfy EvalBackend.
        # An "only" claim needs an EQUALITY. This was an intersection
        # (`set(vars(...)) & names == names`), which is merely a subset check —
        # it passed no matter what else was on the base, including a hoisted
        # kpi_eval, which is the exact regression the class is here to catch.
        assert "__init__" in vars(DeftEvaluator)
        assert {name for name in vars(DeftEvaluator) if not name.startswith("_")} == {
            "DEFAULT_THRESHOLDS",
            "DEFAULT_MIN_SAMPLE_SIZE",
            "get_status",
            "get_results",
            "cancel",
        }

    @pytest.mark.parametrize("name", ["collect", "evaluate", "kpi_eval"])
    def test_the_base_leaves_the_scoring_verbs_abstract(self, name):
        # BaseEvaluator declares them; DeftEvaluator deliberately does not
        # answer them, so a subclass that forgets one fails loudly.
        assert name in DeftEvaluator.__abstractmethods__


class TestConstructionDefaults:
    """The AOI construction defaults must be bit-for-bit what they were."""

    def test_no_arg_aoi_evaluator_keeps_its_defaults(self):
        ev = DeftAOIEvaluator()
        assert ev.min_sample_size == 100
        assert ev.thresholds == {}
        assert ev.online_target == []
        assert ev._results_source is None

    def test_module_level_aliases_still_import_and_match(self):
        # Kept so nothing importing the old private names breaks.
        assert eval_mod._DEFAULT_MIN_SAMPLE_SIZE == 100
        assert eval_mod._DEFAULT_AOI_THRESHOLDS == {}
        assert eval_mod._DEFAULT_MIN_SAMPLE_SIZE == DeftEvaluator.DEFAULT_MIN_SAMPLE_SIZE
        assert eval_mod._DEFAULT_AOI_THRESHOLDS is DeftEvaluator.DEFAULT_THRESHOLDS

    def test_explicit_args_still_win_over_the_classvars(self):
        ev = DeftAOIEvaluator(min_sample_size=7, thresholds={"far": 0.5})
        assert ev.min_sample_size == 7
        assert ev.thresholds == {"far": 0.5}

    def test_min_sample_size_zero_is_honoured_not_defaulted(self):
        # `is None` (not truthiness) selects the default — 0 is a real value.
        assert DeftAOIEvaluator(min_sample_size=0).min_sample_size == 0

    def test_thresholds_do_not_mutate_the_shared_classvar(self):
        # The ClassVar is now shared by every subclass instance; a per-instance
        # override must copy, or one evaluator would gate the next. Mutate the
        # DEFAULTED instance — that is the only branch that can alias the
        # ClassVar. Mutating an explicitly-passed dict (as this used to) cannot
        # reach it, so the old spelling passed even with the copy removed.
        DeftAOIEvaluator().thresholds["far"] = 0.9
        assert DeftEvaluator.DEFAULT_THRESHOLDS == {}
        assert DeftAOIEvaluator().thresholds == {}
        # The caller's dict is copied too, not adopted.
        passed = {"far": 0.5}
        DeftAOIEvaluator(thresholds=passed).thresholds["far"] = 0.9
        assert passed == {"far": 0.5}


class TestConstraintEngine:
    """Every documented offline behaviour of the AOI gate."""

    def test_case_insensitive_metric_lookup(self):
        cfg = EvalHarnessConfig(kpi_target=[{"metric": "FAR", "op": "<", "threshold": 0.001}])
        assert DeftAOIEvaluator().kpi_eval(None, {"metrics": {"far": 0.0005}}, cfg)["passed"]

    def test_lookup_prefers_the_exact_key(self):
        # Exact hit short-circuits the case-folded scan.
        assert DeftAOIEvaluator._lookup_metric({"FAR": 1.0, "far": 2.0}, "FAR") == 1.0

    def test_lookup_returns_none_for_absent_and_non_numeric(self):
        assert DeftAOIEvaluator._lookup_metric({"far": "0.5"}, "far") is None
        assert DeftAOIEvaluator._lookup_metric({}, "far") is None

    def test_absent_metric_is_unmet_fail_closed(self):
        cfg = EvalHarnessConfig(kpi_target=[{"metric": "mAP50", "op": ">=", "threshold": 0.9}])
        out = DeftAOIEvaluator().kpi_eval(None, {"metrics": {"far": 0.0005}}, cfg)
        assert out["passed"] is False
        assert out["unmet"] == ["mAP50 >= 0.9 (actual=None)"]

    def test_unknown_dict_operator_is_unmet_not_a_crash(self):
        out = DeftAOIEvaluator().kpi_eval(
            None,
            {"metrics": {"recall": 1.0}},
            {"kpi_target": [{"metric": "recall", "op": "~=", "threshold": 1.0}]},
        )
        assert out["passed"] is False
        assert out["unmet"] == ["recall ~= 1.0 (actual=1.0)"]

    def test_the_unmet_string_carries_no_its_style_reason_marker(self):
        """AOI's rendered ``unmet`` is byte-identical to what it was pre-ITS.

        The ITS gate appends ``, reason=<why>`` when a constraint could not be
        evaluated. Adding it to AOI would have changed AOI-visible output — the
        string an operator greps and a promotion log records — for a use case
        this MR does not otherwise touch, so AOI keeps the bare rendering and the
        markers stay on ITS's own gate (``test_its_eval.py``).
        """
        ev = DeftAOIEvaluator()
        cfg = EvalHarnessConfig(
            kpi_target=[
                {"metric": "far", "op": "<", "threshold": 0.001},  # reported, and lost
                {"metric": "mAP50", "op": ">=", "threshold": 0.9},  # never reported
            ]
        )
        assert ev.kpi_eval(None, {"metrics": {"far": 0.5}}, cfg)["unmet"] == [
            "far < 0.001 (actual=0.5)",
            "mAP50 >= 0.9 (actual=None)",
        ]
        assert eval_mod._REASON_METRIC_NOT_REPORTED not in "".join(
            ev.kpi_eval(None, {"metrics": {"far": 0.5}}, cfg)["unmet"]
        )

    def test_kpi_constraint_check_is_preferred_over_the_op_table(self):
        # The validated model carries its own comparator; the dict table is only
        # the defensive fallback. Pin the preference with a constraint whose
        # `check` deliberately disagrees with its own op string.
        class _Contrary(KpiConstraint):
            def check(self, value: float) -> bool:  # type: ignore[override]
                return False

        cfg = EvalHarnessConfig(kpi_target=[_Contrary(metric="far", op="<", threshold=1.0)])
        # `far=0.0 < 1.0` would pass via the op table; `check` says no and wins.
        assert DeftAOIEvaluator().kpi_eval(None, {"metrics": {"far": 0.0}}, cfg)["passed"] is False

    def test_partial_constraint_is_skipped_not_counted(self):
        # KNOWN FAIL-OPEN, pinned deliberately to main's behaviour. A constraint
        # missing metric/op/threshold is SKIPPED, so a kpi_target whose only row
        # is malformed passes having evaluated nothing. Fixing it changes AOI
        # promotion behaviour, so it is out of scope here. This asserts what
        # ships, NOT what is correct; flip it when the fail-closed branch lands.
        out = DeftAOIEvaluator().kpi_eval(
            None, {"metrics": {"far": 0.5}}, {"kpi_target": [{"metric": "far", "op": "<"}]}
        )
        assert out["passed"] is True
        assert "unmet" not in out

    def test_kpi_set_may_be_a_model_a_dict_or_none(self):
        ev = DeftAOIEvaluator()
        assert ev.kpi_eval(None, {"metrics": {"recall": 1.0}}, None)["passed"] is True
        assert (
            ev.kpi_eval(
                None,
                {"metrics": {"recall": 1.0}},
                {"kpi_target": [{"metric": "recall", "op": ">=", "threshold": 1.0}]},
            )["passed"]
            is True
        )

    def test_candidate_may_be_a_payload_a_bare_dict_or_none(self):
        ev = DeftAOIEvaluator()
        assert ev.kpi_eval(None, {"metrics": {"mAP50": 0.9}}, None)["metrics"] == {"mAP50": 0.9}
        assert ev.kpi_eval(None, {"mAP50": 0.9}, None)["metrics"] == {"mAP50": 0.9}
        assert ev.kpi_eval(None, None, None)["metrics"] == {}
        assert ev.kpi_eval(None, "not-a-payload", None)["metrics"] == {}

    def test_non_numeric_metrics_are_dropped(self):
        out = DeftAOIEvaluator().kpi_eval(None, {"metrics": {"far": 0.1, "note": "ok"}}, None)
        assert out["metrics"] == {"far": 0.1}

    def test_baseline_is_ignored(self):
        cfg = EvalHarnessConfig(kpi_target=[{"metric": "far", "op": "<", "threshold": 0.001}])
        ev = DeftAOIEvaluator()
        strong_baseline = {"metrics": {"far": 0.0}}
        candidate = {"metrics": {"far": 0.0005}}
        assert (
            ev.kpi_eval(strong_baseline, candidate, cfg)
            == ev.kpi_eval(None, candidate, cfg)
            == {"metrics": {"far": 0.0005}, "passed": True}
        )


class TestAOIOnlineGate:
    """The AOI ONLINE path runs the same constraint engine and stays FAR-only."""

    @staticmethod
    def _samples(n_good: int, n_flagged_good: int) -> list[dict]:
        out = [
            {"ai_classification": "Good", "confirmed_label": "Good"}
            for _ in range(n_good - n_flagged_good)
        ]
        out += [
            {"ai_classification": "NotGood", "confirmed_label": "Good"}
            for _ in range(n_flagged_good)
        ]
        return out

    def test_far_gate_still_advances_and_rolls_back(self):
        ev = DeftAOIEvaluator(min_sample_size=10)
        ev.online_target = [KpiConstraint(metric="FAR_pct", op="<", threshold=5.0)]
        assert ev.evaluate(None, None, self._samples(100, 0))["verdict"] == "advance"
        assert ev.evaluate(None, None, self._samples(100, 20))["verdict"] == "rollback"

    def test_legacy_thresholds_far_fallback_still_gates(self):
        # AOI keeps its legacy escape hatch (fractions scaled to a percentage).
        ev = DeftAOIEvaluator(min_sample_size=10, thresholds={"false_accept_rate": 0.001})
        assert ev._online_constraints() == [
            {"metric": "FAR_pct", "op": "<", "threshold": pytest.approx(0.1)}
        ]
        assert ev.evaluate(None, None, self._samples(100, 20))["verdict"] == "rollback"
