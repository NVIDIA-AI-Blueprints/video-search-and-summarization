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
"""Coalesce successful metadata reads within one retrieval request only."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING
from typing import cast

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from collections.abc import Awaitable
    from collections.abc import Callable
    from typing import Any

_reads: ContextVar[dict[tuple[object, ...], asyncio.Task[Any]] | None] = ContextVar("vst_metadata_reads", default=None)


@asynccontextmanager
async def metadata_scope() -> AsyncIterator[None]:
    """Reuse an outer search scope; never retain data between requests."""
    if _reads.get() is not None:
        yield
        return
    reads: dict[tuple[object, ...], asyncio.Task[Any]] = {}
    token = _reads.set(reads)
    try:
        yield
    finally:
        _reads.reset(token)
        pending = [task for task in reads.values() if not task.done()]
        for task in pending:
            task.cancel()
        if reads:
            await asyncio.gather(*reads.values(), return_exceptions=True)


async def metadata_value[T](key: tuple[object, ...], fetch: Callable[[], Awaitable[T]]) -> T:
    """Coalesce a successful raw response or parsed view in the same request scope."""
    reads = _reads.get()
    if reads is None:
        return await fetch()
    task = reads.get(key)
    if task is None:
        task = asyncio.create_task(fetch())
        reads[key] = task
    try:
        return cast("T", await asyncio.shield(task))
    except BaseException:
        if task.done() and (task.cancelled() or task.exception() is not None) and reads.get(key) is task:
            del reads[key]
        raise


async def metadata_text(key: tuple[str, float, int], fetch: Callable[[], Awaitable[str]]) -> str:
    """Share raw text while consumers retain their own error/parsing boundaries."""
    return await metadata_value(key, fetch)


def discard_metadata(key: tuple[str, float, int]) -> None:
    """Do not retain a response whose consumer rejected its syntax or shape."""
    reads = _reads.get()
    if reads is not None:
        task = reads.get(key)
        if task is not None and task.done():
            del reads[key]
