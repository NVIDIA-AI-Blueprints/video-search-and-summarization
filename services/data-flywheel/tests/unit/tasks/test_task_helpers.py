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

"""Tests for helper functions and utilities."""

from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from src.api.models import TaskResult
from src.lib.flywheel.cancellation import FlywheelCancelledError
from src.tasks.base import BaseTaskHandler
from src.tasks.tasks import (
    init_worker,
    worker_shutdown,
)
from tests.unit.tasks.conftest import (
    get_default_job_config,
    get_default_mlflow_config,
)


class TestHandler(BaseTaskHandler):
    """Concrete implementation of BaseTaskHandler for testing."""

    def execute(self, *args, **kwargs):
        """Dummy execute method."""
        pass


@pytest.fixture
def handler(mock_task_db):
    """Create a TestHandler instance for testing."""
    return TestHandler(mock_task_db)


class TestWorkerManagement:
    """Tests for worker initialization and shutdown functions."""

    def test_worker_shutdown_main_worker(self, mock_task_db):
        """Test worker shutdown signal handler for main worker."""
        with (
            patch("src.tasks.tasks.get_db_manager") as mock_get_db_manager,
            patch("src.tasks.tasks.CleanupManager") as mock_cleanup_manager_class,
        ):
            mock_task_db_manager = MagicMock()
            mock_get_db_manager.return_value = mock_task_db_manager

            mock_cleanup_manager = mock_cleanup_manager_class.return_value

            # Test with main_worker sender
            worker_shutdown(sig="SIGTERM", how="warm", exitcode=0, sender="main_worker_123")

            # Verify cleanup manager was created and called
            mock_cleanup_manager_class.assert_called_once_with(mock_task_db_manager)
            mock_cleanup_manager.cleanup_all_running_resources.assert_called_once()

    def test_worker_shutdown_non_main_worker(self, mock_task_db):
        """Test worker shutdown signal handler with non-main worker."""
        with (
            patch("src.tasks.tasks.get_db_manager") as mock_get_db_manager,
            patch("src.tasks.tasks.CleanupManager") as mock_cleanup_manager_class,
        ):
            # Test with non-main_worker sender
            worker_shutdown(sig="SIGTERM", how="warm", exitcode=0, sender="other_worker_123")

            # Verify cleanup manager was NOT called for non-main workers
            mock_get_db_manager.assert_not_called()
            mock_cleanup_manager_class.assert_not_called()

    def test_init_worker(self, mock_task_db):
        """Test worker process init signal handler."""
        with (
            patch("src.tasks.tasks.init_db") as mock_init_db,
            patch("src.tasks.tasks.get_db_manager") as mock_get_db_manager,
        ):
            mock_task_db_manager = MagicMock()
            mock_get_db_manager.return_value = mock_task_db_manager

            init_worker()

            # Verify database initialization
            mock_init_db.assert_called_once()
            mock_get_db_manager.assert_called_once()

    def test_worker_shutdown(self):
        """Test worker shutdown signal handler."""
        with (
            patch("src.tasks.tasks.get_db_manager") as mock_get_db_manager,
            patch("src.tasks.tasks.CleanupManager") as mock_cleanup_manager_class,
        ):
            mock_task_db_manager = MagicMock()
            mock_get_db_manager.return_value = mock_task_db_manager

            mock_cleanup_manager = mock_cleanup_manager_class.return_value

            # Test with main_worker sender
            worker_shutdown(sig="SIGTERM", how="warm", exitcode=0, sender="main_worker_123")

            # Verify cleanup manager was created and called
            mock_cleanup_manager_class.assert_called_once_with(mock_task_db_manager)
            mock_cleanup_manager.cleanup_all_running_resources.assert_called_once()


class TestStageSkipping:
    """Tests for stage skipping logic."""

    def test_should_skip_stage_no_error(self, handler):
        """Test should_skip_stage when previous result has no error."""
        previous_result = TaskResult(
            workload_id="test-workload",
            flywheel_run_id=str(ObjectId()),
            job_config=get_default_job_config(),
            client_id="test-client",
            error=None,
        )

        result = handler.should_skip_stage(previous_result, "test_stage")
        assert result is False

    def test_should_skip_stage_with_error(self, handler):
        """Test should_skip_stage when previous result has error."""
        previous_result = TaskResult(
            workload_id="test-workload",
            flywheel_run_id=str(ObjectId()),
            job_config=get_default_job_config(),
            client_id="test-client",
            error="Previous stage failed",
        )

        result = handler.should_skip_stage(previous_result, "test_stage")
        assert result is True

    def test_should_skip_stage_with_none(self, handler):
        """Test should_skip_stage with None input."""
        result = handler.should_skip_stage(None, "test_stage")
        assert result is False

    def test_should_skip_stage_none_result(self, handler):
        """Test should_skip_stage with None result."""
        result = handler.should_skip_stage(None, "test_stage")
        assert result is False

    def test_should_skip_stage_without_error(self, handler):
        """Test should_skip_stage when previous result has no error."""
        previous_result = TaskResult(
            workload_id="test-workload",
            flywheel_run_id=str(ObjectId()),
            job_config=get_default_job_config(),
            client_id="test-client",
            error=None,
        )

        result = handler.should_skip_stage(previous_result, "test_stage")
        assert result is False


