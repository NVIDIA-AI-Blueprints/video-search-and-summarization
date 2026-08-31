# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The CLI's view of the unified memory tier (``nv.vss.memory/1.0``).

``vss_core.memory`` is deliberately group-agnostic: it stores records keyed by
``job_id`` and knows nothing about command groups. The framework's read verbs
ask a narrower question -- *this group's* job, by id (SDD §6.2) -- so the two
need one adapter, and this is it. It resolves the store from the recorded
deployment, scopes reads to the calling group, and returns plain JSON for the
emitter.

Nothing here is imported at CLI start-up: ``vss_core.memory`` and the
Elasticsearch client load on the first call that actually touches memory, so
``vss --help`` and ``run --no-persist`` stay free of them.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

import click

from .exits import Exit

if TYPE_CHECKING:
    from typing import Literal

    from vss_core.memory import MemoryService
    from vss_core.memory import UnifiedMemoryRecord
    from vss_core.memory.models import MemoryGroup

    from . import config as config_mod

#: CLI group name -> the schema's ``job.group``. The two differ where the verb
#: reads better than the noun: the command is ``summarize``, the record it
#: writes is a ``summary``. Identity for every other group.
_GROUP_TOKENS = {"summarize": "summary"}

#: Child collections owned by each job group. ``get`` hydrates these into a
#: presentation-only envelope; they are never nested back into the stored
#: ``nv.vss.memory/1.0`` parent.
_CHILD_RECORD_TYPES = {
    "summary": "event",
    "search": "search_hit",
    "alert": "incident",
}
_CHILD_COUNT_KEYS = {
    "event": "event_count",
    "search_hit": "result_count",
    "incident": "incident_count",
}
_DEFAULT_CHILD_LIMIT = 100


class MemoryUnavailable(click.ClickException):
    """Memory was asked for and cannot be reached (exit 4).

    Explicit by design. ``status``/``get``/``list`` are memory reads by
    definition, so a deployment without an index cannot serve them; failing
    with a named cause beats three verbs that appear to work and return
    nothing.
    """

    exit_code = int(Exit.CONFIGURATION)


def _close_resources(resources: tuple[Any, ...]) -> None:
    first_error: Exception | None = None
    for resource in resources:
        try:
            resource.close()
        except Exception as error:
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error


def group_token(name: str) -> MemoryGroup:
    """The unified-schema group a CLI group writes under.

    Cast rather than validated: a third-party group is free to name a token
    the schema does not know, and the store is where that gets rejected on
    write. A read filtered by an unknown group simply matches nothing.
    """
    return cast("MemoryGroup", _GROUP_TOKENS.get(name, name))


