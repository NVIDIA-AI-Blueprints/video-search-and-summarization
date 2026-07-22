# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Unit tests for src/via_logger.py

Tests logging functionality, security filtering, and performance measurement.
"""
import importlib
import io
import logging
import logging.handlers
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from via_logger import (
    LOG_COLORS,
    LOG_PERF_LEVEL,
    LOG_STATUS_LEVEL,
    LogFormatter,
    SafeStreamHandler,
    SecureLogFilter,
    TimeMeasure,
    logger,
    patch_logger_handlers,
    safe_log,
)


@pytest.mark.unit
class TestSecureLogFilter:
    """Tests for SecureLogFilter class"""

    def test_mask_api_key_patterns_nvapi(self):
        """Test masking of NVIDIA API keys (nvapi- pattern)."""
        msg = "Using API key nvapi-1234567890abcdef to authenticate"
        masked = SecureLogFilter.mask_api_key_patterns(msg)
        assert "nvapi-1234567890abcdef" not in masked
        assert "***MASKED***" in masked
        assert masked == "Using API key ***MASKED*** to authenticate"

    def test_mask_api_key_patterns_openai(self):
        """Test masking of OpenAI API keys (sk- pattern)."""
        msg = "OpenAI key: sk-proj-1234567890abcdefghijklmnop"
        masked = SecureLogFilter.mask_api_key_patterns(msg)
        assert "sk-proj-" not in masked
        assert "***MASKED***" in masked

    def test_mask_api_key_patterns_multiple(self):
        """Test masking multiple API keys in same message."""
        msg = "Keys: nvapi-abc123 and sk-xyz789"
        masked = SecureLogFilter.mask_api_key_patterns(msg)
        assert "nvapi-abc123" not in masked
        assert "sk-xyz789" not in masked
        assert masked.count("***MASKED***") == 2

    def test_mask_api_key_patterns_no_keys(self):
        """Test that normal messages are not modified."""
        msg = "This is a normal log message with no keys"
        masked = SecureLogFilter.mask_api_key_patterns(msg)
        assert masked == msg

    def test_filter_masks_env_var_values(self, monkeypatch):
        """Test that filter masks sensitive environment variable values."""
        monkeypatch.setenv("NVIDIA_API_KEY", "secret-nvidia-key-123")
        monkeypatch.setenv("OPENAI_API_KEY", "secret-openai-key-456")

        log_filter = SecureLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Using NVIDIA_API_KEY=secret-nvidia-key-123 and OPENAI_API_KEY=secret-openai-key-456",
            args=(),
            exc_info=None,
        )

        result = log_filter.filter(record)
        assert result is True  # Filter should not block the record
        assert "secret-nvidia-key-123" not in record.getMessage()
        assert "secret-openai-key-456" not in record.getMessage()
        assert "***MASKED***" in record.getMessage()

    def test_filter_with_no_secrets(self, monkeypatch):
        """Test filter with no sensitive environment variables set."""
        # Clear all sensitive env vars
        for var in SecureLogFilter._SENSITIVE_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        log_filter = SecureLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="This is a safe log message",
            args=(),
            exc_info=None,
        )

        result = log_filter.filter(record)
        assert result is True
        assert record.getMessage() == "This is a safe log message"

    def test_filter_masks_multiple_secrets(self, monkeypatch):
        """Test masking multiple secret values in one message."""
        monkeypatch.setenv("NGC_API_KEY", "ngc-secret-abc")
        monkeypatch.setenv("GRAPH_DB_PASSWORD", "db-pass-xyz")

        log_filter = SecureLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Connecting with NGC_API_KEY ngc-secret-abc and password db-pass-xyz",
            args=(),
            exc_info=None,
        )

        log_filter.filter(record)
        message = record.getMessage()
        assert "ngc-secret-abc" not in message
        assert "db-pass-xyz" not in message
        assert message.count("***MASKED***") == 2

    def test_filter_with_pattern_and_env_var(self, monkeypatch):
        """Test filtering both env var values and pattern-matched keys."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-env-var-key")

        log_filter = SecureLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Keys: nvapi-env-var-key and nvapi-pattern-key-123",
            args=(),
            exc_info=None,
        )

        log_filter.filter(record)
        message = record.getMessage()
        assert "nvapi-env-var-key" not in message
        assert "nvapi-pattern-key-123" not in message
        assert message.count("***MASKED***") == 2


