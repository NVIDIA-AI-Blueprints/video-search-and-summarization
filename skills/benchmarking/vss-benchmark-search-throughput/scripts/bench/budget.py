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

"""Shared measurement budget across the worker pool.

The budget is a target per ``query_id`` **distributed across N workers**, not
per worker. Workers claim a slot from this coordinator before issuing a measured
request, so raising the concurrency shortens the run rather than multiplying the
work.

The target itself does grow with concurrency -- see
:func:`bench.plan.samples_per_query` -- but only enough to keep each worker's
share above a round-count floor, so the run does not spend its window draining.

``execution.min_run_duration_sec`` adds a second stopping condition on the
clock: the budget keeps handing out slots past the target until that many
seconds have passed since the window opened. A request budget is the right unit
for statistics and the wrong one for anything read off a Prometheus scrape, so
the two floors answer different questions and a run stops only when both are
met.
"""

from __future__ import annotations

import threading
import time


class SampleBudget:
    """Thread-safe per-query slot allocator for the measurement phase.

    A worker calls :meth:`claim` to reserve a slot, then either :meth:`commit`
    (request succeeded) or :meth:`release` (request failed, slot returns to the
    pool so another attempt can still reach the target sample count).

    ``min_duration_sec`` holds the run open past the target. The clock starts at
    :meth:`open_window` rather than at construction, because warmup runs in
    between and only the measured window should count against the floor.
    """

    def __init__(
        self,
        query_ids: list[str],
        target_per_query: int,
        min_duration_sec: float = 0.0,
    ) -> None:
        self._lock = threading.Lock()
        self._target = target_per_query
        self._order = list(query_ids)
        self._committed: dict[str, int] = {qid: 0 for qid in query_ids}
        self._in_flight: dict[str, int] = {qid: 0 for qid in query_ids}
        self._attempts: dict[str, int] = {qid: 0 for qid in query_ids}
        self._min_duration_sec = max(0.0, float(min_duration_sec or 0.0))
        # None until the window opens, and the extension is gated on it, so a
        # budget whose window never opened stops at the target as it always did.
        self._deadline: float | None = None

    def open_window(self) -> None:
        """Start the minimum-duration clock. Called when the barrier releases.

        Only the first call counts, so a worker that found the barrier broken
        can open it too without restarting a clock another worker started.
        """
        with self._lock:
            if self._deadline is None:
                self._deadline = time.monotonic() + self._min_duration_sec

    @property
    def extended(self) -> bool:
        """Whether the duration floor carried this run past its sample target."""
        with self._lock:
            return any(self._committed[qid] > self._target for qid in self._order)

    @property
    def committed(self) -> dict[str, int]:
        with self._lock:
            return dict(self._committed)

    def _attempt_cap(self, query_id: str) -> int:
        """Three attempts per sample asked for.

        Stops a dead service from turning a run into a spin: failures release
        their slot without committing, so the cap is what ends the run instead.
        It has to track the extension as well as the target, or the clock floor
        would reinstate the unbounded loop the cap exists to prevent --
        ``committed`` only grows when a request actually succeeds.
        """
        return 3 * max(self._target, self._committed[query_id] + 1)

    def claim(self, query_id: str) -> bool:
        """Reserve one measured request for ``query_id``. False if not needed."""
        with self._lock:
            if self._attempts[query_id] >= self._attempt_cap(query_id):
                return False
            held = self._committed[query_id] + self._in_flight[query_id]
            if held < self._target:
                return self._grant(query_id)
            if self._deadline is None or time.monotonic() >= self._deadline:
                return False
            # Past the target and still on the clock. Hand the slot only to a
            # query that is not ahead of the others, which keeps the extra
            # samples spread evenly -- analysis groups by query_id, so an
            # extension favouring one query would skew its percentiles.
            #
            # Queries at their attempt cap are left out of the comparison. A
            # failing query stops committing, so it would sit at the minimum for
            # the rest of the run and refuse every healthy query for being
            # "ahead" of it -- ending the extension the moment any one query
            # broke, which is exactly when a long resource window is most
            # wanted. The caller is uncapped (checked above), so this never
            # minimises over an empty set.
            behind = min(
                self._committed[qid] + self._in_flight[qid]
                for qid in self._order
                if self._attempts[qid] < self._attempt_cap(qid)
            )
            if held > behind:
                return False
            return self._grant(query_id)

    def _grant(self, query_id: str) -> bool:
        """Record one reservation. Caller holds the lock."""
        self._in_flight[query_id] += 1
        self._attempts[query_id] += 1
        return True

    def commit(self, query_id: str) -> None:
        with self._lock:
            self._in_flight[query_id] -= 1
            self._committed[query_id] += 1

    def release(self, query_id: str) -> None:
        with self._lock:
            self._in_flight[query_id] -= 1

    def next_claimable(self, start_index: int) -> tuple[int, str] | None:
        """Round-robin scan from ``start_index`` for a query still needing samples.

        Preserves the q1 -> q2 -> q3 -> q1 cycle while letting a worker skip
        queries whose budget is exhausted, so the run ends as soon as every
        query reaches its target and the duration floor is met.

        Returning ``None`` is the worker's stop signal, so the scan must not
        give up while any query is still claimable. During an extension it
        cannot: the gate is "not ahead of the others", and the uncapped query
        holding the minimum always passes it. So the scan ends only once the
        clock runs out or every query has hit its attempt cap.
        """
        count = len(self._order)
        for offset in range(count):
            index = (start_index + offset) % count
            query_id = self._order[index]
            if self.claim(query_id):
                return index, query_id
        return None
