# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""NAT-free unified memory library (``nv.vss.memory/1.0``).

Bare ``import vss_core.memory`` must not pull elasticsearch, NAT, or torch.
Heavy backends load lazily through :func:`build_memory_service`.

This package owns the schema, store, service, and adapter *contract*
(protocol / ``RecordBundle`` / helpers). Group-specific mappers live with
their command groups under ``vss_cli.<group>.memory_adapter``.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING
from typing import Any

__all__ = [
    "SCHEMA_ID",
    "EmbeddingProvider",
    "EmbeddingProviderError",
    "InMemoryStore",
    "JobFilters",
    "JobInfo",
    "LifecycleAdapter",
    "MemoryAdapter",
    "MemoryDecodeError",
    "MemoryInput",
    "MemoryNotFoundError",
    "MemoryNoteWriteResult",
    "MemoryOutput",
    "MemoryQuery",
    "MemoryService",
    "MemoryStore",
    "NestedCollectionError",
    "OpenAICompatibleEmbeddingProvider",
    "OpenClawDailyNoteStore",
    "PersistResult",
    "RecordBundle",
    "UnifiedMemoryRecord",
    "build_memory_service",
    "canonical_searchable_text",
    "content_hash",
    "get_adapter",
    "is_embedding_eligible",
    "register_adapter",
    "render_memory_note",
]

_LAZY_EXPORTS = {
    "SCHEMA_ID": ".models",
    "UnifiedMemoryRecord": ".models",
    "MemoryInput": ".models",
    "MemoryOutput": ".models",
    "JobInfo": ".models",
    "EmbeddingProvider": ".embeddings",
    "EmbeddingProviderError": ".embeddings",
    "OpenAICompatibleEmbeddingProvider": ".embeddings",
    "canonical_searchable_text": ".embeddings",
    "content_hash": ".embeddings",
    "is_embedding_eligible": ".embeddings",
    "MemoryStore": ".store",
    "MemoryQuery": ".store",
    "JobFilters": ".store",
    "MemoryDecodeError": ".store",
    "InMemoryStore": ".backends.in_memory",
    "MemoryService": ".service",
    "MemoryNotFoundError": ".service",
    "NestedCollectionError": ".service",
    "PersistResult": ".service",
    "build_memory_service": ".service",
    "RecordBundle": ".adapters",
    "LifecycleAdapter": ".adapters",
    "register_adapter": ".adapters",
    "get_adapter": ".adapters",
    "MemoryAdapter": ".adapters",
    "MemoryNoteWriteResult": ".notes",
    "OpenClawDailyNoteStore": ".notes",
    "render_memory_note": ".notes",
}

if TYPE_CHECKING:
    from .adapters import LifecycleAdapter
    from .adapters import MemoryAdapter
    from .adapters import RecordBundle
    from .adapters import get_adapter
    from .adapters import register_adapter
    from .backends.in_memory import InMemoryStore
    from .embeddings import EmbeddingProvider
    from .embeddings import EmbeddingProviderError
    from .embeddings import OpenAICompatibleEmbeddingProvider
    from .embeddings import canonical_searchable_text
    from .embeddings import content_hash
    from .embeddings import is_embedding_eligible
    from .models import SCHEMA_ID
    from .models import JobInfo
    from .models import MemoryInput
    from .models import MemoryOutput
    from .models import UnifiedMemoryRecord
    from .notes import MemoryNoteWriteResult
    from .notes import OpenClawDailyNoteStore
    from .notes import render_memory_note
    from .service import MemoryNotFoundError
    from .service import MemoryService
    from .service import NestedCollectionError
    from .service import PersistResult
    from .service import build_memory_service
    from .store import JobFilters
    from .store import MemoryDecodeError
    from .store import MemoryQuery
    from .store import MemoryStore


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
