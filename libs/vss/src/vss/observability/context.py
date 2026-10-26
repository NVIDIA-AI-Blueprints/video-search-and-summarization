# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Opt-in bounded collection with task-local, sanitized lineage."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import math
import secrets
import threading
import time
import uuid
import weakref
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from types import MappingProxyType, TracebackType
from typing import Any

from .contracts import METRIC_CATALOG, Availability, Library, Observation, Status


@dataclass(frozen=True, slots=True)
class Scope:
    collector: weakref.ReferenceType[MetricsCollector]
    request_id: str
    source_id: str = "unknown"
    pipeline: str = "not_applicable"
    configured_model: str = "unknown"
    model: str = "unknown"
    backend: str = "unknown"
    parent_id: str | None = None
    chunk_idx: int | None = None
    device: str = "unknown"
    selection_reason: str = "unknown"


_SCOPE: ContextVar[Scope | None] = ContextVar("vss_measurement_scope", default=None)
_DEFERRED: ContextVar[list[tuple[Scope, Observation, WorkLease]] | None] = ContextVar(
    "vss_deferred_sinks", default=None
)
_DELIVERING: ContextVar[frozenset[str]] = ContextVar(
    "vss_delivering", default=frozenset()
)
_ACTIVE: ContextVar[Measurement | None] = ContextVar(
    "vss_active_measurement", default=None
)


class MetricsCollector:
    """Bounded scalar records. Finalization never waits for outstanding work.

    A supplied sink receives already sanitized records. Sink failures invalidate
    evidence but do not replace workload errors. Sinks must be nonblocking.
    """

    def __init__(
        self,
        *,
        max_events: int = 10_000,
        sink: Callable[[Observation], None] | None = None,
    ) -> None:
        if type(max_events) is not int or not 1 <= max_events <= 1_000_000:
            raise ValueError("max_events must be between 1 and 1000000")
        self.run_id = uuid.uuid4().hex
        self._key = secrets.token_bytes(32)
        self._max_events = max_events
        self._sink = sink
        self._events: list[Observation] = []
        self._source_sets: dict[str, tuple[str, ...]] = {}
        self._source_aliases: dict[str, str] = {}
        self._lock = threading.RLock()
        self._outstanding = 0
        self._closed = False
        self._incomplete = False
        self._dropped = 0

    @property
    def observations(self) -> tuple[Observation, ...]:
        with self._lock:
            return tuple(self._events)

    @property
    def complete(self) -> bool:
        with self._lock:
            return self._closed and not self._incomplete

    @property
    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def source_sets(self) -> Mapping[str, tuple[str, ...]]:
        with self._lock:
            return MappingProxyType(dict(self._source_sets))

    def sources(self, values: Iterable[str]) -> str:
        identities: set[str] = set()
        for value in values:
            identities.add(self.source_identity(value))
            if len(identities) > 32:
                self.invalidate()
                return "unknown"
        if not identities:
            return "unknown"
        if len(identities) == 1:
            return next(iter(identities))
        ordered = tuple(sorted(identities))
        key = self.identity("\x00".join(ordered))
        with self._lock:
            if self._closed:
                return "unknown"
            if len(self._source_sets) >= 128 and key not in self._source_sets:
                self._incomplete = True
                return "unknown"
            self._source_sets[key] = ordered
        return key

    def identity(self, value: str | None) -> str:
        """Opaque run-scoped identity, without retaining the original string."""
        if value is None:
            return "unknown"
        # Bound work on attacker-controlled labels as well as output cardinality.
        if len(value) > 4096:
            self.invalidate()
            return "unknown"
        return (
            "opaque:"
            + hmac.new(
                self._key, value.encode("utf-8", errors="replace"), hashlib.sha256
            ).hexdigest()
        )

    def source_identity(self, value: str | None) -> str:
        """Resolve an uploaded source within this collection only."""
        identity = self.identity(value)
        with self._lock:
            return self._source_aliases.get(identity, identity)

    def correlate_source(self, original: str, prepared: str) -> None:
        """Retain at most 128 opaque upload aliases, never rewriting evidence.

        Conflicting, late or excessive aliases invalidate the collection rather
        than altering public responses or failing the workload. Targets remain
        canonical, so resolution is one hop. No original strings are retained.
        """
        original_identity = self.identity(original)
        prepared_identity = self.identity(prepared)
        with self._lock:
            if self._closed:
                return
            canonical = self._source_aliases.get(original_identity, original_identity)
            if not canonical.startswith("opaque:") or not prepared_identity.startswith(
                "opaque:"
            ):
                self._incomplete = True
                return
            if prepared_identity == canonical:
                return
            previous = self._source_aliases.get(prepared_identity)
            if previous == canonical:
                return
            if (
                previous is not None
                or prepared_identity in self._source_aliases.values()
                or any(event.source_id == prepared_identity for event in self._events)
                or any(
                    prepared_identity in members
                    for members in self._source_sets.values()
                )
                or len(self._source_aliases) >= 128
            ):
                self._incomplete = True
                return
            self._source_aliases[prepared_identity] = canonical

    def invalidate(self) -> None:
        with self._lock:
            if not self._closed:
                self._incomplete = True

    def _append(self, observation: Observation, scope: Scope) -> None:
        with self._lock:
            if self._closed:
                return
            if observation.source_id in self._source_aliases:
                # A span may have captured this identity before upload alias
                # registration. Retain its evidence, but never call it complete.
                self._incomplete = True
            if len(self._events) >= self._max_events:
                self._incomplete = True
                self._dropped += 1
                return
            self._events.append(observation)
            if self._sink is None:
                return
            deferred = _DEFERRED.get()
            if deferred is not None:
                if len(deferred) >= 128:
                    self._incomplete = True
                    return
                deferred.append((scope, observation, WorkLease(scope)))
                return
        self._deliver(observation)

    def _deliver(self, observation: Observation) -> None:
        with self._lock:
            sink = self._sink if not self._closed else None
        if sink is None:
            return
        active = _DELIVERING.get()
        if self.run_id in active:
            self.invalidate()
            return
        token = _DELIVERING.set(active | {self.run_id})
        try:
            sink(observation)
        except (Exception, asyncio.CancelledError):
            with self._lock:
                if not self._closed:
                    self._incomplete = True
                    self._sink = None
        finally:
            _DELIVERING.reset(token)

    def _finish(self) -> None:
        with self._lock:
            self._incomplete |= self._outstanding > 0
            self._closed = True
            self._sink = None