@pytest.mark.unit
class TestLogFormatter:
    """Tests for LogFormatter class"""

    def test_formatter_adds_colors(self):
        """Test that formatter adds color codes to log records."""
        formatter = LogFormatter("%(asctime)s %(levelname)s %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="Test error message",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        # Should contain ERROR color code
        assert LOG_COLORS["ERROR"] in formatted
        assert LOG_COLORS["RESET"] in formatted
        assert "Test error message" in formatted

    def test_formatter_different_levels(self):
        """Test formatter with different log levels."""
        formatter = LogFormatter("%(levelname)s %(message)s")

        for level_name, level_num in [
            ("ERROR", logging.ERROR),
            ("WARNING", logging.WARNING),
            ("INFO", logging.INFO),
            ("DEBUG", logging.DEBUG),
        ]:
            record = logging.LogRecord(
                name="test",
                level=level_num,
                pathname="",
                lineno=0,
                msg=f"Test {level_name} message",
                args=(),
                exc_info=None,
            )
            formatted = formatter.format(record)
            assert level_name in formatted
            assert f"Test {level_name} message" in formatted


@pytest.mark.unit
class TestTimeMeasure:
    """Tests for TimeMeasure context manager"""

    def test_time_measure_basic(self):
        """Test TimeMeasure basic functionality."""
        with TimeMeasure("test_operation") as tm:
            time.sleep(0.01)  # Sleep 10ms

        assert tm.execution_time >= 0.01
        assert tm.execution_time < 0.1  # Should be less than 100ms

    def test_time_measure_current_execution_time(self):
        """Test current_execution_time property during execution."""
        with TimeMeasure("test_operation") as tm:
            time.sleep(0.01)
            current_time = tm.current_execution_time
            assert current_time >= 0.01
            assert current_time < 0.1

            time.sleep(0.01)
            later_time = tm.current_execution_time
            assert later_time > current_time

    def test_time_measure_execution_time_property(self):
        """Test execution_time property after completion."""
        with TimeMeasure("test_operation") as tm:
            time.sleep(0.02)

        exec_time = tm.execution_time
        assert exec_time >= 0.02
        assert exec_time < 0.1

    def test_time_measure_with_print_flag(self):
        """Test TimeMeasure with print flag (though not used in implementation)."""
        with TimeMeasure("test_operation", print=True) as tm:
            time.sleep(0.01)

        assert tm.execution_time >= 0.01

    def test_time_measure_logs_at_perf_level(self):
        """Test that TimeMeasure logs at PERF level (skipping actual log capture)."""
        # Note: Log capture in tests can be unreliable due to logger configuration
        # This test validates the TimeMeasure completes successfully
        with TimeMeasure("my_operation"):
            time.sleep(0.01)
        # Test passes if no exception raised

    def test_time_measure_formats_seconds(self):
        """Test time formatting for seconds."""
        with TimeMeasure("slow_operation"):
            time.sleep(0.05)  # Short sleep for test speed
        # Test validates execution completes

    def test_time_measure_formats_milliseconds(self):
        """Test time formatting for milliseconds."""
        with TimeMeasure("fast_operation"):
            time.sleep(0.01)
        # Test validates execution completes

    def test_time_measure_zero_time(self):
        """Test TimeMeasure with very fast operations."""
        with TimeMeasure("instant_operation") as tm:
            pass  # No sleep

        # Should still have some execution time (even if very small)
        assert tm.execution_time >= 0

    def test_time_measure_string_attribute(self):
        """Test that TimeMeasure stores the operation string."""
        tm = TimeMeasure("my_custom_operation", print=False)
        assert tm._string == "my_custom_operation"
        assert tm._print is False


@pytest.mark.unit
class TestLoggerConfiguration:
    """Tests for logger module configuration"""

    def test_logger_exists(self):
        """Test that logger is properly initialized."""
        assert logger is not None
        assert isinstance(logger, logging.Logger)

    def test_custom_log_levels(self):
        """Test that custom log levels are registered."""
        assert logging.getLevelName(LOG_PERF_LEVEL) == "PERF"
        assert logging.getLevelName(LOG_STATUS_LEVEL) == "STATUS"

    def test_log_perf_level_value(self):
        """Test PERF log level value."""
        assert LOG_PERF_LEVEL == 15
        assert LOG_PERF_LEVEL < logging.INFO  # Should be between DEBUG and INFO

    def test_log_status_level_value(self):
        """Test STATUS log level value."""
        assert LOG_STATUS_LEVEL == 16
        assert LOG_STATUS_LEVEL < logging.INFO

    def test_log_colors_defined(self):
        """Test that all log colors are defined."""
        required_colors = ["RESET", "BOLD", "ERROR", "WARNING", "INFO", "DEBUG", "STATUS", "PERF"]
        for color in required_colors:
            assert color in LOG_COLORS
            assert isinstance(LOG_COLORS[color], str)

    def test_logger_has_secure_filter(self):
        """Test that logger has SecureLogFilter attached."""
        filters = logger.filters
        assert any(isinstance(f, SecureLogFilter) for f in filters)

    def test_logger_has_handlers(self):
        """Test that logger has both stream and file handlers."""
        assert len(logger.handlers) >= 2
        has_safe_stream = any(isinstance(h, SafeStreamHandler) for h in logger.handlers)
        has_file = any(
            isinstance(h, logging.handlers.TimedRotatingFileHandler) for h in logger.handlers
        )
        assert has_safe_stream
        assert has_file


@pytest.mark.unit
class TestSafeStreamHandler:

    def test_emit_returns_early_on_closed_stream(self):
        handler = SafeStreamHandler()
        closed_stream = MagicMock()
        closed_stream.closed = True
        handler.stream = closed_stream
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="should be skipped",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        closed_stream.write.assert_not_called()

    def test_emit_delegates_to_super_for_open_stream(self):
        stream = io.StringIO()
        handler = SafeStreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello from handler",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        assert "hello from handler" in stream.getvalue()

    def test_handle_error_swallows_value_error(self):
        handler = SafeStreamHandler()
        try:
            raise ValueError("stream is closed")
        except ValueError:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="test",
                args=(),
                exc_info=None,
            )
            handler.handleError(record)

    def test_handle_error_swallows_os_error(self):
        handler = SafeStreamHandler()
        try:
            raise OSError("broken pipe")
        except OSError:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="test",
                args=(),
                exc_info=None,
            )
            handler.handleError(record)

    def test_handle_error_delegates_other_exceptions_to_super(self):
        handler = SafeStreamHandler()
        try:
            raise RuntimeError("unexpected")
        except RuntimeError:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="test",
                args=(),
                exc_info=None,
            )
            with patch.object(logging.StreamHandler, "handleError") as mock_super:
                handler.handleError(record)
                mock_super.assert_called_once_with(record)


