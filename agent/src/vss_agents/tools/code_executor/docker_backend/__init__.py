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
"""
Docker backend for code execution.

The ImageBuilder is a singleton that manages Docker images across all tools.
Images are automatically cleaned up when the process exits via atexit handler.

Manual cleanup should only be done in special cases (e.g., testing) as it affects all tools.
"""

from .docker_executor import DockerExecutor
from .image_builder import ImageBuilder


def cleanup_docker_resources() -> None:
    """
    Manually cleanup all Docker resources managed by the ImageBuilder singleton.

    WARNING: This affects ALL tools using the ImageBuilder singleton.
    Only call this when you're sure no other tools are using Docker images.

    In normal operation, cleanup happens automatically on process exit.
    """
    ImageBuilder.reset_instance()


__all__ = ["DockerExecutor", "ImageBuilder", "cleanup_docker_resources"]
