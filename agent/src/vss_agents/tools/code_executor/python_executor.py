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

from collections.abc import AsyncGenerator
import logging
import random
import string
from typing import Literal

from vss_agents.tools.code_executor import DockerExecutor

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class CodeExecutorConfig(FunctionBaseConfig, name="python_executor"):
    """Configuration for the Code Executor tool."""

    backend: Literal["docker"] = Field(
        "docker",
        description="Executor backend to be used",
    )
    gpu: bool = Field(
        False,
        description="Whether to use GPU in the container, only valid when backend is docker",
    )
    base_image: str = Field(
        ...,
        description="The base image of the runtime to be used, for example, 'python:3.11-slim'",
    )
    language_packages: list[str] = Field(
        ...,
        description="The packages to be installed in the container, for example, ['numpy', 'pandas']",
    )


class CodeExecutorInput(BaseModel):
    """Input for the Code Executor tool"""

    code: str | None = Field(
        None,
        description="The code to be executed, only valid when action is run",
    )
    files: dict[str, str] = Field(
        ...,
        description="The files to be mounted to the container, only valid when action is run",
    )


class CodeExecutorOutput(BaseModel):
    """Output for the Code Executor tool"""

    message: str = Field(..., description="The output of the code execution")


@register_function(config_type=CodeExecutorConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def python_executor(config: CodeExecutorConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """A tool that executes python code in a container
    Args:
        files: a dictionary of file paths and their contents, which will be mounted to the container and used by code
        code: the code to be executed, only valid when action is run
    Returns:
        AsyncGenerator[FunctionInfo, None]: A generator of FunctionInfo
    """
    # TODO: add executor backend for k8s
    # make a random name string for the image (lowercase only for Docker compatibility)
    image_name = "python-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    if config.backend == "docker":
        executor = DockerExecutor(gpu=config.gpu)
        # build images from the config
        logger.info(f"Building image {config.base_image}")
        executor.build_image(image_name, config.base_image, config.language_packages)
    logger.info(f"Built images: {executor.builder.get_all_images()}")

    async def _python_executor(code_executor_input: CodeExecutorInput) -> CodeExecutorOutput:
        """
        this tool first mount files' content to the container, based on the relative path,
        then run the code in the container, and return the output(stdout, stderr)
        Args:
            code_executor_input (CodeExecutorInput): The input for the Code Executor tool
        Returns:
            CodeExecutorOutput: The output of the code execution, if the code execution is successful, the message will be the stdout ONLY, otherwise the message will include stdout and stderr
        """
        code = code_executor_input.code or ""
        output = executor.run_code(code, code_executor_input.files, image=image_name)
        if output["exit_code"] == 0:
            return CodeExecutorOutput(message=f"{output['stdout']}")
        else:
            return CodeExecutorOutput(message=f"Error: {output}")

    yield FunctionInfo.create(
        single_fn=_python_executor,
        description="Execute code in a container",
        input_schema=CodeExecutorInput,
        single_output_schema=CodeExecutorOutput,
    )
