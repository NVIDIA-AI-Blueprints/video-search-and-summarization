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

"""Online (shadow/canary) evaluation wrapper that drives the rollout verdict."""

from typing import Any, get_args

from src.lib.eval_harness.base import BaseEvaluator, OnlineEvalResult, Verdict
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.eval_harness.online")

_VALID_VERDICTS = frozenset(get_args(Verdict))


class OnlineEval:
    """Reads shadow/canary inference results for a window/role and evaluates."""

    def __init__(self, evaluator: BaseEvaluator):
        self.evaluator = evaluator

    def evaluate(self, baseline: Any, candidate: Any, window: Any, role: str) -> OnlineEvalResult:
        """Collect + evaluate online; returns ``{"metrics": ..., "verdict": ...}``.

        Metric-agnostic: delegates to the injected evaluator to collect samples
        for the ``window``/``role`` and compute the verdict, then validates the
        verdict (defaulting unknown/missing to ``"hold"``).
        """
        sample = self.evaluator.collect(window, role)
        result = self.evaluator.evaluate(baseline, candidate, sample)
        raw_verdict = str(result.get("verdict", "hold")).lower()
        if raw_verdict not in _VALID_VERDICTS:
            logger.warning("Unknown verdict %r; defaulting to 'hold'", raw_verdict)
            raw_verdict = "hold"
        verdict: Verdict = raw_verdict
        return {"metrics": result.get("metrics", {}), "verdict": verdict}
