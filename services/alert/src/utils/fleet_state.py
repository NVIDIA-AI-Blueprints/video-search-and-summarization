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

"""How many pipeline processes exist, are alive, and hold an assignment.

The health endpoint runs in its own process and cannot see the pipelines, so
this state has to cross a process boundary. It travelled through the Prometheus
multiprocess shards first, which made an observability switch a precondition
for serving: with metrics off the endpoint could not tell a dead fleet from a
whole one, so multi-process startup was refused outright. Nothing in the
requirements asked for that.

A small shared array carries it instead. Only the parent writes -- it is the
one that knows how many slots exist, which are alive, and which have signalled
ready -- and only the API child reads. Metrics still publish the same numbers
for scraping, but nothing depends on them being on.
"""

from typing import Optional, Tuple

# (configured, alive, ready). -1 means "not published yet", which reads
# differently from zero: an instance that has not said anything is not an
# instance reporting an empty fleet.
UNPUBLISHED = -1
_SLOTS = 3

_state = None


def create(ctx, configured: int) -> "object":
    """Allocate the shared array on ``ctx`` and publish the configured count.

    The count is written here rather than by a separate call because the
    separate call is what went wrong: it was made before the array existed,
    so it returned early and the fleet stayed unpublished, and /health
    answered ok for a whole startup with no pipeline running. Creating and
    publishing in one step leaves no order to get wrong. ``ctx`` must be the
    context the children spawn from, or they inherit a different array.
    """
    array = ctx.Array("i", [UNPUBLISHED] * _SLOTS, lock=True)
    attach(array)
    publish(configured, 0, 0)
    return array


def attach(array) -> None:
    """Adopt an array created elsewhere -- used by the API child after spawn."""
    global _state
    _state = array


def publish(configured: int, alive: int, ready: int) -> None:
    if _state is None:
        return
    with _state.get_lock():
        _state[0], _state[1], _state[2] = int(configured), int(alive), int(ready)


def read() -> Optional[Tuple[int, int, int]]:
    """The last published counts, or None when nothing has published yet."""
    if _state is None:
        return None
    with _state.get_lock():
        configured, alive, ready = _state[0], _state[1], _state[2]
    if configured == UNPUBLISHED:
        return None
    return configured, alive, ready
