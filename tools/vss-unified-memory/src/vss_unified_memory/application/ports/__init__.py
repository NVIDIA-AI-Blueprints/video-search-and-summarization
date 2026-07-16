# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Interfaces implemented by infrastructure adapters."""

from vss_unified_memory.application.ports.embedding_provider import EmbeddingProvider
from vss_unified_memory.application.ports.memory_repository import MemoryRepository

__all__ = ["EmbeddingProvider", "MemoryRepository"]