class TestCancellationChecking:
    """Tests for cancellation checking functionality."""

    def test_check_cancellation_not_cancelled(self, handler):
        """Test check_cancellation when not cancelled."""
        flywheel_run_id = str(ObjectId())

        with patch("src.tasks.base.check_cancellation") as mock_check:
            mock_check.return_value = None  # No exception raised

            result = handler.check_cancellation(flywheel_run_id, raise_error=False)
            assert result is False

            result = handler.check_cancellation(flywheel_run_id, raise_error=True)
            assert result is False

    def test_check_cancellation_cancelled_raise_error(self, handler):
        """Test check_cancellation when cancelled and raising error."""
        flywheel_run_id = str(ObjectId())

        with patch("src.tasks.base.check_cancellation") as mock_check:
            mock_check.side_effect = FlywheelCancelledError(flywheel_run_id, "Cancelled")

            with pytest.raises(FlywheelCancelledError):
                handler.check_cancellation(flywheel_run_id, raise_error=True)

    def test_check_cancellation_cancelled_no_raise(self, handler):
        """Test check_cancellation when cancelled but not raising error."""
        flywheel_run_id = str(ObjectId())

        with patch("src.tasks.base.check_cancellation") as mock_check:
            mock_check.side_effect = FlywheelCancelledError(flywheel_run_id, "Cancelled")

            result = handler.check_cancellation(flywheel_run_id, raise_error=False)
            assert result is True

    def test_check_cancellation_cancelled_with_raise(self, handler):
        """Test check_cancellation when cancelled and raising error."""
        flywheel_run_id = str(ObjectId())

        with patch("src.tasks.base.check_cancellation") as mock_check:
            mock_check.side_effect = FlywheelCancelledError(flywheel_run_id, "Cancelled")

            with pytest.raises(FlywheelCancelledError):
                handler.check_cancellation(flywheel_run_id, raise_error=True)


