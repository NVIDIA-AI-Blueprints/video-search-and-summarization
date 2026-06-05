# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import functools
import logging
import multiprocessing as mp
import signal
import traceback

from multiprocessing.connection import Connection
from multiprocessing.context import SpawnProcess
from multiprocessing.synchronize import Event as EventType

from mdx.analytics.core.app.scheduler.app_scheduler import Task, Scheduler
from mdx.analytics.core.logger_setup import setup_logging

logger = logging.getLogger(__name__)


def _sig_shutdown(shutdown_flag: EventType, signum: int, _) -> None:
    """Signal handler for graceful shutdown of tasks.

    Sets the shutdown flag when SIGINT or SIGTERM signals are received.

    :param EventType shutdown_flag: Event object to signal shutdown to the task
    :param int signum: Signal number received
    :param _: Signal frame (unused)
    """

    if not shutdown_flag.is_set():
        logger.info(f"Exiting task, shutdown signal {signal.Signals(signum).name} received.")
        shutdown_flag.set()


def _target(task: Task, shutdown_event: EventType, err_conn: Connection) -> None:
    """Target function executed in each spawn-mode subprocess.

    Re-applies logging config (spawn children do not inherit the parent's
    logging setup), installs signal handlers, and runs the task. Any
    exception is reported back to the main process through err_conn.

    :param Task task: The task object to execute
    :param EventType shutdown_event: Event to check for shutdown signals
    :param Connection err_conn: Connection to send errors back to main process
    """

    setup_logging(task.log_config_path)

    logger.info("Task about to execute")
    signal.signal(signal.SIGINT, functools.partial(_sig_shutdown, shutdown_event))
    signal.signal(signal.SIGTERM, functools.partial(_sig_shutdown, shutdown_event))

    try:
        task(shutdown_event)

    except Exception as e:
        # Convert exception to a simple, serializable form. Some complex exceptions don't pickle well across processes
        err_conn.send((RuntimeError(f"{e.__class__.__name__}: {str(e)}"), traceback.format_exc()))
    finally:
        err_conn.close()


class MultiprocessingScheduler(Scheduler):
    """A multiprocessing-based task scheduler that runs tasks in separate processes.

    Uses the `spawn` start method so child processes get a fresh interpreter
    rather than inheriting the parent's address space. This avoids
    fork-after-threads deadlocks that can occur when the parent has spun up
    background threads (Kafka/Redis/MQTT clients, calibration watchdog, etc.)
    before the workers fork.

    :ivar float SHUTDOWN_TIMEOUT_SECONDS: Timeout in seconds for graceful process shutdown
    :ivar dict[int, SpawnProcess] _processes: Dictionary mapping process IDs to Process objects
    :ivar list[EventType] _shutdown_events: References to per-task Event objects, retained for the
        lifetime of the scheduler so their backing SemLocks aren't garbage-collected before spawn
        children can attach to them (spawn unpickles via sem_open by name).
    """

    # Shutdown timeout
    SHUTDOWN_TIMEOUT_SECONDS = 10.0

    def __init__(self) -> None:
        """Initialize the multiprocessing scheduler."""

        self._ctx = mp.get_context("spawn")
        self._processes: dict[int, SpawnProcess] = {}
        self._shutdown_events: list[EventType] = []

    def submit(self, tasks: list[Task]) -> None:
        """Submit a list of tasks to be executed in separate processes.

        Each task is run in its own process with proper signal handling and error
        propagation. The method blocks until all tasks complete or an error occurs.

        :param list[Task] tasks: List of Task objects to be executed in parallel
        :raises Exception: If a task fails to start
        :raises RuntimeError: If any process raises a fatal error during execution
        """

        err_pipes: list[tuple[int, Connection]] = []

        logger.info(f"Submitting {len(tasks)} tasks for execution")
        for task in tasks:
            logger.info(f"Processing task: {task.tid}")
            try:
                shutdown_event = self._ctx.Event()
                # Retain the Event reference so its SemLock isn't GC'd before the spawn
                # child unpickles it via sem_open. Without this, the child crashes with
                # FileNotFoundError in multiprocessing/synchronize.py:__setstate__.
                self._shutdown_events.append(shutdown_event)
                main_conn, task_conn = self._ctx.Pipe(duplex=False)

                logger.info(f"[Task - {task.tid}] Creating process...")
                process = self._ctx.Process(target=_target, name=task.tid, args=(task, shutdown_event, task_conn,))
                logger.info(f"[Task - {task.tid}] Starting process...")
                process.start()

                # Close the parent's copy of the write end so an EOF on main_conn
                # unambiguously means the child closed/crashed.
                task_conn.close()

                logger.info(f"[Task - {task.tid}] Started with pid {process.pid}")
                err_pipes.append((process.pid, main_conn))
                self._processes[process.pid] = process

            except Exception as e:
                logger.error(f"[Task - {task.tid}] failed to start with error: {str(e)}")
                raise e

        logger.info(f"All tasks submitted. Active processes: {len(self._processes)}")

        # Monitor running processes for errors
        while mp.active_children():
            for pid, err_conn in err_pipes:
                logger.debug(f"Checking for errors from active process [pid - {pid}]")
                if err_conn.poll(1):
                    ex, tb = err_conn.recv()
                    logger.info(f"Traceback from process {pid}:\n{tb}")
                    self.shutdown()
                    raise RuntimeError(f"Process {pid} failed: {str(ex)}")

    def shutdown(self) -> None:
        """Shutdown all active processes and close any open connections.

        Terminates all running processes gracefully, and kills any processes
        that don't terminate within the timeout period.
        """

        logger.info(f"Shutting down {len(self._processes)} processes...")

        # Set the shutdown event first so workers can exit through their normal
        # poll loop. terminate() below is the fallback if a worker is wedged.
        for shutdown_event in self._shutdown_events:
            shutdown_event.set()

        for pid, process in self._processes.items():
            try:
                if process.is_alive():
                    logger.info(f"Terminating process {process.name}({pid})")
                    process.terminate()
                    process.join(timeout=self.SHUTDOWN_TIMEOUT_SECONDS)

                    if process.is_alive():
                        logger.warning(f"Process {process.name}({pid}) did not terminate gracefully, forcing kill")
                        process.kill()
                        process.join()

            except Exception as e:
                logger.error(f"Error shutting down process {pid}: {e}")

        # Clear the dictionaries
        self._processes.clear()
        self._shutdown_events.clear()
        logger.info("Cleanup complete, all workers removed...")
