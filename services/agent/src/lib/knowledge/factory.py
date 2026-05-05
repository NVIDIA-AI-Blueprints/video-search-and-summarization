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
"""Backend registry and singleton factory."""

from __future__ import annotations

import asyncio
import importlib
import logging
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from pydantic import BaseModel

    from .base import BackendAdapter

logger = logging.getLogger(__name__)

_registry: dict[str, tuple[type[BackendAdapter], type[BaseModel]]] = {}
_instances: dict[tuple[str, frozenset[tuple[str, Any]]], BackendAdapter] = {}
_lock = asyncio.Lock()

_LAZY_BACKENDS: dict[str, str] = {
    "frag_api": "lib.knowledge.adapters.frag_api",
    "es_caption": "lib.knowledge.adapters.es_caption",
}


def register_adapter(name: str, *, config_type: type[BaseModel]):
    """Register a BackendAdapter under `name` with its config class."""

    def _decorator(cls: type[BackendAdapter]) -> type[BackendAdapter]:
        cls.backend_name = name
        if name in _registry and _registry[name][0] is not cls:
            logger.warning("Re-registering knowledge backend '%s'", name)
        _registry[name] = (cls, config_type)
        return cls

    return _decorator


def _ensure_loaded(backend: str) -> None:
    if backend not in _registry and backend in _LAZY_BACKENDS:
        importlib.import_module(_LAZY_BACKENDS[backend])
    if backend not in _registry:
        available = sorted(set(_registry) | set(_LAZY_BACKENDS))
        raise ValueError(f"Unknown knowledge backend '{backend}'. Available: {available}")


def get_config_cls(backend: str) -> type[BaseModel]:
    _ensure_loaded(backend)
    return _registry[backend][1]


def _freeze(config: dict[str, Any]) -> frozenset[tuple[str, Any]]:
    def _hashable(v: Any) -> Any:
        if isinstance(v, dict):
            return frozenset((k, _hashable(x)) for k, x in v.items())
        if isinstance(v, list):
            return tuple(_hashable(x) for x in v)
        return v

    return frozenset((k, _hashable(v)) for k, v in (config or {}).items())


async def get_retriever(
    backend: str,
    config: dict[str, Any] | BaseModel | None = None,
) -> BackendAdapter:
    """Return a singleton adapter for (backend, config)."""
    _ensure_loaded(backend)
    cls, config_cls = _registry[backend]

    if config is None:
        config = config_cls()
    elif isinstance(config, dict):
        config = config_cls(**config)
    elif not isinstance(config, config_cls):
        raise TypeError(f"Backend '{backend}' expects {config_cls.__name__}, got {type(config).__name__}")

    cache_key = (backend, _freeze(config.model_dump()))
    async with _lock:
        instance = _instances.get(cache_key)
        if instance is None:
            instance = cls(config=config)
            _instances[cache_key] = instance
            logger.info("Initialised knowledge backend '%s'", backend)
    return instance
