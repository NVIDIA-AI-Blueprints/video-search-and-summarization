# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Fixed-sample, distribution-free median intervals at the trial level."""

from __future__ import annotations

import math
import statistics
from typing import Literal

from pydantic import Field

from .results import Evidence


class Distribution(Evidence):
    n: int = Field(ge=1, le=1000)
    median: float
    mean: float
    sample_stddev: float | None
    minimum: float
    maximum: float
    confidence: float = Field(gt=0, lt=1)
    interval: tuple[float, float] | None
    coverage: float | None
    method: Literal["binomial_median_order_statistics.v1"] = (
        "binomial_median_order_statistics.v1"
    )
    p95: float | None
    status: Literal["descriptive", "insufficient_samples"]


def describe(values: list[float], confidence: float = 0.95) -> Distribution:
    if not 1 <= len(values) <= 1000 or not 0 < confidence < 1:
        raise ValueError("invalid sample count or confidence")
    if any(type(v) not in {int, float} or not math.isfinite(v) for v in values):
        raise ValueError("samples must be finite numbers")
    ordered = sorted(values)
    n = len(values)
    # For zero-based rank j, coverage is P(j+1 <= Bin(n, .5) <= n-j-1).
    # Choose the narrowest non-interpolated interval meeting the requested
    # coverage. Ties make this conservative. No repeated peeking is supported.
    mass, choice, coverage = 0, None, None
    for rank in range((n + 1) // 2):
        mass += math.comb(n, rank)
        achieved = 1 - 2 * mass / 2**n
        if achieved >= confidence:
            choice, coverage = rank, achieved
        else:
            break
    return Distribution(
        n=n,
        median=statistics.median(ordered),
        mean=statistics.mean(ordered),
        sample_stddev=statistics.stdev(ordered) if n > 1 else None,
        minimum=ordered[0],
        maximum=ordered[-1],
        confidence=confidence,
        interval=(ordered[choice], ordered[n - 1 - choice])
        if choice is not None
        else None,
        coverage=coverage,
        p95=ordered[math.ceil(0.95 * n) - 1] if n >= 100 else None,
        status="descriptive" if choice is not None else "insufficient_samples",
    )
