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
"""Abstract base for embedding clients.

Ported from services/agent/src/agent/embed/embed.py:21 with no behavior
changes. Two implementations: CosmosEmbedClient (full text+image+video) and
RTVICVEmbedClient (text-only).
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
import math
from numbers import Real
from typing import Any

from ..errors import BackendUnreachableError


def validate_embedding(value: Any, *, backend: str) -> list[float]:
    """Return a finite numeric embedding or raise a typed backend error."""
    if not isinstance(value, list) or not value:
        raise BackendUnreachableError(backend, "embedding response must be a non-empty list")
    if any(isinstance(item, bool) or not isinstance(item, Real) for item in value):
        raise BackendUnreachableError(backend, "embedding response contains a non-numeric value")
    embedding = [float(item) for item in value]
    if not all(math.isfinite(item) for item in embedding):
        raise BackendUnreachableError(backend, "embedding response contains a non-finite value")
    return embedding


class EmbedClient(ABC):
    """Abstract base class for embedding clients."""

    @abstractmethod
    async def get_image_embedding(self, image_url: str) -> list[float]:
        """Generate embedding for image input."""

    @abstractmethod
    async def get_text_embedding(self, text: str) -> list[float]:
        """Generate embedding for text input."""

    @abstractmethod
    async def get_video_embedding(self, video_url: str) -> list[float]:
        """Generate embedding for video input."""

    async def aclose(self) -> None:  # noqa: B027  intentional no-op default; HTTP-less subclasses don't need to override
        """Release any resources held by this client.

        Default implementation is a no-op. Subclasses with persistent HTTP clients
        or caches should override to close/clear them.
        """
