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

import logging

from abc import ABC, abstractmethod
from pydantic import BaseModel
from functools import cached_property
from multiprocessing.synchronize import Event as EventType
from typing import Any
from collections.abc import Callable

from mdx.analytics.core.utils.processing_stats import ProcessingStats, BatchStats
from mdx.analytics.core.app.app_base import BaseApp
from mdx.analytics.core.schema.config import AppConfig

logger = logging.getLogger(__name__)


class Scheduler(ABC):
    """Abstract base class for task schedulers.
    
    Defines the interface for scheduling and managing task execution.
    """

    @abstractmethod
    def submit(self, tasks: list['Task']) -> None:
        """Submit a list of tasks to the scheduler for execution.

        :param list[Task] tasks: List of Task objects to be scheduled for execution
        """

        raise NotImplementedError("Subclasses must implement method before invoking")


    @abstractmethod
    def shutdown(self) -> None:
        """Shutdown the scheduler and cleanup resources."""

        raise NotImplementedError("Subclasses must implement method before invoking")


class Task(BaseModel):
    """Task model representing a unit of work to be executed by a scheduler.
    
    Contains all necessary information to instantiate and run an application
    processor in a separate process.

    :ivar str app_name: Name of the application
    :ivar int id: Task identifier
    :ivar str poller_name: Name of the poller method to invoke
    :ivar str handler_name: Name of the handler method to invoke
    :ivar str shutdown_hook_name: Name of the shutdown hook method
    :ivar type[BaseApp] app_class: The application class that extends BaseApp
    :ivar AppConfig config: Application configuration
    :ivar str | None calibration_path: Path to the calibration configuration file
    :ivar str | None log_config_path: Path to the logging config file (re-applied in spawn-mode children)
    """

    app_name: str
    id: int
    poller_name: str  # method name
    handler_name: str  # method name
    shutdown_hook_name: str = 'close'  # method name

    # For lazy initialization in child processes
    app_class: type[BaseApp]
    config: AppConfig
    calibration_path: str | None
    log_config_path: str | None = None

    @cached_property
    def app_instance(self) -> BaseApp:
        return self.app_class(self.config, self.calibration_path)

    def _get_callable(self, attr: str) -> Callable:
        """Get callable from either direct reference or by name from app instance.
        
        :param str attr: Attribute name to retrieve from app instance
        :return Callable: The callable retrieved from the app instance
        """
        return getattr(self.app_instance, attr)

    @cached_property
    def poller(self) -> Callable:
        return self._get_callable(self.poller_name)

    @cached_property
    def handler(self) -> Callable:
        return self._get_callable(self.handler_name)

    @cached_property
    def shutdown_hook(self) -> Callable:
        return self._get_callable(self.shutdown_hook_name)

    @cached_property
    def tname(self) -> str:
        return f"{self.app_name}.{self.handler_name}"

    @cached_property
    def tid(self) -> str:
        return f"{self.tname}-{self.id}"

    def __call__(self, shutdown_flag: EventType) -> Any:
        """Execute the task by continuously polling for messages and processing them.

        :param EventType shutdown_flag: Multiprocessing event used to signal when task should stop execution
        :return Any: Result of task execution
        """

        logger.info("Task Ready for processing...")

        stats = None
        batch_id = 1

        try:
            while not shutdown_flag.is_set():

                if (messages := self.poller(group_id_suffix = self.tname)):
                    # Initialize stats only after first batch with data
                    if not stats:
                        stats = ProcessingStats(worker_id = self.tid)

                    logger.info(f"[Batch {batch_id}] - {len(messages)} msgs fetched from source.")
                    batch_stats = BatchStats(worker_id = self.tid, batch_id = batch_id)
                    batch_stats.update(len(messages))
                    stats.update(batch_stats.num_msgs)
                    # TODO: remove batch stats from handler
                    self.handler(messages, batch_stats)
                    logger.info(f"[Batch {batch_id}] - processing speed = {batch_stats.msgs_per_sec} msgs/sec")
                    logger.info(f"Avg processing speed = {stats.msgs_per_sec} msgs/sec")

                else:
                    logger.debug(f"[Batch {batch_id}] - No msgs fetched from source.")

                batch_id += 1

        finally:
            self._close()


    def _close(self):
        """Clean up task resources by executing the shutdown hook."""

        logger.info(f"Task Cleaning up...")
        self.shutdown_hook()
        logger.info(f"Task Cleanup completed...")
