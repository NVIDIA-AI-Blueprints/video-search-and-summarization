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

from unittest.mock import MagicMock, patch

import pytest

from src.lib.flywheel.cancellation import FlywheelCancelledError, check_cancellation


class TestFlywheelCancelledError:
    """Test cases for FlywheelCancelledError exception class."""

    def test_init_with_default_message(self):
        """Test FlywheelCancelledError initialization with default message."""
        flywheel_run_id = "test-run-123"
        error = FlywheelCancelledError(flywheel_run_id)

        assert error.flywheel_run_id == flywheel_run_id
        assert error.message == "Flywheel run was cancelled"
        assert str(error) == "Flywheel run was cancelled"

    def test_init_with_custom_message(self):
        """Test FlywheelCancelledError initialization with custom message."""
        flywheel_run_id = "test-run-456"
        custom_message = "Custom cancellation message"
        error = FlywheelCancelledError(flywheel_run_id, custom_message)

        assert error.flywheel_run_id == flywheel_run_id
        assert error.message == custom_message
        assert str(error) == custom_message

    def test_exception_inheritance(self):
        """Test that FlywheelCancelledError properly inherits from Exception."""
        error = FlywheelCancelledError("test-run")
        assert isinstance(error, Exception)

        # Test that it can be raised and caught
        with pytest.raises(FlywheelCancelledError) as exc_info:
            raise error

        assert exc_info.value.flywheel_run_id == "test-run"


class TestCheckCancellation:
    """Test cases for check_cancellation function."""

    @patch("src.lib.flywheel.cancellation.get_db_manager")
    def test_check_cancellation_not_cancelled(self, mock_get_db_manager):
        """Test check_cancellation when flywheel run is not cancelled."""
        # Setup mock
        mock_db_manager = MagicMock()
        mock_db_manager.is_flywheel_run_cancelled.return_value = False
        mock_get_db_manager.return_value = mock_db_manager

        flywheel_run_id = "test-run-active"

        # Should not raise an exception
        check_cancellation(flywheel_run_id)

        # Verify database check was called
        mock_db_manager.is_flywheel_run_cancelled.assert_called_once_with(flywheel_run_id)

    @patch("src.lib.flywheel.cancellation.get_db_manager")
    def test_check_cancellation_is_cancelled(self, mock_get_db_manager):
        """Test check_cancellation when flywheel run is cancelled."""
        # Setup mock
        mock_db_manager = MagicMock()
        mock_db_manager.is_flywheel_run_cancelled.return_value = True
        mock_get_db_manager.return_value = mock_db_manager

        flywheel_run_id = "test-run-cancelled"

        # Should raise FlywheelCancelledError
        with pytest.raises(FlywheelCancelledError) as exc_info:
            check_cancellation(flywheel_run_id)

        # Verify exception details
        error = exc_info.value
        assert error.flywheel_run_id == flywheel_run_id
        assert f"Flywheel run {flywheel_run_id} was cancelled" in error.message

        # Verify database check was called
        mock_db_manager.is_flywheel_run_cancelled.assert_called_once_with(flywheel_run_id)

    @patch("src.lib.flywheel.cancellation.logger")
    @patch("src.lib.flywheel.cancellation.get_db_manager")
    def test_check_cancellation_logs_message(self, mock_get_db_manager, mock_logger):
        """Test that check_cancellation logs a message when cancelled."""
        # Setup mock
        mock_db_manager = MagicMock()
        mock_db_manager.is_flywheel_run_cancelled.return_value = True
        mock_get_db_manager.return_value = mock_db_manager

        flywheel_run_id = "test-run-logged"
        expected_message = f"Flywheel run {flywheel_run_id} was cancelled"

        # Should raise FlywheelCancelledError and log message
        with pytest.raises(FlywheelCancelledError):
            check_cancellation(flywheel_run_id)

        # Verify logging was called with correct message
        mock_logger.info.assert_called_once_with(expected_message)

    @patch("src.lib.flywheel.cancellation.get_db_manager")
    def test_check_cancellation_with_empty_string_id(self, mock_get_db_manager):
        """Test check_cancellation with empty string flywheel run ID."""
        # Setup mock
        mock_db_manager = MagicMock()
        mock_db_manager.is_flywheel_run_cancelled.return_value = False
        mock_get_db_manager.return_value = mock_db_manager

        flywheel_run_id = ""

        # Should not raise an exception
        check_cancellation(flywheel_run_id)

        # Verify database check was called with empty string
        mock_db_manager.is_flywheel_run_cancelled.assert_called_once_with(flywheel_run_id)

    @patch("src.lib.flywheel.cancellation.get_db_manager")
    def test_check_cancellation_db_manager_exception(self, mock_get_db_manager):
        """Test check_cancellation when database manager raises an exception."""
        # Setup mock to raise exception
        mock_db_manager = MagicMock()
        mock_db_manager.is_flywheel_run_cancelled.side_effect = Exception("Database error")
        mock_get_db_manager.return_value = mock_db_manager

        flywheel_run_id = "test-run-db-error"

        # Should propagate the database exception
        with pytest.raises(Exception, match="Database error"):
            check_cancellation(flywheel_run_id)

        # Verify database check was attempted
        mock_db_manager.is_flywheel_run_cancelled.assert_called_once_with(flywheel_run_id)

    @patch("src.lib.flywheel.cancellation.get_db_manager")
    def test_check_cancellation_multiple_calls(self, mock_get_db_manager):
        """Test multiple calls to check_cancellation with same ID."""
        # Setup mock
        mock_db_manager = MagicMock()
        mock_db_manager.is_flywheel_run_cancelled.return_value = False
        mock_get_db_manager.return_value = mock_db_manager

        flywheel_run_id = "test-run-multiple"

        # Call multiple times
        check_cancellation(flywheel_run_id)
        check_cancellation(flywheel_run_id)
        check_cancellation(flywheel_run_id)

        # Verify database was checked each time
        assert mock_db_manager.is_flywheel_run_cancelled.call_count == 3
        for call in mock_db_manager.is_flywheel_run_cancelled.call_args_list:
            assert call[0][0] == flywheel_run_id


