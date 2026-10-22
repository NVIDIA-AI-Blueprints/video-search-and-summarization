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
"""Unit tests for retry_utils module."""

from unittest.mock import Mock, patch

import pytest
import requests

from src.backends.nmp.retry_utils import (
    is_transient_error,
    retry_on_transient_error,
)


class TestIsTransientError:
    """Test class for is_transient_error function."""

    def test_connection_error(self):
        """Test that ConnectionError is identified as transient."""
        error = requests.exceptions.ConnectionError("Connection failed")
        assert is_transient_error(error) is True

    def test_remote_disconnected(self):
        """Test that RemoteDisconnected error is identified as transient."""
        error = Exception("RemoteDisconnected: Remote end closed connection")
        assert is_transient_error(error) is True

    def test_connection_aborted(self):
        """Test that 'Connection aborted' error is identified as transient."""
        error = Exception("Connection aborted by peer")
        assert is_transient_error(error) is True

    def test_remote_end_closed_connection(self):
        """Test that 'Remote end closed connection' error is identified as transient."""
        error = Exception("Remote end closed connection without response")
        assert is_transient_error(error) is True

    def test_protocol_error(self):
        """Test that ProtocolError is identified as transient."""
        error = Exception("ProtocolError('Connection aborted.', RemoteDisconnected(...))")
        assert is_transient_error(error) is True

    def test_connection_reset(self):
        """Test that 'Connection reset' error is identified as transient."""
        error = Exception("Connection reset by peer")
        assert is_transient_error(error) is True

    def test_non_transient_error(self):
        """Test that non-transient errors are not identified as transient."""
        error = ValueError("Invalid input")
        assert is_transient_error(error) is False

    def test_http_error(self):
        """Test that HTTP errors are not identified as transient."""
        error = requests.exceptions.HTTPError("404 Not Found")
        assert is_transient_error(error) is False


