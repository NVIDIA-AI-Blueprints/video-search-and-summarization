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
"""Unit tests for python_executor module."""

from pydantic import ValidationError
import pytest

from vss_agents.tools.code_executor.python_executor import CodeExecutorConfig
from vss_agents.tools.code_executor.python_executor import CodeExecutorInput
from vss_agents.tools.code_executor.python_executor import CodeExecutorOutput


class TestCodeExecutorConfig:
    """Test CodeExecutorConfig model."""

    def test_with_required_fields(self):
        config = CodeExecutorConfig(
            base_image="python:3.11-slim",
            language_packages=["numpy", "pandas"],
        )
        assert config.backend == "docker"
        assert config.gpu is False
        assert config.base_image == "python:3.11-slim"
        assert config.language_packages == ["numpy", "pandas"]

    def test_with_gpu(self):
        config = CodeExecutorConfig(
            base_image="python:3.11-slim",
            language_packages=["numpy"],
            gpu=True,
        )
        assert config.gpu is True

    def test_empty_packages(self):
        config = CodeExecutorConfig(
            base_image="python:3.11-slim",
            language_packages=[],
        )
        assert config.language_packages == []

    def test_missing_base_image_fails(self):
        with pytest.raises(ValidationError):
            CodeExecutorConfig(language_packages=["numpy"])

    def test_missing_packages_fails(self):
        with pytest.raises(ValidationError):
            CodeExecutorConfig(base_image="python:3.11-slim")


class TestCodeExecutorInput:
    """Test CodeExecutorInput model."""

    def test_basic_input(self):
        input_data = CodeExecutorInput(
            code="print('hello')",
            files={},
        )
        assert input_data.code == "print('hello')"
        assert input_data.files == {}

    def test_with_files(self):
        input_data = CodeExecutorInput(
            code="import data",
            files={
                "data.py": "x = 42",
                "config.json": '{"key": "value"}',
            },
        )
        assert len(input_data.files) == 2
        assert "data.py" in input_data.files
        assert "config.json" in input_data.files

    def test_none_code(self):
        input_data = CodeExecutorInput(
            code=None,
            files={},
        )
        assert input_data.code is None

    def test_multiline_code(self):
        code = """
def hello():
    return "Hello, World!"

print(hello())
"""
        input_data = CodeExecutorInput(code=code, files={})
        assert "def hello():" in input_data.code


class TestCodeExecutorOutput:
    """Test CodeExecutorOutput model."""

    def test_successful_output(self):
        output = CodeExecutorOutput(message="Hello, World!")
        assert output.message == "Hello, World!"

    def test_error_output(self):
        output = CodeExecutorOutput(message="Error: NameError: name 'undefined' is not defined")
        assert "Error" in output.message

    def test_multiline_output(self):
        output = CodeExecutorOutput(message="Line 1\nLine 2\nLine 3")
        assert "Line 1" in output.message
        assert "Line 2" in output.message

    def test_serialization(self):
        output = CodeExecutorOutput(message="Test output")
        data = output.model_dump()
        assert "message" in data
        assert data["message"] == "Test output"
