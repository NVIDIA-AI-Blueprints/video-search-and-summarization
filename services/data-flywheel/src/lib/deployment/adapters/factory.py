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

"""Factory for creating deployment adapters."""

from typing import Any, ClassVar

from src.lib.deployment.adapters.base import BaseDeploymentAdapter, DeploymentPlatform
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.deployment.factory")


class DeploymentAdapterFactory:
    """Factory for creating deployment adapters.

    This factory uses a registry pattern to manage available adapters.
    Adapters register themselves using the @register decorator.

    Example:
        @DeploymentAdapterFactory.register(DeploymentPlatform.DATAROBOT)
        class DataRobotAdapter(BaseDeploymentAdapter):
            ...

        # Create adapter instance
        adapter = DeploymentAdapterFactory.create(DeploymentPlatform.DATAROBOT, config)
    """

    _adapters: ClassVar[dict[DeploymentPlatform, type[BaseDeploymentAdapter]]] = {}

    @classmethod
    def register(cls, platform: DeploymentPlatform):
        """Decorator to register an adapter class for a platform.

        Args:
            platform: The DeploymentPlatform this adapter handles

        Returns:
            Decorator function that registers the adapter class

        Example:
            @DeploymentAdapterFactory.register(DeploymentPlatform.DATAROBOT)
            class DataRobotAdapter(BaseDeploymentAdapter):
                ...
        """

        def decorator(adapter_class: type[BaseDeploymentAdapter]):
            if platform in cls._adapters:
                logger.warning(
                    f"Overwriting existing adapter for platform {platform}: "
                    f"{cls._adapters[platform].__name__} -> {adapter_class.__name__}"
                )
            cls._adapters[platform] = adapter_class
            logger.debug(f"Registered adapter {adapter_class.__name__} for platform {platform}")
            return adapter_class

        return decorator

    @classmethod
    def create(
        cls,
        platform: DeploymentPlatform | str,
        config: dict[str, Any] | None = None,
    ) -> BaseDeploymentAdapter:
        """Create an adapter instance for the specified platform.

        Args:
            platform: The deployment platform (enum or string)
            config: Platform-specific configuration dictionary

        Returns:
            An instance of the appropriate adapter

        Raises:
            ValueError: If platform is unknown
            NotImplementedError: If platform exists but no adapter is implemented
        """
        # Convert string to enum if necessary
        if isinstance(platform, str):
            try:
                platform = DeploymentPlatform(platform.lower())
            except ValueError:
                raise ValueError(
                    f"Unknown platform: '{platform}'. "
                    f"Valid platforms: {[p.value for p in DeploymentPlatform]}"
                ) from None

        if platform not in cls._adapters:
            # Platform is valid but no adapter implemented yet
            available = [p.value for p in cls._adapters.keys()]
            raise NotImplementedError(
                f"Platform '{platform.value}' is not yet implemented. "
                f"Currently available adapters: {available}. "
                f"To add support for '{platform.value}', create an adapter in "
                f"src/lib/deployment/adapters/{platform.value}/"
            )

        adapter_class = cls._adapters[platform]
        logger.info(f"Creating adapter {adapter_class.__name__} for platform {platform.value}")
        return adapter_class(config or {})

    @classmethod
    def get_available_platforms(cls) -> list[DeploymentPlatform]:
        """Get list of platforms with registered adapters.

        Returns:
            List of DeploymentPlatform values that have registered adapters
        """
        return list(cls._adapters.keys())

    @classmethod
    def is_platform_supported(cls, platform: DeploymentPlatform | str) -> bool:
        """Check if a platform has a registered adapter.

        Args:
            platform: The platform to check (enum or string)

        Returns:
            True if an adapter is registered for the platform
        """
        if isinstance(platform, str):
            try:
                platform = DeploymentPlatform(platform.lower())
            except ValueError:
                return False

        return platform in cls._adapters
