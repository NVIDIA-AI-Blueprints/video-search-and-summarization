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

"""Process-local profiling helpers.

:class:`RateProfiler` counts events and computes a cumulative events/sec
since the first recorded event; optionally tracks average per-event
latency. The reported rate is simply
``total_count / (now - first_event_ts)`` -- no windowing, no idle-gap
detection.

Instances are **process-local**: they only aggregate within the process
they're constructed in. The Kafka consumer subprocess and the FastAPI
parent each own their own instances; there is no cross-process
aggregation.

Because background reporter threads do **not** survive ``fork()``, the
convention is to construct a :class:`RateProfiler` *inside* the process
that will use it (after the fork), not at module-import time in the
parent.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional


_default_logger = logging.getLogger(__name__)


class RateProfiler:
    """Counts events and reports cumulative events/sec since first event.

    Parameters
    ----------
    name:
        Tag included in every log line, e.g. ``"search_requests"``.
    unit:
        Pluralised noun for the log line, e.g. ``"req"`` -> ``"12.3 req/s"``.
    log_interval_sec:
        How often the background thread emits a summary line. Before
        the first ``record()`` call there's nothing to report, so the
        reporter stays silent.
    track_latency:
        When True, the second argument of :meth:`record` is summed and
        reported as an arithmetic mean per event.
    logger:
        Where the summary lines are emitted; defaults to this module's
        logger so that callers can route via ``logging.getLogger(...)``.
    """

    def __init__(
        self,
        name: str,
        *,
        unit: str = "events",
        log_interval_sec: float = 30.0,
        track_latency: bool = False,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._name = name
        self._unit = unit
        self._log_interval_sec = max(1.0, float(log_interval_sec))
        self._track_latency = track_latency
        self._log = logger or _default_logger

        self._lock = threading.Lock()
        self._count = 0
        self._sum_latency_sec = 0.0
        # Timestamp of the first recorded event. ``None`` until the
        # very first ``record()`` call. Never reset, so the reported
        # rate is cumulative-since-first-event for the lifetime of the
        # profiler instance.
        self._first_event_ts: Optional[float] = None
        # Peak-rate tracking: at each emit we compute the rate for the
        # *just-elapsed reporting interval* (delta count / delta time)
        # and keep the running max. This catches bursts that the
        # cumulative average smears out.
        self._peak_rate: float = 0.0
        self._last_emit_ts: Optional[float] = None
        self._last_emit_count: int = 0

        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._reporter_loop,
            name=f"profiler-{name}",
            daemon=True,
        )
        self._thread.start()

    def record(self, count: int = 1, latency_sec: float = 0.0) -> None:
        """Register ``count`` events with optional aggregate latency.

        ``latency_sec`` should be the total latency for the recorded
        events (so e.g. inserting a batch of 32 rows in 80ms should pass
        ``count=32, latency_sec=0.08``). Pass 0 (the default) when
        latency isn't tracked.
        """
        if count <= 0:
            return
        with self._lock:
            if self._first_event_ts is None:
                self._first_event_ts = time.monotonic()
            self._count += count
            if self._track_latency:
                self._sum_latency_sec += float(latency_sec)

    def _reporter_loop(self) -> None:
        while not self._stop_event.wait(self._log_interval_sec):
            self._emit()

    def _emit(self) -> None:
        now = time.monotonic()
        with self._lock:
            count = self._count
            first_ts = self._first_event_ts
            sum_latency_sec = self._sum_latency_sec

            if count == 0 or first_ts is None:
                # Nothing recorded yet: stay silent rather than spam the log.
                return

            # Per-interval rate (since last emit) -> update peak.
            ref_ts = self._last_emit_ts if self._last_emit_ts is not None else first_ts
            interval = now - ref_ts
            delta = count - self._last_emit_count
            if interval > 0.0 and delta > 0:
                interval_rate = delta / interval
                if interval_rate > self._peak_rate:
                    self._peak_rate = interval_rate
            self._last_emit_ts = now
            self._last_emit_count = count
            peak_rate = self._peak_rate

        if delta == 0:
            # No events recorded in the interval: stay silent rather than spam the log.
            return

        elapsed = now - first_ts
        msg = f"[profile] {self._name}: count={count}, elapsed={elapsed:.2f}s"
        if elapsed > 0.0:
            msg += f", avg={count / elapsed:.2f} {self._unit}/s" f", peak={peak_rate:.2f} {self._unit}/s"
        if self._track_latency:
            avg_latency_ms = (sum_latency_sec / count) * 1000.0
            msg += f", avg_latency={avg_latency_ms:.2f}ms"
        self._log.info(msg)

    def shutdown(self) -> None:
        """Stop the reporter thread and emit one final summary line."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._emit()


__all__ = ["RateProfiler"]
