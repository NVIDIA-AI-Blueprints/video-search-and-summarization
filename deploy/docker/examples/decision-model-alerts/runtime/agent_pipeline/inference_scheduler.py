# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One Cosmos call at a time, with durable interactive work ahead of reviews.

The queue owns reservations for persisted questions. They close the gap between
HTTP admission and the question worker reaching the inference lock. This is an
in-process scheduler, matching the application's single analytics-process model.
"""
import threading
import time
from contextlib import contextmanager


class InferenceCancelled(RuntimeError):
    """Shutdown interrupted a wait before inference began."""


class InferenceScheduler:
    def __init__(self):
        self._condition = threading.Condition()
        self._busy = False
        self._interactive_waiters = 0
        self._reservations = set()

    def reserve(self, key):
        with self._condition:
            self._reservations.add(key)
            self._condition.notify_all()

    def unreserve(self, key):
        with self._condition:
            self._reservations.discard(key)
            self._condition.notify_all()

    def wake(self):
        with self._condition:
            self._condition.notify_all()

    def acquire(self, blocking=True, timeout=-1, *, priority="background", stop=None):
        if priority not in ("background", "interactive"):
            raise ValueError("Unknown inference priority")
        deadline = time.monotonic() + timeout if timeout >= 0 else None
        with self._condition:
            interactive = priority == "interactive"
            if interactive:
                self._interactive_waiters += 1
            try:
                while True:
                    if stop is not None and stop.is_set():
                        raise InferenceCancelled("Inference wait stopped")
                    priority_wait = not interactive and (self._reservations or self._interactive_waiters)
                    if not self._busy and not priority_wait:
                        self._busy = True
                        return True
                    if not blocking:
                        return False
                    remaining = deadline - time.monotonic() if deadline is not None else None
                    if remaining is not None and remaining <= 0:
                        return False
                    self._condition.wait(min(0.1, remaining) if remaining is not None else 0.1)
            finally:
                if interactive:
                    self._interactive_waiters -= 1
                    self._condition.notify_all()

    def release(self):
        with self._condition:
            if not self._busy:
                raise RuntimeError("Release of unlocked inference scheduler")
            self._busy = False
            self._condition.notify_all()

    def locked(self):
        with self._condition:
            return self._busy

    @contextmanager
    def slot(self, *, priority="background", stop=None):
        self.acquire(priority=priority, stop=stop)
        try:
            yield
        finally:
            self.release()

    # Compatibility with the previous process-wide lock and its offline tests.
    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


INFERENCE_SCHEDULER = InferenceScheduler()
INFERENCE_LOCK = INFERENCE_SCHEDULER
