# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Knowledge retrieval abstractions."""
from .base import BackendAdapter
from .base import ChunkFilter
from .factory import get_retriever
from .factory import register_adapter
from .schema import Chunk
from .schema import ContentType
from .schema import RetrievalResult

__all__ = [
    "BackendAdapter",
    "Chunk",
    "ChunkFilter",
    "ContentType",
    "RetrievalResult",
    "get_retriever",
    "register_adapter",
]
