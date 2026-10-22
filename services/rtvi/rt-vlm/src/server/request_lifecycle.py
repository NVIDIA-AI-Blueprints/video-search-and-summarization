# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""HTTP ownership helpers, independent of the GPU pipeline implementation."""

import asyncio
from collections.abc import Awaitable, Callable

from sse_starlette.sse import EventSourceResponse


class ClientDisconnected(Exception):
    """The response's client disappeared before its backend work completed."""


async def wait_for_completion_or_disconnect(
    is_done: Callable[[], bool],
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    timeout: float | None = None,
    poll_interval: float = 0.05,
) -> None:
    """Watch non-streaming work without occupying a blocking executor worker.

    Request bodies must already have been consumed. SSE responses use their own
    disconnect listener instead; two consumers must not read the same receive.
    Completion wins a simultaneous disconnect so an already finished request is
    not spuriously cancelled.
    """
    loop = asyncio.get_running_loop()
    deadline = None if timeout is None else loop.time() + timeout
    while not is_done():
        if await is_disconnected():
            if not is_done():
                raise ClientDisconnected
            return
        if deadline is not None and loop.time() >= deadline:
            raise TimeoutError
        await asyncio.sleep(poll_interval)


class RequestOwnedEventSourceResponse(EventSourceResponse):
    """Finalize owned work even if sending fails before the generator starts."""

    def __init__(self, *args, finalize: Callable[[], Awaitable[None]], **kwargs):
        super().__init__(*args, **kwargs)
        self._finalize_owned_request = finalize

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # The supplied finalizer tracks and shields its cleanup task. The
            # SSE library remains the sole consumer of disconnect messages.
            await self._finalize_owned_request()
