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

"""Eval Harness base contract.

Defines the evaluator interface used by the offline (pre-promotion KPI gate)
and online (shadow/canary verdict) wrappers. Skeleton only: no metric logic.
"""

from abc import ABC, abstractmethod
from typing import Any, Literal, TypedDict

# Online verdict returned by an evaluator: advance the rollout, hold (insufficient
# data / within noise), or rollback (regression vs baseline).
Verdict = Literal["advance", "hold", "rollback"]


class OnlineEvalResult(TypedDict):
    """Structured result of an online (shadow/canary) evaluation."""

    metrics: dict[str, Any]
    verdict: Verdict


class OfflineEvalResult(TypedDict):
    """Structured result of an offline (pre-promotion KPI) evaluation."""

    metrics: dict[str, Any]
    passed: bool


class BaseEvaluator(ABC):
    """Abstract evaluator for a use case (e.g. AOI / Visual-ChangeNet)."""

    @abstractmethod
    def collect(self, window: Any, role: str) -> list[dict]:
        """Collect inference results for a time ``window`` and rollout ``role``."""
        ...

    @abstractmethod
    def evaluate(self, baseline: Any, candidate: Any, sample: Any) -> OnlineEvalResult:
        """Compute online metrics + verdict (``{"metrics": {...}, "verdict": <Verdict>}``)."""
        ...

    @abstractmethod
    def kpi_eval(self, baseline: Any, candidate: Any, kpi_set: Any) -> OfflineEvalResult:
        """Run the offline pre-promotion KPI gate (``{"metrics": {...}, "passed": <bool>}``)."""
        ...
