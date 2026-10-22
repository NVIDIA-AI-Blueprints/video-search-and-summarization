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
"""Tests for the generic eval-harness wrappers (OfflineEval / OnlineEval).

These exercise the wrappers in isolation via a fake ``BaseEvaluator``. The KPI
*decision* (applying the structured ``kpi_target`` constraints + the baseline
check) is backend-owned and lives in the evaluator's ``kpi_eval`` — the
``OfflineEval`` wrapper is a thin delegate, so here we mostly assert it forwards
``(baseline, candidate, kpi_set)`` and returns the evaluator's verdict. The
constraint logic itself is tested against the DEFT evaluator in
``tests/unit/backends/test_deft_provider.py``.

``TestOfflineEvalUnmetSeam`` is the exception: it runs the REAL evaluator through
the wrapper because ``unmet`` is the one field that has to survive the join, and
every test on either side of it injects the value rather than crossing it.
"""

from typing import Any

import pytest

from src.backends.deft.eval import DeftITSEvaluator
from src.config.deft import EvalHarnessConfig, KpiConstraint
from src.lib.eval_harness import OfflineEval, OnlineEval
from src.lib.eval_harness.base import BaseEvaluator


class FakeEvaluator(BaseEvaluator):
    """Minimal injectable evaluator with scripted return values."""

    def __init__(
        self,
        *,
        kpi_result: dict | None = None,
        online_result: dict | None = None,
        sample: list[dict] | None = None,
    ):
        self._kpi_result = kpi_result if kpi_result is not None else {"metrics": {}, "passed": True}
        self._online_result = online_result or {"metrics": {}, "verdict": "hold"}
        self._sample = sample if sample is not None else []
        self.collect_calls: list[tuple[Any, str]] = []
        self.kpi_calls: list[tuple[Any, Any, Any]] = []

    def collect(self, window: Any, role: str) -> list[dict]:
        self.collect_calls.append((window, role))
        return self._sample

    def evaluate(self, baseline: Any, candidate: Any, sample: Any) -> dict:
        return self._online_result

    def kpi_eval(self, baseline: Any, candidate: Any, kpi_set: Any) -> dict:
        self.kpi_calls.append((baseline, candidate, kpi_set))
        return self._kpi_result


# --- OfflineEval (thin delegate) ----------------------------------------------


class TestOfflineEval:
    def test_delegates_to_evaluator_kpi_eval(self):
        # The wrapper forwards args verbatim and returns the evaluator's verdict.
        ev = FakeEvaluator(kpi_result={"metrics": {"mAP50": 0.8}, "passed": True})
        kpi_set = object()
        out = OfflineEval(ev).run(baseline="base", candidate="cand", kpi_set=kpi_set)
        assert out == {"metrics": {"mAP50": 0.8}, "passed": True}
        assert ev.kpi_calls == [("base", "cand", kpi_set)]

    def test_evaluator_fail_propagates(self):
        # The backend evaluator owns the decision; a failed gate propagates.
        ev = FakeEvaluator(kpi_result={"metrics": {"mAP50": 0.95}, "passed": False})
        out = OfflineEval(ev).run(None, None, kpi_set=None)
        assert out["passed"] is False

    def test_normalizes_missing_keys(self):
        # Missing metrics/passed are normalized to {}/False (fail-closed).
        ev = FakeEvaluator(kpi_result={})
        out = OfflineEval(ev).run(None, None, kpi_set=None)
        assert out == {"metrics": {}, "passed": False}

    @pytest.mark.parametrize("passed", [True, False])
    def test_passed_is_coerced_to_bool(self, passed):
        ev = FakeEvaluator(kpi_result={"metrics": {}, "passed": passed})
        out = OfflineEval(ev).run(None, None, kpi_set=None)
        assert out["passed"] is passed

    def test_unmet_survives_the_wrapper(self):
        # The gate handler reads `unmet` off this dict to say WHICH constraint
        # missed. Rebuilding the dict from metrics+passed alone stranded it here,
        # so `deft.gate.unmet` was always empty and the abort message always
        # rendered its "no constraint detail reported" fallback.
        detail = ["macro_ap >= 0.55 (actual=None, reason=metric-not-reported)"]
        ev = FakeEvaluator(
            kpi_result={"metrics": {"macro_ap": None}, "passed": False, "unmet": detail}
        )
        out = OfflineEval(ev).run(None, None, kpi_set=None)
        assert out["unmet"] == detail

    def test_unmet_absent_when_gate_passes(self):
        # Absent, NOT []. The `if result.get("unmet")` guard is deliberate: the
        # evaluator omits the key on a pass, and the wrapper keeps that shape so
        # a passing gate carries no empty detail field.
        ev = FakeEvaluator(kpi_result={"metrics": {"macro_ap": 0.9}, "passed": True})
        out = OfflineEval(ev).run(None, None, kpi_set=None)
        assert "unmet" not in out

    def test_empty_unmet_is_not_forwarded(self):
        # Same guard from the other side: an evaluator that reports `unmet: []`
        # must not put an empty key on the wrapper's output either.
        ev = FakeEvaluator(kpi_result={"metrics": {}, "passed": True, "unmet": []})
        out = OfflineEval(ev).run(None, None, kpi_set=None)
        assert "unmet" not in out


