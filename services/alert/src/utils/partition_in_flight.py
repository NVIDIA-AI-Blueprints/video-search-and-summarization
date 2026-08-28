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

"""Per-partition accounting of work accepted for processing.

Dedup state is held in this process, so two members working the same
``sensorId`` at the same time cannot see each other's decisions and both can
publish. A rebalance creates exactly that: the member losing a partition is
still finishing a cohort while the member gaining it starts a new one. Knowing
how much work a partition still owes is what lets the outgoing member finish
first.

This does not make a crash safe. Offsets are committed when records are read,
so work lost when a process dies is lost whatever this says; that is a
delivery-semantics question and is deliberately not addressed here.
"""

import threading
import time
from typing import Dict, Iterable, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger(__name__)

PartitionKey = Tuple[str, int]

# Comfortably inside kafka.max_poll_interval_ms (60s by default). A drain runs
# on the consume thread inside the rebalance callback, and the group cannot
# finish rebalancing until every member returns from it, so overrunning would
# cost the member its place and start another rebalance -- worse than the
# overlap the drain exists to prevent.
DEFAULT_DRAIN_TIMEOUT = 15.0


class Admission:
    """One accepted message, released exactly once by whoever ends up owning it.

    Handed down the pipeline so the release travels with the work: a message
    dropped before it runs is released by the stage that dropped it, and one
    that is dispatched is released by its completion callback.
    """

    __slots__ = ("_tracker", "key", "_released", "transferred")

    def __init__(self, tracker: "PartitionInFlight", key: Optional[PartitionKey]) -> None:
        self._tracker = tracker
        self.key = key
        self._released = False
        self.transferred = False

    def split(self) -> "Admission":
        """Take a second admission for the same partition.

        For a stage that turns one accepted message into several. Reusing this
        one for all of them would let the first completion clear the group, so
        a drain could finish over work that was still running.
        """
        return self._tracker.accept(self.key)

    def transfer(self) -> "Admission":
        """Hand ownership to a completion callback that outlives this call."""
        self.transferred = True
        return self

    def release(self) -> None:
        if self._released or self.key is None:
            return
        self._released = True
        self._tracker.release(self.key)


class PartitionInFlight:
    """Counts work accepted per partition, and waits for it to finish."""

    def __init__(self) -> None:
        self._state = threading.Condition()
        self._counts: Dict[PartitionKey, int] = {}

    def accept(self, key: Optional[PartitionKey]) -> "Admission":
        """Record one message as accepted, from the moment it is scheduled.

        Taken here rather than where the work finally runs, because a message
        waiting in a worker queue is already this instance's responsibility:
        counting it only once it starts would let a rebalance drain
        successfully while queued records were still waiting to begin.
        """
        if key is None:
            return Admission(self, None)
        with self._state:
            self._counts[key] = self._counts.get(key, 0) + 1
        return Admission(self, key)

    def release(self, key: Optional[PartitionKey]) -> None:
        if key is None:
            return
        with self._state:
            remaining = self._counts.get(key, 0) - 1
            if remaining > 0:
                self._counts[key] = remaining
            else:
                self._counts.pop(key, None)
            self._state.notify_all()

    def in_flight(self, key: PartitionKey) -> int:
        with self._state:
            return self._counts.get(key, 0)

    def total(self) -> int:
        with self._state:
            return sum(self._counts.values())

    def drain(
        self,
        keys: Iterable[PartitionKey],
        timeout: float = DEFAULT_DRAIN_TIMEOUT,
    ) -> bool:
        """Block until nothing is in flight for ``keys``, or ``timeout`` passes.

        Returns whether it drained. On expiry the caller carries on: being
        evicted from the group for holding up the rebalance is worse than the
        remaining overlap, and the alternative -- abandoning the work -- would
        lose records that are already committed.
        """
        keys = list(keys)
        if not keys:
            return True

        deadline = time.monotonic() + timeout
        with self._state:
            while True:
                outstanding = sum(self._counts.get(key, 0) for key in keys)
                if outstanding == 0:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "Gave up draining %d partition(s) after %.0fs with %d "
                        "message(s) still in flight; the incoming owner may "
                        "process the same sensor concurrently",
                        len(keys), timeout, outstanding,
                    )
                    return False
                self._state.wait(remaining)