class TestRetryOnTransientError:
    """Test class for retry_on_transient_error function."""

    @pytest.fixture(autouse=True)
    def mock_sleep(self):
        """Automatically mock time.sleep for all tests."""
        with patch("src.lib.retry.sleep") as mock_sleep:
            yield mock_sleep

    def test_success_on_first_attempt(self, mock_sleep):
        """Test that function succeeds on first attempt without retrying."""
        mock_func = Mock(return_value="success")

        result = retry_on_transient_error(mock_func, "test_operation")

        assert result == "success"
        assert mock_func.call_count == 1
        mock_sleep.assert_not_called()

    def test_success_after_one_retry(self, mock_sleep):
        """Test that function succeeds after one retry."""
        transient_error = requests.exceptions.ConnectionError("Connection failed")
        mock_func = Mock(side_effect=[transient_error, "success"])

        result = retry_on_transient_error(mock_func, "test_operation")

        assert result == "success"
        assert mock_func.call_count == 2
        assert mock_sleep.call_count == 1
        # Verify exponential backoff: 2^1 = 2 seconds
        mock_sleep.assert_called_with(2.0)

    def test_success_after_two_retries(self, mock_sleep):
        """Test that function succeeds after two retries."""
        transient_error = requests.exceptions.ConnectionError("Connection failed")
        mock_func = Mock(side_effect=[transient_error, transient_error, "success"])

        result = retry_on_transient_error(mock_func, "test_operation")

        assert result == "success"
        assert mock_func.call_count == 3
        assert mock_sleep.call_count == 2
        # Verify exponential backoff: 2^1 = 2, 2^2 = 4 seconds
        assert mock_sleep.call_args_list[0][0][0] == 2.0
        assert mock_sleep.call_args_list[1][0][0] == 4.0

    def test_failure_after_max_retries(self, mock_sleep):
        """Test that function fails after max retries are exhausted."""
        transient_error = requests.exceptions.ConnectionError("Connection failed")
        mock_func = Mock(side_effect=transient_error)

        with pytest.raises(requests.exceptions.ConnectionError, match="Connection failed"):
            retry_on_transient_error(mock_func, "test_operation", max_retries=3)

        # Should try 3 times (initial + 2 retries)
        assert mock_func.call_count == 3
        assert mock_sleep.call_count == 2  # Sleep before retries 2 and 3

    def test_non_transient_error_no_retry(self, mock_sleep):
        """Test that non-transient errors are not retried."""
        non_transient_error = ValueError("Invalid input")
        mock_func = Mock(side_effect=non_transient_error)

        with pytest.raises(ValueError, match="Invalid input"):
            retry_on_transient_error(mock_func, "test_operation")

        # Should only try once, no retries
        assert mock_func.call_count == 1
        mock_sleep.assert_not_called()

    def test_protocol_error_retry(self, mock_sleep):
        """Test that ProtocolError is retried."""
        protocol_error = Exception("ProtocolError('Connection aborted.', RemoteDisconnected(...))")
        mock_func = Mock(side_effect=[protocol_error, "success"])

        result = retry_on_transient_error(mock_func, "test_operation")

        assert result == "success"
        assert mock_func.call_count == 2
        assert mock_sleep.call_count == 1

    def test_remote_disconnected_retry(self, mock_sleep):
        """Test that RemoteDisconnected error is retried."""
        remote_error = Exception("RemoteDisconnected: Remote end closed connection")
        mock_func = Mock(side_effect=[remote_error, "success"])

        result = retry_on_transient_error(mock_func, "test_operation")

        assert result == "success"
        assert mock_func.call_count == 2
        assert mock_sleep.call_count == 1

    def test_custom_max_retries(self, mock_sleep):
        """Test that custom max_retries parameter works."""
        transient_error = requests.exceptions.ConnectionError("Connection failed")
        mock_func = Mock(side_effect=transient_error)

        with pytest.raises(requests.exceptions.ConnectionError):
            retry_on_transient_error(mock_func, "test_operation", max_retries=5)

        # Should try 5 times
        assert mock_func.call_count == 5
        assert mock_sleep.call_count == 4

    def test_custom_backoff_factor(self, mock_sleep):
        """Test that custom backoff_factor parameter works."""
        transient_error = requests.exceptions.ConnectionError("Connection failed")
        mock_func = Mock(side_effect=[transient_error, transient_error, "success"])

        result = retry_on_transient_error(
            mock_func, "test_operation", backoff_factor=3.0, max_retries=3
        )

        assert result == "success"
        # Verify custom exponential backoff: 3^1 = 3, 3^2 = 9 seconds
        assert mock_sleep.call_args_list[0][0][0] == 3.0
        assert mock_sleep.call_args_list[1][0][0] == 9.0

    def test_function_with_args_and_kwargs(self, mock_sleep):
        """Test that function arguments and kwargs are passed correctly."""
        mock_func = Mock(return_value="success")

        result = retry_on_transient_error(
            mock_func, "test_operation", "arg1", "arg2", key1="value1", key2="value2"
        )

        assert result == "success"
        mock_func.assert_called_once_with("arg1", "arg2", key1="value1", key2="value2")

    def test_function_with_args_and_kwargs_retry(self, mock_sleep):
        """Test that function arguments and kwargs are passed correctly during retries."""
        transient_error = requests.exceptions.ConnectionError("Connection failed")
        mock_func = Mock(side_effect=[transient_error, "success"])

        result = retry_on_transient_error(
            mock_func, "test_operation", "arg1", "arg2", key1="value1", key2="value2"
        )

        assert result == "success"
        assert mock_func.call_count == 2
        # Verify args and kwargs are passed in both calls
        expected_call = Mock()
        expected_call("arg1", "arg2", key1="value1", key2="value2")
        assert mock_func.call_args_list[0] == expected_call.call_args_list[0]
        assert mock_func.call_args_list[1] == expected_call.call_args_list[0]

    def test_mixed_transient_and_non_transient_errors(self, mock_sleep):
        """Test behavior when transient error is followed by non-transient error."""
        transient_error = requests.exceptions.ConnectionError("Connection failed")
        non_transient_error = ValueError("Invalid input")
        mock_func = Mock(side_effect=[transient_error, non_transient_error])

        with pytest.raises(ValueError, match="Invalid input"):
            retry_on_transient_error(mock_func, "test_operation")

        # Should retry once, then fail on non-transient error
        assert mock_func.call_count == 2
        assert mock_sleep.call_count == 1

    def test_operation_name_in_logging(self, mock_sleep):
        """Test that operation_name is used in logging (indirectly via call count)."""
        transient_error = requests.exceptions.ConnectionError("Connection failed")
        mock_func = Mock(side_effect=[transient_error, "success"])

        result = retry_on_transient_error(mock_func, "my_custom_operation")

        assert result == "success"
        # Verify retry happened (which would log with operation_name)
        assert mock_func.call_count == 2
        assert mock_sleep.call_count == 1
