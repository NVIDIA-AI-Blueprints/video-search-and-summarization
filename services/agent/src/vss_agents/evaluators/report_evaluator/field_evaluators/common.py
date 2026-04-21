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

import logging
import re

from .base import EvaluationMetric
from .base import register_metric

logger = logging.getLogger(__name__)


def tokenize_text(text: str) -> list[str]:
    """Tokenize text into lowercase words."""
    tokens = re.findall(r"\b\w+\b", text.lower())
    return tokens


def calculate_f1_score(pred_tokens: list[str], ref_tokens: list[str]) -> float:
    """Calculate F1 score between predicted and reference tokens."""
    if not pred_tokens and not ref_tokens:
        return 1.0
    if not pred_tokens or not ref_tokens:
        return 0.0

    pred_set = set(pred_tokens)
    ref_set = set(ref_tokens)

    intersection = pred_set & ref_set

    if not intersection:
        return 0.0

    precision = len(intersection) / len(pred_set) if pred_set else 0.0
    recall = len(intersection) / len(ref_set) if ref_set else 0.0

    if precision + recall == 0:
        return 0.0

    f1 = 2 * (precision * recall) / (precision + recall)
    return f1


@register_metric("non_empty")
class NonEmptyMetric(EvaluationMetric):
    """Accept any non-empty value."""

    async def evaluate(self, actual: str, reference: str, field_name: str = "") -> float:  # noqa: ARG002
        return 1.0 if actual and actual.strip() else 0.0


@register_metric("f1")
class F1Metric(EvaluationMetric):
    """Evaluate using F1 score on tokens."""

    async def evaluate(self, actual: str, reference: str, field_name: str = "") -> float:
        logger.debug(f"Evaluating F1 metric for field {field_name} with actual: {actual} and reference: {reference}")
        pred_tokens = tokenize_text(actual)
        ref_tokens = tokenize_text(reference)
        return calculate_f1_score(pred_tokens, ref_tokens)


@register_metric("exact_match")
class ExactMatchMetric(EvaluationMetric):
    """Evaluate using exact string match with normalized whitespace."""

    async def evaluate(self, actual: str, reference: str, field_name: str = "") -> float:
        logger.debug(
            f"Evaluating exact match metric for field {field_name} with actual: {actual} and reference: {reference}"
        )
        actual_normalized = re.sub(r"\s+", " ", actual.strip())
        reference_normalized = re.sub(r"\s+", " ", reference.strip())
        return 1.0 if actual_normalized == reference_normalized else 0.0


@register_metric("regex")
class RegexMetric(EvaluationMetric):
    """Evaluate if actual matches the reference regex pattern."""

    async def evaluate(self, actual: str, reference: str, field_name: str = "") -> float:
        logger.debug(f"Evaluating regex metric for field {field_name} with actual: {actual} and reference: {reference}")
        try:
            return 1.0 if re.fullmatch(reference, actual) else 0.0
        except re.error as e:
            logger.warning(f"Invalid regex pattern '{reference}': {e}")
            return 0.0
