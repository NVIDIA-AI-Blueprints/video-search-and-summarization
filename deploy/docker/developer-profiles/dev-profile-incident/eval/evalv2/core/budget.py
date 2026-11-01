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

"""Separate, retry-proof budget ledgers.

Each resource has its own ledger: P1 inference, the caption pass, grounding, the zoom look, object tracking,
embeddings, the judge, and reference generation. A cap can be scoped to one subject or task, or ``*`` for all.

**Every HTTP attempt counts.** ``reserve`` charges one attempt and a pessimistic token bound *before* the call is
sent, and refuses the attempt when any matching cap would be passed. ``settle`` replaces the bound with the
measured tokens once the response arrives; an attempt that never reports usage keeps its bound. Every event is
appended to a JSONL log, so a resumed run starts from the counts the interrupted run reached, including attempts
whose responses were never seen.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from evalv2.core.hashing import canonical_json

LEDGERS = ("inference", "caption", "grounding", "zoom", "object_tracking", "embedding", "judge", "generation")
ANY = "*"


class BudgetExceeded(RuntimeError):
    """The attempt would pass a cap; it was not sent."""


@dataclass(frozen=True)
class Cap:
    ledger: str
    max_attempts: int | None = None
    max_tokens: int | None = None
    subject: str = ANY
    task: str = ANY

    def __post_init__(self) -> None:
        if self.ledger not in LEDGERS:
            raise ValueError(f"unknown ledger {self.ledger!r}; expected one of {LEDGERS}")
        if self.max_attempts is None and self.max_tokens is None:
            raise ValueError(f"cap on {self.ledger} limits nothing")
        for name in ("max_attempts", "max_tokens"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")

    def matches(self, ledger: str, subject: str, task: str) -> bool:
        return self.ledger == ledger and self.subject in (ANY, subject) and self.task in (ANY, task)


@dataclass
class Reservation:
    id: str
    ledger: str
    subject: str
    task: str
    tokens_bound: int
    settled: bool = False


@dataclass
class _Totals:
    attempts: int = 0
    tokens: int = 0


@dataclass
class BudgetLedger:
    caps: list[Cap]
    log_path: Path | None = None
    _open: dict[str, Reservation] = field(default_factory=dict, init=False)
    _events: list[dict] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.caps = list(self.caps)
        if self.log_path is not None and Path(self.log_path).exists():
            for line in Path(self.log_path).read_text().splitlines():
                if line.strip():
                    self._apply(json.loads(line))

    @classmethod
    def from_caps(cls, caps: Iterable[Cap], log_path: Path | None = None) -> BudgetLedger:
        return cls(list(caps), log_path)

    def _apply(self, event: dict) -> None:
        self._events.append(event)
        if event["event"] == "reserve":
            self._open[event["id"]] = Reservation(event["id"], event["ledger"], event["subject"], event["task"],
                                                  event["tokens_bound"])
        elif event["event"] == "settle" and event["id"] in self._open:
            self._open[event["id"]].settled = True

    def _log(self, event: dict) -> None:
        self._apply(event)
        if self.log_path is not None:
            path = Path(self.log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                handle.write(canonical_json(event) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())

    def totals(self, ledger: str, subject: str = ANY, task: str = ANY) -> _Totals:
        """Attempts and tokens charged so far that fall under a cap with this ledger, subject and task scope."""
        totals = _Totals()
        settled_tokens = {e["id"]: e["tokens_used"] for e in self._events
                          if e["event"] == "settle" and e.get("tokens_used") is not None}
        for reservation in self._open.values():
            if reservation.ledger != ledger:
                continue
            if subject != ANY and reservation.subject != subject:
                continue
            if task != ANY and reservation.task != task:
                continue
            totals.attempts += 1
            totals.tokens += settled_tokens.get(reservation.id, reservation.tokens_bound)
        return totals

    def reserve(self, ledger: str, *, subject: str, task: str, tokens_bound: int) -> Reservation:
        """Charge one attempt before sending it; raise ``BudgetExceeded`` (nothing charged) if a cap would pass."""
        if ledger not in LEDGERS:
            raise ValueError(f"unknown ledger {ledger!r}")
        if tokens_bound < 0:
            raise ValueError("tokens_bound must not be negative")
        for cap in self.caps:
            if not cap.matches(ledger, subject, task):
                continue
            used = self.totals(ledger, cap.subject, cap.task)
            if cap.max_attempts is not None and used.attempts + 1 > cap.max_attempts:
                raise BudgetExceeded(f"{ledger} attempts cap {cap.max_attempts} reached "
                                     f"(subject={cap.subject}, task={cap.task})")
            if cap.max_tokens is not None and used.tokens + tokens_bound > cap.max_tokens:
                raise BudgetExceeded(f"{ledger} tokens cap {cap.max_tokens} would be passed "
                                     f"(subject={cap.subject}, task={cap.task}, bound={tokens_bound})")
        reservation_id = uuid.uuid4().hex
        self._log({"event": "reserve", "id": reservation_id, "ledger": ledger, "subject": subject, "task": task,
                   "tokens_bound": tokens_bound})
        return self._open[reservation_id]

    def settle(self, reservation: Reservation, *, tokens_used: int | None) -> None:
        """Replace the bound with measured tokens; ``None`` (no usage reported) keeps the pessimistic bound."""
        if reservation.id not in self._open:
            raise ValueError(f"unknown reservation {reservation.id}")
        if self._open[reservation.id].settled:
            raise ValueError(f"reservation {reservation.id} is already settled")
        self._log({"event": "settle", "id": reservation.id, "tokens_used": tokens_used})

    def snapshot(self) -> dict:
        """Usage per ledger against every cap, for reports and preflight."""
        rows = []
        for cap in self.caps:
            used = self.totals(cap.ledger, cap.subject, cap.task)
            rows.append({"ledger": cap.ledger, "subject": cap.subject, "task": cap.task,
                         "attempts": used.attempts, "max_attempts": cap.max_attempts,
                         "tokens": used.tokens, "max_tokens": cap.max_tokens})
        return {"caps": rows}
