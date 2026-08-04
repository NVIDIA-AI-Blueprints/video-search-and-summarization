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
Unit tests for src/via_exception.py

Tests custom ViaException error handling.
"""
from unittest.mock import patch

import pytest

from via_exception import ViaException


@pytest.mark.unit
def test_via_exception_default_values():
    """Test ViaException with default code and status."""
    exc = ViaException("Something went wrong")
    assert exc.message == "Something went wrong"
    assert exc.code == "InternalServerError"
    assert exc.status_code == 500


@pytest.mark.unit
def test_via_exception_with_custom_code():
    """Test ViaException with custom error code."""
    exc = ViaException("Invalid input", code="InvalidParameters")
    assert exc.message == "Invalid input"
    assert exc.code == "InvalidParameters"
    assert exc.status_code == 500  # Default status


@pytest.mark.unit
def test_via_exception_with_custom_status():
    """Test ViaException with custom HTTP status code."""
    exc = ViaException("Not found", code="NotFound", status_code=404)
    assert exc.message == "Not found"
    assert exc.code == "NotFound"
    assert exc.status_code == 404


@pytest.mark.unit
def test_via_exception_bad_request():
    """Test ViaException for 400 Bad Request."""
    exc = ViaException("Bad request data", code="BadRequest", status_code=400)
    assert exc.message == "Bad request data"
    assert exc.code == "BadRequest"
    assert exc.status_code == 400


@pytest.mark.unit
def test_via_exception_unauthorized():
    """Test ViaException for 401 Unauthorized."""
    exc = ViaException("Authentication required", code="Unauthorized", status_code=401)
    assert exc.message == "Authentication required"
    assert exc.code == "Unauthorized"
    assert exc.status_code == 401


@pytest.mark.unit
def test_via_exception_str_representation():
    """Test __str__ method of ViaException."""
    exc = ViaException("Test error", code="TestError", status_code=418)
    str_repr = str(exc)
    assert "ViaException" in str_repr
    assert "TestError" in str_repr
    assert "Test error" in str_repr


@pytest.mark.unit
def test_via_exception_properties_are_readonly():
    """Test that exception properties are accessible."""
    exc = ViaException("Property test", code="PropertyError", status_code=503)

    # Properties should be readable
    assert exc.status_code == 503
    assert exc.code == "PropertyError"
    assert exc.message == "Property test"


@pytest.mark.unit
def test_via_exception_is_exception():
    """Test that ViaException is an Exception subclass."""
    exc = ViaException("Test")
    assert isinstance(exc, Exception)
    assert isinstance(exc, ViaException)


@pytest.mark.unit
def test_via_exception_can_be_raised():
    """Test that ViaException can be raised and caught."""
    with pytest.raises(ViaException) as exc_info:
        raise ViaException("Raised exception", code="TestRaise", status_code=422)

    exc = exc_info.value
    assert exc.message == "Raised exception"
    assert exc.code == "TestRaise"
    assert exc.status_code == 422


@pytest.mark.unit
def test_via_exception_with_empty_message():
    """Test ViaException with empty message."""
    exc = ViaException("", code="EmptyMessage", status_code=500)
    assert exc.message == ""
    assert exc.code == "EmptyMessage"
    assert exc.status_code == 500


@pytest.mark.unit
def test_via_exception_with_multiline_message():
    """Test ViaException with multiline error message."""
    message = """First line of error
Second line of error
Third line of error"""
    exc = ViaException(message, code="MultilineError")
    assert exc.message == message
    assert "\n" in exc.message


@pytest.mark.unit
def test_via_exception_realistic_scenarios():
    """Test realistic ViaException usage scenarios."""
    # File not found
    exc1 = ViaException("File 'video.mp4' not found", code="FileNotFound", status_code=404)
    assert exc1.status_code == 404

    # Invalid parameter
    exc2 = ViaException(
        "Parameter 'chunk_size' must be positive", code="InvalidParameters", status_code=400
    )
    assert exc2.status_code == 400

    # Service unavailable
    exc3 = ViaException("VLM service is unavailable", code="ServiceUnavailable", status_code=503)
    assert exc3.status_code == 503

    # Timeout
    exc4 = ViaException("Request timeout after 30 seconds", code="Timeout", status_code=504)
    assert exc4.status_code == 504


@pytest.mark.unit
def test_via_exception_extra_args_forwarded():
    exc = ViaException("test msg", "CustomCode", 400, "extra1", "extra2")
    assert exc.args == ("CustomCode", "test msg", "extra1", "extra2")
    assert exc.message == "test msg"
    assert exc.code == "CustomCode"
    assert exc.status_code == 400


@pytest.mark.unit
def test_via_exception_super_init_sets_args():
    exc = ViaException("hello world")
    assert exc.args == ("InternalServerError", "hello world")


@pytest.mark.unit
def test_via_exception_logger_error_called():
    with patch("via_exception.logger") as mock_logger:
        ViaException("logged error message", code="LogTest", status_code=500)
        mock_logger.error.assert_called_once_with("logged error message")


@pytest.mark.unit
def test_via_exception_caught_as_generic_exception():
    try:
        raise ViaException("catch me", code="CatchCode", status_code=418)
    except Exception as e:
        assert isinstance(e, ViaException)
        assert e.message == "catch me"
        assert e.code == "CatchCode"
        assert e.status_code == 418


@pytest.mark.unit
def test_via_exception_properties_are_not_settable():
    with patch("via_exception.logger"):
        exc = ViaException("readonly")
    with pytest.raises(AttributeError):
        exc.status_code = 999
    with pytest.raises(AttributeError):
        exc.code = "new"
    with pytest.raises(AttributeError):
        exc.message = "new"


@pytest.mark.unit
def test_via_exception_logger_called_per_instance():
    with patch("via_exception.logger") as mock_logger:
        ViaException("first error")
        ViaException("second error")
        assert mock_logger.error.call_count == 2
        mock_logger.error.assert_any_call("first error")
        mock_logger.error.assert_any_call("second error")


@pytest.mark.unit
def test_via_exception_with_many_extra_args():
    with patch("via_exception.logger"):
        exc = ViaException("msg", "Code", 400, "a", "b", "c")
    assert exc.args == ("Code", "msg", "a", "b", "c")
    assert exc.message == "msg"
    assert exc.code == "Code"
    assert exc.status_code == 400


@pytest.mark.unit
def test_via_exception_super_init_with_no_extra_args():
    with patch("via_exception.logger"):
        exc = ViaException("hello")
    assert exc.args == ("InternalServerError", "hello")


@pytest.mark.unit
def test_via_exception_chained_with_from():
    try:
        try:
            raise ValueError("root cause")
        except ValueError as orig:
            raise ViaException("wrapper", code="Chained", status_code=500) from orig
    except ViaException as exc:
        assert exc.__cause__ is not None
        assert isinstance(exc.__cause__, ValueError)
        assert exc.message == "wrapper"
