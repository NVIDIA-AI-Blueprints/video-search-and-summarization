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
"""TimeMeasure context manager.

Times a block of code and logs the elapsed duration. The PERF and STATUS log
levels are registered here so their level names resolve consistently wherever
this helper's log records are emitted.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

LOG_PERF_LEVEL = 15
LOG_STATUS_LEVEL = 16

logging.addLevelName(LOG_PERF_LEVEL, "PERF")
logging.addLevelName(LOG_STATUS_LEVEL, "STATUS")

#: Slack before calling a negative self time "overlapping children" rather than
#: float noise from summing many small durations.
_CONCURRENCY_EPSILON = 1e-6

#: Active timing collector, if a caller opened one.
#:
#: A ContextVar rather than a parameter so no ``TimeMeasure`` call site has to
#: change: measured blocks are spread across the search primitives and their
#: helpers, and threading a sink through every signature would be a large,
#: churn-heavy diff for something callers usually do not want. asyncio tasks
#: inherit a copy of the context, and the copy still references the same dict,
#: so concurrent legs inside one search accumulate into the same collector.
_timing_collector: ContextVar[dict[str, dict[str, float]] | None] = ContextVar("vss_timing_collector", default=None)

#: Labels currently on the stack, innermost last. Used to attribute a block's
#: duration to its parent so callers can separate self time from inclusive time.
_timing_stack: ContextVar[tuple[str, ...]] = ContextVar("vss_timing_stack", default=())


def _new_entry() -> dict[str, float]:
    return {"total_s": 0.0, "_children_s": 0.0, "calls": 0.0}


@contextmanager
def collect_timings() -> Iterator[dict[str, dict[str, float]]]:
    """Collect every ``TimeMeasure`` duration in this context, in seconds.

    Each label maps to ``{"total_s", "self_s", "calls"}``:

    * ``total_s`` is inclusive -- the block and everything measured inside it.
    * ``self_s`` excludes nested measured blocks. This is what a "where did the
      time go" table wants; ``total_s`` alone makes a parent and its children
      look like separate costs. Self times sum to real elapsed work only where
      blocks did not overlap -- see ``concurrent_children``.
    * ``calls`` counts entries, because some blocks run per hit rather than
      once per search.
    * ``concurrent_children`` is 1.0 when nested blocks overlapped -- their
      summed duration exceeded the parent's own elapsed time, which happens
      whenever children run under ``asyncio.gather`` (fusion runs its embedding
      and attribute legs that way). Self time is not meaningful for such a
      block: it is clamped to 0.0 rather than reported negative, and this flag
      says why.

    Blocks that run more than once accumulate rather than overwrite.
    """
    sink: dict[str, dict[str, float]] = {}
    token = _timing_collector.set(sink)
    stack_token = _timing_stack.set(())
    try:
        yield sink
    finally:
        _timing_collector.reset(token)
        _timing_stack.reset(stack_token)
        # Derive self time once every block has closed. Doing it incrementally
        # is order-dependent: children close before their parent, so the parent
        # would be clamped before it had added its own elapsed time.
        for entry in sink.values():
            children = entry.pop("_children_s", 0.0)
            self_s = entry["total_s"] - children
            entry["concurrent_children"] = 1.0 if self_s < -_CONCURRENCY_EPSILON else 0.0
            entry["self_s"] = max(self_s, 0.0)


class TimeMeasure:
    """Measures the execution time of a block of code as a context manager."""

    def __init__(self, string: str, print: bool = True) -> None:
        self._string = string
        self._print = print

    def __enter__(self) -> TimeMeasure:
        self._start_time = time.perf_counter()
        if _timing_collector.get() is not None:
            self._stack_token = _timing_stack.set((*_timing_stack.get(), self._string))
        logger.debug("[START] " + self._string)
        return self

    def __exit__(
        self,
        type: type[BaseException] | None,
        value: BaseException | None,
        traceback: object,
    ) -> None:
        self._end_time = time.perf_counter()
        logger.debug("[END]   " + self._string)
        if self._print:
            exec_time = self._end_time - self._start_time
            if exec_time > 1:
                exec_time, unit = exec_time, "sec"
            elif exec_time > 0.001:
                exec_time, unit = exec_time * 1000.0, "millisec"
            elif exec_time > 1e-6:
                exec_time, unit = exec_time * 1e6, "usec"
            else:
                exec_time, unit = exec_time * 1e9, "nanosec"
            logger.log(
                LOG_PERF_LEVEL,
                f"{self._string:s} execution time = {exec_time:.3f} {unit:s}",
            )
            logger.debug(f"{self._string} start={self._start_time!s} end={self._end_time!s}")

        # Collected regardless of `print`: the caller asked for timings by
        # opening a collector, which is independent of log verbosity.
        sink = _timing_collector.get()
        if sink is None:
            return

        elapsed = self._end_time - self._start_time
        stack = _timing_stack.get()
        _timing_stack.reset(self._stack_token)

        entry = sink.setdefault(self._string, _new_entry())
        entry["total_s"] += elapsed
        entry["calls"] += 1

        # Accumulate against whichever measured block encloses this one; self
        # time is derived from it when the collector closes.
        if len(stack) > 1:
            parent = sink.setdefault(stack[-2], _new_entry())
            parent["_children_s"] += elapsed

    @property
    def execution_time(self) -> float:
        return self._end_time - self._start_time

    @property
    def current_execution_time(self) -> float:
        return time.perf_counter() - self._start_time
