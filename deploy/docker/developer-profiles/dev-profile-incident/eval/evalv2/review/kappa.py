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

"""Agreement between two raters on the same items: Cohen's κ and weighted κ for ordered labels.

Used for two reviewers on the human-verified subsets, and for a person against an AI pass (the judge or the
validator). Both raters are mappings from item id to label and are aligned on the ids they share; ids only one
rater labelled are counted, not scored. κ is ``None`` when chance agreement is total (every label the same), since
it is undefined there.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Mapping, Sequence
from typing import Any


def _aligned(a: Mapping[str, Hashable], b: Mapping[str, Hashable]) -> tuple[list[str], dict[str, int]]:
    shared = sorted(set(a) & set(b))
    return shared, {"only_in_a": len(set(a) - set(b)), "only_in_b": len(set(b) - set(a))}


def cohen_kappa(a: Mapping[str, Hashable], b: Mapping[str, Hashable]) -> dict[str, Any]:
    """Unweighted κ over the shared ids: ``(p_o - p_e) / (1 - p_e)``."""
    shared, unmatched = _aligned(a, b)
    n = len(shared)
    if n == 0:
        return {"kappa": None, "n": 0, "observed": None, "expected": None, **unmatched}
    observed = sum(1 for i in shared if a[i] == b[i]) / n
    count_a, count_b = Counter(a[i] for i in shared), Counter(b[i] for i in shared)
    expected = sum(count_a[label] * count_b[label] for label in set(count_a) | set(count_b)) / (n * n)
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    return {"kappa": kappa, "n": n, "observed": observed, "expected": expected,
            "labels": sorted(map(str, set(count_a) | set(count_b))), **unmatched}


def weighted_kappa(a: Mapping[str, Hashable], b: Mapping[str, Hashable], *, categories: Sequence[Hashable],
                   weights: str = "quadratic") -> dict[str, Any]:
    """Weighted κ for ordered ``categories`` (for example severity 1-5): ``1 - Σ w·O / Σ w·E``.

    ``weights`` is ``linear`` (``|i-j| / (k-1)``) or ``quadratic`` (its square). A label outside ``categories`` is
    an error, not silently dropped.
    """
    if weights not in ("linear", "quadratic"):
        raise ValueError(f"weights must be 'linear' or 'quadratic', got {weights!r}")
    position = {label: index for index, label in enumerate(categories)}
    shared, unmatched = _aligned(a, b)
    unknown = sorted({str(x) for i in shared for x in (a[i], b[i]) if x not in position})
    if unknown:
        raise ValueError(f"labels outside the ordered categories: {unknown}")
    n, k = len(shared), len(categories)
    if n == 0 or k < 2:
        return {"kappa": None, "n": n, "weights": weights, **unmatched}
    pairs = Counter((position[a[i]], position[b[i]]) for i in shared)
    rows, cols = Counter(position[a[i]] for i in shared), Counter(position[b[i]] for i in shared)
    power = 1 if weights == "linear" else 2

    def weight(i: int, j: int) -> float:
        return float((abs(i - j) / (k - 1)) ** power)

    observed = sum(weight(i, j) * count for (i, j), count in pairs.items()) / n
    expected = sum(weight(i, j) * rows[i] * cols[j] for i in range(k) for j in range(k)) / (n * n)
    kappa = None if expected == 0 else 1 - observed / expected
    return {"kappa": kappa, "n": n, "weights": weights, "observed_disagreement": observed,
            "expected_disagreement": expected, **unmatched}