@contextmanager
def collect_vss_metrics(
    *,
    max_events: int = 10_000,
    sink: Callable[[Observation], None] | None = None,
) -> Iterator[MetricsCollector]:
    """Collect this context's measurements; nested collections are isolated."""
    collector = MetricsCollector(max_events=max_events, sink=sink)
    token = _SCOPE.set(Scope(weakref.ref(collector), uuid.uuid4().hex))
    try:
        yield collector
    finally:
        _SCOPE.reset(token)
        collector._finish()


def current_scope() -> Scope | None:
    scope = _SCOPE.get()
    collector = scope.collector() if scope else None
    if collector is None or collector._closed:
        return None
    return scope


@contextmanager
def metric_scope(
    *,
    source_id: str | None = None,
    source_ids: Iterable[str] | None = None,
    pipeline: str | None = None,
    backend: str | None = None,
    model: str | None = None,
    configured_model: str | None = None,
    request_id: str | None = None,
    chunk_idx: int | None = None,
    device: str | None = None,
    selection_reason: str | None = None,
) -> Iterator[None]:
    """Inherit lineage, replacing only explicitly supplied identities."""
    scope = current_scope()
    if scope is None:
        yield
        return
    collector = scope.collector()
    assert collector is not None
    changes: dict[str, Any] = {}
    for name, value in (
        ("source_id", source_id),
        ("pipeline", pipeline),
        ("backend", backend),
        ("model", model),
        ("configured_model", configured_model),
    ):
        if value is not None:
            changes[name] = (
                collector.source_identity(value)
                if name == "source_id"
                else collector.identity(value)
            )
    if source_ids is not None:
        changes["source_id"] = collector.sources(source_ids)
    if request_id is not None:
        changes["request_id"] = request_id
    for name, value in (
        ("chunk_idx", chunk_idx),
        ("device", device),
        ("selection_reason", selection_reason),
    ):
        if value is not None:
            changes[name] = value
    token = _SCOPE.set(replace(scope, **changes))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def correlate_source(original: str, prepared: str) -> None:
    """Associate a provider upload with its source without retaining raw IDs."""
    scope = current_scope()
    collector = scope.collector() if scope is not None else None
    if collector is not None:
        collector.correlate_source(original, prepared)


