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
DEFAULT_STOP_TIMEOUT = 15.0


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
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT,
    ) -> None:
        if count < 1:
            raise ValueError(f"count must be >= 1, got {count}")
        self._count = count
        self._spawn = spawn
        self._on_exit = on_exit
        self._on_poll = on_poll
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

    def stop(self) -> None:
        """Terminate every child, escalating to kill after ``stop_timeout``."""
        # Read before the flag is set, or every exit reported from here looks
        # like one that was asked for -- including the crash that got us here.
        expected = self._shutdown.is_set()
        self._shutdown.set()
        processes, self._processes = self._processes, []
        for process in processes:
            if process.is_alive():
                process.terminate()

        deadline = time.monotonic() + self._stop_timeout
        for process in processes:
            process.join(timeout=max(0.0, deadline - time.monotonic()))
            if process.is_alive():
                logger.warning("Pipeline process %s did not stop gracefully, killing", process.pid)
                process.kill()
                process.join()
            self._notify_exit(process, expected)

    def _notify_exit(self, process: Any, expected: bool) -> None:
        if self._on_exit is None:
            return
        try:
            self._on_exit(process, expected)
        except Exception:
            logger.debug("Pipeline process exit hook failed", exc_info=True)
