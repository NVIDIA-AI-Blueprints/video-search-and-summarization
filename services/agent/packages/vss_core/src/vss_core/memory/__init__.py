# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""NAT-free unified memory library (``nv.vss.memory/1.0``).

Bare ``import vss_core.memory`` must not pull elasticsearch, NAT, or torch.
Heavy backends load lazily through :func:`build_memory_service`.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING
from typing import Any

__all__ = [
    "SCHEMA_ID",
    "SUMMARY_SCHEMA_ID",
    "InMemoryStore",
    "JobFilters",
    "JobInfo",
    "MemoryAdapter",
    "MemoryInput",
    "MemoryNotFoundError",
    "MemoryOutput",
    "MemoryQuery",
    "MemoryService",
    "MemoryStore",
    "SearchAdapter",
    "SummaryAdapter",
    "SummaryEvent",
    "SummaryExtension",
    "UnifiedMemoryRecord",
    "build_memory_service",
    "get_adapter",
    "register_adapter",
]

_LAZY_EXPORTS = {
    "SCHEMA_ID": ".models",
    "UnifiedMemoryRecord": ".models",
    "MemoryInput": ".models",
    "MemoryOutput": ".models",
    "JobInfo": ".models",
    "MemoryStore": ".store",
    "MemoryQuery": ".store",
    "JobFilters": ".store",
    "InMemoryStore": ".backends.in_memory",
    "MemoryService": ".service",
    "MemoryNotFoundError": ".service",
    "build_memory_service": ".service",
    "SummaryAdapter": ".adapters",
    "SUMMARY_SCHEMA_ID": ".summary",
    "SummaryEvent": ".summary",
    "SummaryExtension": ".summary",
    "SearchAdapter": ".adapters",
    "register_adapter": ".adapters",
    "get_adapter": ".adapters",
    "MemoryAdapter": ".adapters",
}

if TYPE_CHECKING:
    from .adapters import MemoryAdapter
    from .adapters import SearchAdapter
    from .adapters import SummaryAdapter
    from .adapters import get_adapter
    from .adapters import register_adapter
    from .backends.in_memory import InMemoryStore
    from .models import SCHEMA_ID
    from .models import JobInfo
    from .models import MemoryInput
    from .models import MemoryOutput
    from .models import UnifiedMemoryRecord
    from .service import MemoryNotFoundError
    from .service import MemoryService
    from .service import build_memory_service
    from .store import JobFilters
    from .store import MemoryQuery
    from .store import MemoryStore
    from .summary import SUMMARY_SCHEMA_ID
    from .summary import SummaryEvent
    from .summary import SummaryExtension


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
