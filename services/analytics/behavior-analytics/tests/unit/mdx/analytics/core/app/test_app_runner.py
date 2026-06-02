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

import json
import os
import pytest
import signal
from unittest.mock import Mock, patch
from datetime import datetime

from mdx.analytics.core.app.app_runner import AppRunner, run
from mdx.analytics.core.app.app_base import BaseApp, Processor
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import StreamMessage, Notification


class MockBaseApp(BaseApp):
    """Mock concrete implementation of BaseApp for testing purposes."""
    
    def __init__(self, config: AppConfig, calibration_path: str):
        # Skip the parent constructor to avoid initialization issues
        self.config = config
        self.calibration_path = calibration_path
        self.processors = []
        self.close_called = False
    
    def get_processors(self) -> list[Processor]:
        """Return mock processors for testing."""
        return self.processors
    
    def close(self) -> None:
        """Mock close method."""
        self.close_called = True


class TestRunFunctionality:
    """Test suite for the run function functionality."""
    
    @patch('sys.argv', ['test_app.py', '--config'])
    def test_run_exits_with_missing_config_argument(self):
        """Test that run exits when config argument is missing."""
        with pytest.raises(SystemExit):
            run(MockBaseApp)
    
    @patch('sys.argv', ['test_app.py', '--help'])
    def test_run_shows_help_when_requested(self):
        """Test that run shows help when --help is used."""
        with pytest.raises(SystemExit):
            run(MockBaseApp)

    @patch('sys.argv', [
        'test_app.py',
        '--config', 'tests/unit/resources/test_config.json',
        '--log', 'rel/log.json',
    ])
    @patch('mdx.analytics.core.app.app_runner.AppRunner')
    @patch('mdx.analytics.core.app.app_runner.setup_logging')
    def test_run_resolves_log_path_to_absolute(self, mock_setup_logging, mock_app_runner_cls):
        """Spawn workers inherit CWD but may run from a different effective root,
        so the parent must hand them an absolute log-config path."""
        run(MockBaseApp)

        mock_setup_logging.assert_called_once()
        log_path = mock_setup_logging.call_args[0][0]
        assert os.path.isabs(log_path)
        assert log_path.endswith('rel/log.json')

        # AppRunner must also receive the absolute path to forward to Tasks.
        forwarded_log = mock_app_runner_cls.call_args[0][3]
        assert os.path.isabs(forwarded_log)


class TestAppRunnerInitialization:
    """Test suite for AppRunner initialization functionality."""
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    def test_init_sets_up_all_components_correctly(self, mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener, mock_load_config):
        """Test that __init__ correctly initializes all components."""
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        mock_scheduler = Mock()
        mock_scheduler_cls.return_value = mock_scheduler
        
        runner = AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Verify all components were initialized
        assert runner._app_cls == MockBaseApp
        assert runner._calibration_path == 'calibration.json'
        assert runner._config == mock_config
        assert runner._calibration_listener == mock_cal_listener
        assert runner._scheduler == mock_scheduler
        assert runner._tasks == []
        
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    def test_init_calls_load_methods_with_correct_paths(self, mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener, mock_load_config):
        """Test that __init__ calls load methods with correct file paths."""
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        
        AppRunner(MockBaseApp, 'test_config.json', 'test_calibration.json')
        
        # Verify load methods were called with correct paths
        mock_load_config.assert_called_once_with('test_config.json')
        mock_load_cal_listener.assert_called_once()


