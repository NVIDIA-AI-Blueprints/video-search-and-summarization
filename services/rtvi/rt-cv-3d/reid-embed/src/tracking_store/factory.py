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

"""Backend selection for :class:`TrackingStore`.

The factory is the only place that should know about concrete backend
classes. ``main.py`` calls ``make_store()`` once at module import time
and never touches a backend constructor directly.

Selection rules
---------------

Order of precedence for the backend name:

1. Explicit ``backend=`` argument to :func:`make_store`.
2. ``TRACKING_STORE_BACKEND`` environment variable.
3. ``"memory"`` (the Phase 1 default).

Recognised values:

* ``memory`` -- :class:`tracking_store.memory.InMemoryTrackingStore`.
* ``sqlite`` -- :class:`tracking_store.sqlite.SqliteTrackingStore` (Phase 2).
  Reads its own configuration from
  :envvar:`TRACKING_STORE_SQLITE_PATH` and
  :envvar:`TRACKING_STORE_RESET_ON_START`; see the module docstring.
* ``redis``  -- reserved for Phase 3; currently raises
  ``NotImplementedError`` so callers see exactly what's missing.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from tracking_store.memory import InMemoryTrackingStore
from tracking_store.sqlite import SqliteTrackingStore
from tracking_store.base import TrackingStore


logger = logging.getLogger(__name__)


_BACKEND_ENV_VAR = "TRACKING_STORE_BACKEND"
_DEFAULT_BACKEND = "sqlite"


def make_store(backend: Optional[str] = None) -> TrackingStore:
    """Return a TrackingStore instance for the requested backend.

    See module docstring for selection rules.
    """
    name = (backend or os.getenv(_BACKEND_ENV_VAR) or _DEFAULT_BACKEND).strip().lower()

    if name == "memory":
        logger.info("TrackingStore: using in-memory backend (Phase 1)")
        return InMemoryTrackingStore()

    if name == "sqlite":
        logger.info("TrackingStore: using SQLite WAL backend (Phase 2)")
        return SqliteTrackingStore()

    if name == "redis":
        raise NotImplementedError(
            "TRACKING_STORE_BACKEND=redis is reserved for Phase 3 of the "
            "tracking-store migration; please set TRACKING_STORE_BACKEND=memory "
            "or TRACKING_STORE_BACKEND=sqlite until the Redis backend lands."
        )

    raise ValueError(f"Unknown TRACKING_STORE_BACKEND={name!r}; expected one of " "'memory', 'sqlite', 'redis'.")


__all__ = ["make_store"]
