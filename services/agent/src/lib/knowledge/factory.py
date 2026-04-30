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
"""Backend registry and singleton factory.

Adapters self-register via `@register_adapter("name")` at import time. The
factory caches one instance per (backend, frozen-config) key so the same
retriever can be shared across the agent tool and any other in-process
caller.
"""
from __future__ import annotations

import importlib
import logging
import threading
from typing import Any

from .base import BackendAdapter

logger = logging.getLogger(__name__)

_registry: dict[str, type[BackendAdapter]] = {}
_instances: dict[tuple[str, frozenset[tuple[str, Any]]], BackendAdapter] = {}
_lock = threading.RLock()

# Lazy-import map: backend name -> module to import to trigger self-registration.
# Avoids importing every backend's deps when only one is configured.
_LAZY_BACKENDS: dict[str, str] = {
    "frag_api": "lib.knowledge.adapters.frag_api",
    "frag_lib": "lib.knowledge.adapters.frag_lib",
}


def register_adapter(name: str):
    """Decorator: register a BackendAdapter subclass under `name`."""

    def _decorator(cls: type[BackendAdapter]) -> type[BackendAdapter]:
        if name in _registry and _registry[name] is not cls:
            logger.warning("Re-registering knowledge backend '%s'", name)
        _registry[name] = cls
        return cls

    return _decorator


def _freeze(config: dict[str, Any]) -> frozenset[tuple[str, Any]]:
    """Produce a hashable cache key from a config dict."""

    def _hashable(v: Any) -> Any:
        if isinstance(v, dict):
            return frozenset((k, _hashable(x)) for k, x in v.items())
        if isinstance(v, list):
            return tuple(_hashable(x) for x in v)
        return v

    return frozenset((k, _hashable(v)) for k, v in (config or {}).items())


def get_retriever(backend: str, config: dict[str, Any] | None = None) -> BackendAdapter:
    """Return a singleton BackendAdapter for (backend, config).

    Triggers lazy import of the backend module if needed, so adapters'
    optional dependencies are only imported when actually used.
    """
    config = config or {}
    if backend not in _registry and backend in _LAZY_BACKENDS:
        importlib.import_module(_LAZY_BACKENDS[backend])

    if backend not in _registry:
        available = sorted(set(_registry) | set(_LAZY_BACKENDS))
        raise ValueError(f"Unknown knowledge backend '{backend}'. Available: {available}")

    cache_key = (backend, _freeze(config))
    with _lock:
        instance = _instances.get(cache_key)
        if instance is None:
            instance = _registry[backend](config=config)
            _instances[cache_key] = instance
            logger.info("Initialised knowledge backend '%s'", backend)
    return instance
