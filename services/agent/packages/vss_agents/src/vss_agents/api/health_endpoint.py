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
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

from collections.abc import AsyncGenerator
import logging
from typing import Any

from nat.builder.builder import Builder
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig

logger = logging.getLogger(__name__)


class HealthEndpointConfig(FunctionBaseConfig, name="health_endpoint"):
    """Configuration for the health endpoint."""

    description: str = "Check if the service is healthy"


@register_function(config_type=HealthEndpointConfig)
async def health_endpoint(config: HealthEndpointConfig, _: Builder) -> AsyncGenerator[FunctionInfo]:
    """Health endpoint that returns service status."""

    async def _health_endpoint(_: None) -> dict[str, Any]:
        """
        Check if the service is healthy.

        Returns:
            dict: Health status with isAlive flag.
        """
        return {"isAlive": True}

    logger.info(f"{__name__}: health_endpoint registered")

    # Create a Generic AI-Q tool that can be used with any supported LLM framework
    yield FunctionInfo.create(
        single_fn=_health_endpoint,
        description=config.description,
    )