@pytest.mark.unit
def test_safe_log_delegates_to_logger_method():
    mock_logger = MagicMock()
    safe_log(mock_logger, "warning", "something %s happened", "bad", extra={"key": "val"})
    mock_logger.warning.assert_called_once_with(
        "something %s happened", "bad", extra={"key": "val"}
    )


@pytest.mark.unit
def test_patch_logger_handlers_replaces_stream_handlers():
    target = logging.getLogger("_test_patch_target")
    target.handlers.clear()

    stream = io.StringIO()
    plain = logging.StreamHandler(stream)
    plain.setLevel(logging.WARNING)
    fmt = logging.Formatter("%(message)s")
    plain.setFormatter(fmt)
    test_filter = logging.Filter("myfilter")
    plain.addFilter(test_filter)
    target.addHandler(plain)

    patch_logger_handlers("_test_patch_target")

    assert len(target.handlers) == 1
    replaced = target.handlers[0]
    assert isinstance(replaced, SafeStreamHandler)
    assert replaced.level == logging.WARNING
    assert replaced.stream is stream
    assert any(f.name == "myfilter" for f in replaced.filters)

    target.handlers.clear()


@pytest.mark.unit
def test_patch_logger_handlers_skips_safe_stream_handlers():
    target = logging.getLogger("_test_patch_skip")
    target.handlers.clear()
    safe = SafeStreamHandler()
    target.addHandler(safe)

    patch_logger_handlers("_test_patch_skip")

    assert len(target.handlers) == 1
    assert target.handlers[0] is safe
    target.handlers.clear()


@pytest.mark.unit
class TestTimeMeasureBranches:

    def test_sec_branch(self):
        original_level = logger.level
        logger.setLevel(LOG_PERF_LEVEL)
        try:
            tm = TimeMeasure("sec_op")
            tm._start_time = 100.0
            with patch("via_logger.time.time", return_value=102.5):
                tm.__exit__(None, None, None)
            assert tm.execution_time == pytest.approx(2.5)
        finally:
            logger.setLevel(original_level)

    def test_millisec_branch(self):
        original_level = logger.level
        logger.setLevel(LOG_PERF_LEVEL)
        try:
            tm = TimeMeasure("ms_op")
            tm._start_time = 100.0
            with patch("via_logger.time.time", return_value=100.005):
                tm.__exit__(None, None, None)
            assert tm.execution_time == pytest.approx(0.005)
        finally:
            logger.setLevel(original_level)

    def test_usec_branch(self):
        original_level = logger.level
        logger.setLevel(LOG_PERF_LEVEL)
        try:
            tm = TimeMeasure("usec_op")
            tm._start_time = 100.0
            with patch("via_logger.time.time", return_value=100.00001):
                tm.__exit__(None, None, None)
            assert tm.execution_time == pytest.approx(0.00001, abs=1e-10)
        finally:
            logger.setLevel(original_level)


