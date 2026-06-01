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

import pytest
from multiprocessing import Event
from unittest.mock import Mock, patch

from mdx.analytics.core.app.scheduler.app_scheduler import Task
from mdx.analytics.core.app.app_base import BaseApp
from mdx.analytics.core.schema.config import AppConfig


class TestTaskFunctionality:
    """Test suite for Task class core functionality."""

    def setup_method(self):
        """Set up common test fixtures."""
        # Create mock app instance
        self.mock_app = Mock()
        self.poller = Mock()
        self.handler = Mock()
        self.shutdown_hook = Mock()
        
        # Set up mock app to return our mock methods
        self.mock_app.test_poller = self.poller
        self.mock_app.test_handler = self.handler
        self.mock_app.close = self.shutdown_hook
        
        # Create mock app class that properly inherits from BaseApp
        test_app = self.mock_app
        class MockApp(BaseApp):
            def __init__(self, config, calibration_path):
                # Don't call super().__init__() to avoid initialization logic
                pass
            
            def __new__(cls, *args, **kwargs):
                # Return the mock app instance instead of creating a new instance
                return test_app
        
        self.mock_app_class = MockApp
        
        # Create mock config
        self.mock_config = Mock(spec=AppConfig)
        
        self.task = Task(
            app_name="test_app",
            id=1,
            poller_name="test_poller",
            handler_name="test_handler",
            shutdown_hook_name="close",
            app_class=self.mock_app_class,
            config=self.mock_config,
            calibration_path="/test/calibration.json"
        )
        
        self.shutdown_flag = Event()

    def test_task_basic_properties(self):
        """Test task initialization and computed properties."""
        assert self.task.app_name == "test_app"
        assert self.task.id == 1
        assert self.task.poller_name == "test_poller"
        assert self.task.handler_name == "test_handler"
        assert self.task.shutdown_hook_name == "close"
        assert self.task.tname == "test_app.test_handler"
        assert self.task.tid == "test_app.test_handler-1"
        
        # Test lazy-loaded properties
        assert self.task.poller == self.poller
        assert self.task.handler == self.handler
        assert self.task.shutdown_hook == self.shutdown_hook

    @patch('mdx.analytics.core.app.scheduler.app_scheduler.ProcessingStats')
    @patch('mdx.analytics.core.app.scheduler.app_scheduler.BatchStats')
    @patch('mdx.analytics.core.app.scheduler.app_scheduler.logger')
    def test_task_execution_with_messages(self, mock_logger, mock_batch_stats_cls, mock_processing_stats_cls):
        """Test successful message processing flow."""
        # Setup mocks
        mock_messages = ["msg1", "msg2"]
        self.poller.return_value = mock_messages
        
        mock_batch_stats = Mock()
        mock_batch_stats.num_msgs = 2
        mock_batch_stats_cls.return_value = mock_batch_stats
        
        mock_stats = Mock()
        mock_stats.msgs_per_sec = 100.0
        mock_processing_stats_cls.return_value = mock_stats
        
        # Set shutdown after first iteration
        def set_shutdown(*args, **kwargs):
            self.shutdown_flag.set()
        self.handler.side_effect = set_shutdown
        
        # Execute task
        self.task(self.shutdown_flag)
        
        # Verify correct execution flow
        self.poller.assert_called_once_with(group_id_suffix="test_app.test_handler")
        self.handler.assert_called_once_with(mock_messages, mock_batch_stats)
        mock_stats.update.assert_called_once_with(2)
        self.shutdown_hook.assert_called_once()

    @patch('mdx.analytics.core.app.scheduler.app_scheduler.ProcessingStats')
    @patch('mdx.analytics.core.app.scheduler.app_scheduler.logger')
    def test_task_execution_no_messages(self, mock_logger, mock_processing_stats_cls):
        """Test behavior when no messages are received."""
        # Setup poller to return no messages
        self.poller.return_value = []
        
        # Set shutdown after first iteration
        def set_shutdown(*args, **kwargs):
            self.shutdown_flag.set()
        self.poller.side_effect = set_shutdown
        
        # Execute task
        self.task(self.shutdown_flag)
        
        # Verify handler was not called
        self.handler.assert_not_called()
        mock_logger.debug.assert_called_with(
            "[Batch 1] - No msgs fetched from source."
        )

    @patch('mdx.analytics.core.app.scheduler.app_scheduler.ProcessingStats')
    @patch('mdx.analytics.core.app.scheduler.app_scheduler.BatchStats')
    @patch('mdx.analytics.core.app.scheduler.app_scheduler.logger')
    def test_task_execution_handler_exception(self, mock_logger, mock_batch_stats_cls, mock_processing_stats_cls):
        """Test error handling during message processing."""
        # Setup mocks
        mock_messages = ["msg1"]
        self.poller.return_value = mock_messages
        
        mock_batch_stats = Mock()
        mock_batch_stats.num_msgs = 1
        mock_batch_stats_cls.return_value = mock_batch_stats
        
        # Make handler raise an exception
        self.handler.side_effect = [Exception("Test error"), None]
        
        # Set shutdown after first iteration
        def set_shutdown(*args, **kwargs):
            self.shutdown_flag.set()
        self.poller.side_effect = set_shutdown
        
        # Execute task
        self.task(self.shutdown_flag)
        
        # Verify cleanup was still performed
        self.shutdown_hook.assert_called_once()

    @patch('mdx.analytics.core.app.scheduler.app_scheduler.ProcessingStats')
    @patch('mdx.analytics.core.app.scheduler.app_scheduler.BatchStats')
    @patch('mdx.analytics.core.app.scheduler.app_scheduler.logger')
    def test_task_execution_batch_stats(self, mock_logger, mock_batch_stats_cls, mock_processing_stats_cls):
        """Test batch statistics tracking."""
        # Setup mocks
        mock_messages = ["msg1", "msg2"]
        self.poller.return_value = mock_messages
        
        mock_batch_stats = Mock()
        mock_batch_stats.num_msgs = 2
        mock_batch_stats_cls.return_value = mock_batch_stats
        
        mock_stats = Mock()
        mock_stats.msgs_per_sec = 100.0
        mock_processing_stats_cls.return_value = mock_stats
        
        # Set shutdown after first iteration
        def set_shutdown(*args, **kwargs):
            self.shutdown_flag.set()
        self.handler.side_effect = set_shutdown
        
        # Execute task
        self.task(self.shutdown_flag)
        
        # Verify stats creation and updates
        mock_processing_stats_cls.assert_called_once_with(worker_id=self.task.tid)
        mock_batch_stats_cls.assert_called_once_with(worker_id=self.task.tid, batch_id=1)
        mock_stats.update.assert_called_once_with(2)
        mock_logger.info.assert_any_call(
            f"Avg processing speed = {mock_stats.msgs_per_sec} msgs/sec"
        )

    @patch('mdx.analytics.core.app.scheduler.app_scheduler.logger')
    def test_task_cleanup(self, mock_logger):
        """Test proper cleanup on task completion."""
        # Set shutdown immediately
        self.shutdown_flag.set()
        
        # Execute task
        self.task(self.shutdown_flag)
        
        # Verify cleanup logging and shutdown hook execution
        mock_logger.info.assert_any_call("Task Cleaning up...")
        self.shutdown_hook.assert_called_once()
        mock_logger.info.assert_any_call("Task Cleanup completed...")

    def test_task_multiple_batch_processing(self):
        """Test processing of multiple batches before shutdown."""
        mock_messages = [["msg1", "msg2"], ["msg3", "msg4"], []]
        self.poller.side_effect = mock_messages
        
        # Set shutdown after processing all batches
        def set_shutdown_after_batches(*args, **kwargs):
            if self.poller.call_count == len(mock_messages):
                self.shutdown_flag.set()
        
        self.poller.side_effect = lambda *args, **kwargs: (
            set_shutdown_after_batches() or mock_messages[self.poller.call_count - 1]
        )
        
        with patch('mdx.analytics.core.app.scheduler.app_scheduler.ProcessingStats') as mock_stats_cls, \
             patch('mdx.analytics.core.app.scheduler.app_scheduler.BatchStats') as mock_batch_stats_cls:
            
            mock_stats = Mock()
            mock_stats.msgs_per_sec = 100.0
            mock_stats_cls.return_value = mock_stats
            
            mock_batch_stats = Mock()
            mock_batch_stats.num_msgs = 2
            mock_batch_stats_cls.return_value = mock_batch_stats
            
            self.task(self.shutdown_flag)
            
            # Verify multiple batch processing
            assert self.poller.call_count == 3
            assert self.handler.call_count == 2  # Only called for non-empty batches
            assert mock_stats.update.call_count == 2

    @pytest.mark.parametrize("exception_type", [
        ValueError,
        KeyError,
        RuntimeError
    ])
    def test_task_poller_exceptions(self, exception_type):
        """Test handling of various exceptions from poller."""
        self.poller.side_effect = exception_type("Test error")
        
        with patch('mdx.analytics.core.app.scheduler.app_scheduler.logger') as mock_logger:
            # Set shutdown after first iteration
            def set_shutdown(*args, **kwargs):
                self.shutdown_flag.set()
            self.poller.side_effect = [exception_type("Test error"), set_shutdown()]
            
            self.task(self.shutdown_flag)
            
            # Verify cleanup was performed
            self.shutdown_hook.assert_called_once()
            self.handler.assert_not_called()

    def test_task_concurrent_shutdown(self):
        """Test task behavior when shutdown occurs during message processing."""
        mock_messages = ["msg1", "msg2"]
        self.poller.return_value = mock_messages
        
        def set_shutdown_during_processing(*args, **kwargs):
            self.shutdown_flag.set()
            return None  # Complete handler processing
            
        self.handler.side_effect = set_shutdown_during_processing
        
        with patch('mdx.analytics.core.app.scheduler.app_scheduler.ProcessingStats') as mock_stats_cls, \
             patch('mdx.analytics.core.app.scheduler.app_scheduler.BatchStats') as mock_batch_stats_cls:
            
            mock_stats = Mock()
            mock_stats.msgs_per_sec = 100.0
            mock_stats_cls.return_value = mock_stats
            
            mock_batch_stats = Mock()
            mock_batch_stats.num_msgs = 2
            mock_batch_stats_cls.return_value = mock_batch_stats
            
            self.task(self.shutdown_flag)
            
            # Verify processing completed for current batch
            self.handler.assert_called_once()
            mock_stats.update.assert_called_once()
            self.shutdown_hook.assert_called_once()

    def test_task_zero_message_batch_stats(self):
        """Test stats handling for empty message batches."""
        self.poller.return_value = []
        
        with patch('mdx.analytics.core.app.scheduler.app_scheduler.ProcessingStats') as mock_stats_cls, \
             patch('mdx.analytics.core.app.scheduler.app_scheduler.BatchStats') as mock_batch_stats_cls, \
             patch('mdx.analytics.core.app.scheduler.app_scheduler.logger') as mock_logger:
            
            mock_stats = Mock()
            mock_stats_cls.return_value = mock_stats
            
            # Set shutdown after first iteration
            def set_shutdown(*args, **kwargs):
                self.shutdown_flag.set()
            self.poller.side_effect = set_shutdown
            
            self.task(self.shutdown_flag)
            
            # Verify no stats updates for empty batch
            mock_stats.update.assert_not_called()
            mock_batch_stats_cls.assert_not_called()