class TestAppRunnerStart:
    """Test suite for AppRunner start method functionality."""
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    def test_start_creates_app_and_processors_correctly(self, mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener, mock_load_config):
        """Test that start creates app instance and processes all processors."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        mock_scheduler = Mock()
        mock_scheduler_cls.return_value = mock_scheduler
        
        # Create mock processors
        mock_poller = Mock()
        mock_poller.__name__ = 'test_handler_poller'
        mock_handler = Mock()
        mock_handler.__name__ = 'test_handler'
        mock_processor = Processor(poller=mock_poller, handler=mock_handler, num_workers=2)
        
        # Create mock app
        mock_app = Mock(spec=MockBaseApp)
        mock_app.get_processors.return_value = [mock_processor]
        mock_close = Mock()
        mock_close.__name__ = 'close'
        mock_app.close = mock_close
        
        with patch.object(MockBaseApp, '__new__', return_value=mock_app):
            runner = AppRunner(MockBaseApp, 'config.json', 'calibration.json')
            runner.start()
        
        # Verify app was created correctly
        mock_app.get_processors.assert_called_once()
        
        # Verify tasks were created (2 workers for the processor)
        assert len(runner._tasks) == 2
        for i, task in enumerate(runner._tasks):
            assert task.app_name == 'MockBaseApp'
            assert task.id == i
            assert task.poller_name == 'test_handler_poller'
            assert task.handler_name == 'test_handler'
            assert task.shutdown_hook_name == 'close'
            assert task.app_class == MockBaseApp
            assert task.config == mock_config
            assert task.calibration_path == 'calibration.json'

        # Verify signal handlers were set up
        mock_signal.assert_any_call(signal.SIGINT, runner._sig_shutdown)
        mock_signal.assert_any_call(signal.SIGTERM, runner._sig_shutdown)

        # Verify calibration listener was started
        mock_cal_listener.start.assert_called_once()
        
        # Verify scheduler was called
        mock_scheduler.submit.assert_called_once_with(runner._tasks)
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.logger')
    def test_start_handles_when_no_processors_and_calls_close(self, mock_logger, mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener, mock_load_config):
        """Test that start handles when no processors are registered and calls close."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        mock_scheduler = Mock()
        mock_scheduler_cls.return_value = mock_scheduler
        
        # Create mock app with no processors
        mock_app = Mock(spec=MockBaseApp)
        mock_app.get_processors.return_value = []
        
        with patch.object(MockBaseApp, '__new__', return_value=mock_app):
            runner = AppRunner(MockBaseApp, 'config.json', 'calibration.json')
            
            # Mock the close method to avoid recursion
            with patch.object(runner, 'close') as mock_close:
                runner.start()
                
                # Verify error was logged and close was called
                mock_logger.error.assert_called_once()
                mock_close.assert_called_once()
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.logger')
    def test_start_handles_exception_and_calls_close(self, mock_logger, mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener, mock_load_config):
        """Test that start handles exceptions and calls close."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        mock_scheduler = Mock()
        mock_scheduler_cls.return_value = mock_scheduler
        
        # Create mock app that raises an exception
        mock_app = Mock(spec=MockBaseApp)
        mock_app.get_processors.side_effect = Exception("Test exception")
        
        with patch.object(MockBaseApp, '__new__', return_value=mock_app):
            runner = AppRunner(MockBaseApp, 'config.json', 'calibration.json')
            
            # Mock the close method to avoid recursion
            with patch.object(runner, 'close') as mock_close:
                runner.start()
                
                # Verify error was logged and close was called
                mock_logger.error.assert_called_once()
                mock_close.assert_called_once()


class TestAppRunnerClose:
    """Test suite for AppRunner close method functionality."""
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    def test_close_shuts_down_calibration_listener(self, mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener, mock_load_config):
        """Test that close shuts down calibration listener."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        
        runner = AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        runner.close()
        
        # Verify calibration listener was closed
        mock_cal_listener.close.assert_called_once()
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    def test_close_shuts_down_all_tasks(self, mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener, mock_load_config):
        """Test that close shuts down all tasks."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        mock_scheduler = Mock()
        mock_scheduler_cls.return_value = mock_scheduler
        
        runner = AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Add mock task
        mock_task = Mock()
        runner._tasks = [mock_task]
        
        runner.close()

        # Verify scheduler shutdown was called
        mock_scheduler.shutdown.assert_called_once()
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    def test_close_handles_none_calibration_listener(self, mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener, mock_load_config):
        """Test that close handles None calibration listener gracefully."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_load_cal_listener.return_value = None
        
        runner = AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Should not raise an exception
        runner.close()