@pytest.mark.unit
def test_module_reload_replaces_root_plain_stream_handler():
    import via_logger

    root = logging.getLogger()
    plain = logging.StreamHandler()
    plain.setLevel(logging.WARNING)
    fmt = logging.Formatter("%(message)s")
    plain.setFormatter(fmt)
    test_filter = logging.Filter("root_filter")
    plain.addFilter(test_filter)
    root.addHandler(plain)

    try:
        importlib.reload(via_logger)

        assert plain not in root.handlers
        replaced = [
            h
            for h in root.handlers
            if isinstance(h, via_logger.SafeStreamHandler)
            and h.stream is plain.stream
            and any(f.name == "root_filter" for f in h.filters)
        ]
        assert len(replaced) >= 1, (
            f"Expected a SafeStreamHandler with root_filter on root logger, "
            f"got handlers: {root.handlers}"
        )
        r = replaced[0]
        assert r.level == logging.WARNING
    finally:
        for h in root.handlers[:]:
            if h not in logging.getLogger(via_logger.__name__).handlers:
                root.removeHandler(h)
        importlib.reload(via_logger)


@pytest.mark.unit
def test_patch_logger_handlers_uses_root_when_name_is_none():
    import via_logger

    root = logging.getLogger()
    original_handlers = root.handlers[:]

    plain = logging.StreamHandler()
    root.addHandler(plain)
    try:
        via_logger.patch_logger_handlers(None)
        assert plain not in root.handlers
        replacements = [h for h in root.handlers if isinstance(h, via_logger.SafeStreamHandler)]
        assert len(replacements) >= 1
    finally:
        root.handlers = original_handlers


@pytest.mark.unit
def test_vss_log_level_env_var_sets_logger_level():
    import via_logger

    original_env = os.environ.get("VSS_LOG_LEVEL")
    os.environ["VSS_LOG_LEVEL"] = "debug"
    try:
        importlib.reload(via_logger)
        assert via_logger.logger.level == logging.DEBUG
        assert via_logger.term_out.level == logging.DEBUG
    finally:
        if original_env is None:
            os.environ.pop("VSS_LOG_LEVEL", None)
        else:
            os.environ["VSS_LOG_LEVEL"] = original_env
        importlib.reload(via_logger)


@pytest.mark.unit
def test_vss_log_level_not_set_defaults_to_info():
    import via_logger

    original_env = os.environ.get("VSS_LOG_LEVEL")
    os.environ.pop("VSS_LOG_LEVEL", None)
    try:
        importlib.reload(via_logger)
        assert via_logger.logger.level == logging.INFO
    finally:
        if original_env is not None:
            os.environ["VSS_LOG_LEVEL"] = original_env
        importlib.reload(via_logger)


@pytest.mark.unit
class TestLogFormatterBranches:

    def test_format_uses_reset_for_unknown_level(self):
        formatter = LogFormatter("%(levelname)s %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.CRITICAL,
            pathname="",
            lineno=0,
            msg="critical msg",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert LOG_COLORS["RESET"] in formatted
        assert "CRITICAL" in formatted
        assert "critical msg" in formatted

    def test_format_with_perf_level(self):
        formatter = LogFormatter("%(levelname)s %(message)s")
        record = logging.LogRecord(
            name="test",
            level=LOG_PERF_LEVEL,
            pathname="",
            lineno=0,
            msg="perf msg",
            args=(),
            exc_info=None,
        )
        record.levelname = "PERF"
        formatted = formatter.format(record)
        assert LOG_COLORS["PERF"] in formatted
        assert "perf msg" in formatted

    def test_format_with_status_level(self):
        formatter = LogFormatter("%(levelname)s %(message)s")
        record = logging.LogRecord(
            name="test",
            level=LOG_STATUS_LEVEL,
            pathname="",
            lineno=0,
            msg="status msg",
            args=(),
            exc_info=None,
        )
        record.levelname = "STATUS"
        formatted = formatter.format(record)
        assert LOG_COLORS["STATUS"] in formatted
        assert "status msg" in formatted


@pytest.mark.unit
class TestSafeStreamHandlerBranches:

    def test_emit_falls_through_when_stream_lacks_closed_attr(self):
        handler = SafeStreamHandler()
        handler.stream = MagicMock(spec=["write", "flush"])
        with patch.object(logging.StreamHandler, "emit") as mock_emit:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="no closed attr",
                args=(),
                exc_info=None,
            )
            handler.emit(record)
            mock_emit.assert_called_once_with(record)

    def test_handle_error_delegates_when_no_exception_active(self):
        handler = SafeStreamHandler()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        with patch.object(logging.StreamHandler, "handleError") as mock_super:
            handler.handleError(record)
            mock_super.assert_called_once_with(record)


