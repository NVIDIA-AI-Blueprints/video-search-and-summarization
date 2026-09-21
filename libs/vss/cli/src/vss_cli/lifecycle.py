# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""One job's persistence choreography, owned by the framework.

A job is written twice -- ``submitted`` before the work, terminal after -- so a
run that dies still leaves a record saying so. :class:`Lifecycle` is that
sequence over one :class:`~vss_cli.memory.Memory` handle and one group adapter.
:meth:`CommandGroup.run <vss_cli.group.CommandGroup.run>` drives it for every
command group; :func:`vss_cli.vlm.runner.run_vlm_job` drives it for
introspection's VLM jobs, inside its own deadline machinery.

What a job id is worth after the run is one of three words, and every path
answers with one of them (SDD §7.2):

``absent``
    nothing was persisted -- policy said no, or the store refused the first write.
``closed``
    the record reflects the outcome.
``stale``
    the record could not be updated and still reads ``submitted``; ``status``
    will call the job running until something reconciles it.
"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from . import memory as memory_mod

if TYPE_CHECKING:
    from vss_core.memory import PersistResult
    from vss_core.memory import RecordBundle
    from vss_core.memory import UnifiedMemoryRecord
    from vss_core.memory.adapters import LifecycleAdapter
    from vss_core.memory.models import JobStatus
    from vss_core.memory.models import MemoryInput
    from vss_core.memory.models import MemoryOutput

    from .exits import Exit

RecordState = Literal["absent", "closed", "stale"]
CloseStatus = Literal["failed", "partial", "timeout"]

#: Crockford base32, for ULID job ids.
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: Bounded retry for the terminal write. Small: a close that keeps failing
#: must not replace the caller's diagnosis with one about Elasticsearch.
TERMINAL_WRITE_ATTEMPTS = 3
TERMINAL_WRITE_BACKOFF_SECONDS = 0.5


def ulid() -> str:
    """A lexicographically sortable 26-char ULID (48-bit time + 80-bit random).

    Stdlib-only so the CLI stays dependency-light; sortability keeps
    ``job_id`` ordering stable over time.
    """
    value = (int(time.time() * 1000) & ((1 << 48) - 1)) << 80 | secrets.randbits(80)
    return "".join(_CROCKFORD32[(value >> shift) & 0x1F] for shift in range(125, -1, -5))


def mint_job_id(domain: str) -> str:
    """``<domain>-<ULID>`` (design §5.2/§7.2). The domain is the group name."""
    return f"{domain}-{ulid()}"


class JobError(Exception):
    """A post-mint failure the framework closes out.

    Raised from :meth:`CommandGroup.execute` when the group has classified what
    went wrong -- which exit code, whether the record should read ``failed`` or
    ``timeout``, and what stderr should say. The framework writes the terminal
    record, prints the diagnostic, and returns the failure through a
    :class:`~vss_cli.group.Result` so the completion marker is still the last
    line of stdout. Raising anything else past the framework would exit without
    a marker, leaving the handle for the record just written nowhere a harness
    can find it.
    """

    def __init__(
        self,
        detail: str,
        *,
        exit: Exit,
        status: Literal["failed", "timeout"] = "failed",
        diagnostic: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.exit = exit
        self.status: Literal["failed", "timeout"] = status
        self.diagnostic = diagnostic


@dataclass
class Job:
    """What the framework knows about one run, handed to every group hook.

    ``input_data`` is the record's request side; :meth:`CommandGroup.prepare`
    fills it before the ``submitted`` write and :meth:`CommandGroup.execute`
    may refine it (a resolved time window, say) before the terminal write.
    ``state`` is the group's own, opaque to the framework.
    """

    job_id: str
    created_at: str
    input_data: Any = None
    #: Feeds the completion marker; the sensor or video the job was about.
    asset_id: str | None = None
    state: Any = None


class Lifecycle:
    """Persistence for one job over one memory handle and one adapter.

    ``memory`` may be None from the start (policy said no) and becomes None if
    the store refuses the first write: from then on every method answers as if
    nothing were configured, so a group never checks twice.
    """

    def __init__(
        self,
        memory: memory_mod.Memory | None,
        adapter: LifecycleAdapter,
        *,
        job_id: str,
        created_at: str,
    ) -> None:
        self.memory = memory
        self.adapter = adapter
        self.job_id = job_id
        self.created_at = created_at
        #: Why the tier dropped out, when it did.
        self.persist_error: str | None = None

    @property
    def active(self) -> bool:
        return self.memory is not None

    # -- records ---------------------------------------------------------

    def submitted_record(self, input_data: MemoryInput) -> UnifiedMemoryRecord:
        return self.adapter.submitted_record(job_id=self.job_id, created_at=self.created_at, input_data=input_data)

    def terminal_record(
        self,
        status: JobStatus,
        input_data: MemoryInput,
        *,
        output: MemoryOutput | None = None,
        message: str | None = None,
    ) -> UnifiedMemoryRecord:
        from vss_core.memory.models import MemoryError

        return self.adapter.terminal_record(
            job_id=self.job_id,
            created_at=self.created_at,
            status=status,
            input_data=input_data,
            output=output,
            error=MemoryError(code=status, message=message) if message is not None else None,
        )

    # -- writes ----------------------------------------------------------

    def open(self, input_data: MemoryInput) -> bool:
        """Write the job before doing the work.

        A configured store that refuses the write is a persistence failure, not
        a reason to skip the work the caller asked for: the tier is dropped,
        :attr:`persist_error` says why, and the run carries on unpersisted.
        """
        if self.memory is None:
            return False
        try:
            self.memory.service.upsert(self.submitted_record(input_data))
        except memory_mod.write_failures() as error:
            self.persist_error = str(error)
            self.memory = None
            return False
        return True

    def complete(self, bundle: RecordBundle) -> PersistResult:
        """Write the outcome. Only meaningful while :attr:`active`."""
        assert self.memory is not None
        return self.memory.service.upsert_bundle(bundle)

    def close(
        self,
        status: CloseStatus,
        message: str,
        input_data: MemoryInput,
        *,
        attempts: int | None = None,
        backoff_seconds: float | None = None,
    ) -> RecordState:
        """Replace ``submitted`` with the outcome, and say what the handle is now worth."""
        if self.memory is None:
            return "absent"
        # Read at call time so a test can bound the budget through the module.
        if attempts is None:
            attempts = TERMINAL_WRITE_ATTEMPTS
        if backoff_seconds is None:
            backoff_seconds = TERMINAL_WRITE_BACKOFF_SECONDS
        record = self.terminal_record(status, input_data, message=message)
        delay = backoff_seconds
        for attempt in range(1, attempts + 1):
            try:
                self.memory.service.upsert(record)
            except Exception:
                if attempt == attempts:
                    return "stale"
                time.sleep(delay)
                delay *= 2
            else:
                return "closed"
        return "stale"


__all__ = [
    "TERMINAL_WRITE_ATTEMPTS",
    "TERMINAL_WRITE_BACKOFF_SECONDS",
    "CloseStatus",
    "Job",
    "JobError",
    "Lifecycle",
    "RecordState",
    "mint_job_id",
    "ulid",
]
