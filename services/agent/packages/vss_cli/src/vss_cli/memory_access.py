# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bridge between the CLI framework and ``vss_core.memory``.

:class:`~vss_cli.group.CommandGroup` inherited verbs call
``ctx.memory.status/get/query(group, ...)`` with the Click group name
(``summarize``, ``search``, ...). Unified-memory records store the shorter
schema token (``summary``, ``search``, ...). This module owns that mapping and
builds a group-scoped facade from the recorded deployment.
"""

from __future__ import annotations

from typing import Any

from vss_core._foundation.errors import ConfigurationError
from vss_core.memory.models import KNOWN_GROUPS
from vss_core.memory.models import MemoryGroup
from vss_core.memory.service import MemoryNotFoundError
from vss_core.memory.service import MemoryService
from vss_core.memory.service import build_memory_service
from vss_core.memory.store import JobFilters

from . import config as config_mod

#: CLI / plugin group name → ``job.group`` schema token (SDD §5.2).
_CLI_GROUP_TO_SCHEMA: dict[str, MemoryGroup] = {
    "summarize": "summary",
    "summary": "summary",
    "search": "search",
    "alerts": "alert",
    "alert": "alert",
    "vios": "media",
    "media": "media",
    "vlm": "vlm",
}

DEFAULT_MEMORY_INDEX = "vss-memory"

#: Injectable facade for hermetic unit tests (avoids requiring live ES / config).
_TEST_MEMORY: GroupScopedMemory | None = None


def set_test_memory(memory: GroupScopedMemory | None) -> None:
    """Install (or clear) a process-local memory facade for tests."""
    global _TEST_MEMORY
    _TEST_MEMORY = memory


def schema_group_for(cli_group: str) -> MemoryGroup:
    """Map a CLI group name to the ``nv.vss.memory/1.0`` ``job.group`` token."""
    mapped = _CLI_GROUP_TO_SCHEMA.get(cli_group)
    if mapped is not None:
        return mapped
    if cli_group in KNOWN_GROUPS:
        return cli_group  # type: ignore[return-value]
    raise ValueError(f"unknown CLI memory group {cli_group!r}")


class GroupScopedMemory:
    """Framework-facing memory adapter: ``status/get/query(group, ...)``.

    Wraps :class:`~vss_core.memory.service.MemoryService` and enforces
    group isolation so ``vss summarize get`` cannot return a search record.
    """

    def __init__(self, service: MemoryService) -> None:
        self._service = service

    @property
    def service(self) -> MemoryService:
        return self._service

    def status(self, group: str, job_id: str) -> dict[str, Any]:
        record = self._get_for_group(group, job_id)
        return {
            "job_id": record.job.job_id,
            "group": record.job.group,
            "status": record.job.status,
            "updated_at": record.job.updated_at,
            "backend_ref": record.job.backend_ref,
            "error": None if record.error is None else record.error.model_dump(mode="json"),
        }

    def get(self, group: str, job_id: str) -> dict[str, Any]:
        return self._get_for_group(group, job_id).model_dump_memory()

    def query(self, group: str, filters: dict[str, Any]) -> dict[str, Any]:
        schema_group = schema_group_for(group)
        status = filters.get("status")
        job_filters = JobFilters(
            group=schema_group,
            status=status if isinstance(status, str) else None,
            sensor_id=filters.get("sensor_id"),
            since=filters.get("since"),
            until=filters.get("until"),
            limit=int(filters.get("limit") or 50),
        )
        records = self._service.list_jobs(job_filters)
        return {"records": [record.model_dump_memory() for record in records]}

    def _get_for_group(self, group: str, job_id: str) -> Any:
        schema_group = schema_group_for(group)
        record = self._service.get(job_id, reconcile=True)
        if record.job.group != schema_group:
            raise MemoryNotFoundError(f"job_id {job_id!r} belongs to group {record.job.group!r}, not {schema_group!r}")
        return record


def memory_from_deployment(
    deployment: config_mod.Deployment | None,
    *,
    memory_index: str | None = None,
) -> GroupScopedMemory | None:
    """Build a facade from the recorded deployment, or ``None`` if ES is absent."""
    if _TEST_MEMORY is not None:
        return _TEST_MEMORY
    if deployment is None:
        return None
    endpoint = deployment.endpoint_or_none("elasticsearch")
    if not endpoint:
        return None
    try:
        service = build_memory_service(
            es_endpoint=endpoint,
            memory_index=memory_index or DEFAULT_MEMORY_INDEX,
        )
    except ConfigurationError:
        return None
    return GroupScopedMemory(service)


def require_memory_service(
    deployment: config_mod.Deployment | None,
    *,
    memory_index: str | None = None,
) -> MemoryService:
    """Return an in-process :class:`MemoryService` or raise :class:`ConfigError`."""
    if _TEST_MEMORY is not None:
        return _TEST_MEMORY.service
    if deployment is None:
        raise config_mod.ConfigError(
            "no deployment configured; run `vss configure --base-url <origin>` before persisting to memory"
        )
    endpoint = deployment.endpoint_or_none("elasticsearch")
    if not endpoint:
        raise config_mod.ConfigError(
            f"deployment at {deployment.base_url} has no elasticsearch route; "
            f"re-run `vss configure --base-url {deployment.base_url}` or pass --no-persist"
        )
    return build_memory_service(
        es_endpoint=endpoint,
        memory_index=memory_index or DEFAULT_MEMORY_INDEX,
    )


__all__ = [
    "DEFAULT_MEMORY_INDEX",
    "GroupScopedMemory",
    "memory_from_deployment",
    "require_memory_service",
    "schema_group_for",
    "set_test_memory",
]
