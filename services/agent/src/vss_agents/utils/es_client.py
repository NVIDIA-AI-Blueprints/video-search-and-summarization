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

"""Shared Elasticsearch client registry for all search operations."""

import asyncio
import logging
from typing import ClassVar

from elasticsearch import AsyncElasticsearch

logger = logging.getLogger(__name__)


class VSSESClient:
    """Endpoint-keyed async Elasticsearch client registry with lazy initialization.

    Each distinct endpoint gets its own AsyncElasticsearch instance. Callers sharing
    the same endpoint share the same client. Transport settings (request_timeout,
    max_retries) are fixed at first initialization per endpoint; subsequent callers
    reuse the existing client.

    Teardown: all callers should call close_all() in their finally blocks. The method
    is idempotent — first caller closes all clients, subsequent callers no-op.
    """

    _clients: ClassVar[dict[str, AsyncElasticsearch]] = {}
    _lock: ClassVar[asyncio.Lock | None] = None

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @staticmethod
    async def get_es_client(
        es_endpoint: str,
        request_timeout: int = 30,
        max_retries: int = 0,
    ) -> AsyncElasticsearch:
        """Return a shared ES client for the given endpoint, creating one if needed.

        Transport settings (request_timeout, max_retries) are used only when creating
        a new client for an endpoint. If a client already exists for the endpoint,
        the existing client is returned and these kwargs are ignored.
        """
        async with VSSESClient._get_lock():
            if es_endpoint in VSSESClient._clients:
                return VSSESClient._clients[es_endpoint]

            client = AsyncElasticsearch(
                hosts=[es_endpoint],
                request_timeout=request_timeout,
                max_retries=max_retries,
            )
            VSSESClient._clients[es_endpoint] = client
            return client

    @staticmethod
    async def close_all() -> None:
        """Close all clients and clear the registry. Idempotent."""
        async with VSSESClient._get_lock():
            for endpoint, client in list(VSSESClient._clients.items()):
                try:
                    await client.close()
                except Exception:
                    logger.debug("Error closing ES client for %s", endpoint, exc_info=True)
            VSSESClient._clients.clear()

    @classmethod
    def _reset(cls) -> None:
        """Reset all class state. For use in tests only."""
        cls._clients = {}
        cls._lock = None
