# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Base classes for task handlers."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from src.api.db_manager import TaskDBManager
from src.api.models import TaskResult
from src.lib.flywheel.cancellation import FlywheelCancelledError, check_cancellation
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.tasks.base")


class BaseTaskHandler(ABC):
    """Base class for all task handlers.

    Each task handler encapsulates the business logic for a specific task in the flywheel pipeline.
    This allows for better separation of concerns and makes it easier to add new pipeline types.
    """

    def __init__(self, db_manager: TaskDBManager):
        """Initialize the task handler.

        Args:
            db_manager: Database manager for MongoDB operations
        """
        self.db_manager = db_manager
        self.logger = setup_logging(f"data_flywheel.tasks.{self.__class__.__name__}")

    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        """Execute the task logic.

        This method should be implemented by each specific task handler.

        Returns:
            The result of the task execution
        """
        pass

    def check_cancellation(self, flywheel_run_id: str, raise_error: bool = False) -> bool:
        """Check if the flywheel run has been cancelled.

        Args:
            flywheel_run_id: ID of the flywheel run
            raise_error: If True, raise FlywheelCancelledError when cancelled

        Returns:
            True if cancelled, False otherwise

        Raises:
            FlywheelCancelledError: If raise_error is True and run is cancelled
        """
        try:
            check_cancellation(flywheel_run_id)
        except FlywheelCancelledError as e:
            self.logger.info(f"Flywheel run cancelled: {e}")
            if raise_error:
                raise e
            return True
        return False

    def should_skip_stage(self, previous_result: TaskResult | None, stage_name: str) -> bool:
        """Check if the stage should be skipped due to previous errors.

        Args:
            previous_result: Result from previous task
            stage_name: Name of the current stage

        Returns:
            True if stage should be skipped, False otherwise
        """
        if isinstance(previous_result, TaskResult) and previous_result.error:
            self.logger.warning(
                "Skipping %s because a previous stage failed: %s",
                stage_name,
                previous_result.error,
            )
            return True
        return False

    def extract_previous_result(
        self,
        previous_results: list[TaskResult] | TaskResult | dict,
        *,
        validator: Callable[[TaskResult], bool] | None = None,
        error_msg: str = "No valid TaskResult found",
    ) -> TaskResult:
        """Extract a single TaskResult from previous_results.

        Celery tasks can receive either a single TaskResult instance, a raw
        dict serialized version of it, or a list containing any mix of those.
        This helper normalizes that input so downstream code can safely assume it
        has a TaskResult instance to work with.

        If previous_results is a list, items are inspected in reverse order
        (i.e., most-recent first) until one satisfies the validator callable.
        When validator is None, the first item is returned.

        Args:
            previous_results: The value passed by the upstream Celery task
            validator: Optional callable that returns True for a result that should be selected
            error_msg: Message to include in the raised ValueError when no suitable result can be found

        Returns:
            TaskResult: The selected result

        Raises:
            ValueError: If previous_results does not contain a suitable TaskResult
        """
        # Fast-path: a single object (TaskResult or dict)
        if not isinstance(previous_results, list):
            if isinstance(previous_results, dict):
                return TaskResult(**previous_results)
            assert isinstance(previous_results, TaskResult)
            return previous_results

        # It is a list - iterate from the end (latest first)
        for result in reversed(previous_results):
            if isinstance(result, dict):
                result = TaskResult(**result)
            if not isinstance(result, TaskResult):
                continue
            if validator is None or validator(result):
                return result

        # Nothing matched - raise so caller can handle
        self.logger.error(error_msg)
        raise ValueError(error_msg)


class PipelineTaskHandler(BaseTaskHandler):
    """Base class for task handlers that are part of a pipeline.

    These handlers typically receive a TaskResult from the previous task
    and return a modified TaskResult for the next task.
    """

    @abstractmethod
    def execute(self, previous_result: TaskResult, **kwargs) -> TaskResult:
        """Execute the task logic.

        Args:
            previous_result: Result from the previous task
            **kwargs: Additional task-specific parameters

        Returns:
            Updated TaskResult for the next task
        """
        pass
