# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Knowledge retrieval abstractions — NAT-independent.

Defines the contracts (BackendAdapter ABC, Chunk/RetrievalResult schema,
factory) used by every retrieval backend. Imports nothing from `nat.*`
so non-NAT callers (eval scripts, unit tests) can share the same
retriever singletons as the agent.

Adapters live under `lib.knowledge.adapters.*` and self-register via
`@register_adapter("name")` at import time.
"""
from .base import BackendAdapter, ChunkFilter
from .factory import get_retriever, register_adapter
from .schema import Chunk, ContentType, RetrievalResult

__all__ = [
    "BackendAdapter",
    "Chunk",
    "ChunkFilter",
    "ContentType",
    "RetrievalResult",
    "get_retriever",
    "register_adapter",
]