@contextmanager
def bind_scope(scope: Scope | None) -> Iterator[None]:
    """Bind scalar origin only for an operation, never across generator yields."""
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)


@contextmanager
def defer_observations() -> Iterator[None]:
    """Retain bounded scalar sink deliveries until an outer critical section exits.

    Scalar records are retained immediately. Closing a collector before queued
    sink work settles marks it incomplete. Nested critical sections reuse a buffer.
    """
    if current_scope() is None or _DEFERRED.get() is not None:
        yield
        return
    pending: list[tuple[Scope, Observation, WorkLease]] = []
    token = _DEFERRED.set(pending)
    try:
        yield
    finally:
        _DEFERRED.reset(token)
        for scope, observation, lease in pending:
            try:
                collector = scope.collector()
                if collector is not None:
                    with bind_scope(scope):
                        collector._deliver(observation)
            finally:
                lease.close()


class WorkLease:
    """Scalar-only outstanding-work bookkeeping, independent of job futures."""

    def __init__(self, scope: Scope) -> None:
        self.scope: Scope | None = scope
        collector = scope.collector()
        if collector is not None:
            with collector._lock:
                collector._outstanding += 1

    def close(self) -> None:
        scope, self.scope = self.scope, None
        collector = scope.collector() if scope else None
        if collector is not None:
            with collector._lock:
                collector._outstanding -= 1


def track_work() -> WorkLease | None:
    scope = current_scope()
    return WorkLease(scope) if scope is not None else None


def record(
    name: str,
    value: float | None,
    *,
    library: Library,
    availability: Availability = "measured",
    origin: str = "client",
    status: Status = "ok",
    scope: Scope | None = None,
    span_id: str | None = None,
) -> None:
    """Emit a known scalar metric. Invalid evidence cannot break the workload."""
    scope = scope or current_scope()
    collector = scope.collector() if scope else None
    if scope is None or collector is None or collector._closed:
        return
    unit = METRIC_CATALOG.get(name)
    if (
        unit is None
        or origin not in {"client", "provider"}
        or (availability == "measured") != (value is not None)
        or (
            value is not None
            and (
                type(value) not in {int, float}
                or value > 2**53 - 1
                or value < 0
                or not math.isfinite(value)
            )
        )
    ):
        collector.invalidate()
        return
    try:
        observation = Observation(
            run_id=collector.run_id,
            request_id=scope.request_id,
            span_id=span_id or uuid.uuid4().hex,
            parent_id=scope.parent_id,
            source_id=scope.source_id,
            pipeline=scope.pipeline,
            configured_model=scope.configured_model,
            model=scope.model,
            backend=scope.backend,
            chunk_idx=scope.chunk_idx,
            device=scope.device,  # type: ignore[arg-type]
            selection_reason=scope.selection_reason,  # type: ignore[arg-type]
            library=library,
            name=name,
            unit=unit,  # type: ignore[arg-type]
            value=value,
            availability=availability,
            origin=origin,  # type: ignore[arg-type]
            status=status,
            timestamp_ns=time.time_ns(),
            reason={
                "ok": "none",
                "error": "workload_error",
                "cancelled": "cancelled",
                "closed": "closed",
            }[status],  # type: ignore[arg-type]
        )
    except (ValueError, KeyError):
        collector.invalidate()
        return
    collector._append(observation, scope)


