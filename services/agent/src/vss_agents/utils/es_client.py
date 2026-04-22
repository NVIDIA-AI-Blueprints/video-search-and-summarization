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

"""Shared Elasticsearch client for all search operations."""

import asyncio
import logging
from typing import ClassVar

from elasticsearch import AsyncElasticsearch

logger = logging.getLogger(__name__)


class ESClient:
    """Shared async Elasticsearch singleton with lazy initialization."""

    _instance: ClassVar[AsyncElasticsearch | None] = None
    _endpoint: ClassVar[str] = ""
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    @staticmethod
    async def get_es_client(
        es_endpoint: str,
        request_timeout: int = 30,
        max_retries: int = 0,
    ) -> AsyncElasticsearch:
        """Return the shared ES client, initializing it lazily on first call."""
        async with ESClient._lock:
            if ESClient._instance is not None:
                if ESClient._endpoint != es_endpoint:
                    logger.warning(
                        "ESClient already initialized with endpoint %s; ignoring re-init with %s",
                        ESClient._endpoint,
                        es_endpoint,
                    )
                return ESClient._instance

            ESClient._endpoint = es_endpoint
            ESClient._instance = AsyncElasticsearch(
                hosts=[es_endpoint],
                request_timeout=request_timeout,
                max_retries=max_retries,
            )
            return ESClient._instance

    @staticmethod
    async def close() -> None:
        """Close the shared ES client if initialized."""
        async with ESClient._lock:
            if ESClient._instance is not None:
                await ESClient._instance.close()
                ESClient._instance = None
                ESClient._endpoint = ""
