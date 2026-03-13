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
"""Additional unit tests for python_executor module to improve coverage."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.code_executor.python_executor import CodeExecutorConfig
from vss_agents.tools.code_executor.python_executor import CodeExecutorInput
from vss_agents.tools.code_executor.python_executor import CodeExecutorOutput


class TestCodeExecutorConfig:
    """Test CodeExecutorConfig model."""

    def test_required_fields(self):
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
            language_packages=[],
            gpu=True,
        )
        assert config.gpu is True

    def test_missing_base_image_raises(self):
        with pytest.raises(ValidationError):
            CodeExecutorConfig(language_packages=["numpy"])

    def test_missing_packages_raises(self):
        with pytest.raises(ValidationError):
            CodeExecutorConfig(base_image="python:3.11-slim")


class TestCodeExecutorInput:
    """Test CodeExecutorInput model."""

    def test_with_code(self):
        inp = CodeExecutorInput(
            code="print('hello')",
            files={"main.py": "print('hello')"},
        )
        assert inp.code == "print('hello')"
        assert "main.py" in inp.files

    def test_no_code(self):
        inp = CodeExecutorInput(files={"data.csv": "a,b\n1,2"})
        assert inp.code is None

    def test_empty_files(self):
        inp = CodeExecutorInput(files={})
        assert inp.files == {}


class TestCodeExecutorOutput:
    """Test CodeExecutorOutput model."""

    def test_success_output(self):
        output = CodeExecutorOutput(message="hello world")
        assert output.message == "hello world"

    def test_error_output(self):
        output = CodeExecutorOutput(message="Error: exit code 1")
        assert "Error" in output.message

    def test_missing_message_raises(self):
        with pytest.raises(ValidationError):
            CodeExecutorOutput()