# --- OfflineEval x real evaluator (the seam `unmet` has to cross) ---------------


class TestOfflineEvalUnmetSeam:
    """`kpi_eval` -> `OfflineEval.run` -> what the gate handler reads.

    The wrapper tests above inject `unmet`, so they would still pass if the
    evaluator stopped producing it. These build the string with the real
    evaluator and assert the exact value the handler sees, which is the only
    assertion that fails if EITHER end of the join regresses.
    """

    def test_unreported_metric_reaches_the_handler_with_its_reason(self):
        # The shipped_gate case: `its_active_learning` has no `evaluate` stage,
        # so macro_ap is never reported and the gate must fail CLOSED *and say
        # why* — reason=metric-not-reported is what separates a missing metric
        # from a genuine miss, and it is the marker that never reached anyone.
        kpi_set = EvalHarnessConfig(
            kpi_target=[KpiConstraint(metric="macro_ap", op=">=", threshold=0.55)]
        )
        out = OfflineEval(DeftITSEvaluator()).run(
            baseline=None, candidate={"metrics": {}}, kpi_set=kpi_set
        )

        assert out["passed"] is False
        # `result.get("unmet") or []`, as handlers.py reads it.
        assert (out.get("unmet") or []) == [
            "macro_ap >= 0.55 (actual=None, reason=metric-not-reported)"
        ]

    def test_missed_threshold_reaches_the_handler_with_the_actual_value(self):
        # A genuine miss carries no reason= marker, just the value that missed.
        kpi_set = EvalHarnessConfig(
            kpi_target=[KpiConstraint(metric="macro_ap", op=">=", threshold=0.55)]
        )
        out = OfflineEval(DeftITSEvaluator()).run(
            baseline=None, candidate={"metrics": {"macro_ap": 0.4}}, kpi_set=kpi_set
        )

        assert out["passed"] is False
        assert (out.get("unmet") or []) == ["macro_ap >= 0.55 (actual=0.4)"]

    def test_satisfied_constraint_leaves_no_detail(self):
        kpi_set = EvalHarnessConfig(
            kpi_target=[KpiConstraint(metric="macro_ap", op=">=", threshold=0.55)]
        )
        out = OfflineEval(DeftITSEvaluator()).run(
            baseline=None, candidate={"metrics": {"macro_ap": 0.9}}, kpi_set=kpi_set
        )

        assert out["passed"] is True
        assert "unmet" not in out


# --- OnlineEval ----------------------------------------------------------------


class TestOnlineEval:
    def test_collect_then_evaluate_flow(self):
        ev = FakeEvaluator(
            online_result={"metrics": {"recall": 1.0}, "verdict": "advance"},
            sample=[{"x": 1}],
        )
        out = OnlineEval(ev).evaluate(None, None, window="w", role="shadow")
        assert out == {"metrics": {"recall": 1.0}, "verdict": "advance"}
        assert ev.collect_calls == [("w", "shadow")]

    @pytest.mark.parametrize("verdict", ["advance", "hold", "rollback"])
    def test_valid_verdicts_pass_through(self, verdict):
        ev = FakeEvaluator(online_result={"metrics": {}, "verdict": verdict})
        out = OnlineEval(ev).evaluate(None, None, "w", "canary")
        assert out["verdict"] == verdict

    def test_unknown_verdict_defaults_to_hold(self):
        ev = FakeEvaluator(online_result={"metrics": {}, "verdict": "promote-now"})
        out = OnlineEval(ev).evaluate(None, None, "w", "canary")
        assert out["verdict"] == "hold"

    def test_missing_verdict_defaults_to_hold(self):
        ev = FakeEvaluator(online_result={"metrics": {"n": 3}})
        out = OnlineEval(ev).evaluate(None, None, "w", "canary")
        assert out["verdict"] == "hold"
        assert out["metrics"] == {"n": 3}

    def test_verdict_case_insensitive(self):
        ev = FakeEvaluator(online_result={"metrics": {}, "verdict": "ADVANCE"})
        out = OnlineEval(ev).evaluate(None, None, "w", "canary")
        assert out["verdict"] == "advance"
