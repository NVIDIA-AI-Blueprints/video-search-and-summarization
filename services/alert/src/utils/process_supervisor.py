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

"""Supervisor running a fixed set of pipeline processes as one unit.

A child that exits unexpectedly takes the whole instance down with it. The
alternative, replacing the dead one in place, kept the container alive around
a partially rebuilt instance: the replacement rejoins the consumer group and
triggers a rebalance, the surviving children keep whatever in-flight work they
had, and whatever caused the exit is still there. Failing whole is what makes
the orchestrator's restart the recovery path, and what makes a crash visible
as a crash.
"""

import threading
import time
from typing import Any, Callable, List, Optional

from utils.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_POLL_INTERVAL = 1.0

# The shutdown timeline, as offsets from the moment a stop begins. Children get
# the drain window to finish in flight work, then a terminate, then a kill; the
# whole sequence is over by DEADLINE. A single join budget was tried first and
# was shorter than the drain a child is allowed to take, which made SIGKILL the
# normal path rather than the exception.
DRAIN_SECONDS = 15.0
TERMINATE_AT = 18.0
KILL_AT = 19.0
DEADLINE = 20.0
DEFAULT_STOP_TIMEOUT = DEADLINE

# A killed process still has to be collected, whatever the budget.
REAP_FLOOR_SECONDS = 1.0


class SupervisedProcessError(RuntimeError):
    """Raised when a pipeline process exits without a shutdown being asked for."""


