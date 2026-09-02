# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Thread-safe in-memory run store with event replay and idempotent creation."""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field

from .contract import TERMINAL_EVENT_TYPES, CreateRunRequest, RunEvent, request_digest


class RunNotFoundError(KeyError):
    pass


class EventsExpiredError(RuntimeError):
    pass


class IdempotencyConflictError(RuntimeError):
    pass


class ThreadBusyError(RuntimeError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"thread already has active run {run_id}")
        self.run_id = run_id


class StoreCapacityError(RuntimeError):
    pass


def validate_idempotency_key(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not 1 <= len(value) <= 255 or any(
        ord(character) < 33 or ord(character) > 126 for character in value
    ):
        raise ValueError("Idempotency-Key must contain 1-255 visible ASCII characters")
    return value


@dataclass(slots=True)
class RunRecord:
    run_id: str
    request: CreateRunRequest
    request_digest: str
    max_events: int
    max_event_chars: int
    status: str = "queued"
    events: list[RunEvent] = field(default_factory=list)
    event_char_sizes: list[int] = field(default_factory=list)
    retained_event_chars: int = 0
    next_sequence: int = 1
    cancel_event: threading.Event = field(default_factory=threading.Event)
    condition: threading.Condition = field(default_factory=threading.Condition)
    created_monotonic: float = field(default_factory=time.monotonic)
    updated_monotonic: float = field(default_factory=time.monotonic)

    @property
    def terminal(self) -> bool:
        return self.status in {"completed", "failed", "cancelled"}

    def append(
        self, event_type: str, data: dict[str, object] | None = None
    ) -> RunEvent:
        with self.condition:
            data_chars = len(
                json.dumps(
                    data or {},
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            if data_chars > self.max_event_chars:
                raise ValueError(
                    "one gateway event exceeds the per-run character limit"
                )
            event = RunEvent.create(
                sequence=self.next_sequence,
                type=event_type,
                run_id=self.run_id,
                thread_id=self.request.thread_id,
                data=data,
            )
            self.next_sequence += 1
            self.events.append(event)
            self.event_char_sizes.append(data_chars)
            self.retained_event_chars += data_chars
            while (
                len(self.events) > self.max_events
                or self.retained_event_chars > self.max_event_chars
            ):
                self.events.pop(0)
                self.retained_event_chars -= self.event_char_sizes.pop(0)
            if event_type == "run.started":
                self.status = "running"
            elif event_type == "run.completed":
                self.status = "completed"
            elif event_type == "run.failed":
                self.status = "failed"
            elif event_type == "run.cancelled":
                self.status = "cancelled"
            self.updated_monotonic = time.monotonic()
            self.condition.notify_all()
            return event

    def events_after(self, sequence: int) -> list[RunEvent]:
        with self.condition:
            if self.events and sequence < self.events[0].sequence - 1:
                raise EventsExpiredError("requested events are no longer retained")
            return [event for event in self.events if event.sequence > sequence]

    def wait_for_events(
        self, sequence: int, timeout: float
    ) -> tuple[list[RunEvent], bool]:
        with self.condition:
            events = self.events_after(sequence)
            if not events and not self.terminal:
                self.condition.wait(timeout=timeout)
                events = self.events_after(sequence)
            return events, self.terminal

    def snapshot(self) -> dict[str, object]:
        with self.condition:
            return {
                "run_id": self.run_id,
                "thread_id": self.request.thread_id,
                "status": self.status,
                "last_event_id": str(self.next_sequence - 1),
            }


class RunStore:
    def __init__(
        self,
        *,
        retention_seconds: int,
        max_runs: int,
        max_events_per_run: int,
        max_event_chars_per_run: int,
    ) -> None:
        self._retention_seconds = retention_seconds
        self._max_runs = max_runs
        self._max_events_per_run = max_events_per_run
        self._max_event_chars_per_run = max_event_chars_per_run
        self._lock = threading.RLock()
        self._runs: dict[str, RunRecord] = {}
        self._active_threads: dict[str, str] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}

    def _remove(self, run_id: str) -> None:
        self._runs.pop(run_id, None)
        for key, (_, mapped_run_id) in list(self._idempotency.items()):
            if mapped_run_id == run_id:
                self._idempotency.pop(key, None)

    def _cleanup(self) -> None:
        now = time.monotonic()
        expired = [
            run_id
            for run_id, record in self._runs.items()
            if record.terminal
            and now - record.updated_monotonic > self._retention_seconds
        ]
        for run_id in expired:
            self._remove(run_id)

    def create(
        self,
        request: CreateRunRequest,
        *,
        idempotency_key: str | None,
    ) -> tuple[RunRecord, bool]:
        key = validate_idempotency_key(idempotency_key)
        digest = request_digest(request)
        with self._lock:
            self._cleanup()
            if key and key in self._idempotency:
                stored_digest, run_id = self._idempotency[key]
                if stored_digest != digest:
                    raise IdempotencyConflictError(
                        "Idempotency-Key was already used with a different request"
                    )
                record = self._runs.get(run_id)
                if record is not None:
                    return record, True

            active_run_id = self._active_threads.get(request.thread_id)
            if active_run_id:
                raise ThreadBusyError(active_run_id)
            if len(self._runs) >= self._max_runs:
                terminal = sorted(
                    (record for record in self._runs.values() if record.terminal),
                    key=lambda record: record.updated_monotonic,
                )
                if terminal:
                    self._remove(terminal[0].run_id)
                else:
                    raise StoreCapacityError("gateway has reached its active run limit")

            run_id = f"run_{secrets.token_urlsafe(18)}"
            record = RunRecord(
                run_id=run_id,
                request=request,
                request_digest=digest,
                max_events=self._max_events_per_run,
                max_event_chars=self._max_event_chars_per_run,
            )
            self._runs[run_id] = record
            self._active_threads[request.thread_id] = run_id
            if key:
                self._idempotency[key] = (digest, run_id)
            return record, False

    def get(self, run_id: str) -> RunRecord:
        with self._lock:
            self._cleanup()
            record = self._runs.get(run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            return record

    def finish(
        self, record: RunRecord, event_type: str, data: dict[str, object] | None = None
    ) -> RunEvent:
        if event_type not in TERMINAL_EVENT_TYPES:
            raise ValueError(f"{event_type} is not terminal")
        event = record.append(event_type, data)
        with self._lock:
            if self._active_threads.get(record.request.thread_id) == record.run_id:
                self._active_threads.pop(record.request.thread_id, None)
        return event
