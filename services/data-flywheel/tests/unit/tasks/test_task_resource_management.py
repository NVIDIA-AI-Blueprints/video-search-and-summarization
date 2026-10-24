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

"""Tests for resource management tasks."""

from unittest.mock import patch

import pytest
from bson import ObjectId

from src.tasks.tasks import cancel_job_resources, delete_job_resources


class TestResourceDeletion:
    """Tests for resource deletion functionality."""

    def test_delete_job_resources_success(self, mock_task_db, mock_init_db):
        """Test successful deletion of job resources."""
        job_id = str(ObjectId())

        with patch("src.tasks.tasks.FlywheelJobManager") as mock_cleanup_class:
            # Configure mock instance
            mock_cleanup = mock_cleanup_class.return_value

            # Execute the task
            delete_job_resources(job_id)

            # Verify the cleanup manager was initialized with the db manager
            mock_cleanup_class.assert_called_once_with(mock_task_db)

            # Verify delete_job was called with correct job_id
            mock_cleanup.delete_job.assert_called_once_with(job_id)

    def test_delete_job_resources_failure(self, mock_task_db, mock_init_db):
        """Test failure of job resource deletion."""
        job_id = str(ObjectId())

        with patch("src.tasks.tasks.FlywheelJobManager") as mock_cleanup_class:
            # Configure mock instance to raise an exception
            mock_cleanup = mock_cleanup_class.return_value
            mock_cleanup.delete_job.side_effect = Exception("Failed to delete job")

            # Execute the task and verify it raises the exception
            with pytest.raises(Exception) as exc_info:
                delete_job_resources(job_id)

            assert "Failed to delete job" in str(exc_info.value)

            # Verify the cleanup manager was initialized with the db manager
            mock_cleanup_class.assert_called_once_with(mock_task_db)

            # Verify delete_job was called with correct job_id
            mock_cleanup.delete_job.assert_called_once_with(job_id)


class TestResourceCancellation:
    """Tests for resource cancellation functionality."""

    def test_cancel_job_resources_success(self, mock_task_db, mock_init_db):
        """Test successful cancellation of job resources."""
        job_id = str(ObjectId())

        with patch("src.tasks.tasks.FlywheelJobManager") as mock_job_manager_class:
            # Configure mock instance
            mock_job_manager = mock_job_manager_class.return_value

            # Execute the task
            cancel_job_resources(job_id)

            # Verify the job manager was initialized with the db manager
            mock_job_manager_class.assert_called_once_with(mock_task_db)

            # Verify cancel_job was called with correct job_id
            mock_job_manager.cancel_job.assert_called_once_with(job_id)

    def test_cancel_job_resources_failure(self, mock_task_db, mock_init_db):
        """Test failure of job resource cancellation."""
        job_id = str(ObjectId())

        with patch("src.tasks.tasks.FlywheelJobManager") as mock_job_manager_class:
            # Configure mock instance to raise an exception
            mock_job_manager = mock_job_manager_class.return_value
            mock_job_manager.cancel_job.side_effect = Exception("Failed to cancel job")

            # Execute the task and verify it raises the exception
            with pytest.raises(Exception) as exc_info:
                cancel_job_resources(job_id)

            assert "Failed to cancel job" in str(exc_info.value)

            # Verify the job manager was initialized with the db manager
            mock_job_manager_class.assert_called_once_with(mock_task_db)

            # Verify cancel_job was called with correct job_id
            mock_job_manager.cancel_job.assert_called_once_with(job_id)
