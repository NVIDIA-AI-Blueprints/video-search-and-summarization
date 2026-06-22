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

"""Abstract interface for alert-config storage backends.

Concrete implementations live in sibling modules:

- ``store.RedisAlertConfigStore`` — Redis JSON adapter (in-process cache).
- ``es_store.ESAlertConfigStore`` — Elasticsearch durable store (to be added).
- ``cached_store.CachedAlertConfigStore`` — write-through / read-through
  composite combining a durable primary with a hot-path cache.

Keeping the interface separate lets ``AlertConfigService`` depend on the
contract rather than any specific backend.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class AlertConfigStoreABC(ABC):
    """Contract for any alert-config storage backend."""

    @abstractmethod
    def set(self, alert_type: str, data: Dict[str, Any]) -> bool:
        """Unconditional write. Caller owns timestamps and merge semantics."""

    @abstractmethod
    def set_if_absent(self, alert_type: str, data: Dict[str, Any]) -> bool:
        """Atomic create. Returns ``True`` on creation, ``False`` if present."""

    @abstractmethod
    def get(
        self,
        alert_type: str,
        *,
        fallback_to_memory: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Return the stored record or ``None`` when absent.

        Args:
            alert_type: Normalized alert type to look up.
            fallback_to_memory: When ``True`` (default) and the store
                has a last-resort in-memory snapshot, that snapshot is
                used to keep service paths up during a dual outage of
                ES + Redis. Caller paths that need a hard signal that
                the durable backend is down (e.g. the REST API, where
                an operator GET should surface a 503 instead of stale
                data) pass ``False``: the underlying ``PersistenceError``
                is then re-raised. The flag is a no-op for stores that
                do not maintain a memory snapshot in the first place
                (Redis-only, ES-only).
        """

    @abstractmethod
    def get_all(
        self,
        *,
        fallback_to_memory: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        """Return every stored record keyed by normalized alert type.

        See :meth:`get` for ``fallback_to_memory`` semantics.
        """

    @abstractmethod
    def delete(self, alert_type: str) -> bool:
        """Hard-delete the record. Returns ``True`` if a record was removed."""
