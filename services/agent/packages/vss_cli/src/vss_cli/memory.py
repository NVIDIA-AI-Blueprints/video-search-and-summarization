# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Group-scoped CLI access to the unified-memory service."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

import click

from .exits import Exit

if TYPE_CHECKING:
    from vss_core.memory import MemoryService
    from vss_core.memory import UnifiedMemoryRecord
    from vss_core.memory.models import MemoryGroup

    from . import config as config_mod


class MemoryUnavailable(click.ClickException):
    """Memory was requested but no usable persistent store is configured."""

    exit_code = int(Exit.CONFIGURATION)

    @classmethod
    def for_verb(cls, verb: str) -> MemoryUnavailable:
        """Describe a read verb used by a group without memory backing."""
        return cls(f"`{verb}` requires unified-memory backing, which this command group does not provide.")


class Memory:
    """Group-scoped JSON reads over one ``MemoryService``."""

    def __init__(self, service: MemoryService, *, index: str) -> None:
        self._service = service
        self.index = index

    @property
    def service(self) -> MemoryService:
        return self._service

    def status(self, group: MemoryGroup, job_id: str) -> dict[str, Any]:
        return self._ensure_group(group, self._service.status(job_id)).model_dump_memory()

    def get(self, group: MemoryGroup, job_id: str) -> dict[str, Any]:
        return self._ensure_group(group, self._service.get(job_id)).model_dump_memory()

    def query(self, group: MemoryGroup, filters: dict[str, Any]) -> list[dict[str, Any]]:
        from vss_core.memory import JobFilters

        records = self._service.list_jobs(
            JobFilters(
                group=group,
                status=filters.get("status"),
                sensor_id=filters.get("sensor_id"),
                since=filters.get("since"),
            )
        )
        return [record.model_dump_memory() for record in records]

    @staticmethod
    def _ensure_group(group: MemoryGroup, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord:
        from vss_core.memory import MemoryNotFoundError

        if record.job.group != group:
            raise MemoryNotFoundError(f"job {record.job.job_id} is a {record.job.group!r} job, not a {group!r} job")
        return record


def build(deployment: config_mod.Deployment | None, *, index: str | None = None) -> Memory:
    """Build the persistent memory tier from configured Elasticsearch."""
    if deployment is None:
        raise MemoryUnavailable(
            "cannot reach unified memory: no deployment is configured. Run `vss configure --base-url <origin>` first."
        )
    endpoint = deployment.endpoint_or_none("elasticsearch")
    if not endpoint:
        raise MemoryUnavailable(
            f"cannot reach unified memory: the deployment at {deployment.base_url} records no Elasticsearch. "
            f"Re-run `vss configure --base-url {deployment.base_url}` if that changed."
        )

    from vss_core.memory import build_memory_service
    from vss_core.memory.backends.elasticsearch import DEFAULT_MEMORY_INDEX

    resolved = index or DEFAULT_MEMORY_INDEX
    return Memory(build_memory_service(es_endpoint=endpoint, memory_index=resolved), index=resolved)


def index_option() -> click.Option:
    """Return the shared memory-index override option."""
    return click.Option(
        ["--memory-index"],
        help="Elasticsearch index holding unified memory. Defaults to vss-memory.",
    )


__all__ = ["Memory", "MemoryUnavailable", "build", "index_option"]
