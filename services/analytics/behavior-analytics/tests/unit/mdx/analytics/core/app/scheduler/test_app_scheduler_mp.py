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

import multiprocessing as mp
import pytest
import signal
import traceback
from unittest.mock import Mock, patch, call
from multiprocessing import Event, Pipe, Process
from multiprocessing.connection import Connection
from multiprocessing.synchronize import Event as EventType

import mdx.analytics.core.app.scheduler.app_scheduler_mp as app_scheduler_mp
from mdx.analytics.core.app.scheduler.app_scheduler_mp import (
    MultiprocessingScheduler,
    _sig_shutdown,
    _target,
)
from mdx.analytics.core.app.scheduler.app_scheduler import Task
from mdx.analytics.core.app.app_base import BaseApp
from mdx.analytics.core.schema.config import AppConfig


class MockBaseApp(BaseApp):
    """Mock BaseApp for testing purposes."""

    def __init__(self, config, calibration_path):
        self.config = config
        self.calibration_path = calibration_path
        self.closed = False

    def mock_poller(self, group_id_suffix=None):
        """Mock poller method."""
        return []

    def mock_handler(self, messages, batch_stats):
        """Mock handler method."""

    def close(self):
        """Mock close method."""
        self.closed = True


@pytest.fixture
def mock_config():
    """Create a mock AppConfig."""
    return Mock(spec=AppConfig)


@pytest.fixture
def mock_task(mock_config):
    """Create a mock Task for testing."""
    return Task(
        app_name="test_app",
        id=1,
        poller_name="mock_poller",
        handler_name="mock_handler",
        shutdown_hook_name="close",
        app_class=MockBaseApp,
        config=mock_config,
        calibration_path="/mock/path"
    )


def _make_scheduler_with_mock_ctx() -> tuple[MultiprocessingScheduler, Mock]:
    """Build a scheduler whose spawn context is a Mock so submit() can be tested
    without actually creating subprocesses, pipes, or events.
    """
    scheduler = MultiprocessingScheduler()
    scheduler._ctx = Mock()
    return scheduler, scheduler._ctx


def _wire_default_ctx_mocks(ctx_mock: Mock, pid: int = 12345) -> tuple[Mock, Mock, Mock, Mock]:
    """Configure the mock context with defaults that let submit() succeed.
    Returns (mock_event, mock_main_conn, mock_task_conn, mock_process).
    """
    mock_event = Mock(spec=EventType)
    ctx_mock.Event.return_value = mock_event

    mock_main_conn = Mock(spec=Connection)
    mock_task_conn = Mock(spec=Connection)
    ctx_mock.Pipe.return_value = (mock_main_conn, mock_task_conn)

    mock_process = Mock(spec=Process)
    mock_process.pid = pid
    ctx_mock.Process.return_value = mock_process

    return mock_event, mock_main_conn, mock_task_conn, mock_process


@pytest.fixture
def scheduler():
    """Create a MultiprocessingScheduler instance (real spawn context)."""
    return MultiprocessingScheduler()


class TestMultiprocessingSchedulerInit:
    """Test MultiprocessingScheduler initialization."""

    def test_init_creates_empty_dictionaries(self):
        """Test that __init__ creates empty process dictionary and event list."""
        scheduler = MultiprocessingScheduler()

        assert scheduler._processes == {}
        assert isinstance(scheduler._processes, dict)
        assert scheduler._shutdown_events == []
        assert isinstance(scheduler._shutdown_events, list)

    def test_init_uses_spawn_context(self):
        """Test that __init__ creates a spawn-mode multiprocessing context."""
        scheduler = MultiprocessingScheduler()

        assert scheduler._ctx.get_start_method() == "spawn"


