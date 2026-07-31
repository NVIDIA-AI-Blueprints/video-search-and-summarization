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

"""Unit tests for ``handlers.exception_handler.error_handler``.

``ErrorHandler`` is the retry/΄swallow policy applied around VSS calls, so its
control flow decides whether a transient VSS hiccup is retried or surfaces as
a hard failure. The properties pinned here:

* ``with_retry`` attempts exactly ``max_retries`` times, sleeps with
  exponential backoff between attempts (never after the last one), and
  re-raises as ``VSSRetryExhaustedError`` chained to the original error — so
  callers can distinguish "gave up" from "failed once".
* Only the exception types passed in ``exceptions`` are retried; anything else
  propagates immediately rather than burning the retry budget.
* Expected ``VSSException`` subclasses are logged without a stack trace, while
  unexpected errors get one.

``time.sleep`` is patched throughout: these tests assert on the computed
backoff, they do not wait for it.
"""

from unittest.mock import MagicMock, patch

import pytest

from handlers.exception_handler.error_handler import ErrorHandler
from handlers.exception_handler.vss_exceptions import (
    VSSAPIError,
    VSSConnectionError,
    VSSRetryExhaustedError,
)


@pytest.fixture
def handler():
    return ErrorHandler()


@pytest.fixture
def no_sleep():
    with patch("time.sleep") as sleep:
        yield sleep


class TestHandleError:
    def test_logs_the_operation_and_message(self, handler, caplog):
        with caplog.at_level("ERROR"):
            handler.handle_error(VSSAPIError("upstream 500"), "verifyAlert")

        assert "Error during verifyAlert: upstream 500" in caplog.text

    def test_context_is_appended(self, handler, caplog):
        with caplog.at_level("ERROR"):
            handler.handle_error(VSSAPIError("boom"), "verifyAlert", {"sensor": "cam-1"})

        assert "Context: {'sensor': 'cam-1'}" in caplog.text

    def test_expected_vss_errors_are_logged_without_a_stack_trace(self, handler, caplog):
        with caplog.at_level("ERROR"):
            handler.handle_error(VSSConnectionError("refused"), "connect")

        assert "Stack trace" not in caplog.text

    def test_unexpected_errors_include_a_stack_trace(self, handler, caplog):
        with caplog.at_level("ERROR"):
            handler.handle_error(ValueError("unexpected"), "connect")

        assert "Stack trace" in caplog.text

    def test_no_context_leaves_the_message_clean(self, handler, caplog):
        with caplog.at_level("ERROR"):
            handler.handle_error(VSSAPIError("boom"), "verifyAlert")

        assert "Context" not in caplog.text