class ProcessSupervisor:
    """Start ``count`` processes via ``spawn(index)`` and fail if any exits."""

    def __init__(
        self,
        count: int,
        spawn: Callable[[int], Any],
        on_exit: Optional[Callable[[Any, bool], None]] = None,
        on_poll: Optional[Callable[[], None]] = None,
        watch: Optional[List[Any]] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT,
    ) -> None:
        if count < 1:
            raise ValueError(f"count must be >= 1, got {count}")
        self._count = count
        self._spawn = spawn
        self._on_exit = on_exit
        self._on_poll = on_poll
        # Processes that belong to the instance without being pipeline slots:
        # not counted, not replaced, but their exit ends the instance too. The
        # API child is one -- an instance without its health endpoint is not a
        # healthy instance, and leaving it behind orphans the port.
        self._watch: List[Any] = list(watch or [])
        self._poll_interval = poll_interval
        self._stop_timeout = stop_timeout
        self._processes: List[Any] = []
        self._shutdown = threading.Event()

    @property
    def processes(self) -> List[Any]:
        return list(self._processes)

    @property
    def shutdown_requested(self) -> bool:
        """Whether the exits being seen are ones somebody asked for."""
        return self._shutdown.is_set()

    def start(self) -> None:
        """Start every child, registering each one the moment it exists.

        Built one at a time and recorded as it goes: a comprehension only
        assigns once it finishes, so a spawn that failed partway left the
        children already started running with nothing tracking them, and the
        teardown that followed saw an empty list.
        """
        self._processes = []
        try:
            for index in range(self._count):
                self._processes.append(self._spawn(index))
        except Exception:
            logger.error("Spawning pipeline process failed; stopping the ones already started")
            self.stop()
            raise

    def run(self) -> None:
        """Block until shutdown is requested, or a child exits on its own."""
        try:
            if not self._processes:
                # Inside the try: a spawn that fails partway through startup
                # would otherwise leave the children already started running,
                # each holding a consumer-group slot.
                self.start()
            while not self._shutdown.is_set():
                self._report_state()
                self._fail_on_any_exit()
                self._shutdown.wait(self._poll_interval)
        finally:
            self.stop()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def _report_state(self) -> None:
        if self._on_poll is None:
            return
        try:
            self._on_poll()
        except Exception:
            logger.debug("Pipeline fleet state hook failed", exc_info=True)

    def _fail_on_any_exit(self) -> None:
        for label, process in self._watch_pairs():
            if self._shutdown.is_set() or process.is_alive():
                continue
            logger.error("%s exited (exitcode=%s); stopping the instance",
                         label, process.exitcode)
            raise SupervisedProcessError(
                f"{label} exited unexpectedly (exitcode={process.exitcode})"
            )

        for index, process in enumerate(self._processes):
            if self._shutdown.is_set() or process.is_alive():
                continue

            # Report the exit before tearing the rest down, so the cause is
            # the first thing in the log rather than the last.
            logger.error(
                "Pipeline process %d exited (exitcode=%s); stopping the instance",
                index,
                process.exitcode,
            )
            raise SupervisedProcessError(
                f"pipeline process {index} exited unexpectedly "
                f"(exitcode={process.exitcode})"
            )

    def _watch_pairs(self):
        # Named, because "supervised process 41 exited" tells an operator
        # nothing about whether the health endpoint or a pipeline slot died.
        return [(getattr(p, "name", None) or f"supervised process {p.pid}", p)
                for p in self._watch]

    def stop(self) -> None:
        """Walk the shutdown timeline, then reap everything.

        T0 stop admission, drain to T+15, terminate survivors at T+18, kill at
        T+19, reap by T+20. Children are asked to stop first and only forced
        later, so work already accepted has a defined window to finish in.
        """
        # Read before the flag is set, or every exit reported from here looks
        # like one that was asked for -- including the crash that got us here.
        expected = self._shutdown.is_set()
        self._shutdown.set()
        processes, self._processes = self._processes, []
        # Sampled before the terminate below, because a process this call
        # stops has an exit somebody asked for whatever brought us here. Only
        # one that was already gone exited on its own; applying the single
        # flag to all of them reported one crash as ``count`` unexpected exits.
        exited_alone = {id(p): not p.is_alive() for p in processes}
        started = time.monotonic()
        everything = processes + list(self._watch)
        # Offsets scale with the configured budget so the phases keep their
        # proportions when a caller shortens it; at the default they are
        # exactly the 15/18/19/20 of the timeline.
        scale = self._stop_timeout / DEADLINE if DEADLINE else 0.0
        drain_until = started + DRAIN_SECONDS * scale
        terminate_at = started + TERMINATE_AT * scale
        kill_at = started + KILL_AT * scale
        reap_by = started + DEADLINE * scale

        # T0 -- ask every child to stop admitting and start draining.
        for process in everything:
            if process.is_alive():
                process.terminate()

        # Drain, then terminate again and kill. The second terminate is not
        # redundant: a child that installed its own handler may still be inside
        # a drain it began at T0.
        if any(process.is_alive() for process in everything):
            self._await_all(everything, drain_until)
        self._escalate(everything, terminate_at, "terminate")
        self._escalate(everything, kill_at, "kill")

        # Reap whatever is left, then report.
        # One floor for the phase, not one per process: applied per iteration
        # it multiplied by the number of children, so a fleet with a few stuck
        # in uninterruptible sleep ran past the deadline it exists to hold.
        # join(0) does not collect a process that was just killed, which is
        # what the floor is for.
        reap_deadline = max(reap_by, time.monotonic() + REAP_FLOOR_SECONDS)
        for process in everything:
            if process.is_alive():
                process.kill()
            process.join(timeout=max(0.0, reap_deadline - time.monotonic()))

        for process in processes:
            self._notify_exit(process, expected or not exited_alone[id(process)])
        self._watch = []

    @staticmethod
    def _await_all(processes: List[Any], until: float) -> None:
        for process in processes:
            process.join(timeout=max(0.0, until - time.monotonic()))

    @staticmethod
    def _escalate(processes: List[Any], at: float, how: str) -> None:
        """Wait until ``at``, then force whatever is still running.

        Liveness is checked before the wait as well as after it: a fleet that
        has already stopped has nothing to escalate, and sleeping through the
        remaining phases anyway delayed every clean shutdown -- and every
        non-zero exit after a crash -- by most of the timeline.
        """
        if not any(process.is_alive() for process in processes):
            return
        remaining = at - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        for process in processes:
            if not process.is_alive():
                continue
            logger.warning("Supervised process %s did not stop in time; %s",
                           getattr(process, "name", None) or process.pid, how)
            getattr(process, how)()

    def _notify_exit(self, process: Any, expected: bool) -> None:
        if self._on_exit is None:
            return
        try:
            self._on_exit(process, expected)
        except Exception:
            logger.debug("Pipeline process exit hook failed", exc_info=True)