class TestMultiprocessingSchedulerSubmit:
    """Test MultiprocessingScheduler submit method."""

    def test_submit_single_task_success(self, mock_task):
        """Test successful submission of a single task."""
        scheduler, ctx = _make_scheduler_with_mock_ctx()
        mock_event, mock_main_conn, mock_task_conn, mock_process = _wire_default_ctx_mocks(ctx)

        with patch.object(app_scheduler_mp.mp, "active_children", return_value=[]):
            scheduler.submit([mock_task])

        ctx.Process.assert_called_once()
        mock_process.start.assert_called_once()
        assert 12345 in scheduler._processes
        assert scheduler._processes[12345] == mock_process
        mock_process.join.assert_not_called()
        mock_main_conn.close.assert_not_called()
        # Parent's write end of the err pipe closed after spawn (so EOF is unambiguous)
        mock_task_conn.close.assert_called_once()
        # Event reference retained to keep its SemLock alive for the spawn child
        assert mock_event in scheduler._shutdown_events

    def test_submit_multiple_tasks_success(self, mock_config):
        """Test successful submission of multiple tasks."""
        tasks = [
            Task(
                app_name="test_app", id=i, poller_name="mock_poller",
                handler_name="mock_handler", app_class=MockBaseApp,
                config=mock_config, calibration_path="/mock/path"
            )
            for i in range(3)
        ]

        scheduler, ctx = _make_scheduler_with_mock_ctx()
        ctx.Event.return_value = Mock(spec=EventType)
        ctx.Pipe.return_value = (Mock(spec=Connection), Mock(spec=Connection))
        mock_processes = [Mock(spec=Process) for _ in range(3)]
        for i, p in enumerate(mock_processes):
            p.pid = 12345 + i
        ctx.Process.side_effect = mock_processes

        with patch.object(app_scheduler_mp.mp, "active_children", return_value=[]):
            scheduler.submit(tasks)

        assert ctx.Process.call_count == 3
        for p in mock_processes:
            p.start.assert_called_once()
            p.join.assert_not_called()
        assert len(scheduler._processes) == 3

    def test_submit_empty_tasks_list(self, scheduler):
        """Test submission with empty tasks list."""
        scheduler._ctx = Mock()

        scheduler.submit([])

        scheduler._ctx.Process.assert_not_called()
        assert len(scheduler._processes) == 0

    def test_submit_process_creation_failure(self, mock_task):
        """Test handling of process creation failure (start raises)."""
        scheduler, ctx = _make_scheduler_with_mock_ctx()
        _, _, _, mock_process = _wire_default_ctx_mocks(ctx)
        mock_process.start.side_effect = Exception("Process creation failed")

        with pytest.raises(Exception, match="Process creation failed"):
            scheduler.submit([mock_task])

    def test_submit_process_error_propagation(self, mock_task):
        """Test error propagation from child process."""
        scheduler, ctx = _make_scheduler_with_mock_ctx()
        _, mock_main_conn, _, mock_process = _wire_default_ctx_mocks(ctx)

        test_exception = Exception("Task execution failed")
        mock_main_conn.poll.return_value = True
        mock_main_conn.recv.return_value = (test_exception, "Traceback: Task execution failed")

        with patch.object(app_scheduler_mp.mp, "active_children", side_effect=[[mock_process], []]):
            with pytest.raises(RuntimeError, match="Process 12345 failed: Task execution failed"):
                scheduler.submit([mock_task])

    def test_submit_process_monitoring_no_errors(self, mock_task):
        """Test process monitoring when no errors occur."""
        scheduler, ctx = _make_scheduler_with_mock_ctx()
        _, mock_main_conn, _, mock_process = _wire_default_ctx_mocks(ctx)
        mock_main_conn.poll.return_value = False

        with patch.object(app_scheduler_mp.mp, "active_children", side_effect=[[mock_process], []]):
            scheduler.submit([mock_task])

        mock_main_conn.poll.assert_called_with(1)
        mock_main_conn.recv.assert_not_called()


