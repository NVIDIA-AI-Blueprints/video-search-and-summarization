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
"""Backend adapter contract."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from .schema import Chunk
from .schema import RetrievalResult

if TYPE_CHECKING:
    from pydantic import BaseModel

ChunkFilter = Callable[[Chunk], bool]


class BackendAdapter(ABC):
    """Pluggable retrieval backend."""

    backend_name: ClassVar[str]
    # Backend-specific guidance appended to the tool's description at
    # registration time. Lets each adapter teach the LLM how to call
    # the tool (accepted filter keys, default behaviour, etc.) without
    # bloating the workflow's system prompt.
    tool_description_hint: ClassVar[str] = ""

    def __init__(self, config: BaseModel) -> None:
        self.config = config

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
        filters: ChunkFilter | dict[str, Any] | None = None,
    ) -> RetrievalResult:
        """Retrieve top_k chunks for `query` from `collection_name`."""

    async def summarize(
        self,
        _query: str,
        chunks: list[Chunk],
        _llm: Any | None = None,
    ) -> str:
        return "\n\n".join(c.content for c in chunks if c.content)

    async def health_check(self) -> bool:
        return True