class Measurement:
    """Inclusive monotonic span; no raw exception or workload object retained."""

    def __init__(self, name: str, library: Library) -> None:
        self.name = name
        self.library: Library = library
        self._scope: Scope | None = None
        self._lease: WorkLease | None = None
        self._started = 0
        self._id: str | None = None
        self._token = None
        self._active_token = None
        self.status: Status = "ok"

    def __enter__(self) -> Measurement:
        self._scope = current_scope()
        if self._scope is not None:
            self._lease = track_work()
            self._id = uuid.uuid4().hex
            self._started = time.perf_counter_ns()
            self._token = _SCOPE.set(replace(self._scope, parent_id=self._id))
            self._active_token = _ACTIVE.set(self)
        return self

    def resolve(self, **identities: str) -> None:
        if self._scope is not None:
            collector = self._scope.collector()
            if collector is not None:
                self._scope = replace(
                    self._scope,
                    **{
                        key: (
                            collector.source_identity(value)
                            if key == "source_id"
                            else collector.identity(value)
                        )
                        for key, value in identities.items()
                    },
                )

    def metadata(self, **values: Any) -> None:
        if self._scope is not None:
            self._scope = replace(self._scope, **values)

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._scope is not None:
            status: Status = self.status
            if error_type is not None:
                status = (
                    "cancelled"
                    if issubclass(error_type, asyncio.CancelledError)
                    else "closed"
                    if issubclass(error_type, GeneratorExit)
                    else "error"
                )
            record(
                self.name,
                (time.perf_counter_ns() - self._started) / 1e9,
                library=self.library,
                scope=self._scope,
                span_id=self._id,
                status=status,
            )
            if self._token is not None:
                _SCOPE.reset(self._token)
            if self._active_token is not None:
                _ACTIVE.reset(self._active_token)
            if self._lease is not None:
                self._lease.close()
            self._scope = self._lease = self._token = None
            self._active_token = None


class _NoMeasurement:
    def __enter__(self) -> _NoMeasurement:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def resolve(self, **identities: str) -> None:
        pass

    def metadata(self, **values: Any) -> None:
        pass


_NO_MEASUREMENT = _NoMeasurement()


def measure(name: str, *, library: Library) -> Measurement | _NoMeasurement:
    if current_scope() is None:
        return _NO_MEASUREMENT
    return Measurement(name, library)


def resolve_current(**identities: str) -> None:
    active = _ACTIVE.get()
    if active is not None:
        active.resolve(**identities)


def fail_current() -> None:
    active = _ACTIVE.get()
    if active is not None:
        active.status = "error"


class DetachedMeasurement:
    """A lazy iterator's lifetime span without a context token across yields."""

    def __init__(self, name: str, *, library: Library) -> None:
        self.name = name
        self.library: Library = library
        self.scope = current_scope()
        self._lease = track_work()
        self.span_id = uuid.uuid4().hex if self.scope is not None else None
        self.started_ns = time.perf_counter_ns() if self.scope is not None else 0
        self.child_scope = (
            replace(self.scope, parent_id=self.span_id) if self.scope else None
        )

    def resolve(self, *, backend: str, device: str) -> None:
        if self.scope is not None:
            collector = self.scope.collector()
            if collector is not None:
                self.scope = replace(
                    self.scope, backend=collector.identity(backend), device=device
                )
                self.child_scope = replace(self.scope, parent_id=self.span_id)

    def finish(self, status: Status) -> None:
        try:
            if self.scope is not None:
                record(
                    self.name,
                    (time.perf_counter_ns() - self.started_ns) / 1e9,
                    library=self.library,
                    scope=self.scope,
                    span_id=self.span_id,
                    status=status,
                )
        finally:
            if self._lease is not None:
                self._lease.close()
                self._lease = None
