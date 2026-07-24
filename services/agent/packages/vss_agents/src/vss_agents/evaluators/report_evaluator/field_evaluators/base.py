# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from abc import ABC
from abc import abstractmethod
from collections.abc import Callable
import logging
from typing import Any

logger = logging.getLogger(__name__)

METRIC_REGISTRY: dict[str, type["EvaluationMetric"]] = {}


def register_metric(name: str) -> Callable[[type["EvaluationMetric"]], type["EvaluationMetric"]]:
    """
    Decorator to register an evaluation metric class.

    Args:
        name: Name of the metric (e.g., "f1", "llm_judge")

    Example:
        @register_metric("my_metric")
        class MyMetric(EvaluationMetric):
            async def evaluate(self, actual, reference, field_name=""):
                return 1.0
    """

    def decorator(cls: type["EvaluationMetric"]) -> type["EvaluationMetric"]:
        metric_name = name.lower()
        if metric_name in METRIC_REGISTRY:
            raise ValueError(
                f"Metric '{metric_name}' is already registered. "
                f"Cannot overwrite existing metric '{METRIC_REGISTRY[metric_name].__name__}' "
                f"with '{cls.__name__}'."
            )
        METRIC_REGISTRY[metric_name] = cls
        logger.debug(f"Registered evaluation metric: {name}")
        return cls

    return decorator


class EvaluationMetric(ABC):
    """Base interface for evaluation metrics."""

    @abstractmethod
    async def evaluate(self, actual: Any, reference: Any, field_name: str = "") -> float | None:
        """
        Evaluate actual value against reference.

        Args:
            actual: The actual generated value
            reference: The reference value
            field_name: Optional field name for context in logging/prompts

        Returns:
            Score between 0.0 and 1.0, or None if evaluation fails
        """
        pass
