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
"""Unit tests for code_executor module."""

from unittest.mock import patch

from pydantic import ValidationError
import pytest

from vss_agents.tools.code_executor.docker_backend import cleanup_docker_resources
from vss_agents.tools.code_executor.python_executor import CodeExecutorConfig
from vss_agents.tools.code_executor.python_executor import CodeExecutorInput
from vss_agents.tools.code_executor.python_executor import CodeExecutorOutput


class TestCodeExecutorConfig:
    """Test CodeExecutorConfig model."""

    def test_config_creation(self):
        config = CodeExecutorConfig(
            base_image="python:3.11-slim",
            language_packages=["numpy", "pandas"],
        )
        assert config.backend == "docker"
        assert config.gpu is False
        assert config.base_image == "python:3.11-slim"
        assert config.language_packages == ["numpy", "pandas"]

    def test_config_with_gpu(self):
        config = CodeExecutorConfig(
            base_image="python:3.11",
            language_packages=[],
            gpu=True,
        )
        assert config.gpu is True

    def test_config_missing_base_image(self):
        with pytest.raises(ValidationError):
            CodeExecutorConfig(language_packages=[])

    def test_config_missing_language_packages(self):
        with pytest.raises(ValidationError):
            CodeExecutorConfig(base_image="python:3.11")

    def test_config_empty_packages(self):
        config = CodeExecutorConfig(
            base_image="python:3.11",
            language_packages=[],
        )
        assert config.language_packages == []


class TestCodeExecutorInput:
    """Test CodeExecutorInput model."""

    def test_input_with_code(self):
        input_data = CodeExecutorInput(
            code="print('hello')",
            files={},
        )
        assert input_data.code == "print('hello')"
        assert input_data.files == {}

    def test_input_with_files(self):
        input_data = CodeExecutorInput(
            code="import data",
            files={"data.py": "x = 42"},
        )
        assert input_data.files == {"data.py": "x = 42"}

    def test_input_none_code(self):
        input_data = CodeExecutorInput(
            code=None,
            files={},
        )
        assert input_data.code is None

    def test_input_multiple_files(self):
        input_data = CodeExecutorInput(
            code="main code",
            files={
                "utils.py": "def helper(): pass",
                "config.py": "DEBUG = True",
                "data.json": '{"key": "value"}',
            },
        )
        assert len(input_data.files) == 3


class TestCodeExecutorOutput:
    """Test CodeExecutorOutput model."""

    def test_output_success(self):
        output = CodeExecutorOutput(message="Hello, World!")
        assert output.message == "Hello, World!"

    def test_output_error(self):
        output = CodeExecutorOutput(message="Error: {'exit_code': 1, 'stderr': 'NameError'}")
        assert "Error" in output.message

    def test_output_empty_message(self):
        output = CodeExecutorOutput(message="")
        assert output.message == ""

    def test_output_multiline(self):
        output = CodeExecutorOutput(message="Line 1\nLine 2\nLine 3")
        assert "\n" in output.message

    def test_output_serialization(self):
        output = CodeExecutorOutput(message="test output")
        data = output.model_dump()
        assert data["message"] == "test output"


class TestDockerBackendModule:
    """Test docker_backend module functions."""

    def test_cleanup_docker_resources(self):
        """Test cleanup_docker_resources calls ImageBuilder.reset_instance (covers line 23)."""
        with patch("vss_agents.tools.code_executor.docker_backend.ImageBuilder") as mock_builder:
            cleanup_docker_resources()
            mock_builder.reset_instance.assert_called_once()
