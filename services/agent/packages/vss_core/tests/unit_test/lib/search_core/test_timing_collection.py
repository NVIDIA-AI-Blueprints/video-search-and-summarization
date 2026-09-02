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
    assert all(v >= 0.0 for v in stages.values())


def test_repeated_labels_accumulate() -> None:
    """Some blocks run per hit; the useful number is what the stage cost in total."""
    with collect_timings() as stages:
        for _ in range(3):
            with TimeMeasure("attribute_search: frame lookups"):
                pass

    assert len(stages) == 1
    assert stages["attribute_search: frame lookups"] >= 0.0


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
    out = SearchOutput(timings=SearchTimings(stages={"a": 1.5}, total_s=2.0))
    dumped = out.model_dump()
    assert dumped["timings"]["stages"]["a"] == 1.5
    assert dumped["timings"]["total_s"] == 2.0


def test_timings_field_does_not_disturb_the_existing_envelope() -> None:
    """Existing consumers read data/search_messages and must be unaffected."""
    dumped = SearchOutput().model_dump()
    assert dumped["data"] == []
    assert dumped["search_messages"] == []
    assert dumped["timings"] is None