class TestMultiprocessingSchedulerShutdown:
    """Test MultiprocessingScheduler shutdown method."""

    def test_shutdown_no_processes(self, scheduler):
        """Test shutdown when no processes are running."""
        scheduler.shutdown()
        assert len(scheduler._processes) == 0

    def test_shutdown_with_alive_processes_terminate_success(self, scheduler):
        """Test shutdown with alive processes that terminate gracefully."""
        mock_process1 = Mock(spec=Process)
        mock_process1.is_alive.side_effect = [True, False]
        mock_process1.name = "test-task-1"
        mock_process1.pid = 12345

        mock_process2 = Mock(spec=Process)
        mock_process2.is_alive.side_effect = [True, False]
        mock_process2.name = "test-task-2"
        mock_process2.pid = 12346

        scheduler._processes = {12345: mock_process1, 12346: mock_process2}

        scheduler.shutdown()

        mock_process1.terminate.assert_called_once()
        mock_process1.join.assert_called_once_with(timeout=10.0)
        mock_process1.kill.assert_not_called()

        mock_process2.terminate.assert_called_once()
        mock_process2.join.assert_called_once_with(timeout=10.0)
        mock_process2.kill.assert_not_called()

    def test_shutdown_with_alive_processes_force_kill(self, scheduler):
        """Test shutdown with processes that don't terminate gracefully."""
        mock_process = Mock(spec=Process)
        mock_process.is_alive.return_value = True
        mock_process.name = "stubborn-task"
        mock_process.pid = 12345

        scheduler._processes = {12345: mock_process}

        scheduler.shutdown()

        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()
        assert mock_process.join.call_count == 2
        calls = mock_process.join.call_args_list
        assert calls[0] == call(timeout=10.0)
        assert calls[1] == call()

    def test_shutdown_with_dead_processes(self, scheduler):
        """Test shutdown with already dead processes."""
        mock_process = Mock(spec=Process)
        mock_process.is_alive.return_value = False
        mock_process.name = "dead-task"
        mock_process.pid = 12345

        scheduler._processes = {12345: mock_process}

        scheduler.shutdown()

        mock_process.terminate.assert_not_called()
        mock_process.join.assert_not_called()
        mock_process.kill.assert_not_called()

    def test_shutdown_clears_shutdown_events(self, scheduler):
        """Test that shutdown signals each event before terminating, then drops references."""
        events = [Mock(spec=EventType), Mock(spec=EventType)]
        scheduler._shutdown_events = events
        mock_process = Mock(spec=Process)
        mock_process.is_alive.return_value = False
        mock_process.name = "x"
        mock_process.pid = 1
        scheduler._processes = {1: mock_process}

        scheduler.shutdown()

        for event in events:
            event.set.assert_called_once()
        assert scheduler._shutdown_events == []


class TestSignalHandling:
    """Test the module-level _sig_shutdown helper."""

    def test_sig_shutdown_sets_flag_on_sigint(self):
        """Test that _sig_shutdown sets the shutdown flag for SIGINT."""
        flag = Event()
        assert not flag.is_set()

        _sig_shutdown(flag, signal.SIGINT, None)

        assert flag.is_set()

    def test_sig_shutdown_sets_flag_on_sigterm(self):
        """Test that _sig_shutdown sets the shutdown flag for SIGTERM."""
        flag = Event()

        _sig_shutdown(flag, signal.SIGTERM, None)

        assert flag.is_set()

    def test_sig_shutdown_already_set_flag(self):
        """Test that _sig_shutdown is idempotent when the flag is already set."""
        flag = Event()
        flag.set()

        _sig_shutdown(flag, signal.SIGINT, None)

        assert flag.is_set()

    @patch("mdx.analytics.core.app.scheduler.app_scheduler_mp.logger")
    def test_sig_shutdown_logs_signal_name(self, mock_logger):
        """Test that _sig_shutdown logs the human-readable signal name."""
        flag = Event()

        _sig_shutdown(flag, signal.SIGINT, None)

        mock_logger.info.assert_called_once_with("Exiting task, shutdown signal SIGINT received.")

    @patch("mdx.analytics.core.app.scheduler.app_scheduler_mp.logger")
    def test_sig_shutdown_already_set_no_logging(self, mock_logger):
        """Test that _sig_shutdown does not log when the flag is already set."""
        flag = Event()
        flag.set()

        _sig_shutdown(flag, signal.SIGTERM, None)

        mock_logger.info.assert_not_called()


