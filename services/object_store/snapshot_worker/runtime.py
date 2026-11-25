"""Shared runtime helper for the worker entrypoints."""

import asyncio
import signal


async def wait_for_termination() -> None:
    """Block until SIGTERM/SIGINT is received."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
