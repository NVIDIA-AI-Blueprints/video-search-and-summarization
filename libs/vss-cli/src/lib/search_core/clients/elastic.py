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

import asyncio
import logging
import urllib.parse
from typing import TYPE_CHECKING, Any, ClassVar

from elasticsearch import ApiError as ESApiError
from elasticsearch import AsyncElasticsearch
from elasticsearch import ConnectionError as ESConnectionError
from elasticsearch import NotFoundError as ESNotFoundError
from elasticsearch import TransportError as ESTransportError

from lib._foundation.sanitize import scrub_log

from ..errors import BackendUnreachableError, IndexNotFoundError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..runtime import SearchRuntime

logger = logging.getLogger(__name__)


def _redact_endpoint(endpoint: str) -> str:
    """Return an endpoint safe to log: strip userinfo (user:pass@) and scrub.

    ES endpoints can embed HTTP basic-auth credentials in the netloc; those
    must never reach the logs (nor may raw control characters — CWE-117).
    """
    try:
        parsed = urllib.parse.urlsplit(endpoint)
    except ValueError:
        return scrub_log(endpoint)
    if parsed.username or parsed.password:
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        parsed = parsed._replace(netloc=netloc)
    return scrub_log(urllib.parse.urlunsplit(parsed))


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

    # Keyed by (endpoint, event loop): an AsyncElasticsearch is bound to the
    # loop it was created on (its httpx pool captures that loop). Reusing one
    # across a fresh ``asyncio.run`` — a new loop — raises "event loop is
    # closed". Keying by the running loop object (not its id, which CPython
    # reuses after a closed loop is freed) gives each loop its own client and
    # never returns a client bound to a different, already-closed loop.
    _clients: ClassVar[dict[tuple[str, asyncio.AbstractEventLoop], AsyncElasticsearch]] = {}

    def __init__(
        self,
        *,
        endpoint: str,
        client: AsyncElasticsearch | None,
        managed: bool = False,
        loop: asyncio.AbstractEventLoop | None = None,
        request_timeout: int = 30,
        max_retries: int = 1,
    ) -> None:
        self._endpoint = endpoint
        self._client = client
        self._managed = managed
        self._client_loop = loop
        self._request_timeout = request_timeout
        self._max_retries = max_retries

    # ---- Construction --------------------------------------------------------

    @staticmethod
    def _current_loop() -> asyncio.AbstractEventLoop | None:
        """Return the running event loop, or None if called outside a loop."""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    @staticmethod
    async def _close_discarded_client(client: AsyncElasticsearch) -> None:
        """Close an unused client without leaking task exceptions."""
        try:
            await client.close()
        except Exception:
            logger.debug("Failed to close discarded Elasticsearch client", exc_info=True)

    @classmethod
    def from_endpoint(
        cls,
        endpoint: str,
        *,
        request_timeout: int = 30,
        max_retries: int = 1,
    ) -> ElasticClient:
        """Return a shared client for the given endpoint, creating one if needed.

        Each distinct (endpoint, event-loop) pair gets its own
        AsyncElasticsearch; callers on the same loop sharing the endpoint share
        the underlying client. Transport settings are fixed at first
        initialization per key; subsequent callers reuse the existing client and
        these kwargs are ignored.
        """
        # A closed loop cannot run the async close method any more, but retaining
        # its client keeps both the loop and transport alive indefinitely. Drop
        # those stale entries before looking up the active loop's client.
        for stale_key in list(cls._clients):
            stale_loop = stale_key[1]
            if stale_loop is not None and stale_loop.is_closed():
                cls._clients.pop(stale_key, None)

        loop = cls._current_loop()
        if loop is None:
            # Public factories are synchronous and may be called before
            # ``asyncio.run``. Do not create/cache a transport under a None key:
            # it would bind on first use and then be reused by a later loop.
            return cls(
                endpoint=endpoint,
                client=None,
                managed=True,
                request_timeout=request_timeout,
                max_retries=max_retries,
            )

        key = (endpoint, loop)
        existing = cls._clients.get(key)
        if existing is not None:
            return cls(
                endpoint=endpoint,
                client=existing,
                managed=True,
                loop=loop,
                request_timeout=request_timeout,
                max_retries=max_retries,
            )
        new = AsyncElasticsearch(
            hosts=[endpoint],
            request_timeout=request_timeout,
            max_retries=max_retries,
        )
        # setdefault is atomic against task-level interleaving: only one of N
        # concurrent callers wins, the rest reuse the winner's client. The
        # loser's `new` is unused and must be closed asynchronously.
        client = cls._clients.setdefault(key, new)
        if client is not new:
            # ``AsyncElasticsearch.close`` is awaitable. Scheduling the public
            # close API on the active loop prevents an abandoned transport
            # coroutine (and its RuntimeWarning). Without a running loop there
            # is no task-level race in this synchronous method; the unopened
            # loser is released without constructing a coroutine.
            loop = cls._current_loop()
            if loop is not None:
                loop.create_task(cls._close_discarded_client(new))
        return cls(
            endpoint=endpoint,
            client=client,
            managed=True,
            loop=loop,
            request_timeout=request_timeout,
            max_retries=max_retries,
        )

    async def _client_for_active_loop(self) -> AsyncElasticsearch:
        """Lazily bind synchronous factories to the loop that performs IO."""
        if not self._managed:
            assert self._client is not None
            return self._client
        loop = asyncio.get_running_loop()
        if self._client is not None and self._client_loop is loop:
            return self._client

        old_client = self._client
        old_loop = self._client_loop
        if old_client is not None and old_loop is not None and old_loop.is_closed():
            if self._clients.get((self._endpoint, old_loop)) is old_client:
                self._clients.pop((self._endpoint, old_loop), None)
            try:
                await old_client.close()
            except Exception:
                logger.debug("Failed to close Elasticsearch client from a closed loop", exc_info=True)

        rebound = self.from_endpoint(
            self._endpoint,
            request_timeout=self._request_timeout,
            max_retries=self._max_retries,
        )
        assert rebound._client is not None
        self._client = rebound._client
        self._client_loop = loop
        return self._client

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
            client = await self._client_for_active_loop()
            return await client.search(index=index, body=body, **kwargs)  # type: ignore[arg-type]
        except ESNotFoundError as e:
            # Honor the "no framework leak" contract: a missing index maps to
            # the library's IndexNotFoundError (a BackendUnreachableError) with
            # the raw elasticsearch error chained via __cause__.
            raise IndexNotFoundError(index, e) from e
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
        for (endpoint, _loop), client in list(cls._clients.items()):
            try:
                await client.close()
            except Exception:
                logger.debug("Error closing ES client for %s", _redact_endpoint(endpoint), exc_info=True)
        cls._clients.clear()

    # ---- Direct access for legacy call sites that still want the raw client.
    @property
    def raw(self) -> AsyncElasticsearch:
        """Return the underlying AsyncElasticsearch. Use sparingly — prefer
        going through .search() so the abstraction stays meaningful."""
        if not self._managed:
            assert self._client is not None
            return self._client
        loop = self._current_loop()
        if loop is None:
            raise RuntimeError("ElasticClient.raw requires a running event loop when constructed synchronously")
        if self._client is None or self._client_loop is not loop:
            rebound = self.from_endpoint(
                self._endpoint,
                request_timeout=self._request_timeout,
                max_retries=self._max_retries,
            )
            assert rebound._client is not None
            self._client = rebound._client
            self._client_loop = loop
        return self._client

    @property
    def endpoint(self) -> str:
        """Public endpoint accessor. Search orchestrator reads this when
        building its config; avoids reaching into the private `_endpoint`."""
        return self._endpoint
