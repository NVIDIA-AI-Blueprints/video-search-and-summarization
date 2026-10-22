"""Admission control: bound the number of boards in flight.

A separate cap from the AOI #2 semaphore. When ``intake_queue_size`` boards
are already being processed, the intake returns 503 so the AOI Client never
holds unbounded work.
"""

import asyncio


class Admission:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._count = 0
        self._lock = asyncio.Lock()

    @property
    def in_flight(self) -> int:
        return self._count

    async def try_admit(self) -> bool:
        """Reserve a slot, or return False when at capacity."""
        async with self._lock:
            if self._count >= self._capacity:
                return False
            self._count += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._count > 0:
                self._count -= 1
