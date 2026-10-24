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

"""Deployment Backend package.

The ``model_manager_client`` import triggers registration of the
``model_manager`` backend with the factory (same trick as the deployment
adapters ``__init__.py``).
"""

# Import the client to auto-register it with the factory.
from src.lib.promotion.deployment_backend import model_manager_client  # noqa: F401
from src.lib.promotion.deployment_backend.base import (
    ApplyResult,
    BackendError,
    ConvergenceReason,
    ConvergenceReport,
    DeploymentBackend,
    PromotionControl,
    PullResult,
    RoutingEntry,
)
from src.lib.promotion.deployment_backend.factory import (
    DeploymentBackendFactory,
    build_deployment_backend,
)

__all__ = [
    "ApplyResult",
    "BackendError",
    "ConvergenceReason",
    "ConvergenceReport",
    "DeploymentBackend",
    "DeploymentBackendFactory",
    "PromotionControl",
    "PullResult",
    "RoutingEntry",
    "build_deployment_backend",
]
