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
