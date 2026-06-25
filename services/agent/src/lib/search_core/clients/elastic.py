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
"""Elasticsearch client wrapper.

An ElasticIndex-shaped object that primitives can hold by reference. It keeps
its OWN endpoint-keyed registry of AsyncElasticsearch instances (the ClassVar
``_clients`` below) — it does NOT share the NAT-side VSSESClient registry
(services/agent/src/vss_agents/utils/es_client.py); search_core is deliberately
decoupled from that module. The two registries therefore open independent
connection pools per endpoint.

Lifecycle: per-instance ``aclose()`` is a no-op because clients are shared
within this registry; for teardown call the class method
``ElasticClient.close_all()`` (separately from ``VSSESClient.close_all()`` —
one does not cover the other). As with VSSESClient, in the long-lived agent
process the pools are otherwise reaped at process exit.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from elasticsearch import ApiError as ESApiError
from elasticsearch import AsyncElasticsearch
from elasticsearch import ConnectionError as ESConnectionError
from elasticsearch import NotFoundError as ESNotFoundError
from elasticsearch import TransportError as ESTransportError

from ..errors import BackendUnreachableError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..runtime import SearchRuntime

logger = logging.getLogger(__name__)


class ElasticClient:
    """Library-side wrapper around an endpoint-keyed AsyncElasticsearch.

    Maintains its own registry of shared AsyncElasticsearch instances — one
    per distinct endpoint. Synchronous construction; the AsyncElasticsearch
    constructor itself does no IO, so an async lock is unnecessary. The
    registry uses dict.setdefault for atomic-insert semantics, which is safe
    against concurrent task interleaving inside a single event loop.

    Mirrors the lifecycle of services/agent/src/vss_agents/utils/es_client.py
    (VSSESClient) but without coupling search_core to it.
    """

    _clients: ClassVar[dict[str, AsyncElasticsearch]] = {}

    def __init__(self, *, endpoint: str, client: AsyncElasticsearch) -> None:
        self._endpoint = endpoint
        self._client = client

    # ---- Construction --------------------------------------------------------

    @classmethod
    def from_endpoint(
        cls,
        endpoint: str,
        *,
        request_timeout: int = 30,
        max_retries: int = 1,
    ) -> ElasticClient:
        """Return a shared client for the given endpoint, creating one if needed.

        Each distinct endpoint gets its own AsyncElasticsearch; callers sharing
        the endpoint share the underlying client. Transport settings are fixed
        at first initialization per endpoint; subsequent callers reuse the
        existing client and these kwargs are ignored.
        """
        existing = cls._clients.get(endpoint)
        if existing is not None:
            return cls(endpoint=endpoint, client=existing)
        new = AsyncElasticsearch(
            hosts=[endpoint],
            request_timeout=request_timeout,
            max_retries=max_retries,
        )
        # setdefault is atomic against task-level interleaving: only one of N
        # concurrent callers wins, the rest reuse the winner's client. The
        # loser's `new` is GC'd; AsyncElasticsearch holds no resources until
        # first IO, so this leaks nothing.
        client = cls._clients.setdefault(endpoint, new)
        if client is not new:
            # Lost the race — don't leave the unused AsyncElasticsearch around
            # holding a closed httpx pool. close() is sync-safe to schedule.
            with contextlib.suppress(Exception):
                # Some elasticsearch versions return a coroutine here that we
                # cannot await from a sync classmethod; best-effort cleanup —
                # any leak is bounded to the unused loser client of the race.
                new._transport.close()  # type: ignore[unused-coroutine]
        return cls(endpoint=endpoint, client=client)

    @classmethod
    def from_runtime(cls, rt: SearchRuntime) -> ElasticClient:
        """Build the default (es_endpoint) client from a SearchRuntime."""
        return cls.from_endpoint(rt.es_endpoint, request_timeout=rt.request_timeout_seconds)

    @classmethod
    def from_runtime_behavior(cls, rt: SearchRuntime) -> ElasticClient:
        """Build a client targeting rt.behavior_es_endpoint (falls back to es_endpoint)."""
        endpoint = rt.behavior_es_endpoint or rt.es_endpoint
        return cls.from_endpoint(endpoint, request_timeout=rt.request_timeout_seconds)

    # ---- ElasticIndex protocol surface --------------------------------------

    async def search(
        self,
        *,
        index: str | list[str],
        body: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Forward to the underlying AsyncElasticsearch.search().

        Kept thin on purpose — the NAT code today calls .search() with raw
        request bodies; reproducing those calls is the safest port. The
        library may grow higher-level helpers later.
        """
        # AsyncElasticsearch's typing for ``body`` is a TypedDict alias; our
        # callers pass plain Mappings (legacy NAT shape). The runtime accepts
        # either, so the cast-style ignore preserves the legacy call shape.
        try:
            return await self._client.search(index=index, body=body, **kwargs)  # type: ignore[arg-type]
        except ESNotFoundError:
            raise
        except (ESConnectionError, ESApiError, ESTransportError) as e:
            raise BackendUnreachableError("elasticsearch", str(e), e) from e

    async def aclose(self) -> None:
        """No-op at the instance level — clients are shared via the class registry.

        For teardown, call ``await ElasticClient.close_all()`` from a process-exit
        hook (or the agent's shutdown handler).
        """
        return None

    # ---- Class-level teardown -----------------------------------------------

    @classmethod
    async def close_all(cls) -> None:
        """Close every client and clear the registry. Idempotent."""
        for endpoint, client in list(cls._clients.items()):
            try:
                await client.close()
            except Exception:
                logger.debug("Error closing ES client for %s", endpoint, exc_info=True)
        cls._clients.clear()

    # ---- Direct access for legacy call sites that still want the raw client.
    @property
    def raw(self) -> AsyncElasticsearch:
        """Return the underlying AsyncElasticsearch. Use sparingly — prefer
        going through .search() so the abstraction stays meaningful."""
        return self._client

    @property
    def endpoint(self) -> str:
        """Public endpoint accessor. Search orchestrator reads this when
        building its config; avoids reaching into the private `_endpoint`."""
        return self._endpoint
