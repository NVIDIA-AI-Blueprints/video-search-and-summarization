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

"""Offline (pre-promotion) KPI gate wrapper around an evaluator's ``kpi_eval``.

The wrapper is a **thin delegate**: it hands ``(baseline, candidate, kpi_set)``
to the backend-owned evaluator and returns its ``{"metrics", "passed"}`` verdict
unchanged. The *decision* — computing the candidate metrics and checking them
against the KPI acceptance criteria (``kpi_set.kpi_target``, a structured list of
``metric/op/threshold`` constraints) plus the not-worse-than-baseline rule — is
owned by the evaluator (``src/backends/deft/eval.py``). The harness deliberately
knows nothing about metric names or comparators; the KPI target is a validated,
deterministic structure (see ``src.config.KpiConstraint``), so there is no
expression parsing to do here.
"""

from typing import Any

from src.lib.eval_harness.base import BaseEvaluator
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.eval_harness.offline")


class OfflineEval:
    """Pre-promotion gate: decides whether a candidate may enter shadow."""

    def __init__(self, evaluator: BaseEvaluator):
        self.evaluator = evaluator

    def run(self, baseline: Any, candidate: Any, kpi_set: Any) -> dict:
        """Run the offline KPI gate; returns ``{"metrics": ..., "passed": bool}``.

        Delegates entirely to the backend-owned ``evaluator.kpi_eval``, which
        computes the candidate metrics and applies both the KPI acceptance
        criteria carried on ``kpi_set`` and the not-worse-than-baseline check.
        """
        result = self.evaluator.kpi_eval(baseline, candidate, kpi_set)
        logger.debug("Offline gate result: passed=%s", result.get("passed"))
        out: dict = {
            "metrics": result.get("metrics", {}) or {},
            "passed": bool(result.get("passed", False)),
        }
        # `unmet` names WHICH constraint failed and why — including the
        # reason=metric-not-reported marker that separates a fail-closed gate
        # from a genuine miss. Rebuilding the dict without it stranded the
        # detail here: the job surface and the error message both read it.
        if result.get("unmet"):
            out["unmet"] = result["unmet"]
        return out
