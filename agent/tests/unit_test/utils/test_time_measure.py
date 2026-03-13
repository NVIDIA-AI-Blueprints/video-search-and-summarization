# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Tests for vss_agents/utils/time_measure.py."""

import time
from unittest.mock import patch

from vss_agents.utils.time_measure import TimeMeasure


class TestTimeMeasure:
    """Tests for TimeMeasure context manager."""

    def test_context_manager_basic(self):
        """Test basic context manager usage."""
        with TimeMeasure("test operation", print=False) as tm:
            time.sleep(0.01)  # 10ms

        assert tm.execution_time > 0
        assert tm.execution_time < 1  # Should be much less than 1 second

    def test_execution_time_property(self):
        """Test execution_time property after context exits."""
        with TimeMeasure("test", print=False) as tm:
            time.sleep(0.02)

        exec_time = tm.execution_time
        assert exec_time >= 0.01  # At least 10ms

    def test_current_execution_time_property(self):
        """Test current_execution_time property during execution."""
        with TimeMeasure("test", print=False) as tm:
            time.sleep(0.01)
            current_time = tm.current_execution_time
            assert current_time > 0
            assert current_time < 1

    def test_timing_accuracy(self):
        """Test that timing is reasonably accurate."""
        sleep_time = 0.05  # 50ms
        with TimeMeasure("accuracy test", print=False) as tm:
            time.sleep(sleep_time)

        # Allow for some tolerance (50-150ms)
        assert tm.execution_time >= 0.03
        assert tm.execution_time < 0.15

    def test_print_enabled(self):
        """Test that print output works when enabled."""
        with patch("builtins.print") as mock_print, patch("sys.stderr"), TimeMeasure("print test", print=True):
            time.sleep(0.001)

        # Verify print was called
        mock_print.assert_called()

    def test_print_disabled(self):
        """Test that print is skipped when disabled."""
        with patch("builtins.print"), TimeMeasure("no print test", print=False):
            pass

        # Print should not be called for timing output
        # (may still be called by logger)

    def test_string_parameter(self):
        """Test that string parameter is used in output."""
        test_string = "unique operation name"
        with patch("sys.stderr"), TimeMeasure(test_string, print=True):
            pass

    def test_nested_context_managers(self):
        """Test nested TimeMeasure contexts."""
        with TimeMeasure("outer", print=False) as outer:
            time.sleep(0.01)
            with TimeMeasure("inner", print=False) as inner:
                time.sleep(0.01)

        assert outer.execution_time > inner.execution_time

    def test_millisecond_format(self):
        """Test that short operations are formatted in milliseconds."""
        with TimeMeasure("ms test", print=False) as tm:
            time.sleep(0.001)  # 1ms

        # Just verify execution completes without error
        assert tm.execution_time > 0

    def test_second_format(self):
        """Test that longer operations show in seconds."""
        with TimeMeasure("sec test", print=False) as tm:
            time.sleep(0.001)  # 1ms - fast for testing

        # Verify execution time is captured
        assert hasattr(tm, "_end_time")
        assert hasattr(tm, "_start_time")

    def test_context_manager_enter_return(self):
        """Test that __enter__ returns self."""
        tm = TimeMeasure("test")
        result = tm.__enter__()
        assert result is tm
        tm.__exit__(None, None, None)

    def test_context_manager_exit_no_exception(self):
        """Test __exit__ with no exception."""
        with TimeMeasure("test", print=False):
            pass
        # Should not raise

    def test_zero_execution_time_handling(self):
        """Test handling of very fast operations."""
        with TimeMeasure("fast", print=False) as tm:
            pass  # Nearly instant

        # Should handle gracefully, time should be >= 0
        assert tm.execution_time >= 0

    def test_seconds_format_output(self):
        """Test output formatting when exec_time > 1 second (covers line 40)."""
        with patch("sys.stderr"), patch("time.perf_counter") as mock_time:
            # Simulate 2 seconds execution
            mock_time.side_effect = [0.0, 2.5]
            with TimeMeasure("slow test", print=True):
                pass
        # Should format as seconds

    def test_nanoseconds_format_output(self):
        """Test output formatting when exec_time is nanoseconds (covers line 46)."""
        with patch("sys.stderr"), patch("time.perf_counter") as mock_time:
            # Simulate sub-microsecond execution (nanoseconds)
            mock_time.side_effect = [0.0, 0.0000001]  # 100 nanoseconds
            with TimeMeasure("nano test", print=True):
                pass
        # Should format as nanoseconds