class TestAppRunnerSignalHandling:
    """Test suite for AppRunner signal handling functionality."""
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.logger')
    def test_sig_shutdown_logs_signal_and_calls_close(self, mock_logger, mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener, mock_load_config):
        """Test that _sig_shutdown logs signal and calls close."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        
        runner = AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        with patch.object(runner, 'close') as mock_close:
            runner._sig_shutdown(signal.SIGINT, None)
            
            # Verify signal was logged and close was called
            # The last call should be the shutdown signal log
            mock_logger.info.assert_called_with('Exiting app, shutdown signal SIGINT received.')
            mock_close.assert_called_once()
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.logger')
    def test_sig_shutdown_handles_sigterm(self, mock_logger, mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener, mock_load_config):
        """Test that _sig_shutdown handles SIGTERM signal."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        
        runner = AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        with patch.object(runner, 'close') as mock_close:
            runner._sig_shutdown(signal.SIGTERM, None)
            
            # Verify signal was logged and close was called
            # The last call should be the shutdown signal log
            mock_logger.info.assert_called_with('Exiting app, shutdown signal SIGTERM received.')
            mock_close.assert_called_once()


class TestAppRunnerConfigLoading:
    """Test suite for AppRunner config loading functionality."""
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.load_json_from_file')
    @patch('mdx.analytics.core.app.app_runner.exit')
    @patch('mdx.analytics.core.app.app_runner.logger')
    def test_load_config_exits_when_file_not_found(self, mock_logger, mock_exit, 
                                                   mock_load_json, 
                                                   mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener):
        """Test that _load_config exits when config file is not found."""
        # Setup mocks
        mock_load_json.side_effect = FileNotFoundError("File not found")
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        
        # The exit call will prevent AppRunner from being created
        mock_exit.side_effect = SystemExit(1)
        
        with pytest.raises(SystemExit):
            AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Verify error was logged and exit was called
        mock_logger.error.assert_called_once()
        assert "Unexpected error loading config" in mock_logger.error.call_args[0][0]
        mock_exit.assert_called_once_with(1)
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.load_json_from_file')
    @patch('mdx.analytics.core.app.app_runner.exit')
    @patch('mdx.analytics.core.app.app_runner.logger')
    def test_load_config_exits_when_path_is_not_file(self, mock_logger, mock_exit, 
                                                    mock_load_json, 
                                                    mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener):
        """Test that _load_config exits when path exists but is not a file."""
        # Setup mocks - simulate path is a directory, not a file
        mock_load_json.side_effect = IsADirectoryError("Is a directory: 'config.json'")
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        
        # The exit call will prevent AppRunner from being created
        mock_exit.side_effect = SystemExit(1)
        
        with pytest.raises(SystemExit):
            AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Verify error was logged and exit was called
        mock_logger.error.assert_called_once()
        assert "Unexpected error loading config" in mock_logger.error.call_args[0][0]
        mock_exit.assert_called_once_with(1)
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.load_json_from_file')
    @patch('mdx.analytics.core.app.app_runner.exit')
    @patch('mdx.analytics.core.app.app_runner.logger')
    def test_load_config_exits_when_json_is_invalid(self, mock_logger, mock_exit, 
                                                   mock_load_json, 
                                                   mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener):
        """Test that _load_config exits when config file contains invalid JSON."""
        # Setup mocks - simulate invalid JSON syntax
        mock_load_json.side_effect = json.JSONDecodeError("Invalid JSON", "doc", 0)
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        
        # The exit call will prevent AppRunner from being created
        mock_exit.side_effect = SystemExit(1)
        
        with pytest.raises(SystemExit):
            AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Verify error was logged and exit was called
        mock_logger.error.assert_called_once()
        assert "contains invalid JSON" in mock_logger.error.call_args[0][0]
        mock_exit.assert_called_once_with(1)
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_calibration_listener')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.load_json_from_file')
    @patch('mdx.analytics.core.app.app_runner.exit')
    @patch('mdx.analytics.core.app.app_runner.logger')
    def test_load_config_exits_when_config_has_invalid_structure(self, mock_logger, mock_exit, 
                                                                mock_load_json, 
                                                                mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                  mock_load_cal_listener):
        """Test that _load_config exits when config has invalid structure."""
        
        # Setup mocks - return data that will fail validation
        # Provide an invalid type for a known field to trigger ValidationError
        mock_load_json.return_value = {"kafka": "invalid_string_instead_of_dict"}
        mock_cal_listener = Mock()
        mock_load_cal_listener.return_value = mock_cal_listener
        
        # The exit call will prevent AppRunner from being created
        mock_exit.side_effect = SystemExit(1)
        
        with pytest.raises(SystemExit):
            AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Verify error was logged and exit was called
        mock_logger.error.assert_called_once()
        assert "has invalid structure" in mock_logger.error.call_args[0][0]
        mock_exit.assert_called_once_with(1)


