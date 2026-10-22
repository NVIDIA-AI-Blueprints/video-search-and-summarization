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
"""Custom exceptions for configuration management."""


class ConfigurationError(Exception):
    """Base exception for configuration management errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigurationNotFoundError(ConfigurationError):
    """Raised when a configuration is not found."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Configuration with name '{name}' not found")
        self.name = name


class ConfigurationAlreadyExistsError(ConfigurationError):
    """Raised when trying to create a configuration that already exists."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Configuration with name '{name}' already exists")
        self.name = name


class ConfigurationValidationError(ConfigurationError):
    """Raised when configuration data validation fails."""

    def __init__(self, message: str) -> None:
        super().__init__(f"Invalid configuration data: {message}")


class ConfigurationUpdateError(ConfigurationError):
    """Raised when configuration update fails due to business rules."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ConfigurationOperationError(ConfigurationError):
    """Raised when a configuration operation fails unexpectedly."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