class Memory:
    """Group-scoped reads over one memory store.

    Writes go through :attr:`service` directly: a group persists whole
    lifecycle records built by its own adapter, and narrowing that to a
    ``put`` here would only re-state the adapter's signature.
    """

    def __init__(
        self,
        service: MemoryService,
        *,
        index: str,
        closeables: tuple[Any, ...] = (),
    ) -> None:
        self._service = service
        self.index = index
        self._closeables = closeables
        self._closed = False

    @property
    def service(self) -> MemoryService:
        return self._service

    def close(self) -> None:
        """Close each runtime resource exactly once."""
        if self._closed:
            return
        self._closed = True
        _close_resources(self._closeables)

    def status(self, group: str, job_id: str) -> dict[str, Any]:
        return self._scoped(group, job_id).model_dump_memory()

    def get(self, group: str, job_id: str) -> dict[str, Any]:
        parent = self._scoped(group, job_id)
        payload = parent.model_dump_memory()
        payload["children"] = self._children(parent)
        return payload

    def query(self, group: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        from vss_core.memory import JobFilters

        records = self._service.list_jobs(
            JobFilters(
                group=group_token(group),
                status=filters.get("status"),
                sensor_id=filters.get("sensor_id"),
                since=filters.get("since"),
            )
        )
        return [record.model_dump_memory() for record in records]

    def _children(self, parent: UnifiedMemoryRecord) -> list[dict[str, Any]]:
        """Return this parent's independently retrievable results in domain order."""
        from vss_core.memory import MemoryQuery

        record_type = _CHILD_RECORD_TYPES.get(parent.job.group)
        if record_type is None:
            return []
        advertised = 0
        if parent.output is not None and parent.output.ext:
            value = parent.output.ext.get(_CHILD_COUNT_KEYS[record_type])
            if isinstance(value, int) and value > 0:
                advertised = value
        records = self._service.query(
            MemoryQuery(
                job_id=parent.job.job_id,
                record_type=record_type,  # type: ignore[arg-type]
                limit=max(_DEFAULT_CHILD_LIMIT, advertised),
            )
        )
        children = [
            record
            for record in records
            if record.job.is_child and record.job.group == parent.job.group and record.job.job_id == parent.job.job_id
        ]

        def sort_key(record: UnifiedMemoryRecord) -> tuple[int, str]:
            if record_type == "search_hit":
                rank = record.output.ext.get("rank") if record.output is not None and record.output.ext else None
                return (int(rank) if isinstance(rank, int | float) else 2**31 - 1, record.job.record_id or "")
            if record.input is not None and record.input.window is not None:
                return (0, record.input.window.start.timestamp.isoformat())
            return (1, record.job.record_id or "")

        return [record.model_dump_memory() for record in sorted(children, key=sort_key)]

    def _scoped(self, group: str, job_id: str) -> UnifiedMemoryRecord:
        """One record, refusing another group's job under this group's verb."""
        from vss_core.memory import MemoryNotFoundError

        record = self._service.get(job_id)
        token = group_token(group)
        if record.job.group != token:
            # Exit 5, same as an unknown id: from the caller's side both mean
            # "this handle names no job of mine".
            raise MemoryNotFoundError(f"job {job_id} is a {record.job.group!r} job, not a {token!r} job")
        return record


def build(deployment: config_mod.Deployment | None) -> Memory:
    """Open the statically configured authoritative memory store."""
    if deployment is None:
        raise MemoryUnavailable(
            "cannot reach unified memory: no deployment is configured. Run `vss configure --base-url <origin>` first."
        )
    memory_config = deployment.memory
    if memory_config is None:
        raise MemoryUnavailable(
            "cannot reach unified memory: memory is not configured. "
            "Run `vss configure memory --enable --backend elasticsearch --index vss-memory` first."
        )
    if not memory_config.enabled:
        raise MemoryUnavailable(
            "cannot reach unified memory: memory is disabled. Run `vss configure memory --enable` first."
        )
    if memory_config.backend != "elasticsearch":
        raise MemoryUnavailable(
            f"cannot reach unified memory: backend {memory_config.backend!r} is unsupported. "
            "Run `vss configure memory --backend elasticsearch`."
        )
    endpoint = deployment.endpoint_or_none("elasticsearch")
    if not endpoint:
        raise MemoryUnavailable(
            f"cannot reach unified memory: the deployment at {deployment.base_url} records no Elasticsearch. "
            f"Re-run `vss configure --base-url {deployment.base_url}` if that changed."
        )

    from vss_core.memory import ElasticsearchEmbeddingStore
    from vss_core.memory import MemoryService
    from vss_core.memory import OpenAICompatibleEmbeddingProvider
    from vss_core.memory.backends.elasticsearch import ElasticsearchMemoryStore

    authoritative = ElasticsearchMemoryStore(endpoint=endpoint, index=memory_config.index)
    if not memory_config.embeddings.enabled:
        return Memory(
            MemoryService(authoritative),
            index=memory_config.index,
            closeables=(authoritative,),
        )

    embedding_config = memory_config.embeddings
    provider: OpenAICompatibleEmbeddingProvider | None = None
    companion: ElasticsearchEmbeddingStore | None = None
    try:
        # Validation guarantees an endpoint whenever embeddings are enabled.
        assert embedding_config.endpoint is not None
        provider = OpenAICompatibleEmbeddingProvider(
            endpoint=embedding_config.endpoint,
            model=embedding_config.model,
            dimensions=embedding_config.dimensions,
            timeout_seconds=embedding_config.timeout_seconds,
            batch_size=embedding_config.batch_size,
            api_key_env=embedding_config.api_key_env,
        )
        companion = ElasticsearchEmbeddingStore(
            endpoint=endpoint,
            index=embedding_config.index,
            provider=provider,
            authoritative_store=authoritative,
        )
        retrieval = memory_config.retrieval
        service = MemoryService(
            authoritative,
            semantic_memory=companion,
            embedding_provider=provider,
            retrieval_mode=cast("Literal['keyword', 'semantic', 'hybrid']", memory_config.effective_retrieval_mode),
            semantic_candidate_count=retrieval.candidate_count,
            rrf_rank_constant=retrieval.rrf_rank_constant,
        )
    except Exception:
        resources = tuple(resource for resource in (companion, provider, authoritative) if resource is not None)
        with suppress(Exception):
            _close_resources(resources)
        raise
    return Memory(
        service,
        index=memory_config.index,
        closeables=(companion, provider, authoritative),
    )


def open_for_persist(deployment: config_mod.Deployment | None, *, no_persist: bool) -> Memory | None:
    """Return the memory store when the deployment policy says to persist, else None.

    Encapsulates the full persistence policy so the framework and individual
    commands do not need to inspect ``memory.enabled`` or
    ``memory.persist_by_default`` directly.  The check order:

    1. ``no_persist=True`` → caller explicitly opted out; return None immediately.
    2. ``memory.enabled`` or ``memory.persist_by_default`` is false → the operator
       disabled auto-persistence; return None without raising.
    3. All other :func:`build` failures (unconfigured, wrong backend, …) → propagate
       :class:`MemoryUnavailable` so the caller can surface the real cause.
    """
    if no_persist:
        return None
    if deployment is None:
        return None
    memory_config = deployment.memory
    if not (memory_config and memory_config.enabled and memory_config.persist_by_default):
        return None
    return build(deployment)


def write_failures() -> tuple[type[BaseException], ...]:
    """Exception classes that mean "the tier would not take this write".

    ``ElasticsearchMemoryStore`` translates connection and transport trouble
    into ``BackendUnreachableError``, but a status rejection -- a read-only
    ingress answering 405 to the store's ``PUT`` -- arrives as the client's own
    ``ApiError``, which would otherwise leave the CLI as an untyped crash
    instead of a persistence failure the caller can act on.
    """
    from vss_core._foundation.errors import BackendUnreachableError

    failures: list[type[BaseException]] = [BackendUnreachableError]
    try:
        from elasticsearch import ApiError
    except ImportError:  # pragma: no cover - present wherever the ES store is
        pass
    else:
        failures.append(ApiError)
    return tuple(failures)


__all__ = ["Memory", "MemoryUnavailable", "build", "group_token", "open_for_persist", "write_failures"]
