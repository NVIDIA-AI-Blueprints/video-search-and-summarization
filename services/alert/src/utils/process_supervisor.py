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

"""Supervisor keeping a fixed set of pipeline processes alive.

A dead child stops polling its Kafka partitions, and those partitions stay
stalled until the group next rebalances, so every exit must be detected and
replaced rather than merely logged.
"""

import threading
import time
from typing import Any, Callable, List, Optional

from utils.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_RESTART_BACKOFF = 5.0
DEFAULT_RAPID_RESTART_WINDOW = 60.0
DEFAULT_MAX_RAPID_RESTARTS = 5
DEFAULT_STOP_TIMEOUT = 15.0


class SupervisedProcessError(RuntimeError):
    """Raised when a slot fails faster than it can be usefully restarted."""


class ProcessSupervisor:
    """Start ``count`` processes via ``spawn(index)`` and restart them on exit."""

    def __init__(
        self,
        count: int,
        spawn: Callable[[int], Any],
        on_exit: Optional[Callable[[Any], None]] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        restart_backoff: float = DEFAULT_RESTART_BACKOFF,
        rapid_restart_window: float = DEFAULT_RAPID_RESTART_WINDOW,
        max_rapid_restarts: int = DEFAULT_MAX_RAPID_RESTARTS,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT,
    ) -> None:
        if count < 1:
            raise ValueError(f"count must be >= 1, got {count}")
        self._count = count
        self._spawn = spawn
        self._on_exit = on_exit
        self._poll_interval = poll_interval
        self._restart_backoff = restart_backoff
        self._rapid_restart_window = rapid_restart_window
        self._max_rapid_restarts = max_rapid_restarts
        self._stop_timeout = stop_timeout
        self._processes: List[Any] = []
        self._started_at: List[float] = []
        self._rapid_restarts: List[int] = [0] * count
        self._shutdown = threading.Event()

    @property
    def processes(self) -> List[Any]:
        return list(self._processes)

    def start(self) -> None:
        self._processes = []
        self._started_at = []
        for index in range(self._count):
            self._processes.append(self._spawn(index))
            self._started_at.append(time.monotonic())

    def run(self) -> None:
        """Block supervising the children until shutdown is requested."""
        if not self._processes:
            self.start()
        try:
            while not self._shutdown.is_set():
                self._reap_and_restart()
                self._shutdown.wait(self._poll_interval)
        finally:
            self.stop()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def _reap_and_restart(self) -> None:
        for index, process in enumerate(self._processes):
            if self._shutdown.is_set() or process.is_alive():
                continue

            exitcode = process.exitcode
            process.join()
            self._notify_exit(process)

            uptime = time.monotonic() - self._started_at[index]
            if uptime < self._rapid_restart_window:
                self._rapid_restarts[index] += 1
            else:
                self._rapid_restarts[index] = 0

            # A slot that cannot stay up is a config or dependency failure;
            # looping on it forever hides the error behind restart noise.
            if self._rapid_restarts[index] > self._max_rapid_restarts:
                raise SupervisedProcessError(
                    f"pipeline process {index} exited {self._rapid_restarts[index]} times "
                    f"within {self._rapid_restart_window}s (last exitcode={exitcode})"
                )

            logger.error(
                "Pipeline process %d exited (exitcode=%s, uptime=%.1fs), restarting",
                index,
                exitcode,
                uptime,
            )
            if self._shutdown.wait(self._restart_backoff):
                return
            self._processes[index] = self._spawn(index)
            self._started_at[index] = time.monotonic()

    def stop(self) -> None:
        """Terminate every child, escalating to kill after ``stop_timeout``."""
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
            self._notify_exit(process)

    def _notify_exit(self, process: Any) -> None:
        if self._on_exit is None:
            return
        try:
            self._on_exit(process)
        except Exception:
            logger.debug("Pipeline process exit hook failed", exc_info=True)
