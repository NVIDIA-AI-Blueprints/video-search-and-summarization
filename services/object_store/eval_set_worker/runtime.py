"""Shared runtime helpers for the long-running worker entrypoints.

The workers are meant to stay up and keep consuming — they do NOT exit/restart on
a message failure. `wait_for_termination` blocks the service until an operator
signal; `run_with_retries` retries a failed (idempotent) message in place with
exponential backoff so a transient S3/Kafka blip is absorbed without tearing the
consumer down.
"""

import asyncio
import logging
import signal
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


async def wait_for_termination() -> None:
    """Block until SIGTERM/SIGINT is received."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()


async def run_with_retries(
    handle: Callable[[], Awaitable[None]],
    *,
    retries: int,
    base_delay: float,
    max_delay: float,
    label: str,
) -> bool:
    """Run an idempotent handler, retrying transient failures in place.

    Returns True on success, or False if it gave up after `retries` (the caller
    then skips that message so the worker keeps processing other events rather
    than wedging the partition). The worker process never exits either way.
    """
    delay = base_delay
    for attempt in range(retries + 1):
        try:
            await handle()
            return True
        except Exception:  # noqa: BLE001 — keep the worker alive; retry then skip
            logger.exception("%s: attempt %d/%d failed", label, attempt + 1, retries + 1)
            if attempt < retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay) if delay else 0.0
    return False