class TestIntegration:
    """Integration tests for cancellation functionality."""

    @patch("src.lib.flywheel.cancellation.get_db_manager")
    def test_full_cancellation_workflow(self, mock_get_db_manager):
        """Test the complete workflow from check to exception."""
        # Setup mock for cancelled run
        mock_db_manager = MagicMock()
        mock_db_manager.is_flywheel_run_cancelled.return_value = True
        mock_get_db_manager.return_value = mock_db_manager

        flywheel_run_id = "test-workflow-run"

        # Test the full workflow
        with pytest.raises(FlywheelCancelledError) as exc_info:
            check_cancellation(flywheel_run_id)

        # Verify all expected properties
        e = exc_info.value
        assert e.flywheel_run_id == flywheel_run_id
        assert flywheel_run_id in e.message
        assert "cancelled" in e.message.lower()

        # Verify it's the correct exception type
        assert isinstance(e, Exception)
        assert type(e).__name__ == "FlywheelCancelledError"

    def test_exception_properties_preservation(self):
        """Test that exception properties are preserved across raise/catch."""
        original_id = "preserved-test-run"
        original_message = "Preserved test message"

        # Create and raise exception
        original_error = FlywheelCancelledError(original_id, original_message)

        with pytest.raises(FlywheelCancelledError) as exc_info:
            raise original_error

        # Verify all properties are preserved
        caught_error = exc_info.value
        assert caught_error.flywheel_run_id == original_id
        assert caught_error.message == original_message
        assert str(caught_error) == original_message
        assert caught_error is original_error  # Should be the same object
