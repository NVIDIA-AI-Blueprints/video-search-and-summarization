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

"""Pacing for live runs: global requests per minute and calls in flight, per-model tokens per minute, and a gap.

The gateway allows 60 requests a minute and 4 in parallel (``experiments/pricing.json`` ``rate_limits``), and Omni
has a quota of about 30,000 tokens a minute (``adapters/capabilities.json``, the 2 Oct benchmark). A call is
admitted when all of these hold:

- fewer than ``parallel`` calls are in flight;
- fewer than ``rpm`` requests started in the last 60 s. Every HTTP attempt counts: a retry passes :meth:`retry_gate`
  after its backoff, so a burst of retries cannot pass the limit either;
- the model's tokens in the last 60 s (measured, booked when a call ends) plus every in-flight call's estimate plus
  this call's estimate stay within the model's ``tpm``. The estimate is the mean of the model's measured calls,
  starting from the 2 Oct benchmark's means. A window holding nothing always admits one call, so a single call
  larger than the quota is not refused for ever;
- ``min_gap_s`` has passed since the last call ended (V1's ``inter_call_delay_s`` rule, used by Track B, which runs
  one analysis at a time).

Waiting is never model time: the adapter's stopwatch starts after admission. ``clock`` and ``sleep`` are injectable,
so a test drives the pacer on a fake clock.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

WINDOW_S = 60.0
POLL_S = 0.5
PRICING_PATH = Path(__file__).resolve().parents[2] / "experiments" / "pricing.json"
# Per-model token quotas known to apply (tokens per minute). Omni: 30k TPM (2 Oct benchmark, capabilities.json).
MODEL_TPM: dict[str, int] = {"nvidia/nemotron-3-nano-omni-30b-a3b-reasoning": 30000}
# Mean P1 tokens per call on the 2 Oct benchmark (55 clips): the starting estimate for the token window.
TYPICAL_TOKENS: dict[str, int] = {"nvidia/cosmos-3-nano-reasoner": 8727, "nvidia/cosmos-3-super-reasoner": 9898,
                                  "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning": 19690}


def gateway_limits(path: Path = PRICING_PATH) -> dict[str, int]:
    """``{"rpm", "parallel"}`` from the gateway's recorded rate limits."""
    limits = json.loads(Path(path).read_text(encoding="utf-8"))["rate_limits"]
    return {"rpm": int(limits["rpm"]), "parallel": int(limits["max_parallel_requests"])}


@dataclass
class _Model:
    tpm: int | None
    estimate: float
    measured: int = 0
    window: deque[tuple[float, int]] = field(default_factory=deque)
    in_flight: list[float] = field(default_factory=list)


class Ticket:
    """One admitted call; :meth:`done` books its measured tokens and frees its slot."""

    def __init__(self, pacer: Pacer, model: str, estimate: float) -> None:
        self._pacer, self.model, self.estimate, self._open = pacer, model, estimate, True

    def done(self, tokens: int | None) -> None:
        if self._open:
            self._open = False
            self._pacer._finish(self, tokens)


class Pacer:
    def __init__(self, *, rpm: int | None, parallel: int, tpm: Mapping[str, int] | None = None,
                 estimates: Mapping[str, int] | None = None, min_gap_s: float = 0.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 poll_s: float = POLL_S) -> None:
        if parallel < 1:
            raise ValueError("parallel must be at least 1")
        self.rpm, self.parallel, self.min_gap_s = rpm, parallel, min_gap_s
        self._tpm = dict(MODEL_TPM if tpm is None else tpm)
        self._estimates = dict(TYPICAL_TOKENS if estimates is None else estimates)
        self._clock, self._sleep, self._poll = clock, sleep, poll_s
        self._starts: deque[float] = deque()
        self._models: dict[str, _Model] = {}
        self._in_flight = 0
        self._last_end: float | None = None
        self._lock = threading.Lock()
        self.waited_s = 0.0
        self.starts: list[tuple[float, str]] = []  # (time, model) of every admitted request, for the run summary

    def settings(self) -> dict[str, object]:
        return {"rpm": self.rpm, "parallel": self.parallel, "tpm": dict(self._tpm), "min_gap_s": self.min_gap_s,
                "estimates": dict(self._estimates)}

    def _model(self, name: str) -> _Model:
        if name not in self._models:
            self._models[name] = _Model(self._tpm.get(name), float(self._estimates.get(name, 0)))
        return self._models[name]

    def _expire(self, now: float) -> None:
        while self._starts and self._starts[0] <= now - WINDOW_S:
            self._starts.popleft()
        for model in self._models.values():
            while model.window and model.window[0][0] <= now - WINDOW_S:
                model.window.popleft()

    def _rpm_delay(self, now: float) -> float:
        if self.rpm is None or len(self._starts) < self.rpm:
            return 0.0
        return self._starts[len(self._starts) - self.rpm] + WINDOW_S - now

    def delay(self, model: str, now: float | None = None) -> float:
        """Seconds until ``model`` may start a call (0 now). Called under the lock, or from a test."""
        now = self._clock() if now is None else now
        self._expire(now)
        if self._in_flight >= self.parallel:
            return self._poll
        waits = [self._rpm_delay(now)]
        if self.min_gap_s and self._last_end is not None:
            waits.append(self._last_end + self.min_gap_s - now)
        state = self._model(model)
        if state.tpm is not None:
            used = sum(tokens for _, tokens in state.window) + sum(state.in_flight)
            if (state.window or state.in_flight) and used + state.estimate > state.tpm:
                if state.in_flight:
                    waits.append(self._poll)  # an in-flight call's tokens are not booked yet
                else:
                    over, wait = used + state.estimate - state.tpm, 0.0
                    for at, tokens in state.window:
                        over -= tokens
                        wait = at + WINDOW_S - now
                        if over <= 0:
                            break
                    waits.append(wait)
        return max(0.0, *waits)

    def acquire(self, model: str) -> Ticket:
        """Block until ``model`` may start a call, then admit it (one request, one slot, its estimate)."""
        while True:
            with self._lock:
                now = self._clock()
                wait = self.delay(model, now)
                if wait <= 0:
                    state = self._model(model)
                    self._in_flight += 1
                    self._starts.append(now)
                    self.starts.append((now, model))
                    state.in_flight.append(state.estimate)
                    return Ticket(self, model, state.estimate)
            self.waited_s += wait
            self._sleep(wait)

    def retry_gate(self, model: str) -> None:
        """A retry of an admitted call: wait for the request limit only (the call keeps its slot), then count it."""
        while True:
            with self._lock:
                now = self._clock()
                self._expire(now)
                wait = self._rpm_delay(now)
                if wait <= 0:
                    self._starts.append(now)
                    self.starts.append((now, model))
                    return
            self.waited_s += wait
            self._sleep(wait)

    def _finish(self, ticket: Ticket, tokens: int | None) -> None:
        with self._lock:
            now = self._clock()
            state = self._model(ticket.model)
            self._in_flight -= 1
            self._last_end = now
            if ticket.estimate in state.in_flight:
                state.in_flight.remove(ticket.estimate)
            if tokens is not None and tokens > 0:
                state.window.append((now, int(tokens)))
                state.measured += 1
                state.estimate += (tokens - state.estimate) / state.measured if state.measured > 1 else (
                    tokens - state.estimate)