class TestAppRunnerCalibrationListener:
    """Test suite for AppRunner calibration listener functionality."""
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.get_source')
    @patch('mdx.analytics.core.app.app_runner.CalibrationListener')
    def test_load_calibration_listener_creates_listener_correctly(self, mock_cal_listener_cls, 
                                                                 mock_get_source, mock_signal, 
                                                                 mock_scheduler_cls, mock_load_config_listener,
                                                   mock_load_config):
        """Test that _load_calibration_listener creates CalibrationListener correctly."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_source = Mock()
        mock_get_source.return_value = mock_source
        mock_cal_listener = Mock()
        mock_cal_listener_cls.return_value = mock_cal_listener
        
        AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Verify CalibrationListener was created with correct arguments
        mock_get_source.assert_called_once_with(mock_config)
        mock_cal_listener_cls.assert_called_once()
        
        # Verify the config was passed correctly
        call_args = mock_cal_listener_cls.call_args
        assert call_args[0][0] == mock_config
        assert callable(call_args[0][1])  # Second argument should be a callable
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.get_source')
    @patch('mdx.analytics.core.app.app_runner.CalibrationListener')
    def test_read_notifications_function_polls_source_correctly(self, mock_cal_listener_cls, 
                                                               mock_get_source, mock_signal, 
                                                               mock_scheduler_cls, mock_load_config_listener,
                                                   mock_load_config):
        """Test that the _read_notifications function polls source correctly."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_source = Mock()
        mock_get_source.return_value = mock_source
        mock_cal_listener = Mock()
        mock_cal_listener_cls.return_value = mock_cal_listener
        
        AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Get the _read_notifications function
        call_args = mock_cal_listener_cls.call_args
        read_notifications_func = call_args[0][1]
        
        # Call the function
        read_notifications_func()
        
        # Verify source.poll was called correctly
        mock_source.poll.assert_called_once()
        call_args = mock_source.poll.call_args
        assert call_args[1]['src_key'] == 'notification'
        assert callable(call_args[1]['msg_deserializer'])
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.get_source')
    @patch('mdx.analytics.core.app.app_runner.CalibrationListener')
    def test_msg_deserializer_handles_valid_calibration_message(self, mock_cal_listener_cls, 
                                                              mock_get_source, mock_signal, 
                                                              mock_scheduler_cls, mock_load_config_listener,
                                                   mock_load_config):
        """Test that msg_deserializer handles valid calibration message."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_source = Mock()
        mock_get_source.return_value = mock_source
        mock_cal_listener = Mock()
        mock_cal_listener_cls.return_value = mock_cal_listener
        
        AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Get the _read_notifications function and then the msg_deserializer
        call_args = mock_cal_listener_cls.call_args
        read_notifications_func = call_args[0][1]
        
        # Mock the source.poll to capture the msg_deserializer
        captured_deserializer = None
        def capture_deserializer(**kwargs):
            nonlocal captured_deserializer
            captured_deserializer = kwargs['msg_deserializer']
            return []
        
        mock_source.poll.side_effect = capture_deserializer
        read_notifications_func()
        
        # Create a valid calibration message
        msg = StreamMessage(
            key=b'calibration',
            value=b'{"sensor": "test"}',
            headers={
                'event.type': b'upsert',
                'timestamp': b'2023-01-01T00:00:00Z'
            }
        )
        
        # Call the deserializer
        assert captured_deserializer is not None
        result = captured_deserializer(msg)
        
        # Verify result is a Notification object
        assert isinstance(result, Notification)
        assert result.event_type == 'upsert'
        assert result.timestamp == datetime.fromisoformat('2023-01-01T00:00:00Z')
        assert result.message == '{"sensor": "test"}'
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.get_source')
    @patch('mdx.analytics.core.app.app_runner.CalibrationListener')
    def test_msg_deserializer_returns_none_for_invalid_key(self, mock_cal_listener_cls, 
                                                          mock_get_source, mock_signal, 
                                                          mock_scheduler_cls, mock_load_config_listener,
                                                   mock_load_config):
        """Test that msg_deserializer returns None for invalid key."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_source = Mock()
        mock_get_source.return_value = mock_source
        mock_cal_listener = Mock()
        mock_cal_listener_cls.return_value = mock_cal_listener
        
        AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Get the msg_deserializer
        call_args = mock_cal_listener_cls.call_args
        read_notifications_func = call_args[0][1]
        
        captured_deserializer = None
        def capture_deserializer(**kwargs):
            nonlocal captured_deserializer
            captured_deserializer = kwargs['msg_deserializer']
            return []
        
        mock_source.poll.side_effect = capture_deserializer
        read_notifications_func()
        
        # Create message with invalid key
        msg = StreamMessage(
            key=b'invalid_key',
            value=b'{"sensor": "test"}',
            headers={
                'event.type': b'upsert',
                'timestamp': b'2023-01-01T00:00:00Z'
            }
        )
        
        # Call the deserializer
        assert captured_deserializer is not None
        result = captured_deserializer(msg)
        
        # Verify result is None
        assert result is None
    
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.get_source')
    @patch('mdx.analytics.core.app.app_runner.CalibrationListener')
    def test_msg_deserializer_returns_none_for_missing_headers(self, mock_cal_listener_cls, 
                                                             mock_get_source, mock_signal, 
                                                             mock_scheduler_cls, mock_load_config_listener,
                                                   mock_load_config):
        """Test that msg_deserializer returns None for missing headers."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_source = Mock()
        mock_get_source.return_value = mock_source
        mock_cal_listener = Mock()
        mock_cal_listener_cls.return_value = mock_cal_listener
        
        AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Get the msg_deserializer
        call_args = mock_cal_listener_cls.call_args
        read_notifications_func = call_args[0][1]
        
        captured_deserializer = None
        def capture_deserializer(**kwargs):
            nonlocal captured_deserializer
            captured_deserializer = kwargs['msg_deserializer']
            return []
        
        mock_source.poll.side_effect = capture_deserializer
        read_notifications_func()
        
        # Create message with missing headers
        msg = StreamMessage(
            key=b'calibration',
            value=b'{"sensor": "test"}',
            headers={'event.type': b'upsert'}  # Missing timestamp
        )
        
        # Call the deserializer
        assert captured_deserializer is not None
        result = captured_deserializer(msg)
        
        # Verify result is None
        assert result is None
    
    @pytest.mark.parametrize("event_type,expected_result", [
        ("upsert-all", True),
        ("upsert", True),
        ("delete", True),
        ("invalid", False),
    ])
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config')
    @patch('mdx.analytics.core.app.app_runner.AppRunner._load_config_listener')
    @patch('mdx.analytics.core.app.app_runner.MultiprocessingScheduler')
    @patch('signal.signal')
    @patch('mdx.analytics.core.app.app_runner.get_source')
    @patch('mdx.analytics.core.app.app_runner.CalibrationListener')
    def test_msg_deserializer_validates_event_types(self, mock_cal_listener_cls, mock_get_source,
                                                   mock_signal, mock_scheduler_cls, mock_load_config_listener,
                                                   mock_load_config, event_type, expected_result):
        """Test that msg_deserializer validates event types correctly."""
        # Setup mocks
        mock_config = Mock(spec=AppConfig)
        mock_load_config.return_value = mock_config
        mock_source = Mock()
        mock_get_source.return_value = mock_source
        mock_cal_listener = Mock()
        mock_cal_listener_cls.return_value = mock_cal_listener
        
        AppRunner(MockBaseApp, 'config.json', 'calibration.json')
        
        # Get the msg_deserializer
        call_args = mock_cal_listener_cls.call_args
        read_notifications_func = call_args[0][1]
        
        captured_deserializer = None
        def capture_deserializer(**kwargs):
            nonlocal captured_deserializer
            captured_deserializer = kwargs['msg_deserializer']
            return []
        
        mock_source.poll.side_effect = capture_deserializer
        read_notifications_func()
        
        # Create message with different event types
        msg = StreamMessage(
            key=b'calibration',
            value=b'{"sensor": "test"}',
            headers={
                'event.type': event_type.encode(),
                'timestamp': b'2023-01-01T00:00:00Z'
            }
        )
        
        # Call the deserializer
        assert captured_deserializer is not None
        result = captured_deserializer(msg)
        
        # Verify result based on expected outcome
        if expected_result:
            assert isinstance(result, Notification)
            assert result.event_type == event_type
        else:
            assert result is None
