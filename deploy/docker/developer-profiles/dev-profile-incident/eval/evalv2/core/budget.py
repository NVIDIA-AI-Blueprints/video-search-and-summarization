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

**Calls another system sends** (the experiment agent's own grounding and zoom calls in Track B) cannot be reserved
one by one. ``hold`` checks every ledger's worst case and charges it *before* the request is sent, refusing the
request (charging nothing) when a cap could not cover it; ``release`` replaces the hold with what that system
reported sending once it has replied (the worst case when what it sent is unknown, nothing when it never received
the request). A hold is in the log while the request is in flight, so a run killed mid-call resumes from counts
that include it, and concurrent requests each see the others' holds. ``check`` (nothing charged) and
``charge_measured`` (charged after the fact) remain as primitives.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Iterable, Mapping, Sequence
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
class Hold:
    """A request's worst case, charged before the request is sent; ``charged`` replaces it once released."""

    id: str
    subject: str
    task: str
    worst_case: dict[str, tuple[int, int]]
    charged: dict[str, tuple[int, int]] | None = None

    @property
    def released(self) -> bool:
        return self.charged is not None


@dataclass
class _Totals:
    attempts: int = 0
    tokens: int = 0


def _figures(rows: Mapping[str, Sequence[int]]) -> dict[str, tuple[int, int]]:
    out = {}
    for ledger, (attempts, tokens) in rows.items():
        if ledger not in LEDGERS:
            raise ValueError(f"unknown ledger {ledger!r}")
        if int(attempts) < 0 or int(tokens) < 0:
            raise ValueError("attempts and tokens must not be negative")
        out[ledger] = (int(attempts), int(tokens))
    return out


@dataclass
class BudgetLedger:
    caps: list[Cap]
    log_path: Path | None = None
    _open: dict[str, Reservation] = field(default_factory=dict, init=False)
    _holds: dict[str, Hold] = field(default_factory=dict, init=False)
    _events: list[dict] = field(default_factory=list, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

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
        elif event["event"] == "hold":
            self._holds[event["id"]] = Hold(event["id"], event["subject"], event["task"],
                                            {k: (int(a), int(t)) for k, (a, t) in event["worst_case"].items()})
        elif event["event"] == "release" and event["id"] in self._holds:
            self._holds[event["id"]].charged = {k: (int(a), int(t)) for k, (a, t) in event["charged"].items()}

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
        for event in self._events:
            if event["event"] != "measured" or event["ledger"] != ledger:
                continue
            if (subject != ANY and event["subject"] != subject) or (task != ANY and event["task"] != task):
                continue
            totals.attempts += event["attempts"]
            totals.tokens += event["tokens"]
        for hold in self._holds.values():
            if (subject != ANY and hold.subject != subject) or (task != ANY and hold.task != task):
                continue
            attempts, tokens = (hold.charged if hold.charged is not None else hold.worst_case).get(ledger, (0, 0))
            totals.attempts += attempts
            totals.tokens += tokens
        return totals

    def check(self, ledger: str, *, subject: str, task: str, attempts: int, tokens: int) -> None:
        """Raise ``BudgetExceeded`` unless every matching cap can still take ``attempts`` and ``tokens``.

        For calls another system makes on our behalf (the agent's own grounding or zoom calls in Track B): the worst
        case is checked before the request and nothing is charged; what was really sent is charged after it
        (:meth:`charge_measured`).
        """
        if ledger not in LEDGERS:
            raise ValueError(f"unknown ledger {ledger!r}")
        for cap in self.caps:
            if not cap.matches(ledger, subject, task):
                continue
            used = self.totals(ledger, cap.subject, cap.task)
            if cap.max_attempts is not None and used.attempts + attempts > cap.max_attempts:
                raise BudgetExceeded(f"{ledger} attempts cap {cap.max_attempts} cannot cover the worst case "
                                     f"{attempts} (used {used.attempts}; subject={cap.subject}, task={cap.task})")
            if cap.max_tokens is not None and used.tokens + tokens > cap.max_tokens:
                raise BudgetExceeded(f"{ledger} tokens cap {cap.max_tokens} cannot cover the worst case {tokens} "
                                     f"(used {used.tokens}; subject={cap.subject}, task={cap.task})")

    def charge_measured(self, ledger: str, *, subject: str, task: str, attempts: int, tokens: int,
                        source: str = "") -> None:
        """Charge attempts and tokens that another system measured (an agent's provenance), after the fact."""
        if ledger not in LEDGERS:
            raise ValueError(f"unknown ledger {ledger!r}")
        if attempts < 0 or tokens < 0:
            raise ValueError("measured attempts and tokens must not be negative")
        with self._lock:
            self._log({"event": "measured", "ledger": ledger, "subject": subject, "task": task, "attempts": attempts,
                       "tokens": tokens, "source": source})

    def hold(self, worst_case: Mapping[str, Sequence[int]], *, subject: str, task: str, source: str = "") -> Hold:
        """Check every ledger's worst case and charge it before sending a request; ``BudgetExceeded`` charges nothing.

        ``worst_case`` maps ledgers to ``(attempts, tokens)``. The check and the charge are one step under the
        ledger's lock, so two requests in flight can never both pass a cap that covers only one of them.
        """
        figures = _figures(worst_case)
        with self._lock:
            for ledger, (attempts, tokens) in figures.items():
                self.check(ledger, subject=subject, task=task, attempts=attempts, tokens=tokens)
            hold_id = uuid.uuid4().hex
            self._log({"event": "hold", "id": hold_id, "subject": subject, "task": task,
                       "worst_case": {ledger: list(pair) for ledger, pair in figures.items()}, "source": source})
            return self._holds[hold_id]

    def release(self, hold: Hold, charged: Mapping[str, Sequence[int]], *, source: str = "") -> None:
        """Replace a hold's worst case with what was really sent (any ledgers; ``{}`` when nothing was sent)."""
        figures = _figures(charged)
        with self._lock:
            if hold.id not in self._holds:
                raise ValueError(f"unknown hold {hold.id}")
            if self._holds[hold.id].released:
                raise ValueError(f"hold {hold.id} is already released")
            self._log({"event": "release", "id": hold.id,
                       "charged": {ledger: list(pair) for ledger, pair in figures.items()}, "source": source})

    def reserve(self, ledger: str, *, subject: str, task: str, tokens_bound: int) -> Reservation:
        """Charge one attempt before sending it; raise ``BudgetExceeded`` (nothing charged) if a cap would pass."""
        if ledger not in LEDGERS:
            raise ValueError(f"unknown ledger {ledger!r}")
        if tokens_bound < 0:
            raise ValueError("tokens_bound must not be negative")
        with self._lock:
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
        with self._lock:
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
