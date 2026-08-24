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

"""Elasticsearch-backed alert config store.

Adapts the generic ``PersistenceStore`` interface from
``persistence/`` to the ``AlertConfigStoreABC`` contract consumed by
``AlertConfigService``.

Mapping:
    * collection = ``ALERT_CONFIG_COLLECTION`` (constant)
    * doc_id     = normalised ``alert_type``
    * document   = the full config dict (prompt, vlm_params, ...)

Errors:
    * ``DocumentNotFoundError`` → translated to ``None`` on ``get`` or
      ``False`` on ``delete``; the service layer interprets those as
      "not found".
    * ``DocumentAlreadyExistsError`` on ``set_if_absent`` → returned as
      ``False`` (existing behaviour of the Redis store).
    * All other ``PersistenceError`` subclasses propagate; the cached
      store above decides whether to fall back or surface 5xx.
"""

import logging
from typing import Any, Dict, Optional

from persistence.base import PersistenceStore
from persistence.exceptions import (
    DocumentAlreadyExistsError,
    DocumentNotFoundError,
)

from .base import AlertConfigStoreABC
from .normalize import normalize_alert_type

logger = logging.getLogger(__name__)

ALERT_CONFIG_COLLECTION = "alert_configs"


class ESAlertConfigStore(AlertConfigStoreABC):
    """ES-backed durable store for alert verification configs."""

    def __init__(
        self,
        persistence: PersistenceStore,
        collection: str = ALERT_CONFIG_COLLECTION,
    ) -> None:
        self._persistence = persistence
        self._collection = collection

    def set(self, alert_type: str, data: Dict[str, Any]) -> bool:
        """Unconditional upsert.

        ``service.update`` composes a full document before calling this,
        so we never need merge semantics here. ``service.create`` calls
        ``set_if_absent`` instead, so ``set`` is only used on existing
        records. We still create-if-missing to keep the contract tolerant
        of out-of-band deletes.

        Concurrent-replica safety: the ``update → DocumentNotFoundError
        → create`` recovery races with another replica that succeeds in
        creating the doc between our update and our create. In that case
        the create raises ``DocumentAlreadyExistsError``; the document
        we wanted is now there (written by the other replica), so the
        right move is to retry the update once with our payload — same
        as a normal upsert. ``persistence.update`` carries OCC, so a
        third replica racing the retry surfaces as
        ``ConcurrentModificationError`` rather than a silent overwrite.
        """
        key = normalize_alert_type(alert_type)
        try:
            self._persistence.update(self._collection, key, data)
            return True
        except DocumentNotFoundError:
            pass
        try:
            self._persistence.create(self._collection, key, data)
        except DocumentAlreadyExistsError:
            # Another replica won the create race after our update saw
            # "not found". Retry update against the freshly-created doc
            # so this caller's payload is the latest version on disk.
            self._persistence.update(self._collection, key, data)
        return True

    def set_if_absent(self, alert_type: str, data: Dict[str, Any]) -> bool:
        key = normalize_alert_type(alert_type)
        try:
            self._persistence.create(self._collection, key, data)
            return True
        except DocumentAlreadyExistsError:
            return False

    def get(
        self,
        alert_type: str,
        *,
        fallback_to_memory: bool = True,
    ) -> Optional[Dict[str, Any]]:
        # ``fallback_to_memory`` is part of the ABC for symmetry with
        # ``CachedAlertConfigStore`` but has no effect here — the ES
        # store has no in-memory snapshot layer.
        del fallback_to_memory
        key = normalize_alert_type(alert_type)
        try:
            return self._persistence.read(self._collection, key)
        except DocumentNotFoundError:
            return None

    def get_all(
        self,
        *,
        fallback_to_memory: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        del fallback_to_memory  # unused; see ``get``
        # ES is the source of truth for listing; pull everything in one
        # call. 1000 is intentionally larger than any realistic
        # alert-type cardinality.
        result = self._persistence.list(self._collection, size=1000)
        items = result.get("items", [])
        total = result.get("total", 0)
        if total > len(items):
            logger.warning(
                "ES list returned %d of %d alert configs — increase page size",
                len(items), total,
            )
        out: Dict[str, Dict[str, Any]] = {}
        for doc in items:
            at = doc.get("alert_type")
            if at:
                out[normalize_alert_type(at)] = doc
        return out

    def delete(self, alert_type: str) -> bool:
        key = normalize_alert_type(alert_type)
        try:
            return self._persistence.delete(self._collection, key)
        except DocumentNotFoundError:
            return False
