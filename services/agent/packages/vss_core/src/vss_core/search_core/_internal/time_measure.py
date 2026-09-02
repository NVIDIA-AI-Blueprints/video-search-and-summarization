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

#: Active timing collector, if a caller opened one.
#:
#: A ContextVar rather than a parameter so no ``TimeMeasure`` call site has to
#: change: measured blocks are spread across the search primitives and their
#: helpers, and threading a sink through every signature would be a large,
#: churn-heavy diff for something callers usually do not want. asyncio tasks
#: inherit a copy of the context, and the copy still references the same dict,
#: so concurrent legs inside one search accumulate into the same collector.
_timing_collector: ContextVar[dict[str, float] | None] = ContextVar("vss_timing_collector", default=None)


@contextmanager
def collect_timings() -> Iterator[dict[str, float]]:
    """Collect every ``TimeMeasure`` duration in this context, in seconds.

    Durations accumulate per label, because some measured blocks run more than
    once per search -- attribute frame lookups run per hit, for instance -- and
    the useful number is the total time that stage cost, not the last one.

    Nesting is intentionally not modelled: labels are already namespaced by
    convention ("embed_search: ES search execution"), so the flat mapping is
    readable and sums that exceed the parent's own total are expected.
    """
    sink: dict[str, float] = {}
    token = _timing_collector.set(sink)
    try:
        yield sink
    finally:
        _timing_collector.reset(token)


class TimeMeasure:
    """Measures the execution time of a block of code as a context manager."""

    def __init__(self, string: str, print: bool = True) -> None:
        self._string = string
        self._print = print

    def __enter__(self) -> TimeMeasure:
        self._start_time = time.perf_counter()
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
        if sink is not None:
            sink[self._string] = sink.get(self._string, 0.0) + (self._end_time - self._start_time)

    @property
    def execution_time(self) -> float:
        return self._end_time - self._start_time

    @property
    def current_execution_time(self) -> float:
        return time.perf_counter() - self._start_time