class TestWithRetry:
    def test_successful_call_is_not_retried(self, handler, no_sleep):
        func = MagicMock(return_value="ok")
        decorated = handler.with_retry()(func)

        assert decorated() == "ok"
        assert func.call_count == 1
        no_sleep.assert_not_called()

    def test_arguments_and_return_value_pass_through(self, handler, no_sleep):
        decorated = handler.with_retry()(lambda a, b=None: (a, b))
        assert decorated(1, b=2) == (1, 2)

    def test_retries_until_success(self, handler, no_sleep):
        func = MagicMock(side_effect=[VSSAPIError("1"), VSSAPIError("2"), "ok"])
        func.__name__ = "verify_alert"
        decorated = handler.with_retry(max_retries=3)(func)

        assert decorated() == "ok"
        assert func.call_count == 3

    def test_exhausted_retries_raise_vss_retry_exhausted(self, handler, no_sleep):
        func = MagicMock(side_effect=VSSAPIError("always fails"))
        func.__name__ = "verify_alert"
        decorated = handler.with_retry(max_retries=3)(func)

        with pytest.raises(VSSRetryExhaustedError, match="failed after 3 attempts"):
            decorated()

        assert func.call_count == 3

    def test_original_error_is_chained(self, handler, no_sleep):
        original = VSSAPIError("upstream 500")
        func = MagicMock(side_effect=original)
        func.__name__ = "verify_alert"

        with pytest.raises(VSSRetryExhaustedError) as exc_info:
            handler.with_retry(max_retries=2)(func)()

        assert exc_info.value.__cause__ is original

    def test_backoff_is_exponential(self, handler, no_sleep):
        func = MagicMock(side_effect=VSSAPIError("boom"))
        func.__name__ = "verify_alert"

        with pytest.raises(VSSRetryExhaustedError):
            handler.with_retry(max_retries=4, retry_delay=1.0, backoff_factor=2.0)(func)()

        # Three sleeps for four attempts: 1, 2, 4 — never after the last one.
        assert [call.args[0] for call in no_sleep.call_args_list] == [1.0, 2.0, 4.0]

    def test_custom_delay_and_factor_are_honoured(self, handler, no_sleep):
        func = MagicMock(side_effect=VSSAPIError("boom"))
        func.__name__ = "verify_alert"

        with pytest.raises(VSSRetryExhaustedError):
            handler.with_retry(max_retries=3, retry_delay=0.5, backoff_factor=3.0)(func)()

        assert [call.args[0] for call in no_sleep.call_args_list] == [0.5, 1.5]

    def test_single_attempt_never_sleeps(self, handler, no_sleep):
        func = MagicMock(side_effect=VSSAPIError("boom"))
        func.__name__ = "verify_alert"

        with pytest.raises(VSSRetryExhaustedError):
            handler.with_retry(max_retries=1)(func)()

        no_sleep.assert_not_called()
        assert func.call_count == 1

    def test_unlisted_exception_types_propagate_immediately(self, handler, no_sleep):
        func = MagicMock(side_effect=ValueError("not retriable"))
        decorated = handler.with_retry(max_retries=3, exceptions=(VSSAPIError,))(func)

        with pytest.raises(ValueError, match="not retriable"):
            decorated()

        assert func.call_count == 1

    def test_listed_exception_types_are_retried(self, handler, no_sleep):
        func = MagicMock(side_effect=[VSSConnectionError("refused"), "ok"])
        func.__name__ = "connect"
        decorated = handler.with_retry(max_retries=3, exceptions=(VSSConnectionError,))(func)

        assert decorated() == "ok"

    def test_function_metadata_is_preserved(self, handler):
        @handler.with_retry()
        def verify_alert():
            """Docstring."""

        assert verify_alert.__name__ == "verify_alert"
        assert verify_alert.__doc__ == "Docstring."


class TestLogRequestError:
    def test_logs_the_operation_and_error(self, handler, caplog):
        with caplog.at_level("ERROR"):
            handler.log_request_error("upload", VSSAPIError("bad gateway"))

        assert "Request error during upload: bad gateway" in caplog.text

    def test_response_status_is_logged_when_available(self, handler, caplog):
        error = VSSAPIError("bad gateway")
        error.response = MagicMock(status_code=502, text="upstream down")

        with caplog.at_level("ERROR"):
            handler.log_request_error("upload", error)

        assert "Response status: 502" in caplog.text
        assert "Response message: upstream down" in caplog.text

    def test_response_without_text_is_tolerated(self, handler, caplog):
        error = VSSAPIError("bad gateway")
        response = MagicMock(status_code=502)
        del response.text
        error.response = response

        with caplog.at_level("ERROR"):
            handler.log_request_error("upload", error)

        assert "Response status: 502" in caplog.text
        assert "Response message" not in caplog.text

    def test_request_details_are_logged(self, handler, caplog):
        with caplog.at_level("ERROR"):
            handler.log_request_error("upload", VSSAPIError("x"), {"url": "http://vss/api"})

        assert "Request details: {'url': 'http://vss/api'}" in caplog.text


class TestCreateSafeWrapper:
    def test_successful_call_returns_normally(self, handler):
        decorated = handler.create_safe_wrapper()(lambda: "ok")
        assert decorated() == "ok"

    def test_failure_returns_the_default(self, handler):
        def boom():
            raise VSSAPIError("upstream 500")

        assert handler.create_safe_wrapper(default_return=[])(boom)() == []

    def test_default_is_none_when_unspecified(self, handler):
        def boom():
            raise RuntimeError("boom")

        assert handler.create_safe_wrapper()(boom)() is None

    def test_arguments_pass_through(self, handler):
        decorated = handler.create_safe_wrapper()(lambda a, b=None: (a, b))
        assert decorated(1, b=2) == (1, 2)

    def test_the_error_is_logged(self, handler, caplog):
        def verify_alert():
            raise VSSAPIError("upstream 500")

        with caplog.at_level("ERROR"):
            handler.create_safe_wrapper()(verify_alert)()

        assert "Error during verify_alert: upstream 500" in caplog.text

    def test_function_metadata_is_preserved(self, handler):
        @handler.create_safe_wrapper()
        def verify_alert():
            """Docstring."""

        assert verify_alert.__name__ == "verify_alert"