class TestTargetFunction:
    """Test the module-level _target helper."""

    @patch("mdx.analytics.core.app.scheduler.app_scheduler_mp.setup_logging")
    @patch("mdx.analytics.core.app.scheduler.app_scheduler_mp.signal.signal")
    def test_target_calls_setup_logging_then_runs_task(self, mock_signal, mock_setup_logging, mock_task):
        """_target re-applies logging config and then invokes the task."""
        shutdown_event = Mock(spec=EventType)
        err_conn = Mock(spec=Connection)
        invoked_task = Mock()
        with patch.object(Task, "__call__", invoked_task):
            _target(mock_task, shutdown_event, err_conn)

        mock_setup_logging.assert_called_once_with(mock_task.log_config_path)
        # Two signal handlers installed (SIGINT + SIGTERM).
        assert mock_signal.call_count == 2
        # Mock isn't a descriptor, so Python's special-method lookup doesn't bind self.
        invoked_task.assert_called_once_with(shutdown_event)
        err_conn.send.assert_not_called()
        err_conn.close.assert_called_once()

    @patch("mdx.analytics.core.app.scheduler.app_scheduler_mp.setup_logging")
    @patch("mdx.analytics.core.app.scheduler.app_scheduler_mp.signal.signal")
    def test_target_propagates_task_exception(self, mock_signal, mock_setup_logging, mock_task):
        """_target sends a serializable error tuple over the pipe on failure."""
        shutdown_event = Mock(spec=EventType)
        err_conn = Mock(spec=Connection)
        invoked_task = Mock(side_effect=ValueError("Task failed"))

        with patch.object(Task, "__call__", invoked_task):
            _target(mock_task, shutdown_event, err_conn)

        err_conn.send.assert_called_once()
        sent_args = err_conn.send.call_args.args[0]
        sent_exc, sent_tb = sent_args
        assert isinstance(sent_exc, RuntimeError)
        assert "ValueError: Task failed" in str(sent_exc)
        assert "Task failed" in sent_tb
        err_conn.close.assert_called_once()


class TestIntegration:
    """Integration tests combining submit and shutdown."""

    def test_submit_and_shutdown_integration(self, mock_config):
        """Test the submit→shutdown lifecycle with a fully mocked context."""
        scheduler, ctx = _make_scheduler_with_mock_ctx()
        _, mock_main_conn, _, mock_process = _wire_default_ctx_mocks(ctx)
        mock_process.is_alive.side_effect = [True, False]
        mock_main_conn.poll.return_value = False

        task = Task(
            app_name="integration_test", id=1, poller_name="mock_poller",
            handler_name="mock_handler", app_class=MockBaseApp,
            config=mock_config, calibration_path="/mock/path"
        )

        with patch.object(app_scheduler_mp.mp, "active_children", return_value=[]):
            scheduler.submit([task])

        assert 12345 in scheduler._processes
        scheduler.shutdown()

        mock_process.terminate.assert_called_once()
        mock_process.join.assert_called_with(timeout=10.0)
        mock_main_conn.close.assert_not_called()


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_submit_with_none_tasks(self, scheduler):
        """Test submit with None instead of list."""
        with pytest.raises(TypeError):
            scheduler.submit(None)

    def test_scheduler_multiple_submit_calls(self, mock_config):
        """Test multiple calls to submit method."""
        scheduler, ctx = _make_scheduler_with_mock_ctx()
        ctx.Event.return_value = Mock(spec=EventType)
        ctx.Pipe.return_value = (Mock(spec=Connection), Mock(spec=Connection))

        mock_process1 = Mock(spec=Process); mock_process1.pid = 11111
        mock_process2 = Mock(spec=Process); mock_process2.pid = 22222
        ctx.Process.side_effect = [mock_process1, mock_process2]

        task1 = Task(
            app_name="test1", id=1, poller_name="mock_poller",
            handler_name="mock_handler", app_class=MockBaseApp,
            config=mock_config, calibration_path="/mock/path"
        )
        task2 = Task(
            app_name="test2", id=2, poller_name="mock_poller",
            handler_name="mock_handler", app_class=MockBaseApp,
            config=mock_config, calibration_path="/mock/path"
        )

        with patch.object(app_scheduler_mp.mp, "active_children", return_value=[]):
            scheduler.submit([task1])
            assert len(scheduler._processes) == 1

            scheduler.submit([task2])
            assert len(scheduler._processes) == 2


