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

"""Timing collection around ``TimeMeasure``."""

from __future__ import annotations

import asyncio
import time

import pytest

from vss_core.search_core._internal.time_measure import TimeMeasure
from vss_core.search_core._internal.time_measure import collect_timings
from vss_core.search_core.models.search import SearchOutput
from vss_core.search_core.models.search import SearchTimings


def test_no_collector_means_no_overhead_and_no_error() -> None:
    """The default path must be unchanged: measure, log, collect nothing."""
    with TimeMeasure("standalone block"):
        pass  # must not raise with no collector open


def test_collector_records_measured_blocks() -> None:
    with collect_timings() as stages:
        with TimeMeasure("embed_search: ES search execution"):
            pass
        with TimeMeasure("embed_search: build ES query"):
            pass

    assert set(stages) == {"embed_search: ES search execution", "embed_search: build ES query"}
    assert all(v["total_s"] >= 0.0 for v in stages.values())
    assert all(v["calls"] == 1 for v in stages.values())


def test_repeated_labels_accumulate() -> None:
    """Some blocks run per hit; the useful number is what the stage cost in total."""
    with collect_timings() as stages:
        for _ in range(3):
            with TimeMeasure("attribute_search: frame lookups"):
                pass

    assert len(stages) == 1
    assert stages["attribute_search: frame lookups"]["calls"] == 3


def test_collector_does_not_leak_out_of_its_context() -> None:
    with collect_timings() as inner:
        with TimeMeasure("inside"):
            pass
    with TimeMeasure("outside"):
        pass

    assert "inside" in inner
    assert "outside" not in inner


def test_nested_collectors_are_isolated() -> None:
    with collect_timings() as outer:
        with TimeMeasure("outer block"):
            pass
        with collect_timings() as inner:
            with TimeMeasure("inner block"):
                pass
        with TimeMeasure("outer block again"):
            pass

    assert set(inner) == {"inner block"}
    assert set(outer) == {"outer block", "outer block again"}


def test_concurrent_tasks_share_one_collector() -> None:
    """asyncio copies the context but not the dict, so parallel legs accumulate.

    Fusion runs its embedding and attribute legs concurrently; both must land
    in the same collector or the breakdown silently loses one of them.
    """

    async def leg(label: str) -> None:
        with TimeMeasure(label):
            await asyncio.sleep(0)

    async def main() -> dict[str, float]:
        with collect_timings() as stages:
            await asyncio.gather(leg("leg a"), leg("leg b"))
        return stages

    stages = asyncio.run(main())
    assert set(stages) == {"leg a", "leg b"}


def test_search_output_timings_default_to_absent() -> None:
    """Absent means nobody collected -- not that the search took no time."""
    assert SearchOutput().timings is None


def test_search_output_accepts_timings() -> None:
    out = SearchOutput(timings=SearchTimings(stages={"a": {"total_s": 1.5, "self_s": 1.5, "calls": 1}}, total_s=2.0))
    dumped = out.model_dump()
    assert dumped["timings"]["stages"]["a"]["total_s"] == 1.5
    assert dumped["timings"]["total_s"] == 2.0


def test_self_time_excludes_nested_blocks() -> None:
    """The point of self_s: a parent must not be charged for its children.

    Without this, a flat table double counts -- "search: embed search" and the
    "embed_search: ..." stages it wraps look like separate costs.
    """
    with collect_timings() as stages:
        with TimeMeasure("outer"):
            with TimeMeasure("inner"):
                time.sleep(0.02)

    outer, inner = stages["outer"], stages["inner"]
    assert outer["total_s"] >= inner["total_s"]
    # Outer's own work was negligible, so nearly all its time was the child's.
    assert outer["self_s"] < inner["total_s"]
    assert inner["self_s"] == inner["total_s"]
    assert outer["concurrent_children"] == 0.0


def test_self_times_sum_to_the_real_elapsed_work() -> None:
    """Self times are additive; inclusive totals are not."""
    with collect_timings() as stages:
        with TimeMeasure("parent"):
            with TimeMeasure("child a"):
                time.sleep(0.01)
            with TimeMeasure("child b"):
                time.sleep(0.01)

    self_sum = sum(v["self_s"] for v in stages.values())
    assert self_sum == pytest.approx(stages["parent"]["total_s"], abs=0.01)


def test_overlapping_children_are_flagged_not_negative() -> None:
    """Fusion runs its legs under asyncio.gather, so children can outlast the parent.

    Subtracting them would drive self time negative; report 0.0 and say why.
    """

    async def leg() -> None:
        with TimeMeasure("child"):
            await asyncio.sleep(0.05)

    async def main() -> dict[str, dict[str, float]]:
        with collect_timings() as stages:
            with TimeMeasure("parent"):
                await asyncio.gather(leg(), leg(), leg())
        return stages

    stages = asyncio.run(main())
    parent = stages["parent"]
    assert stages["child"]["calls"] == 3
    assert stages["child"]["total_s"] > parent["total_s"]  # overlapped
    assert parent["self_s"] == 0.0
    assert parent["concurrent_children"] == 1.0


def test_siblings_are_not_treated_as_nested() -> None:
    with collect_timings() as stages:
        with TimeMeasure("first"):
            time.sleep(0.01)
        with TimeMeasure("second"):
            time.sleep(0.01)

    assert stages["first"]["self_s"] == stages["first"]["total_s"]
    assert stages["second"]["self_s"] == stages["second"]["total_s"]


def test_timings_field_does_not_disturb_the_existing_envelope() -> None:
    """Existing consumers read data/search_messages and must be unaffected."""
    dumped = SearchOutput().model_dump()
    assert dumped["data"] == []
    assert dumped["search_messages"] == []
    assert dumped["timings"] is None
