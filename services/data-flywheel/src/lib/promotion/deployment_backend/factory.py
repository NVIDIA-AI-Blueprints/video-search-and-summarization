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

"""Factory for creating deployment backends.

Mirrors ``src/lib/deployment/adapters/factory.py``: a registry keyed by a
string ``kind`` with a ``register`` decorator and a ``create`` constructor.
"""

from typing import Any, ClassVar

from src.lib.promotion.deployment_backend.base import DeploymentBackend
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.promotion.deployment_backend.factory")


class DeploymentBackendFactory:
    """Factory for creating deployment backends via a registry pattern.

    Example:
        @DeploymentBackendFactory.register("model_manager")
        class ModelManagerBackend(DeploymentBackend):
            ...

        backend = DeploymentBackendFactory.create("model_manager", config)
    """

    _backends: ClassVar[dict[str, type[DeploymentBackend]]] = {}

    @classmethod
    def register(cls, kind: str):
        """Decorator to register a backend class for a ``kind`` string."""

        def decorator(backend_class: type[DeploymentBackend]):
            if kind in cls._backends:
                logger.warning(
                    f"Overwriting existing deployment backend for kind {kind}: "
                    f"{cls._backends[kind].__name__} -> {backend_class.__name__}"
                )
            cls._backends[kind] = backend_class
            logger.debug(f"Registered deployment backend {backend_class.__name__} for kind {kind}")
            return backend_class

        return decorator

    @classmethod
    def create(cls, kind: str, config: dict[str, Any] | None = None) -> DeploymentBackend:
        """Create a backend instance for the specified ``kind``.

        Raises:
            ValueError: If no backend is registered for ``kind``.
        """
        if kind not in cls._backends:
            available = list(cls._backends.keys())
            raise ValueError(f"Unknown deployment backend kind: '{kind}'. Available: {available}")

        backend_class = cls._backends[kind]
        logger.info(f"Creating deployment backend {backend_class.__name__} for kind {kind}")
        return backend_class(config or {})

    @classmethod
    def get_available_kinds(cls) -> list[str]:
        """Return the list of registered backend kinds."""
        return list(cls._backends.keys())


def build_deployment_backend(kind: str = "model_manager") -> DeploymentBackend:
    """Build the configured deployment backend from system settings.

    The single place the base URL / timeout / pull-timeout assembly lives, so
    callers that need a backend do not each re-derive it — and so a pipeline
    handler does not have to reach through the Promotion Controller's internals
    to get one.

    ``settings`` is imported lazily on purpose: this module is otherwise free of
    DFW config, and keeping it that way means the factory can be imported (and
    unit-tested) without a configured system.
    """
    from src.config import settings

    mm = settings.system_config.model_manager
    return DeploymentBackendFactory.create(
        kind,
        {
            "base_url": mm.base_url,
            "timeout": mm.timeout,
            # Dedicated longer read budget for the pull download (large/slow
            # weights); apply/convergence/cleanup keep the short ``timeout``.
            "pull_timeout": getattr(mm, "pull_timeout", 900.0),
        },
    )