class TestPreviousResultExtraction:
    """Tests for extracting previous results from various input formats."""

    def test_extract_previous_result_single_task_result(self, handler):
        """Test extract_previous_result with single TaskResult."""
        task_result = TaskResult(
            workload_id="test-workload",
            flywheel_run_id=str(ObjectId()),
            client_id="test-client",
            mlflow_config=get_default_mlflow_config(),
        )

        result = handler.extract_previous_result(task_result)
        assert result == task_result

    def test_extract_previous_result_dict(self, handler):
        """Test extract_previous_result with single dict."""
        task_dict = {
            "workload_id": "test-workload",
            "flywheel_run_id": str(ObjectId()),
            "client_id": "test-client",
        }

        result = handler.extract_previous_result(task_dict)
        assert isinstance(result, TaskResult)
        assert result.workload_id == "test-workload"

    def test_extract_previous_result_list_of_task_results(self, handler):
        """Test extract_previous_result with list of TaskResults (returns last one)."""
        previous_results = [
            TaskResult(
                workload_id="test-workload-1",
                client_id="test-client-1",
                flywheel_run_id=str(ObjectId()),
                job_config=get_default_job_config(),
            ),
            TaskResult(
                workload_id="test-workload-2",
                client_id="test-client-2",
                flywheel_run_id=str(ObjectId()),
                job_config=get_default_job_config(),
            ),
        ]

        result = handler.extract_previous_result(previous_results)
        # Should return the last item (most recent)
        assert result == previous_results[-1]
        assert result.workload_id == "test-workload-2"

    def test_extract_previous_result_list_of_dicts(self, handler):
        """Test extract_previous_result with list of dicts (returns last one)."""
        previous_results = [
            {
                "workload_id": "test-workload-1",
                "client_id": "test-client-1",
                "flywheel_run_id": str(ObjectId()),
            },
            {
                "workload_id": "test-workload-2",
                "client_id": "test-client-2",
                "flywheel_run_id": str(ObjectId()),
            },
        ]

        result = handler.extract_previous_result(previous_results)
        assert isinstance(result, TaskResult)
        # Should return the last item (most recent)
        assert result.workload_id == "test-workload-2"

    def test_extract_previous_result_with_validator(self, handler):
        """Test extract_previous_result with validator that finds no match."""
        task_result = TaskResult(
            workload_id="test-workload",
            flywheel_run_id=str(ObjectId()),
            client_id="test-client",
            job_config=get_default_job_config(),
        )

        results = [task_result]

        # Validator that never matches
        def validator(r):
            return r.workload_id == "non-existent"

        with pytest.raises(ValueError) as exc_info:
            handler.extract_previous_result(results, validator=validator, error_msg="Custom error")

        assert "Custom error" in str(exc_info.value)

    def test_extract_previous_result_with_validator_success(self, handler):
        """Test extract_previous_result with validator that finds a match."""
        task_result1 = TaskResult(
            workload_id="test-workload-1",
            flywheel_run_id=str(ObjectId()),
            job_config=get_default_job_config(),
            client_id="test-client",
        )
        task_result2 = TaskResult(
            workload_id="test-workload-2",
            flywheel_run_id=str(ObjectId()),
            job_config=get_default_job_config(),
            client_id="test-client",
        )

        results = [task_result1, task_result2]

        # Validator that selects workload-2
        def validator(r):
            return r.workload_id == "test-workload-2"

        result = handler.extract_previous_result(results, validator=validator)
        assert result == task_result2

    def test_extract_previous_result_invalid_type(self, handler):
        """Test extract_previous_result with invalid input type."""
        # Pass an invalid type (not TaskResult, dict, or list)
        invalid_input = "invalid"

        with pytest.raises(AssertionError):
            handler.extract_previous_result(invalid_input)

    def test_extract_previous_result_empty_list(self, handler):
        """Test extract_previous_result with empty list."""
        with pytest.raises(ValueError) as exc_info:
            handler.extract_previous_result([])

        assert "No valid TaskResult found" in str(exc_info.value)

    def test_extract_previous_result_single_dict(self, handler):
        """Test extract_previous_result with single dict."""
        task_dict = {
            "workload_id": "test-workload",
            "flywheel_run_id": str(ObjectId()),
            "client_id": "test-client",
        }

        result = handler.extract_previous_result(task_dict)
        assert isinstance(result, TaskResult)
        assert result.workload_id == "test-workload"

    def test_extract_previous_result_list_with_validator(self, handler):
        """Test extract_previous_result with list and validator."""
        task_result1 = TaskResult(
            workload_id="test-workload-1",
            flywheel_run_id=str(ObjectId()),
            job_config=get_default_job_config(),
            client_id="test-client",
        )
        task_result2 = TaskResult(
            workload_id="test-workload-2",
            flywheel_run_id=str(ObjectId()),
            job_config=get_default_job_config(),
            client_id="test-client",
        )

        results = [task_result1, task_result2]

        # Validator that selects workload-2
        def validator(r):
            return r.workload_id == "test-workload-2"

        result = handler.extract_previous_result(results, validator=validator)
        assert result == task_result2

    def test_extract_previous_result_list_no_validator(self, handler):
        """Test extract_previous_result with list and no validator."""
        task_result1 = TaskResult(
            workload_id="test-workload-1",
            flywheel_run_id=str(ObjectId()),
            job_config=get_default_job_config(),
            client_id="test-client",
        )
        task_result2 = TaskResult(
            workload_id="test-workload-2",
            flywheel_run_id=str(ObjectId()),
            job_config=get_default_job_config(),
            client_id="test-client",
        )

        results = [task_result1, task_result2]

        # Should return the last item (most recent)
        result = handler.extract_previous_result(results)
        assert result == task_result2

    def test_extract_previous_result_list_with_dicts(self, handler):
        """Test extract_previous_result with list containing dicts."""
        task_dict1 = {
            "workload_id": "test-workload-1",
            "flywheel_run_id": str(ObjectId()),
            "client_id": "test-client",
        }
        task_dict2 = {
            "workload_id": "test-workload-2",
            "flywheel_run_id": str(ObjectId()),
            "client_id": "test-client",
        }

        results = [task_dict1, task_dict2]

        result = handler.extract_previous_result(results)
        assert isinstance(result, TaskResult)
        assert result.workload_id == "test-workload-2"

    def test_extract_previous_result_no_valid_result(self, handler):
        """Test extract_previous_result when no valid result found."""
        # List with invalid items
        results = ["invalid", 123, None]

        with pytest.raises(ValueError) as exc_info:
            handler.extract_previous_result(results)

        assert "No valid TaskResult found" in str(exc_info.value)

    def test_extract_previous_result_validator_no_match(self, handler):
        """Test extract_previous_result when validator finds no match."""
        task_result = TaskResult(
            workload_id="test-workload",
            flywheel_run_id=str(ObjectId()),
            client_id="test-client",
            job_config=get_default_job_config(),
        )

        results = [task_result]

        # Validator that never matches
        def validator(r):
            return r.workload_id == "non-existent"

        with pytest.raises(ValueError) as exc_info:
            handler.extract_previous_result(results, validator=validator, error_msg="Custom error")

        assert "Custom error" in str(exc_info.value)