class TestProcessMonitoringCoverage:
    """Tests that cover the process monitoring loop."""

    def test_submit_monitoring_multiple_iterations(self, mock_task):
        """Test process monitoring through multiple iterations."""
        scheduler, ctx = _make_scheduler_with_mock_ctx()
        _, mock_main_conn, _, mock_process = _wire_default_ctx_mocks(ctx)
        mock_main_conn.poll.side_effect = [False, False]

        with patch.object(
            app_scheduler_mp.mp,
            "active_children",
            side_effect=[[mock_process], [mock_process], []],
        ):
            scheduler.submit([mock_task])

        assert mock_main_conn.poll.call_count == 2
        mock_main_conn.recv.assert_not_called()

    def test_submit_monitoring_with_mixed_processes(self, mock_config):
        """Test monitoring with multiple processes where one reports an error."""
        tasks = [
            Task(
                app_name="test_app", id=i, poller_name="mock_poller",
                handler_name="mock_handler", app_class=MockBaseApp,
                config=mock_config, calibration_path="/mock/path"
            )
            for i in range(2)
        ]

        scheduler, ctx = _make_scheduler_with_mock_ctx()
        ctx.Event.return_value = Mock(spec=EventType)
        mock_main_conn1 = Mock(spec=Connection)
        mock_main_conn2 = Mock(spec=Connection)
        ctx.Pipe.side_effect = [
            (mock_main_conn1, Mock(spec=Connection)),
            (mock_main_conn2, Mock(spec=Connection)),
        ]
        mock_process1 = Mock(spec=Process); mock_process1.pid = 12345
        mock_process2 = Mock(spec=Process); mock_process2.pid = 12346
        ctx.Process.side_effect = [mock_process1, mock_process2]

        mock_main_conn1.poll.return_value = False
        mock_main_conn2.poll.return_value = True
        mock_main_conn2.recv.return_value = (RuntimeError("Process 2 failed"), "Error traceback")

        with patch.object(
            app_scheduler_mp.mp,
            "active_children",
            side_effect=[[mock_process1, mock_process2], []],
        ):
            with pytest.raises(RuntimeError, match="Process 12346 failed: Process 2 failed"):
                scheduler.submit(tasks)


class TestShutdownEdgeCases:
    """Additional shutdown edge cases."""

    def test_shutdown_mixed_process_states(self, scheduler):
        """Test shutdown with mixed alive and dead processes."""
        mock_alive_process = Mock(spec=Process)
        mock_alive_process.is_alive.side_effect = [True, False]
        mock_alive_process.name = "alive-task"
        mock_alive_process.pid = 11111

        mock_dead_process = Mock(spec=Process)
        mock_dead_process.is_alive.return_value = False
        mock_dead_process.name = "dead-task"
        mock_dead_process.pid = 22222

        scheduler._processes = {11111: mock_alive_process, 22222: mock_dead_process}

        scheduler.shutdown()

        mock_alive_process.terminate.assert_called_once()
        mock_alive_process.join.assert_called_once_with(timeout=10.0)

        mock_dead_process.terminate.assert_not_called()
        mock_dead_process.join.assert_not_called()


class TestAdditionalEdgeCases:
    """Additional edge cases for comprehensive coverage."""

    def test_submit_pipe_creation_failure(self, mock_task):
        """Test handling of pipe creation failure."""
        scheduler, ctx = _make_scheduler_with_mock_ctx()
        ctx.Event.return_value = Mock(spec=EventType)
        ctx.Pipe.side_effect = Exception("Pipe creation failed")

        with pytest.raises(Exception, match="Pipe creation failed"):
            scheduler.submit([mock_task])

    def test_submit_event_creation_failure(self, mock_task):
        """Test handling of event creation failure."""
        scheduler, ctx = _make_scheduler_with_mock_ctx()
        ctx.Event.side_effect = Exception("Event creation failed")

        with pytest.raises(Exception, match="Event creation failed"):
            scheduler.submit([mock_task])
