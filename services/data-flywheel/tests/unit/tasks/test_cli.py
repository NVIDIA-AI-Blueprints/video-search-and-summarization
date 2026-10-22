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

import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def clean_cli_module():
    """Clean up CLI module before and after each test."""
    # Setup: Remove the CLI module from sys.modules if it exists
    if "src.tasks.cli" in sys.modules:
        del sys.modules["src.tasks.cli"]

    yield  # This is where the test runs

    # Teardown: Clean up after test (optional)
    if "src.tasks.cli" in sys.modules:
        del sys.modules["src.tasks.cli"]


@pytest.fixture
def mock_validate_llm_judge():
    """Mock the validate_llm_judge function."""
    with patch("src.backends.nmp.provider.NmpBackendProvider.validate_llm_judge") as mock:
        yield mock


@pytest.fixture
def mock_setup_logging():
    """Mock the setup_logging function and return a mock logger."""
    with patch("src.log_utils.setup_logging") as mock:
        mock_logger = MagicMock()
        mock.return_value = mock_logger
        yield mock, mock_logger


@pytest.fixture
def mock_celery_app():
    """Mock the celery_app object."""
    with patch("src.celery_app.celery_app") as mock:
        mock.__str__ = MagicMock(return_value="MockCeleryApp<test_app>")
        yield mock


@pytest.fixture
def all_mocks(mock_validate_llm_judge, mock_setup_logging, mock_celery_app):
    """Convenience fixture that provides all mocks together."""
    mock_setup_func, mock_logger = mock_setup_logging
    return {
        "validate_llm_judge": mock_validate_llm_judge,
        "setup_logging": mock_setup_func,
        "logger": mock_logger,
        "celery_app": mock_celery_app,
    }


@pytest.fixture
def mock_validate_llm_judge_error():
    """Mock validate_llm_judge that raises an exception."""

    def _mock_with_error(error_type, error_message):
        with patch(
            "src.backends.nmp.provider.NmpBackendProvider.validate_llm_judge",
            side_effect=error_type(error_message),
        ) as mock:
            yield mock

    return _mock_with_error


@pytest.fixture
def mock_setup_logging_error():
    """Mock setup_logging that raises an exception."""
    with patch(
        "src.log_utils.setup_logging", side_effect=Exception("Logging setup failed")
    ) as mock:
        yield mock


class TestCliModule:
    """Test suite for the CLI module."""

    def test_module_initialization(self, clean_cli_module, all_mocks):
        """Test that the CLI module initializes correctly with all dependencies."""
        # Import the module to trigger initialization
        import src.tasks.cli  # noqa: F401

        # Verify that setup_logging was called with correct parameter
        all_mocks["setup_logging"].assert_called_once_with("src.tasks.cli")

        # Verify that validate_llm_judge was called
        all_mocks["validate_llm_judge"].assert_called_once()

        # Verify that logger.info was called with celery_app
        all_mocks["logger"].info.assert_called_once()
        call_args = all_mocks["logger"].info.call_args[0][0]
        assert "Loaded" in call_args

    def test_logging_setup(self, clean_cli_module, all_mocks):
        """Test that logging is set up with the correct module name."""
        # Import the module
        import src.tasks.cli  # noqa: F401

        # Verify setup_logging was called with the correct module path
        all_mocks["setup_logging"].assert_called_once_with("src.tasks.cli")

    def test_validate_llm_judge_success_case(self, clean_cli_module, all_mocks):
        """Test that validate_llm_judge succeeds and doesn't interfere with module initialization."""
        # Explicitly set validate_llm_judge to return None (success case)
        all_mocks["validate_llm_judge"].return_value = None

        # Import the module should succeed without exceptions
        import src.tasks.cli  # noqa: F401

        # Verify validate_llm_judge was called exactly once
        # The function should be called with llm_judge_config and nmp_config arguments
        assert all_mocks["validate_llm_judge"].call_count == 1
        # Verify the module initialization completed successfully (logger.info was called)
        all_mocks["logger"].info.assert_called_once()

    def test_celery_app_logging(self, clean_cli_module, all_mocks):
        """Test that celery_app information is logged correctly."""
        # Import the module
        import src.tasks.cli  # noqa: F401

        # Verify that logger.info was called with celery_app information
        all_mocks["logger"].info.assert_called_once()
        logged_message = all_mocks["logger"].info.call_args[0][0]
        assert "Loaded" in logged_message
        assert "MockCeleryApp<test_app>" in logged_message

    @pytest.mark.parametrize(
        "error_type,error_message",
        [
            (Exception, "Validation failed"),
            (ValueError, "Invalid LLM configuration"),
            (RuntimeError, "LLM service unavailable"),
        ],
    )
    def test_validate_llm_judge_error_cases(
        self, clean_cli_module, mock_setup_logging, mock_celery_app, error_type, error_message
    ):
        """Test behavior when validate_llm_judge raises different types of exceptions."""
        mock_setup_func, mock_logger = mock_setup_logging

        with patch(
            "src.backends.nmp.provider.NmpBackendProvider.validate_llm_judge",
            side_effect=error_type(error_message),
        ) as mock_validate:
            # Import should raise the exception from validate_llm_judge
            with pytest.raises(error_type, match=error_message):
                import src.tasks.cli  # noqa: F401

            # Verify validate_llm_judge was called before the exception
            assert mock_validate.call_count == 1
            # Verify that setup_logging was called before the exception
            mock_setup_func.assert_called_once_with("src.tasks.cli")
            # Verify that logger.info was NOT called due to the exception
            mock_logger.info.assert_not_called()

    def test_setup_logging_exception_handling(
        self, clean_cli_module, mock_setup_logging_error, mock_celery_app, mock_validate_llm_judge
    ):
        """Test behavior when setup_logging raises an exception."""
        # Import should raise the exception from setup_logging
        with pytest.raises(Exception, match="Logging setup failed"):
            import src.tasks.cli  # noqa: F401

        # Verify setup_logging was called
        mock_setup_logging_error.assert_called_once_with("src.tasks.cli")