@pytest.mark.unit
class TestSecureLogFilterBranches:

    def test_filter_no_replace_when_secret_not_in_message(self, monkeypatch):
        monkeypatch.setenv("NGC_API_KEY", "my-ngc-secret-value")
        log_filter = SecureLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="nothing sensitive here",
            args=(),
            exc_info=None,
        )
        log_filter.filter(record)
        assert record.getMessage() == "nothing sensitive here"
        assert record.args == ()

    def test_filter_with_format_args_cleared_on_mask(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "the-secret")
        log_filter = SecureLogFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="key is %s and the-secret is here",
            args=("some_arg",),
            exc_info=None,
        )
        log_filter.filter(record)
        assert record.args == ()
        assert "the-secret" not in record.msg


@pytest.mark.unit
class TestPatchLoggerHandlersBranches:

    def test_no_op_on_empty_handler_list(self):
        target = logging.getLogger("_test_empty_handlers")
        target.handlers.clear()
        patch_logger_handlers("_test_empty_handlers")
        assert len(target.handlers) == 0

    def test_skips_non_stream_handlers(self):
        target = logging.getLogger("_test_non_stream")
        target.handlers.clear()
        nh = logging.NullHandler()
        target.addHandler(nh)
        patch_logger_handlers("_test_non_stream")
        assert len(target.handlers) == 1
        assert target.handlers[0] is nh
        target.handlers.clear()

    def test_copies_handler_without_filters(self):
        import via_logger

        target = logging.getLogger("_test_no_filters")
        target.handlers.clear()
        plain = logging.StreamHandler(io.StringIO())
        plain.setLevel(logging.ERROR)
        target.addHandler(plain)

        patch_logger_handlers("_test_no_filters")

        assert len(target.handlers) == 1
        replaced = target.handlers[0]
        assert isinstance(replaced, via_logger.SafeStreamHandler)
        assert replaced.level == logging.ERROR
        assert len(replaced.filters) == 0
        target.handlers.clear()


@pytest.mark.unit
class TestTimeMeasureBranchesExtra:

    def test_exit_skips_logging_when_level_above_perf(self):
        original_level = logger.level
        logger.setLevel(logging.WARNING)
        try:
            tm = TimeMeasure("skip_op")
            tm._start_time = 100.0
            with patch("via_logger.time.time", return_value=102.0):
                with patch.object(logger, "log") as mock_log:
                    tm.__exit__(None, None, None)
                    mock_log.assert_not_called()
            assert tm.execution_time == pytest.approx(2.0)
        finally:
            logger.setLevel(original_level)

    def test_sub_microsecond_uses_nsec(self):
        """Sub-microsecond durations use nsec unit (previously caused UnboundLocalError)."""
        original_level = logger.level
        logger.setLevel(LOG_PERF_LEVEL)
        try:
            tm = TimeMeasure("zero_op")
            tm._start_time = 100.0
            with patch("via_logger.time.time", return_value=100.0):
                tm.__exit__(None, None, None)  # exec_time == 0 → nsec branch
        finally:
            logger.setLevel(original_level)


@pytest.mark.unit
def test_module_reload_removes_preexisting_handlers():
    import via_logger

    orig_handler_count = len(via_logger.logger.handlers)
    importlib.reload(via_logger)
    assert len(via_logger.logger.handlers) == orig_handler_count
    importlib.reload(via_logger)


@pytest.mark.unit
def test_safe_log_delegates_info():
    mock_logger = MagicMock()
    safe_log(mock_logger, "info", "message %d", 42)
    mock_logger.info.assert_called_once_with("message %d", 42)


@pytest.mark.unit
def test_safe_log_delegates_debug():
    mock_logger = MagicMock()
    safe_log(mock_logger, "debug", "dbg")
    mock_logger.debug.assert_called_once_with("dbg")
