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

"""Confirmed-verdict marker retention.

The ES-backed confirmed-verdict markers written by
:class:`DedupStateHandler` carry an ``expires_at`` used for read-time
fail-open, but expiry alone never removes the document — without a reaper
``ab-confirmed-verdicts`` grows without bound. This module owns the
Alert-MS-side maintenance task: an hourly, throttled
``delete_by_query`` on ``expires_at < now``.

It is a daemon thread (not a cron/ILM policy) so retention is owned by the
service that writes the markers, is throttled via ``requests_per_second`` to
bound cluster impact, and emits deleted-count / last-run / failure metrics
(wired inside :meth:`DedupStateHandler.purge_expired_verdicts`).
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# One hour between passes. Kept as a module constant so tests can
# monkeypatch a short interval.
DEFAULT_INTERVAL_SECONDS = 3600.0
DEFAULT_REQUESTS_PER_SECOND = 50.0


class VerdictRetentionJob:
    """Background daemon that periodically reaps expired verdict markers."""

    def __init__(
        self,
        handler,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
        run_on_start: bool = False,
    ) -> None:
        """
        Args:
            handler: A :class:`DedupStateHandler` (its
                ``purge_expired_verdicts`` does the work + metrics).
            interval_seconds: Seconds between passes.
            requests_per_second: Delete-by-query throttle.
            run_on_start: Run one pass immediately before the first sleep.
        """
        self._handler = handler
        self._interval = max(1.0, float(interval_seconds))
        self._rps = requests_per_second
        self._run_on_start = run_on_start
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the daemon thread (no-op if already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="verdict-retention", daemon=True,
        )
        self._thread.start()
        logger.info(
            "Verdict retention job started (interval=%ss, rps=%s)",
            self._interval, self._rps,
        )

    def stop(self, timeout: Optional[float] = None) -> None:
        """Signal the thread to stop and optionally join it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def run_once(self) -> int:
        """Run a single retention pass; returns markers deleted."""
        try:
            return self._handler.purge_expired_verdicts(requests_per_second=self._rps)
        except Exception as e:  # pragma: no cover - handler already guards
            logger.warning("Verdict retention pass raised: %s", e)
            return 0

    def _run(self) -> None:
        if self._run_on_start:
            self.run_once()
        # ``Event.wait`` returns True when set — lets stop() interrupt the
        # sleep immediately instead of waiting out a full hour.
        while not self._stop.wait(self._interval):
            self.run_once()
